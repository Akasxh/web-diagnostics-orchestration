# test_mock_run.py
# Mock run of full orchestration: Planner -> Tasks (GA4/SEO/Answer) -> Response.
# - Mocks: LLM (_llm_call -> fixed JSON), GA4 (fake rows), SEO (fake DF), gspread (hardcoded data).
# - Runs hybrid Tier 3 query; prints plan, results, response.
# - No real APIs; for local testing. Extend with pytest for full suite.

import sys
import os
# Add directory containing 'app' package to path so absolute imports work when running directly
# From app/agents/mock_run.py, go up to app/, then up to web-diagnostics-orchestration/
app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # app/
project_root = os.path.dirname(app_dir)  # web-diagnostics-orchestration/
sys.path.insert(0, project_root)

import json
from unittest.mock import patch, MagicMock
from langgraph.graph import StateGraph
from agent import build_orchestration_graph, OrchestrationState
from planner import plan_query
from ga4_agent import execute_ga4_task, get_ga4_client
from seo_agent import execute_seo_task, _load_seo_data
from orchestrator_agent import execute_plan, _llm_call as orch_llm_call

# Mock Data
MOCK_SEO_DF = {
    'Address': ['https://example.com/home', 'http://example.com/old', 'https://example.com/pricing'],
    'Title 1': ['Home Page', 'Old Page - Long Title Exceeding 60 Chars Here...', 'Pricing Page'],
    'Title 1 Length': [9, 65, 12],
    'Status Code': [200, 301, 200]
}
MOCK_GA4_ROWS = [
    {'pagePath': '/home', 'sessions': '1500'},
    {'pagePath': '/pricing', 'sessions': '1200'},
    {'pagePath': '/old', 'sessions': '500'}
]

# Patches
def mock_llm_call(prompt, *args, **kwargs):
    if 'Classify' in prompt:
        return 'hybrid-ga4-driven'
    if 'Decompose' in prompt:
        return json.dumps([
            {
                "id": "q0",
                "agent": "ga4",
                "desc": "Fetch top 10 pagePaths by sessions last 14 days",
                "depends_on": [],
                "inputs": {
                    "metrics": ["sessions"],
                    "dimensions": ["pagePath"],
                    "order_by": {"field": "sessions", "desc": True},
                    "limit": 10,
                    "date_ranges": [{"start": "14daysAgo", "end": "today"}]
                }
            },
            {
                "id": "q1",
                "agent": "seo",
                "desc": "Fetch title tags for top pagePaths",
                "depends_on": ["q0"],
                "inputs": {"urls": "{{q0.output.pagePaths}}", "fields": ["title"]}
            },
            {
                "id": "q2",
                "agent": "answer",
                "desc": "Summarize top pages with views and titles",
                "depends_on": ["q1"],
                "inputs": {
                    "prev_results": "{{q0.output}}, {{q1.output}}",
                    "format": "nl",
                    "prompt": "Join on paths; list top 10 with views and titles."
                }
            }
        ])
    if 'fusion' in prompt.lower():
        return "Top pages: /home (1500 views, 'Home Page'), /pricing (1200 views, 'Pricing Page'), /old (500 views, 'Old Page - Long Title...')."
    return '{"type": "invalid"}'

def mock_ga4_execute(self, payload):
    """Mock for GA4Wrapper.execute - instance method so needs self parameter."""
    # Extract pagePaths for template resolution
    page_paths = [row['pagePath'] for row in MOCK_GA4_ROWS]
    return {
        "rows": MOCK_GA4_ROWS, 
        "row_count": 3, 
        "headers": ["pagePath", "sessions"],
        "pagePaths": page_paths  # Add this for template {{q0.output.pagePaths}}
    }

def mock_seo_load():
    import pandas as pd
    return pd.DataFrame(MOCK_SEO_DF)

def mock_seo_execute(df, task, prev_data=None):
    """Mock for execute_seo_task - handles title lookup."""
    import pandas as pd
    if df.empty:
        df = pd.DataFrame(MOCK_SEO_DF)
    
    if 'title' in task.get('desc', '').lower():
        # Extract URLs from prev_data (which is resolved_inputs from orchestrator)
        urls = []
        if prev_data:
            urls_input = prev_data.get('urls', [])
            if isinstance(urls_input, list):
                urls = urls_input
            elif isinstance(urls_input, str):
                # Try to parse as JSON if it's a string
                try:
                    parsed = json.loads(urls_input)
                    if isinstance(parsed, list):
                        urls = parsed
                except:
                    # If not JSON, treat as single URL or use mock data
                    urls = [urls_input] if urls_input else ['/home', '/pricing', '/old']
        else:
            # Use pagePaths from mock GA4 data
            urls = [row['pagePath'] for row in MOCK_GA4_ROWS]
        
        # Match URLs to addresses in the DataFrame
        # Create a mapping of simplified paths to full addresses
        titles = {}
        for url in urls:
            # Normalize URL for matching
            url_clean = url.strip('/')
            for _, row in df.iterrows():
                address = str(row.get('Address', ''))
                address_clean = address.replace('https://', '').replace('http://', '').strip('/')
                # Match if URL is in address or vice versa
                if url_clean in address_clean or address_clean.endswith(url_clean):
                    titles[url] = row.get('Title 1', '')
                    break
            # If no match found, use a default
            if url not in titles:
                titles[url] = f"Title for {url}"
        
        return {"task_id": task['id'], "data": {"titles": titles, "found": len(titles), "urls": urls}}
    return {"task_id": task['id'], "data": {"error": "Mock SEO error"}}

@patch('planner._llm_call', mock_llm_call)
@patch('seo_agent._llm_call', mock_llm_call)
@patch('seo_agent._load_seo_data', mock_seo_load)
@patch('seo_agent.execute_seo_task', mock_seo_execute)
@patch('orchestrator_agent._llm_call', mock_llm_call)
@patch('orchestrator_agent._load_seo_data', mock_seo_load)  # Patch where it's used
@patch('orchestrator_agent.execute_seo_task', mock_seo_execute)  # Patch where it's used
@patch('ga4_agent.GA4Wrapper.execute', mock_ga4_execute)
def test_mock_hybrid_run():
    # Plan
    query = "Top 10 pages by views last 14 days with their title tags?"
    property_id = "123456789"
    plan = plan_query(query, property_id)
    print("Mock Plan:", json.dumps(plan, indent=2))

    # Execute via graph
    graph = build_orchestration_graph()
    initial = {
        "query": query,
        "property_id": property_id,
        "plans": {},
        "task_results": [],
        "response": ""
    }
    result = graph.invoke(initial)
    print("Mock Results:", json.dumps(result['task_results'], indent=2))
    print("Mock Response:", result['response'])

if __name__ == "__main__":
    test_mock_hybrid_run()