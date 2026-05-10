"""
### Trino-based Wikipedia backfill DAG

Same taskflow pattern as other pipelines:
- ensure columns
- enrich persons
- enrich organizations
"""

from airflow.decorators import dag, task
from airflow.providers.trino.hooks.trino import TrinoHook
from pendulum import datetime

import json
import logging
import requests
import wikipedia

TRINO_CONN_ID = "trino_default"
WIKI_LANG = "en"
USER_AGENT = "OrochiDataOps/1.0 (airflow; contact: admin@local)"
CANDIDATE_LIMIT = 200

log = logging.getLogger(__name__)


def _sql_quote(v: str) -> str:
    return "'" + (v or "").replace("'", "''") + "'"


def _wiki_categories(page_title: str):
    url = "https://en.wikipedia.org/w/api.php"
    params = {
        "action": "query",
        "prop": "categories",
        "titles": page_title,
        "cllimit": "max",
        "format": "json",
        "redirects": 1,
    }
    r = requests.get(url, params=params, timeout=25, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    data = r.json()
    pages = data.get("query", {}).get("pages", {})
    cats = []
    for _, p in pages.items():
        for c in p.get("categories", []):
            title = c.get("title")
            if title:
                cats.append(title)
    return cats


def _pick_page(name: str):
    wikipedia.set_lang(WIKI_LANG)
    wikipedia.set_user_agent(USER_AGENT)

    candidates = wikipedia.search(name, results=8)
    if not candidates:
        return None

    lowered = name.lower().strip()
    ordered = sorted(candidates, key=lambda t: (t.lower() != lowered, len(t)))

    for cand in ordered:
        try:
            return wikipedia.page(cand, auto_suggest=False, redirect=True)
        except wikipedia.DisambiguationError as de:
            for opt in de.options[:5]:
                try:
                    return wikipedia.page(opt, auto_suggest=False, redirect=True)
                except Exception:
                    continue
        except Exception:
            continue
    return None


@dag(dag_id="wiki_dim_backfill", start_date=datetime(2026, 3, 16), schedule=None, catchup=False)
def wiki_dim_backfill():

    @task
    def ensure_columns(conn_id):
        hook = TrinoHook(trino_conn_id=conn_id)

        def has_col(cur, table: str, col: str):
            cur.execute(
                f"""
                SELECT count(*)
                FROM iceberg.information_schema.columns
                WHERE table_schema='oidw'
                  AND table_name={_sql_quote(table)}
                  AND column_name={_sql_quote(col)}
                """
            )
            return cur.fetchone()[0] > 0

        with hook.get_conn() as conn:
            cur = conn.cursor()
            for table in ("dim_person", "dim_organization"):
                for col, ctype in (
                    ("wikipedia_id", "VARCHAR"),
                    ("wikipedia_url", "VARCHAR"),
                    ("wikipedia_categories_json", "VARCHAR"),
                ):
                    if not has_col(cur, table, col):
                        cur.execute(f"ALTER TABLE iceberg.oidw.{table} ADD COLUMN {col} {ctype}")
                        log.info("Added %s.%s", table, col)

    @task
    def enrich_persons(conn_id):
        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT person_uuid, full_name
                FROM iceberg.oidw.dim_person
                WHERE coalesce(trim(wikipedia_id), '') = ''
                  AND coalesce(trim(full_name), '') <> ''
                ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST
                LIMIT {CANDIDATE_LIMIT}
                """
            )
            rows = cur.fetchall()

            updated = 0
            for person_uuid, full_name in rows:
                page = _pick_page(full_name)
                if not page:
                    continue

                cats = []
                try:
                    cats = _wiki_categories(page.title)
                except Exception as e:
                    log.warning("Category fetch failed for %s: %s", page.title, e)

                cur.execute(
                    f"""
                    UPDATE iceberg.oidw.dim_person
                    SET
                      wikipedia_id = {_sql_quote(page.title)},
                      wikipedia_url = {_sql_quote(page.url)},
                      wikipedia_categories_json = {_sql_quote(json.dumps(cats))},
                      updated_at = current_timestamp
                    WHERE person_uuid = {_sql_quote(person_uuid)}
                    """
                )
                updated += 1

            log.info("dim_person updated rows: %s", updated)

    @task
    def enrich_orgs(conn_id):
        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT organization_uuid, organization_name
                FROM iceberg.oidw.dim_organization
                WHERE coalesce(trim(wikipedia_id), '') = ''
                  AND coalesce(trim(organization_name), '') <> ''
                ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST
                LIMIT {CANDIDATE_LIMIT}
                """
            )
            rows = cur.fetchall()

            updated = 0
            for org_uuid, org_name in rows:
                page = _pick_page(org_name)
                if not page:
                    continue

                cats = []
                try:
                    cats = _wiki_categories(page.title)
                except Exception as e:
                    log.warning("Category fetch failed for %s: %s", page.title, e)

                cur.execute(
                    f"""
                    UPDATE iceberg.oidw.dim_organization
                    SET
                      wikipedia_id = {_sql_quote(page.title)},
                      wikipedia_url = {_sql_quote(page.url)},
                      wikipedia_categories_json = {_sql_quote(json.dumps(cats))},
                      updated_at = current_timestamp
                    WHERE organization_uuid = {_sql_quote(org_uuid)}
                    """
                )
                updated += 1

            log.info("dim_organization updated rows: %s", updated)

    ensure_columns(conn_id=TRINO_CONN_ID) \
        >> enrich_persons(conn_id=TRINO_CONN_ID) \
        >> enrich_orgs(conn_id=TRINO_CONN_ID)


wiki_dim_backfill()
