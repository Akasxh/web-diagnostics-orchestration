# ga4_agent.py
"""
ga4_agent.py: Simplified GA4 wrapper for LangGraph integration.
- Core: LLM parses raw inputs to payload; wrapper builds/executes RunReportRequest.
- Optimizations: Dedup imports; direct dict support (bypass LLM if structured); validation; no pagination (assume small queries).
- Edges: Invalid payload/JSON -> raise ValueError; missing prop_id -> TypeError; empty dims/metrics -> skip report; date fallback to '7daysAgo to today'.
- Performance: Single LLM call; no retries (user has no limits); async-ready but sync for simplicity.
- Config: Load from credentials.json; env for GA creds fallback.
- Usage: In task_executor: result = execute_ga4_task(task, prev_data); append to task_results.
- No realtime (focus report); extend if needed.
"""

import os
import sys
import json
import re
from typing import Dict, Any, Optional
# Add directory containing 'app' package to path so absolute imports work when running directly
# From app/agents/ga4_agent.py, go up to app/, then up to web-diagnostics-orchestration/
app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # app/
project_root = os.path.dirname(app_dir)  # web-diagnostics-orchestration/
sys.path.insert(0, project_root)
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    RunReportRequest, DateRange, Dimension, Metric,
    FilterExpression, Filter, FilterExpressionList, OrderBy
)
from google.oauth2 import service_account
from openai import OpenAI, OpenAIError
from app.config import get_settings  # Assume: LITELLM_KEY/URL

settings = get_settings()
os.environ['LITELLM_API_KEY'] = getattr(settings, 'LITELLM_API_KEY', None) or os.getenv('LITELLM_API_KEY', 'sk-Mh6Ytmir4rdFDFmxzk46KA')
LITELLM_BASE_URL = getattr(settings, 'LITELLM_BASE_URL', None) or os.getenv('LITELLM_BASE_URL', 'http://3.110.18.218')

client = OpenAI(api_key=os.environ['LITELLM_API_KEY'], base_url=LITELLM_BASE_URL)

def get_ga4_client() -> BetaAnalyticsDataClient:
    """Returns authenticated GA4 client from credentials.json or default."""
    credentials_path = 'credentials.json'
    scopes = ['https://www.googleapis.com/auth/analytics.readonly']
    if os.path.exists(credentials_path):
        credentials = service_account.Credentials.from_service_account_file(
            credentials_path, scopes=scopes
        )
        return BetaAnalyticsDataClient(credentials=credentials)
    return BetaAnalyticsDataClient()

def _llm_parse_payload(raw_input: str) -> Dict[str, Any]:
    """Parse raw/natural input to payload via LLM. Raise on failure."""
    system_prompt = """
You are a GA4 API Architect. Translate input to JSON for Google Analytics Data API.

- Natural lang or dict-string: Extract dims/metrics/filters/dates.
- Comparisons (e.g., "vs previous"): Add 2nd date_range.
- Dates: "Last 30 days" -> {"start": "30daysAgo", "end": "today"}; "Previous 30" -> {"start": "60daysAgo", "end": "31daysAgo"}.
- Filters: Operator EXACT/CONTAINS/BEGINS_WITH.
- Output ONLY JSON: {"property_id": str|null, "date_ranges": [{"start":str, "end":str}], "dimensions": [str], "metrics": [str], "filters": [{"field":str, "operator":str, "value":str}], "order_by": {"field":str, "desc":bool}}
"""
    try:
        response = client.chat.completions.create(
            model="gemini-2.5-pro",
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": raw_input}],
            temperature=0.0
        )
        raw = response.choices[0].message.content.strip()
        clean = re.sub(r"```json|```", "", raw).strip()
        return json.loads(clean)
    except (OpenAIError, json.JSONDecodeError, KeyError) as e:
        raise ValueError(f"LLM parse failed: {e}")

def get_ga4_payload(raw_input: Any) -> Dict[str, Any]:
    """Unified: If dict, return as-is; else LLM parse."""
    if isinstance(raw_input, dict):
        return raw_input
    if isinstance(raw_input, str) and raw_input.strip().startswith('{'):
        try:
            return json.loads(raw_input)
        except json.JSONDecodeError:
            pass
    return _llm_parse_payload(str(raw_input))

class GA4Wrapper:
    def __init__(self, default_property_id: str = None):
        self.default_property_id = default_property_id

    def build_request(self, payload: Dict[str, Any]) -> RunReportRequest:
        """Build request; validate essentials."""
        property_id = payload.get("property_id") or self.default_property_id
        if not property_id:
            raise TypeError("Missing property_id in payload.")

        # Dates: Ensure at least one; fallback if empty
        date_ranges = []
        for dr in payload.get("date_ranges", [{}]):
            start = dr.get("start", "7daysAgo")
            end = dr.get("end", "today")
            date_ranges.append(DateRange(start_date=start, end_date=end))
        if not date_ranges:
            date_ranges = [DateRange(start_date="7daysAgo", end_date="today")]

        # Dims/Metrics: Skip if empty (API allows, but warn)
        dimensions = [Dimension(name=d) for d in payload.get("dimensions", [])]
        metrics = [Metric(name=m) for m in payload.get("metrics", [])]
        if not dimensions and not metrics:
            raise ValueError("No dimensions or metrics specified.")

        # Filters
        filter_expr = self._build_filters(payload.get("filters", []))

        # Order: Single for now
        order_bys = []
        ob = payload.get("order_by")
        if ob:
            field = ob.get("field")
            if field:
                order_bys.append(OrderBy(
                    dimension=OrderBy.DimensionOrderBy(dimension_name=field),
                    desc=ob.get("desc", False)
                ))

        return RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=date_ranges,
            dimensions=dimensions,
            metrics=metrics,
            dimension_filter=filter_expr,
            order_bys=order_bys
        )

    def _build_filters(self, filters_list: list) -> Optional[FilterExpression]:
        """Build filter expr; supports AND only."""
        if not filters_list:
            return None
        expressions = []
        for f in filters_list:
            op = f.get('operator', 'EXACT').upper()
            match_type = {
                'EXACT': Filter.StringFilter.MatchType.EXACT,
                'CONTAINS': Filter.StringFilter.MatchType.CONTAINS,
                'BEGINS_WITH': Filter.StringFilter.MatchType.BEGINS_WITH
            }.get(op, Filter.StringFilter.MatchType.EXACT)
            expressions.append(FilterExpression(
                filter=Filter(
                    field_name=f['field'],
                    string_filter=Filter.StringFilter(value=f['value'], match_type=match_type)
                )
            ))
        if len(expressions) > 1:
            return FilterExpression(and_group=FilterExpressionList(expressions=expressions))
        return expressions[0] if expressions else None

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Full: Parse if needed, build, run, return rows as dict."""
        if not isinstance(payload, dict):
            payload = get_ga4_payload(payload)
        request = self.build_request(payload)
        ga_client = get_ga4_client()
        response = ga_client.run_report(request=request)
        if not response.rows:
            return {"rows": [], "summary": "No data matched."}
        # Flatten rows to dicts (dim0=dim1=... metric0=...)
        rows = []
        headers = {h.name: i for i, h in enumerate(response.dimension_headers + response.metric_headers)}
        for row in response.rows:
            row_dict = {k: row.dimension_values[headers[k]].value if k in [h.name for h in response.dimension_headers] else row.metric_values[headers[k]].value for k in headers}
            rows.append(row_dict)
        return {"rows": rows, "row_count": len(rows), "headers": [h.name for h in response.dimension_headers + response.metric_headers]}

# Usage in LangGraph (e.g., ga4_agent node)
def execute_ga4_task(task: Dict[str, Any], prev_data: Any = None) -> Dict[str, Any]:
    """Single task exec; inject prev (e.g., filters from SEO)."""
    inputs = task.get('inputs', {})
    if prev_data:
        inputs.update(prev_data)  # e.g., {'urls': [...]} -> filter
    wrapper = GA4Wrapper()
    try:
        result = wrapper.execute(inputs)
        return {"task_id": task['id'], "data": result}
    except Exception as e:
        return {"task_id": task['id'], "data": {"error": str(e)}}

# Example/Test
if __name__ == "__main__":
    test_input = {
        "metrics": ["pageViews"],
        "dimensions": ["date"],
        "filters": [{"field": "pagePath", "operator": "EXACT", "value": "/"}],
        "date_ranges": [{"start": "30daysAgo", "end": "today"}, {"start": "60daysAgo", "end": "31daysAgo"}],
        "property_id": "123456789",
        "order_by": {"field": "date", "desc": False}
    }
    wrapper = GA4Wrapper()
    result = wrapper.execute(test_input)
    print(json.dumps(result, indent=2, default=str))