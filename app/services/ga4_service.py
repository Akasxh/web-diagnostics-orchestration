"""
Google Analytics Data API (GA4) wrapper.

This file is a stub; wire it up to the real GA4 Data API as needed.
"""

from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import RunReportRequest
from google.analytics.data_v1beta.types import RunRealtimeReportRequest

def get_ga4_client():
    """
    Returns an authenticated GA4 client.
    Because we set GOOGLE_APPLICATION_CREDENTIALS in main.py,
    this constructor automatically finds and reads the JSON file.
    """
    return BetaAnalyticsDataClient()


def run_ga4_queries(property_id):

    """
    Queries the GA4 Realtime API.
    Shows who is on the site RIGHT NOW (last 30 minutes).
    """
    client = BetaAnalyticsDataClient()

    print(f"--- 📡 Checking Realtime Data for: {property_id} ---")

    request = RunRealtimeReportRequest(
        property=f"properties/{property_id}",
        # Realtime does NOT use date_ranges. It is fixed to the last 30 minutes.
        dimensions=[
            {"name": "unifiedScreenName"},  # The name of the page/screen they are on
            {"name": "country"},            # Where they are from
        ],
        metrics=[
            {"name": "activeUsers"}         # How many people
        ]
    )

    response = client.run_realtime_report(request=request)

    if not response.rows:
        print("❌ RESULT: No active users in the last 30 minutes.")
        print("   -> Go visit your website in a new tab and run this script again immediately.")
        return

    print(f"{'Page / Screen Name':<30} | {'Country':<15} | {'Active Users':<10}")
    print("-" * 65)

    for row in response.rows:
        screen_name = row.dimension_values[0].value
        country = row.dimension_values[1].value
        users = row.metric_values[0].value
        
        print(f"{screen_name:<30} | {country:<15} | {users:<10}")

# Usage:
# run_realtime_check('YOUR_PROPERTY_ID')