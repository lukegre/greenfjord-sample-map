import sys
import pathlib
import folium
import pandas as pd
from loguru import logger
from typing import Union


logger.remove()
logger.add(sys.stdout, level="INFO")

pwd = pathlib.Path(__file__).resolve().parent
base = pwd.parent.parent


GOOGLE_SHEET_URL = "https://docs.google.com/spreadsheets/d/1iiT7vFbSD5viC6HbDgk3-UziFO5OaffKlKr9PUTY5YM/edit?gid=0#gid=0"
LOGO_URL = "https://greenfjord-project.ch/wp-content/uploads/2022/05/logo.svg"


def main(sname_html: Union[str, pathlib.Path] = base / "docs/index.html") -> folium.Map:
    from collections import defaultdict
    from . import data, viz

    url = GOOGLE_SHEET_URL
    df_all = data.read_google_spreadsheet(url)
    logger.debug(f"Data loaded from {url}")

    m = folium.Map(location=[60.868, -46.148], zoom_start=10, max_zoom=16, tiles=None)
    m.add_css_link("custom_css", "./custom_style.css")
    # adding greenfjord-logo to map
    viz.add_logo_top_left(m, LOGO_URL)

    # adding background tiles
    viz.add_tiles(m)

    # adding marker cluster group for numerous samples
    mcg = folium.plugins.MarkerCluster(name='all', overlay=False, **viz.marker_cluster_defaults).add_to(m)
    _add_markers_to_subgroups(map=m, layer_control=mcg, data=df_all)

    # for year in df_all.Year.unique():
    #     df = df_all[df_all.Year == year]
    #     mc = folium.plugins.MarkerCluster(name=f"{year}", overlay=False, **viz.marker_cluster_defaults).add_to(m)
    #     _add_markers_to_subgroups(map=m, layer_control=mc, data=df)
        
    folium.LayerControl(collapsed=False).add_to(m)

    if sname_html is not None:
        # creating parent directory
        sname_html = pathlib.Path(sname_html)
        sname_html.parent.mkdir(parents=True, exist_ok=True)
        # saving map to file
        m.save(sname_html)
        # logging success message
        logger.success(f"Map saved to {sname_html}")


def _add_markers_to_subgroups(map, layer_control, data:pd.DataFrame):
    from collections import defaultdict
    from . import viz
    
    layer_groups = defaultdict(list)
    for (cluster, group), df in data.groupby(["Cluster", "Group"]):
        color = df.background_color.unique()[0]
        cluster_html = f"{viz.make_svg_circle(color)}{cluster}"

        # using FeatureGroupSubGroup, but can also use folium.FeatureGroup
        fg = folium.plugins.FeatureGroupSubGroup(layer_control, group).add_to(map)
        # adding markers to dictionary for GroupedLayerControl
        layer_groups[cluster_html].append(fg)
        
        # creating markers for each group and adding to FeatureGroup
        markers = df.apply(viz.make_marker, axis=1)
        for marker in markers:
            marker.add_to(fg)
    
    # adding layer control (legend) to map
    folium.plugins.GroupedLayerControl(
        layer_groups, exclusive_groups=False, collapsed=False, sortLayers=False
    ).add_to(map)
    
