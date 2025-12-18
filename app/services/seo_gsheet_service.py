import re
import gspread
import pandas as pd
import numpy as np
from typing import Dict, List, Any, Optional
import os
from cachetools import TTLCache
from app.config import get_settings

# Get settings instance
settings = get_settings()

# --- CACHE SETUP ---
# Stores the entire dictionary of DataFrames. 
sheet_cache = TTLCache(maxsize=1, ttl=300)

def fetch_all_sheet_data(sheet_url: Optional[str] = None, force_refresh: bool = False) -> Dict[str, pd.DataFrame]:
    """
    Connects to Google Sheet and fetches all worksheets into a dictionary of Pandas DataFrames.
    Returns: Dict where Key = Sheet Name, Value = DataFrame
    """
    try:
        # Determine the target Sheet ID/URL for the cache key
        target_id = settings.SHEET_ID
        if sheet_url:
            target_id = sheet_url 
        
        cache_key = f"sheet_data_{target_id}"

        # 1. CHECK CACHE
        if not force_refresh and cache_key in sheet_cache:
            print("⚡ [CACHE HIT] Serving sheet data from memory.")
            # FIX: Return the actual cached data, not a status dict
            return sheet_cache[cache_key]

        # 2. FETCH FROM API (If cache miss)
        print("[CACHE MISS] Fetching fresh data from Google Sheets API...")
        
        target_url = sheet_url if sheet_url else f"https://docs.google.com/spreadsheets/d/{settings.SHEET_ID}/edit"
        credentials_path = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS') or settings.GOOGLE_APPLICATION_CREDENTIALS
        
        gc = gspread.service_account(filename=credentials_path)
        sh = gc.open_by_url(target_url)

        all_data = {}
        worksheets = sh.worksheets()

        print(f"Found {len(worksheets)} worksheets. Downloading...")

        for worksheet in worksheets:
            title = worksheet.title
            data = worksheet.get_all_values()

            if not data:
                # Create empty DF with no columns if sheet is empty
                all_data[title] = pd.DataFrame()
                continue

            headers = data[0]
            rows = data[1:]

            # Create DF and try to infer numeric types automatically
            df = pd.DataFrame(rows, columns=headers)
            df = df.apply(pd.to_numeric, errors='ignore')

            all_data[title] = df

        # 3. STORE IN CACHE
        sheet_cache[cache_key] = all_data
        print("Data cached successfully for 5 minutes.")
        
        # FIX: Return the actual data!
        return all_data

    except Exception as e:
        print(f"Error fetching sheet data: {e}")
        # Return empty dict on error so downstream functions don't crash
        return {}

def _convert_to_native_types(value: Any) -> Any:
    """
    Recursively converts numpy/pandas types to native Python types for JSON serialization.
    """
    if pd.isna(value):
        return None
    elif isinstance(value, (np.integer, np.int64, np.int32, np.int16, np.int8)):
        return int(value)
    elif isinstance(value, (np.floating, np.float64, np.float32, np.float16)):
        return float(value)
    elif isinstance(value, np.bool_):
        return bool(value)
    elif isinstance(value, (list, tuple)):
        return [_convert_to_native_types(item) for item in value]
    elif isinstance(value, dict):
        return {k: _convert_to_native_types(v) for k, v in value.items()}
    else:
        return value

def generate_schema_info() -> List[Dict[str, Any]]:
    """
    Extracts schema metadata from the dictionary of DataFrames.
    """
    dfs = fetch_all_sheet_data()
    schema_info = []

    # Safe iteration
    for table_name, df in dfs.items():
        # Safety Check: Ensure we are actually dealing with a DataFrame
        if not isinstance(df, pd.DataFrame):
            print(f"Skipping {table_name}: Expected DataFrame, got {type(df)}")
            continue

        if df.empty:
            continue

        columns = df.columns.tolist()
        first_row_text = columns

        if len(df) > 0:
            # iloc[0] gives a Series, tolist() converts to python list
            # but contents might still be numpy types (int64 etc)
            example_row = df.iloc[0].tolist()
            # Convert numpy types to native Python types for JSON serialization
            example_row = _convert_to_native_types(example_row)
        else:
            example_row = []

        schema_object = {
            "table_name": table_name,
            "columns": columns,
            "first_row_text": first_row_text,
            "example": example_row
        }

        schema_info.append(schema_object)
    
    return schema_info