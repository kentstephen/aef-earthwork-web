# nsi-dynamical

Browser experiments on cloud-native Zarr, read straight from the page with no server.

## `web/`: Earthwork in the browser

The chance the ground was dug, filled or graded between two years, scored for every 10 m pixel on
the GPU. A port of the map in [aef-earthwork](https://github.com/kentstephen/aef-earthwork)'s
`earthwork.py`, with the H3 fold replaced by a shader.

- **Data:** the AlphaEarth Foundations GeoZarr mosaic on Source Cooperative
  (`tge-labs/aef-mosaic`: int8, 64 bands, 9 years, 256 x 256 chunks), read with zarrita.
- **Rendering:** Development Seed's [deck.gl-raster](https://github.com/developmentseed/deck.gl-raster)
  (`ZarrLayer`). Each tile uploads both years as `r8sint` texture arrays; a shader module
  (`src/earthwork-score.ts`) dequantizes, unit-normalizes, applies the logistic regression on
  `[b, a, a*b, (a-b)^2]`, gates by `1 - cos`, and hands the score to a viridis colormap.
  Switching models is a uniform change, with no refetch.
- **Models:** `public/models/*.json`, the two logistic regressions from aef-earthwork
  (`current`: US repeat lidar only; `abroad`: truth from outside the 48 states too).
- **Checked:** against numpy on a 512 x 512 window at Huntsville AL, correlation 0.998.
- **Limits:** the mosaic has no overviews, so the layer draws from zoom 12 in.

```sh
cd web
npm install
npm run dev
```

## `nsi_bulk.py`

The USACE National Structure Inventory 2022 for CONUS, folded to H3 res 6 with DuckDB.

## Credits

The AlphaEarth Foundations Satellite Embedding dataset is produced by Google and Google DeepMind
(CC BY 4.0). Basemap by OpenFreeMap, OpenMapTiles, OpenStreetMap contributors.
