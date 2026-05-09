from airflow.decorators import dag, task
from airflow.providers.trino.hooks.trino import TrinoHook
from pendulum import datetime
import math
import hashlib

TRINO_CONN_ID = "trino_default"


def _fnum(v, default=None):
    if v is None:
        return default
    s = str(v).strip()
    if not s or s.upper() == "NULL":
        return default
    s = s.replace("R$", "").replace(" ", "")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except Exception:
        return default


def _esc(s):
    return (s or "").replace("'", "''")


def _gh_decode(gh):
    alphabet = "0123456789bcdefghjkmnpqrstuvwxyz"
    gh = (gh or "").strip().lower()
    if not gh:
        return None
    lat = [-90.0, 90.0]
    lon = [-180.0, 180.0]
    even = True
    for ch in gh:
        if ch not in alphabet:
            return None
        cd = alphabet.index(ch)
        for mask in [16, 8, 4, 2, 1]:
            if even:
                mid = (lon[0] + lon[1]) / 2
                if cd & mask:
                    lon[0] = mid
                else:
                    lon[1] = mid
            else:
                mid = (lat[0] + lat[1]) / 2
                if cd & mask:
                    lat[0] = mid
                else:
                    lat[1] = mid
            even = not even
    return ((lon[0] + lon[1]) / 2, (lat[0] + lat[1]) / 2)


def _hav(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _jurisdiction_type(road: str) -> str:
    road = (road or "").upper().strip()
    if road.startswith("BR"):
        return "FEDERAL"
    if road:
        return "STATE"
    return "UNKNOWN"


@dag(
    dag_id="dim_toll_plaza_unified_pipeline",
    start_date=datetime(2026, 4, 24),
    schedule="20 3 * * *",
    catchup=False,
    tags=["tolls", "dim", "unified", "antt", "mapeia"],
)
def dim_toll_plaza_unified_pipeline():
    @task
    def rebuild(conn_id: str = TRINO_CONN_ID):
        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()

            def exec_sql(sql):
                cur.execute(sql)

            exec_sql(
                """
            CREATE TABLE IF NOT EXISTS iceberg.oidw.dim_toll_plaza (
              plaza_id varchar,
              plaza_name varchar,
              concessionaria varchar,
              road_code varchar,
              uf varchar,
              municipio varchar,
              km_m double,
              latitude double,
              longitude double,
              source_presence varchar,
              jurisdiction_type varchar,
              coverage_type varchar,
              price_base_brl double,
              price_source varchar,
              has_antt boolean,
              has_mapeia boolean,
              antt_praca_id varchar,
              mapeia_toll_id varchar,
              mapeia_heading_code varchar,
              match_distance_m double,
              match_confidence varchar,
              updated_at timestamp(6)
            ) WITH (format='PARQUET')
            """
            )
            exec_sql("DELETE FROM iceberg.oidw.dim_toll_plaza")

            cur.execute(
                "SELECT concessionaria, praca_de_pedagio, rodovia, uf, km_m, municipal, latitude, longitude FROM iceberg.oidw.raw_antt_pracas_pedagio"
            )
            antt = cur.fetchall()

            cur.execute("SELECT toll_id, heading_code, toll_name_raw, price_brl_raw FROM iceberg.oidw.raw_mapeia_tolls_snapshot")
            mapr = cur.fetchall()

            mpts = []
            for toll_id, heading_code, toll_name_raw, price_brl_raw in mapr:
                d = _gh_decode(toll_id)
                if not d:
                    continue
                lon, lat = d
                mpts.append(
                    {
                        "toll_id": toll_id,
                        "heading_code": heading_code,
                        "name": toll_name_raw or "",
                        "lat": lat,
                        "lon": lon,
                        "price": _fnum(price_brl_raw, 0.0),
                    }
                )

            used_m = set()
            rows = []

            for concessionaria, praca_de_pedagio, rodovia, uf, km_m, municipal, latitude, longitude in antt:
                lat = _fnum(latitude)
                lon = _fnum(longitude)
                if lat is None or lon is None:
                    continue

                best = None
                bestd = 10**18
                for i, m in enumerate(mpts):
                    d = _hav(lat, lon, m["lat"], m["lon"])
                    if d < bestd:
                        bestd = d
                        best = (i, m)

                has_m = False
                price = None
                price_source = None
                mtid = None
                mhead = None
                conf = "low"
                md = None

                if best and bestd <= 2000:
                    i, m = best
                    used_m.add(i)
                    has_m = True
                    price = m["price"]
                    price_source = "mapeia"
                    mtid = m["toll_id"]
                    mhead = m["heading_code"]
                    md = round(bestd, 2)
                    conf = "high" if bestd <= 1000 else "medium"

                road = (rodovia or "").upper().strip()
                cov = "federal" if road.startswith("BR") else "state_or_other"
                plaza_name = (praca_de_pedagio or "").strip()
                pid = hashlib.sha1(f"ANTT|{plaza_name}|{road}|{uf or ''}|{km_m or ''}".encode()).hexdigest()
                source_presence = "both" if has_m else "antt_only"

                rows.append(
                    {
                        "plaza_id": pid,
                        "plaza_name": plaza_name,
                        "concessionaria": concessionaria or "",
                        "road_code": road,
                        "uf": (uf or "").upper(),
                        "municipio": municipal or "",
                        "km_m": _fnum(km_m, 0.0),
                        "latitude": lat,
                        "longitude": lon,
                        "source_presence": source_presence,
                        "jurisdiction_type": _jurisdiction_type(road),
                        "coverage_type": cov,
                        "price_base_brl": price,
                        "price_source": price_source,
                        "has_antt": True,
                        "has_mapeia": has_m,
                        "antt_praca_id": plaza_name,
                        "mapeia_toll_id": mtid,
                        "mapeia_heading_code": mhead,
                        "match_distance_m": md,
                        "match_confidence": conf,
                    }
                )

            for i, m in enumerate(mpts):
                if i in used_m:
                    continue
                name = (m["name"] or "").strip()
                road = "BR" if "BR" in name.upper() else ""
                cov = "federal" if road == "BR" else "state_or_other"
                pid = hashlib.sha1(f"MAPEIA|{m['toll_id']}".encode()).hexdigest()
                rows.append(
                    {
                        "plaza_id": pid,
                        "plaza_name": name,
                        "concessionaria": "",
                        "road_code": road,
                        "uf": "",
                        "municipio": "",
                        "km_m": None,
                        "latitude": m["lat"],
                        "longitude": m["lon"],
                        "source_presence": "mapeia_only",
                        "jurisdiction_type": _jurisdiction_type(road),
                        "coverage_type": cov,
                        "price_base_brl": m["price"],
                        "price_source": "mapeia",
                        "has_antt": False,
                        "has_mapeia": True,
                        "antt_praca_id": None,
                        "mapeia_toll_id": m["toll_id"],
                        "mapeia_heading_code": m["heading_code"],
                        "match_distance_m": None,
                        "match_confidence": "unmatched",
                    }
                )

            chunk = 120
            for k in range(0, len(rows), chunk):
                part = rows[k : k + chunk]
                vals = []
                for r in part:
                    vals.append(
                        "("
                        + f"'{_esc(r['plaza_id'])}',"
                        + f"'{_esc(r['plaza_name'])}',"
                        + f"'{_esc(r['concessionaria'])}',"
                        + f"'{_esc(r['road_code'])}',"
                        + f"'{_esc(r['uf'])}',"
                        + f"'{_esc(r['municipio'])}',"
                        + ("NULL" if r["km_m"] is None else str(r["km_m"]))
                        + ","
                        + ("NULL" if r["latitude"] is None else str(r["latitude"]))
                        + ","
                        + ("NULL" if r["longitude"] is None else str(r["longitude"]))
                        + ","
                        + f"'{_esc(r['source_presence'])}',"
                        + f"'{_esc(r['jurisdiction_type'])}',"
                        + f"'{_esc(r['coverage_type'])}',"
                        + ("NULL" if r["price_base_brl"] is None else str(r["price_base_brl"]))
                        + ","
                        + ("NULL" if r["price_source"] is None else f"'{_esc(r['price_source'])}'")
                        + ","
                        + ("true" if r["has_antt"] else "false")
                        + ","
                        + ("true" if r["has_mapeia"] else "false")
                        + ","
                        + ("NULL" if r["antt_praca_id"] is None else f"'{_esc(r['antt_praca_id'])}'")
                        + ","
                        + ("NULL" if r["mapeia_toll_id"] is None else f"'{_esc(r['mapeia_toll_id'])}'")
                        + ","
                        + (
                            "NULL"
                            if r["mapeia_heading_code"] is None
                            else f"'{_esc(r['mapeia_heading_code'])}'"
                        )
                        + ","
                        + ("NULL" if r["match_distance_m"] is None else str(r["match_distance_m"]))
                        + ","
                        + f"'{_esc(r['match_confidence'])}',CURRENT_TIMESTAMP)"
                    )
                exec_sql("INSERT INTO iceberg.oidw.dim_toll_plaza VALUES " + ",".join(vals))

            return {"rows_inserted": len(rows)}

    @task
    def validate_target(meta: dict, conn_id: str = TRINO_CONN_ID):
        hook = TrinoHook(trino_conn_id=conn_id)
        with hook.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT count(*), count_if(has_antt), count_if(has_mapeia), count_if(source_presence = 'both') FROM iceberg.oidw.dim_toll_plaza")
            cnt, has_antt_cnt, has_mapeia_cnt, both_cnt = cur.fetchone()
        if cnt < 1000:
            raise RuntimeError(f"dim_toll_plaza DQ failed: expected >=1000 rows, got {cnt}")
        if has_antt_cnt < 50:
            raise RuntimeError(f"dim_toll_plaza DQ failed: expected >=50 ANTT-backed rows, got {has_antt_cnt}")
        if has_mapeia_cnt < 500:
            raise RuntimeError(f"dim_toll_plaza DQ failed: expected >=500 Mapeia-backed rows, got {has_mapeia_cnt}")
        if both_cnt < 10:
            raise RuntimeError(f"dim_toll_plaza DQ failed: expected >=10 matched rows, got {both_cnt}")

    validate_target(rebuild())


dim_toll_plaza_unified_pipeline()
