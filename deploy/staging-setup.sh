#!/bin/bash
# One-time set-up of the staging stack on the production server (deploy/STAGING.md, step 3).
# Run on the server as `securepay`, from the production clone:
#
#   bash ~/csit321-secure-pay/deploy/staging-setup.sh
#
# It changes nothing in production. It makes a second clone at ~/securepay-staging on the
# `staging` branch, with its own deploy/.env (from staging.env.example) and its own fresh
# deploy/secrets/ (deploy/make-secrets.sh), copies production's fraud model folder into it,
# and prints what to do next. Safe to run again: it never overwrites a setting or a secret.
set -euo pipefail

prod="$(cd "$(dirname "$0")/.." && pwd)"
dir="${STAGING_DIR:-$HOME/securepay-staging}"
domain="${STAGING_DOMAIN:-staging.securepaysg.me}"

conf() { [ -f "$1" ] && sed -n "s/^$2=//p" "$1" | tail -1 | tr -d "\"' " || true; }

if [ "$dir" = "$prod" ]; then echo "STAGING_DIR must not be the production clone"; exit 1; fi

# 1. The clone, on the staging branch (same remote and deploy key as production).
if [ ! -d "$dir/.git" ]; then
  git clone --quiet "$(git -C "$prod" remote get-url origin)" "$dir"
  echo "Cloned into $dir"
fi
cd "$dir"
git fetch --quiet origin staging
if [ "$(git rev-parse --abbrev-ref HEAD)" != staging ]; then
  git checkout --quiet -B staging origin/staging
fi
if [ ! -f deploy/compose.staging.yaml ]; then
  echo "The staging branch has no deploy/compose.staging.yaml yet: update it from main first"
  echo "(BRANCHING.md, 'Keeping dev and staging up to date')."; exit 1
fi

# 2. Settings: staging's own, never production's.
if [ ! -e deploy/.env ]; then
  cp deploy/staging.env.example deploy/.env
  sed -i "s/^DOMAIN=.*/DOMAIN=$domain/" deploy/.env
  acme="$(conf "$prod/deploy/.env" ACME_EMAIL)"
  [ -n "$acme" ] && sed -i "s/^ACME_EMAIL=.*/ACME_EMAIL=$acme/" deploy/.env
  echo "Made deploy/.env for $domain"
fi
chmod 600 deploy/.env
grep -qx 'COMPOSE_PROJECT_NAME=securepay-staging' deploy/.env || {
  echo "deploy/.env in $dir must contain COMPOSE_PROJECT_NAME=securepay-staging"; exit 1; }

# 3. Secrets: fresh ones (production's keys never leave production). Prints the staging
# inbox password once.
bash deploy/make-secrets.sh

# 4. The fraud model, if production has one (git-ignored, so not in the clone).
model="$(conf "$prod/deploy/.env" RISK_MODEL_VERSION)"
if [ -n "$model" ] && [ -z "$(conf deploy/.env RISK_MODEL_VERSION)" ] \
   && [ -d "$prod/gateway/apps/risk/model_artifacts/$model" ]; then
  cp -a "$prod/gateway/apps/risk/model_artifacts/$model" gateway/apps/risk/model_artifacts/
  chmod -R a+rX "gateway/apps/risk/model_artifacts/$model"
  sed -i "s/^RISK_MODEL_VERSION=.*/RISK_MODEL_VERSION=$model/" deploy/.env
  sha="$(conf "$prod/deploy/.env" RISK_MODEL_SHA256)"
  [ -n "$sha" ] && sed -i "s/^RISK_MODEL_SHA256=.*/RISK_MODEL_SHA256=$sha/" deploy/.env
  echo "Copied the fraud model $model"
fi

cat <<NEXT

Staging clone ready: $dir (branch staging, DOMAIN=$domain).

Next (deploy/STAGING.md):
  1. Check the memory first:   bash $prod/deploy/headroom.sh
  2. Production's Caddy forwards the staging names once you add one line to PRODUCTION's
     deploy/.env and restart only its Caddy (a few seconds):
       echo 'STAGING_DOMAIN=$domain' >> $prod/deploy/.env
       docker compose -f $prod/deploy/compose.prod.yaml up -d caddy
  3. Start staging:            bash $dir/deploy/staging-deploy.sh
  4. Register the staging demo shop and staff (STAGING.md, step 5).
NEXT
