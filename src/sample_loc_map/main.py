import pathlib
import folium
from loguru import logger
from typing import Union


pwd = pathlib.Path(__file__).resolve().parent
base = pwd.parent.parent


def main(sname_html:Union[str, pathlib.Path]=base / "docs/index.html")->folium.Map:
    from collections import defaultdict
    from . import data, viz

    url = "https://docs.google.com/spreadsheets/d/1iiT7vFbSD5viC6HbDgk3-UziFO5OaffKlKr9PUTY5YM/edit?gid=0#gid=0"
    df_all = data.read_google_spreadsheet(url)

    m = folium.Map(location=[61, -46], zoom_start=9, max_zoom=16, tiles=None)
    
    # adding background tiles
    tiles = viz.add_tiles(m)

    # adding greenfjord-logo to map
    viz.add_logo_top_left(m, "https://greenfjord-project.ch/wp-content/uploads/2022/05/logo.svg")

    # adding marker cluster group for numerous samples
    mcg = folium.plugins.MarkerCluster(**viz.marker_cluster_defaults).add_to(m)

    markers = defaultdict(list)
    for (cluster, group), df in df_all.groupby(["Cluster", "Group"]):
        # using FeatureGroupSubGroup, but can also use folium.FeatureGroup
        fg = folium.plugins.FeatureGroupSubGroup(mcg, group).add_to(m)
        # adding markers to dictionary for GroupedLayerControl
        markers[cluster].append(fg)
        # creating markers for each group and adding to FeatureGroup
        out = df.apply(viz.make_marker, parent=fg, axis=1)

    # adding layer control (legend) to map
    folium.plugins.GroupedLayerControl(markers, exclusive_groups=False, collapsed=False).add_to(m) 

    if sname_html is not None:
        sname_html = pathlib.Path(sname_html)
        sname_html.parent.mkdir(parents=True, exist_ok=True)
        
        m.save(sname_html, )
    
    return m
