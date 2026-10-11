# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "duckdb>=1.5.5",
# ]
# ///
"""USACE National Structure Inventory 2022, CONUS, folded to H3 res 6 by DuckDB.

USACE publishes the 2022 inventory as one zipped GeoPackage per state
(nsi.sec.usace.army.mil/downloads/nsi_2022/nsi_2022_{statefips}.gpkg.zip, 11.8 GB
for the 48 states and DC). Each state is downloaded once (the zips are kept), unzipped
to a temporary GeoPackage, folded by one DuckDB statement (spatial's st_read, the h3
extension's h3_latlng_to_cell, GROUP BY), written as one small parquet per state, and
the GeoPackage deleted. A state whose parquet exists is skipped, so a rerun resumes.

WHAT IS KEPT per res 6 cell (the HRRR film's cells): the structure count, the
residential count, and SUMS of square feet, structure / contents / vehicle value and
the four population columns (under / over 65, 2 am / 2 pm). Sums and counts stay
exact at any coarser res (h3_cell_to_parent, re-sum), and every average is sum /
count. A finer res means refolding from the zips (RES, then delete the output dir).

Alaska, Hawaii and Puerto Rico are left out: HRRR, the weather this joins, is CONUS.

    uv run nsi_bulk.py           # every CONUS state
    uv run nsi_bulk.py 11 44     # just these state FIPS
"""

import subprocess
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import duckdb

URL = "https://nsi.sec.usace.army.mil/downloads/nsi_2022/nsi_2022_{}.gpkg.zip"
CONUS = (
    "01 04 05 06 08 09 10 11 12 13 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 "
    "33 34 35 36 37 38 39 40 41 42 44 45 46 47 48 49 50 51 53 54 55 56"
).split()
RES = 6
DOWNLOADS = 4  # concurrent zip downloads; the fold runs one state at a time
ROOT = Path(__file__).parent / "data" / "nsi_2022"
ZIPS, OUT, TMP = ROOT / "zip", ROOT / f"h3r{RES}", ROOT / "tmp"


def download(fips):
    """curl to a .part file, renamed on success, so a killed run never leaves a short zip."""
    dst = ZIPS / f"nsi_2022_{fips}.gpkg.zip"
    if not dst.exists():
        part = dst.with_suffix(".part")
        subprocess.run(["curl", "-sSf", "--retry", "5", "-o", str(part), URL.format(fips)], check=True)
        part.replace(dst)
    return fips, dst


def fold(con, fips, zpath):
    out = OUT / f"{fips}.parquet"
    with zipfile.ZipFile(zpath) as z:
        name = next(n for n in z.namelist() if n.endswith(".gpkg"))
        z.extract(name, TMP)
    gpkg = TMP / name
    try:
        con.sql(f"""
            COPY (
                SELECT h3_latlng_to_cell(y, x, {RES})::UBIGINT AS hex,
                       count(*) AS n,
                       count(*) FILTER (WHERE st_damcat = 'RES') AS n_res,
                       sum(sqft) AS sqft,
                       sum(val_struct) AS val_struct, sum(val_cont) AS val_cont, sum(val_vehic) AS val_vehic,
                       sum(pop2amu65) AS pop2amu65, sum(pop2amo65) AS pop2amo65,
                       sum(pop2pmu65) AS pop2pmu65, sum(pop2pmo65) AS pop2pmo65
                FROM st_read('{gpkg}')
                GROUP BY hex ORDER BY hex
            ) TO '{out.with_suffix(".tmp")}' (FORMAT parquet)
        """)
        out.with_suffix(".tmp").replace(out)
    finally:
        gpkg.unlink(missing_ok=True)
    return con.sql(f"SELECT sum(n), count(*) FROM '{out}'").fetchone()


def main():
    states = sys.argv[1:] or CONUS
    for d in (ZIPS, OUT, TMP):
        d.mkdir(parents=True, exist_ok=True)
    todo = [s for s in states if not (OUT / f"{s}.parquet").exists()]
    con = duckdb.connect()
    con.sql("INSTALL h3 FROM community; LOAD h3; INSTALL spatial; LOAD spatial;")
    t0, total = time.perf_counter(), 0
    print(f"{len(states) - len(todo)} of {len(states)} states already folded", flush=True)
    with ThreadPoolExecutor(DOWNLOADS) as pool:
        for k, (fips, zpath) in enumerate(pool.map(download, todo), 1):
            t = time.perf_counter()
            n, cells = fold(con, fips, zpath)
            total += n
            print(f"state {k}/{len(todo)} · {fips} · {n:,} structures · {cells:,} cells · "
                  f"fold {time.perf_counter() - t:.0f}s · {total:,} so far · {(time.perf_counter() - t0) / 60:.1f} min",
                  flush=True)
    print(f"DONE · {total:,} structures in {len(todo)} states · {(time.perf_counter() - t0) / 60:.1f} min", flush=True)


if __name__ == "__main__":
    main()
