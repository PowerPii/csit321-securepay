#!/bin/bash
# Server watchdog (demo sprint 16, RUNBOOK.md section 9a). Cron runs it every 5 minutes on
# the public server as `securepay`:
#
#   */5 * * * * /home/securepay/csit321-secure-pay/deploy/watchdog.sh >> /home/securepay/watchdog.log 2>&1
#
#   bash deploy/watchdog.sh              # check, restart what is down, alert on changes
#   bash deploy/watchdog.sh --check      # check only: no restart, no alert (demo.sh preflight)
#   bash deploy/watchdog.sh --test-alert # send one test message to the team chat
#
# Checks: every container running; the gateway's /readyz (databases + worker heartbeat) and
# the shop's /healthz through Caddy on this machine; disk, memory, certificate expiry and the
# age of the newest backup.
#
# Heals (the URS failover rule: restart automatically): a service that fails two runs in a
# row is restarted, at most once per 30 minutes each, and never while a deploy is running.
# Docker's restart policy already restarts a container that exits; this catches the ones
# that keep running but stop answering, and the worker loop going quiet.
#
# Alerts go to the team chat when the list of problems changes (a new problem, or all clear)
# if deploy/.env sets ALERT_WEBHOOK_URL (a Discord or Slack incoming webhook) or
# ALERT_TELEGRAM_BOT_TOKEN + ALERT_TELEGRAM_CHAT_ID. The server down as a whole cannot alert
# from here: the outside uptime monitor (RUNBOOK.md section 9a) covers that.
# Exit code 0 = all fine, 1 = problems (after any restarts).
set -uo pipefail

main() {
  local mode="${1:-run}" repo state env domain file problems=() healed=() line
  repo="$(cd "$(dirname "$0")/.." && pwd)"
  state="${WATCHDOG_STATE:-$HOME/.securepay-watchdog}"
  env="$repo/deploy/.env"
  mkdir -p "$state"
  cd "$repo" || exit 1

  conf() { [ -f "$env" ] && sed -n "s/^$1=//p" "$env" | tail -1 | tr -d "\"' "; }
  domain="$(conf DOMAIN)"
  if [ -n "$domain" ]; then file=deploy/compose.prod.yaml; else file=deploy/compose.yaml; domain=localhost; fi
  P=(docker compose -f "$file")

  case "$mode" in
    --test-alert) alert "SecurePay watchdog test message from $(hostname) ($domain). Alerts work."; return $? ;;
    --check|run) ;;
    *) echo "usage: watchdog.sh [--check | --test-alert]"; return 2 ;;
  esac

  # A deploy restarts everything on purpose; checking then would only cause noise.
  if [ "$mode" = run ] && [ -e "$HOME/.securepay-deploy.lock" ]; then
    exec 8<"$HOME/.securepay-deploy.lock"
    if ! flock -n 8; then echo "$(stamp) deploy running, skipped"; return 0; fi
    flock -u 8
  fi

  # 1. Containers.
  local running svc
  running="$("${P[@]}" ps --status running --services 2>/dev/null)"
  for svc in db gateway gateway-worker simulator network-switch network-issuer merchant caddy mailpit; do
    if ! grep -qx "$svc" <<<"$running"; then
      problems+=("container $svc is not running")
      heal "$svc" && healed+=("$svc (not running)")
    else
      ok_again "$svc-container"
    fi
  done

  # 2. The gateway's readiness and the shop, through Caddy on this machine. Locally the
  # names are *.localhost with Caddy's own CA, so certificates are not checked there.
  local tls=() body code
  [ "$domain" = localhost ] && tls=(-k)
  body="$(curl -s "${tls[@]}" --max-time 10 -w '\n%{http_code}' \
          --resolve "gateway.$domain:443:127.0.0.1" "https://gateway.$domain/readyz")"
  code="$(tail -1 <<<"$body")"
  if [ "$code" = 200 ]; then
    ok_again gateway; ok_again gateway-worker
  elif [ "$code" = 503 ] && grep -q '"worker"' <<<"$body"; then
    problems+=("gateway worker stopped (no heartbeat for 2 minutes): webhooks, emails and reconciliation wait")
    ok_again gateway
    failing gateway-worker && heal gateway-worker && healed+=("gateway-worker (no heartbeat)")
  elif [ "$code" = 503 ]; then
    problems+=("gateway not ready: $(head -1 <<<"$body" | tr -d '\n' | cut -c1-120)")
  else
    problems+=("gateway not answering (/readyz HTTP $code)")
    failing gateway && heal gateway && healed+=("gateway (HTTP $code)")
  fi

  code="$(curl -s "${tls[@]}" --max-time 10 -o /dev/null -w '%{http_code}' \
          --resolve "shop.$domain:443:127.0.0.1" "https://shop.$domain/healthz")"
  if [ "$code" = 200 ]; then
    ok_again merchant
  else
    problems+=("demo shop not answering (/healthz HTTP $code)")
    failing merchant && heal merchant && healed+=("merchant (HTTP $code)")
  fi

  # 3. The machine.
  local disk mem days newest age
  disk="$(df -P / | awk 'NR==2 {gsub("%", "", $5); print $5}')"
  [ "${disk:-0}" -ge "${WATCHDOG_DISK_PCT:-85}" ] && \
    problems+=("disk ${disk}% full: docker image prune -f, old backups, docker logs")
  mem="$(awk '/MemAvailable/ {print int($2 / 1024)}' /proc/meminfo)"
  [ "${mem:-9999}" -lt "${WATCHDOG_MIN_MEM_MB:-150}" ] && \
    problems+=("only ${mem} MB memory free: docker stats")

  if [ "$domain" != localhost ]; then
    days="$(cert_days "gateway.$domain")"
    if [ -z "$days" ]; then
      problems+=("could not read the certificate of gateway.$domain")
    elif [ "$days" -lt 14 ]; then
      problems+=("certificate for gateway.$domain expires in $days days: docker compose logs caddy")
    fi
  fi

  # Complete backups only: <time> folders with a MANIFEST (backup.sh), not .incomplete-* or safety/.
  newest=""
  for d in $(ls -1d "${BACKUP_DIR:-$HOME/fyp-backups}"/2*/ 2>/dev/null | sort -r); do
    [ -f "${d}MANIFEST" ] && { newest="$d"; break; }
  done
  # Before the first backup.sh of 19 Nov 2026 no folder has one: use the newest folder.
  [ -n "$newest" ] || newest="$(ls -1d "${BACKUP_DIR:-$HOME/fyp-backups}"/2*/ 2>/dev/null | sort | tail -1)"
  if [ -z "$newest" ]; then
    [ "$domain" != localhost ] && problems+=("no complete database backup found (RUNBOOK.md step 9)")
  else
    age=$(( ($(date +%s) - $(stat -c %Y "$newest")) / 3600 ))
    [ "$age" -ge 36 ] && problems+=("newest backup is $age hours old: is the backup cron running?")
  fi

  # Report.
  if [ ${#problems[@]} -eq 0 ]; then
    echo "$(stamp) ok"
  else
    for line in "${problems[@]}"; do echo "$(stamp) PROBLEM $line"; done
  fi
  for line in "${healed[@]}"; do echo "$(stamp) RESTARTED $line"; done
  [ "$mode" = run ] && notify_changes
  [ ${#problems[@]} -eq 0 ]
}

stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# failing <name>: count consecutive failures; true from the second one (one slow answer
# during a busy moment is not worth a restart).
failing() {
  local f="$state/$1.fails" n
  [ "$mode" = run ] || return 1
  n=$(( $(cat "$f" 2>/dev/null || echo 0) + 1 ))
  echo "$n" > "$f"
  [ "$n" -ge 2 ]
}

ok_again() { rm -f "$state/$1.fails"; }

# heal <service>: restart it, unless check-only or it was restarted in the last 30 minutes.
heal() {
  local svc="$1" last now
  [ "$mode" = run ] || return 1
  now="$(date +%s)"
  last="$(cat "$state/$svc.restarted" 2>/dev/null || echo 0)"
  [ $(( now - last )) -lt "${WATCHDOG_RESTART_GAP:-1800}" ] && return 1
  echo "$now" > "$state/$svc.restarted"
  "${P[@]}" unpause "$svc" >/dev/null 2>&1 || true
  "${P[@]}" up -d --no-build "$svc" >/dev/null 2>&1 || true
  "${P[@]}" restart "$svc" >/dev/null 2>&1
}

cert_days() {
  local end
  end="$(echo | openssl s_client -connect 127.0.0.1:443 -servername "$1" 2>/dev/null \
         | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)"
  [ -n "$end" ] && echo $(( ($(date -d "$end" +%s) - $(date +%s)) / 86400 ))
}

# Alert only when the problem list changes, so a long outage is one message, not one every
# five minutes; then one "all clear" when it is over.
notify_changes() {
  local now_state prev msg restarted=""
  now_state="$(printf '%s\n' "${problems[@]}" | sed 's/[0-9]\+/N/g' | sort)"
  prev="$(cat "$state/last-problems" 2>/dev/null)"
  [ ${#healed[@]} -gt 0 ] && restarted="$(printf '\n- restarted %s' "${healed[@]}")"
  if [ "$now_state" = "$prev" ] && [ -z "$restarted" ]; then return 0; fi
  printf '%s' "$now_state" > "$state/last-problems"
  if [ ${#problems[@]} -eq 0 ]; then
    msg="SecurePay ($domain): all clear again.$restarted"
  else
    msg="SecurePay ($domain) needs a look:$(printf '\n- %s' "${problems[@]}")$restarted"
  fi
  alert "$msg"
}

# alert <text>: Discord/Slack webhook and/or Telegram, from deploy/.env.
alert() {
  local text="$1" hook token chat sent=1 payload
  hook="$(conf ALERT_WEBHOOK_URL)"
  token="$(conf ALERT_TELEGRAM_BOT_TOKEN)"
  chat="$(conf ALERT_TELEGRAM_CHAT_ID)"
  if [ -n "$hook" ]; then
    # Discord reads "content", Slack reads "text"; each ignores the other.
    payload="$(python3 -c 'import json,sys; t=sys.argv[1]; print(json.dumps({"content": t, "text": t}))' "$text")"
    curl -s --max-time 15 -o /dev/null -H 'Content-Type: application/json' -d "$payload" "$hook" && sent=0
  fi
  if [ -n "$token" ] && [ -n "$chat" ]; then
    curl -s --max-time 15 -o /dev/null "https://api.telegram.org/bot$token/sendMessage" \
      --data-urlencode "chat_id=$chat" --data-urlencode "text=$text" && sent=0
  fi
  if [ -z "$hook$token" ]; then echo "$(stamp) no alert channel set in deploy/.env (ALERT_WEBHOOK_URL or ALERT_TELEGRAM_*)"; fi
  echo "$(stamp) ALERT $(head -1 <<<"$text")"
  return $sent
}

main "$@"
