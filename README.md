# MedSecure

Automated CodeQL remediation across AI tools (Devin, Anthropic, OpenAI, Google). Point it at any repo with CodeQL enabled — it creates branches, fixes vulnerabilities, and records every step for replay.

## How It Works

1. **Scan** — Fetches open CodeQL alerts from your repo's `main` branch via GitHub API.
2. **Branch** — Creates a fresh `remediate/{tool}-{timestamp}` branch from `main` automatically. No manual branch setup needed.
3. **Group & Fix** — Groups alerts by file so multiple vulnerabilities in the same file are fixed in one pass (one LLM call, one commit per file). Avoids merge conflicts.
4. **Replay** — Every step is recorded with rich metadata (model, tokens, latency, commit SHAs). The VP of Engineering can play back any run to stakeholders without re-running it.

Supports: **Devin** (autonomous agent sessions), **Anthropic** (Claude), **OpenAI** (GPT), **Google** (Gemini).

## Architecture

- **Backend**: FastAPI + SQLite — GitHub API integration, LLM orchestration, replay recording
- **Frontend**: Next.js + shadcn/ui — dashboard, alert browser, remediation log, reports, replay timeline
- **Auth**: [better-auth](https://www.better-auth.com/) with SQLite — email/password login, session cookies
- **Infra**: Terraform (EC2 + S3), Docker Compose (Caddy + API + Web), GitHub Actions CI/CD

## Quick Start (Local)

### 1. Prepare your target repo

Enable CodeQL on the repo you want to scan:

1. Repo -> Settings -> Advanced Security 
2. Find Code scanning → CodeQL analysis and click three dots -> Switch to advanced
3. Update codeql.yml to scan all branches:
```yml
on:
  push:
    branches: ['**']
```

> **Note:** You do not need to create branches manually. Remediation runs automatically create a fresh `remediate/{tool}-{timestamp}` branch from `main` via the GitHub API.

### 2. Create a GitHub PAT
Go to: [https://github.com/settings/personal-access-tokens/new](https://github.com/settings/personal-access-tokens/new)

Fine-grained PAT with these permissions on the target repo:
- **Code scanning alerts**: Read
- **Contents**: Read & Write
- **Pull requests**: Read & Write

Or a classic PAT with scopes: `repo`, `security_events`.

### 3. Configure environment

```bash
# Backend
cp backend/.env.example backend/.env
# Edit backend/.env — set GITHUB_TOKEN, GITHUB_REPO, DEVIN_API_KEY, etc.

# Frontend
cp frontend/.env.example frontend/.env
# Edit frontend/.env — set BETTER_AUTH_SECRET (generate with: openssl rand -hex 32)
```

### 4. Set up auth

```bash
cd frontend
npm install

# Runs migrations + seeds a user (reads SEED_EMAIL/SEED_PASSWORD from frontend/.env)
npm run setup
```

### 5. Run locally

```bash
# Backend
cd backend
uv sync
uv run fastapi dev app/main.py

# Frontend (separate terminal)
cd frontend
npm run dev
```

Open http://localhost:3000. Sign in with your seeded credentials, then click "Run New Scan" to fetch CodeQL alerts.

<details>
<summary><strong>Deploy to AWS (Optional)</strong></summary>

### Prerequisites

- AWS CLI configured with your profile (or change in `infra/terraform/variables.tf`)
- SSH key pair for EC2
- Devin GitHub App installed on target repo (for Devin API access)

### Steps

```bash
# 1. Provision infrastructure
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars  # edit values
terraform init && terraform apply

# 2. Push secrets to SSM
cp backend/.env.example .env  # fill in real values
bash infra/scripts/push-env-to-ssm.sh .env

# 3. SSH to EC2 and set up
ssh ubuntu@<elastic-ip>
bash setup.sh

# 4. Deploy
bash deploy.sh
```

### What gets deployed

| Service | Port | Description |
|---------|------|-------------|
| Caddy | 80/443 | Reverse proxy, auto-TLS |
| API | 8000 | FastAPI backend |
| Web | 3000 | Next.js dashboard |

SQLite database is stored on a Docker volume at `/data/medsecure.db` and backed up to S3 every 6 hours.

</details>

## API Endpoints

All endpoints except `/api/health` require authentication (better-auth session cookie).

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check (public) |
| GET | `/api/config` | Current repo/branch config |
| POST | `/api/scans/trigger` | Fetch CodeQL alerts for all branches |
| GET | `/api/scans` | List scan snapshots |
| GET | `/api/scans/compare/latest` | Compare latest scan across tools |
| GET | `/api/alerts/live?tool=baseline` | Live alerts from GitHub API |
| POST | `/api/remediate/devin` | Fix alerts via Devin (auto-branches, groups by file) |
| POST | `/api/remediate/api-tool` | Fix alerts via LLM API (anthropic/openai/gemini) |
| GET | `/api/remediate/devin/sessions` | List Devin sessions |
| GET | `/api/remediate/api-tool/jobs` | List API remediation jobs |
| POST | `/api/reports/generate/{type}` | Generate CISO or CTO report |
| GET | `/api/reports/latest/{type}` | Get latest report by type |
| GET | `/api/replay/runs` | List replay runs (includes branch name) |
| GET | `/api/replay/runs/{id}` | Get replay run with events + metadata |
| POST | `/api/replay/demo-seed` | Seed demo replay data |

## Environment Variables

See `backend/.env.example` and `frontend/.env.example` for full reference.

### Backend

| Variable | Required | Description |
|----------|----------|-------------|
| `GITHUB_TOKEN` | Yes | GitHub PAT with code scanning access |
| `GITHUB_REPO` | Yes | Target repo (e.g. `owner/repo`) |
| `DEVIN_API_KEY` | For remediation | Devin API key |
| `AUTH_DB_PATH` | No | Path to better-auth SQLite database (default: `../frontend/auth.db`) |
| `CORS_ORIGINS` | No | Allowed origins (default: `http://localhost:3000`) |
| `BRANCH_BASELINE` | No | Baseline branch (default: `main`) |
| `DATABASE_PATH` | No | SQLite path (default: `medsecure.db`) |
| `S3_BACKUP_BUCKET` | For backups | S3 bucket name |

### Frontend

| Variable | Required | Description |
|----------|----------|-------------|
| `BETTER_AUTH_SECRET` | Yes | Random secret for encryption (`openssl rand -hex 32`) |
| `BETTER_AUTH_URL` | Yes | Base URL of the frontend (e.g. `http://localhost:3000`) |
| `NEXT_PUBLIC_API_URL` | No | Backend API URL (default: `http://localhost:8000`) |

