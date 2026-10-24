# Per-host deployment

Each Docker host has one entry file listing exactly the stacks it runs:

| Host | Entry file | Host variables | Host variants |
|---|---|---|---|
| oichi | `docker-compose.oichi.yml` | `hosts/oichi.sh` | - |
| hichi | `docker-compose.hichi.yml` | `hosts/hichi.sh` | `apps/docker-compose.<stack>.hichi.yml` |

- Shared stacks live in `apps/docker-compose.<stack>.yml`. A host variant (`<stack>.<host>.yml`) exists only where a
  host really differs (fixed IPs, DNS settings, pinned image).
- `hosts/<host>.sh` holds non-secret interpolation variables that differ from `scripts/vars.sh`.
- Each include entry lists the env files used to fill `${...}` in that stack (`env_file:` on the include), the same
  as deploying it with `--env-file envs/base.env --env-file envs/<stack>.env`. A service's own `env_file` only
  sets container environment; it never feeds interpolation.
- `envs/base.env` holds only shared, non-secret settings (`TZ`). Almost every container loads it, so a secret
  there would reach all of them; put secrets in the stack's own env file instead.
- `docker-compose.yml` is the old shared include list. It is deprecated and kept only for reference.

## Everyday use
Always go through the wrapper. It loads `scripts/vars.sh` and `hosts/<host>.sh`, picks the entry file from the
hostname (`harpy-ichi` -> `hichi`, override with `HARPY_HOST`) and uses compose project `apps`, which owns the
existing containers.

```bash
scripts/compose.sh ps
scripts/compose.sh config --quiet          # validate (plain `config` is refused: it prints secrets)
scripts/compose.sh up -d <service>         # service-scoped changes
scripts/compose.sh up -d --dry-run         # preview before any wider change
```

Containers created by older compose versions are recreated on the next `up` even when nothing changed
(compose changed its config hash). Expect a restart, not a change; preview with `--dry-run` first.

## Full re-setup of a host
1. Provision the VM: Docker with compose >= 2.20, Tailscale, SSH key access.
2. Clone this repo (`~/infra-harpy` on oichi; hichi currently deploys from `~/terraform`, same repo).
3. Materialize the host's env set from the secrets store into `deploy/` (writes `envs/*.env` and
   `scripts/vars.sh`; real values never come from Git).
4. Check, then prepare:
   ```bash
   scripts/bootstrap-host.sh            # read-only: env files, lsio network, data dirs, config templates
   scripts/bootstrap-host.sh --apply    # create the lsio network, missing dirs under $HOME/data, templates
   ```
5. Restore service data (`$DATA_PATH/<service>`, databases) from backups.
6. Start: `scripts/compose.sh up -d`.

## Not managed by the entry files
Started outside compose or by their own project; recreate them by hand if needed:
- oichi: `ib-gateway` (own project in `$DATA_PATH/ib-gateway`), `metrics-plotter`, `osrm-centro-oeste` (stopped).
- hichi: `flaresolverr`.
