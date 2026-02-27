"""Validate Better Auth session tokens by calling the Next.js auth API."""

import logging

import httpx
from fastapi import Depends, HTTPException, Request

from app.config import settings

logger = logging.getLogger(__name__)


async def get_session_token(request: Request) -> str:
    """Extract the Better Auth session token from the request."""
    # Check cookie first (same-origin requests)
    token = request.cookies.get("better-auth.session_token")
    if not token:
        token = request.cookies.get("__Secure-better-auth.session_token")

    # Check Authorization header (cross-origin / API clients)
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    return token


async def validate_session(
    token: str = Depends(get_session_token),
) -> dict:
    """Validate a Better Auth session token against the Next.js auth API.

    Returns the user dict if valid, raises 401 otherwise.
    """
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{settings.next_auth_url}/api/auth/get-session",
                headers={
                    "Cookie": f"better-auth.session_token={token}",
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                user = data.get("user")
                if user:
                    return user
    except httpx.RequestError:
        logger.exception("Failed to validate session against Next.js auth")

    raise HTTPException(status_code=401, detail="Invalid or expired session")
