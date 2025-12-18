# test_mock_run.py
# Mock run of full orchestration: Planner -> Tasks (GA4/SEO/Answer) -> Response.
# - Mocks: LLM (_llm_call -> fixed JSON), GA4 (fake rows), SEO (fake DF), gspread (hardcoded data).
# - Runs hybrid Tier 3 query; prints plan, results, response.
# - No real APIs; for local testing. Extend with pytest for full suite.

import sys
import os
import json
import pandas as pd
from unittest.mock import patch

# Add directory containing 'app' package to path so absolute imports work when running directly
# From app/agents/mock_run.py, go up to app/, then up to web-diagnostics-orchestration/
app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # app/
project_root = os.path.dirname(app_dir)  # web-diagnostics-orchestration/
sys.path.insert(0, project_root)

# Import your agent modules (Ensure these paths match your project structure)
from agent import build_orchestration_graph
from planner import plan_query

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

# --- Mock Functions ---

def mock_llm_call(prompt, *args, **kwargs):
    """
    Mocks the LLM response based on keywords found in the prompt.
    """
    prompt_lower = prompt.lower()

    # 1. Classification Step
    if 'classify' in prompt_lower:
        return 'hybrid-ga4-driven'

    # 2. Decomposition (Planning) Step
    if 'decompose' in prompt_lower:
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

    # 3. Final Answer / Summarization Step
    # FIX: Updated to check for 'summarize' or 'join', which appear in Task q2 inputs
    if 'summarize' in prompt_lower or 'join' in prompt_lower:
        return "Top pages: /home (1500 views, 'Home Page'), /pricing (1200 views, 'Pricing Page'), /old (500 views, 'Old Page - Long Title...')."
    
    # Fallback for debugging
    print(f"DEBUG: Unmatched Prompt: {prompt[:50]}...") 
    return '{"type": "invalid"}'

def mock_ga4_execute(self, payload):
    """Mock for GA4Wrapper.execute."""
    page_paths = [row['pagePath'] for row in MOCK_GA4_ROWS]
    return {
        "rows": MOCK_GA4_ROWS, 
        "row_count": 3, 
        "headers": ["pagePath", "sessions"],
        "pagePaths": page_paths 
    }

def mock_seo_load():
    """Mock loading SEO data from GSpread/CSV."""
    return pd.DataFrame(MOCK_SEO_DF)

def mock_seo_execute(df, task, prev_data=None):
    """Mock for execute_seo_task."""
    if df.empty:
        df = pd.DataFrame(MOCK_SEO_DF)
    
    # Logic to mock the 'title' lookup
    if 'title' in task.get('desc', '').lower():
        urls = []
        # Resolve URLs from previous input
        if prev_data:
            urls_input = prev_data.get('urls', [])
            if isinstance(urls_input, list):
                urls = urls_input
            elif isinstance(urls_input, str):
                try:
                    parsed = json.loads(urls_input)
                    if isinstance(parsed, list):
                        urls = parsed
                except:
                    urls = [urls_input] if urls_input else ['/home', '/pricing', '/old']
        else:
            urls = [row['pagePath'] for row in MOCK_GA4_ROWS]
        
        # Simple matching logic
        titles = {}
        for url in urls:
            url_clean = url.strip('/')
            found = False
            for _, row in df.iterrows():
                address = str(row.get('Address', ''))
                address_clean = address.replace('https://', '').replace('http://', '').strip('/')
                
                if url_clean in address_clean or address_clean.endswith(url_clean):
                    titles[url] = row.get('Title 1', '')
                    found = True
                    break
            if not found:
                titles[url] = f"Title for {url}"
        
        return {"task_id": task['id'], "data": {"titles": titles, "found": len(titles), "urls": urls}}
    
    return {"task_id": task['id'], "data": {"error": "Mock SEO error"}}

# --- Test Execution with Patches ---

@patch('planner._llm_call', side_effect=mock_llm_call)
@patch('seo_agent._llm_call', side_effect=mock_llm_call)
@patch('seo_agent._load_seo_data', side_effect=mock_seo_load)
@patch('seo_agent.execute_seo_task', side_effect=mock_seo_execute)
@patch('orchestrator_agent._llm_call', side_effect=mock_llm_call)
@patch('orchestrator_agent._load_seo_data', side_effect=mock_seo_load)
@patch('orchestrator_agent.execute_seo_task', side_effect=mock_seo_execute)
@patch('ga4_agent.GA4Wrapper.execute', side_effect=mock_ga4_execute)
def test_mock_hybrid_run(mock_ga4, mock_orch_seo_exec, mock_orch_seo_load, mock_orch_llm, 
                         mock_seo_exec, mock_seo_load, mock_seo_llm, mock_planner_llm):
    
    # Define the inputs
    query = "Top 10 pages by views last 14 days with their title tags?"
    property_id = "123456789"

    print("--- 1. Generating Plan ---")
    plan = plan_query(query, property_id)
    print("Mock Plan:", json.dumps(plan, indent=2))

    print("\n--- 2. Executing Graph ---")
    graph = build_orchestration_graph()
    initial = {
        "query": query,
        "property_id": property_id,
        "plans": {},
        "task_results": [],
        "response": ""
    }
    
    # Run the graph
    result = graph.invoke(initial)
    
    print("\n--- 3. Final Outputs ---")
    print("Mock Results:", json.dumps(result['task_results'], indent=2))
    print("Mock Response:", result['response'])

if __name__ == "__main__":
    test_mock_hybrid_run()