"""Run untrusted code with a timeout, memory/output caps, a scrubbed environment and (on Linux/macOS) rlimits.

NOT A SECURITY BOUNDARY. The code still runs as your user and can read any file you can read (including
a .env file by absolute path). Use it for your own solutions on your own machine; Docker adds real isolation.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

try:                      # POSIX only; on Windows the limits below are simply skipped
    import resource       # noqa: F401
    HAS_RLIMIT = True
except ImportError:
    HAS_RLIMIT = False

DEFAULT_TIMEOUT = 4.0        # seconds per test
DEFAULT_MEM_MB = 256
MAX_CODE_CHARS = 100_000     # source size accepted by Program
MAX_STDIN_CHARS = 1_000_000  # input size fed to a program
MAX_OUTPUT_BYTES = 1_000_000 # stdout kept; a program printing more is killed (status "output_limit")
MAX_STDERR_BYTES = 20_000
MAX_FILE_BYTES = 10 * 1024 * 1024   # RLIMIT_FSIZE: largest file a program may write

# Runs inside the child just before exec (no preexec_fn: that is unsafe in a multi-threaded server).
_LAUNCHER = (
    "import os,sys,resource\n"
    "cpu,fsize,addr=int(sys.argv[1]),int(sys.argv[2]),int(sys.argv[3])\n"
    "for lim,val in ((resource.RLIMIT_CPU,cpu),(resource.RLIMIT_FSIZE,fsize),(resource.RLIMIT_CORE,0),\n"
    "                (resource.RLIMIT_AS,addr)):\n"
    "    if lim==resource.RLIMIT_AS and not val: continue\n"
    "    try: resource.setrlimit(lim,(val,val))\n"
    "    except (ValueError,OSError): pass\n"
    "os.execv(sys.argv[4],sys.argv[4:])\n"
)


def safe_env(home: str) -> dict[str, str]:
    """Only what a compiler / interpreter needs. API keys and everything else in os.environ are dropped."""
    keep = ["PATH"]
    keep += (["SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP"] if os.name == "nt"
             else ["LANG", "LC_ALL", "TMPDIR"])
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", HOME=home)
    return env


@dataclass
class RunResult:
    status: str            # ok | timeout | memory | output_limit | runtime_error | compile_error | input_too_large
    stdout: str = ""
    stderr: str = ""
    runtime_ms: float = 0.0
    returncode: int | None = None


class Program:
    """A prepared (and, for C++, compiled) program that can be run on many inputs."""

    def __init__(self, code: str, language: str):
        self.language = language.lower()
        self.dir = tempfile.mkdtemp(prefix="breaker_")
        self.env = safe_env(self.dir)
        self.compile_error: str | None = None
        self.cmd: list[str] | None = None

        if len(code) > MAX_CODE_CHARS:
            self.compile_error = f"code too large ({len(code)} > {MAX_CODE_CHARS} characters)"
            return
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
                p = subprocess.run(["g++", "-O2", "-std=c++17", str(src), "-o", str(exe)],
                                   capture_output=True, text=True, timeout=60, env=self.env, cwd=self.dir)
            except subprocess.TimeoutExpired:
                self.compile_error = "compile timed out"
                return
            if p.returncode != 0:
                self.compile_error = p.stderr[:2000]
                return
            self.cmd = [str(exe)]
        else:
            self.compile_error = f"unsupported language: {language}"

    def _launch_cmd(self, timeout: float, mem_mb: int) -> list[str]:
        if not HAS_RLIMIT:
            return self.cmd
        addr = mem_mb * 1024 * 1024 * 8 if sys.platform.startswith("linux") else 0   # RLIMIT_AS is unreliable on macOS
        return [sys.executable, "-I", "-S", "-c", _LAUNCHER, str(int(timeout) + 2), str(MAX_FILE_BYTES), str(addr), *self.cmd]

    def run(self, stdin_text: str, timeout: float = DEFAULT_TIMEOUT, mem_mb: int = DEFAULT_MEM_MB) -> RunResult:
        if self.compile_error:
            return RunResult(status="compile_error", stderr=self.compile_error)
        if len(stdin_text) > MAX_STDIN_CHARS:
            return RunResult(status="input_too_large", stderr=f"input over {MAX_STDIN_CHARS} characters")

        start = time.perf_counter()
        proc = subprocess.Popen(
            self._launch_cmd(timeout, mem_mb), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=self.dir, env=self.env, start_new_session=(os.name != "nt"),
        )
        out_chunks: list[bytes] = []
        err_chunks: list[bytes] = []
        over = threading.Event()

        def drain(stream, chunks, limit, flag):
            total = 0
            try:
                while True:
                    data = stream.read1(65536)
                    if not data:
                        return
                    total += len(data)
                    if total > limit:
                        if flag is not None:
                            flag.set()
                        return
                    chunks.append(data)
            except (OSError, ValueError):
                return

        def feed():
            try:
                proc.stdin.write(stdin_text.encode("utf-8", errors="replace"))
                proc.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                pass

        threads = [threading.Thread(target=drain, args=(proc.stdout, out_chunks, MAX_OUTPUT_BYTES, over), daemon=True),
                   threading.Thread(target=drain, args=(proc.stderr, err_chunks, MAX_STDERR_BYTES, None), daemon=True),
                   threading.Thread(target=feed, daemon=True)]
        for t in threads:
            t.start()

        killed_for: str | None = None
        try:
            ps = psutil.Process(proc.pid)
        except psutil.NoSuchProcess:
            ps = None
        while proc.poll() is None:
            if time.perf_counter() - start > timeout:
                killed_for = "timeout"
            elif over.is_set():
                killed_for = "output_limit"
            elif ps is not None:
                try:
                    rss = ps.memory_info().rss + sum(c.memory_info().rss for c in ps.children(recursive=True))
                    if rss > mem_mb * 1024 * 1024:
                        killed_for = "memory"
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            if killed_for:
                self._kill_tree(proc)
                break
            time.sleep(0.005)
        proc.wait()
        runtime_ms = (time.perf_counter() - start) * 1000
        for t in threads:
            t.join(1.0)
        for s in (proc.stdout, proc.stderr):
            try:
                s.close()
            except OSError:
                pass

        stdout = b"".join(out_chunks).decode("utf-8", errors="replace")
        stderr = b"".join(err_chunks).decode("utf-8", errors="replace")[:5000]
        if killed_for:
            return RunResult(status=killed_for, stdout=stdout, stderr=stderr, runtime_ms=runtime_ms)
        status = "ok" if proc.returncode == 0 else "runtime_error"
        return RunResult(status=status, stdout=stdout, stderr=stderr, runtime_ms=runtime_ms, returncode=proc.returncode)

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
