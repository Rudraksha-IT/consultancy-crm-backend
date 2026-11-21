#!/usr/bin/env bash
# Send one exact request to the server's ci-entry.sh and pass its output through untouched
# (ticket 15). For requests that carry no who or reason, or whose output is a file:
#
#   .github/scripts/ssh-request.sh backup-check
#   .github/scripts/ssh-request.sh fetch <backup name> > <file>
#
# Same environment as deploy-over-ssh.sh (DEPLOY_SSH_KEY, DEPLOY_HOST_KEY, PRODUCT_DOMAIN,
# optional DEPLOY_PORT, CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET). No secret is printed.
set -euo pipefail

: "${DEPLOY_SSH_KEY:?the production environment needs DEPLOY_SSH_KEY}"
: "${DEPLOY_HOST_KEY:?the production environment needs DEPLOY_HOST_KEY}"
host="${DEPLOY_HOST:-deploy.${PRODUCT_DOMAIN:?the organisation needs the secret PRODUCT_DOMAIN}}"
host_pattern='^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$'
key_pattern='^[a-z0-9-]+ [A-Za-z0-9+/]+=*$'
[[ "$host" =~ $host_pattern ]] || { echo "PRODUCT_DOMAIN must be a plain domain name." >&2; exit 2; }
[[ "$DEPLOY_HOST_KEY" =~ $key_pattern ]] || {
  echo "DEPLOY_HOST_KEY must be one line with the key type and the key only (ssh-ed25519 AAAA...)." >&2
  exit 2
}
(($# > 0)) || { echo "Say what to ask the server." >&2; exit 2; }

work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT
(
  umask 077
  printf '%s\n' "$DEPLOY_SSH_KEY" >"$work/key"
  printf 'deploy-server %s\n' "$DEPLOY_HOST_KEY" >"$work/known_hosts"
)
ssh_options=(
  -i "$work/key" -p "${DEPLOY_PORT:-22}"
  -o IdentitiesOnly=yes -o "UserKnownHostsFile=$work/known_hosts" -o HostKeyAlias=deploy-server
  -o StrictHostKeyChecking=yes
  -o BatchMode=yes -o ConnectTimeout=30 -o ServerAliveInterval=15
)
if [[ -n "${CF_ACCESS_CLIENT_ID:-}" ]]; then
  export TUNNEL_SERVICE_TOKEN_ID="$CF_ACCESS_CLIENT_ID"
  export TUNNEL_SERVICE_TOKEN_SECRET="${CF_ACCESS_CLIENT_SECRET:?CF_ACCESS_CLIENT_SECRET goes with the ID}"
  ssh_options+=(-o "ProxyCommand=cloudflared access ssh --hostname %h")
fi
exec ssh "${ssh_options[@]}" "deploy@$host" "$*"
