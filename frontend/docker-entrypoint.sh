#!/bin/sh
# Render the nginx config, then start nginx.
#
# The RESOLVER dance exists to fix a specific production failure: nginx
# resolves a literal proxy_pass hostname ONCE at startup and caches the IP for
# the life of the process. On Railway every deploy gives the backend a new
# private IP, so a frontend container that outlives a backend deploy proxies to
# a dead address and every /api request 504s until the frontend is redeployed.
#
# Resolving through a variable with an explicit `resolver` makes nginx look the
# name up per request with a short TTL, so a backend redeploy is picked up in
# seconds without touching the frontend.
set -e

PORT="${PORT:-80}"
BACKEND_URL="${BACKEND_URL:-http://backend:8000}"

# Prefer an explicit override, else the container's own DNS server.
RESOLVER="${NGINX_RESOLVER:-}"
if [ -z "$RESOLVER" ]; then
    RESOLVER=$(awk '/^nameserver/ { print $2; exit }' /etc/resolv.conf 2>/dev/null || true)
fi
# Fall back to Docker's embedded DNS rather than failing to boot.
[ -z "$RESOLVER" ] && RESOLVER="127.0.0.11"

# nginx needs IPv6 resolver addresses bracketed. Railway private networking is
# IPv6, so this is the normal path there, not an edge case.
case "$RESOLVER" in
    \[*\]) ;;
    *:*) RESOLVER="[$RESOLVER]" ;;
esac

export PORT BACKEND_URL RESOLVER
envsubst '$PORT $BACKEND_URL $RESOLVER' \
    < /etc/nginx/conf.d/default.conf.template \
    > /etc/nginx/conf.d/default.conf

echo "nginx: proxying /api/ -> ${BACKEND_URL} via resolver ${RESOLVER}"

# Fail loudly on a bad config instead of serving a broken site.
nginx -t
exec nginx -g 'daemon off;'
