"""The ONLY module that talks to an LLM. Swap providers by rewriting these two functions."""
import hashlib
import json
import os
import re
import sys
import threading
import time
from typing import Type, TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel, ValidationError

from app.config import settings

T = TypeVar("T", bound=BaseModel)
_client: genai.Client | None = None

HTTP_TIMEOUT_MS = 90_000
MIN_GAP_S = 4.0   # seconds between calls (free tier is rate limited); the throttle is lock-protected
_last_call = 0.0
_throttle_lock = threading.Lock()

# --- optional on-disk response cache (used by the eval so reruns are free and resumable) ---
_cache: dict[str, dict] | None = None
_cache_path: str | None = None
_cache_lock = threading.Lock()
STATS = {"calls": 0, "hits": 0, "replayed_s": 0.0}   # eval reads deltas of this


class LLMError(RuntimeError):
    pass


def _log(msg: str):
    print(f"[llm] {msg}", file=sys.stderr, flush=True)


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not settings.gemini_api_key:
            raise LLMError("GEMINI_API_KEY is not set in .env")
        _client = genai.Client(
            api_key=settings.gemini_api_key,
            http_options=types.HttpOptions(api_version="v1beta", timeout=HTTP_TIMEOUT_MS),
        )
    return _client


def _cfg(**kw) -> types.GenerateContentConfig:
    # gemini-2.5 flash models: turn "thinking" off for speed (it makes big JSON outputs very slow).
    # Other models keep their default unless GEMINI_THINKING_BUDGET is set explicitly.
    budget = settings.gemini_thinking_budget
    if budget is None and settings.gemini_model.startswith("gemini-2.5"):
        budget = 0
    if budget is not None:
        kw["thinking_config"] = types.ThinkingConfig(thinking_budget=budget)
    return types.GenerateContentConfig(**kw)


def _throttle():
    global _last_call
    with _throttle_lock:   # held while sleeping so concurrent threads queue up instead of racing
        wait = MIN_GAP_S - (time.perf_counter() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.perf_counter()


def enable_cache(path: str) -> int:
    """Turn on the JSONL response cache. Returns the number of entries loaded.
    Append-only, so a crash mid-run loses nothing that was already written."""
    global _cache, _cache_path
    _cache, _cache_path = {}, path
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                    _cache[row["k"]] = row
                except (ValueError, KeyError):
                    continue   # torn last line from a crash
    return len(_cache)


def _jsonable(x):
    if hasattr(x, "model_dump"):
        x = x.model_dump(exclude_none=True)
    return x


def _cache_key(contents, config) -> str:
    payload = {"model": settings.gemini_model, "salt": os.environ.get("BREAKER_CACHE_SALT", ""),
               "contents": contents if isinstance(contents, str) else [_jsonable(c) for c in contents],
               "config": _jsonable(config)}
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _call(contents, config: types.GenerateContentConfig):
    """One API call: throttled, and honors the server's suggested retry delay."""
    key = None
    if _cache is not None:
        key = _cache_key(contents, config)
        with _cache_lock:
            row = _cache.get(key)
        if row is not None:
            STATS["hits"] += 1
            STATS["replayed_s"] += row.get("latency", 0.0)
            return types.GenerateContentResponse.model_validate_json(row["resp"])
    last = None
    for attempt in range(5):
        t0 = time.perf_counter()
        try:
            _throttle()
            _log(f"calling {settings.gemini_model} (attempt {attempt + 1})...")
            resp = _get_client().models.generate_content(
                model=settings.gemini_model, contents=contents, config=config
            )
            latency = time.perf_counter() - t0
            _log(f"got response in {latency:.1f}s")
            STATS["calls"] += 1
            if key is not None:
                row = {"k": key, "model": settings.gemini_model, "latency": round(latency, 2),
                       "resp": resp.model_dump_json(exclude_none=True)}
                with _cache_lock:
                    _cache[key] = row
                    with open(_cache_path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(row) + "\n")
            return resp
        except Exception as e:
            last = e
            msg = str(e)
            retryable = any(k in msg for k in ("429", "503", "RESOURCE_EXHAUSTED", "UNAVAILABLE",
                                               "timed out", "Timeout", "timeout", "DEADLINE"))
            _log(f"error after {time.perf_counter() - t0:.1f}s: {msg[:160]}")
            if retryable and attempt < 4:
                m = re.search(r"retry in ([\d.]+)s", msg)
                wait = float(m.group(1)) + 2 if m else 5 * (attempt + 1)
                _log(f"retrying in {wait:.0f}s")
                time.sleep(wait)
                continue
            raise
    raise LLMError(f"LLM call failed after retries: {last}")


def generate_text(prompt: str, system: str | None = None, temperature: float = 0.3) -> str:
    cfg = _cfg(system_instruction=system, temperature=temperature)
    return _call(prompt, cfg).text or ""


def generate_structured(
    prompt: str,
    schema: Type[T],
    system: str | None = None,
    temperature: float = 0.7,
    retries: int = 1,
) -> T:
    """Return a validated pydantic object; retry once with the error fed back."""
    cfg = _cfg(
        system_instruction=system,
        temperature=temperature,
        response_mime_type="application/json",
        response_schema=schema,
    )
    current = prompt
    last_err: Exception | None = None
    for _ in range(retries + 1):
        resp = _call(current, cfg)
        try:
            if isinstance(resp.parsed, schema):
                return resp.parsed
            return schema.model_validate_json(resp.text or "")
        except (ValidationError, ValueError) as e:
            last_err = e
            current = (
                f"{prompt}\n\nYour previous output was invalid ({e}). "
                "Return ONLY valid JSON matching the schema."
            )
    raise LLMError(f"Invalid structured output after retry: {last_err}")