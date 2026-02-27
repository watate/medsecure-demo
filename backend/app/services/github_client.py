import httpx

from app.config import settings
from app.models.schemas import Alert, BranchSummary


class GitHubClient:
    BASE_URL = "https://api.github.com"

    def __init__(self, token: str | None = None, repo: str | None = None):
        self.token = token or settings.github_token
        self.repo = repo or settings.github_repo
        self.headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def get_alerts(self, branch: str, state: str | None = None, per_page: int = 100) -> list[Alert]:
        """Fetch CodeQL alerts for a specific branch."""
        alerts: list[Alert] = []
        page = 1

        async with httpx.AsyncClient(timeout=30.0) as client:
            while True:
                params: dict[str, str | int] = {
                    "ref": f"refs/heads/{branch}",
                    "per_page": per_page,
                    "page": page,
                }
                if state:
                    params["state"] = state

                response = await client.get(
                    f"{self.BASE_URL}/repos/{self.repo}/code-scanning/alerts",
                    headers=self.headers,
                    params=params,
                )
                response.raise_for_status()
                data = response.json()

                if not data:
                    break

                for item in data:
                    rule = item.get("rule", {})
                    most_recent = item.get("most_recent_instance", {})
                    location = most_recent.get("location", {})

                    alerts.append(
                        Alert(
                            number=item["number"],
                            rule_id=rule.get("id", ""),
                            rule_description=rule.get("description", ""),
                            severity=rule.get("security_severity_level", rule.get("severity", "note")),
                            state=item.get("state", "open"),
                            tool=item.get("tool", {}).get("name", "CodeQL"),
                            file_path=location.get("path", ""),
                            start_line=location.get("start_line", 0),
                            end_line=location.get("end_line", 0),
                            message=most_recent.get("message", {}).get("text", ""),
                            html_url=item.get("html_url", ""),
                            created_at=item.get("created_at", ""),
                            dismissed_at=item.get("dismissed_at"),
                            fixed_at=item.get("fixed_at"),
                        )
                    )

                if len(data) < per_page:
                    break
                page += 1

        return alerts

    async def get_branch_summary(self, branch: str, tool_name: str) -> BranchSummary:
        """Get a summary of alerts for a branch."""
        alerts = await self.get_alerts(branch)

        severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "other": 0}
        state_counts = {"open": 0, "fixed": 0, "dismissed": 0}

        for alert in alerts:
            sev = alert.severity.lower() if alert.severity else "other"
            if sev in severity_counts:
                severity_counts[sev] += 1
            else:
                severity_counts["other"] += 1

            st = alert.state.lower()
            if st in state_counts:
                state_counts[st] += 1

        return BranchSummary(
            branch=branch,
            tool=tool_name,
            total=len(alerts),
            open=state_counts["open"],
            fixed=state_counts["fixed"],
            dismissed=state_counts["dismissed"],
            critical=severity_counts["critical"],
            high=severity_counts["high"],
            medium=severity_counts["medium"],
            low=severity_counts["low"],
            other=severity_counts["other"],
        )

    async def get_alert_detail(self, alert_number: int) -> dict:
        """Get detailed information about a specific alert."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.BASE_URL}/repos/{self.repo}/code-scanning/alerts/{alert_number}",
                headers=self.headers,
            )
            response.raise_for_status()
            return response.json()

    async def get_file_content(self, path: str, ref: str) -> str:
        """Get file content from a specific branch."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.BASE_URL}/repos/{self.repo}/contents/{path}",
                headers=self.headers,
                params={"ref": ref},
            )
            response.raise_for_status()
            data = response.json()
            import base64

            return base64.b64decode(data["content"]).decode("utf-8")
