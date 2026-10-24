# Trino host config templates

Mounted from `${DATA_PATH}/trino/` by `deploy/apps/docker-compose.trino-iceberg.yml`:

| Template | Host path | Container path |
|---|---|---|
| `config.properties` | `${DATA_PATH}/trino/config.properties` | `/etc/trino/config.properties` |
| `jvm.config` | `${DATA_PATH}/trino/jvm.config` | `/etc/trino/jvm.config` |
| `catalog/iceberg.properties` | `${DATA_PATH}/trino/catalog/` | `/etc/trino/catalog/` |

Also create `${DATA_PATH}/trino/runtime` and `${DATA_PATH}/trino/tmp`. Size `-Xmx` and `query.max-*` to the host.
