#!/usr/bin/env bash
# Send one exact request to the server's ci-entry.sh and pass its output through untouched
# (ticket 15). For requests that carry no who or reason, or whose output is a file:
#
#   .github/scripts/ssh-request.sh backup-check
#   .github/scripts/ssh-request.sh fetch <backup name> > <file>
#
# Same environment as deploy-over-ssh.sh (DEPLOY_SSH_KEY, DEPLOY_KNOWN_HOSTS, DEPLOY_HOST,
# optional DEPLOY_PORT, CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET). No secret is printed.
set -euo pipefail

: "${DEPLOY_SSH_KEY:?the production environment needs DEPLOY_SSH_KEY}"
: "${DEPLOY_KNOWN_HOSTS:?the production environment needs DEPLOY_KNOWN_HOSTS}"
: "${DEPLOY_HOST:?the production environment needs DEPLOY_HOST}"
(($# > 0)) || { echo "Say what to ask the server." >&2; exit 2; }

work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT
(
  umask 077
  printf '%s\n' "$DEPLOY_SSH_KEY" >"$work/key"
  printf '%s\n' "$DEPLOY_KNOWN_HOSTS" >"$work/known_hosts"
)
ssh_options=(
  -i "$work/key" -p "${DEPLOY_PORT:-22}"
  -o IdentitiesOnly=yes -o "UserKnownHostsFile=$work/known_hosts" -o StrictHostKeyChecking=yes
  -o BatchMode=yes -o ConnectTimeout=30 -o ServerAliveInterval=15
)
if [[ -n "${CF_ACCESS_CLIENT_ID:-}" ]]; then
  export TUNNEL_SERVICE_TOKEN_ID="$CF_ACCESS_CLIENT_ID"
  export TUNNEL_SERVICE_TOKEN_SECRET="${CF_ACCESS_CLIENT_SECRET:?CF_ACCESS_CLIENT_SECRET goes with the ID}"
  ssh_options+=(-o "ProxyCommand=cloudflared access ssh --hostname %h")
fi
exec ssh "${ssh_options[@]}" "deploy@$DEPLOY_HOST" "$*"
