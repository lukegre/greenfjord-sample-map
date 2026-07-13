# GreenFjord sampling-location map — build notes

A running log of what this deliverable is, the decisions behind it, and how to
regenerate / reuse it.

## What it is

A single self-contained interactive map of the GreenFjord South Greenland
sampling campaign, styled to resemble the reference figure
[`docs/static_map_example.png`](../docs/static_map_example.png).

- **Primary use:** a publication figure — open in a browser, frame the view,
  and screenshot / print at high zoom (or via the browser's PDF export).
- **Secondary use:** an online, pan-/zoom-able map.

Files in this folder:

| File | Role |
|------|------|
| `config.yml` | **Everything the author edits** — text, colours, towns, boxes, blob, cruise lines. |
| `build_map.py` | Python generator: reads the CSV + config, aggregates, emits the HTML. |
| `greenfjord_sample_map.html` | The generated map (open directly in a browser). |
| `BUILD_NOTES.md` | This file. |

## How to build

```bash
uv run python paper_map/build_map.py
# options: --csv <path>  --config <path>  --out <path>
```

Then open `paper_map/greenfjord_sample_map.html` in any browser (needs an
internet connection for the map tiles + JS/icon CDNs).

Python deps (managed via `uv`, in `pyproject.toml`): `pandas`, `numpy`,
`pyyaml`.

## Features

- **Themed sample markers** — colour from `config.yml` (per theme), icon
  *shape* from the CSV `icon` column (a theme can span several sample types).
- **Meaningful aggregation (pie/donut clusters)** — nearby samples collapse
  into a donut whose segments show the per-theme contribution, with the total
  count in the centre. Zooming in expands clusters down to individual badges.
  (Leaflet.markercluster with a custom `iconCreateFunction`.)
- **CTD cruise lines** — marine CTD stations are split into two fjords by the
  leading character of their `Station ID` (`O` → EKAS Fjord / ocean-
  terminating; `L` → Igaliku Fjord / land-terminating), aggregated *by distance
  along each fjord's principal axis* into `bins` nodes, then connected. This
  tames the messy raw casts (repeat occupations, side branches) into one clean
  representative track per fjord.
- **Atmospheric-sampling blob** — a radially-fading disc (configurable centre,
  radius, opacity, colour) rendered as an `L.circle` filled with an SVG radial
  gradient, sitting below the markers.
- **Configurable glacier boxes, named towns, legend, north arrow, title.**

## What lives in `config.yml`

| Key | Controls |
|-----|----------|
| `title`, `subtitle` | Title-card text (`{n_samples}`/`{n_themes}` auto-filled). |
| `legend.title`, `legend.transect_label`, `legend.blob_label` | Legend text. |
| `view.bounds` | Default framing (all markers still plotted). |
| `themes.<Cluster>` | Default icon `color`, `text_color`, `legend_icon`, `label` per research theme; order sets legend + pie-segment order. |
| `towns` | Which towns get a label + dot. |
| `boxes` | Glacier highlight rectangles: `name`, `bounds`, `color`. |
| `atmosphere_blob` | `center`, `radius_km`, `opacity`, `color`. |
| `cruise_lines` | `bins`, `color`, `weight`, `opacity`, and the `fjords` list (`name` + `station_prefix`). |

## Design decisions

- **Tech:** Leaflet + web map tiles. Interactive online; screenshot for the
  paper. Default basemap **Esri World Topo** (reliable); a top-right switcher
  also offers **OpenTopoMap** and **Esri Satellite**.
- **Python vs config vs HTML/CSS split:** Python loads/tidies data and does the
  transect aggregation; `config.yml` holds all author-editable values; all
  styling lives in the HTML `<style>` block.
- **Colours are theme-level (from config), not per-row** — the earlier per-row
  CSV colours are superseded so the palette is edited in one place. The near-
  white CSV `Cryosphere` colour was replaced with a legible light blue.
- **Standalone:** shares no code with `src/sample_loc_map/` (the folium /
  Google-Sheets project), as requested.

## Approximate / author-supplied (not in the CSV)

Edit these in `config.yml`:

- `towns`, `boxes`, `atmosphere_blob`, `view.bounds` — hand-placed.
- Cruise-line aggregation is derived from the CTD data, but the **fjord
  assignment** (which `Station ID` prefix belongs to which fjord) and the
  number of `bins` are config choices. `Location == "Faulty location"` casts
  are dropped.

## Known quirks / gotchas

- **Atmosphere blob fill:** Leaflet re-applies `fill`/`fill-opacity` to vector
  paths on every redraw, which would wipe the gradient. The gradient fill +
  full opacity are therefore pinned with `!important` CSS on `.atmo-blob` so
  they survive zoom/pan.
- **Initial fit:** the map is given an explicit `center`/`zoom` and only refits
  to `view.bounds` once its container reports a real size (via a
  `ResizeObserver`) — guards against embed/preview contexts that report a 0×0
  window at load (which collapses `fitBounds` to max zoom).
- Requires network access at view time (tiles + Leaflet / markercluster / Font
  Awesome CDNs). A guaranteed-offline paper build would mean embedding a
  fetched basemap image + bundling the JS/CSS locally.
- Esri basemap prints its own faint town labels; the styled bold labels from
  `towns` sit on top of them.

## Changelog

- 2026-07-13 — Initial build: Leaflet map, CSS-styled theme badges, legend,
  place labels, north arrow, glacier boxes, title card.
- 2026-07-13 — Config-driven rewrite: all author-editable values moved to
  `config.yml`; added pie/donut cluster aggregation, distance-aggregated CTD
  cruise lines per fjord, and the configurable atmospheric-sampling blob.
