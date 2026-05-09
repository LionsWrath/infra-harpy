# DAG Registry

_Last updated: 2026-05-09 UTC_

## Status classes
- **prod-scheduled**: active scheduled DAG expected to run automatically
- **prod-manual**: active/on-demand DAG expected to be triggered manually or by orchestration
- **backfill**: one-shot or historical replay DAG
- **lab**: experimental pipeline, not yet stable production
- **legacy-review**: older/redundant DAG kept for reference or migration safety

## Operational decisions - phase 2
- Promote the corrected price DAGs to **prod-scheduled** after successful validation with final DQ:
  - `imea_precos_interior_raw_pipeline`
  - `cepea_precos_praca_raw_pipeline`
  - `estadual_precos_interior_raw_pipeline`
- Keep `dim_toll_plaza_unified_pipeline` as the active scheduled toll-plaza dimension DAG.
- Keep `dim_toll_plaza_pipeline` paused as legacy because it was superseded by the unified pipeline.
- Keep old/derived curated DAGs paused until their production role is explicitly confirmed.
- Avoid changing DAG IDs during stabilization unless a migration plan is ready.

## Current classification

### prod-scheduled
| DAG | schedule | paused | notes |
|---|---|---:|---|
| `anp_diesel_raw_pipeline` | `0 9 5 * *` | no | monthly ANP raw upstream; promoted to prod-scheduled |
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
| `imea_precos_interior_raw_pipeline` | `0 8 * * 1-5` | no | corrected, validated, final DQ added; promoted to prod-scheduled |
| `cepea_precos_praca_raw_pipeline` | `20 8 * * 1-5` | no | corrected, validated, final DQ added; promoted to prod-scheduled |
| `estadual_precos_interior_raw_pipeline` | `0 9 * * 5` | no | corrected, validated, final DQ added; promoted to prod-scheduled |

### prod-manual
| DAG | schedule | paused | notes |
|---|---|---:|---|
| `dim_logistic_node_seed_pipeline` | `None` | no | utility/manual seed pipeline |
| `curated_antt_tarifa_base_pipeline` | `None` | yes | useful curated step, but still paused pending promotion decision |

### backfill
| DAG | schedule | paused | notes |
|---|---|---:|---|
| `raw_rss_update_news_backfill` | `None` | yes | historical replay only |
| `wiki_dim_backfill` | `None` | yes | backfill/enrichment helper |

### lab
| DAG | schedule | paused | notes |
|---|---|---:|---|
| `route_analytics_staged_pipeline` | `None` | yes | experimental/staged analytics |

### legacy-review
| DAG | schedule | paused | notes |
|---|---|---:|---|
| `dim_toll_plaza_pipeline` | `None` | yes | superseded by `dim_toll_plaza_unified_pipeline` |
| `raw_antt_pracas_pedagio_pipeline` | `None` | yes | source/support ingest, not active production schedule |
| `raw_antt_toll_economics_pipeline` | `10 3 * * *` | yes | paused pending decision on production role |
| `curated_conab_frete_latest_pipeline` | `None` | yes | table exists, but production role not yet confirmed |
| `curated_diesel_price_latest_by_uf_pipeline` | `None` | yes | table exists, but production role not yet confirmed |
| `curated_ibge_municipios_pipeline` | `None` | yes | older derived helper kept paused |

## Naming target
- Raw: `<source>_<entity>_raw_pipeline`
- Curated: `<domain>_<entity>_curated_pipeline`
- Dim: `<entity>_dim_pipeline`
- Analytics: `<domain>_<metric>_analytics_pipeline`

## Notes
- Keep old DAG IDs paused during migrations when history matters.
- Keep backups out of the live DAG directory.
- Prefer explicit source labeling when fallback providers are used.
- Treat `infra-harpy/deploy/airflow/dags` as the git mirror/source-of-truth snapshot for review history.
