def read_google_spreadsheet(url):
    """Read data from a publicly shared Google Spreadsheet.

    Parameters
    ----------
    url : str
        The URL of the Google Spreadsheet. Should be a publicly shared spreadsheet
        link in the format 'https://docs.google.com/spreadsheets/d/...'

    Returns
    -------
    pandas.DataFrame
        A DataFrame containing the spreadsheet data with the following modifications:
        - All columns are initially read as strings
        - The 'Year' column is converted to string type with empty strings replacing NaN values

    Notes
    -----
    The function converts the Google Sheets URL to a CSV export URL before reading.
    """
    import pandas as pd
    from loguru import logger

    url_csv = url.replace('/edit?gid=', '/export?format=csv&gid=')

    logger.info(f"Reading {url_csv}")
    df = pd.read_csv(url_csv, dtype=str)
    df['Year'] = df.Year.astype(str).replace('nan', '')
    return df
