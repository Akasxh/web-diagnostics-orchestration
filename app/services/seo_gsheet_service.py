import time
from io import BytesIO
import pandas as pd
import requests
from app.config import get_settings
from openai import OpenAI
import os
import json
import re
from typing import List, Dict, Any

settings = get_settings()

# Global cache variables
_SEO_WORKBOOK_CACHE = None  # type: dict | None
_LAST_FETCH_TIME = 0
CACHE_DURATION = 120  # Refresh every 2 minutes (optional)

os.environ['LITELLM_API_KEY'] = settings.LITELLM_KEY
LITELLM_BASE_URL = settings.LITELLM_PROXY_URL

client = OpenAI(
    api_key= settings.LITELLM_KEY,
    base_url=LITELLM_BASE_URL
)


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




def get_schema_info(force_refresh: bool = False) -> List[Dict[str, Any]]:
    """
    Fetches the workbook and extracts JSON-safe schema metadata.
    """
    dfs = get_seo_workbook(force_refresh=force_refresh)
    
    schema_info = []

    for table_name, df in dfs.items():
        if df.empty:
            continue

        # 1. Ensure column headers are standard strings (fixes complex header types)
        columns = [str(col) for col in df.columns]
        first_row_text = columns

        # 2. Safely extract the example row
        if len(df) > 0:
            # TRICK: We convert the first row (iloc[0:1]) to a JSON string using Pandas, 
            # then load it back to a Python dict. 
            # This automatically converts:
            # - NumPy int/float -> Python int/float
            # - NaNs -> null
            # - Timestamps -> ISO formatted strings
            example_row_json = df.iloc[0:1].to_json(orient='records', date_format='iso')
            example_row = json.loads(example_row_json)[0]
            
            # The result is a clean dictionary like {'col1': 'val1', ...}
            # If you specifically want a list of values like [val1, val2]:
            example_row = list(example_row.values())
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


import math
import datetime
def execute_workbook_query(query: str, force_refresh: bool = False) -> Any:
    """
    Executes a natural language query against the SEO workbook using an LLM to generate
    Python code. Returns the raw result (number, string, or dataframe).
    """
    
    # 1. Fetch Data & Schema
    try:
        dfs = get_seo_workbook(force_refresh=force_refresh)
        schemas = get_schema_info(force_refresh=False)
    except Exception as e:
        return f"Error loading workbook data: {e}"
    
    # 2. Build Context Summary
    # Maps sheet names to their columns for the LLM
    schema_text_block = ""
    for info in schemas:
        schema_text_block += f"- Sheet: '{info['table_name']}' | Cols: {info['columns']}\n"

    # 3. Construct the Prompt
    prompt = f"""
    You are a Python Data Analyst. 
    You have a dictionary of pandas DataFrames named `dfs` containing the following sheets:
    
    {schema_text_block}
    
    User Query: "{query}"
    
    Write a Python function named `solve(dfs)` that takes the dictionary `dfs` as input and returns the answer.
    
    Rules:
    1. The input `dfs` is a dictionary: {{'SheetName': dataframe}}.
    2. Access data using `dfs['SheetName']`.
    3. Do NOT load data with pd.read_excel/csv. Use the passed `dfs` argument.
    4. Handle edge cases (like empty data) gracefully (e.g., check `if not df.empty`).
    5. Return ONLY valid python code inside ```python ``` blocks.
    """

    # 4. Call LLM
    max_retries = 5
    base_delay = 1
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model="gemini-2.5-pro",
                messages=[{"role": "user", "content": prompt}]
            )
            llm_response = response.choices[0].message.content.strip()
            break
        except Exception as e:
            if hasattr(e, "status_code") and getattr(e, "status_code", None) == 429:
                wait_time = base_delay * (2 ** attempt)
                print(f"LLM API rate limited. Retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                if attempt == max_retries - 1:
                    return f"Error contacting LLM: {e}"
                continue
    else:
        return "Failed to contact LLM after retries."

    # 5. Extract Code Block
    # Regex explains: Look for ```python (or just ```), capture content, end with ```
    match = re.search(r"```(?:python)?\n(.*?)```", llm_response, re.DOTALL | re.IGNORECASE)
    if match:
        code_to_run = match.group(1).strip()
    else:
        # Fallback: Assume the whole response is code if no blocks found
        code_to_run = llm_response

    # 6. Execute Code Safely
    # CRITICAL FIX: We use a single dictionary for the execution scope.
    # This acts as both 'globals' and 'locals' for the exec environment.
    execution_scope = {
        'pd': pd,
        're': re,
        'math': math,
        'datetime': datetime,
        'dfs': dfs  # Optional, but good for debugging scope
    }
    
    try:
        # Define the function in the scope
        exec(code_to_run, execution_scope)
        
        # Check if function exists
        if 'solve' not in execution_scope:
            return f"Error: The LLM generated code but failed to define the 'solve(dfs)' function.\nGenerated Code:\n{code_to_run}"
            
        # Execute the function
        # We grab the function object from the map and call it
        solve_func = execution_scope['solve']
        result = solve_func(dfs)
        
        return result

    except Exception as e:
        # detailed error logging
        return f"Execution Error: {e}\n\nBad Code:\n{code_to_run}"
    # what is the sum of length of all the urls addresses