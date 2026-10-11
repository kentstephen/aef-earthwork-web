# aef-earthwork-web

**Open it: https://kentstephen.github.io/aef-earthwork-web/** (Chrome, Edge, Firefox or Safari with WebGL2).

The Earthwork map from [aef-earthwork](https://github.com/kentstephen/aef-earthwork) (first named
aef-elevation), running entirely in the browser with no kernel and no server.

Earthwork is the chance the ground itself was dug, filled or graded between two years, from
AlphaEarth, by a model taught where USGS 3DEP lidar flew the same ground twice.

## What runs where

- **The map's interface** (`web/src/ui.js`, `ui.css`) is the notebook's widget carried over: search,
  the Earthwork panel, the imagery card, hold space, the pair, the keys.
- **Earthwork, per 10 m pixel, on the GPU** (`web/src/earthwork.ts`, `earthwork-score.ts`). Development
  Seed's [deck.gl-raster](https://github.com/developmentseed/deck.gl-raster) `ZarrLayer` reads both years
  of the AlphaEarth GeoZarr mosaic (`tge-labs/aef-mosaic` on Source Cooperative) per chunk into `r8sint`
  texture arrays; a shader module dequantizes, unit-normalizes, applies the logistic regression on
  `[b, a, a*b, (a-b)^2]` and the change gate, then viridis. The notebook folded the same score to H3
  hexagons in its kernel. Checked against numpy on a 512 x 512 window at Huntsville AL: correlation 0.998.
- **The tooltip** scores the pixel on the CPU from the chunk already read. **A click** picks the 10 m pixel
  and opens a card in the notebook's form: every Overture division under it, the H3 res 13 string
  and the pixel's center to copy, and its score.
- **Sentinel-2** (`web/src/s2.ts`): Earth Genome's STAC search from the page, each footprint's COG read
  and decoded in workers (`MosaicLayer` + `COGLayer`). True color, or the urban (B12 B11 B4) and
  blue-yellow (B11 B11 B2) false colors from the raw bands, each band stretched to its 98th percentile
  over the view.
- **Place names**: Overture divisions PMTiles, a pinned release with the newest one as the fallback.
- **Models**: `web/public/models/*.json`, the notebook's two logistic regressions: US only (`current.json`, US repeat lidar) and US + abroad (`abroad.json`, US lidar plus truth from outside the US).

The AlphaEarth mosaic has no coarser levels, so earthwork is drawn from zoom 12 in.

```sh
cd web
npm install
npm run dev
```

## Also here

`archive/`: the USACE National Structure Inventory scripts this repo started with.

## Credits

AlphaEarth Foundations Satellite Embedding dataset by Google and Google DeepMind (CC BY 4.0).
Sentinel-2 mosaics by Earth Genome (CC BY 4.0). Place names from Overture Maps (ODbL). Search by
Photon over OpenStreetMap (ODbL). Basemap by CARTO, OpenStreetMap contributors.
