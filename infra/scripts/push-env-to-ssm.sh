#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-.env}"
PREFIX="/medsecure/prod"
PROFILE="watate"
REGION="ap-southeast-1"

if [ ! -f "$ENV_FILE" ]; then
  echo "Error: $ENV_FILE not found"
  exit 1
fi

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

echo "Done. All parameters pushed to SSM under $PREFIX/"
