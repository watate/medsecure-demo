# Devin CodeQL Remediation — Setup Guide

Automated workflow that fetches CodeQL alerts from your repo and dispatches Devin to fix them in batch, opening one PR per file group.

## Prerequisites

- **CodeQL** must be enabled on your repo (Settings > Code security > Code scanning)
- **Devin GitHub app** must have access to your repo (so Devin can clone, branch, and open PRs)

## Step 1 — Devin Account

Accept the Devin trial account invitation (sent to your email) and sign in.

## Step 2 — Create Secrets

You'll need four required secrets and one optional secret. All values come from the Devin dashboard.

| Secret | Where to find it | Required |
|--------|-------------------|----------|
| `DEVIN_API_KEY` | Service Users > Provision service user (starts with `cog_`) | Yes |
| `DEVIN_ORG_ID` | Service Users (shown at the top of the page) | Yes |
| `DEVIN_PLAYBOOK_ID` | Playbooks > Create team playbook using `parent-playbook.md` | Yes |
| `DEVIN_CHILD_PLAYBOOK_ID` | Playbooks > Create team playbook using `child-playbook.md` | Yes |
| `DEVIN_USER_ID` | Team > copy the user ID you want sessions attributed to | No |

`DEVIN_USER_ID` is optional — it makes batch sessions appear under a specific user in the Devin web app. The service user needs the `ImpersonateOrgSessions` permission for this to work.

## Step 3 — Add Secrets to GitHub

Add each secret to your GitHub repo. You can use the CLI or the web UI:

```bash
gh secret set DEVIN_API_KEY
gh secret set DEVIN_ORG_ID
gh secret set DEVIN_PLAYBOOK_ID
gh secret set DEVIN_CHILD_PLAYBOOK_ID
# Optional:
gh secret set DEVIN_USER_ID
```

Or: repo Settings > Secrets and variables > Actions > New repository secret.

## Step 4 — Connect Your Repo to Devin

1. In the Devin dashboard, go to **Repositories** and add your repo
2. Go to **Machine configuration** and set up a machine snapshot with your repo's linters, test runners, and dependency managers so Devin can validate its fixes

## Step 5 — Add the Workflow File

Copy `codeql-devin-remediation.yml` into your repo:

```bash
mkdir -p .github/workflows
cp codeql-devin-remediation.yml .github/workflows/
git add .github/workflows/codeql-devin-remediation.yml
git commit -m "ci: add CodeQL Devin auto-remediation workflow"
git push
```

## Step 6 — Run It

The workflow runs automatically every Monday at 9am UTC. To trigger it manually:

1. Go to **Actions** > **CodeQL Devin Remediation** > **Run workflow**
2. Optionally adjust the ACU budget and severity filter
3. Click **Run workflow**

The job summary will show a link to the Devin batch session. Child sessions and their PRs will appear in the Devin web app.
