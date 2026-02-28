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

    def _build_prompt(self, alert: Alert, repo: str, branch: str) -> str:
        """Build a remediation prompt for Devin."""
        return (
            f"Fix a CodeQL security finding in the repository `{repo}` on branch `{branch}`.\n\n"
            f"**Alert #{alert.number}**: {alert.rule_id}\n"
            f"**Description**: {alert.rule_description}\n"
            f"**Severity**: {alert.severity}\n"
            f"**File**: `{alert.file_path}` (lines {alert.start_line}-{alert.end_line})\n"
            f"**Message**: {alert.message}\n\n"
            f"Instructions:\n"
            f"1. Clone the repo and checkout the `{branch}` branch\n"
            f"2. Read the affected file and understand the vulnerability\n"
            f"3. Fix the security issue following best practices\n"
            f"4. Make sure the fix doesn't break existing functionality\n"
            f"5. Commit and push directly to the `{branch}` branch\n"
            f"6. Do NOT create a PR — push directly to the branch\n\n"
            f"The fix should address the root cause, not just suppress the warning."
        )

    def _build_grouped_prompt(
        self, alerts: list[Alert], repo: str, branch: str,
    ) -> str:
        """Build a prompt for multiple alerts in the same file."""
        file_path = alerts[0].file_path
        alert_sections: list[str] = []
        for alert in alerts:
            alert_sections.append(
                f"- **Alert #{alert.number}**: {alert.rule_id}\n"
                f"  Description: {alert.rule_description}\n"
                f"  Severity: {alert.severity}\n"
                f"  Lines: {alert.start_line}-{alert.end_line}\n"
                f"  Message: {alert.message}"
            )
        alerts_text = "\n\n".join(alert_sections)
        alert_nums = ", ".join(f"#{a.number}" for a in alerts)
        return (
            f"Fix {len(alerts)} CodeQL security findings in the repository "
            f"`{repo}` on branch `{branch}`.\n\n"
            f"All alerts are in the same file: `{file_path}`\n\n"
            f"{alerts_text}\n\n"
            f"Instructions:\n"
            f"1. Clone the repo and checkout the `{branch}` branch\n"
            f"2. Read `{file_path}` and understand all {len(alerts)} "
            f"vulnerabilities (alerts {alert_nums})\n"
            f"3. Fix ALL security issues in a single edit\n"
            f"4. Make sure the fixes don't break existing functionality\n"
            f"5. Commit and push directly to the `{branch}` branch\n"
            f"6. Do NOT create a PR — push directly to the branch\n\n"
            f"Address the root cause of each issue."
        )

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

    async def create_remediation_session(self, alert: Alert, repo: str, branch: str) -> dict:
        """Create a Devin session to fix a specific alert."""
        prompt = self._build_prompt(alert, repo, branch)
        response = await self._request_with_retry(
            "POST",
            self._sessions_url,
            headers=self.headers,
            json={
                "prompt": prompt,
                "repos": [repo],
            },
        )
        return response.json()

    async def create_grouped_session(
        self, alerts: list[Alert], repo: str, branch: str,
    ) -> dict:
        """Create a Devin session to fix multiple alerts in the same file."""
        prompt = self._build_grouped_prompt(alerts, repo, branch)
        response = await self._request_with_retry(
            "POST",
            self._sessions_url,
            headers=self.headers,
            json={
                "prompt": prompt,
                "repos": [repo],
            },
        )
        return response.json()

    async def create_snapshot_session(
        self,
        prompt: str,
        snapshot_id: str,
        *,
        title: str | None = None,
        structured_output_schema: dict | None = None,
    ) -> dict:
        """Create a Devin session using the v1 API with a machine snapshot.

        The v1 endpoint (``POST /v1/sessions``) supports ``snapshot_id``
        which boots the session from a pre-configured machine image (e.g.
        with JDK/Ant pre-installed for running Tomcat tests).

        Returns ``{session_id, url, is_new_session}``.
        """
        body: dict[str, object] = {
            "prompt": prompt,
            "snapshot_id": snapshot_id,
        }
        if title:
            body["title"] = title
        if structured_output_schema:
            body["structured_output_schema"] = structured_output_schema

        response = await self._request_with_retry(
            "POST",
            "https://api.devin.ai/v1/sessions",
            headers=self.headers,
            json=body,
        )
        return response.json()

    async def get_session_status_v1(self, session_id: str) -> dict:
        """Get session status via the v1 API.

        Returns full session details including ``status_enum``,
        ``structured_output``, and ``messages``.
        """
        response = await self._request_with_retry(
            "GET",
            f"https://api.devin.ai/v1/sessions/{session_id}",
            headers=self.headers,
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

    async def send_message(self, session_id: str, message: str) -> None:
        """Send a message to an existing Devin session."""
        await self._request_with_retry(
            "POST",
            f"{self._sessions_url}/{session_id}/messages",
            headers=self.headers,
            json={"message": message},
        )

    def build_followup_message(
        self, alerts: list[Alert], repo: str, branch: str,
    ) -> str:
        """Build a follow-up message for the next batch of alerts.

        Used in the single-session model: after Devin reaches
        ``waiting_for_user``, we send it the next file group's alerts
        as a message instead of creating a new session.
        """
        file_path = alerts[0].file_path
        if len(alerts) == 1:
            a = alerts[0]
            return (
                f"Great work! Now fix the next CodeQL finding on the same branch `{branch}`.\n\n"
                f"**Alert #{a.number}**: {a.rule_id}\n"
                f"**Description**: {a.rule_description}\n"
                f"**Severity**: {a.severity}\n"
                f"**File**: `{a.file_path}` (lines {a.start_line}-{a.end_line})\n"
                f"**Message**: {a.message}\n\n"
                f"Instructions:\n"
                f"1. Make sure you're on the `{branch}` branch\n"
                f"2. Read the affected file and understand the vulnerability\n"
                f"3. Fix the security issue following best practices\n"
                f"4. Make sure the fix doesn't break existing functionality\n"
                f"5. Commit and push directly to the `{branch}` branch\n"
                f"6. Do NOT create a PR — push directly to the branch\n\n"
                f"The fix should address the root cause, not just suppress the warning."
            )

        alert_sections: list[str] = []
        for a in alerts:
            alert_sections.append(
                f"- **Alert #{a.number}**: {a.rule_id}\n"
                f"  Description: {a.rule_description}\n"
                f"  Severity: {a.severity}\n"
                f"  Lines: {a.start_line}-{a.end_line}\n"
                f"  Message: {a.message}"
            )
        alerts_text = "\n\n".join(alert_sections)
        alert_nums = ", ".join(f"#{a.number}" for a in alerts)
        return (
            f"Great work! Now fix {len(alerts)} more CodeQL findings on the same "
            f"branch `{branch}`.\n\n"
            f"All alerts are in the same file: `{file_path}`\n\n"
            f"{alerts_text}\n\n"
            f"Instructions:\n"
            f"1. Make sure you're on the `{branch}` branch\n"
            f"2. Read `{file_path}` and understand all {len(alerts)} "
            f"vulnerabilities (alerts {alert_nums})\n"
            f"3. Fix ALL security issues\n"
            f"4. Make sure the fixes don't break existing functionality\n"
            f"5. Commit and push directly to the `{branch}` branch\n"
            f"6. Do NOT create a PR — push directly to the branch\n\n"
            f"Address the root cause of each issue."
        )
