#!/usr/bin/env python3
"""
build_map.py — Generate a self-contained HTML sampling-location map for the
GreenFjord South Greenland campaign.

Reads ``data/sample_locations.csv`` and writes a single interactive Leaflet
map (``greenfjord_sample_map.html``) styled to resemble the reference figure
in ``docs/static_map_example.png``. The map is intended primarily as a
publication figure (screenshot / print at high zoom) and secondarily as an
online, pan-/zoom-able map.

Design split (per the brief):
    * Python  -> reads + tidies the data, computes the legend, emits HTML.
    * HTML/CSS -> all styling (marker badges, legend, north arrow, labels).

This module is deliberately standalone and shares no code with
``src/sample_loc_map`` (a different, folium/Google-Sheets project).

Usage:
    python paper_map/build_map.py
    # optional: python paper_map/build_map.py --csv <path> --out <path>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = REPO_ROOT / "data" / "sample_locations.csv"
DEFAULT_OUT = REPO_ROOT / "paper_map" / "greenfjord_sample_map.html"

# Initial map view: the fjord core around Narsaq / Narsarsuaq / Qaqortoq,
# roughly matching the framing of the reference figure. All markers are still
# plotted; zooming out reveals the coastal river-mouth samples further afield.
INITIAL_BOUNDS = [[60.60, -46.55], [61.45, -44.75]]  # [[south, west],[north, east]]

# Category ("Cluster") display metadata. Colours + representative icons come
# from the CSV itself; order here controls the legend order (top -> bottom),
# following the reference figure.
CLUSTER_ORDER = ["Ocean", "Human", "Cryosphere", "Atmosphere", "Land", "Biodiversity"]

CLUSTER_LABELS = {
    "Ocean": "Ocean",
    "Human": "Human",
    "Cryosphere": "Cryosphere",
    "Atmosphere": "Atmosphere",
    "Land": "Land",
    "Biodiversity": "Biodiversity",
}

# Representative legend icon per category (a category may use several icons
# across its samples; this is the one shown in the legend).
CLUSTER_LEGEND_ICON = {
    "Ocean": "ship",
    "Human": "user",
    "Cryosphere": "snowflake",
    "Atmosphere": "wind",
    "Land": "droplet",
    "Biodiversity": "fish",
}

# Place-name labels (not in the CSV; standard settlements in the study area).
PLACE_LABELS = [
    {"name": "Narsarsuaq", "lat": 61.155, "lon": -45.425},
    {"name": "Narsaq", "lat": 60.913, "lon": -46.048},
    {"name": "Qaqortoq", "lat": 60.718, "lon": -46.037},
    {"name": "Igaliku", "lat": 60.990, "lon": -45.424},
]

# Glacier context boxes. These are NOT in the CSV; they are approximate
# highlight rectangles matching the reference figure and are meant to be
# hand-tuned. Coordinates: [[south, west], [north, east]].
GLACIER_BOXES = [
    {
        "label": "Ocean-terminating glacier",
        "bounds": [[61.28, -45.90], [61.40, -45.55]],
        "color": "#1b7a6b",
    },
    {
        "label": "Land-terminating glacier",
        "bounds": [[60.94, -45.10], [61.06, -44.70]],
        "color": "#c9772f",
    },
]

# --------------------------------------------------------------------------
# Data loading / tidying
# --------------------------------------------------------------------------


def load_samples(csv_path: Path) -> pd.DataFrame:
    """Load and tidy the sample-location table."""
    df = pd.read_csv(csv_path)

    # Fix the known misspelling so it matches CLUSTER_ORDER.
    df["Cluster"] = df["Cluster"].replace({"Cryoshpere": "Cryosphere"})

    # Coerce coordinates and drop anything unplottable.
    df["Lat"] = pd.to_numeric(df["Lat"], errors="coerce")
    df["Lon"] = pd.to_numeric(df["Lon"], errors="coerce")
    df = df.dropna(subset=["Lat", "Lon"]).copy()

    # Tidy string fields used in tooltips / styling.
    for col in ("Location", "Type", "icon", "text_color", "background_color", "Year"):
        df[col] = df[col].fillna("").astype(str).str.strip()

    return df


def build_features(df: pd.DataFrame) -> list[dict]:
    """Convert rows to lightweight dicts for embedding as JSON."""
    features = []
    for _, r in df.iterrows():
        features.append(
            {
                "lat": round(float(r["Lat"]), 6),
                "lon": round(float(r["Lon"]), 6),
                "cluster": r["Cluster"],
                "icon": r["icon"] or "circle",
                "fg": r["text_color"] or "black",
                "bg": r["background_color"] or "#888888",
                # Short label shown on hover (kept minimal per the brief:
                # "markers + legend only", no rich popups).
                "type": r["Type"],
                "loc": r["Location"],
                "year": r["Year"],
            }
        )
    return features


def build_legend(df: pd.DataFrame) -> list[dict]:
    """One legend entry per category, coloured from the data."""
    legend = []
    for cluster in CLUSTER_ORDER:
        sub = df[df["Cluster"] == cluster]
        if sub.empty:
            continue
        # Dominant background colour for the category.
        bg = sub["background_color"].mode().iloc[0] or "#888888"
        fg = sub["text_color"].mode().iloc[0] or "black"
        legend.append(
            {
                "cluster": cluster,
                "label": CLUSTER_LABELS.get(cluster, cluster),
                "icon": CLUSTER_LEGEND_ICON.get(cluster, "circle"),
                "bg": bg,
                "fg": fg,
                "count": int(len(sub)),
            }
        )
    return legend


# --------------------------------------------------------------------------
# HTML generation
# --------------------------------------------------------------------------

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>GreenFjord — Sampling Locations, South Greenland</title>

<link rel="stylesheet"
      href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
      integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY="
      crossorigin="" />
<link rel="stylesheet"
      href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.2/css/all.min.css" />

<style>
  :root {
    --panel-bg: rgba(255, 255, 255, 0.92);
    --panel-border: #cfd6dd;
    --ink: #1c2733;
    --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  }
  html, body { margin: 0; height: 100%; font-family: var(--font); color: var(--ink); }
  #map { position: absolute; inset: 0; background: #dfeaf2; }

  /* ---- Sample marker badges (styled entirely in CSS) ---- */
  .sample-badge {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 22px;
    height: 22px;
    border-radius: 50%;
    border: 1.5px solid rgba(255, 255, 255, 0.9);
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.45);
    font-size: 11px;
    line-height: 1;
    transition: transform 0.08s ease;
  }
  .sample-badge:hover { transform: scale(1.35); z-index: 10000; }

  /* ---- Place-name labels ---- */
  .place-label {
    background: transparent;
    border: none;
    box-shadow: none;
    font-weight: 700;
    font-size: 14px;
    color: #14324f;
    text-shadow:
      -1.5px -1.5px 0 #fff, 1.5px -1.5px 0 #fff,
      -1.5px 1.5px 0 #fff, 1.5px 1.5px 0 #fff,
      0 0 4px #fff;
    white-space: nowrap;
  }
  .place-dot {
    width: 7px; height: 7px; border-radius: 50%;
    background: #14324f; border: 1.5px solid #fff;
    box-shadow: 0 0 3px rgba(0,0,0,0.5);
  }

  /* ---- Glacier context box labels ---- */
  .glacier-label {
    background: transparent; border: none; box-shadow: none;
    font-weight: 800; font-size: 13px; line-height: 1.15;
    text-shadow:
      -1.5px -1.5px 0 #fff, 1.5px -1.5px 0 #fff,
      -1.5px 1.5px 0 #fff, 1.5px 1.5px 0 #fff, 0 0 4px #fff;
    white-space: nowrap;
  }

  /* ---- Legend ---- */
  .legend {
    background: var(--panel-bg);
    border: 1px solid var(--panel-border);
    border-radius: 8px;
    padding: 10px 12px;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.18);
    line-height: 1.5;
    backdrop-filter: blur(2px);
  }
  .legend h4 {
    margin: 0 0 8px;
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: #55616d;
  }
  .legend-row { display: flex; align-items: center; gap: 9px; font-size: 13.5px; }
  .legend-row + .legend-row { margin-top: 5px; }
  .legend-badge {
    display: flex; align-items: center; justify-content: center;
    width: 20px; height: 20px; border-radius: 50%;
    border: 1.5px solid rgba(255, 255, 255, 0.9);
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.35);
    font-size: 10px; flex: 0 0 auto;
  }
  .legend-count { color: #8a949e; font-size: 11.5px; margin-left: auto; padding-left: 8px; }

  /* ---- North arrow ---- */
  .north-arrow {
    background: var(--panel-bg);
    border: 1px solid var(--panel-border);
    border-radius: 8px;
    padding: 6px 8px 4px;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.18);
    text-align: center;
    width: 46px;
  }
  .north-arrow .arrow { font-size: 22px; color: var(--ink); line-height: 1; }
  .north-arrow .n { font-weight: 800; font-size: 12px; letter-spacing: 0.05em; }

  /* ---- Title card ---- */
  .title-card {
    background: var(--panel-bg);
    border: 1px solid var(--panel-border);
    border-radius: 8px;
    padding: 8px 12px;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.18);
    max-width: 300px;
  }
  .title-card h1 { margin: 0; font-size: 15px; }
  .title-card p { margin: 3px 0 0; font-size: 11.5px; color: #55616d; }
</style>
</head>
<body>
<div id="map"></div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
        integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo="
        crossorigin=""></script>
<script>
// ------------------------------------------------------------------
// Data injected by build_map.py
// ------------------------------------------------------------------
const SAMPLES = __SAMPLES__;
const LEGEND = __LEGEND__;
const PLACES = __PLACES__;
const GLACIER_BOXES = __GLACIER_BOXES__;
const INITIAL_BOUNDS = __INITIAL_BOUNDS__;

// ------------------------------------------------------------------
// Base layers
// ------------------------------------------------------------------
const terrain = L.tileLayer(
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
  {
    maxZoom: 18,
    attribution:
      "Tiles &copy; Esri — Esri, DeLorme, NAVTEQ, USGS, Intermap, iPC, NRCAN, " +
      "Esri Japan, METI, Esri China (Hong Kong), Esri (Thailand), TomTom",
  }
);
const opentopo = L.tileLayer(
  "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
  {
    maxZoom: 17,
    attribution:
      'Map data: &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, ' +
      'SRTM | Style: &copy; <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA)',
  }
);
const satellite = L.tileLayer(
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
  {
    maxZoom: 18,
    attribution: "Imagery &copy; Esri, Maxar, Earthstar Geographics",
  }
);

// Explicit centre/zoom so the view is always sane even before the container
// has been laid out (some embed/preview contexts report a 0x0 window at load,
// which would otherwise collapse fitBounds to max zoom).
const INITIAL_CENTER = [
  (INITIAL_BOUNDS[0][0] + INITIAL_BOUNDS[1][0]) / 2,
  (INITIAL_BOUNDS[0][1] + INITIAL_BOUNDS[1][1]) / 2,
];
const map = L.map("map", {
  center: INITIAL_CENTER,
  zoom: 9,
  layers: [terrain],
  zoomControl: true,
  scrollWheelZoom: true,
});

// Dedicated pane so place-names / glacier labels always sit above the sample
// markers (which Leaflet auto-orders by latitude and would otherwise cover them).
map.createPane("labels");
map.getPane("labels").style.zIndex = 650;
map.getPane("labels").style.pointerEvents = "none";

// Re-fit to the study area once the container genuinely has pixels. A
// ResizeObserver handles late layout robustly across browsers/embeds; we only
// auto-fit until the user first interacts with the map.
let userMoved = false;
map.on("zoomstart movestart", function () { userMoved = true; });
function fitStudyArea() {
  if (userMoved) return;
  const s = map.getSize();
  if (s.x < 50 || s.y < 50) return;      // container not ready yet
  map.invalidateSize();
  map.fitBounds(INITIAL_BOUNDS, { padding: [20, 20] });
}
if (window.ResizeObserver) {
  new ResizeObserver(fitStudyArea).observe(document.getElementById("map"));
}
window.addEventListener("load", fitStudyArea);
map.whenReady(fitStudyArea);

L.control.layers(
  { "Terrain (Esri)": terrain, "Topographic (OpenTopoMap)": opentopo, "Satellite (Esri)": satellite },
  {},
  { position: "topright", collapsed: true }
).addTo(map);

// ------------------------------------------------------------------
// Sample markers  (one styled div-icon badge per row)
// ------------------------------------------------------------------
function badgeIcon(s) {
  const html =
    '<div class="sample-badge" style="background:' + s.bg + ';color:' + s.fg + ';">' +
    '<i class="fa-solid fa-' + s.icon + '"></i></div>';
  return L.divIcon({
    html: html,
    className: "",           // strip default leaflet styling
    iconSize: [22, 22],
    iconAnchor: [11, 11],
  });
}

const sampleLayer = L.layerGroup();
SAMPLES.forEach(function (s) {
  const bits = [s.loc, s.type, s.year].filter(Boolean);
  const tip = bits.join(" · ");
  const m = L.marker([s.lat, s.lon], { icon: badgeIcon(s), keyboard: false });
  if (tip) m.bindTooltip(tip, { direction: "top", offset: [0, -10] });
  m.addTo(sampleLayer);
});
sampleLayer.addTo(map);

// ------------------------------------------------------------------
// Glacier context boxes
// ------------------------------------------------------------------
GLACIER_BOXES.forEach(function (b) {
  L.rectangle(b.bounds, {
    color: b.color,
    weight: 2.5,
    fill: false,
    dashArray: null,
  }).addTo(map);

  // Label anchored at the NE corner of the box.
  const ne = L.latLngBounds(b.bounds).getNorthEast();
  L.marker(ne, {
    interactive: false,
    pane: "labels",
    icon: L.divIcon({
      className: "glacier-label",
      html: '<span style="color:' + b.color + '">' + b.label.replace(/ /g, "<br>") + "</span>",
      iconSize: [130, 40],
      iconAnchor: [-6, 10],
    }),
  }).addTo(map);
});

// ------------------------------------------------------------------
// Place-name labels
// ------------------------------------------------------------------
PLACES.forEach(function (p) {
  L.marker([p.lat, p.lon], {
    interactive: false,
    pane: "labels",
    icon: L.divIcon({ className: "", html: '<div class="place-dot"></div>', iconSize: [7, 7], iconAnchor: [3, 3] }),
  }).addTo(map);
  L.marker([p.lat, p.lon], {
    interactive: false,
    pane: "labels",
    icon: L.divIcon({
      className: "place-label",
      html: p.name,
      iconSize: [120, 18],
      iconAnchor: [-6, 14],
    }),
  }).addTo(map);
});

// ------------------------------------------------------------------
// Legend (bottom-left)
// ------------------------------------------------------------------
const legend = L.control({ position: "bottomleft" });
legend.onAdd = function () {
  const div = L.DomUtil.create("div", "legend");
  let html = "<h4>Research theme</h4>";
  LEGEND.forEach(function (e) {
    html +=
      '<div class="legend-row">' +
      '<span class="legend-badge" style="background:' + e.bg + ';color:' + e.fg + ';">' +
      '<i class="fa-solid fa-' + e.icon + '"></i></span>' +
      "<span>" + e.label + "</span>" +
      '<span class="legend-count">' + e.count + "</span>" +
      "</div>";
  });
  return div.innerHTML = html, div;
};
legend.addTo(map);

// ------------------------------------------------------------------
// North arrow (bottom-right)
// ------------------------------------------------------------------
const north = L.control({ position: "bottomright" });
north.onAdd = function () {
  const div = L.DomUtil.create("div", "north-arrow");
  div.innerHTML = '<div class="arrow"><i class="fa-solid fa-location-arrow" style="transform:rotate(-45deg)"></i></div><div class="n">N</div>';
  return div;
};
north.addTo(map);

// ------------------------------------------------------------------
// Title card (top-left)
// ------------------------------------------------------------------
const title = L.control({ position: "topleft" });
title.onAdd = function () {
  const div = L.DomUtil.create("div", "title-card");
  div.innerHTML =
    "<h1>GreenFjord sampling locations</h1>" +
    "<p>South Greenland &middot; " + SAMPLES.length + " samples across " + LEGEND.length + " research themes</p>";
  L.DomEvent.disableClickPropagation(div);
  return div;
};
title.addTo(map);
</script>
</body>
</html>
"""


def render_html(features, legend, places, glacier_boxes, bounds) -> str:
    def dump(obj):
        return json.dumps(obj, ensure_ascii=False)

    return (
        HTML_TEMPLATE
        .replace("__SAMPLES__", dump(features))
        .replace("__LEGEND__", dump(legend))
        .replace("__PLACES__", dump(places))
        .replace("__GLACIER_BOXES__", dump(glacier_boxes))
        .replace("__INITIAL_BOUNDS__", dump(bounds))
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    df = load_samples(args.csv)
    features = build_features(df)
    legend = build_legend(df)

    html = render_html(features, legend, PLACE_LABELS, GLACIER_BOXES, INITIAL_BOUNDS)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8")

    print(f"Wrote {args.out}  ({len(features)} samples, {len(legend)} themes)")
    for e in legend:
        print(f"  {e['cluster']:<13} {e['count']:>3}  {e['bg']}")


if __name__ == "__main__":
    main()
