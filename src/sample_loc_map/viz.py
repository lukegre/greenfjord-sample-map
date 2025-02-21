"""
Visualization utilities for creating interactive maps with markers.

This module provides functions for creating and customizing Folium maps
with custom markers, logos, and tile layers for the GreenFjord project.
"""

import pandas as pd
import folium
import folium.plugins

# Default configuration for map tiles
_LEAFLET_DEFAULTS = dict()
GOOGLE_TERRAIN = dict(
    tiles="http://mt0.google.com/vt/lyrs=p&hl=en&x={x}&y={y}&z={z}",
    attr="Google",
    **_LEAFLET_DEFAULTS,
)
GOOGLE_SATELLITE = dict(
    tiles="http://mt0.google.com/vt/lyrs=s&hl=en&x={x}&y={y}&z={z}",
    attr="Google",
    **_LEAFLET_DEFAULTS,
)

# Default settings for marker clustering
marker_cluster_defaults = dict(
    control=False,
    options=dict(
        disableClusteringAtZoom=12,
        spiderfyOnMaxZoom=True,
        maxClusterRadius=40,
        showCoverageOnHover=False,
        removeOutsideVisibleBounds=True,
    ),
)


def make_marker(
    ser: pd.Series,
    x="Lon",
    y="Lat",
    prop_columns=["icon", "text_color", "background_color"],
    parent=None,
    **kwargs,
):
    """
    Create a Folium marker with customized appearance and information popup.

    Parameters
    ----------
    ser : pd.Series
        Series containing marker information including coordinates and properties
    x : str, optional
        Column name for longitude, by default 'Lon'
    y : str, optional
        Column name for latitude, by default 'Lat'
    prop_columns : list, optional
        Columns to use for marker appearance properties, by default ['icon', 'text_color', 'background_color']
    parent : folium.Map or folium.FeatureGroup, optional
        Parent container to add the marker to, by default None
    **kwargs : dict
        Additional arguments passed to folium.Marker

    Returns
    -------
    folium.Marker
        The created marker object
    """
    # Extract coordinates
    lon = ser[x]
    lat = ser[y]

    title_name = "Sample info"

    # Create information panel from series data
    info = ser.drop("Group", errors="ignore").rename(title_name).to_frame()
    props = dict(
        inner_icon_style="padding-top:0px; font-size:14px",
        border_width=1,
        iconSize=(25, 25),
    )
    props = props | info.loc[prop_columns, title_name].to_dict()

    # Create tooltip and popup content
    tooltip = f"{ser.Cluster} – {ser.Type} {ser.Year}"
    tooltip_html = f"<strong>{tooltip}</strong><br>Click for more info"

    popup = info.drop(prop_columns, errors="ignore").dropna()
    popup_html = popup.to_html(
        col_space=80,
        border=0,
        justify="left",
        classes="table table-hover table-responsive",
    )

    # Create and return the marker
    marker = folium.Marker(
        location=[lat, lon],
        tooltip=tooltip_html,
        popup=popup_html,
        icon=folium.plugins.BeautifyIcon(**props),
        **kwargs,
    )

    if parent is not None:
        marker.add_to(parent)

    return marker


def add_logo_top_left(map, url):
    """
    Add a logo to the top-left corner of the map.

    Parameters
    ----------
    map : folium.Map
        The map object to add the logo to
    url : str
        URL of the logo image

    Returns
    -------
    None
    """
    # Define the HTML + CSS for the logo
    logo_html = """
        <style>
            .custom-logo {{
                position: absolute;
                top: 0px;
                left: 0px;
                z-index: 1000;
                background: rgba(200, 42, 68, 1);
                padding-left: 40px;
            }}
            .custom-logo img {{
                height: 90px;  /* Adjust size */
                width: auto;
            }}
        </style>
        <div class="custom-logo">
            <a href="https://greenfjord-project.ch/" target="_blank">
            <img src="{url}" alt="Logo">
            </a>
        </div>
    """.format(url=url)

    # Inject the HTML into the map
    map.get_root().html.add_child(folium.Element(logo_html))


def add_tiles(map):
    """
    Add multiple tile layers to the map with a layer control.

    Parameters
    ----------
    map : folium.Map
        The map object to add tile layers to

    Returns
    -------
    dict
        Dictionary containing the added tile layers
    """
    # Set default properties for tile layers
    props = dict(max_zoom=16, control=False)
    key = "Base Maps"
    tiles = {key: []}

    # Add various tile layer options
    tiles[key] += (folium.TileLayer("OpenStreetMap", name="OpenStreetMaps", **props),)
    tiles[key] += (
        folium.TileLayer("Cartodb dark_matter", name="Cartodb-dark", **props),
    )
    tiles[key] += (folium.TileLayer(**GOOGLE_SATELLITE, name="Google Earth", **props),)
    tiles[key] += (
        folium.TileLayer(
            **GOOGLE_TERRAIN, name="Google Terrain", control=False, max_zoom=14
        ),
    )

    # Add all tile layers to the map
    for layer in tiles[key]:
        map.add_child(layer)

    # Add layer control to toggle between tile layers
    folium.plugins.GroupedLayerControl(
        tiles, exclusive_groups=True, collapsed=False
    ).add_to(map)

    return tiles
