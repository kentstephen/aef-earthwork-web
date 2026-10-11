# aef-elevation-web

**Open it: https://kentstephen.github.io/aef-elevation-web/** (Chrome, Edge, Firefox or Safari with WebGL2).

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
- **The tooltip and the click's card** score the pixel on the CPU from the chunk already read.
- **Sentinel-2** (`web/src/s2.ts`): Earth Genome's STAC search from the page, each footprint's COG read
  and decoded in workers (`MosaicLayer` + `COGLayer`).
- **Place names**: Overture divisions PMTiles, a pinned release with the newest one as the fallback.
- **Models**: `web/public/models/*.json`, the notebook's two logistic regressions: Current (US repeat lidar) and Global (`abroad.json`: US lidar plus truth from outside the US).

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
