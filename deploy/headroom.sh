#!/bin/bash
# Is there room on this server for the staging stack next to production (deploy/STAGING.md)?
# Read-only: changes nothing. Run on the server:   bash deploy/headroom.sh
#
# Prints memory, swap and disk, what each running stack uses now, and a verdict. The staging
# estimate is what a stack like production's uses with staging's smaller worker counts
# (STAGING_EXPECT_MB, default 1400 MB; every staging container also has a hard cap,
# compose.staging.yaml, about 2.6 GB in all) plus room for an image build (BUILD_PEAK_MB,
# default 800 MB: the checkout and dashboard bundles are built on the server).
set -uo pipefail

mb() { awk -v k="$1" '$1 == k":" {print int($2 / 1024)}' /proc/meminfo; }
total="$(mb MemTotal)"; avail="$(mb MemAvailable)"; swap="$(mb SwapTotal)"
expect="${STAGING_EXPECT_MB:-1400}"; build="${BUILD_PEAK_MB:-800}"
cpus="$(nproc)"

echo "Memory: $total MB total, $avail MB available, swap $swap MB. CPUs: $cpus."
df -h / | awk 'NR==2 {print "Disk /: " $3 " used of " $2 " (" $5 "), " $4 " free"}'
echo
if command -v docker >/dev/null; then
  echo "Memory per stack now (production = project 'deploy', staging = 'securepay-staging'):"
  docker stats --no-stream --format '{{.Name}} {{.MemUsage}}' 2>/dev/null | awk '
    function tomb(v) { if (v ~ /GiB/) return v * 1024; if (v ~ /MiB/) return v + 0; if (v ~ /KiB/) return v / 1024; return v / 1048576 }
    { split($2, a, "/"); used = tomb(a[1]); p = ($1 ~ /^securepay-staging-/) ? "staging" : ($1 ~ /^deploy-/) ? "production" : "other"
      sum[p] += used; printf "  %-40s %6.0f MB\n", $1, used }
    END { for (p in sum) printf "  = %-38s %6.0f MB\n", p, sum[p] }'
  echo
fi

staging_up="$(docker ps -q --filter label=com.docker.compose.project=securepay-staging 2>/dev/null | wc -l)"
if [ "$staging_up" -gt 0 ]; then
  left=$((avail - build))
  echo "Staging is running. Room left for a build: $left MB (want at least 300)."
else
  left=$((avail - expect - build))
  echo "Staging is not running. After starting it and during a build, about $left MB would be left"
  echo "(available $avail - staging ~$expect - build ~$build)."
fi
echo
if [ "$total" -lt 6000 ] && [ "$left" -lt 300 ]; then
  echo "VERDICT: too tight. Upgrade the VM to e2-standard-2 (8 GB, 2 full vCPUs) before"
  echo "running staging (STAGING.md, 'Memory'). Adding swap is a stop-gap only."
  exit 1
elif [ "$left" -lt 300 ]; then
  echo "VERDICT: tight. Stop staging before demos (staging-deploy.sh --stop) and watch the"
  echo "watchdog's memory alerts."
  exit 1
fi
echo "VERDICT: enough room for staging next to production."
