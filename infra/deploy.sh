#!/bin/bash
set -eo pipefail

# Pull secrets from SSM into .env
aws ssm get-parameters-by-path \
  --path "/medsecure/prod/" \
  --with-decryption \
  --region us-east-1 \
  --query "Parameters[*].[Name,Value]" \
  --output text | while read name value; do
    key=$(echo "$name" | sed 's|/medsecure/prod/||')
    echo "${key}=${value}"
done > ~/medsecure/.env

# Pull latest images and restart
cd ~/medsecure
docker compose pull
docker compose up -d

# Fix shared volume ownership so nextjs (uid 1001) can write auth.db
docker compose exec -T -u root web chown 1001:1001 /app/data

# Source .env so seed vars are available
set -a
source ~/medsecure/.env
set +a

# Seed default user (idempotent — skips if user already exists)
if [ -n "$SEED_EMAIL" ] && [ -n "$SEED_PASSWORD" ]; then
  echo "==> Waiting for web container to be ready..."
  for i in $(seq 1 15); do
    if docker compose exec -T web node -e "fetch('http://localhost:3000/').then(r=>{console.log(r.status);process.exit(r.ok?0:1)}).catch(()=>process.exit(1))" 2>/dev/null; then
      echo "Web container is ready"
      break
    fi
    sleep 2
  done

  echo "==> Seeding default user"
  docker compose exec -T web node -e "
    fetch('http://localhost:3000/api/auth/sign-up/email', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'Origin': 'http://localhost:3000'},
      body: JSON.stringify({email:'${SEED_EMAIL}',password:'${SEED_PASSWORD}',name:'${SEED_NAME:-Admin}'})
    }).then(r=>r.text()).then(console.log).catch(console.error)
  " || true
fi
