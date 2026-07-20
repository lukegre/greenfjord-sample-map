# Repository guide for agents

## What this repository does

This repository generates the static, interactive GreenFjord sampling map
published from `docs/`. The current implementation is a Python generator that
embeds data and configuration into a hand-written Leaflet HTML/CSS/JavaScript
template.

The active implementation does **not** use Folium or fetch its sample data from
Google Sheets, despite the older wording in `README.md`. Use the code and paths
below as the source of truth.

## Source of truth

- `src/sample_loc_map/build_map.py`: data cleanup, feature construction, cruise
  track aggregation, the entire HTML/CSS/JavaScript template, and the CLI.
- `src/sample_loc_map/config.yml`: author-editable map appearance and content,
  including themes, basemaps, legend, overlays, towns, glacier boxes, tracks,
  logo, and initial visibility.
- `data/sample_locations_merged.csv`: canonical build input. It currently has
  579 rows across six research themes.
- `docs/index.html`: tracked generated artifact and GitHub Pages entry point.
- `.github/workflows/run-sample-loc.yml`: rebuilds the map on pushes to `main`,
  commits `docs/index.html` when changed, and deploys `docs/`.
- `pyproject.toml` and `uv.lock`: Python 3.11 environment and locked
  dependencies. Use `uv`; do not introduce a second environment workflow.

The other CSVs are source/reference datasets. There is no checked-in script
that recreates `data/sample_locations_merged.csv`, so do not assume it can be
regenerated automatically.

## Build and validation

Run commands from the repository root:

```bash
uv sync
uv run sample-loc-map
```

The second command rebuilds `docs/index.html`. Equivalent explicit usage is:

```bash
uv run python src/sample_loc_map/build_map.py \
  --csv data/sample_locations_merged.csv \
  --config src/sample_loc_map/config.yml \
  --out docs/index.html
```

Useful validation:

```bash
uv run python -m compileall -q src/sample_loc_map
uv run sample-loc-map --out /tmp/greenfjord-index.html
cmp /tmp/greenfjord-index.html docs/index.html
```

There is currently no test suite and no configured linter. Do not report
`ruff`, `pytest`, or another check as passing unless you first add/configure it
or verify it is available.

The build tries to download and recolor the configured SVG logo. A failed
download is non-fatal: the generator warns and leaves the remote logo URL in
the output. That fallback can make generated HTML differ between online and
offline builds. Viewing the map also needs network access for tiles and CDN
assets.

## Data and rendering flow

1. `load_samples()` reads the merged CSV, fixes the `Cryoshpere` spelling,
   coerces coordinates, fills expected optional columns, and normalizes years.
2. `config.yml` supplies the theme palette, labels, map furniture, overlay
   settings, hand-placed geography, and cruise-track points.
3. Python turns rows into JSON-friendly sample features and legend entries.
4. Configured cruise tracks are preferred; PCA/binning of Ocean CTD stations
   is only the fallback when a configured track is empty.
5. `render_html()` injects JSON into `HTML_TEMPLATE`.
6. Browser-side Leaflet code creates markers, donut clusters, filters,
   overlays, draggable controls, zoom-dependent labels/boxes, and fullscreen
   behavior.

Bounding boxes in YAML use `[west, south, east, north]`. Marker and town
coordinates use latitude/longitude fields or `[lat, lon]`. Preserve this
distinction.

## Editing guidance

- Prefer `config.yml` for content, colors, positions, visibility, and styling
  already represented there. Change Python/JavaScript only for behavior or new
  configuration capabilities.
- Keep CSV column compatibility with `load_samples()` and `build_features()`.
  The essential fields are `Cluster`, `Lat`, and `Lon`; the generator supplies
  empty strings for its known optional metadata fields.
- Theme names connect CSV rows, YAML keys, legend state, and cluster colors.
  Preserve those keys or update every consumer.
- Treat `docs/index.html` as generated. Make source changes first, rebuild it,
  and include the regenerated file when output changes.
- Do not hand-edit the large JSON/script payload in `docs/index.html`.
- `--extract-tracks` is a mutating maintenance command: it recalculates tracks
  and writes them into `config.yml`. Do not use it as a routine build or test.
- The HTML is intended both for direct GitHub Pages use and embedding in an
  iframe. Check both implications when changing fullscreen or sizing logic.
- Preserve existing user changes. This checkout may be managed by Jujutsu and
  can be on a detached Git commit, so inspect status before assuming a branch.

## Before handing off a change

1. Inspect `git diff` and confirm no source/reference data changed
   unintentionally.
2. Run Python compilation.
3. Rebuild the map using the project CLI.
4. Confirm the CLI still reports the expected scale (currently 579 samples,
   six themes, and two cruise lines).
5. For visual or interaction changes, open `docs/index.html` in a browser and
   test the affected control at desktop and narrow viewport sizes.
6. Include `docs/index.html` in the change if rebuilding altered it.

## Known documentation drift

`README.md`, the module docstring/CLI help, and
`src/sample_loc_map/BUILD_NOTES.md` contain stale references to Folium, Google
Sheets, `paper_map/`, `greenfjord_sample_map.html`, and a separate
`src/sample_loc_map` project. The working paths and commands in this file are
verified against the current repository. If touching those documents, update
them toward the current architecture rather than copying the stale wording.
