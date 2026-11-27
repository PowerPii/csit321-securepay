#!/bin/bash
# What is running, exactly: the source revision and the image digest of every service.
# auto-deploy.sh and rollback.sh call it after each change; docs/RECOVERY.md.
#
#   bash deploy/release-record.sh check <revision>        # every app container built from it?
#   bash deploy/release-record.sh record <action> <revision> [<previous revision>]
#   bash deploy/release-record.sh show [N]                # the last N records (default 5)
#
# record appends one JSON line to ~/deploy-history.jsonl (RELEASE_HISTORY to change it):
#   {"at":"2026-11-19T12:00:00Z","action":"deploy","revision":"<40 hex>","previous":"<40 hex>",
#    "host":"securepay","services":[{"service":"gateway","image":"sha256:...",
#    "revision":"<40 hex>","repo_digest":""}, ...]}
# "image" is the content digest of the image the container runs (docker inspect .Image); it
# changes whenever the image's bytes do. "repo_digest" is the registry digest for pulled images
# (Postgres, Mailpit) and empty for images built on the server. The newest record is also
# written to ~/deployed-release.txt in plain text, for the status of the server at a glance.
set -euo pipefail
deploy="$(cd "$(dirname "$0")" && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-$deploy/compose.prod.yaml}"
history="${RELEASE_HISTORY:-$HOME/deploy-history.jsonl}"
# The services built from this repository; their images carry the commit as a label.
APPS="gateway gateway-worker simulator network-switch network-issuer merchant caddy"
# The laptop stack (compose.yaml) runs the stock Caddy image, so only the Django services.
[ "$(basename "$COMPOSE_FILE")" = compose.yaml ] && APPS="gateway gateway-worker simulator network-switch network-issuer merchant"

services() {
  local id
  for id in $(docker compose -f "$COMPOSE_FILE" ps -q); do
    docker inspect --format '{{ index .Config.Labels "com.docker.compose.service" }} {{ .Image }} {{ index .Config.Labels "org.opencontainers.image.revision" }}' "$id"
  done | sort
}

case "${1:-show}" in
  check)
    want="${2:?give the revision}"
    bad=0
    while read -r name image built; do
      case " $APPS " in *" $name "*) ;; *) continue ;; esac
      if [ "${built:-}" != "$want" ]; then
        echo "MISMATCH: $name is running an image built from ${built:-an unknown commit}, not $want"; bad=1
      fi
    done < <(services)
    [ $bad -eq 0 ] && echo "every app container runs $want"
    exit $bad ;;
  record)
    action="${2:?give the action}"; revision="${3:?give the revision}"; previous="${4:-}"
    at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    entries=""; text=""
    while read -r name image built; do
      repo="$(docker image inspect --format '{{ join .RepoDigests "," }}' "$image" 2>/dev/null || true)"
      entries="$entries${entries:+,}{\"service\":\"$name\",\"image\":\"$image\",\"revision\":\"${built:-}\",\"repo_digest\":\"$repo\"}"
      text="$text$(printf '  %-15s %s  %s' "$name" "$image" "${built:-${repo:-}}")"$'\n'
    done < <(services)
    line="{\"at\":\"$at\",\"action\":\"$action\",\"revision\":\"$revision\",\"previous\":\"$previous\",\"host\":\"$(hostname)\",\"services\":[$entries]}"
    echo "$line" >> "$history"
    {
      echo "$action of $revision at $at (before: ${previous:-unknown})"
      printf '%s' "$text"
    } | tee "$(dirname "$history")/deployed-release.txt"
    echo "recorded in $history" ;;
  show)
    n="${2:-5}"
    [ -f "$history" ] || { echo "no records yet in $history"; exit 0; }
    tail -n "$n" "$history" | python3 -c '
import json, sys
for line in sys.stdin:
    r = json.loads(line)
    at, action, rev, prev = r["at"], r["action"], r["revision"][:12], r["previous"][:12] or "?"
    print(f"{at}  {action:<8} {rev}  (before {prev})")
    for s in r["services"]:
        name, image, where = s["service"], s["image"][:19], (s["revision"] or s["repo_digest"])[:60]
        print(f"    {name:<15} {image}  {where}")
' ;;
  previous)
    # What ran before the newest deploy: the default target of rollback.sh. Rollbacks in the
    # history are skipped, so running it twice does not bounce between two revisions.
    [ -f "$history" ] || exit 1
    python3 -c '
import json, sys
deploys = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
deploys = [r for r in deploys if r["action"] == "deploy" and r["previous"] not in ("", r["revision"])]
if deploys:
    print(deploys[-1]["previous"])
' "$history" ;;
  *) sed -n '2,16p' "$0"; exit 2 ;;
esac
