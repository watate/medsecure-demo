"""Validate Better Auth session tokens directly against the auth SQLite database.

Follows the pattern from ktph-backend: direct DB lookup with a TTL cache
instead of making HTTP calls to the frontend.
"""

import logging
import sqlite3
import time

from fastapi import Depends, HTTPException, Request

from app.config import settings

logger = logging.getLogger(__name__)

# Simple TTL cache: {token: (user_dict, expiry_timestamp)}
_cache: dict[str, tuple[dict, float]] = {}
_CACHE_TTL = 60  # seconds


def _get_auth_db() -> sqlite3.Connection:
    """Open a connection to the better-auth SQLite database."""
    conn = sqlite3.connect(settings.auth_db_path)
    conn.row_factory = sqlite3.Row
    return conn


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
    """Validate a Better Auth session token against the auth database.

    Better Auth tokens are formatted as "{token}.{signature}" —
    only the part before the dot is stored in the DB.

    Returns the user dict if valid, raises 401 otherwise.
    """
    # Extract the DB token (before the dot)
    db_token = token.split(".")[0]

    # Check cache first
    if db_token in _cache:
        user, expires = _cache[db_token]
        if time.monotonic() < expires:
            return user
        del _cache[db_token]

    # Query the auth database directly
    try:
        conn = _get_auth_db()
        try:
            cursor = conn.execute(
                """
                SELECT u.id AS user_id, u.name, u.email
                FROM session s
                JOIN user u ON s.userId = u.id
                WHERE s.token = ? AND s.expiresAt > datetime('now')
                """,
                (db_token,),
            )
            row = cursor.fetchone()
        finally:
            conn.close()

        if row:
            user = dict(row)
            _cache[db_token] = (user, time.monotonic() + _CACHE_TTL)
            return user
    except sqlite3.OperationalError:
        logger.exception("Failed to query auth database at %s", settings.auth_db_path)

    raise HTTPException(status_code=401, detail="Invalid or expired session")
