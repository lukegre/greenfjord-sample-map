# GreenFjord sampling-location map — build notes

This document describes the current map generator, its configuration, and the
steps required to rebuild and validate the published artifact.

## What the project produces

The project generates one interactive Leaflet page for the GreenFjord South
Greenland sampling campaign:

- source data: `data/sample_locations_merged.csv`;
- author configuration: `src/sample_loc_map/config.yml`;
- generator: `src/sample_loc_map/build_map.py`; and
- generated and published artifact: `docs/index.html`.

The HTML contains the cleaned sample data, map configuration, styles, and
browser-side map logic. It still loads map tiles, Leaflet, Leaflet.markercluster,
and Font Awesome over the network when viewed.

The map is useful as an online explorer and as a publication figure that can be
framed in a browser and exported or captured at high resolution.

## Build

Run commands from the repository root:

```bash
uv sync
uv run sample-loc-map
```

The CLI defaults are equivalent to:

```bash
uv run python src/sample_loc_map/build_map.py \
  --csv data/sample_locations_merged.csv \
  --config src/sample_loc_map/config.yml \
  --out docs/index.html
```

All three paths can be overridden:

```bash
uv run sample-loc-map \
  --csv path/to/samples.csv \
  --config path/to/config.yml \
  --out path/to/map.html
```

To rebuild without touching the tracked artifact:

```bash
uv run sample-loc-map --out /tmp/greenfjord-index.html
cmp /tmp/greenfjord-index.html docs/index.html
```

The build may download the SVG configured under `logo.url`, recolor it, and
embed it as a data URI. If that request fails, the build emits a warning and
uses the original remote URL instead. Consequently, an offline build can
differ from an online build without failing.

## Validation

There is currently no automated test suite or configured linter. The available
checks are:

```bash
uv run python -m compileall -q src/sample_loc_map
uv run sample-loc-map --out /tmp/greenfjord-index.html
cmp /tmp/greenfjord-index.html docs/index.html
```

The normal build currently reports 579 samples, six themes, one sample subtype
disabled by default, and two configured cruise lines. For visual or interaction
changes, also open `docs/index.html` in a browser and test the affected behavior
at desktop and narrow viewport sizes.

## Data flow

1. `load_samples()` reads the merged CSV.
2. The misspelled cluster value `Cryoshpere` is normalized to `Cryosphere`.
3. `Lat` and `Lon` are coerced to numbers; rows without valid coordinates are
   removed.
4. Expected metadata columns are trimmed and missing optional columns are
   supplied as empty strings.
5. Years such as `2023.0` are rendered as `2023`.
6. Python creates sample feature records, theme legend entries, disabled
   subtype keys, and configured or calculated cruise tracks.
7. `render_html()` injects JSON into `HTML_TEMPLATE`.
8. Browser-side Leaflet code builds the map, clusters, filters, overlays, and
   controls.

The essential CSV columns are `Cluster`, `Lat`, and `Lon`. The current popup
and filtering behavior also recognizes:

- `Group`
- `Year`
- `Location`
- `Type`
- `Time`
- `Station ID`
- `Link to paper`
- `Link display`
- `icon`
- `Extra`

The other CSV files in `data/` are source or reference datasets. No checked-in
script recreates `data/sample_locations_merged.csv`, so treat the merged file
as a curated build input.

## Map behavior

- **Sample badges:** theme colors and text colors come from `config.yml`; the
  icon name comes from each CSV row.
- **Donut clusters:** nearby markers collapse into theme-colored segments with
  a total count in the center.
- **Cluster clicks:** clicks zoom toward the child markers until
  `clusters.spiderfy_from_zoom`; at or above that level, the cluster fans its
  markers out instead.
- **Popups and tooltips:** individual markers show metadata popups; clusters
  summarize their contents.
- **Interactive legend:** themes expand into sample subtypes. Clicking a theme
  or subtype filters markers and recomputes the clusters and displayed counts.
- **Map feature toggles:** cruise tracks, atmospheric sampling, place names,
  and glacier boxes can be toggled from a collapsible legend section that is
  closed by default.
- **Zoom rules:** place names and glacier boxes appear from zoom 9. The
  atmospheric overlay follows its configurable inclusive `min_zoom` and
  `max_zoom` range. Manual state is retained while zooming inside the same
  threshold band.
- **CTD tracks:** configured points are drawn with a halo and optional curve
  smoothing.
- **Map furniture:** the legend is draggable by its heading. The north arrow
  and scale bar share a draggable group. Dragging is clamped to the map bounds.
- **Viewport bounds:** an optional live readout reports the visible map window
  in standard BBOX order: west, south, east, north.
- **Fullscreen:** the button uses browser fullscreen for a top-level page and
  opens the map page directly when iframe fullscreen is unavailable.
- **Logo:** an SVG can be linked, recolored at build time, and positioned from
  any map corner.

## Configuration reference

All author-facing values live in `src/sample_loc_map/config.yml`.

| Key | Controls |
|---|---|
| `title`, `subtitle` | Browser title and optional formatted text. `{n_samples}` and `{n_themes}` are interpolated where supported. |
| `legend` | Heading, feature labels, position, interactivity, theme expansion, and the initial expansion state of the Map features section. |
| `view.center`, `view.zoom` | Initial map center in `[latitude, longitude]` order and Leaflet zoom level. |
| `north_arrow`, `scale_bar` | Visibility, placement, and scale units. The two controls are rendered as one draggable group. |
| `bbox_display` | Visibility, Leaflet corner, and decimal precision of the live viewport BBOX (WSEN) readout. |
| `logo` | Visibility, source URL, link, height, color/background, padding, and position. |
| `z_order` | Pane ordering for boxes, samples, labels, and tooltips. |
| `basemaps` | Ordered tile layers. The first entry is displayed initially. |
| `themes.<Cluster>` | Theme color, text color, icon, and label. YAML order controls legend and donut-segment order. |
| `markers.size` | Individual sample-badge diameter. |
| `clusters.spiderfy_from_zoom` | Zoom level at which cluster clicks switch from zooming to spiderfying. |
| `filters.exclude_types` | Subtypes that begin disabled in the interactive legend. Samples remain available and can be re-enabled. |
| `towns` | Hand-placed town dots and labels. |
| `box_defaults`, `boxes` | Glacier rectangle geometry, outlines, fill, rounded corners, shadows, and labels. |
| `atmosphere_blob` | Center, radius, opacity, color, and inclusive visible zoom range (`min_zoom`/`max_zoom`; `null` removes a limit). |
| `cruise_lines` | Track styling, smoothing, aggregation bins, fjord prefixes, and editable track points. |

Coordinate conventions are intentionally different:

- glacier-box bounds use `[west, south, east, north]`;
- the initial view center uses `[latitude, longitude]`;
- towns use named `lat` and `lon` fields; and
- atmospheric centers and cruise-track points use `[lat, lon]`.

## Cruise tracks

Each fjord normally uses the hand-editable points under:

```yaml
cruise_lines:
  fjords:
    - name: ...
      station_prefix: ...
      track:
        - [lat, lon]
```

If a configured `track` is empty, the normal build calculates a fallback from
Ocean CTD stations. It selects stations by the leading `Station ID` prefix,
drops rows whose location is `Faulty location`, projects coordinates into local
meters, finds the principal fjord axis, bins points along that axis, and joins
the bin means.

To deliberately replace the configured points with recalculated tracks:

```bash
uv run sample-loc-map --extract-tracks
```

This is a mutating maintenance command: it writes to `config.yml` and exits
without generating the HTML. Review its diff before rebuilding.

## Approximate or author-supplied geography

The following values are not derived from the sample CSV and should be reviewed
as authored map content:

- `view.center` and `view.zoom`
- `towns`
- `boxes`
- `atmosphere_blob`
- fjord assignments and `cruise_lines.fjords[].track`

## Generated artifact and deployment

`docs/index.html` is tracked because it is the GitHub Pages entry point. Do not
edit its embedded JSON, CSS, or JavaScript by hand. Change the CSV, YAML, or
generator first, then run:

```bash
uv run sample-loc-map
```

On pushes to `main`, `.github/workflows/run-sample-loc.yml` runs the same CLI,
commits `docs/index.html` if it changed, and publishes `docs/` to the
`gh-pages` branch.

## Known implementation details

- Leaflet reapplies fill properties to vector paths after redraws. The
  atmospheric gradient is therefore pinned with `!important` CSS on
  `.atmo-blob`.
- A `ResizeObserver` refreshes Leaflet's viewport dimensions when an embedded
  map container changes size without altering the configured center or zoom.
- Town labels are laid out in the browser to reduce overlap.
- Map tiles and CDN assets require network access at viewing time. A fully
  offline build would need locally bundled JavaScript/CSS/icons and an offline
  basemap.

## Changelog

- **2026-07-13:** Added the Leaflet generator, themed markers, legend, place
  labels, north arrow, glacier boxes, configuration-driven styling, donut
  clusters, CTD tracks, atmospheric overlay, editable tracks, interactive
  filters, and configurable basemaps.
- **2026-07-15:** Added fullscreen behavior, draggable legend positioning,
  map screenshot documentation, and north-arrow positioning.
- **2026-07-20:** Grouped the north arrow and scale bar into one draggable
  control, added configurable cluster spiderfying, and added the optional live
  viewport BBOX (WSEN) readout. Switched the initial view configuration from a
  fitted BBOX to an explicit center and zoom. Made the Map features legend
  section collapsible and closed by default. Exposed the atmospheric overlay's
  minimum and maximum visible zoom levels in YAML.
