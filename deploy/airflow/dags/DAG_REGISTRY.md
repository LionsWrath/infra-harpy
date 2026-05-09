# DAG Registry

_Last updated: 2026-05-09 UTC_

## Status classes
- **prod-scheduled**: active scheduled DAG expected to run automatically
- **prod-manual**: active/on-demand DAG expected to be triggered manually or by orchestration
- **backfill**: one-shot or historical replay DAG
- **lab**: experimental pipeline, not yet stable production
- **legacy-review**: older/redundant DAG kept until replacement decision

## Current classification

### prod-scheduled
- `bcb_rates_fx_raw_pipeline`
- `cbot_futures_raw_pipeline`
- `cbot_contracts_raw_pipeline`
- `comex_fertilizantes_raw_pipeline`
- `conab_frete_raw_pipeline`
- `ibge_ufs_regioes_raw_pipeline`
- `ibge_municipios_raw_pipeline`
- `sidra_lavouras_raw_pipeline`
- `raw_mapeia_tolls_snapshot_pipeline`
- `raw_rss_update_news`
- `dim_toll_plaza_unified_pipeline`

### prod-manual
- `imea_precos_interior_raw_pipeline`
- `cepea_precos_praca_raw_pipeline`
- `estadual_precos_interior_raw_pipeline`
- `dim_logistic_node_seed_pipeline`
- `curated_antt_tarifa_base_pipeline`

### backfill
- `raw_rss_update_news_backfill`
- `wiki_dim_backfill`

### lab
- `route_analytics_staged_pipeline`

### legacy-review
- `dim_toll_plaza_pipeline`
- `raw_antt_pracas_pedagio_pipeline`
- `raw_antt_toll_economics_pipeline`
- `curated_conab_frete_latest_pipeline`
- `curated_diesel_price_latest_by_uf_pipeline`
- `curated_ibge_municipios_pipeline`
- `anp_diesel_raw_pipeline`

## Naming target
- Raw: `<source>_<entity>_raw_pipeline`
- Curated: `<domain>_<entity>_curated_pipeline`
- Dim: `<entity>_dim_pipeline`
- Analytics: `<domain>_<metric>_analytics_pipeline`

## Notes
- Keep old DAG IDs paused during migrations when history matters.
- Keep backups out of the live DAG directory.
- Prefer explicit source labeling when fallback providers are used.
