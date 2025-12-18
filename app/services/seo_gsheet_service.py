import time
from io import BytesIO
import pandas as pd
import requests
from app.config import get_settings

# Global cache variables
_SEO_WORKBOOK_CACHE = None  # type: dict | None
_LAST_FETCH_TIME = 0
CACHE_DURATION = 300  # Refresh every 5 minutes (optional)



settings = get_settings()


def get_seo_workbook(force_refresh: bool = False) ->        dict:
    """
    Download and cache the entire Google Sheet (all tabs).
    Returns a dict mapping sheet name -> pandas.DataFrame.
    Uses an in-memory cache with a simple time-based expiry.
    """
    global _SEO_WORKBOOK_CACHE, _LAST_FETCH_TIME

    current_time = time.time()

    if (
        not force_refresh
        and _SEO_WORKBOOK_CACHE is not None
        and (current_time - _LAST_FETCH_TIME) < CACHE_DURATION
    ):
        print("Returning cached workbook...")
        return _SEO_WORKBOOK_CACHE

    sheet_id = settings.SHEET_ID
    print(sheet_id)

    if not sheet_id:
        raise ValueError("GSHEET_ID is not configured in settings.")

    print("Fetching fresh workbook from Google Sheets...")
    # Export the whole spreadsheet as XLSX so all sheets are included
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=xlsx"

    try:
        response = requests.get(url)
        response.raise_for_status()

        # Read all sheets; returns dict[sheet_name, DataFrame]
        _SEO_WORKBOOK_CACHE = pd.read_excel(BytesIO(response.content), sheet_name=None)
        _LAST_FETCH_TIME = current_time
        return _SEO_WORKBOOK_CACHE

    except Exception as e:
        print(f"Failed to fetch workbook: {e}")
        # If refresh fails, return old cache if it exists
        return _SEO_WORKBOOK_CACHE or {}


def get_seo_data() -> dict:
    """
    Backwards-compatible helper that returns the cached workbook.

    Alias of get_seo_workbook() for existing callers.
    """
    return get_seo_workbook()


def get_sheet_names(force_refresh: bool = False) -> list[str]:
    """
    Return a list of all sheet names in the Google Sheet.
    """
    workbook = get_seo_workbook(force_refresh=force_refresh)
    return list(workbook.keys())
