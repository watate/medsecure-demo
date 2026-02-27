"""Unified LLM client for API-based remediation.

Calls Anthropic (claude-opus-4-6), OpenAI (gpt-5.3-codex), or
Google (gemini-3.1-pro-preview) to generate security fixes.
Uses httpx directly to avoid heavy SDK dependencies.
"""

import asyncio
import logging
import re

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

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
    fence_match = re.search(r"```(?:\w+)?\n(.*?)```", text, re.DOTALL)
    if fence_match:
        return fence_match.group(1).rstrip("\n")
    # If no fences, return the raw text (trimmed)
    return text.strip()


async def call_anthropic(prompt: str) -> str:
    """Call Anthropic Messages API and return the generated text."""
    if not settings.anthropic_api_key:
        raise ValueError("ANTHROPIC_API_KEY not configured")

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

        # Anthropic returns content as a list of blocks
        content_blocks = data.get("content", [])
        text_parts = [block["text"] for block in content_blocks if block.get("type") == "text"]
        raw_text = "\n".join(text_parts)

        return _extract_code_from_response(raw_text)


async def call_openai(prompt: str) -> str:
    """Call OpenAI Chat Completions API and return the generated text."""
    if not settings.openai_api_key:
        raise ValueError("OPENAI_API_KEY not configured")

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

        choices = data.get("choices", [])
        if not choices:
            raise ValueError("OpenAI returned no choices")

        raw_text = choices[0].get("message", {}).get("content", "")
        return _extract_code_from_response(raw_text)


async def call_gemini(prompt: str) -> str:
    """Call Google Gemini API and return the generated text."""
    if not settings.gemini_api_key:
        raise ValueError("GEMINI_API_KEY not configured")

    url = GEMINI_API_URL.format(model=GEMINI_MODEL)

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

        candidates = data.get("candidates", [])
        if not candidates:
            raise ValueError("Gemini returned no candidates")

        parts = candidates[0].get("content", {}).get("parts", [])
        text_parts = [p["text"] for p in parts if "text" in p]
        raw_text = "\n".join(text_parts)

        return _extract_code_from_response(raw_text)


# Map tool name to caller
_TOOL_CALLERS = {
    "anthropic": call_anthropic,
    "openai": call_openai,
    "gemini": call_gemini,
}


async def call_llm(tool: str, prompt: str) -> str:
    """Call the appropriate LLM for the given tool name.

    Returns the extracted fixed file content.
    Raises ValueError if the tool is unknown or the API key is missing.
    Raises httpx.HTTPStatusError on API errors.
    """
    caller = _TOOL_CALLERS.get(tool)
    if not caller:
        raise ValueError(f"Unknown LLM tool: {tool}. Must be one of {list(_TOOL_CALLERS.keys())}")
    return await caller(prompt)


async def call_llm_with_delay(tool: str, prompt: str) -> str:
    """Call LLM and wait a short delay afterwards to avoid rate limits."""
    result = await call_llm(tool, prompt)
    await asyncio.sleep(INTER_CALL_DELAY)
    return result
