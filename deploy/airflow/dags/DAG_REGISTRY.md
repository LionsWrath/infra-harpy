# DAG Registry

_Last updated: 2026-05-10 UTC_

## Status classes
- **prod-scheduled**: active scheduled DAG expected to run automatically
- **prod-manual**: active/on-demand DAG expected to be triggered manually or by orchestration
- **archived**: removed from live DAG root and stored outside Airflow scan path

## Current classification

### prod-scheduled
| DAG | schedule | paused | notes |
|---|---|---:|---|
| `anp_diesel_raw_pipeline` | `0 9 5 * *` | no | monthly ANP raw upstream; validated with real run |
| `bcb_rates_fx_raw_pipeline` | `30 3 * * *` | no | active scheduled raw ingest |
| `cbot_futures_raw_pipeline` | `30 3 * * *` | no | active scheduled raw ingest |
| `cbot_contracts_raw_pipeline` | `45 3 * * *` | no | active scheduled raw ingest |
| `comex_fertilizantes_raw_pipeline` | `45 4 * * *` | no | active scheduled raw ingest |
| `conab_frete_raw_pipeline` | `15 4 * * *` | no | active scheduled raw ingest |
| `ibge_ufs_regioes_raw_pipeline` | `0 2 * * 1` | no | active scheduled dimension/raw support |
| `ibge_municipios_raw_pipeline` | `0 2 * * 1` | no | active scheduled dimension/raw support |
| `sidra_lavouras_raw_pipeline` | `30 2 * * 1` | no | active scheduled raw ingest |
| `raw_mapeia_tolls_snapshot_pipeline` | `15 2 * * *` | no | active scheduled toll source |
| `raw_rss_update_news` | `0 3 * * *` | no | active scheduled news refresh |
| `dim_toll_plaza_unified_pipeline` | `20 3 * * *` | no | corrected and validated with final DQ |
| `imea_precos_interior_raw_pipeline` | `0 8 * * 1-5` | no | corrected, validated, final DQ added; **upstream milho currently needs review** |
| `cepea_precos_praca_raw_pipeline` | `20 8 * * 1-5` | no | corrected, validated, final DQ added |
| `estadual_precos_interior_raw_pipeline` | `0 9 * * 5` | no | corrected, validated, final DQ added |

### prod-manual
| DAG | schedule | paused | notes |
|---|---|---:|---|
| `dim_logistic_node_seed_pipeline` | `None` | no | utility/manual seed pipeline |

### archived
Archive root outside live DAG scan path:
- `/home/lionswrath/data/services/airflow/dags_archived/2026-05-10-inactive/`

| DAG | previous_class | notes |
|---|---|---|
| `curated_antt_tarifa_base_pipeline` | prod-manual | paused helper archived out of live DAG root |
| `raw_rss_update_news_backfill` | backfill | historical replay DAG archived |
| `wiki_dim_backfill` | backfill | historical helper archived |
| `route_analytics_staged_pipeline` | lab | experimental DAG archived |
| `dim_toll_plaza_pipeline` | legacy-review | superseded by unified pipeline |
| `raw_antt_pracas_pedagio_pipeline` | legacy-review | inactive source/support DAG archived |
| `raw_antt_toll_economics_pipeline` | legacy-review | inactive toll economics DAG archived |
| `curated_conab_frete_latest_pipeline` | legacy-review | inactive derived DAG archived |
| `curated_diesel_price_latest_by_uf_pipeline` | legacy-review | inactive derived DAG archived |
| `curated_ibge_municipios_pipeline` | legacy-review | inactive derived DAG archived |

## Notes
- Archived DAGs were moved **outside** `/home/lionswrath/data/services/airflow/dags` so Airflow no longer scans them.
- Keep old DAG IDs archived for reference/backfill recovery when needed.
- Prefer explicit source labeling when fallback providers are used.
- Treat `infra-harpy/deploy/airflow/dags` as the git mirror/source-of-truth snapshot for review history.
