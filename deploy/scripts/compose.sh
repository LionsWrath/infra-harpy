#!/usr/bin/env bash
# Host-aware docker compose for infra-harpy: loads scripts/vars.sh and uses docker-compose.<host>.yml.
# Host comes from the hostname (harpy-ichi -> hichi) or HARPY_HOST. Examples:
#   scripts/compose.sh ps
#   scripts/compose.sh up -d <service>
#   scripts/compose.sh config --quiet
set -euo pipefail
deploy=$(cd "$(dirname "$0")/.." && pwd)
host=${HARPY_HOST:-$(hostname -s)}
case $host in harpy-ichi) host=hichi ;; esac
file="$deploy/docker-compose.$host.yml"
[[ -f $file ]] || { echo "No compose file for host '$host' ($file). Set HARPY_HOST." >&2; exit 1; }

# 'config' without these flags prints every interpolated secret
if [[ ${1:-} == config && " $* " != *" --no-interpolate "* && " $* " != *" --quiet "* \
      && " $* " != *" -q "* && " $* " != *" --services "* ]]; then
  echo "Refusing 'config' without --no-interpolate, --quiet or --services (it would print secrets)." >&2
  exit 2
fi

set -a
# shellcheck source=vars.sh
. "$deploy/scripts/vars.sh"
# shellcheck disable=SC1090
[[ -f $deploy/hosts/$host.sh ]] && . "$deploy/hosts/$host.sh"
set +a
cd "$deploy"
# Existing containers belong to project "apps" (the old deploy/apps working dir); keep it so compose owns them
exec docker compose -p "${COMPOSE_PROJECT_NAME:-apps}" -f "$file" "$@"
