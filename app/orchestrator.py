"""
Tier 3: Intent detection & routing orchestrator.

This module wires together the various agents and services.

"""
import os
import json
import time
from openai import OpenAI, APIError
from app.config import get_settings

settings = get_settings()

taxonomy = {
    "hybrid-insight": "Cross-domain with added analysis/risks.",
    "invalid": "Doesn't match domains",
}

# Populate taxonomy from agent_taxonomy.json structure if file is available,
# mapping taxonomy type -> description for all agents.
def load_agent_taxonomy(filepath):
    """
    Loads agent taxonomies from a JSON file into a flat taxonomy dict:
      taxonomy[type] = description
    """
    global taxonomy
    if not os.path.exists(filepath):
        print(f"[taxonomy] JSON file '{filepath}' not found. Using built-in taxonomy only.")
        return
    try:
        with open(filepath, "r") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "agents" not in data:
            print(f"[taxonomy] Invalid agent taxonomy structure in '{filepath}'")
            return
        for agent in data["agents"]:
            for t in agent.get("taxonomies", []):
                ttype = t.get("type")
                desc = t.get("description", "")
                if ttype:
                    taxonomy[ttype] = desc
        print(f"[taxonomy] Loaded {len(taxonomy)} taxonomy mappings from '{filepath}'")
    except Exception as e:
        print(f"[taxonomy] Failed to load taxonomy: {e}")

# Replace standard loader with the above
TAXONOMY_JSON_PATH = settings.AGENT_TAXONOMY_PATH
load_agent_taxonomy(TAXONOMY_JSON_PATH)



# --- Configuration & Setup ---
os.environ['LITELLM_API_KEY'] = 'sk-Mh6Ytmir4rdFDFmxzk46KA'
LITELLM_BASE_URL = 'http://3.110.18.218'

client = OpenAI(
    api_key=os.environ['LITELLM_API_KEY'],
    base_url=LITELLM_BASE_URL
)

# --- Core Helper Functions ---

def classify_query(query: str, model: str = "gemini-2.5-pro") -> str:
    """
    Classifies the query into one of the taxonomy types using LLM.
    """
    print(f"Using model: {model} for classification")
    prompt = f"""
Classify this query based on the taxonomy:
- single-ga4-retrieval: Direct GA4 data fetch (metrics, dimensions, filters).
- single-ga4-analysis: GA4 fetch + aggregations/trends/insights.
- single-seo-retrieval: Direct SEO data fetch (filters, lists from spreadsheet).
- single-seo-analysis: SEO fetch + groupings/percentages/assessments.
- hybrid-ga4-driven: Starts with GA4, enriches with SEO.
- hybrid-seo-driven: Starts with SEO, enriches with GA4.
- hybrid-insight: Cross-domain with added analysis/risks.
- invalid: Doesn't match domains.

Query: {query}

Output only the type as a single string.
"""
    max_retries = 5
    base_delay = 1
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}]
            )
            query_type = response.choices[0].message.content.strip()
            print(f"Debug: Classified as '{query_type}'")
            return query_type
        except APIError as e:
            if e.status_code == 429:
                wait_time = base_delay * (2 ** attempt)
                print(f"Rate limited. Retrying in {wait_time}s...")
                time.sleep(wait_time)
            elif e.status_code == 400 and 'invalid model' in str(e).lower():
                print("Invalid model detected. Falling back to gemini-2.5-flash.")
                return classify_query(query, model="gemini-2.5-flash")
            else:
                raise e
    raise ValueError("Failed after retries.")

def decompose_query(query: str, query_type: str, model: str = "gemini-2.5-pro") -> list[dict]:
    """
    Decomposes the query into atomic tasks based on the type.
    """
    print(f"Using model: {model} for decomposition")
    if query_type == 'invalid':
        return []

    prompt = f"""
Decompose this query into atomic tasks based on type '{query_type}'.
Each task is a single agent action ('ga4' or 'seo').
For single types: 1-3 tasks (e.g., fetch data, compute if needed, explain).
For hybrid types: 2+ tasks with implied dependencies (e.g., fetch from ga4, then enrich with seo using outputs like URLs).
Infer parameters like metrics (e.g., 'pageViews'), dimensions (e.g., 'date'), filters (e.g., 'pagePath=/pricing'), date_ranges (e.g., 'last 14 days').

Output ONLY the JSON list, nothing else—no explanations, no code blocks, no extra text. Must be valid JSON.
Schema: A list of dicts like [{{"id": integer starting from 1, "agent": "ga4" or "seo", "desc": "brief description", "inputs": {{"param1": "value1", ...}}}}]

Example for query "Top 10 pages by views last week and titles" with type 'hybrid-ga4-driven':
[{{"id":1, "agent":"ga4", "desc":"Fetch top 10 pages by page views last 7 days", "inputs":{{"metrics":"pageViews", "dimensions":"pagePath", "date_range":"last 7 days", "order_by":"pageViews desc", "limit":10}}}}, {{"id":2, "agent":"seo", "desc":"Lookup title tags for given URLs", "inputs":{{"urls":"from task 1 output"}}}}]

Query: {query}
"""
    max_retries = 5
    base_delay = 1
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}]
            )
            content = response.choices[0].message.content.strip()
            if not content:
                print("Debug: Empty LLM output—retrying with fix prompt.")
                prompt += "\nIf previous output was empty, ensure you output ONLY valid JSON as specified."
                continue
            tasks = json.loads(content)
            print(f"Debug: Decomposed into {len(tasks)} tasks")
            return tasks
        except APIError as e:
            if e.status_code == 429:
                wait_time = base_delay * (2 ** attempt)
                print(f"Rate limited. Retrying in {wait_time}s...")
                time.sleep(wait_time)
            elif e.status_code == 400 and 'invalid model' in str(e).lower():
                print("Invalid model detected. Falling back to gemini-2.5-flash.")
                return decompose_query(query, query_type, model="gemini-2.5-flash")
            else:
                raise e
        except json.JSONDecodeError:
            print("Debug: Invalid JSON—retrying with fix prompt.")
            prompt += "\nPrevious output was not valid JSON. Output ONLY the exact JSON list format, no wrappers."
    raise ValueError("Failed after retries—check LLM prompt or model.")

def generate_plan(query: str, property_id: str = None, model: str = "gemini-2.5-pro") -> dict:
    """
    Generates full plan: Classify, decompose, add dependencies/aggregation.
    """
    print(f"Using model: {model} for plan generation")
    query_type = classify_query(query, model)
    tasks = decompose_query(query, query_type, model)

    dependencies = {task['id']: [task['id'] - 1] for task in tasks if task['id'] > 1}

    output_format = 'json' if 'json' in query.lower() else 'nl'

    aggregation_prompt = "Fuse results: Join on URLs, summarize trends/insights."

    if 'single' in query_type:
        aggregation_prompt = "Aggregate and explain in natural language."

    next_id = len(tasks) + 1
    tasks.append({
        'id': next_id,
        'agent': 'answer',
        'desc': f'Format final aggregated results as {"strict JSON" if output_format == "json" else "natural language"}',
        'inputs': {
            'results': 'from previous tasks',
            'format': output_format,
            'prompt': aggregation_prompt
        }
    })
    dependencies[next_id] = [next_id - 1]

    if property_id:
        for task in tasks:
            if task['agent'] == 'ga4':
                task['inputs']['property_id'] = property_id

    plan = {
        'type': query_type,
        'tasks': tasks,
        'dependencies': dependencies,
        'output_format': output_format,
        'aggregation_prompt': aggregation_prompt
    }
    
    print(f"Debug: Generated plan with {len(tasks)} tasks (includes final answer)")
    return plan

"""{
'type': 'single-seo-analysis', 
'tasks': [
    {'id': 1, 'agent': 'seo', 'desc': 'Fetch the distribution of HTTP status codes from the last site crawl.', 'inputs': {'analysis': 'status_codes'}}, 
    {'id': 2, 'agent': 'seo', 'desc': 'Calculate the percentage of 200 (OK) and 301 (Moved Permanently) status codes from the total and present the result.', 'inputs': {'status_code_distribution': 'from task 1 output'}}, 
    {'id': 3, 'agent': 'answer', 'desc': 'Format final aggregated results as natural language', 'inputs': {'results': 'from previous tasks', 'format': 'nl', 'prompt': 'Aggregate and explain in natural language.'}}], 
'dependencies': {2: [1], 3: [2]}, 
'output_format': 'nl', 
'aggregation_prompt': 'Aggregate and explain in natural language.'
}"""



def answer_agent(task_results: list[dict], plan: dict, model: str = "gemini-2.5-pro") -> str:
    """
    Final Answer Agent: Aggregates task results, applies fusion/explanation, formats per plan.
    """
    print(f"Using model: {model} for answer aggregation")
    if not task_results:
        return "No results to aggregate."

    merged_results = {result['task_id']: result['data'] for result in task_results}
    print(f"Debug: Merging {len(merged_results)} results")

    fusion_prompt = f"""
Aggregate these results: {merged_results}
Use this prompt: {plan['aggregation_prompt']}
Output the fused data only—no intro text.
"""
    max_retries = 3
    base_delay = 1
    fused_data = ""
    
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": fusion_prompt}]
            )
            fused_data = response.choices[0].message.content.strip()
            break
        except APIError as e:
            if e.status_code == 429:
                wait_time = base_delay * (2 ** attempt)
                print(f"Rate limited in answer agent. Retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                raise e
    else:
        fused_data = str(merged_results)

    if plan['output_format'] == 'json':
        try:
            if not fused_data.startswith('{') and not fused_data.startswith('['):
                fused_data = json.dumps({'aggregated_results': fused_data})
            final_response = json.dumps(json.loads(fused_data), indent=2)
        except (json.JSONDecodeError, ValueError):
            final_response = json.dumps({'error': 'Failed to format as JSON', 'raw': fused_data})
    else:
        final_response = f"Final Answer: {fused_data}"

    return final_response

# --- Execution Simulation ---

def mock_execution_layer(plan: dict) -> list[dict]:
    """
    Simulates the execution of GA4 and SEO tools by returning the hardcoded mock data
    provided in the source ipynb. This allows the pipeline to run end-to-end.
    """
    print("Debug: Executing tasks (MOCK MODE)...")
    
    # These are the exact hardcoded values from your 'mock_results' variable
    mock_results = [
        {'task_id': 1, 'data': {'pages': ['/home', '/pricing'], 'views': [1000, 800]}},  # From GA4
        {'task_id': 2, 'data': {'titles': {'/home': 'Home Page', '/pricing': 'Pricing Plan'}}},  # From SEO
        # task_id 3 is usually the 'answer' agent, which doesn't produce data for itself
    ]
    
    # We filter results to only return data for tasks that exist in the plan (excluding the final answer task)
    relevant_ids = [t['id'] for t in plan['tasks'] if t['agent'] != 'answer']
    
    # Simple logic: just return the mock data for the first N tasks required
    # In a real scenario, this would loop through plan['tasks'] and call actual tools
    execution_outputs = []
    for i, t_id in enumerate(relevant_ids):
        if i < len(mock_results):
            execution_outputs.append(mock_results[i])
            
    return execution_outputs

# --- Main Entry Point ---

def run_agent_pipeline(query: str, property_id: str = None):
    """
    The main function that takes the user query, orchestrates the plan,
    executes (mocks) the tools, and generates the final output.
    """
    print(f"\n--- Starting Pipeline for Query: {query} ---")
    
    # 1. Generate Plan (Classify -> Decompose -> Structure)
    plan = generate_plan(query, property_id)
    
    # 2. Execute Tasks (using Mock Layer to provide the hardcoded data)
    task_results = mock_execution_layer(plan)
    
    # 3. Aggregate and Answer
    final_output = answer_agent(task_results, plan)
    
    return final_output

# --- Usage Example ---

if __name__ == "__main__":
    # Example usage with the specific test case from your notebook
    test_query = "What are the top 10 pages by views in the last 14 days, and what are their corresponding title tags?"
    test_property_id = "123456789"
    
    result = run_agent_pipeline(test_query, test_property_id)
    print("\nFINAL OUTPUT:")
    print(result)