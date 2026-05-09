from airflow.decorators import dag, task
from airflow.providers.trino.hooks.trino import TrinoHook
from pendulum import datetime

import hashlib
import math
import re
import unicodedata

TRINO_CONN_ID = "trino_default"


def _norm_text(v: str) -> str:
    if v is None:
        return ""
    s = unicodedata.normalize("NFKD", str(v))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return " ".join(s.strip().lower().split())


def _gh_decode(gh: str):
    if not gh:
        return None
    base32 = "0123456789bcdefghjkmnpqrstuvwxyz"
    even = True
    lat = [-90.0, 90.0]
    lon = [-180.0, 180.0]
    for c in gh.strip().lower():
        cd = base32.find(c)
        if cd < 0:
            return None
        for mask in [16, 8, 4, 2, 1]:
            if even:
                mid = (lon[0] + lon[1]) / 2.0
                if cd & mask:
                    lon[0] = mid
                else:
                    lon[1] = mid
            else:
                mid = (lat[0] + lat[1]) / 2.0
                if cd & mask:
                    lat[0] = mid
                else:
                    lat[1] = mid
            even = not even
    return (lat[0] + lat[1]) / 2.0, (lon[0] + lon[1]) / 2.0


def _haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _normalize_road_code(road: str) -> str:
    s = (road or "").upper().strip()
    if not s:
        return ""
    s = s.replace("_", "-").replace(" ", "-")
    s = re.sub(r"-+", "-", s)
    return s


def _infer_jurisdiction_type(road_code: str) -> str:
    rc = _normalize_road_code(road_code)
    if rc.startswith("BR-"):
        return "FEDERAL"
    if len(rc) >= 3 and rc[0:2].isalpha() and rc[2] == "-":
        return "STATE"
    return "UNKNOWN"


def _extract_road_from_name(name: str) -> str:
    nm = str(name or "")
    m = re.search(r"\(([^\)]+)\)", nm)
    if not m:
        return ""
    raw = m.group(1).strip().upper()
    raw = raw.replace(" ", "-")
    raw = re.sub(r"-+", "-", raw)
    # normalize examples: 'SP-330', 'BR-153'
    if re.match(r"^[A-Z]{2,3}-?\d{2,4}$", raw):
        if "-" not in raw:
            raw = raw[:2] + "-" + raw[2:]
    return raw


@dag(
    dag_id="dim_toll_plaza_pipeline",
    start_date=datetime(2026, 4, 24),
    schedule=None,
    catchup=False,
    tags=["tolls", "dimension", "on-demand"],
)
def dim_toll_plaza_pipeline():
    @task
    def build_dim(conn_id: str = TRINO_CONN_ID):
        hook = TrinoHook(trino_conn_id=conn_id)
        conn = hook.get_conn()
        cur = conn.cursor()

        # 1) latest mapeia snapshot only (critical dedup rule)
        cur.execute(
            """
            SELECT toll_id, toll_name_raw, radius_m_raw, price_brl_raw, heading_code
            FROM iceberg.oidw.raw_mapeia_tolls_snapshot
            WHERE datestr = (SELECT max(datestr) FROM iceberg.oidw.raw_mapeia_tolls_snapshot)
            """
        )
        m_rows = cur.fetchall()

        mapeia = []
        seen_m = set()
        for toll_id, toll_name_raw, radius_m_raw, price_brl_raw, heading_code in m_rows:
            if not toll_id:
                continue
            d = _gh_decode(str(toll_id))
            if not d:
                continue
            # dedup by toll_id in latest snapshot
            if toll_id in seen_m:
                continue
            seen_m.add(toll_id)
            try:
                price = float(price_brl_raw) if price_brl_raw is not None else None
            except Exception:
                price = None
            try:
                radius = float(radius_m_raw) if radius_m_raw is not None else None
            except Exception:
                radius = None
            mapeia.append(
                {
                    "toll_id": str(toll_id),
                    "name": str(toll_name_raw or "").strip(),
                    "lat": d[0],
                    "lon": d[1],
                    "price": price,
                    "radius": radius,
                    "heading": str(heading_code or "").strip(),
                }
            )

        # 2) Build ANTT implied base tariff map from Receita / Volume Equivalente
        #    tarifa_implicita = receita_pedagio / volume_veiculo_equivalente
        antt_tariff_map = {}
        antt_tariff_name_only = {}
        try:
            cur.execute(
                """
                SELECT
                  concessionaria,
                  praca,
                  tarifa_base_median_brl
                FROM iceberg.oidw.curated_antt_tarifa_base_praca
                WHERE tarifa_base_median_brl IS NOT NULL
                """
            )
            tv_rows = cur.fetchall()
            for conc, praca, tarifa in tv_rows:
                n_conc = _norm_text(conc)
                n_praca = _norm_text(praca)
                key = (n_conc, n_praca)
                # curated table already stores median per praça; keep latest overwrite-safe assignment
                antt_tariff_map[key] = float(tarifa)
                if n_praca not in antt_tariff_name_only:
                    antt_tariff_name_only[n_praca] = float(tarifa)
        except Exception:
            # keep map empty if source tables are unavailable/incomplete
            antt_tariff_map = {}
            antt_tariff_name_only = {}

        # 3) ANTT active dedup by geo/name key
        cur.execute(
            """
            SELECT praca_de_pedagio, concessionaria, rodovia, uf, km_m, municipal, latitude, longitude
            FROM iceberg.oidw.raw_antt_pracas_pedagio
            WHERE situacao = 'Ativo'
              AND latitude IS NOT NULL
              AND longitude IS NOT NULL
            """
        )
        a_rows = cur.fetchall()
        antt = []
        seen_a = set()
        for praca, conc, rod, uf, km_m, mun, lat, lon in a_rows:
            key = (
                _norm_text(praca),
                str(uf or "").strip().upper(),
                _norm_text(rod),
                round(float(km_m), 1) if km_m is not None else None,
                round(float(lat), 5),
                round(float(lon), 5),
            )
            if key in seen_a:
                continue
            seen_a.add(key)
            antt.append(
                {
                    "name": str(praca or "").strip(),
                    "concessionaria": str(conc or "").strip(),
                    "road": str(rod or "").strip(),
                    "uf": str(uf or "").strip().upper(),
                    "km_m": float(km_m) if km_m is not None else None,
                    "municipio": str(mun or "").strip(),
                    "lat": float(lat),
                    "lon": float(lon),
                }
            )

        # 3) one-to-one nearest match ANTT->Mapeia within threshold
        threshold_m = 500.0
        matched_pairs = []
        used_m = set()

        for i, a in enumerate(antt):
            best = None
            best_j = None
            for j, m in enumerate(mapeia):
                if j in used_m:
                    continue
                d = _haversine_m(a["lat"], a["lon"], m["lat"], m["lon"])
                if best is None or d < best:
                    best = d
                    best_j = j
            if best is not None and best <= threshold_m:
                matched_pairs.append((i, best_j, best))
                used_m.add(best_j)

        matched_a = {i for i, _, _ in matched_pairs}
        matched_m = {j for _, j, _ in matched_pairs}

        records = []

        def mk_id(*parts):
            raw = "|".join(str(p or "") for p in parts)
            return hashlib.sha1(raw.encode("utf-8")).hexdigest()

        # BOTH
        for i, j, dist in matched_pairs:
            a = antt[i]
            m = mapeia[j]
            records.append(
                {
                    "plaza_id": mk_id("both", a["name"], a["uf"], m["toll_id"]),
                    "plaza_name": m["name"] or a["name"],
                    "concessionaria": a["concessionaria"],
                    "road_code": a["road"],
                    "uf": a["uf"],
                    "municipio": a["municipio"],
                    "km_m": a["km_m"],
                    "latitude": a["lat"],
                    "longitude": a["lon"],
                    "source_presence": "BOTH",
                    "jurisdiction_type": _infer_jurisdiction_type(a["road"]),
                    "coverage_type": "both",
                    "price_base_brl": m["price"],
                    "price_source": "mapeia",
                    "has_antt": True,
                    "has_mapeia": True,
                    "antt_praca_id": mk_id("antt", a["name"], a["uf"], a["road"], a["km_m"]),
                    "mapeia_toll_id": m["toll_id"],
                    "mapeia_heading_code": m["heading"],
                    "match_distance_m": dist,
                    "match_confidence": "high" if dist <= 200 else ("medium" if dist <= 350 else "low"),
                }
            )

        # ANTT only
        for i, a in enumerate(antt):
            if i in matched_a:
                continue

            n_conc = _norm_text(a["concessionaria"])
            n_praca = _norm_text(a["name"])
            implied = antt_tariff_map.get((n_conc, n_praca))
            if implied is None:
                implied = antt_tariff_name_only.get(n_praca)

            records.append(
                {
                    "plaza_id": mk_id("antt", a["name"], a["uf"], a["road"], a["km_m"]),
                    "plaza_name": a["name"],
                    "concessionaria": a["concessionaria"],
                    "road_code": a["road"],
                    "uf": a["uf"],
                    "municipio": a["municipio"],
                    "km_m": a["km_m"],
                    "latitude": a["lat"],
                    "longitude": a["lon"],
                    "source_presence": "ANTT_ONLY",
                    "jurisdiction_type": _infer_jurisdiction_type(a["road"]),
                    "coverage_type": "federal",
                    "price_base_brl": implied,
                    "price_source": "antt_implied" if implied is not None else None,
                    "has_antt": True,
                    "has_mapeia": False,
                    "antt_praca_id": mk_id("antt", a["name"], a["uf"], a["road"], a["km_m"]),
                    "mapeia_toll_id": None,
                    "mapeia_heading_code": None,
                    "match_distance_m": None,
                    "match_confidence": "unmatched",
                }
            )

        # Mapeia only
        for j, m in enumerate(mapeia):
            if j in matched_m:
                continue
            # best-effort parse UF from name format "City, UF - ..."
            uf = ""
            nm = m["name"]
            if "," in nm:
                maybe = nm.split(",", 1)[1].strip()[:2].upper()
                if len(maybe) == 2 and maybe.isalpha():
                    uf = maybe
            m_road = _extract_road_from_name(m["name"])
            records.append(
                {
                    "plaza_id": mk_id("mapeia", m["toll_id"]),
                    "plaza_name": m["name"],
                    "concessionaria": None,
                    "road_code": m_road or None,
                    "uf": uf,
                    "municipio": None,
                    "km_m": None,
                    "latitude": m["lat"],
                    "longitude": m["lon"],
                    "source_presence": "MAPEIA_ONLY",
                    "jurisdiction_type": _infer_jurisdiction_type(m_road),
                    "coverage_type": "state_or_other",
                    "price_base_brl": m["price"],
                    "price_source": "mapeia",
                    "has_antt": False,
                    "has_mapeia": True,
                    "antt_praca_id": None,
                    "mapeia_toll_id": m["toll_id"],
                    "mapeia_heading_code": m["heading"],
                    "match_distance_m": None,
                    "match_confidence": "unmatched",
                }
            )

        # final strict dedup by plaza_id (safety)
        by_id = {}
        for r in records:
            by_id[r["plaza_id"]] = r
        records = list(by_id.values())

        def q(v):
            if v is None:
                return "NULL"
            if isinstance(v, bool):
                return "true" if v else "false"
            if isinstance(v, (int, float)):
                return str(v)
            return "'" + str(v).replace("'", "''") + "'"

        cur.execute("DROP TABLE IF EXISTS iceberg.oidw.dim_toll_plaza")
        cur.execute(
            """
            CREATE TABLE iceberg.oidw.dim_toll_plaza (
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

        chunk = 250
        for i in range(0, len(records), chunk):
            part = records[i : i + chunk]
            vals = []
            for r in part:
                vals.append(
                    "("
                    + ",".join(
                        [
                            q(r["plaza_id"]),
                            q(r["plaza_name"]),
                            q(r["concessionaria"]),
                            q(r["road_code"]),
                            q(r["uf"]),
                            q(r["municipio"]),
                            q(r["km_m"]),
                            q(r["latitude"]),
                            q(r["longitude"]),
                            q(r["source_presence"]),
                            q(r["jurisdiction_type"]),
                            q(r["coverage_type"]),
                            q(r["price_base_brl"]),
                            q(r["price_source"]),
                            q(r["has_antt"]),
                            q(r["has_mapeia"]),
                            q(r["antt_praca_id"]),
                            q(r["mapeia_toll_id"]),
                            q(r["mapeia_heading_code"]),
                            q(r["match_distance_m"]),
                            q(r["match_confidence"]),
                            "CURRENT_TIMESTAMP",
                        ]
                    )
                    + ")"
                )
            cur.execute("INSERT INTO iceberg.oidw.dim_toll_plaza VALUES " + ",".join(vals))

    build_dim()


dim_toll_plaza_pipeline()
