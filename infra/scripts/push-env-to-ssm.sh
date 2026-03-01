#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

ENV_FILES=("$REPO_ROOT/backend/.env" "$REPO_ROOT/frontend/.env" "$REPO_ROOT/infra/.env")
PREFIX="/medsecure/prod"
PROFILE="watate"
REGION="us-east-1"

for ENV_FILE in "${ENV_FILES[@]}"; do
  if [ ! -f "$ENV_FILE" ]; then
    echo "Warning: $ENV_FILE not found, skipping"
    continue
  fi

  echo "=== Processing $ENV_FILE ==="
  while IFS='=' read -r key value || [ -n "$key" ]; do
    # Skip empty lines and comments
    [[ -z "$key" || "$key" =~ ^# ]] && continue

    echo "Putting $PREFIX/$key"
    aws ssm put-parameter \
      --profile "$PROFILE" \
      --region "$REGION" \
      --name "$PREFIX/$key" \
      --type SecureString \
      --value "$value" \
      --overwrite
  done < "$ENV_FILE"
done

echo "Done. All parameters pushed to SSM under $PREFIX/"
