"""Run untrusted code with a timeout and a memory cap. NOT a security boundary until Docker is added."""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

DEFAULT_TIMEOUT = 4.0      # seconds per test
DEFAULT_MEM_MB = 256
MAX_OUTPUT_CHARS = 200_000


@dataclass
class RunResult:
    status: str            # "ok" | "timeout" | "memory" | "runtime_error" | "compile_error"
    stdout: str = ""
    stderr: str = ""
    runtime_ms: float = 0.0
    returncode: int | None = None


class Program:
    """A prepared (and, for C++, compiled) program that can be run on many inputs."""

    def __init__(self, code: str, language: str):
        self.language = language.lower()
        self.dir = tempfile.mkdtemp(prefix="breaker_")
        self.compile_error: str | None = None
        self.cmd: list[str] | None = None

        if self.language in ("python", "py"):
            path = Path(self.dir) / "sol.py"
            path.write_text(code, encoding="utf-8")
            self.cmd = [sys.executable, str(path)]
        elif self.language in ("cpp", "c++"):
            src = Path(self.dir) / "sol.cpp"
            exe = Path(self.dir) / ("sol.exe" if os.name == "nt" else "sol")
            src.write_text(code, encoding="utf-8")
            if shutil.which("g++") is None:
                self.compile_error = "g++ not found on PATH (install MinGW-w64 / MSYS2, or use Docker)"
                return
            try:
                p = subprocess.run(
                    ["g++", "-O2", "-std=c++17", str(src), "-o", str(exe)],
                    capture_output=True, text=True, timeout=60,
                )
            except subprocess.TimeoutExpired:
                self.compile_error = "compile timed out"
                return
            if p.returncode != 0:
                self.compile_error = p.stderr[:2000]
                return
            self.cmd = [str(exe)]
        else:
            self.compile_error = f"unsupported language: {language}"

    def run(self, stdin_text: str, timeout: float = DEFAULT_TIMEOUT, mem_mb: int = DEFAULT_MEM_MB) -> RunResult:
        if self.compile_error:
            return RunResult(status="compile_error", stderr=self.compile_error)

        start = time.perf_counter()
        proc = subprocess.Popen(
            self.cmd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=self.dir, text=True, encoding="utf-8", errors="replace",
        )
        killed_for: str | None = None
        ps = psutil.Process(proc.pid)

        # Feed stdin and collect output in threads so large I/O can't deadlock us.
        import threading
        out_buf: dict[str, str] = {}

        def communicate():
            try:
                o, e = proc.communicate(stdin_text)
                out_buf["o"], out_buf["e"] = o, e
            except Exception:
                out_buf.setdefault("o", "")
                out_buf.setdefault("e", "")

        t = threading.Thread(target=communicate, daemon=True)
        t.start()

        while t.is_alive():
            elapsed = time.perf_counter() - start
            if elapsed > timeout:
                killed_for = "timeout"
                break
            try:
                rss = ps.memory_info().rss
                for child in ps.children(recursive=True):
                    rss += child.memory_info().rss
                if rss > mem_mb * 1024 * 1024:
                    killed_for = "memory"
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            t.join(0.01)

        if killed_for:
            self._kill_tree(proc)
            t.join(1.0)

        runtime_ms = (time.perf_counter() - start) * 1000
        stdout = (out_buf.get("o") or "")[:MAX_OUTPUT_CHARS]
        stderr = (out_buf.get("e") or "")[:5000]

        if killed_for:
            return RunResult(status=killed_for, stdout=stdout, stderr=stderr, runtime_ms=runtime_ms)
        status = "ok" if proc.returncode == 0 else "runtime_error"
        return RunResult(status=status, stdout=stdout, stderr=stderr,
                         runtime_ms=runtime_ms, returncode=proc.returncode)

    @staticmethod
    def _kill_tree(proc: subprocess.Popen):
        try:
            parent = psutil.Process(proc.pid)
            for c in parent.children(recursive=True):
                c.kill()
            parent.kill()
        except psutil.NoSuchProcess:
            pass

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def normalize(s: str) -> str:
    """Whitespace-normalize: strip trailing spaces per line and trailing blank lines."""
    return "\n".join(line.rstrip() for line in s.strip().splitlines())


def outputs_match(a: str, b: str) -> bool:
    return normalize(a) == normalize(b)