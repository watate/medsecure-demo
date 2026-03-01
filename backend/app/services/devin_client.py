import asyncio
import logging

import httpx

from app.config import settings
from app.models.schemas import Alert

logger = logging.getLogger(__name__)

# Retry configuration for 429 rate limits
_MAX_RETRIES = 10
_BASE_BACKOFF_SECONDS = 10.0
_MAX_BACKOFF_SECONDS = 320.0


class DevinClient:
    """Client for the Devin v3 Organization API.

    Uses https://api.devin.ai/v3/organizations/{org_id}/sessions endpoints.
    Requires DEVIN_API_KEY (service user credential) and DEVIN_ORG_ID.
    Retries on 429 with exponential backoff.
    """

    def __init__(self, api_key: str | None = None, org_id: str | None = None):
        self.api_key = api_key or settings.devin_api_key
        self.org_id = org_id or settings.devin_org_id
        self.base_url = "https://api.devin.ai/v3"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    @property
    def _sessions_url(self) -> str:
        return f"{self.base_url}/organizations/{self.org_id}/sessions"

    @staticmethod
    def build_prompt(alerts: list[Alert], repo: str, branch: str) -> str:
        """Build a remediation prompt for one or more alerts in the same file.

        Mirrors the prompt format used by the CodeQL Devin Remediation
        workflow (``codeql-devin-remediation.yml``).
        """
        file_path = alerts[0].file_path
        alert_sections: list[str] = []
        for alert in alerts:
            alert_sections.append(
                f"- Alert #{alert.number}: {alert.rule_id}\n"
                f"  Description: {alert.rule_description}\n"
                f"  Severity: {alert.severity}\n"
                f"  Lines: {alert.start_line}-{alert.end_line}\n"
                f"  Message: {alert.message}"
            )
        alerts_text = "\n\n".join(alert_sections)
        alert_nums = ", ".join(f"#{a.number}" for a in alerts)
        return (
            f"Fix {len(alerts)} CodeQL security finding(s) in `{repo}`.\n\n"
            f"All alerts are in: `{file_path}`\n\n"
            f"{alerts_text}\n\n"
            f"Instructions:\n"
            f"1. Clone the repo and checkout the `{branch}` branch\n"
            f"2. Read `{file_path}` and understand the {len(alerts)} "
            f"issue(s) (alerts {alert_nums})\n"
            f"3. Fix ALL security issues while preserving existing functionality\n"
            f"4. Run any existing tests to verify nothing is broken\n"
            f"5. Commit and push directly to the `{branch}` branch\n"
            f"6. Do NOT create a PR — push directly to the branch\n\n"
            f"Address root causes, not just suppress warnings."
        )

    @staticmethod
    def build_title(alerts: list[Alert]) -> str:
        """Build a human-readable session title."""
        file_path = alerts[0].file_path
        return f"Fix {len(alerts)} CodeQL alert(s) in {file_path}"

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        **kwargs: object,
    ) -> httpx.Response:
        """Make an HTTP request with retry on 429 rate limits."""
        for attempt in range(_MAX_RETRIES):
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.request(method, url, **kwargs)

            if response.status_code != 429:
                response.raise_for_status()
                return response

            # Respect Retry-After header if present, else exponential backoff
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                wait = min(float(retry_after), _MAX_BACKOFF_SECONDS)
            else:
                wait = min(_BASE_BACKOFF_SECONDS * (2 ** attempt), _MAX_BACKOFF_SECONDS)

            logger.warning(
                "Devin API 429 rate limited (attempt %d/%d), retrying in %.1fs",
                attempt + 1, _MAX_RETRIES, wait,
            )
            await asyncio.sleep(wait)

        # Final attempt — let it raise on any error
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(method, url, **kwargs)
        response.raise_for_status()
        return response

    async def create_session(
        self,
        alerts: list[Alert],
        repo: str,
        branch: str,
        *,
        max_acu_limit: int = 5,
        tags: list[str] | None = None,
    ) -> dict:
        """Create a Devin session to fix alerts in a single file.

        Mirrors the session-creation approach of the CodeQL Devin
        Remediation workflow (``codeql-devin-remediation.yml``).

        Returns ``{session_id, url, ...}`` from the Devin API.
        """
        prompt = self.build_prompt(alerts, repo, branch)
        title = self.build_title(alerts)

        body: dict[str, object] = {
            "prompt": prompt,
            "title": title,
            "repos": [repo],
            "max_acu_limit": max_acu_limit,
            "tags": tags or ["benchmark", "automated"],
        }

        response = await self._request_with_retry(
            "POST",
            self._sessions_url,
            headers=self.headers,
            json=body,
        )
        return response.json()

    async def get_session_status(self, session_id: str) -> dict:
        """Get the status of a Devin session.

        v3 response includes: session_id, status, acus_consumed,
        pull_requests [{pr_url, pr_state}], url, etc.
        """
        response = await self._request_with_retry(
            "GET",
            f"{self._sessions_url}/{session_id}",
            headers=self.headers,
        )
        return response.json()

    async def list_sessions(self) -> list[dict]:
        """List all sessions for the organization.

        GET /v3/organizations/{org_id}/sessions

        Returns the ``items`` array from the response. Each item includes
        ``session_id``, ``status``, ``status_detail``, ``acus_consumed``,
        ``url``, ``pull_requests``, etc.

        This endpoint reliably exposes ``status_detail`` (e.g.
        ``"waiting_for_user"``), which the single-session endpoint may omit.
        """
        response = await self._request_with_retry(
            "GET",
            self._sessions_url,
            headers=self.headers,
        )
        data = response.json()
        return data.get("items", [])
