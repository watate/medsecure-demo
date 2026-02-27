# MedSecure

Compare CodeQL security remediation across AI tools (Devin vs Copilot Autofix vs Anthropic vs OpenAI vs Google). Point it at any repo with CodeQL enabled and see which tool fixes the most vulnerabilities.

## Architecture

- **Backend**: FastAPI + SQLite — fetches CodeQL alerts via GitHub API, triggers Devin remediation sessions
- **Frontend**: Next.js + shadcn/ui — dashboard, alert browser, remediation log, reports, replay
- **Auth**: [better-auth](https://www.better-auth.com/) with SQLite — email/password login, session cookies
- **Infra**: Terraform (EC2 + S3), Docker Compose (Caddy + API + Web), GitHub Actions CI/CD

## Quick Start (Local)

### 1. Prepare your target repo

Enable CodeQL on the repo you want to scan. Update the workflow to scan all branches:

1. Repo -> Settings -> Advanced Security 
2. Find Code scanning → CodeQL analysis and click three dots -> Switch to advanced
3. Update codeql.yml
```yml
on:
  push:
    branches: ['**']
```

Create branches from `main` for each tool (examples here are for tomcat):

```bash
git checkout main
git checkout -b tomcat-devin && git push origin tomcat-devin
git checkout main
git checkout -b tomcat-copilot && git push origin tomcat-copilot
git checkout main
git checkout -b tomcat-anthropic && git push origin tomcat-anthropic
git checkout main
git checkout -b tomcat-openai && git push origin tomcat-openai
git checkout main
git checkout -b tomcat-google && git push origin tomcat-google
```

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

# Create auth database tables
npx @better-auth/cli migrate

# Seed a user by adding details to frontend/.env, then run:
npm run seed
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

## Deploy to AWS (Optional)

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
| POST | `/api/remediate/devin` | Create Devin sessions to fix alerts |
| GET | `/api/remediate/devin/sessions` | List Devin sessions |
| POST | `/api/reports/generate/{type}` | Generate CISO or CTO report |
| GET | `/api/reports/latest/{type}` | Get latest report by type |
| GET | `/api/replay/runs` | List replay runs |
| GET | `/api/replay/runs/{id}` | Get replay run with events |
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
| `BRANCH_DEVIN` | No | Devin fix branch (default: `tomcat-devin`) |
| `BRANCH_COPILOT` | No | Copilot fix branch (default: `tomcat-copilot`) |
| `BRANCH_ANTHROPIC` | No | Anthropic fix branch (default: `tomcat-anthropic`) |
| `DATABASE_PATH` | No | SQLite path (default: `medsecure.db`) |
| `S3_BACKUP_BUCKET` | For backups | S3 bucket name |

### Frontend

| Variable | Required | Description |
|----------|----------|-------------|
| `BETTER_AUTH_SECRET` | Yes | Random secret for encryption (`openssl rand -hex 32`) |
| `BETTER_AUTH_URL` | Yes | Base URL of the frontend (e.g. `http://localhost:3000`) |
| `NEXT_PUBLIC_API_URL` | No | Backend API URL (default: `http://localhost:8000`) |

