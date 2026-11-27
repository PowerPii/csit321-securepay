#!/bin/bash
# Update the staging stack (deploy/STAGING.md): the v2 test copy at gateway., shop. and
# mail.<staging domain>. It runs on the production server as a separate Compose project
# (securepay-staging) with its own database, secrets, settings and demo inbox.
# Production's twin is deploy/auto-deploy.sh.
#
# Run it from the STAGING clone (deploy/staging-setup.sh makes it), never the production one:
#   bash ~/securepay-staging/deploy/staging-deploy.sh            # the newest dev branch
#   bash ~/securepay-staging/deploy/staging-deploy.sh <commit>   # one commit on dev
#   bash ~/securepay-staging/deploy/staging-deploy.sh --stop     # stop staging, keep its data
#   bash ~/securepay-staging/deploy/staging-deploy.sh --status   # what runs, memory use
#
# A deploy is your own sign-off, as for production: run the checks for that commit first (CI
# on the pull request into dev, or locally while Actions minutes are used up).
#
# Staging runs the `dev` branch (decided 27 Nov 2026: one pull request per feature, no
# separate promotion into a staging branch). STAGING_BRANCH overrides it.
#
# Safety: it refuses to run from a clone that is not on that branch, whose
# deploy/.env does not name the securepay-staging project, or whose domain is production's.
# It respects the demo freeze (deploy/freeze.sh), which covers the whole server, and it
# refuses to build when the server is short of memory (STAGING_MIN_MEM_MB, default 1200 MB
# free; --force skips that check). It never touches production's containers, volumes or
# images.
set -euo pipefail

main() {
  local repo log want request now prev domain prod_domain tries force=0 mode=deploy mem min branch
  repo="$(cd "$(dirname "$0")/.." && pwd)"
  cd "$repo"
  for request in "$@"; do
    case "$request" in
      --force) force=1 ;;
      --stop) mode=stop ;;
      --status) mode=status ;;
      *) want="$request" ;;
    esac
  done
  want="${want:-}"

  conf() { sed -n "s/^$1=//p" deploy/.env 2>/dev/null | tail -1 | tr -d "\"' "; }
  if [ "$(conf COMPOSE_PROJECT_NAME)" != securepay-staging ]; then
    echo "refusing: $repo/deploy/.env does not say COMPOSE_PROJECT_NAME=securepay-staging."
    echo "Is this the production clone? Staging runs from ~/securepay-staging (staging-setup.sh)."
    exit 1
  fi
  domain="$(conf DOMAIN)"
  prod_domain="$(sed -n 's/^DOMAIN=//p' "$HOME/csit321-secure-pay/deploy/.env" 2>/dev/null | tail -1 | tr -d "\"' ")"
  if [ -z "$domain" ] || [ "$domain" = "$prod_domain" ]; then
    echo "refusing: DOMAIN in $repo/deploy/.env is empty or production's ($domain)"; exit 1
  fi
  P=(docker compose -p securepay-staging -f deploy/compose.prod.yaml -f deploy/compose.staging.yaml)

  case "$mode" in
    stop)
      "${P[@]}" stop
      echo "Staging stopped (data kept). Start it again with: bash $0"
      return 0 ;;
    status)
      "${P[@]}" ps
      docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}' | grep -E 'NAME|securepay-staging' || true
      awk '/MemAvailable/ {printf "Memory available on the server: %d MB\n", $2 / 1024}' /proc/meminfo
      return 0 ;;
  esac

  if [ -n "$want" ] && ! [[ "$want" =~ ^[0-9a-f]{40}$ ]]; then
    echo "refusing: expected a 40-character commit, got: $want"; exit 2
  fi
  branch="${STAGING_BRANCH:-dev}"
  if [ "$(git rev-parse --abbrev-ref HEAD)" = staging ] && [ "$branch" != staging ] \
      && [ -z "$(git status --porcelain --untracked-files=no)" ]; then
    # A clone made before 27 Nov 2026 is on the retired staging branch: move it once.
    git fetch --quiet origin "$branch"
    git checkout --quiet -B "$branch" "origin/$branch"
    echo "moved the staging clone from the staging branch to $branch"
  fi
  if [ "$(git rev-parse --abbrev-ref HEAD)" != "$branch" ]; then
    echo "refusing: the clone at $repo is not on the $branch branch"; exit 1
  fi

  log="$HOME/staging-deploy.log"
  # One staging deploy at a time.
  exec 7>"$HOME/.securepay-staging-deploy.lock"
  flock -w 900 7 || { echo "another staging deploy is still running"; exit 1; }
  exec > >(tee -a "$log") 2>&1
  echo "=== staging deploy $(date -u +%Y-%m-%dT%H:%M:%SZ)"

  # A demo freeze covers the whole server: no staging builds then either (they use the same
  # CPU and memory as the demo).
  if bash deploy/freeze.sh status >/dev/null 2>&1; then
    echo "$(bash deploy/freeze.sh status) Not deploying staging until it ends."; exit 1
  fi

  # Building images takes the most memory. Production comes first.
  mem="$(awk '/MemAvailable/ {print int($2 / 1024)}' /proc/meminfo)"
  min="${STAGING_MIN_MEM_MB:-1200}"
  if [ "$force" = 0 ] && [ "$mem" -lt "$min" ]; then
    echo "refusing: only $mem MB memory available (want $min MB so production is not starved)."
    echo "See bash deploy/headroom.sh, stop staging (--stop) or upgrade the VM (STAGING.md)."
    exit 1
  fi

  prev="$(git rev-parse HEAD)"
  git fetch --quiet origin "$branch"
  if [ -z "$want" ]; then
    want="$(git rev-parse "origin/$branch")"
  elif ! git merge-base --is-ancestor "$want" "origin/$branch" 2>/dev/null; then
    echo "refusing: $want is not a commit on $branch"; exit 1
  fi
  if ! git merge --ff-only --quiet "$want"; then
    echo "the staging clone has local changes or the $branch branch was rewritten; fix by hand (git status)"
    exit 1
  fi
  now="$(git rev-parse HEAD)"
  echo "code: ${prev:0:12} -> ${now:0:12} ($now)"

  # Never while a production deploy is building (two builds at once on one small VM), and a
  # production deploy started now waits for this build. The watchdog skips its checks while
  # this lock is held, as it does during a production deploy.
  exec 9>"$HOME/.securepay-deploy.lock"
  flock -w 900 9 || { echo "a production deploy is still running; try again later"; exit 1; }
  GIT_REVISION="$now" "${P[@]}" up -d --build --remove-orphans
  flock -u 9

  # Every staging container must run an image built from this commit.
  local id svc rev bad=0
  for id in $("${P[@]}" ps -q); do
    read -r svc rev < <(docker inspect --format '{{ index .Config.Labels "com.docker.compose.service" }} {{ index .Config.Labels "org.opencontainers.image.revision" }}' "$id")
    case "$svc" in db|mailpit) continue ;; esac
    if [ "$rev" != "$now" ]; then echo "FAIL  $svc runs ${rev:-an unlabelled image}, not $now"; bad=1; fi
  done
  [ "$bad" = 0 ] || { echo "=== staging deploy of $now FAILED the revision check"; exit 1; }
  echo "$now" > "$HOME/staging-deployed-revision"
  # Only dangling layers: images production runs are in use and never removed.
  docker image prune -f >/dev/null

  for tries in $(seq 1 18); do
    if bash deploy/smoke-check.sh "$domain" >/tmp/securepay-staging-smoke.txt 2>&1; then
      cat /tmp/securepay-staging-smoke.txt
      echo "=== staging deployed ${now:0:7}, smoke check passed"
      return 0
    fi
    sleep 10
  done
  cat /tmp/securepay-staging-smoke.txt
  echo "=== staging deployed ${now:0:7}, but the smoke check FAILED after $tries tries"
  echo "Is STAGING_DOMAIN=$domain in production's deploy/.env, with its Caddy restarted (STAGING.md step 4)?"
  "${P[@]}" ps
  exit 1
}

# Everything is inside main(), so bash has read the whole script before git replaces this file.
main "$@"
