# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "duckdb>=1.5.5",
#     "pyarrow>=25.0.0",
# ]
# ///
"""USACE National Structure Inventory over CONUS, folded to H3 by DuckDB, straight off the API.

The NSI has no bulk mirror on an object store: USACE serves it from its own API
(nsi.sec.usace.army.mil/nsiapi), and the newest fields (the 65+ day/night population,
the community resilience columns) exist only there, not in the 2022 state GeoPackages.
So the fold is a remote query: DuckDB's read_json reads a LIST of bbox URLs in
parallel, one FeatureCollection per tile, `unnest(features)` makes a row per
structure, and `h3_latlng_to_cell` + GROUP BY fold them into cells. Nothing is
downloaded to disk but the per-cell answers.

WHAT IS KEPT: per (tile, res 8 cell), the structure count, the residential count, and
SUMS of value, contents, vehicles, square feet and the four population columns. Sums
and counts are exact at any coarser res (h3_cell_to_parent and re-sum), and every
average is sum / count, so nothing is lost by not storing averages. Res 8 is the
store; the map reads res 6 (the HRRR film's cells).

THE TILES. A 0.2 degree grid anchored at (-180, -90), so tiles are reusable across
boxes. A structure belongs to the tile whose [w, w+step) x [s, s+step) holds it
(the API's bbox is inclusive, so a point on an edge comes back twice; the filter
keeps one). Dense tiles that overflow MAX_OBJECT_MB are split into four and requeued.
A batch that fails is bisected until the failing tile is alone, then that tile is
split (oversize) or retried (anything else) and, after RETRIES, marked failed.

FETCHING. `force_download` (the API ignores Range; httpfs's range reads re-fetch a
tile many times over) and `auto_detect=false` with explicit columns (one GET per tile).
Measured from home: 16 tiles of 0.2 deg over Atlanta, 1.42M structures in 56 s
(25k/s, 10 GB peak), so CONUS is about 80 minutes.

RESUMABLE. State lives in one DuckDB file (`tiles` with pending/done/split/failed,
`cells` with the answers); a batch's cells and its tiles' status commit together.
Kill it and run it again: it picks up the pending tiles.

    uv run nsi_build.py            # CONUS
    uv run nsi_build.py --retry    # requeue failed tiles
    uv run nsi_build.py --box=-95.6,29.6,-95.2,30.0 --db=/tmp/x.duckdb   # a test box
"""

import math
import sys
import time
from pathlib import Path

import duckdb
import pyarrow as pa

API = "https://nsi.sec.usace.army.mil/nsiapi/structures?bbox="
BOX = (-124.8, 24.4, -66.9, 49.5)  # CONUS, the HRRR domain's US land (AK, HI, PR are off-grid)
STEP = 0.2  # base tile, degrees
MIN_STEP = 0.0125  # never split below this (0.2 / 16)
CACHE_RES = 8
BATCH = 32  # tiles per read_json call
THREADS = 16
MAX_OBJECT_MB = 256  # one tile's FeatureCollection must fit; DuckDB buffers ~2x per thread
MEMORY_GB = 40
RETRIES = 3
# Tiles inside FIRST go before the rest (the September 2026 nor'easter's Northeast).
FIRST = (-82.0, 34.0, -66.9, 47.6)
DB = Path(__file__).parent.parent / "data" / "nsi_h3.duckdb"
# What the notebook reads (the DB is locked while the build writes): the res 8 cells
# summed over tiles, and the tiles' status, rewritten every EXPORT_EVERY batches.
OUT_CELLS = DB.parent / "nsi_h3_r8.parquet"
OUT_TILES = DB.parent / "nsi_tiles.parquet"
EXPORT_EVERY = 10

PROPS = (
    "STRUCT(x DOUBLE, y DOUBLE, st_damcat VARCHAR, sqft DOUBLE, val_struct DOUBLE, val_cont DOUBLE, "
    "val_vehic DOUBLE, pop2amu65 DOUBLE, pop2amo65 DOUBLE, pop2pmu65 DOUBLE, pop2pmo65 DOUBLE)"
)


def tile_url(w, s, step):
    e, n = round(w + step, 6), round(s + step, 6)
    return API + f"{w},{s},{e},{s},{e},{n},{w},{n},{w},{s}"


def open_db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB))
    con.sql("INSTALL h3 FROM community; LOAD h3; INSTALL httpfs; LOAD httpfs;")
    con.sql(f"SET memory_limit='{MEMORY_GB}GB'; SET threads={THREADS}; SET preserve_insertion_order=false;")
    # force_download: the API ignores Range headers, and httpfs's default range reads
    # re-fetched each tile over and over (a 171 MB tile curl gets in 9 s sat 15 min).
    con.sql("SET http_timeout=600; SET http_retries=3; SET force_download=true;")
    con.sql("""
        CREATE TABLE IF NOT EXISTS tiles (
            w DOUBLE, s DOUBLE, step DOUBLE, status VARCHAR, n BIGINT, err VARCHAR,
            PRIMARY KEY (w, s, step)
        )""")
    con.sql("""
        CREATE TABLE IF NOT EXISTS cells (
            w DOUBLE, s DOUBLE, step DOUBLE, hex UBIGINT,
            n BIGINT, n_res BIGINT, sqft DOUBLE,
            val_struct DOUBLE, val_cont DOUBLE, val_vehic DOUBLE,
            pop2amu65 DOUBLE, pop2amo65 DOUBLE, pop2pmu65 DOUBLE, pop2pmo65 DOUBLE
        )""")
    if con.sql("SELECT count(*) FROM tiles").fetchone()[0] == 0:
        i0, i1 = math.floor((BOX[0] + 180) / STEP), math.ceil((BOX[2] + 180) / STEP)
        j0, j1 = math.floor((BOX[1] + 90) / STEP), math.ceil((BOX[3] + 90) / STEP)
        ws = [round(i * STEP - 180, 6) for i in range(i0, i1) for _ in range(j0, j1)]
        ss = [round(j * STEP - 90, 6) for _ in range(i0, i1) for j in range(j0, j1)]
        con.register("seed", pa.table({"w": ws, "s": ss}))
        con.sql(f"INSERT INTO tiles SELECT w, s, {STEP}, 'pending', NULL, NULL FROM seed")
        con.unregister("seed")
    return con


def fold(con, batch):
    """One read_json over the batch's URLs; cells and tile status commit together."""
    urls = [tile_url(w, s, st) for w, s, st in batch]
    con.register("batch", pa.table({
        "w": [b[0] for b in batch], "s": [b[1] for b in batch], "step": [b[2] for b in batch], "url": urls,
    }))
    try:
        con.sql("BEGIN")
        con.sql(f"""
            CREATE OR REPLACE TEMP TABLE batch_cells AS
            WITH f AS (
                SELECT filename, unnest(features).properties AS p
                FROM read_json({urls!r}, columns={{'features': 'STRUCT(properties {PROPS})[]'}},
                               filename=true, auto_detect=false, format='auto',
                               maximum_object_size={MAX_OBJECT_MB << 20})
            )
            SELECT b.w, b.s, b.step, h3_latlng_to_cell(p.y, p.x, {CACHE_RES})::UBIGINT AS hex,
                   count(*), count(*) FILTER (WHERE p.st_damcat = 'RES'), sum(p.sqft),
                   sum(p.val_struct), sum(p.val_cont), sum(p.val_vehic),
                   sum(p.pop2amu65), sum(p.pop2amo65), sum(p.pop2pmu65), sum(p.pop2pmo65)
            FROM f JOIN batch b ON b.url = f.filename
            WHERE p.x >= b.w AND p.x < b.w + b.step AND p.y >= b.s AND p.y < b.s + b.step
            GROUP BY ALL
        """)
        con.sql("INSERT INTO cells SELECT * FROM batch_cells")
        con.sql("""
            UPDATE tiles SET status = 'done', err = NULL, n = k.n
            FROM (SELECT b.w, b.s, b.step, coalesce(sum(c.n), 0) AS n
                  FROM batch b LEFT JOIN batch_cells c ON c.w = b.w AND c.s = b.s AND c.step = b.step
                  GROUP BY ALL) k
            WHERE tiles.w = k.w AND tiles.s = k.s AND tiles.step = k.step
        """)
        con.sql("COMMIT")
    except Exception:
        con.sql("ROLLBACK")
        raise
    finally:
        con.unregister("batch")


def run(con, batch, stats):
    """Fold a batch; on failure bisect down to the tile that fails, then split or retry it."""
    try:
        fold(con, batch)
        return
    except Exception as e:
        err = str(e)
    if len(batch) > 1:
        h = len(batch) // 2
        run(con, batch[:h], stats)
        run(con, batch[h:], stats)
        return
    w, s, st = batch[0]
    if "maximum_object_size" in err and st / 2 >= MIN_STEP:
        h = round(st / 2, 6)
        kids = [(w, s), (round(w + h, 6), s), (w, round(s + h, 6)), (round(w + h, 6), round(s + h, 6))]
        con.sql("BEGIN")
        con.sql("UPDATE tiles SET status = 'split' WHERE w = ? AND s = ? AND step = ?", params=[w, s, st])
        con.executemany("INSERT OR REPLACE INTO tiles VALUES (?, ?, ?, 'pending', NULL, NULL)", [[a, b, h] for a, b in kids])
        con.sql("COMMIT")
        stats["split"] += 1
        return
    for k in range(RETRIES):
        time.sleep(5 * (k + 1))
        try:
            fold(con, batch)
            return
        except Exception as e2:
            err = str(e2)
    con.sql("UPDATE tiles SET status = 'failed', err = ? WHERE w = ? AND s = ? AND step = ?", params=[err[:500], w, s, st])
    stats["failed"] += 1
    print(f"failed tile {w},{s} step {st}: {err[:200]}", flush=True)


def export(con):
    """Write the parquet the notebook reads; temp file then rename so a reader never sees half."""
    for sql, out in (
        ("""SELECT hex, sum(n) AS n, sum(n_res) AS n_res, sum(sqft) AS sqft,
                   sum(val_struct) AS val_struct, sum(val_cont) AS val_cont, sum(val_vehic) AS val_vehic,
                   sum(pop2amu65) AS pop2amu65, sum(pop2amo65) AS pop2amo65,
                   sum(pop2pmu65) AS pop2pmu65, sum(pop2pmo65) AS pop2pmo65
            FROM cells GROUP BY hex ORDER BY hex""", OUT_CELLS),
        ("SELECT * FROM tiles", OUT_TILES),
    ):
        tmp = out.with_suffix(".tmp.parquet")
        con.sql(f"COPY ({sql}) TO '{tmp}' (FORMAT parquet)")
        tmp.replace(out)


def main():
    global BOX, DB, OUT_CELLS, OUT_TILES
    for a in sys.argv[1:]:
        if a.startswith("--box="):
            BOX = tuple(float(v) for v in a[6:].split(","))
        elif a.startswith("--db="):
            DB = Path(a[5:])
            OUT_CELLS, OUT_TILES = DB.with_suffix(".cells.parquet"), DB.with_suffix(".tiles.parquet")
    con = open_db()
    if "--retry" in sys.argv:
        con.sql("UPDATE tiles SET status = 'pending', err = NULL WHERE status = 'failed'")
    stats = {"split": 0, "failed": 0}
    t0 = time.perf_counter()
    n0 = con.sql("SELECT coalesce(sum(n), 0) FROM tiles WHERE status = 'done'").fetchone()[0]
    k = 0
    while True:
        # FIRST's tiles, then the rest; splits come back as pending and are picked up
        pend = con.sql("""
            SELECT w, s, step FROM tiles WHERE status = 'pending'
            ORDER BY (w + step > ? AND w < ? AND s + step > ? AND s < ?) DESC, w, s LIMIT ?""",
            params=[FIRST[0], FIRST[2], FIRST[1], FIRST[3], BATCH]).fetchall()
        if not pend:
            break
        run(con, pend, stats)
        k += 1
        if k % EXPORT_EVERY == 0:
            export(con)
        done, total, nst = con.sql("""
            SELECT count(*) FILTER (WHERE status IN ('done', 'failed')), count(*) FILTER (WHERE status <> 'split'),
                   coalesce(sum(n) FILTER (WHERE status = 'done'), 0) FROM tiles""").fetchone()
        dt = time.perf_counter() - t0
        rate = (nst - n0) / dt if dt > 0 else 0
        print(f"tiles {done}/{total} · {nst:,} structures · {rate:,.0f}/s · {stats['split']} split · "
              f"{stats['failed']} failed · {dt / 60:.1f} min", flush=True)
    export(con)
    n_cells = con.sql("SELECT count(DISTINCT hex) FROM cells").fetchone()[0]
    print(f"DONE · {nst:,} structures · {n_cells:,} res {CACHE_RES} cells · {(time.perf_counter() - t0) / 60:.1f} min",
          flush=True)


if __name__ == "__main__":
    main()
