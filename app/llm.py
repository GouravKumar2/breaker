"""The ONLY module that talks to an LLM. Swap providers by rewriting these two functions."""
import re
import sys
import time
from typing import Type, TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel, ValidationError

from app.config import settings

T = TypeVar("T", bound=BaseModel)
_client: genai.Client | None = None

HTTP_TIMEOUT_MS = 90_000
MIN_GAP_S = 4.0   # free tier gemini-2.5-flash = 5 req/min. Use 4.0 for gemini-2.5-flash-lite
_last_call = 0.0


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
    # Flash models: skip "thinking" for speed (it makes big JSON outputs very slow).
    if "flash" in settings.gemini_model and "3.5" not in settings.gemini_model:
        kw["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
    return types.GenerateContentConfig(**kw)


def _throttle():
    global _last_call
    wait = MIN_GAP_S - (time.perf_counter() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.perf_counter()


def _call(contents, config: types.GenerateContentConfig):
    """One API call: throttled, and honors the server's suggested retry delay."""
    last = None
    for attempt in range(5):
        t0 = time.perf_counter()
        try:
            _throttle()
            _log(f"calling {settings.gemini_model} (attempt {attempt + 1})...")
            resp = _get_client().models.generate_content(
                model=settings.gemini_model, contents=contents, config=config
            )
            _log(f"got response in {time.perf_counter() - t0:.1f}s")
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