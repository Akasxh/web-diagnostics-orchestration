# planner.py (No changes needed; already injects property_id correctly)
"""
planner.py: Optimized query planning module.
Focus: Single entry point `plan_query(query: str, property_id: str = None) -> dict`.
Internally: Loads taxonomy, classifies query type, decomposes into tasks with dependencies, builds plan.
- Enhanced: Few-shot prompting for smart decomposition; task IDs as 'q0', 'q1', ...; LLM-specified depends_on for logical sequencing.
- Dependencies: Non-linear (parallel where possible); final 'answer' task included in decomposition.
- Removed: Linear assumption, manual final task addition; now LLM infers format, prompts, refs like {{q0.output}}.
- Edge Cases: Empty/invalid query -> {'type': 'invalid', 'tasks': []}; No taxonomy load -> built-in fallback.
- Modular: All LLM calls wrapped in reusable `_llm_call` with retries; taxonomy loaded once at module init.
- Usage: Returns plan dict for LangGraph state (e.g., state['plans'] = plan['tasks'] for routing).
"""
import os
import sys
import json
from typing import Dict, List, Any
# Add directory containing 'app' package to path so absolute imports work when running directly
# From app/agents/planner.py, go up to app/, then up to web-diagnostics-orchestration/
app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # app/
project_root = os.path.dirname(app_dir)  # web-diagnostics-orchestration/
sys.path.insert(0, project_root)
from openai import OpenAI, APIError
from app.config import get_settings  # Assume this exists; adjust if needed
settings = get_settings()

# Built-in fallback taxonomy (expand as needed)
TAXONOMY = {
    "single-ga4-retrieval": "Direct GA4 data fetch (metrics, dimensions, filters).",
    "single-ga4-analysis": "GA4 fetch + aggregations/trends/insights.",
    "single-seo-retrieval": "Direct SEO data fetch (filters, lists from spreadsheet).",
    "single-seo-analysis": "SEO fetch + groupings/percentages/assessments.",
    "hybrid-ga4-driven": "Starts with GA4, enriches with SEO.",
    "hybrid-seo-driven": "Starts with SEO, enriches with GA4.",
    "hybrid-insight": "Cross-domain with added analysis/risks.",
    "invalid": "Doesn't match domains.",
}

# Load extended taxonomy from JSON at module init (one-time)
def _load_taxonomy(filepath: str) -> None:
    if not os.path.exists(filepath):
        print(f"[planner] Taxonomy file '{filepath}' not found. Using built-in only.")
        return
    try:
        with open(filepath, "r") as f:
            data = json.load(f)
        if isinstance(data, dict) and "agents" in data:
            for agent in data["agents"]:
                for t in agent.get("taxonomies", []):
                    ttype = t.get("type")
                    if ttype:
                        TAXONOMY[ttype] = t.get("description", TAXONOMY.get(ttype, ""))
        print(f"[planner] Loaded/extended taxonomy: {len(TAXONOMY)} types.")
    except Exception as e:
        print(f"[planner] Taxonomy load failed: {e}. Using built-in.")

_load_taxonomy(settings.AGENT_TAXONOMY_PATH)

# LLM Client Setup (configurable; assumes LiteLLM proxy)
os.environ['LITELLM_API_KEY'] = os.getenv('LITELLM_API_KEY', 'sk-Mh6Ytmir4rdFDFmxzk46KA')  # Secure via env
LITELLM_BASE_URL = os.getenv('LITELLM_BASE_URL', 'http://3.110.18.218')
client = OpenAI(api_key=os.environ['LITELLM_API_KEY'], base_url=LITELLM_BASE_URL)

# Reusable LLM caller with retries and fallbacks
def _llm_call(prompt: str, model: str = "gemini-2.5-pro", max_retries: int = 3) -> str:
    """Internal: Call LLM with exponential backoff, fallback model on invalid."""
    base_delay = 1
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1  # Low temp for consistency
            )
            return response.choices[0].message.content.strip()
        except APIError as e:
            if e.status_code == 429:  # Rate limit
                wait_time = base_delay * (2 ** attempt)
                print(f"[planner] Rate limited (attempt {attempt+1}). Waiting {wait_time}s...")
                import time
                time.sleep(wait_time)
                continue
            elif e.status_code == 400 and 'invalid model' in str(e).lower():
                print(f"[planner] Invalid model '{model}'. Falling back to 'gemini-2.5-flash'.")
                return _llm_call(prompt, model="gemini-2.5-flash", max_retries=1)  # No retry on fallback
            else:
                raise e
    raise ValueError(f"[planner] LLM call failed after {max_retries} retries.")

# Core Functions (internal; called by plan_query)
def _classify_query(query: str, model: str = "gemini-2.5-pro") -> str:
    """Classify query into taxonomy type."""
    if not query or not query.strip():
        return "invalid"
   
    taxonomy_str = "\n".join([f"- {k}: {v}" for k, v in TAXONOMY.items()])
    prompt = f"""
Classify this query based on the taxonomy:
{taxonomy_str}
Query: {query}
Output ONLY the type as a single string (e.g., 'single-ga4-retrieval').
"""
    result = _llm_call(prompt, model)
    # Validate: Must match a key or default to 'invalid'
    result = result.strip().lower().replace(" ", "-")  # Normalize (e.g., 'single ga4 retrieval' -> 'single-ga4-retrieval')
    return TAXONOMY.get(result, "invalid")

def _decompose_query(query: str, query_type: str, model: str = "gemini-2.5-pro") -> List[Dict[str, Any]]:
    """Decompose into atomic tasks with dependencies; include final 'answer' task; validate JSON output."""
    if query_type == "invalid":
        return []
   
    format_hint = "json" if "json" in query.lower() else "nl"
    prompt = f"""
Decompose this query into minimal atomic tasks based on type '{query_type}'.
- Label tasks sequentially as 'q0', 'q1', 'q2', ... where each (except final) is a single agent action ('ga4' or 'seo').
- Specify dependencies: For each task, include 'depends_on': list of prior task IDs it waits for (e.g., ['q0']); [] if independent/parallel.
- Reference outputs in 'inputs': Use {{q0.output.field}} for data from deps (e.g., {{q0.output.pagePaths}}).
- Always end with a final 'answer' task: agent='answer', depends_on all data tasks it aggregates, inputs include {{dep.output}} refs, 'format': '{format_hint}', and 'prompt': brief aggregation instruction.
- Infer params: metrics (e.g., 'sessions'), dimensions (e.g., 'pagePath'), filters, date_ranges (e.g., 'last 14 days').
- Tasks can be parallel if independent; minimize tasks; aggregate only in 'answer'.

Output ONLY valid JSON: List of dicts [{{"id": "q0", "agent": "ga4/seo/answer", "desc": "brief desc", "depends_on": ["q0"] or [], "inputs": {{"param": "value", ...}}}}]
No extra text.

Example 1 (single-ga4-retrieval, nl format):
Query: "Top 10 pages by views last 14 days"
[
  {{"id": "q0", "agent": "ga4", "desc": "Fetch top 10 pagePaths by sessions last 14 days", "depends_on": [], "inputs": {{"metrics": ["sessions"], "dimensions": ["pagePath"], "orderBy": [{{"metric": "sessions", "desc": true}}], "limit": 10, "date_range": "last 14 days"}}}},
  {{"id": "q1", "agent": "answer", "desc": "List top pages with views", "depends_on": ["q0"], "inputs": {{"prev_results": "{{q0.output}}", "format": "nl", "prompt": "List top 10 pages by sessions with paths and counts."}}}}
]

Example 2 (hybrid-ga4-driven, nl format):
Query: "Top 10 pages by views last 14 days and their title tags?"
[
  {{"id": "q0", "agent": "ga4", "desc": "Fetch top 10 pagePaths by sessions last 14 days", "depends_on": [], "inputs": {{"metrics": ["sessions"], "dimensions": ["pagePath"], "orderBy": [{{"metric": "sessions", "desc": true}}], "limit": 10, "date_range": "last 14 days"}}}},
  {{"id": "q1", "agent": "seo", "desc": "Fetch title tags for top pagePaths", "depends_on": ["q0"], "inputs": {{"urls": "{{q0.output.pagePaths}}", "fields": ["title"]}}}},
  {{"id": "q2", "agent": "answer", "desc": "Summarize top pages with views and titles", "depends_on": ["q1"], "inputs": {{"prev_results": "{{q0.output}}, {{q1.output}}", "format": "nl", "prompt": "Join on paths; list top 10 with views and titles; note missing titles."}}}}
]

Example 3 (hybrid-insight, parallel possible, json format):
Query: "JSON of all pages' views and SEO issues last month"
[
  {{"id": "q0", "agent": "ga4", "desc": "Fetch all pagePaths with sessions last month", "depends_on": [], "inputs": {{"metrics": ["sessions"], "dimensions": ["pagePath"], "date_range": "last 30 days"}}}},
  {{"id": "q1", "agent": "seo", "desc": "Fetch SEO issues for all pagePaths", "depends_on": [], "inputs": {{"fields": ["title", "issues"], "all_urls": true}}}},
  {{"id": "q2", "agent": "answer", "desc": "Join views and issues as JSON", "depends_on": ["q0", "q1"], "inputs": {{"prev_results": "{{q0.output}}, {{q1.output}}", "format": "json", "prompt": "Join on pagePath; output dict of paths: {{views: int, issues: list}}."}}}}
]

Query: {query}
"""
    max_json_tries = 3
    for _ in range(max_json_tries):
        content = _llm_call(prompt, model)
        if not content:
            continue
        try:
            tasks = json.loads(content)
            if isinstance(tasks, list) and all("id" in t and "agent" in t and "depends_on" in t for t in tasks):
                # Ensure final is 'answer'
                if tasks and tasks[-1]["agent"] != "answer":
                    continue
                return tasks
        except json.JSONDecodeError:
            prompt += "\nOutput ONLY valid JSON list—no wrappers, no explanations."
    raise ValueError("[planner] Failed to parse decomposition JSON after retries.")

# Main Entry Point
def plan_query(query: str, property_id: str = None, model: str = "gemini-2.5-pro") -> Dict[str, Any]:
    """
    Single callable: Generates plan dict.
    Returns: {'type': str, 'tasks': list[dict], 'dependencies': dict[str, list[str]], 'output_format': str}
    - For LangGraph: Use plan['tasks'] for routing; build graph from 'dependencies' (id -> list of deps to wait for).
    - Edge: Invalid/empty -> {'type': 'invalid', 'tasks': [], ...}; Handles property_id injection.
    """
    print(f"[planner] Planning query: {query[:50]}...")  # Trunc for logs
   
    query_type = _classify_query(query, model)
    if query_type == "invalid":
        return {"type": "invalid", "tasks": [], "dependencies": {}, "output_format": "nl"}
   
    tasks = _decompose_query(query, query_type, model)
    if not tasks:
        return {"type": query_type, "tasks": [], "dependencies": {}, "output_format": "nl"}
   
    # Build dependencies dict from tasks
    dependencies = {task["id"]: task["depends_on"] for task in tasks}
   
    # Infer output format from final task
    output_format = tasks[-1]["inputs"].get("format", "nl") if tasks else "nl"
   
    # Inject property_id for GA4 tasks
    if property_id:
        for task in tasks:
            if task["agent"] == "ga4" and "inputs" in task:
                task["inputs"]["property_id"] = property_id
   
    plan = {
        "type": query_type,
        "tasks": tasks,
        "dependencies": dependencies,
        "output_format": output_format
    }
   
    print(f"[planner] Generated plan: {query_type} with {len(tasks)} tasks.")
    return plan

# Usage Example (for testing)
if __name__ == "__main__":
    test_query = "What are the top 10 pages by views in the last 14 days, and what are their corresponding title tags?"
    plan = plan_query(test_query, property_id="123456789")
    print(json.dumps(plan, indent=2, default=str))  # Safe dump for dicts