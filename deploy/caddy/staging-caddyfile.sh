#!/bin/sh
# Writes the staging Caddy's config from the production Caddyfile (deploy/compose.staging.yaml
# runs it when the staging Caddy starts), so staging always has exactly production's routes,
# security headers and private :8443 sites. Three changes, nothing else:
#   1. gateway., shop. and mail.{$DOMAIN} listen on plain HTTP inside Docker. The production
#      Caddy in front holds their certificates and forwards each request here
#      (deploy/caddy/staging.caddy.tmpl); browsers only ever see HTTPS.
#   2. The production Caddy is a trusted proxy, so the visitor's address and "https" pass
#      through (the staging gateway counts TRUSTED_PROXY_HOPS=2).
#   3. No /etc/caddy/extra import (old-domain redirects and the staging forward belong to
#      production only).
# It stops with an error if the production Caddyfile no longer has the shape it expects, so a
# change there cannot silently leave staging half-configured.
#
#   sh deploy/caddy/staging-caddyfile.sh deploy/Caddyfile.prod > /tmp/Caddyfile.staging
set -eu
src="${1:?usage: staging-caddyfile.sh <Caddyfile.prod>}"
awk '
  /^(gateway|shop|mail)[.][{][$]DOMAIN[}] [{]$/ { print "http://" $0; sites++; next }
  /^\temail [{][$]ACME_EMAIL[}]$/ {
    print
    print "\tservers {"
    print "\t\ttrusted_proxies static private_ranges"
    print "\t}"
    global++
    next
  }
  /^import \/etc\/caddy\/extra\// { extra++; next }
  { print }
  END {
    if (sites != 3 || global != 1 || extra != 1) {
      printf "staging-caddyfile.sh: %s changed shape (public sites %d of 3, email line %d of 1, extra import %d of 1); update this script\n", FILENAME, sites, global, extra > "/dev/stderr"
      exit 1
    }
  }
' "$src"
