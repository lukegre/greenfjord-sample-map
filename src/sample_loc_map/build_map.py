#!/usr/bin/env python3
"""
build_map.py — Generate a self-contained HTML sampling-location map for the
GreenFjord South Greenland campaign.

Reads ``data/sample_locations_merged.csv`` plus a ``config.yml`` and writes a single
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
import base64
import json
import re
import urllib.request
from pathlib import Path

import dotenv
import numpy as np
import pandas as pd
import yaml
from ruamel.yaml import YAML

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

REPO_ROOT = Path(dotenv.find_dotenv("pyproject.toml")).resolve().parent
HERE = Path(__file__).resolve().parent
DEFAULT_CSV = REPO_ROOT / "data" / "sample_locations_merged.csv"
DEFAULT_CONFIG = HERE / "config.yml"
DEFAULT_OUT = REPO_ROOT / "docs" / "index.html"

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

    for col in (
        "Location",
        "Type",
        "icon",
        "Year",
        "Station ID",
        "Group",
        "Time",
        "Link to paper",
        "Link display",
        "Extra",
    ):
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str).str.strip()
        else:
            df[col] = ""

    # Year often arrives as a float ("2023.0"); render it as a plain integer.
    def _clean_year(s: str) -> str:
        if s in ("", "nan"):
            return ""
        try:
            return str(int(float(s)))
        except ValueError:
            return s

    df["Year"] = df["Year"].map(_clean_year)

    return df


def _sub_label(v) -> str:
    """The sub-type label the features/legend use (empty Type -> placeholder)."""
    s = "" if v is None else str(v)
    return s or "(unspecified)"


def compute_disabled_keys(df: pd.DataFrame, filters: dict) -> list[list[str]]:
    """Sub-types that start toggled *off* in the interactive legend.

    ``filters['exclude_types']`` maps a theme name (or ``"*"`` for all themes)
    to a list of ``Type`` values. Rather than dropping these samples, the map
    keeps them and starts their legend entries disabled — exactly as if the
    user had clicked them off — so they can be re-enabled interactively.

    Returns ``[theme, subtype]`` pairs matching the keys the legend toggles.
    """
    exclude = (filters or {}).get("exclude_types", {}) or {}
    if not exclude:
        return []

    global_ex = {str(t) for t in exclude.get("*", []) or []}
    present = {(str(theme), _sub_label(st)) for theme, st in zip(df["Cluster"], df["Type"])}
    disabled = set()
    for theme, subtype in present:
        theme_ex = {str(t) for t in (exclude.get(theme, []) or [])}
        if subtype in global_ex or subtype in theme_ex:
            disabled.add((theme, subtype))
    return sorted(list(pair) for pair in disabled)


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
        features.append({
            "lat": round(float(r["Lat"]), 6),
            "lon": round(float(r["Lon"]), 6),
            "theme": theme,
            "icon": r["icon"] or "circle",
            "bg": tcfg.get("color", "#888888"),
            "fg": tcfg.get("text_color", "#ffffff"),
            "subtype": r["Type"] or "(unspecified)",
            "location": r["Location"],
            "year": r["Year"],
            "group": r["Group"],
            "time": r["Time"],
            "station": r["Station ID"],
            "link": r["Link to paper"],
            "link_display": r["Link display"],
            "extra": r["Extra"],
        })
    return features


def build_legend(df: pd.DataFrame, themes: dict) -> list[dict]:
    """One legend entry per theme present in the data, in config order."""
    counts = df["Cluster"].value_counts().to_dict()
    legend = []
    for theme, cfg in themes.items():
        if theme not in counts:
            continue
        legend.append({
            "theme": theme,
            "label": cfg.get("label", theme),
            "icon": cfg.get("legend_icon", "circle"),
            "bg": cfg.get("color", "#888888"),
            "fg": cfg.get("text_color", "#ffffff"),
            "count": int(counts[theme]),
        })
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
        nodes.append([
            round(float(points[mask, 0].mean()), 6),
            round(float(points[mask, 1].mean()), 6),
        ])
    return nodes


def aggregate_fjord_track(df: pd.DataFrame, prefix: str, n_bins: int):
    """Aggregate the CTD stations of one fjord into ordered track nodes."""
    ocean = df[df["Cluster"] == "Ocean"].copy()
    ocean = ocean[ocean["Location"].str.lower() != "faulty location"]  # drop bad fixes
    sub = ocean[ocean["Station ID"].str.upper().str.startswith(prefix.upper())]
    if len(sub) < 2:
        return [], int(len(sub))
    pts = sub[["Lat", "Lon"]].to_numpy(dtype=float)
    return aggregate_transect(pts, n_bins), int(len(sub))


def build_cruise_lines(df: pd.DataFrame, cfg: dict) -> list[dict]:
    """One cruise line per configured fjord.

    Uses the hand-editable ``track`` points from the config if present;
    otherwise falls back to aggregating the CTD stations on the fly (so the map
    still renders before ``--extract-tracks`` has been run).
    """
    n_bins = int(cfg.get("bins", 9))
    lines = []
    for fj in cfg.get("fjords", []):
        track = fj.get("track") or []
        if track:
            coords = [[float(p[0]), float(p[1])] for p in track]
            source = "config"
        else:
            coords, _ = aggregate_fjord_track(df, str(fj.get("station_prefix", "")), n_bins)
            source = "auto"
        if len(coords) < 2:
            continue
        lines.append({"name": fj.get("name", ""), "coords": coords, "source": source})
    return lines


def extract_tracks_to_config(df: pd.DataFrame, config_path: Path) -> None:
    """Recompute each fjord's track from the CTD data and write the points back
    into ``config.yml`` (comments preserved via ruamel round-trip)."""
    from ruamel.yaml.comments import CommentedSeq

    def flow_point(lat, lon):
        pt = CommentedSeq([round(lat, 5), round(lon, 5)])
        pt.fa.set_flow_style()  # render as "[lat, lon]" on one line
        return pt

    yaml_rt = YAML()
    yaml_rt.preserve_quotes = True
    yaml_rt.width = 4096  # don't wrap flow-style maps (themes/towns/boxes)
    yaml_rt.indent(mapping=2, sequence=4, offset=2)
    with config_path.open(encoding="utf-8") as fh:
        doc = yaml_rt.load(fh)

    cfg = doc.get("cruise_lines", {})
    n_bins = int(cfg.get("bins", 9))
    for fj in cfg.get("fjords", []):
        nodes, n = aggregate_fjord_track(df, str(fj.get("station_prefix", "")), n_bins)
        fj["track"] = CommentedSeq(flow_point(lat, lon) for lat, lon in nodes)
        print(f"  {fj.get('name', ''):<15} {n:>3} stations -> {len(nodes)} track points")

    with config_path.open("w", encoding="utf-8") as fh:
        yaml_rt.dump(doc, fh)
    print(f"Updated tracks in {config_path}")


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
    width: var(--badge-size, 26px); height: var(--badge-size, 26px); border-radius: 50%;
    border: 1.5px solid rgba(255, 255, 255, 0.9);
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.45);
    font-size: calc(var(--badge-size, 26px) * 0.5); line-height: 1;
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

  /* ---- Aggregated-cluster tooltip ---- */
  .cluster-tt { font-family: var(--font); }
  table.cluster-tip { border-collapse: collapse; font-size: 11.5px; }
  .cluster-tip td { padding: 1px 7px; vertical-align: top; line-height: 1.5; }
  .cluster-tip .ct-head {
    font-weight: 700; font-size: 12.5px; color: var(--ink);
    padding: 0 7px 4px; border-bottom: 1px solid #e2e7ec;
  }
  .cluster-tip tr:first-child + tr td { padding-top: 4px; }
  .cluster-tip tr.ct-sec td { border-top: 1px solid #eef1f4; padding-top: 4px; }
  .cluster-tip .ct-k { color: #8a949e; font-weight: 600; white-space: nowrap; }
  .cluster-tip .ct-v { color: var(--ink); font-weight: 600; }
  .cluster-tip .ct-item { color: var(--ink); }
  .cluster-tip .ct-n { color: var(--ink); font-weight: 600; text-align: right; }
  .cluster-tip a { color: #2f6fb0; font-weight: 600; text-decoration: none; }
  .cluster-tip a:hover { text-decoration: underline; }

  /* ---- Single-sample popup ---- */
  .sample-popup .leaflet-popup-content { margin: 9px 12px; }
  .sample-popup .leaflet-popup-content-wrapper { border-radius: 8px; }
  .sample-tip .ct-v { font-weight: 500; }
  .sample-tip .ct-v a { font-weight: 600; }

  /* ---- Place-name labels ---- */
  .place-label {
    background: transparent; border: none; box-shadow: none;
    font-weight: 700; font-size: 14px; color: #14324f;
    text-shadow: -1.5px -1.5px 0 #fff, 1.5px -1.5px 0 #fff,
      -1.5px 1.5px 0 #fff, 1.5px 1.5px 0 #fff, 0 0 4px #fff;
    white-space: nowrap;
  }
  /* Left-side variant: text hugs the right edge so it sits left of the dot. */
  .place-label.place-label-left { text-align: right; }
  /* Re-enable pointer events (the "labels" pane is pointer-events:none, and the
     property is inherited) so place names can be hovered-to-front. */
  .place-label, .place-dot-icon, .place-dot { pointer-events: auto; }
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
    position: absolute; z-index: 1000;
    background: var(--panel-bg); border: 1px solid var(--panel-border);
    border-radius: 8px; padding: 10px 12px;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.18);
    line-height: 1.5; backdrop-filter: blur(2px);
  }
  .legend h4 {
    margin: 0 0 8px; font-size: 13px; text-transform: uppercase;
    letter-spacing: 0.04em; color: #55616d;
  }
  .legend-subhead {
    margin: 0 0 6px; font-size: 11px; font-weight: 700; text-transform: uppercase;
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

  /* interactive legend: expandable + clickable */
  .legend-theme.clickable, .legend-sub.clickable, .legend-toggle.clickable { cursor: pointer; user-select: none; }
  .legend-theme.clickable, .legend-toggle.clickable { padding: 1px 4px; margin: 0 -4px; border-radius: 5px; }
  .legend-theme.clickable:hover, .legend-toggle.clickable:hover { background: rgba(0, 0, 0, 0.05); }
  .legend-caret {
    width: 11px; flex: 0 0 auto; font-size: 9px; color: #99a2ab;
    text-align: center; transition: transform 0.12s ease;
  }
  .legend-caret.open { transform: rotate(90deg); }
  .legend-subs { display: none; margin: 3px 0 5px 30px; }
  .legend-subs.open { display: block; }
  .legend-sub {
    display: flex; align-items: center; gap: 7px; font-size: 12px;
    color: #3d4650; padding: 1.5px 4px; margin: 0 -4px; border-radius: 5px;
  }
  .legend-sub:hover { background: rgba(0, 0, 0, 0.05); }
  .legend-sub .dot {
    width: 9px; height: 9px; border-radius: 50%; flex: 0 0 auto;
    border: 1px solid rgba(255, 255, 255, 0.85); box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.12);
  }
  .legend-sub .sub-count { margin-left: auto; color: #9aa3ac; font-size: 11px; padding-left: 8px; }
  .legend-off { opacity: 0.45; }
  .legend-off > .legend-label, .legend-off .sub-name { text-decoration: line-through; }
  .legend-line { width: 22px; height: 0; border-top: 3px solid #22375f; flex: 0 0 auto; }
  .legend-blob { width: 20px; height: 20px; border-radius: 50%; flex: 0 0 auto; }
  .legend-town { width: 20px; display: flex; justify-content: center; flex: 0 0 auto; }
  .legend-town span {
    width: 8px; height: 8px; border-radius: 50%;
    background: #14324f; border: 1.5px solid #fff; box-shadow: 0 0 2px rgba(0,0,0,0.5);
  }
  .legend-box {
    width: 18px; height: 14px; border-radius: 2px; flex: 0 0 auto;
    border: 2px solid #6b7280; background: transparent;
  }

  /* ---- North arrow ---- */
  .north-arrow {
    position: absolute; z-index: 1000;
    padding: 6px 8px 4px; text-align: center; width: 46px;
    /* No panel — a white halo keeps it legible on any basemap. */
    filter: drop-shadow(0 0 1.5px #fff) drop-shadow(0 0 1px #fff);
  }
  .north-arrow .arrow { font-size: 22px; color: var(--ink); line-height: 1; }
  .north-arrow .n { font-weight: 800; font-size: 12px; letter-spacing: 0.05em; color: var(--ink); }

  /* ---- Scale bar (cartographic ruler: bracket bar + end ticks) ---- */
  .leaflet-control-scale-line {
    background: transparent; box-shadow: none; border-radius: 0;
    border: 2.5px solid var(--ink); border-top: none;   /* bottom rule + two end ticks */
    color: var(--ink); font-size: 11px; font-weight: 700;
    letter-spacing: 0.03em; line-height: 1.15;
    padding: 2px 6px 3px; text-align: center; white-space: nowrap;
    /* White halo around the whole shape (text + bracket) so it reads on any basemap. */
    filter: drop-shadow(0 0 1.5px #fff) drop-shadow(0 0 1px #fff);
  }
  .leaflet-control-scale-line:not(:first-child) { margin-top: 3px; border-top: none; }

  /* ---- Fullscreen toggle control ---- */
  /* Match the layer switcher's toggle exactly. Leaflet grows that toggle to
     44px on touch-capable browsers (the .leaflet-touch class) but leaves plain
     .leaflet-bar buttons at 36px, so mirror both sizes here. */
  .fullscreen-control a {
    display: flex; align-items: center; justify-content: center;
    width: 36px; height: 36px; line-height: 36px;
    font-size: 17px; color: var(--ink);
  }
  .leaflet-touch .fullscreen-control a {
    width: 44px; height: 44px; line-height: 44px; font-size: 20px;
  }

  /* ---- Project logo ---- */
  .map-logo {
    position: absolute; z-index: 1000;
    display: flex; align-items: center;
  }
  .map-logo a { display: block; line-height: 0; }
  .map-logo img { display: block; width: auto; }
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
// Base layers (from CFG.basemaps)
// ------------------------------------------------------------------
const baseLayers = {};
let defaultBase = null;
(CFG.basemaps || []).forEach(function (b, i) {
  const layer = L.tileLayer(b.url, { maxZoom: b.max_zoom || 18, attribution: b.attribution || "" });
  baseLayers[b.name] = layer;
  if (i === 0) defaultBase = layer; // the first basemap in the list loads by default
});

const bounds = CFG.view.bounds;
const center = [(bounds[0][0] + bounds[1][0]) / 2, (bounds[0][1] + bounds[1][1]) / 2];

const map = L.map("map", { center: center, zoom: 9, layers: defaultBase ? [defaultBase] : [], scrollWheelZoom: true, zoomControl: false });

// Panes. z-index for boxes / samples / labels is author-configurable
// (CFG.z_order); the atmosphere blob sits below them all.
const ZO = CFG.z_order || {};
map.createPane("boxesPane");   map.getPane("boxesPane").style.zIndex = ZO.boxes != null ? ZO.boxes : 400;
map.createPane("samplesPane"); map.getPane("samplesPane").style.zIndex = ZO.samples != null ? ZO.samples : 600;
map.createPane("labels");      map.getPane("labels").style.zIndex = ZO.labels != null ? ZO.labels : 650;
map.getPane("labels").style.pointerEvents = "none";
map.getPane("tooltipPane").style.zIndex = ZO.tooltip != null ? ZO.tooltip : 700; // built-in pane
map.createPane("blob");        map.getPane("blob").style.zIndex = 350;
map.getPane("blob").style.pointerEvents = "none";

// Hover-to-front: lift ONLY the hovered place-name above everything else,
// regardless of the configured pane z-order (CFG.z_order). We move just that
// marker's icon element into a dedicated top pane on mouse-over and put it back
// on mouse-out, so its neighbours stay where they are. HOVER_Z sits below the
// tooltip pane (700) so tooltips still render on top. All panes share the map
// pane's origin, so the icon keeps its position when re-parented.
const HOVER_Z = 690;
map.createPane("hoverTop");
map.getPane("hoverTop").style.zIndex = HOVER_Z;
// `layer` is the thing being hovered; `companions` (optional) are lifted with it
// so, e.g., a place name and its dot rise together no matter which you hover.
function enableHoverFront(layer, companions) {
  const group = [layer].concat(companions || []);
  layer.on("mouseover", function () {
    const topPane = map.getPane("hoverTop");
    group.forEach(function (l) {
      const icon = l._icon;
      if (!icon || icon.parentNode === topPane) return; // no icon yet, or already lifted
      l._hoverOrigParent = icon.parentNode;
      topPane.appendChild(icon);
    });
  });
  layer.on("mouseout", function () {
    group.forEach(function (l) {
      const icon = l._icon;
      if (icon && l._hoverOrigParent) l._hoverOrigParent.appendChild(icon);
      l._hoverOrigParent = null;
    });
  });
}

// ------------------------------------------------------------------
// Fullscreen toggle — a Leaflet control button in the top-right corner.
// Added before the layer switcher so it stacks *above* it (controls in a
// corner render in order of addition).
//
// Behaviour depends on context:
//   * Standalone page -> toggle the native Fullscreen API on the map container.
//   * Embedded in an iframe -> the Fullscreen API is usually blocked (needs
//     allow="fullscreen" on the host's iframe), so instead open the map's own
//     URL as a standalone full page.
// ------------------------------------------------------------------
// True when this page is running inside an iframe. A cross-origin parent makes
// even reading window.top throw, which itself means we are embedded.
function inIframe() {
  try { return window.self !== window.top; } catch (e) { return true; }
}
const FullscreenControl = L.Control.extend({
  options: { position: "topright" },
  onAdd: function (m) {
    const container = L.DomUtil.create("div", "leaflet-bar leaflet-control fullscreen-control");
    const link = L.DomUtil.create("a", "", container);
    link.href = "#";
    link.setAttribute("role", "button");
    const target = m.getContainer();
    const embedded = inIframe();
    function isFs() { return document.fullscreenElement === target; }
    function update() {
      if (embedded) {
        link.innerHTML = '<i class="fa-solid fa-up-right-from-square"></i>';
        link.title = "Open full map";
      } else {
        link.innerHTML = isFs()
          ? '<i class="fa-solid fa-compress"></i>'
          : '<i class="fa-solid fa-expand"></i>';
        link.title = isFs() ? "Exit full screen" : "View full screen";
      }
      link.setAttribute("aria-label", link.title);
    }
    L.DomEvent.on(link, "click", function (ev) {
      L.DomEvent.stop(ev);
      if (embedded) {
        // Break out of the iframe to the standalone map page. Prefer navigating
        // the top window (allowed on user activation); fall back to a new tab if
        // a cross-origin parent blocks it.
        const url = window.location.href;
        try {
          window.top.location.href = url;
        } catch (e) {
          window.open(url, "_blank", "noopener");
        }
        return;
      }
      if (isFs()) {
        if (document.exitFullscreen) document.exitFullscreen();
      } else if (target.requestFullscreen) {
        target.requestFullscreen();
      }
    });
    L.DomEvent.disableClickPropagation(container);
    if (!embedded) {
      document.addEventListener("fullscreenchange", function () {
        update();
        m.invalidateSize();
      });
    }
    update();
    return container;
  },
});
map.addControl(new FullscreenControl());

L.control.layers(baseLayers, {}, { position: "topright", collapsed: true }).addTo(map);

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
let ATMO_LAYER = null;   // exposed so the legend can toggle it
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
  ATMO_LAYER = circle;
})();

// ------------------------------------------------------------------
// Cruise lines (aggregated CTD transects)
// ------------------------------------------------------------------
// Cardinal-spline smoothing: densify the track so corners round off.
// `smoothing` in [0,1]: 0 -> straight segments, 1 -> a full Catmull-Rom curve.
function smoothTrack(coords, smoothing, steps) {
  if (!smoothing || coords.length < 3) return coords;
  const n = coords.length, out = [];
  for (let i = 0; i < n - 1; i++) {
    const p0 = coords[i > 0 ? i - 1 : 0];
    const p1 = coords[i];
    const p2 = coords[i + 1];
    const p3 = coords[i + 2 < n ? i + 2 : i + 1];
    for (let j = 0; j < steps; j++) {
      const s = j / steps, s2 = s * s, s3 = s2 * s;
      const h00 = 2 * s3 - 3 * s2 + 1;
      const h10 = s3 - 2 * s2 + s;
      const h01 = -2 * s3 + 3 * s2;
      const h11 = s3 - s2;
      const lat = h00 * p1[0] + h10 * (smoothing * (p2[0] - p0[0]) / 2) +
                  h01 * p2[0] + h11 * (smoothing * (p3[0] - p1[0]) / 2);
      const lon = h00 * p1[1] + h10 * (smoothing * (p2[1] - p0[1]) / 2) +
                  h01 * p2[1] + h11 * (smoothing * (p3[1] - p1[1]) / 2);
      out.push([lat, lon]);
    }
  }
  out.push(coords[n - 1]);
  return out;
}

const CRUISE_SMOOTHING = CFG.cruise_lines.smoothing != null ? CFG.cruise_lines.smoothing : 0;
const CRUISE_WEIGHT = CFG.cruise_lines.weight || 3;
// White "casing" drawn beneath each line so the transects stay legible over
// dark backgrounds (glaciers, satellite imagery). Width/opacity are configurable.
const CRUISE_HALO_WEIGHT = CFG.cruise_lines.halo_weight != null
  ? CFG.cruise_lines.halo_weight : CRUISE_WEIGHT + 3;
const CRUISE_HALO_COLOR = CFG.cruise_lines.halo_color || "#ffffff";
const CRUISE_HALO_OPACITY = CFG.cruise_lines.halo_opacity != null
  ? CFG.cruise_lines.halo_opacity : 0.6;
const CRUISE_LAYERS = [];   // exposed so the legend can toggle the transects
CRUISE_LINES.forEach(function (line) {
  if (!line.coords || line.coords.length < 2) return;
  const pts = smoothTrack(line.coords, CRUISE_SMOOTHING, 16);
  // Group the halo and the coloured line so the legend toggles both together.
  const halo = L.polyline(pts, {
    color: CRUISE_HALO_COLOR,
    weight: CRUISE_HALO_WEIGHT,
    opacity: CRUISE_HALO_OPACITY,
    lineJoin: "round", lineCap: "round",
  });
  const pl = L.polyline(pts, {
    color: CFG.cruise_lines.color || "#22375f",
    weight: CRUISE_WEIGHT,
    opacity: CFG.cruise_lines.opacity != null ? CFG.cruise_lines.opacity : 0.9,
    lineJoin: "round", lineCap: "round",
  });
  const grp = L.layerGroup([halo, pl]).addTo(map);
  CRUISE_LAYERS.push(grp);
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

const BADGE_SIZE = (CFG.markers && CFG.markers.size) || 26;
document.documentElement.style.setProperty("--badge-size", BADGE_SIZE + "px");
function badgeIcon(s) {
  const html =
    '<div class="sample-badge" style="background:' + s.bg + ';color:' + s.fg + ';">' +
    '<i class="fa-solid fa-' + s.icon + '"></i></div>';
  const c = BADGE_SIZE / 2;
  return L.divIcon({ html: html, className: "", iconSize: [BADGE_SIZE, BADGE_SIZE], iconAnchor: [c, c] });
}

// Click popup for a single sample — a small detail table. Renders link-like
// fields (the paper link, "Extra") as clickable HTML when a URL/anchor is
// present. The paper link's display text is the "Link display" column value,
// falling back to the URL itself.
function markerPopup(s) {
  function esc(t) {
    return String(t).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  function linkCell(v, label) {
    const t = String(v).trim();
    if (/<a\b/i.test(t)) return t;                                   // already HTML
    if (/^https?:\/\//i.test(t))                                     // bare URL
      return '<a href="' + esc(t) + '" target="_blank" rel="noopener">' + esc(label) + "</a>";
    return esc(t);
  }
  const rows = [];
  function add(label, val) {
    if (val == null || String(val).trim() === "" || val === "(unspecified)") return;
    rows.push('<tr><td class="ct-k">' + label + '</td><td class="ct-v">' + esc(val) + "</td></tr>");
  }
  function addLink(label, val, linkText) {
    if (val == null || String(val).trim() === "") return;
    rows.push('<tr><td class="ct-k">' + label + '</td><td class="ct-v">' + linkCell(val, linkText) + "</td></tr>");
  }
  add("Type", s.subtype);
  add("Group", s.group);
  add("Location", s.location);
  add("Year", s.year);
  add("Time", s.time);
  add("Station", s.station);
  if (s.lat != null && s.lon != null)
    add("Lat, Lon", Number(s.lat).toFixed(5) + ", " + Number(s.lon).toFixed(5));
  addLink("Paper", s.link, (s.link_display && String(s.link_display).trim()) || s.link);
  addLink("More", s.extra, "Open");

  return '<table class="sample-tip cluster-tip"><tbody>' +
    '<tr><td class="ct-head" colspan="2">' + esc(s.theme) + "</td></tr>" +
    rows.join("") + "</tbody></table>";
}

const clusters = L.markerClusterGroup({
  maxClusterRadius: 55,
  showCoverageOnHover: false,
  spiderfyOnMaxZoom: true,
  iconCreateFunction: pieClusterIcon,
  clusterPane: "samplesPane",
});

// One marker object per sample, tagged with theme + sub-type so the legend can
// toggle visibility. SEP joins them into a single key.
const SEP = "␟";
const ALL_MARKERS = SAMPLES.map(function (s) {
  const m = L.marker([s.lat, s.lon], { icon: badgeIcon(s), theme: s.theme, keyboard: false, pane: "samplesPane" });
  m.bindPopup(markerPopup(s), { className: "sample-popup", maxWidth: 320, offset: [0, -6] });
  m._key = s.theme + SEP + s.subtype;
  m._s = s;
  return m;
});

// Sub-types hidden via the legend. A marker is shown when its key is absent.
// Seeded from CFG.disabled_types (config `filters.exclude_types`), so those
// sub-types start toggled off but can be re-enabled by clicking the legend.
const hidden = new Set();
(CFG.disabled_types || []).forEach(function (p) { hidden.add(p[0] + SEP + p[1]); });
function refreshClusters() {
  clusters.clearLayers();
  clusters.addLayers(ALL_MARKERS.filter(function (m) { return !hidden.has(m._key); }));
}
refreshClusters();
map.addLayer(clusters);

// ------------------------------------------------------------------
// Aggregated-cluster tooltips
//   location : mode (most common value)   lat/lon : median
//   years    : counts                     cluster : counts
// ------------------------------------------------------------------
function clusterTooltip(cluster) {
  const kids = cluster.getAllChildMarkers();
  const locs = {}, years = {}, themes = {};
  const lats = [], lons = [];
  kids.forEach(function (m) {
    const s = m._s || {};
    if (s.location) locs[s.location] = (locs[s.location] || 0) + 1;
    if (s.year) years[s.year] = (years[s.year] || 0) + 1;
    if (s.theme) themes[s.theme] = (themes[s.theme] || 0) + 1;
    if (s.lat != null) lats.push(s.lat);
    if (s.lon != null) lons.push(s.lon);
  });

  function esc(t) {
    return String(t).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  function mode(obj) {
    let best = null, n = -1;
    Object.keys(obj).forEach(function (k) { if (obj[k] > n) { n = obj[k]; best = k; } });
    return best;
  }
  function median(arr) {
    if (!arr.length) return null;
    const a = arr.slice().sort(function (x, y) { return x - y; });
    const m = Math.floor(a.length / 2);
    return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2;
  }
  // [key, count] pairs ordered by `order` if given, else by count desc.
  function ordered(obj, order) {
    const keys = order
      ? order.filter(function (k) { return obj[k]; })
      : Object.keys(obj).sort(function (a, b) { return obj[b] - obj[a]; });
    return keys.map(function (k) { return [k, obj[k]]; });
  }
  // A row where the value spans the item + count columns.
  function spanRow(label, value) {
    return '<tr><td class="ct-k">' + label + '</td>' +
           '<td class="ct-v" colspan="2">' + value + "</td></tr>";
  }
  // One row per [item, count] pair; the label spans them via rowspan.
  function groupRows(label, pairs) {
    if (!pairs.length) return "";
    return pairs.map(function (p, i) {
      const head = i === 0
        ? '<td class="ct-k" rowspan="' + pairs.length + '">' + label + "</td>" : "";
      return "<tr>" + head + '<td class="ct-item">' + esc(p[0]) +
             '</td><td class="ct-n">' + p[1] + "</td></tr>";
    }).join("");
  }

  const medLat = median(lats), medLon = median(lons);
  const sections = [];
  const topLoc = mode(locs);
  if (topLoc) sections.push(spanRow("Location", esc(topLoc))); // mode, no count
  if (medLat != null) sections.push(spanRow("Lat/Lon", medLat.toFixed(2) + ", " + medLon.toFixed(2)));
  const yrs = ordered(years, Object.keys(years).sort());
  if (yrs.length) sections.push(groupRows("Years", yrs));
  const cls = ordered(themes, THEME_ORDER);
  if (cls.length) sections.push(groupRows("Cluster", cls));

  // A light rule between major sections (added to each section but the first).
  const rows = sections.map(function (s, i) {
    return i ? s.replace("<tr", '<tr class="ct-sec"') : s;
  }).join("");

  return '<table class="cluster-tip"><tbody>' +
    '<tr><td class="ct-head" colspan="3">' + kids.length + " samples</td></tr>" +
    rows + "</tbody></table>";
}

clusters.on("clustermouseover", function (e) {
  const c = e.propagatedFrom || e.layer;
  c.unbindTooltip();
  c.bindTooltip(clusterTooltip(c), { direction: "top", offset: [0, -14], className: "cluster-tt" });
  c.openTooltip();
});
clusters.on("clustermouseout", function (e) {
  (e.propagatedFrom || e.layer).closeTooltip();
});

// ------------------------------------------------------------------
// Glacier context boxes
// ------------------------------------------------------------------
const BOX_DEF = CFG.box_defaults || {};
function boxStyleKey(b, key, fallback) {
  if (b[key] !== undefined && b[key] !== null) return b[key];
  if (BOX_DEF[key] !== undefined && BOX_DEF[key] !== null) return BOX_DEF[key];
  return fallback;
}
// Rewrite a rectangle's SVG path as a rounded rect. A lat/lon box projects to
// an axis-aligned rectangle in layer-point space, so we can round it directly.
// Recomputed on every redraw so the corner radius stays constant in pixels.
function roundBox(rect, bounds, radius) {
  if (!radius || !rect._path) return;
  const bb = L.latLngBounds(bounds);
  const nw = map.latLngToLayerPoint(bb.getNorthWest());
  const se = map.latLngToLayerPoint(bb.getSouthEast());
  const x0 = Math.min(nw.x, se.x), x1 = Math.max(nw.x, se.x);
  const y0 = Math.min(nw.y, se.y), y1 = Math.max(nw.y, se.y);
  const r = Math.max(0, Math.min(radius, (x1 - x0) / 2, (y1 - y0) / 2));
  rect._path.setAttribute("d",
    "M " + (x0 + r) + " " + y0 + " L " + (x1 - r) + " " + y0 +
    " Q " + x1 + " " + y0 + " " + x1 + " " + (y0 + r) +
    " L " + x1 + " " + (y1 - r) + " Q " + x1 + " " + y1 + " " + (x1 - r) + " " + y1 +
    " L " + (x0 + r) + " " + y1 + " Q " + x0 + " " + y1 + " " + x0 + " " + (y1 - r) +
    " L " + x0 + " " + (y0 + r) + " Q " + x0 + " " + y0 + " " + (x0 + r) + " " + y0 + " Z");
}

const BOX_LAYERS = [];   // rect + label per box, exposed so the legend can toggle them
(CFG.boxes || []).forEach(function (b) {
  const color = b.color || "#444444";
  const fill = !!boxStyleKey(b, "fill", false);
  const dash = boxStyleKey(b, "dash_array", "");
  const rect = L.rectangle(b.bounds, {
    pane: "boxesPane",
    color: color,
    weight: boxStyleKey(b, "weight", 2.5),
    dashArray: dash ? String(dash) : null,
    lineJoin: "round",
    fill: fill,
    fillColor: b.fill_color || color,
    fillOpacity: fill ? boxStyleKey(b, "fill_opacity", 0.08) : 0,
  }).addTo(map);

  // Drop-shadow (applied to the SVG path element).
  if (boxStyleKey(b, "shadow", false)) {
    const off = boxStyleKey(b, "shadow_offset", [0, 2]);
    rect._path.style.filter = "drop-shadow(" + (off[0] || 0) + "px " + (off[1] || 0) +
      "px " + boxStyleKey(b, "shadow_blur", 6) + "px " +
      boxStyleKey(b, "shadow_color", "rgba(0,0,0,0.35)") + ")";
  }

  // Rounded corners: redraw the path after every Leaflet path update, and
  // again whenever the box is (re-)added to the map via the legend toggle.
  const radius = boxStyleKey(b, "corner_radius", 0);
  if (radius) {
    const apply = function () { roundBox(rect, b.bounds, radius); };
    apply();
    map.on("zoomend moveend viewreset", apply);
    rect.on("add", apply);
  }

  const labelColor = b.label_color || color;
  const labelSize = boxStyleKey(b, "label_size", 13);
  const ne = L.latLngBounds(b.bounds).getNorthEast();
  const label = L.marker(ne, {
    interactive: false, pane: "labels",
    icon: L.divIcon({
      className: "glacier-label",
      html: '<span style="color:' + labelColor + ';font-size:' + labelSize + 'px">' +
            b.name.replace(/ /g, "<br>") + "</span>",
      iconSize: [130, 40], iconAnchor: [-6, 10],
    }),
  }).addTo(map);
  BOX_LAYERS.push(rect, label);
});

// ------------------------------------------------------------------
// Town labels
// ------------------------------------------------------------------
const TOWN_LAYERS = [];   // dot + label per town, exposed so the legend can toggle them
const TOWN_LABELS = [];   // {name, lat, lon, label, width, preferSide, side} for collision layout
const TOWN_GAP = 6;       // px between the dot and its label
const TOWN_H = 18;        // label box height (px)

// Build the label divIcon for a given side. "right" text starts just right of
// the dot; "left" text ends just left of it (anchor accounts for text width).
function townIcon(name, side, width) {
  if (side === "left") {
    return L.divIcon({
      className: "place-label place-label-left", html: '<span class="pl-txt">' + name + "</span>",
      iconSize: [width, TOWN_H], iconAnchor: [width + TOWN_GAP, 14],
    });
  }
  return L.divIcon({
    className: "place-label", html: '<span class="pl-txt">' + name + "</span>",
    iconSize: [width, TOWN_H], iconAnchor: [-TOWN_GAP, 14],
  });
}

(CFG.towns || []).forEach(function (p) {
  // interactive so place names can be hovered-to-front; the "labels" pane is
  // pointer-events:none, so the icon elements re-enable events via CSS.
  const dot = L.marker([p.lat, p.lon], {
    interactive: true, pane: "labels",
    icon: L.divIcon({ className: "place-dot-icon", html: '<div class="place-dot"></div>', iconSize: [7, 7], iconAnchor: [3, 3] }),
  }).addTo(map);
  const preferSide = p.side === "left" ? "left" : "right";
  const label = L.marker([p.lat, p.lon], {
    interactive: true, pane: "labels",
    icon: townIcon(p.name, preferSide, 120),
  }).addTo(map);
  enableHoverFront(dot, [label]);
  enableHoverFront(label, [dot]);
  TOWN_LAYERS.push(dot, label);
  TOWN_LABELS.push({ name: p.name, lat: p.lat, lon: p.lon, label: label, width: 120, preferSide: preferSide, side: preferSide });
});

// Keep place-name labels from overlapping each other: flip a label to the
// opposite side of its dot when its preferred side collides with a label that
// has already been placed (greedy, north-to-south, deterministic).
function layoutTownLabels() {
  if (!TOWN_LABELS.length) return;
  // Refresh measured text widths (they change with font loading / zoom is fixed).
  TOWN_LABELS.forEach(function (t) {
    const el = t.label.getElement();
    const span = el && el.querySelector(".pl-txt");
    if (span && span.offsetWidth) t.width = span.offsetWidth;
  });

  function boxFor(pt, side, w) {
    const y1 = pt.y - 14, y2 = pt.y - 14 + TOWN_H;
    return side === "left"
      ? { x1: pt.x - TOWN_GAP - w, x2: pt.x - TOWN_GAP, y1: y1, y2: y2 }
      : { x1: pt.x + TOWN_GAP, x2: pt.x + TOWN_GAP + w, y1: y1, y2: y2 };
  }
  function overlaps(a, b) { return a.x1 < b.x2 && a.x2 > b.x1 && a.y1 < b.y2 && a.y2 > b.y1; }
  function hits(box, placed) {
    let n = 0;
    for (let i = 0; i < placed.length; i++) if (overlaps(box, placed[i])) n++;
    return n;
  }

  const placed = [];
  TOWN_LABELS.slice().sort(function (a, b) { return b.lat - a.lat; }).forEach(function (t) {
    const pt = map.latLngToLayerPoint([t.lat, t.lon]);
    const other = t.preferSide === "left" ? "right" : "left";
    const preferBox = boxFor(pt, t.preferSide, t.width);
    let side = t.preferSide, box = preferBox;
    if (hits(preferBox, placed) > 0) {
      const otherBox = boxFor(pt, other, t.width);
      if (hits(otherBox, placed) < hits(preferBox, placed)) { side = other; box = otherBox; }
    }
    if (side !== t.side) { t.label.setIcon(townIcon(t.name, side, t.width)); t.side = side; }
    placed.push(box);
  });
}

layoutTownLabels();
setTimeout(layoutTownLabels, 0);              // re-run once widths are measurable
map.on("zoomend moveend viewreset", layoutTownLabels);

// ------------------------------------------------------------------
// Legend — positioned in px from a chosen corner (CFG.legend.position).
// When interactive: theme rows expand to reveal sub-types, and clicking a
// theme or sub-type toggles those samples; parent counts track what is shown.
// ------------------------------------------------------------------
(function () {
  const interactive = CFG.legend.interactive !== false;
  const startOpen = !!CFG.legend.start_expanded;

  function esc(t) {
    return String(t).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  // Sub-type breakdown per theme, derived from the samples.
  const subCounts = {};
  SAMPLES.forEach(function (s) {
    (subCounts[s.theme] = subCounts[s.theme] || {});
    subCounts[s.theme][s.subtype] = (subCounts[s.theme][s.subtype] || 0) + 1;
  });
  function themeSubs(t) { return Object.keys(subCounts[t] || {}); }

  const div = document.createElement("div");
  div.className = "legend";
  let html = "<h4>" + esc(CFG.legend.title || "Legend") + "</h4>";
  LEGEND.forEach(function (e) {
    const subNames = themeSubs(e.theme).sort();
    html +=
      '<div class="legend-item" data-theme="' + esc(e.theme) + '">' +
        '<div class="legend-row legend-theme' + (interactive ? " clickable" : "") + '">' +
        (interactive ? '<span class="legend-caret' + (startOpen ? " open" : "") + '">▶</span>' : "") +
        '<span class="legend-badge" style="background:' + e.bg + ";color:" + e.fg + ';">' +
        '<i class="fa-solid fa-' + e.icon + '"></i></span>' +
        '<span class="legend-label">' + esc(e.label) + "</span>" +
        '<span class="legend-count">' + e.count + "</span></div>";
    if (interactive && subNames.length) {
      html += '<div class="legend-subs' + (startOpen ? " open" : "") + '">';
      subNames.forEach(function (st) {
        html +=
          '<div class="legend-sub clickable" data-theme="' + esc(e.theme) + '" data-sub="' + esc(st) + '">' +
          '<span class="dot" style="background:' + e.bg + '"></span>' +
          '<span class="sub-name">' + esc(st) + "</span>" +
          '<span class="sub-count">' + subCounts[e.theme][st] + "</span></div>";
      });
      html += "</div>";
    }
    html += "</div>";
  });
  html += '<hr class="legend-sep">';
  html += '<div class="legend-subhead">Map features</div>';
  if (CRUISE_LINES.length) {
    html +=
      '<div class="legend-row legend-toggle' + (interactive ? " clickable" : "") + '" data-layer="transect">' +
      '<span class="legend-line" style="border-top-color:' +
      (CFG.cruise_lines.color || "#22375f") + '"></span><span class="legend-label">' +
      esc(CFG.legend.transect_label || "CTD transect") + "</span></div>";
  }
  if (CFG.atmosphere_blob) {
    const bc = CFG.atmosphere_blob.color || "#e7cf4f";
    html +=
      '<div class="legend-row legend-toggle' + (interactive ? " clickable" : "") + '" data-layer="blob">' +
      '<span class="legend-blob" style="background:radial-gradient(circle,' +
      bc + ' 0%, rgba(255,255,255,0) 72%)"></span><span class="legend-label">' +
      esc(CFG.legend.blob_label || "Atmospheric sampling") + "</span></div>";
  }
  if ((CFG.towns || []).length) {
    html +=
      '<div class="legend-row legend-toggle' + (interactive ? " clickable" : "") + '" data-layer="towns">' +
      '<span class="legend-town"><span></span></span><span class="legend-label">' +
      esc(CFG.legend.towns_label || "Place names") + "</span></div>";
  }
  if ((CFG.boxes || []).length) {
    html +=
      '<div class="legend-row legend-toggle' + (interactive ? " clickable" : "") + '" data-layer="boxes">' +
      '<span class="legend-box"></span><span class="legend-label">' +
      esc(CFG.legend.boxes_label || "Glacier boxes") + "</span></div>";
  }
  div.innerHTML = html;

  const pos = (CFG.legend && CFG.legend.position) || { anchor: "bottom-left", x: 12, y: 12 };
  const anchor = pos.anchor || "bottom-left";
  const x = (pos.x != null ? pos.x : 12) + "px";
  const y = (pos.y != null ? pos.y : 12) + "px";
  if (anchor.indexOf("right") >= 0) { div.style.right = x; } else { div.style.left = x; }
  if (anchor.indexOf("top") >= 0) { div.style.top = y; } else { div.style.bottom = y; }

  map.getContainer().appendChild(div);
  L.DomEvent.disableClickPropagation(div);
  L.DomEvent.disableScrollPropagation(div);

  // Map-overlay layers (CTD transects, atmosphere blob, place names, glacier
  // boxes). setOverlay syncs the Leaflet layers AND greys the legend row so the
  // current state is always visible. Runs regardless of legend interactivity so
  // the zoom auto-rules below always apply.
  const overlayToggles = {
    transect: CRUISE_LAYERS,
    blob: ATMO_LAYER ? [ATMO_LAYER] : [],
    towns: TOWN_LAYERS,
    boxes: BOX_LAYERS,
  };
  function setOverlay(key, on) {
    const layers = overlayToggles[key];
    if (!layers) return;
    layers.forEach(function (l) { if (on) l.addTo(map); else map.removeLayer(l); });
    const row = div.querySelector('.legend-toggle[data-layer="' + key + '"]');
    if (row) row.classList.toggle("legend-off", !on);
    if (on && key === "towns") layoutTownLabels();
  }

  // Zoom auto-rules: place names + glacier boxes are off at zoom 0-8, on from 9
  // up; atmospheric sampling turns off at zoom 13+. Only fires on threshold
  // crossings, so the user's manual toggles persist while zooming within a band.
  let prevNear = null, prevFar = null;
  function syncZoomOverlays() {
    const z = map.getZoom();
    const near = z >= 9;          // place names + glacier boxes visible
    const far = z >= 13;          // atmospheric sampling suppressed
    if (near !== prevNear) {
      setOverlay("towns", near);
      setOverlay("boxes", near);
      prevNear = near;
    }
    if (far !== prevFar) {
      setOverlay("blob", !far);
      prevFar = far;
    }
  }
  map.on("zoomend", syncZoomOverlays);
  syncZoomOverlays();

  if (!interactive) return;

  function visibleCount(theme) {
    const subs = subCounts[theme] || {};
    let n = 0;
    for (const st in subs) if (!hidden.has(theme + SEP + st)) n += subs[st];
    return n;
  }
  function updateUI() {
    div.querySelectorAll(".legend-sub").forEach(function (el) {
      el.classList.toggle("legend-off", hidden.has(el.dataset.theme + SEP + el.dataset.sub));
    });
    div.querySelectorAll(".legend-item").forEach(function (item) {
      const theme = item.dataset.theme;
      item.querySelector(".legend-count").textContent = visibleCount(theme);
      const allOff = themeSubs(theme).every(function (st) { return hidden.has(theme + SEP + st); });
      item.querySelector(".legend-theme").classList.toggle("legend-off", allOff);
    });
  }
  function toggleSub(theme, sub) {
    const k = theme + SEP + sub;
    if (hidden.has(k)) hidden.delete(k); else hidden.add(k);
    refreshClusters(); updateUI();
  }
  function toggleTheme(theme) {
    const subs = themeSubs(theme);
    const allOff = subs.every(function (st) { return hidden.has(theme + SEP + st); });
    subs.forEach(function (st) {
      const k = theme + SEP + st;
      if (allOff) hidden.delete(k); else hidden.add(k);
    });
    refreshClusters(); updateUI();
  }

  function toggleOverlay(row) {
    // Currently greyed-out (off) -> turn on, and vice-versa.
    setOverlay(row.dataset.layer, row.classList.contains("legend-off"));
  }

  div.addEventListener("click", function (ev) {
    const caret = ev.target.closest(".legend-caret");
    if (caret) {
      caret.classList.toggle("open");
      const subsEl = caret.closest(".legend-item").querySelector(".legend-subs");
      if (subsEl) subsEl.classList.toggle("open");
      return;
    }
    const overlay = ev.target.closest(".legend-toggle");
    if (overlay) { toggleOverlay(overlay); return; }
    const sub = ev.target.closest(".legend-sub");
    if (sub) { toggleSub(sub.dataset.theme, sub.dataset.sub); return; }
    const themeRow = ev.target.closest(".legend-theme");
    if (themeRow) { toggleTheme(themeRow.closest(".legend-item").dataset.theme); }
  });
  updateUI();
})();

// ------------------------------------------------------------------
// North arrow — positioned in px from a chosen corner (like the legend).
// ------------------------------------------------------------------
(function () {
  const cfg = CFG.north_arrow || {};
  if (cfg.show === false) return;
  const div = document.createElement("div");
  div.className = "north-arrow";
  div.innerHTML = '<div class="arrow"><i class="fa-solid fa-location-arrow" style="transform:rotate(-45deg)"></i></div><div class="n">N</div>';
  const pos = cfg.position || { anchor: "bottom-right", x: 12, y: 44 };
  const anchor = pos.anchor || "bottom-right";
  const x = (pos.x != null ? pos.x : 12) + "px";
  const y = (pos.y != null ? pos.y : 44) + "px";
  if (anchor.indexOf("right") >= 0) { div.style.right = x; } else { div.style.left = x; }
  if (anchor.indexOf("top") >= 0) { div.style.top = y; } else { div.style.bottom = y; }
  map.getContainer().appendChild(div);
  L.DomEvent.disableClickPropagation(div);
})();

// ------------------------------------------------------------------
// Project logo — an <img> pinned to a chosen corner (like the legend / north
// arrow: anchor + px offset). Shown on a transparent background, or on a
// coloured `background` band. Recolouring (config `logo.color`) happens at
// build time — the SVG is edited and inlined as a data: URI in cfg.url.
// ------------------------------------------------------------------
(function () {
  const cfg = CFG.logo || {};
  if (cfg.show === false || !cfg.url) return;
  const div = document.createElement("div");
  div.className = "map-logo";
  if (cfg.background) {
    div.style.background = cfg.background;
    div.style.boxShadow = "0 2px 8px rgba(0, 0, 0, 0.18)";
  }
  if (cfg.padding) div.style.padding = cfg.padding;
  const img = document.createElement("img");
  img.src = cfg.url;
  img.alt = cfg.alt || "Logo";
  img.style.height = (cfg.height != null ? cfg.height : 64) + "px";
  if (cfg.link) {
    const a = document.createElement("a");
    a.href = cfg.link;
    a.target = "_blank";
    a.rel = "noopener";
    a.appendChild(img);
    div.appendChild(a);
  } else {
    div.appendChild(img);
  }
  const pos = cfg.position || { anchor: "top-left", x: 0, y: 0 };
  const anchor = pos.anchor || "top-left";
  const x = (pos.x != null ? pos.x : 0) + "px";
  const y = (pos.y != null ? pos.y : 0) + "px";
  if (anchor.indexOf("right") >= 0) { div.style.right = x; } else { div.style.left = x; }
  if (anchor.indexOf("top") >= 0) { div.style.top = y; } else { div.style.bottom = y; }
  map.getContainer().appendChild(div);
  L.DomEvent.disableClickPropagation(div);
})();

// ------------------------------------------------------------------
// Scale bar (Leaflet control)
// ------------------------------------------------------------------
(function () {
  const cfg = CFG.scale_bar || {};
  if (cfg.show === false) return;
  const pos = cfg.position;
  const anchored = pos && typeof pos === "object" && pos.anchor;
  const ctrl = L.control.scale({
    // A plain string ("bottomright") is passed straight to Leaflet; the anchor
    // object is repositioned by hand below, so it just needs a valid placeholder.
    position: (typeof pos === "string" ? pos : "bottomright"),
    maxWidth: cfg.max_width || 140,
    metric: cfg.metric !== false,
    imperial: !!cfg.imperial,
  }).addTo(map);

  if (anchored) {
    // Position like the legend / north arrow: pin to a corner + px offset.
    const el = ctrl.getContainer();
    const anchor = pos.anchor || "bottom-right";
    const x = (pos.x != null ? pos.x : 12) + "px";
    const y = (pos.y != null ? pos.y : 12) + "px";
    el.style.position = "absolute";
    el.style.zIndex = 1000;
    el.style.margin = "0";
    if (anchor.indexOf("right") >= 0) { el.style.right = x; } else { el.style.left = x; }
    if (anchor.indexOf("top") >= 0) { el.style.top = y; } else { el.style.bottom = y; }
    map.getContainer().appendChild(el);   // out of the Leaflet corner, onto the map
    L.DomEvent.disableClickPropagation(el);
  }
})();
</script>
</body>
</html>
"""


def _bbox_to_leaflet(bbox):
    """Convert a standard [west, south, east, north] bbox to Leaflet's
    [[south, west], [north, east]] corner pair."""
    w, s, e, n = bbox
    return [[s, w], [n, e]]


def _load_svg(src: str) -> str:
    """Read an SVG from a local path or an http(s) URL."""
    if re.match(r"^https?://", src):
        with urllib.request.urlopen(src, timeout=15) as resp:  # noqa: S310 (trusted config URL)
            return resp.read().decode("utf-8")
    return Path(src).read_text(encoding="utf-8")


def _recolor_svg(svg: str, color: str) -> str:
    """Recolour a single-colour logo: drop its opaque background and paint the
    (white) marks in ``color``.

    Background = any ``<rect>`` that spans the whole canvas (its width/height
    match the ``<svg>`` width/height); its fill is set to ``none``. White fills
    (``#fff`` / ``#ffffff`` / ``white``) then become ``color``.
    """
    m = re.search(r"<svg\b[^>]*\bwidth=\"([\d.]+)\"[^>]*\bheight=\"([\d.]+)\"", svg)
    if m:
        w, h = m.group(1), m.group(2)

        def _transparent_bg(rect: re.Match) -> str:
            tag = rect.group(0)
            if f'width="{w}"' in tag and f'height="{h}"' in tag:
                return re.sub(r'fill="[^"]*"', 'fill="none"', tag)
            return tag

        svg = re.sub(r"<rect\b[^>]*/?>", _transparent_bg, svg)

    return re.sub(r'fill="(?:#fff|#ffffff|white)"', f'fill="{color}"', svg, flags=re.I)


def _prepare_logo(logo_cfg: dict) -> dict:
    """If the logo requests a `color`, recolour its SVG at build time and inline
    it as a self-contained data: URI (so it works regardless of CORS/origin)."""
    logo = dict(logo_cfg or {})
    color = logo.get("color")
    url = logo.get("url")
    if not color or not url:
        return logo
    try:
        svg = _recolor_svg(_load_svg(url), color)
    except Exception as exc:  # network down, moved asset, etc. — don't break the build
        print(f"  warning: could not recolour logo ({exc}); using it as-is")
        return logo
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    logo["url"] = "data:image/svg+xml;base64," + b64
    logo.pop("color", None)  # recolouring is now baked into the inlined SVG
    return logo


def render_html(cfg, features, legend, cruise_lines, disabled_keys=None) -> str:
    def dump(obj):
        return json.dumps(obj, ensure_ascii=False)

    n_samples = len(features)
    n_themes = len(legend)
    title = cfg.get("title", "Sampling locations")
    subtitle = (cfg.get("subtitle") or "").format(n_samples=n_samples, n_themes=n_themes)

    # The legend heading supports {n_samples}/{n_themes} placeholders too.
    legend_cfg = dict(cfg.get("legend", {}))
    if legend_cfg.get("title"):
        legend_cfg["title"] = legend_cfg["title"].format(n_samples=n_samples, n_themes=n_themes)

    # Config bounds use standard bbox order [west, south, east, north]; Leaflet
    # wants [[south, west], [north, east]], so convert before handing to JS.
    view_cfg = dict(cfg.get("view", {}))
    if view_cfg.get("bounds") is not None:
        view_cfg["bounds"] = _bbox_to_leaflet(view_cfg["bounds"])
    boxes_cfg = []
    for box in cfg.get("boxes", []):
        box = dict(box)
        if box.get("bounds") is not None:
            box["bounds"] = _bbox_to_leaflet(box["bounds"])
        boxes_cfg.append(box)

    # The config object handed to JS (with the interpolated subtitle).
    js_cfg = {
        "title": title,
        "subtitle": subtitle,
        "legend": legend_cfg,
        "view": view_cfg,
        "north_arrow": cfg.get("north_arrow", {}),
        "scale_bar": cfg.get("scale_bar", {}),
        "logo": _prepare_logo(cfg.get("logo", {})),
        "z_order": cfg.get("z_order", {}),
        "basemaps": cfg.get("basemaps", []),
        "towns": cfg.get("towns", []),
        "box_defaults": cfg.get("box_defaults", {}),
        "boxes": boxes_cfg,
        "atmosphere_blob": cfg.get("atmosphere_blob"),
        "cruise_lines": cfg.get("cruise_lines", {}),
        "markers": cfg.get("markers", {}),
        "disabled_types": disabled_keys or [],
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
    parser.add_argument(
        "--extract-tracks",
        action="store_true",
        help="Recompute cruise tracks from the CTD data and write "
        "the points back into the config YAML, then exit.",
    )
    args = parser.parse_args()

    df_all = load_samples(args.csv)

    if args.extract_tracks:
        print("Extracting cruise tracks from CTD data:")
        extract_tracks_to_config(df_all, args.config)
        return

    cfg = load_config(args.config)
    themes = cfg["themes"]

    disabled_keys = compute_disabled_keys(df_all, cfg.get("filters", {}))
    features = build_features(df_all, themes)
    legend = build_legend(df_all, themes)
    cruise_lines = build_cruise_lines(df_all, cfg.get("cruise_lines", {}))

    html = render_html(cfg, features, legend, cruise_lines, disabled_keys)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8")

    disabled = f", {len(disabled_keys)} sub-types disabled by default" if disabled_keys else ""
    print(f"Wrote {args.out}  ({len(features)} samples{disabled}, {len(legend)} themes)")
    for line in cruise_lines:
        print(
            f"  cruise line: {line['name']:<15} {len(line['coords']):>2} points ({line['source']})"
        )


if __name__ == "__main__":
    main()
