"""Thin wrapper around the LLM: system prompt + JSON schema in, validated object out.

Provider is chosen with LLM_PROVIDER in .env: "openai" (default) or "gemini".
Both use structured outputs, so the reply is forced to match the Pydantic schema.
Includes a simple rate limiter and retries.
"""
from __future__ import annotations

import json
import random
import threading
import time
from typing import Type, TypeVar

from pydantic import BaseModel, ValidationError

from . import config

T = TypeVar("T", bound=BaseModel)

_lock = threading.Lock()
_last_call = 0.0
STATS = {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0}

_openai = None
_gemini = None

CREDIT_WORDS = ("insufficient_quota", "no credits", "credits remaining", "billing", "exceeded your current quota",
                "perday", "per day")


class OutOfCredits(RuntimeError):
    """The LLM account has no credits/quota left. Stops the whole run instead of failing app by app."""


ABORTED: str | None = None  # set once credits run out, so queued apps stop immediately


def _wait_for_slot():
    """Spread calls evenly so we never exceed LLM_RPM."""
    global _last_call
    gap = 60.0 / max(config.LLM_RPM, 1)
    with _lock:
        sleep_for = _last_call + gap - time.time()
        if sleep_for > 0:
            time.sleep(sleep_for)
        _last_call = time.time()


def _record(usage: dict):
    STATS["llm_calls"] += 1
    STATS["input_tokens"] += usage["input_tokens"]
    STATS["output_tokens"] += usage["output_tokens"]
    return usage


# ------------------------------------------------------------------ OpenAI
def _call_openai(model: str, system: str, prompt: str, schema: Type[T]) -> tuple[T, dict]:
    global _openai
    from openai import OpenAI
    if _openai is None:
        if not config.OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is missing. Add it to your .env file.")
        _openai = OpenAI(api_key=config.OPENAI_API_KEY)
    kwargs = {}
    if config.OPENAI_REASONING_EFFORT and model.startswith(("gpt-5", "o1", "o3", "o4")):  # only reasoning models accept it
        kwargs["reasoning_effort"] = config.OPENAI_REASONING_EFFORT
    resp = _openai.chat.completions.parse(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        response_format=schema,
        **kwargs,
    )
    msg = resp.choices[0].message
    if msg.parsed is None:
        raise ValueError(f"model returned no parsed output (refusal: {msg.refusal})")
    u = resp.usage
    return msg.parsed, {"model": model, "input_tokens": getattr(u, "prompt_tokens", 0) or 0,
                        "output_tokens": getattr(u, "completion_tokens", 0) or 0}


def _openai_retryable(e: Exception) -> tuple[bool, float]:
    import openai
    if isinstance(e, openai.RateLimitError):
        if any(w in str(e).lower() for w in CREDIT_WORDS):
            raise OutOfCredits("OpenAI credits ran out. Add credits at platform.openai.com/settings/billing, "
                               "or switch to Gemini's free tier (LLM_PROVIDER=gemini).") from e
        return True, 20.0
    if isinstance(e, (openai.APIConnectionError, openai.APITimeoutError, openai.InternalServerError)):
        return True, 5.0
    return False, 0.0


# ------------------------------------------------------------------ Gemini
def _call_gemini(model: str, system: str, prompt: str, schema: Type[T]) -> tuple[T, dict]:
    global _gemini
    from google import genai
    from google.genai import types
    if _gemini is None:
        if not config.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is missing. Add it to your .env file.")
        _gemini = genai.Client(api_key=config.GEMINI_API_KEY)
    cfg = types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json",
                                      response_schema=schema, temperature=0)
    resp = _gemini.models.generate_content(model=model, contents=prompt, config=cfg)
    obj = resp.parsed if isinstance(resp.parsed, schema) else schema.model_validate_json(resp.text)
    um = getattr(resp, "usage_metadata", None)
    return obj, {"model": model, "input_tokens": getattr(um, "prompt_token_count", 0) or 0,
                 "output_tokens": getattr(um, "candidates_token_count", 0) or 0}


def _gemini_retryable(e: Exception) -> tuple[bool, float]:
    from google.genai import errors
    if isinstance(e, errors.ClientError) and getattr(e, "code", None) == 429:
        if any(w in str(e).lower() for w in CREDIT_WORDS):
            raise OutOfCredits("Gemini daily free quota is used up. Try again tomorrow or use another key/model.") from e
        return True, 20.0
    if isinstance(e, errors.ServerError):
        return True, 5.0
    return False, 0.0


# ------------------------------------------------------------------ public
def generate_json(model: str, system: str, prompt: str, schema: Type[T], retries: int = 4) -> tuple[T, dict]:
    """Call the model and return (parsed object, usage dict)."""
    if config.MOCK:
        from . import mock
        if config.MOCK_DELAY:
            time.sleep(config.MOCK_DELAY * (0.5 + random.random()))
        STATS["llm_calls"] += 1
        return mock.fake_llm(schema, prompt), {"model": f"mock:{model}", "input_tokens": len(prompt) // 4,
                                               "output_tokens": 200}

    global ABORTED
    if ABORTED:
        raise OutOfCredits(ABORTED)
    call, retryable = (_call_gemini, _gemini_retryable) if config.LLM_PROVIDER == "gemini" \
        else (_call_openai, _openai_retryable)
    last_err: Exception | None = None
    for attempt in range(retries):
        _wait_for_slot()
        try:
            obj, usage = call(model, system, prompt, schema)
            return obj, _record(usage)
        except (ValidationError, json.JSONDecodeError, ValueError) as e:  # bad/empty output: try again
            last_err = e
            time.sleep(2 + random.random() * 2)
        except RuntimeError:
            raise
        except Exception as e:
            try:
                ok, wait = retryable(e)
            except OutOfCredits as oc:
                ABORTED = str(oc)
                raise
            if not ok:
                raise
            last_err = e
            wait *= attempt + 1
            print(f"   LLM busy/rate limited, retrying in {wait:.0f}s ...")
            time.sleep(wait)
    raise RuntimeError(f"LLM call failed after {retries} attempts: {last_err}")
