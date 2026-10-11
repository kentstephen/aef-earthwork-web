# archive

The USACE National Structure Inventory work this repo started with, before it became the Earthwork map.
Both scripts read and write `../data/` (gitignored).

- `nsi_bulk.py`: the 2022 inventory for CONUS from USACE's per-state GeoPackages, folded to H3 res 6
  with DuckDB (counts and sums per cell). This one works: 122,382,941 structures, 171,686 cells.
- `nsi_build.py`: the earlier route through the NSI API (`nsiapi/structures`). It does not run as is
  (its batch status update fails to bind), and the bulk files made it unnecessary.
