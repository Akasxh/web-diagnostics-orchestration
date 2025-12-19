"""
Google Analytics Data API (GA4) wrapper.

"""

from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    RunReportRequest, DateRange, Dimension, Metric,
    FilterExpression, Filter, FilterExpressionList, OrderBy
)
import os
import json
import re
from openai import OpenAI
from app.config import get_settings

def get_ga4_client():
    """
    Returns an authenticated GA4 client.
    Because we set GOOGLE_APPLICATION_CREDENTIALS in main.py,
    this constructor automatically finds and reads the JSON file.
    """
    return BetaAnalyticsDataClient()

# ==========================================
# 1. SETUP
# ==========================================

settings = get_settings()

client = OpenAI(
    api_key= settings.LITELLM_KEY,
    base_url=settings.LITELLM_PROXY_URL
)

# ==========================================
# 2. THE INTELLIGENT LLM PARSER
# ==========================================
def get_ga4_payload(raw_input_string):
    """
    Takes ANY raw input (string, dict, natural language) and returns
    a cleaned JSON payload ready for the GA4 builder.
    """

    system_prompt = """
    You are a GA4 API Architect. Your goal is to translate user inputs (which may be natural language OR raw JSON-like strings) into a precise JSON structure for the Google Analytics Data API.

    INPUT ANALYSIS:
    - If the input is a dictionary string (e.g. {'inputs': ...}), prioritize the values inside 'inputs'.
    - If the user asks for comparisons (e.g., "preceding period", "previous 30 days"), you must generate TWO items in the "date_ranges" list.

    You must convert common names to specific GA4 API field names:
    - "page views" or "views" -> "screenPageViews"
    - "users" or "visitors"   -> "activeUsers"
    - "sessions"              -> "sessions"
    - "bounce rate"           -> "bounceRate"
    - "page" or "url"         -> "pagePath"
    - "title"                 -> "pageTitle"

    DATE LOGIC (CRITICAL):
    - "Last 30 days" -> {"start": "30daysAgo", "end": "today"}
    - "Preceding 30 days" (Comparison) -> {"start": "60daysAgo", "end": "31daysAgo"}
    - "Last week" -> {"start": "7daysAgo", "end": "today"}
    - "Previous week" -> {"start": "14daysAgo", "end": "8daysAgo"}

    REQUIRED JSON OUTPUT FORMAT:
    {
        "date_ranges": [
            {"start": "start_date_string", "end": "end_date_string"},
            {"start": "start_date_string_2", "end": "end_date_string_2"} // Optional for comparison
        ],
        "dimensions": ["dim_1", "dim_2"],
        "metrics": ["metric_1", "metric_2"],
        "filters": [
            {"field": "dim_name", "operator": "EXACT", "value": "val"}
            // Operators: EXACT, CONTAINS, BEGINS_WITH
        ],
        "order_by": {"field": "name", "desc": boolean}
    }

    NOTE: Do NOT include "property_id" in your output. It will be provided separately and will override any value you might extract.

    Return ONLY valid JSON.
    """

    try:
        response = client.chat.completions.create(
            model="gemini-2.5-pro",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": str(raw_input_string)} # Convert dict to str if needed
            ]
        )

        # Clean potential markdown wrapping
        raw = response.choices[0].message.content.strip()
        clean_json = re.sub(r"```json|```", "", raw).strip()
        return json.loads(clean_json)

    except Exception as e:
        print(f"LLM Error: {e}")
        return None

# ==========================================
# 3. THE UNIVERSAL PYTHON WRAPPER
# ==========================================
class GA4UniversalWrapper:
    def build(self, data):
        """
        Maps the cleaned LLM JSON to GA4 Objects.
        """
        property_id = data.get("property_id")
        if not property_id:
            raise ValueError("property_id is required in the data payload")

        # 1. Handle Multiple Date Ranges (Loop)
        date_ranges = []
        for dr in data.get("date_ranges", []):
            date_ranges.append(DateRange(
                start_date=dr["start"],
                end_date=dr["end"]
            ))

        # 2. Dimensions & Metrics
        dimensions = [Dimension(name=d) for d in data.get("dimensions", [])]
        metrics = [Metric(name=m) for m in data.get("metrics", [])]

        # 3. Filters
        filter_expr = self._build_filters(data.get("filters", []))

        # 4. Order By
        order_bys = []
        if data.get("order_by"):
            order_bys.append(OrderBy(
                dimension=OrderBy.DimensionOrderBy(dimension_name=data["order_by"]["field"]),
                desc=data["order_by"].get("desc", False)
            ))

        return RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=date_ranges,
            dimensions=dimensions,
            metrics=metrics,
            dimension_filter=filter_expr,
            order_bys=order_bys
        )

    def _build_filters(self, filters_list):
        if not filters_list:
            return None

        expressions = []
        for f in filters_list:
            match_type = Filter.StringFilter.MatchType.EXACT
            op = f.get('operator', 'EXACT').upper()
            if op == 'CONTAINS': match_type = Filter.StringFilter.MatchType.CONTAINS
            elif op == 'BEGINS_WITH': match_type = Filter.StringFilter.MatchType.BEGINS_WITH

            expressions.append(FilterExpression(
                filter=Filter(
                    field_name=f['field'],
                    string_filter=Filter.StringFilter(value=f['value'], match_type=match_type)
                )
            ))

        if len(expressions) > 1:
            return FilterExpression(and_group=FilterExpressionList(expressions=expressions))
        elif len(expressions) == 1:
            return expressions[0]
        return None


def run_ga4_queries(property_id, user_query):
    """
    Runs the GA4 queries.
    
    Args:
        property_id: The GA4 property ID to query (ALWAYS used, overrides any value in user_query)
        user_query: The query string/dict that will be parsed by LLM
    """
    if not property_id:
        raise ValueError("property_id is required and cannot be None or empty")
    
    client = get_ga4_client()
    llm_payload = get_ga4_payload(user_query)
    
    if not llm_payload:
        llm_payload = {}
    
    llm_payload["property_id"] = property_id
    
    builder = GA4UniversalWrapper()
    request_object = builder.build(llm_payload)
    print(request_object)
    response = client.run_report(request=request_object)
    clean_data = []
    
    for row in response.rows:
        item = {
            # Extract dimensions (e.g., date, page path)
            "dimensions": [d.value for d in row.dimension_values],
            # Extract metrics (e.g., views, active users)
            "metrics": [m.value for m in row.metric_values]
        }
        clean_data.append(item)

    # 4. Return the standard Python list
    print(clean_data)
    return {
        "status": "success",
        "data": clean_data
    }