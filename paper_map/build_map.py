#!/usr/bin/env python3
"""
build_map.py — Generate a self-contained HTML sampling-location map for the
GreenFjord South Greenland campaign.

Reads ``data/sample_locations.csv`` plus a ``config.yml`` and writes a single
interactive Leaflet map (``greenfjord_sample_map.html``) styled to resemble the
reference figure in ``docs/static_map_example.png``.

Design split (per the brief):
    * Python  -> reads + tidies the data, aggregates the CTD transects, and
                 fills the HTML template.
    * config.yml -> everything the author edits (text, colours, towns, boxes,
                 atmosphere blob, cruise-line settings).
    * HTML/CSS -> all styling (pie clusters, marker badges, legend, north
                 arrow, labels).

Features
    * Themed marker badges (colour from config, icon-shape from the CSV).
    * Meaningful aggregation: nearby samples collapse into pie/donut clusters
      showing the per-theme contribution with a total count in the centre.
    * CTD cruise lines: marine stations aggregated by distance along each
      fjord's principal axis, then connected.
    * A configurable, radially-fading atmospheric-sampling blob.
    * Configurable glacier boxes, named towns, and all static text.

This module is deliberately standalone and shares no code with
``src/sample_loc_map`` (a different, folium/Google-Sheets project).

Usage:
    uv run python paper_map/build_map.py
    # options: --csv <path> --config <path> --out <path>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
DEFAULT_CSV = REPO_ROOT / "data" / "sample_locations.csv"
DEFAULT_CONFIG = HERE / "config.yml"
DEFAULT_OUT = HERE / "greenfjord_sample_map.html"

# CSV "Cluster" spellings that should map onto a canonical theme name.
CLUSTER_ALIASES = {"Cryoshpere": "Cryosphere"}


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def load_config(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_samples(csv_path: Path) -> pd.DataFrame:
    """Load and tidy the sample-location table."""
    df = pd.read_csv(csv_path)
    df["Cluster"] = df["Cluster"].replace(CLUSTER_ALIASES)

    df["Lat"] = pd.to_numeric(df["Lat"], errors="coerce")
    df["Lon"] = pd.to_numeric(df["Lon"], errors="coerce")
    df = df.dropna(subset=["Lat", "Lon"]).copy()

    for col in ("Location", "Type", "icon", "Year", "Station ID"):
        df[col] = df[col].fillna("").astype(str).str.strip()

    return df


# --------------------------------------------------------------------------
# Feature / legend building
# --------------------------------------------------------------------------


def build_features(df: pd.DataFrame, themes: dict) -> list[dict]:
    """One dict per sample. Colours come from the theme config; the icon
    *shape* stays per-row from the CSV (a theme can span several sample types)."""
    features = []
    for _, r in df.iterrows():
        theme = r["Cluster"]
        tcfg = themes.get(theme, {})
        bits = [b for b in (r["Location"], r["Type"], r["Year"]) if b]
        features.append(
            {
                "lat": round(float(r["Lat"]), 6),
                "lon": round(float(r["Lon"]), 6),
                "theme": theme,
                "icon": r["icon"] or "circle",
                "bg": tcfg.get("color", "#888888"),
                "fg": tcfg.get("text_color", "#ffffff"),
                "tip": " · ".join(bits),
            }
        )
    return features


def build_legend(df: pd.DataFrame, themes: dict) -> list[dict]:
    """One legend entry per theme present in the data, in config order."""
    counts = df["Cluster"].value_counts().to_dict()
    legend = []
    for theme, cfg in themes.items():
        if theme not in counts:
            continue
        legend.append(
            {
                "theme": theme,
                "label": cfg.get("label", theme),
                "icon": cfg.get("legend_icon", "circle"),
                "bg": cfg.get("color", "#888888"),
                "fg": cfg.get("text_color", "#ffffff"),
                "count": int(counts[theme]),
            }
        )
    return legend


# --------------------------------------------------------------------------
# CTD cruise-line aggregation
# --------------------------------------------------------------------------


def aggregate_transect(points: np.ndarray, n_bins: int) -> list[list[float]]:
    """Aggregate scattered CTD casts into an ordered set of nodes along the
    fjord's principal axis.

    The stations are messy (repeat occupations across years, side branches),
    so we: project onto the first principal component (the along-fjord axis),
    bin by distance along that axis, average the [lat, lon] within each bin,
    and return the bin means ordered head-to-mouth. Connecting these gives a
    clean representative cruise track.
    """
    if len(points) < 2:
        return points.tolist()

    lat0 = float(points[:, 0].mean())
    # Local equirectangular metres so the PCA axis is not distorted by the
    # lon/lat aspect ratio at ~61 degN.
    x = np.radians(points[:, 1]) * np.cos(np.radians(lat0)) * 6_371_000.0
    y = np.radians(points[:, 0]) * 6_371_000.0
    proj = np.column_stack([x, y])

    centred = proj - proj.mean(axis=0)
    _, _, vt = np.linalg.svd(centred, full_matrices=False)
    t = centred @ vt[0]  # distance along the principal axis

    edges = np.linspace(t.min(), t.max(), n_bins + 1)
    nodes = []
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        mask = (t >= lo) & (t <= hi) if i == n_bins - 1 else (t >= lo) & (t < hi)
        if not mask.any():
            continue
        nodes.append([round(float(points[mask, 0].mean()), 6),
                      round(float(points[mask, 1].mean()), 6)])
    return nodes


def build_cruise_lines(df: pd.DataFrame, cfg: dict) -> list[dict]:
    """Build one aggregated cruise line per configured fjord."""
    ocean = df[df["Cluster"] == "Ocean"].copy()
    # Drop obviously bad fixes.
    ocean = ocean[ocean["Location"].str.lower() != "faulty location"]

    n_bins = int(cfg.get("bins", 9))
    lines = []
    for fj in cfg.get("fjords", []):
        prefix = str(fj["station_prefix"]).upper()
        sub = ocean[ocean["Station ID"].str.upper().str.startswith(prefix)]
        if len(sub) < 2:
            continue
        pts = sub[["Lat", "Lon"]].to_numpy(dtype=float)
        nodes = aggregate_transect(pts, n_bins)
        lines.append({"name": fj.get("name", prefix), "coords": nodes,
                      "n_stations": int(len(sub))})
    return lines


# --------------------------------------------------------------------------
# HTML generation
# --------------------------------------------------------------------------

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>__TITLE__</title>

<link rel="stylesheet"
      href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
      integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY="
      crossorigin="" />
<link rel="stylesheet"
      href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css" />
<link rel="stylesheet"
      href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.2/css/all.min.css" />

<style>
  :root {
    --panel-bg: rgba(255, 255, 255, 0.93);
    --panel-border: #cfd6dd;
    --ink: #1c2733;
    --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  }
  html, body { margin: 0; height: 100%; font-family: var(--font); color: var(--ink); }
  #map { position: absolute; inset: 0; background: #dfeaf2; }

  /* ---- Individual sample badges ---- */
  .sample-badge {
    display: flex; align-items: center; justify-content: center;
    width: 22px; height: 22px; border-radius: 50%;
    border: 1.5px solid rgba(255, 255, 255, 0.9);
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.45);
    font-size: 11px; line-height: 1;
    transition: transform 0.08s ease;
  }
  .sample-badge:hover { transform: scale(1.35); z-index: 10000; }

  /* ---- Pie / donut cluster ---- */
  .pie-cluster { background: none !important; border: none !important; }
  .pie-cluster svg { display: block; filter: drop-shadow(0 1px 3px rgba(0,0,0,0.4)); }
  .pie-cluster .pie-count {
    font-family: var(--font); font-weight: 700; fill: var(--ink);
    text-anchor: middle; dominant-baseline: central;
  }
  .pie-hole { fill: rgba(255,255,255,0.96); }

  /* ---- Place-name labels ---- */
  .place-label {
    background: transparent; border: none; box-shadow: none;
    font-weight: 700; font-size: 14px; color: #14324f;
    text-shadow: -1.5px -1.5px 0 #fff, 1.5px -1.5px 0 #fff,
      -1.5px 1.5px 0 #fff, 1.5px 1.5px 0 #fff, 0 0 4px #fff;
    white-space: nowrap;
  }
  .place-dot {
    width: 7px; height: 7px; border-radius: 50%;
    background: #14324f; border: 1.5px solid #fff;
    box-shadow: 0 0 3px rgba(0,0,0,0.5);
  }

  /* ---- Glacier box labels ---- */
  .glacier-label {
    background: transparent; border: none; box-shadow: none;
    font-weight: 800; font-size: 13px; line-height: 1.15;
    text-shadow: -1.5px -1.5px 0 #fff, 1.5px -1.5px 0 #fff,
      -1.5px 1.5px 0 #fff, 1.5px 1.5px 0 #fff, 0 0 4px #fff;
    white-space: nowrap;
  }

  /* ---- Atmosphere blob (radial fade via SVG gradient) ---- */
  /* !important so the gradient fill + full opacity survive Leaflet's
     per-redraw restyling of the path (which would otherwise reset fill to a
     solid colour and fill-opacity to 0.2). */
  .atmo-blob { fill: url(#atmoGrad) !important; fill-opacity: 1 !important; stroke: none !important; }

  /* ---- Legend ---- */
  .legend {
    background: var(--panel-bg); border: 1px solid var(--panel-border);
    border-radius: 8px; padding: 10px 12px;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.18);
    line-height: 1.5; backdrop-filter: blur(2px);
  }
  .legend h4 {
    margin: 0 0 8px; font-size: 13px; text-transform: uppercase;
    letter-spacing: 0.04em; color: #55616d;
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
  .legend-sep { border: none; border-top: 1px solid #e2e7ec; margin: 8px 0; }
  .legend-line { width: 22px; height: 0; border-top: 3px solid #22375f; flex: 0 0 auto; }
  .legend-blob { width: 20px; height: 20px; border-radius: 50%; flex: 0 0 auto; }

  /* ---- North arrow ---- */
  .north-arrow {
    background: var(--panel-bg); border: 1px solid var(--panel-border);
    border-radius: 8px; padding: 6px 8px 4px;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.18); text-align: center; width: 46px;
  }
  .north-arrow .arrow { font-size: 22px; color: var(--ink); line-height: 1; }
  .north-arrow .n { font-weight: 800; font-size: 12px; letter-spacing: 0.05em; }

  /* ---- Title card ---- */
  .title-card {
    background: var(--panel-bg); border: 1px solid var(--panel-border);
    border-radius: 8px; padding: 8px 12px;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.18); max-width: 320px;
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
<script src="https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js"></script>
<script>
// ------------------------------------------------------------------
// Data + config injected by build_map.py
// ------------------------------------------------------------------
const CFG = __CFG__;
const SAMPLES = __SAMPLES__;
const LEGEND = __LEGEND__;
const CRUISE_LINES = __CRUISE_LINES__;

const THEME_ORDER = LEGEND.map(function (e) { return e.theme; });
const THEME_COLOR = {};
LEGEND.forEach(function (e) { THEME_COLOR[e.theme] = e.bg; });

// ------------------------------------------------------------------
// Base layers
// ------------------------------------------------------------------
const terrain = L.tileLayer(
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
  { maxZoom: 18, attribution: "Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ, USGS, Intermap, iPC, NRCAN, TomTom" }
);
const opentopo = L.tileLayer(
  "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
  { maxZoom: 17, attribution: 'Map data &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, SRTM | Style &copy; <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA)' }
);
const satellite = L.tileLayer(
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
  { maxZoom: 18, attribution: "Imagery &copy; Esri, Maxar, Earthstar Geographics" }
);

const bounds = CFG.view.bounds;
const center = [(bounds[0][0] + bounds[1][0]) / 2, (bounds[0][1] + bounds[1][1]) / 2];

const map = L.map("map", { center: center, zoom: 9, layers: [terrain], scrollWheelZoom: true });

// Panes: labels above everything; the atmosphere blob below the markers.
map.createPane("labels");   map.getPane("labels").style.zIndex = 650;
map.getPane("labels").style.pointerEvents = "none";
map.createPane("blob");     map.getPane("blob").style.zIndex = 350;
map.getPane("blob").style.pointerEvents = "none";

L.control.layers(
  { "Terrain (Esri)": terrain, "Topographic (OpenTopoMap)": opentopo, "Satellite (Esri)": satellite },
  {}, { position: "topright", collapsed: true }
).addTo(map);

// Re-fit once the container truly has a size (guards against 0x0 at load).
let userMoved = false;
map.on("zoomstart movestart", function () { userMoved = true; });
function fitStudyArea() {
  if (userMoved) return;
  const s = map.getSize();
  if (s.x < 50 || s.y < 50) return;
  map.invalidateSize();
  map.fitBounds(bounds, { padding: [20, 20] });
}
if (window.ResizeObserver) new ResizeObserver(fitStudyArea).observe(document.getElementById("map"));
window.addEventListener("load", fitStudyArea);
map.whenReady(fitStudyArea);

// ------------------------------------------------------------------
// Atmosphere blob — an L.circle (geographic radius) filled with a radial
// gradient so it fades to transparent at the edge.
// ------------------------------------------------------------------
(function () {
  const b = CFG.atmosphere_blob;
  if (!b) return;
  const circle = L.circle(b.center, {
    radius: (b.radius_km || 10) * 1000,
    pane: "blob",
    className: "atmo-blob",
    stroke: false,
    fillOpacity: 1,
    interactive: false,
  }).addTo(map);

  // Inject a radialGradient into the pane's SVG and point the circle's fill at it.
  const svg = map.getPane("blob").querySelector("svg");
  const NS = "http://www.w3.org/2000/svg";
  const defs = document.createElementNS(NS, "defs");
  const grad = document.createElementNS(NS, "radialGradient");
  grad.setAttribute("id", "atmoGrad");
  const stops = [[0, b.opacity], [0.55, (b.opacity || 0.5) * 0.6], [1, 0]];
  stops.forEach(function (s) {
    const stop = document.createElementNS(NS, "stop");
    stop.setAttribute("offset", (s[0] * 100) + "%");
    stop.setAttribute("stop-color", b.color || "#e7cf4f");
    stop.setAttribute("stop-opacity", s[1]);
    grad.appendChild(stop);
  });
  defs.appendChild(grad);
  svg.insertBefore(defs, svg.firstChild);
  circle._path.setAttribute("fill", "url(#atmoGrad)");
})();

// ------------------------------------------------------------------
// Cruise lines (aggregated CTD transects)
// ------------------------------------------------------------------
CRUISE_LINES.forEach(function (line) {
  if (!line.coords || line.coords.length < 2) return;
  L.polyline(line.coords, {
    color: CFG.cruise_lines.color || "#22375f",
    weight: CFG.cruise_lines.weight || 3,
    opacity: CFG.cruise_lines.opacity != null ? CFG.cruise_lines.opacity : 0.9,
    lineJoin: "round", lineCap: "round",
  }).addTo(map);
  // Small nodes at each aggregated station bin.
  line.coords.forEach(function (c) {
    L.circleMarker(c, {
      radius: 3, color: "#fff", weight: 1.2,
      fillColor: CFG.cruise_lines.color || "#22375f", fillOpacity: 1,
    }).addTo(map);
  });
});

// ------------------------------------------------------------------
// Pie / donut cluster icon
// ------------------------------------------------------------------
function pieClusterIcon(cluster) {
  const children = cluster.getAllChildMarkers();
  const counts = {};
  children.forEach(function (m) {
    const t = m.options.theme || "?";
    counts[t] = (counts[t] || 0) + 1;
  });
  const total = children.length;

  // Size scales gently with count.
  const size = Math.round(34 + 22 * Math.min(1, Math.sqrt(total) / 12));
  const cx = size / 2, cy = size / 2;
  const stroke = Math.max(7, size * 0.20);
  const r = (size - stroke) / 2 - 1;
  const circ = 2 * Math.PI * r;

  let offset = 0;
  let segs = "";
  THEME_ORDER.forEach(function (t) {
    if (!counts[t]) return;
    const len = (counts[t] / total) * circ;
    segs +=
      '<circle cx="' + cx + '" cy="' + cy + '" r="' + r + '" fill="none" ' +
      'stroke="' + (THEME_COLOR[t] || "#888") + '" stroke-width="' + stroke + '" ' +
      'stroke-dasharray="' + len + ' ' + (circ - len) + '" ' +
      'stroke-dashoffset="' + (-offset) + '" transform="rotate(-90 ' + cx + ' ' + cy + ')"/>';
    offset += len;
  });

  const fontSize = total >= 100 ? size * 0.30 : size * 0.34;
  const html =
    '<svg width="' + size + '" height="' + size + '" viewBox="0 0 ' + size + ' ' + size + '">' +
    segs +
    '<circle class="pie-hole" cx="' + cx + '" cy="' + cy + '" r="' + (r - stroke / 2) + '"/>' +
    '<text class="pie-count" x="' + cx + '" y="' + (cy + 0.5) + '" font-size="' + fontSize + '">' + total + '</text>' +
    '</svg>';

  return L.divIcon({ html: html, className: "pie-cluster", iconSize: [size, size], iconAnchor: [cx, cy] });
}

function badgeIcon(s) {
  const html =
    '<div class="sample-badge" style="background:' + s.bg + ';color:' + s.fg + ';">' +
    '<i class="fa-solid fa-' + s.icon + '"></i></div>';
  return L.divIcon({ html: html, className: "", iconSize: [22, 22], iconAnchor: [11, 11] });
}

const clusters = L.markerClusterGroup({
  maxClusterRadius: 55,
  showCoverageOnHover: false,
  spiderfyOnMaxZoom: true,
  iconCreateFunction: pieClusterIcon,
});

SAMPLES.forEach(function (s) {
  const m = L.marker([s.lat, s.lon], { icon: badgeIcon(s), theme: s.theme, keyboard: false });
  if (s.tip) m.bindTooltip(s.tip, { direction: "top", offset: [0, -10] });
  clusters.addLayer(m);
});
map.addLayer(clusters);

// ------------------------------------------------------------------
// Glacier context boxes
// ------------------------------------------------------------------
(CFG.boxes || []).forEach(function (b) {
  L.rectangle(b.bounds, { color: b.color, weight: 2.5, fill: false }).addTo(map);
  const ne = L.latLngBounds(b.bounds).getNorthEast();
  L.marker(ne, {
    interactive: false, pane: "labels",
    icon: L.divIcon({
      className: "glacier-label",
      html: '<span style="color:' + b.color + '">' + b.name.replace(/ /g, "<br>") + "</span>",
      iconSize: [130, 40], iconAnchor: [-6, 10],
    }),
  }).addTo(map);
});

// ------------------------------------------------------------------
// Town labels
// ------------------------------------------------------------------
(CFG.towns || []).forEach(function (p) {
  L.marker([p.lat, p.lon], {
    interactive: false, pane: "labels",
    icon: L.divIcon({ className: "", html: '<div class="place-dot"></div>', iconSize: [7, 7], iconAnchor: [3, 3] }),
  }).addTo(map);
  L.marker([p.lat, p.lon], {
    interactive: false, pane: "labels",
    icon: L.divIcon({ className: "place-label", html: p.name, iconSize: [120, 18], iconAnchor: [-6, 14] }),
  }).addTo(map);
});

// ------------------------------------------------------------------
// Legend
// ------------------------------------------------------------------
const legend = L.control({ position: "bottomleft" });
legend.onAdd = function () {
  const div = L.DomUtil.create("div", "legend");
  let html = "<h4>" + (CFG.legend.title || "Legend") + "</h4>";
  LEGEND.forEach(function (e) {
    html +=
      '<div class="legend-row">' +
      '<span class="legend-badge" style="background:' + e.bg + ';color:' + e.fg + ';">' +
      '<i class="fa-solid fa-' + e.icon + '"></i></span>' +
      "<span>" + e.label + "</span>" +
      '<span class="legend-count">' + e.count + "</span></div>";
  });
  html += '<hr class="legend-sep">';
  if (CRUISE_LINES.length) {
    html +=
      '<div class="legend-row"><span class="legend-line" style="border-top-color:' +
      (CFG.cruise_lines.color || "#22375f") + '"></span><span>' +
      (CFG.legend.transect_label || "CTD transect") + "</span></div>";
  }
  if (CFG.atmosphere_blob) {
    const bc = CFG.atmosphere_blob.color || "#e7cf4f";
    html +=
      '<div class="legend-row"><span class="legend-blob" style="background:radial-gradient(circle,' +
      bc + ' 0%, rgba(255,255,255,0) 72%)"></span><span>' +
      (CFG.legend.blob_label || "Atmospheric sampling") + "</span></div>";
  }
  div.innerHTML = html;
  return div;
};
legend.addTo(map);

// ------------------------------------------------------------------
// North arrow
// ------------------------------------------------------------------
const north = L.control({ position: "bottomright" });
north.onAdd = function () {
  const div = L.DomUtil.create("div", "north-arrow");
  div.innerHTML = '<div class="arrow"><i class="fa-solid fa-location-arrow" style="transform:rotate(-45deg)"></i></div><div class="n">N</div>';
  return div;
};
north.addTo(map);

// ------------------------------------------------------------------
// Title card
// ------------------------------------------------------------------
const title = L.control({ position: "topleft" });
title.onAdd = function () {
  const div = L.DomUtil.create("div", "title-card");
  div.innerHTML = "<h1>" + CFG.title + "</h1>" + (CFG.subtitle ? "<p>" + CFG.subtitle + "</p>" : "");
  L.DomEvent.disableClickPropagation(div);
  return div;
};
title.addTo(map);
</script>
</body>
</html>
"""


def render_html(cfg, features, legend, cruise_lines) -> str:
    def dump(obj):
        return json.dumps(obj, ensure_ascii=False)

    n_samples = len(features)
    n_themes = len(legend)
    title = cfg.get("title", "Sampling locations")
    subtitle = (cfg.get("subtitle") or "").format(n_samples=n_samples, n_themes=n_themes)

    # The config object handed to JS (with the interpolated subtitle).
    js_cfg = {
        "title": title,
        "subtitle": subtitle,
        "legend": cfg.get("legend", {}),
        "view": cfg.get("view", {}),
        "towns": cfg.get("towns", []),
        "boxes": cfg.get("boxes", []),
        "atmosphere_blob": cfg.get("atmosphere_blob"),
        "cruise_lines": cfg.get("cruise_lines", {}),
    }

    return (
        HTML_TEMPLATE
        .replace("__TITLE__", title)
        .replace("__CFG__", dump(js_cfg))
        .replace("__SAMPLES__", dump(features))
        .replace("__LEGEND__", dump(legend))
        .replace("__CRUISE_LINES__", dump(cruise_lines))
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    cfg = load_config(args.config)
    themes = cfg["themes"]

    df = load_samples(args.csv)
    features = build_features(df, themes)
    legend = build_legend(df, themes)
    cruise_lines = build_cruise_lines(df, cfg.get("cruise_lines", {}))

    html = render_html(cfg, features, legend, cruise_lines)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8")

    print(f"Wrote {args.out}  ({len(features)} samples, {len(legend)} themes)")
    for line in cruise_lines:
        print(f"  cruise line: {line['name']:<15} {line['n_stations']:>3} stations "
              f"-> {len(line['coords'])} nodes")


if __name__ == "__main__":
    main()
