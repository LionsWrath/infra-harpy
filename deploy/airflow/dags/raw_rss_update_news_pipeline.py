"""
### Trino-based RSS update DAG

Same task structure as before:
- create staging table
- fetch RSS + insert staging
- promote staging to target
- cleanup staging
"""

from airflow.decorators import dag, task
from airflow.operators.python import get_current_context
from pendulum import datetime, from_timestamp
from airflow.providers.trino.hooks.trino import TrinoHook
from calendar import timegm
import feedparser
import logging
import requests

TRINO_CONN_ID = "trino_conn"
log = logging.getLogger(__name__)


@dag(start_date=datetime(2026, 3, 13), schedule="0 3 * * *", catchup=False)
def raw_rss_update_news():

    @task
    def create_stg_table(conn_id):
        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("DROP TABLE IF EXISTS oidw.stg_raw_news")
            cur.execute(
                """
                CREATE TABLE oidw.stg_raw_news (
                    ingested_at TIMESTAMP,
                    datestr VARCHAR,
                    summary VARCHAR,
                    feed_uuid VARCHAR,
                    published TIMESTAMP,
                    url VARCHAR,
                    title VARCHAR
                )
                """
            )

    @task
    def rss_news(conn_id):
        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT
                    feed_uuid,
                    url
                FROM oidw.dim_rss_feed
                WHERE is_active = TRUE
                ORDER BY priority DESC, name
                """
            )
            sources = cur.fetchall()

            news = []
            for feed_uuid, url in sources:
                log.info("RSS: %s %s", feed_uuid, url)
                try:
                    resp = requests.get(url, timeout=20, headers={"User-Agent": "orochi-rss-ingest/1.0"})
                    resp.raise_for_status()
                    raw_news = feedparser.parse(resp.content)
                except Exception as e:
                    log.warning("RSS fetch failed: %s %s err=%s", feed_uuid, url, e)
                    continue

                for n in raw_news.get("entries", []):
                    if "published_parsed" not in n:
                        continue
                    published_epoch = timegm(n["published_parsed"])  # UTC-safe
                    datestr = from_timestamp(published_epoch, tz="UTC").format("YYYY-MM-DD")

                    news.append([
                        n.get("summary", ""),
                        feed_uuid,
                        published_epoch,
                        n.get("link", url),
                        n.get("title", ""),
                        datestr,
                    ])

            if not news:
                log.info("No RSS entries to insert")
                return

            cur.executemany(
                """
                INSERT INTO oidw.stg_raw_news (
                    ingested_at,
                    summary,
                    feed_uuid,
                    published,
                    url,
                    title,
                    datestr
                )
                VALUES (
                    CURRENT_TIMESTAMP,
                    ?,
                    ?,
                    from_unixtime(?),
                    ?,
                    ?,
                    ?
                )
                """,
                news,
            )

    @task
    def promote_stg_table(conn_id):
        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                INSERT INTO iceberg.oidw.raw_news (
                    ingested_at,
                    summary,
                    feed_uuid,
                    published,
                    url,
                    title,
                    datestr
                )
                SELECT
                    ingested_at,
                    summary,
                    feed_uuid,
                    published,
                    url,
                    title,
                    datestr
                FROM oidw.stg_raw_news
                """
            )

    @task
    def cleanup(conn_id):
        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("DROP TABLE IF EXISTS oidw.stg_raw_news")

    create_stg_table(conn_id=TRINO_CONN_ID) \
        >> rss_news(conn_id=TRINO_CONN_ID) \
        >> promote_stg_table(conn_id=TRINO_CONN_ID) \
        >> cleanup(conn_id=TRINO_CONN_ID)


raw_rss_update_news()
