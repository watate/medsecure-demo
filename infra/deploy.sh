#!/bin/bash
set -eo pipefail

# Pull secrets from SSM into .env
aws ssm get-parameters-by-path \
  --path "/medsecure/prod/" \
  --with-decryption \
  --region ap-southeast-1 \
  --query "Parameters[*].[Name,Value]" \
  --output text | while read name value; do
    key=$(echo "$name" | sed 's|/medsecure/prod/||')
    echo "${key}=${value}"
done > ~/medsecure/.env

# Pull latest images and restart
cd ~/medsecure
docker compose pull
docker compose up -d
