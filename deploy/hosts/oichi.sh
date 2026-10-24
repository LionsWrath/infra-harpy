# oichi: compose interpolation variables that differ from scripts/vars.sh (non-secret, sourced after it).
export OPENCLAW_PATH=${HOME}/openclaw   # code-server workspace mount

# Env files whose values are needed for ${...} interpolation in compose files (passed as --env-file).
# Compose never reads a service's env_file for interpolation.
COMPOSE_INTERPOLATION_ENV_FILES="envs/code-members.env"   # PASSWORD=${CODE_MEMBERS_PASSWORD}
