"""
Google Analytics Data API (GA4) wrapper.

"""

from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import RunReportRequest
from google.analytics.data_v1beta.types import RunRealtimeReportRequest

from google.analytics.data_v1beta.types import (
    RunReportRequest, RunRealtimeReportRequest,
    FilterExpression, Filter, OrderBy, DateRange, Metric, Dimension
)

import os
import json
import re
from openai import OpenAI
from google.analytics.data_v1beta.types import (
    RunReportRequest, DateRange, Dimension, Metric,
    FilterExpression, Filter, FilterExpressionList, OrderBy
)

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
os.environ['LITELLM_API_KEY'] = 'sk-Mh6Ytmir4rdFDFmxzk46KA'
LITELLM_BASE_URL = 'http://3.110.18.218'

client = OpenAI(
    api_key=os.environ['LITELLM_API_KEY'],
    base_url=LITELLM_BASE_URL
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

    DATE LOGIC (CRITICAL):
    - "Last 30 days" -> {"start": "30daysAgo", "end": "today"}
    - "Preceding 30 days" (Comparison) -> {"start": "60daysAgo", "end": "31daysAgo"}
    - "Last week" -> {"start": "7daysAgo", "end": "today"}
    - "Previous week" -> {"start": "14daysAgo", "end": "8daysAgo"}

    REQUIRED JSON OUTPUT FORMAT:
    {
        "property_id": "extracted_id_or_null",
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
            # Fallback if LLM didn't find it in the text, use a default or raise error
            property_id = "0"

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


# ==========================================
# 4. EXECUTION
# ==========================================

# Your problematic query (passed as a raw string or dict)
bad_user_query = "{'id': 1, 'agent': 'ga4', 'desc': 'Fetch daily page views for the homepage for the last 30 days and the preceding 30-day period.', 'inputs': {'metrics': 'pageViews', 'dimensions': 'date', 'filters': 'pagePath=/', 'date_ranges': ['last 30 days', 'previous 30 days'], 'property_id': '123456789'}"

print("... processing query ...")

# Step 1: LLM structures the data and solves date math
llm_payload = get_ga4_payload(bad_user_query)
print("\n[LLM Interpreted Payload]:")
print(json.dumps(llm_payload, indent=2))

# Step 2: Wrapper builds the object
builder = GA4UniversalWrapper()
request_object = builder.build(llm_payload)

print("\n[Final GA4 Request Object]:")
print(request_object)

# Validation of the critical fix:
print(f"\n[Validation] Number of Date Ranges: {len(request_object.date_ranges)}")
print(f"Range 1: {request_object.date_ranges[0].start_date} to {request_object.date_ranges[0].end_date}")
if len(request_object.date_ranges) > 1:
    print(f"Range 2: {request_object.date_ranges[1].start_date} to {request_object.date_ranges[1].end_date}")

def run_ga4_queries(property_id, request_object):
    """
    Runs the GA4 queries.
    """
    client = get_ga4_client()
    response = client.run_report(request=request_object)
    return response

response = run_ga4_queries(property_id, request_object)
print(json.dumps(response.to_dict(), indent=2))