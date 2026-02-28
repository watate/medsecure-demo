"""Unified LLM client for API-based remediation.

Calls Anthropic (claude-opus-4-6), OpenAI (gpt-5.3-codex), or
Google (gemini-3.1-pro-preview) to generate security fixes.
Uses httpx directly to avoid heavy SDK dependencies.
"""

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class LLMResult:
    """Rich result from an LLM call, capturing data for replay."""

    tool: str
    model: str
    extracted_code: str
    raw_response_text: str
    latency_ms: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    prompt_preview: str = ""
    error: str | None = None
    extra: dict[str, object] = field(default_factory=dict)

# Model identifiers
ANTHROPIC_MODEL = "claude-opus-4-6"
OPENAI_MODEL = "gpt-5.3-codex"
GEMINI_MODEL = "gemini-3.1-pro-preview"

# API endpoints
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Timeout for LLM API calls (generous — large files can take a while)
LLM_TIMEOUT = 120.0

# Delay between sequential API calls (seconds) to avoid rate limits
INTER_CALL_DELAY = 2.0


def _extract_code_from_response(text: str) -> str:
    """Extract file content from LLM response.

    The prompt asks for ONLY the fixed file content, but models sometimes
    wrap it in markdown code fences. Strip those if present.
    """
    # Try to extract content from code fences
    fence_match = re.search(r"```(?:\w+)?\n(.*)```", text, re.DOTALL)
    if fence_match:
        return fence_match.group(1).rstrip("\n")
    # If no fences, return the raw text (trimmed)
    return text.strip()


async def call_anthropic(prompt: str) -> LLMResult:
    """Call Anthropic Messages API and return a rich result."""
    if not settings.anthropic_api_key:
        raise ValueError("ANTHROPIC_API_KEY not configured")

    start = time.monotonic()
    async with httpx.AsyncClient(timeout=LLM_TIMEOUT) as client:
        response = await client.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": settings.anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": ANTHROPIC_MODEL,
                "max_tokens": 16384,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        response.raise_for_status()
        data = response.json()

    latency_ms = int((time.monotonic() - start) * 1000)

    # Anthropic returns content as a list of blocks
    content_blocks = data.get("content", [])
    text_parts = [block["text"] for block in content_blocks if block.get("type") == "text"]
    raw_text = "\n".join(text_parts)

    usage = data.get("usage", {})

    return LLMResult(
        tool="anthropic",
        model=ANTHROPIC_MODEL,
        extracted_code=_extract_code_from_response(raw_text),
        raw_response_text=raw_text,
        latency_ms=latency_ms,
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        prompt_preview=prompt[:500],
    )


async def call_openai(prompt: str) -> LLMResult:
    """Call OpenAI Chat Completions API and return a rich result."""
    if not settings.openai_api_key:
        raise ValueError("OPENAI_API_KEY not configured")

    start = time.monotonic()
    async with httpx.AsyncClient(timeout=LLM_TIMEOUT) as client:
        response = await client.post(
            OPENAI_API_URL,
            headers={
                "Authorization": f"Bearer {settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENAI_MODEL,
                "max_tokens": 16384,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        response.raise_for_status()
        data = response.json()

    latency_ms = int((time.monotonic() - start) * 1000)

    choices = data.get("choices", [])
    if not choices:
        raise ValueError("OpenAI returned no choices")

    raw_text = choices[0].get("message", {}).get("content", "")
    usage = data.get("usage", {})

    return LLMResult(
        tool="openai",
        model=OPENAI_MODEL,
        extracted_code=_extract_code_from_response(raw_text),
        raw_response_text=raw_text,
        latency_ms=latency_ms,
        input_tokens=usage.get("prompt_tokens"),
        output_tokens=usage.get("completion_tokens"),
        prompt_preview=prompt[:500],
    )


async def call_gemini(prompt: str) -> LLMResult:
    """Call Google Gemini API and return a rich result."""
    if not settings.gemini_api_key:
        raise ValueError("GEMINI_API_KEY not configured")

    url = GEMINI_API_URL.format(model=GEMINI_MODEL)

    start = time.monotonic()
    async with httpx.AsyncClient(timeout=LLM_TIMEOUT) as client:
        response = await client.post(
            url,
            params={"key": settings.gemini_api_key},
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "maxOutputTokens": 16384,
                },
            },
        )
        response.raise_for_status()
        data = response.json()

    latency_ms = int((time.monotonic() - start) * 1000)

    candidates = data.get("candidates", [])
    if not candidates:
        raise ValueError("Gemini returned no candidates")

    parts = candidates[0].get("content", {}).get("parts", [])
    text_parts = [p["text"] for p in parts if "text" in p]
    raw_text = "\n".join(text_parts)

    # Gemini usage metadata
    usage_meta = data.get("usageMetadata", {})

    return LLMResult(
        tool="gemini",
        model=GEMINI_MODEL,
        extracted_code=_extract_code_from_response(raw_text),
        raw_response_text=raw_text,
        latency_ms=latency_ms,
        input_tokens=usage_meta.get("promptTokenCount"),
        output_tokens=usage_meta.get("candidatesTokenCount"),
        prompt_preview=prompt[:500],
    )


# Map tool name to caller
_TOOL_CALLERS = {
    "anthropic": call_anthropic,
    "openai": call_openai,
    "gemini": call_gemini,
}


async def call_llm(tool: str, prompt: str) -> LLMResult:
    """Call the appropriate LLM for the given tool name.

    Returns an LLMResult with extracted code and rich metadata.
    Raises ValueError if the tool is unknown or the API key is missing.
    Raises httpx.HTTPStatusError on API errors.
    """
    caller = _TOOL_CALLERS.get(tool)
    if not caller:
        raise ValueError(f"Unknown LLM tool: {tool}. Must be one of {list(_TOOL_CALLERS.keys())}")
    return await caller(prompt)


async def call_llm_with_delay(tool: str, prompt: str) -> LLMResult:
    """Call LLM and wait a short delay afterwards to avoid rate limits."""
    result = await call_llm(tool, prompt)
    await asyncio.sleep(INTER_CALL_DELAY)
    return result
