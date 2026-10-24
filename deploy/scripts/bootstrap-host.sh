#!/usr/bin/env bash
# Prepares a host for a full re-setup from this repo. Read-only unless --apply.
# Checks: docker/compose, host compose file, env files, lsio network, bind-mount dirs, config templates.
# With --apply: creates the lsio network, missing data dirs under $HOME/data and copies config templates.
# Never starts containers; it prints the command to run when everything is ready.
set -euo pipefail
deploy=$(cd "$(dirname "$0")/.." && pwd)
apply=false; [[ ${1:-} == --apply ]] && apply=true
host=${HARPY_HOST:-$(hostname -s)}
case $host in harpy-ichi) host=hichi ;; esac
file="$deploy/docker-compose.$host.yml"
problems=0
say() { printf '%-8s %s\n' "$1" "$2"; }

command -v docker >/dev/null || { say MISSING "docker"; exit 1; }
v=$(docker compose version --short 2>/dev/null || echo 0)
say ok "docker compose $v (include support needs >= 2.20)"
[[ -f $file ]] || { say MISSING "$file (set HARPY_HOST)"; exit 1; }
say ok "host '$host' -> $(basename "$file")"

# env files referenced by the host's stacks
mapfile -t stacks < <(grep -oE 'apps/docker-compose\.[a-z0-9.-]+\.yml' "$file")
mapfile -t envs < <(cd "$deploy/apps" && grep -hoE '\.\./envs/[A-Za-z0-9_.-]+' "${stacks[@]##apps/}" | sort -u)
for e in "${envs[@]}"; do
  if [[ -s $deploy/apps/$e ]]; then say ok "env ${e#../}"; else say MISSING "env ${e#../} (materialize from Vaultwarden)"; problems=$((problems + 1)); fi
done
[[ -s $deploy/scripts/vars.sh ]] || { say MISSING "scripts/vars.sh"; problems=$((problems + 1)); }
(( problems == 0 )) || { echo "Materialize the env set first (see HOSTS.md), then re-run."; exit 1; }

set -a
. "$deploy/scripts/vars.sh"
# shellcheck disable=SC1090
[[ -f $deploy/hosts/$host.sh ]] && . "$deploy/hosts/$host.sh"
set +a

# lsio network
if docker network inspect lsio >/dev/null 2>&1; then
  say ok "network lsio ($(docker network inspect lsio --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}'))"
elif $apply; then
  docker network create --subnet "${LSIO_IP}.0/16" lsio >/dev/null && say created "network lsio ${LSIO_IP}.0/16"
else
  say MISSING "network lsio (would create ${LSIO_IP}.0/16)"
fi

# bind-mount sources (parsed in-process; values are never printed)
extra=()
for e in ${COMPOSE_INTERPOLATION_ENV_FILES:-}; do extra+=(--env-file "$e"); done
mapfile -t mounts < <(cd "$deploy" && docker compose -p "${COMPOSE_PROJECT_NAME:-apps}" "${extra[@]}" -f "$file" config --format json 2>/dev/null | python3 -c '
import json, sys
cfg = json.load(sys.stdin)
for svc in cfg.get("services", {}).values():
    for v in svc.get("volumes", []):
        if v.get("type") == "bind" and v.get("source", "").startswith("/"):
            print(v["source"])
' | sort -u)
for m in "${mounts[@]}"; do
  [[ -e $m ]] && continue
  case $m in
    *.properties|*.config|*.conf|*.yml|*.yaml|*.json|*.py|*.ini) say MISSING "file $m (from deploy/references or a backup)" ;;
    "$HOME"/data/*)
      if $apply; then mkdir -p "$m" && say created "dir $m"; else say MISSING "dir $m"; fi ;;
    *) say MISSING "path $m (outside \$HOME/data, create manually)" ;;
  esac
done

# config templates that services expect under DATA_PATH
if grep -q 'docker-compose.trino-iceberg.yml' "$file"; then
  for t in config.properties jvm.config catalog/iceberg.properties; do
    dst="$DATA_PATH/trino/$t"
    [[ -e $dst ]] && continue
    if $apply; then mkdir -p "$(dirname "$dst")" && cp "$deploy/references/trino/$t" "$dst" && say created "$dst (template)"
    else say MISSING "$dst (template in references/trino)"; fi
  done
fi

echo
$apply || echo "Read-only run. Re-run with --apply to create what is missing."
echo "Restore service data from backups, then start: scripts/compose.sh up -d"
