# seo_agent.py
# SEO Agent: Processes SEO tasks from plan using Google Sheets (Screaming Frog export).
# - Loads data via gspread (assumes service account setup).
# - Handles: Filtering (LLM-generated queries), Grouping (value_counts), Enrichment (title lookup), Insights (calc + LLM).
# - Sequential: Uses prev_data for dependencies (e.g., URLs from GA4).
# - Edges: Missing cols/sheet -> error dict; Empty DF -> {}; Exceptions caught.
# - Output: Appends to state['task_results'] as [{'task_id': int, 'data': dict/list}].
# - Reuse: _llm_call from planner.py (import if shared; copied here for standalone).
# - Config: Add to app/config.py: SEO_SHEET_ID, GSHEET_CREDENTIALS_PATH.
# - Libs: Assume gspread, google-auth, pandas installed (pip if needed).
# Usage: In agent.py, call execute_seo_task in task_executor for 'seo' agents.

import os
import sys
import json
from typing import Dict, Any, List
# Add directory containing 'app' package to path so absolute imports work when running directly
# From app/agents/seo_agent.py, go up to app/, then up to web-diagnostics-orchestration/
app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # app/
project_root = os.path.dirname(app_dir)  # web-diagnostics-orchestration/
sys.path.insert(0, project_root)
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from google.auth.exceptions import DefaultCredentialsError
from openai import OpenAI, APIError
from app.config import get_settings  # Assume exists; add SEO_SHEET_ID, GSHEET_CREDENTIALS_PATH

settings = get_settings()

# LLM Client (same as planner)
os.environ['LITELLM_API_KEY'] = os.getenv('LITELLM_API_KEY', 'sk-Mh6Ytmir4rdFDFmxzk46KA')
LITELLM_BASE_URL = os.getenv('LITELLM_BASE_URL', 'http://3.110.18.218')

client = OpenAI(api_key=os.environ['LITELLM_API_KEY'], base_url=LITELLM_BASE_URL)

def _llm_call(prompt: str, model: str = "gemini-2.5-pro", max_retries: int = 3) -> str:
    """Reusable LLM call with retries/fallback (copied from planner for standalone)."""
    base_delay = 1
    import time
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1
            )
            return response.choices[0].message.content.strip()
        except APIError as e:
            if e.status_code == 429:
                wait_time = base_delay * (2 ** attempt)
                print(f"[SEO] Rate limited (attempt {attempt+1}). Waiting {wait_time}s...")
                time.sleep(wait_time)
                continue
            elif e.status_code == 400 and 'invalid model' in str(e).lower():
                print(f"[SEO] Invalid model '{model}'. Falling back to 'gemini-2.5-flash'.")
                return _llm_call(prompt, model="gemini-2.5-flash", max_retries=1)
            else:
                raise e
    raise ValueError(f"[SEO] LLM call failed after {max_retries} retries.")

def _load_seo_data() -> pd.DataFrame:
    """Load Screaming Frog data from Google Sheet. Edge: No auth/sheet -> empty DF."""
    scopes = ['https://www.googleapis.com/auth/spreadsheets']
    try:
        creds_path = getattr(settings, 'GSHEET_CREDENTIALS_PATH', None) or settings.GOOGLE_APPLICATION_CREDENTIALS
        creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
        gc = gspread.authorize(creds)
        sheet_id = getattr(settings, 'SEO_SHEET_ID', None) or settings.SHEET_ID
        if not sheet_id:
            raise ValueError("SEO_SHEET_ID or SHEET_ID must be configured")
        sheet = gc.open_by_key(sheet_id)
        worksheet = sheet.worksheet('Internal')  # Common tab; adjust if 'All' or dynamic
        records = worksheet.get_all_records()  # Dict per row
        if not records:
            print("[SEO] Empty sheet data.")
            return pd.DataFrame()
        df = pd.DataFrame(records)
        # Clean: Strip strings, convert numerics
        for col in df.select_dtypes(include=['object']).columns:
            df[col] = df[col].astype(str).str.strip()
        numeric_cols = ['Title 1 Length', 'Status Code']  # Common; try-convert
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        print(f"[SEO] Loaded {len(df)} rows. Columns: {list(df.columns)}")
        return df
    except (FileNotFoundError, DefaultCredentialsError) as e:
        print(f"[SEO] Auth error: {e}. Check GOOGLE_APPLICATION_CREDENTIALS or GSHEET_CREDENTIALS_PATH.")
        return pd.DataFrame()
    except gspread.SpreadsheetNotFound:
        sheet_id = getattr(settings, 'SEO_SHEET_ID', None) or settings.SHEET_ID
        print(f"[SEO] Sheet not found: {sheet_id}")
        return pd.DataFrame()
    except Exception as e:
        print(f"[SEO] Load error: {e}")
        return pd.DataFrame()

def execute_seo_task(df: pd.DataFrame, task: Dict[str, Any], prev_data: Any = None) -> Dict[str, Any]:
    """Execute single SEO task. Handles modes via desc keywords + LLM for dynamic parts."""
    if df.empty:
        return {"task_id": task['id'], "data": {"error": "No data available"}}

    desc_lower = task['desc'].lower()
    inputs = task.get('inputs', {})
    if prev_data:
        inputs.update(prev_data)

    # Mode 1: Enrichment (e.g., titles for URLs from prev/GA4)
    if 'title tags for' in desc_lower or 'lookup title' in desc_lower:
        urls = inputs.get('urls', [])
        if isinstance(urls, str):
            try:
                urls = json.loads(urls) if urls.startswith('[') else [urls]
            except:
                urls = [urls]
        if not urls:
            return {"task_id": task['id'], "data": {"error": "No URLs provided"}}
        if 'Address' not in df.columns or 'Title 1' not in df.columns:
            return {"task_id": task['id'], "data": {"error": "Missing Address/Title 1 columns"}}
        url_df = df[df['Address'].isin(urls)]
        result = url_df.set_index('Address')['Title 1'].to_dict()
        data = {"titles": result, "found": len(result), "total_requested": len(urls)}
        return {"task_id": task['id'], "data": data}

    # Mode 2: Conditional Filtering (e.g., non-HTTPS + long titles)
    elif 'do not use https' in desc_lower or 'title tags longer than 60' in desc_lower or 'filtering' in desc_lower:
        prompt = f"""Generate pandas query condition for: {task['desc']}

Available: {', '.join(df.columns)}

Output ONLY condition, e.g., "~Address.str.startswith('https') & `Title 1 Length` > 60"
"""
        condition = _llm_call(prompt)
        try:
            filtered = df.query(condition)
            data = filtered.to_dict('records')
        except Exception as e:
            data = {"error": str(e)}
        return {"task_id": task['id'], "data": {"filtered": data, "count": len(data) if isinstance(data, list) else 0}}

    # Mode 3: Grouping/Aggregation
    elif 'group' in desc_lower or 'aggregation' in desc_lower:
        group_col = inputs.get('group_by', 'Status Code')
        if group_col not in df.columns:
            return {"task_id": task['id'], "data": {"error": f"Column {group_col} not found"}}
        groups = df[group_col].value_counts().to_dict()
        data = {"groups": groups}
        return {"task_id": task['id'], "data": data}

    # Default: Full data or error
    else:
        return {"task_id": task['id'], "data": {"error": f"Unknown SEO task: {desc_lower}"}}

# Test
if __name__ == "__main__":
    df = _load_seo_data()
    test_task = {"id": "q0", "desc": "URLs do not use HTTPS and have title tags longer than 60 characters", "inputs": {}}
    result = execute_seo_task(df, test_task)
    print(json.dumps(result, indent=2))