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
| `build_map.py` | Python generator: reads the CSV, tidies it, emits the HTML. |
| `greenfjord_sample_map.html` | The generated map (self-contained, open directly in a browser). |
| `BUILD_NOTES.md` | This file. |

## How to build

```bash
uv run python paper_map/build_map.py
# options: --csv <path>  --out <path>
```

Then open `paper_map/greenfjord_sample_map.html` in any browser (needs an
internet connection for the map tiles + icon/library CDNs).

## Data

Source: [`data/sample_locations.csv`](../data/sample_locations.csv) (466
plottable rows). Columns used:

- `Cluster` — research theme; drives the legend grouping. (The CSV's
  `Cryoshpere` spelling is corrected to `Cryosphere` on load.)
- `Lat`, `Lon` — marker position.
- `icon` — Font Awesome (v6 solid) icon name, rendered inside the badge.
- `background_color`, `text_color` — badge fill / icon colour, taken verbatim
  from the data (authoritative — richer than the reference figure's palette).
- `Location`, `Type`, `Year` — shown in a small hover tooltip only.

Theme counts: Biodiversity 141, Land 138, Cryosphere 74, Ocean 57,
Atmosphere 47, Human 9.

## Design decisions

- **Tech:** Leaflet + web map tiles (chosen over a fully static, image-embedded
  build). Interactive online; screenshot for the paper.
- **Python vs HTML/CSS split:** Python does data loading/tidying and template
  filling; all styling (marker badges, legend, north arrow, labels, panels)
  lives in the HTML `<style>` block.
- **Markers + legend only** (no rich click popups), per the brief — a light
  hover tooltip is the only per-marker detail, keeping it clean as a figure.
- **Basemap:** default is **Esri World Topo** (reliable, good fjord/terrain
  look). A layer switcher (top-right) also offers **OpenTopoMap**
  (tan/contour look closest to the reference, but rate-limited) and **Esri
  Satellite**.
- **Standalone:** shares no code with `src/sample_loc_map/` (the folium /
  Google-Sheets project), as requested.

## Annotations NOT in the CSV (approximate — edit in `build_map.py`)

These reproduce the reference figure and are hand-placed constants at the top
of `build_map.py`; adjust the coordinates there as needed:

- `PLACE_LABELS` — Narsarsuaq, Narsaq, Qaqortoq, Igaliku (standard settlements).
- `GLACIER_BOXES` — the "Ocean-terminating glacier" / "Land-terminating
  glacier" highlight rectangles + labels. Positions are approximate.
- `INITIAL_BOUNDS` — the default fjord-core view (all markers are still
  plotted; zooming out reveals the ~88 coastal river-mouth samples further
  afield).

## Deliberately omitted

- The reference figure's dark **"Ocean" transect line** connecting CTD casts.
  The CSV has no explicit cast ordering, so drawing a route would mean
  inventing one — left out rather than misrepresenting the survey track.

## Known quirks / gotchas

- The map is given an explicit `center`/`zoom` and only refits to
  `INITIAL_BOUNDS` once its container reports a real size (via a
  `ResizeObserver`). This guards against embed/preview contexts that report a
  0×0 window at load, which would otherwise collapse `fitBounds` to max zoom.
- Requires network access at view time (tiles + Leaflet/Font Awesome CDNs). For
  a guaranteed-offline paper build, the next step would be to embed a fetched
  basemap image + bundle the assets locally.

## Changelog

- 2026-07-13 — Initial build: Leaflet map, CSS-styled theme badges, legend,
  place labels, north arrow, glacier boxes, title card; label pane fix so text
  sits above markers; robust initial-fit logic.
