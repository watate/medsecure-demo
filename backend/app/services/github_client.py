import asyncio
import io
import logging
import time
import zipfile

import httpx

from app.config import settings
from app.models.schemas import Alert, AlertWithCWE, BranchSummary

logger = logging.getLogger(__name__)


class GitHubClient:
    BASE_URL = "https://api.github.com"

    def __init__(self, token: str | None = None, repo: str | None = None):
        self.token = token or settings.github_token
        self.repo = repo or ""
        self.headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def list_accessible_repos(self, per_page: int = 100) -> list[dict]:
        """List repositories accessible by the configured PAT.

        Returns a list of dicts with repo metadata (full_name, description,
        default_branch, private, language, html_url).
        """
        repos: list[dict] = []
        page = 1

        async with httpx.AsyncClient(timeout=30.0) as client:
            while True:
                response = await client.get(
                    f"{self.BASE_URL}/user/repos",
                    headers=self.headers,
                    params={
                        "per_page": per_page,
                        "page": page,
                        "sort": "updated",
                        "direction": "desc",
                    },
                )
                response.raise_for_status()
                data = response.json()

                if not data:
                    break

                for item in data:
                    repos.append({
                        "full_name": item["full_name"],
                        "description": item.get("description"),
                        "default_branch": item.get("default_branch", "main"),
                        "private": item.get("private", False),
                        "language": item.get("language"),
                        "html_url": item.get("html_url", ""),
                    })

                if len(data) < per_page:
                    break
                page += 1

        return repos

    async def get_repo_info(self, repo: str | None = None) -> dict:
        """Get metadata for a single repository."""
        target = repo or self.repo
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.BASE_URL}/repos/{target}",
                headers=self.headers,
            )
            response.raise_for_status()
            item = response.json()
            return {
                "full_name": item["full_name"],
                "description": item.get("description"),
                "default_branch": item.get("default_branch", "main"),
                "private": item.get("private", False),
                "language": item.get("language"),
                "html_url": item.get("html_url", ""),
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
                            severity=rule.get("security_severity_level") or rule.get("severity") or "note",
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

    def compute_branch_summary(self, alerts: list[Alert], branch: str, tool_name: str) -> BranchSummary:
        """Compute a summary from a pre-fetched list of alerts."""
        return self._build_summary(alerts, branch, tool_name)

    async def get_branch_summary(self, branch: str, tool_name: str) -> BranchSummary:
        """Get a summary of alerts for a branch (fetches alerts internally)."""
        alerts = await self.get_alerts(branch)
        return self._build_summary(alerts, branch, tool_name)

    @staticmethod
    def _build_summary(alerts: list[Alert], branch: str, tool_name: str) -> BranchSummary:

        severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "other": 0}
        state_counts = {"open": 0, "fixed": 0, "dismissed": 0}

        for a in alerts:
            sev = a.severity.lower() if a.severity else "other"
            if sev in severity_counts:
                severity_counts[sev] += 1
            else:
                severity_counts["other"] += 1

            st = a.state.lower()
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

    async def get_alerts_with_cwe(self, branch: str, state: str | None = None) -> list[AlertWithCWE]:
        """Fetch CodeQL alerts enriched with CWE IDs from rule tags."""
        from app.services.compliance import parse_cwe_ids_from_tags

        enriched: list[AlertWithCWE] = []
        page = 1

        async with httpx.AsyncClient(timeout=30.0) as client:
            while True:
                params: dict[str, str | int] = {
                    "ref": f"refs/heads/{branch}",
                    "per_page": 100,
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
                    tags = rule.get("tags", [])
                    cwe_ids = parse_cwe_ids_from_tags(tags)

                    enriched.append(
                        AlertWithCWE(
                            number=item["number"],
                            rule_id=rule.get("id", ""),
                            rule_description=rule.get("description", ""),
                            severity=rule.get("security_severity_level") or rule.get("severity") or "note",
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
                            cwe_ids=cwe_ids,
                            rule_tags=tags,
                        )
                    )

                if len(data) < 100:
                    break
                page += 1

        return enriched

    async def get_alert_detail(self, alert_number: int) -> dict:
        """Get detailed information about a specific alert."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.BASE_URL}/repos/{self.repo}/code-scanning/alerts/{alert_number}",
                headers=self.headers,
            )
            response.raise_for_status()
            return response.json()

    async def get_branch_sha(self, branch: str) -> str:
        """Get the HEAD commit SHA of a branch."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.BASE_URL}/repos/{self.repo}/git/ref/heads/{branch}",
                headers=self.headers,
            )
            response.raise_for_status()
            return response.json()["object"]["sha"]

    async def create_branch(self, new_branch: str, from_branch: str = "main") -> str:
        """Create a new branch from an existing branch via GitHub API.

        Returns the SHA of the new branch HEAD.
        """
        sha = await self.get_branch_sha(from_branch)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.BASE_URL}/repos/{self.repo}/git/refs",
                headers=self.headers,
                json={
                    "ref": f"refs/heads/{new_branch}",
                    "sha": sha,
                },
            )
            response.raise_for_status()
            return response.json()["object"]["sha"]

    async def branch_exists(self, branch: str) -> bool:
        """Check if a branch exists."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.BASE_URL}/repos/{self.repo}/git/ref/heads/{branch}",
                headers=self.headers,
            )
            return response.status_code == 200

    # ------------------------------------------------------------------
    # Copilot Autofix helpers
    # ------------------------------------------------------------------

    async def trigger_autofix(self, alert_number: int) -> dict:
        """Trigger Copilot Autofix generation for a code-scanning alert.

        POST /repos/{owner}/{repo}/code-scanning/alerts/{number}/autofix
        Returns 202 on success (generation started).
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.BASE_URL}/repos/{self.repo}"
                f"/code-scanning/alerts/{alert_number}/autofix",
                headers=self.headers,
            )
            response.raise_for_status()
            return response.json()

    async def get_autofix_status(self, alert_number: int) -> dict:
        """Get autofix status and fix details for an alert.

        GET /repos/{owner}/{repo}/code-scanning/alerts/{number}/autofix
        Returns status (e.g. "pending", "succeeded", "failed") plus
        fix description and changes when succeeded.
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.BASE_URL}/repos/{self.repo}"
                f"/code-scanning/alerts/{alert_number}/autofix",
                headers=self.headers,
            )
            response.raise_for_status()
            return response.json()

    async def commit_autofix(
        self, alert_number: int, target_ref: str, message: str,
    ) -> dict:
        """Commit a Copilot Autofix to a branch.

        POST /repos/{owner}/{repo}/code-scanning/alerts/{number}/autofix/commits
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.BASE_URL}/repos/{self.repo}"
                f"/code-scanning/alerts/{alert_number}/autofix/commits",
                headers=self.headers,
                json={
                    "target_ref": f"refs/heads/{target_ref}",
                    "message": message,
                },
            )
            response.raise_for_status()
            return response.json()

    async def poll_autofix(
        self,
        alert_number: int,
        *,
        poll_interval: float = 3.0,
        max_wait: float = 120.0,
    ) -> dict:
        """Trigger autofix and poll until it completes or times out.

        Returns the final autofix status dict.
        """
        await self.trigger_autofix(alert_number)
        logger.info(
            "Triggered Copilot Autofix for alert #%d, polling...",
            alert_number,
        )

        deadline = time.monotonic() + max_wait
        while time.monotonic() < deadline:
            await asyncio.sleep(poll_interval)
            status = await self.get_autofix_status(alert_number)
            state = status.get("status", "")
            if state in ("succeeded", "success", "failed", "dismissed", "skipped"):
                return status
        # Timed out — return last known status
        return await self.get_autofix_status(alert_number)

    async def list_commits(
        self, branch: str, since_sha: str | None = None, per_page: int = 100,
    ) -> list[dict]:
        """List commits on a branch, optionally only those after *since_sha*.

        Returns a list of dicts with keys: sha, message, author, date.
        When *since_sha* is provided the returned list excludes that commit
        and all of its ancestors (i.e. only newer commits are returned).
        """
        params: dict[str, str | int] = {
            "sha": branch,
            "per_page": per_page,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.BASE_URL}/repos/{self.repo}/commits",
                headers=self.headers,
                params=params,
            )
            response.raise_for_status()
            raw_commits = response.json()

        commits: list[dict] = []
        for item in raw_commits:
            sha = item.get("sha", "")
            if since_sha and sha == since_sha:
                # We've reached the starting point — stop collecting
                break
            commit_obj = item.get("commit", {})
            author_obj = commit_obj.get("author", {})
            commits.append({
                "sha": sha,
                "message": commit_obj.get("message", ""),
                "author": author_obj.get("name", ""),
                "date": author_obj.get("date", ""),
            })
        return commits

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

    async def get_file_sha(self, path: str, ref: str) -> str:
        """Get the SHA of a file on a specific branch (needed for updates)."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.BASE_URL}/repos/{self.repo}/contents/{path}",
                headers=self.headers,
                params={"ref": ref},
            )
            response.raise_for_status()
            data = response.json()
            return data["sha"]

    async def update_file_content(
        self, path: str, new_content: str, branch: str, commit_message: str,
    ) -> str:
        """Update a file on a branch via the GitHub Contents API.

        Returns the commit SHA of the new commit.
        """
        import base64

        # Get current file SHA (required by the API)
        file_sha = await self.get_file_sha(path, branch)

        encoded = base64.b64encode(new_content.encode("utf-8")).decode("ascii")

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.put(
                f"{self.BASE_URL}/repos/{self.repo}/contents/{path}",
                headers=self.headers,
                json={
                    "message": commit_message,
                    "content": encoded,
                    "sha": file_sha,
                    "branch": branch,
                },
            )
            response.raise_for_status()
            data = response.json()
            return data.get("commit", {}).get("sha", "")

    # ------------------------------------------------------------------
    # GitHub Actions helpers (workflow runs & artifacts)
    # ------------------------------------------------------------------

    async def get_workflow_runs_for_branch(
        self,
        branch: str,
        *,
        workflow_name: str | None = None,
        per_page: int = 5,
    ) -> list[dict]:
        """Get the most recent workflow runs for a branch.

        Optionally filters by *workflow_name* (the ``name:`` field in the
        workflow YAML, matched case-insensitively).

        Returns a list of run dicts with keys: id, name, status,
        conclusion, html_url, created_at, updated_at, head_sha.
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.BASE_URL}/repos/{self.repo}/actions/runs",
                headers=self.headers,
                params={
                    "branch": branch,
                    "per_page": per_page,
                },
            )
            response.raise_for_status()
            data = response.json()

        runs: list[dict] = []
        for item in data.get("workflow_runs", []):
            if workflow_name and item.get("name", "").lower() != workflow_name.lower():
                continue
            runs.append({
                "id": item["id"],
                "name": item.get("name", ""),
                "status": item.get("status", ""),          # queued, in_progress, completed
                "conclusion": item.get("conclusion"),       # success, failure, cancelled, …
                "html_url": item.get("html_url", ""),
                "created_at": item.get("created_at", ""),
                "updated_at": item.get("updated_at", ""),
                "head_sha": item.get("head_sha", ""),
            })
        return runs

    async def get_run_artifacts(self, run_id: int) -> list[dict]:
        """List artifacts produced by a workflow run.

        Returns a list of dicts with keys: id, name, size_in_bytes,
        archive_download_url, created_at, expired.
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.BASE_URL}/repos/{self.repo}/actions/runs/{run_id}/artifacts",
                headers=self.headers,
            )
            response.raise_for_status()
            data = response.json()

        return [
            {
                "id": a["id"],
                "name": a.get("name", ""),
                "size_in_bytes": a.get("size_in_bytes", 0),
                "archive_download_url": a.get("archive_download_url", ""),
                "created_at": a.get("created_at", ""),
                "expired": a.get("expired", False),
            }
            for a in data.get("artifacts", [])
        ]

    async def download_artifact_zip(self, artifact_id: int) -> dict[str, str]:
        """Download an artifact zip and return its contents as {filename: text}.

        Only text-decodable files are included; binary files are skipped.
        """
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            response = await client.get(
                f"{self.BASE_URL}/repos/{self.repo}/actions/artifacts/{artifact_id}/zip",
                headers=self.headers,
            )
            response.raise_for_status()

        files: dict[str, str] = {}
        with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
            for name in zf.namelist():
                if name.endswith("/"):
                    continue  # skip directories
                try:
                    files[name] = zf.read(name).decode("utf-8")
                except UnicodeDecodeError:
                    logger.debug("Skipping binary file in artifact: %s", name)
        return files
