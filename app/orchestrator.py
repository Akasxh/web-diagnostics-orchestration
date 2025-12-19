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
os.environ['LITELLM_API_KEY'] = settings.LITELLM_KEY
LITELLM_BASE_URL = settings.LITELLM_PROXY_URL

client = OpenAI(
    api_key= settings.LITELLM_KEY,
    base_url=LITELLM_BASE_URL
)

# --- Core Helper Functions ---

def classify_query(query: str, model: str = "gemini-2.5-pro") -> str:
    """
    Classifies the query into one of the taxonomy types using LLM.
    """
    print(f"Using model: {model} for classification")
    taxonomy_prompt = "\n".join([f"- {t}: {desc}" for t, desc in taxonomy.items()])
    prompt = f"""
        Classify this query based on the taxonomy:
        {taxonomy_prompt}
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



import re
import json
import time
from openai import APIError

def strip_markdown(text: str) -> str:
    """
    Removes Markdown formatting (bold, italics, code blocks, headers) 
    to return clean, human-readable plain text.
    """
    # Remove bold/italic markers (**text** or *text* or __text__)
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)  # Remove **bold**
    text = re.sub(r'__(.*?)__', r'\1', text)      # Remove __bold__
    text = re.sub(r'\*(?![ ])(.*?)\*', r'\1', text) # Remove *italic* (but not bullets)
    
    # Remove code blocks and inline code
    text = re.sub(r'```.*?```', '', text, flags=re.DOTALL) # Remove multiline code blocks
    text = re.sub(r'`(.*?)`', r'\1', text)        # Remove inline `code`
    
    # Remove headers (e.g. "## Title" -> "Title")
    text = re.sub(r'^#+\s*', '', text, flags=re.MULTILINE)
    
    # Fix lists: Ensure standard bullets become simple dashes or numbers
    text = re.sub(r'^\s*\*\s+', '- ', text, flags=re.MULTILINE)
    
    return text.strip()

def answer_agent(task_results, input_query, isJson ,plan: dict, model: str = "gemini-2.5-pro") -> str:
    """
    Final Answer Agent: 
    1. Aggregates results into valid JSON (if requested) OR clean plain text.
    2. Strips all markdown formatting for natural language outputs.
    """
    print(f"Using model: {model} for answer aggregation")
    
    # --- 1. Normalize Inputs ---
    if not task_results:
        return "No results to aggregate."

    merged_results = {}
    if isinstance(task_results, dict):
        merged_results = task_results
    elif isinstance(task_results, list):
        try:
            merged_results = {r['task_id']: r['data'] for r in task_results}
        except TypeError:
            merged_results = {i: val for i, val in enumerate(task_results)}
    else:
        merged_results = {0: str(task_results)}


    if isJson:
        # === JSON PATH ===
        print("Debug: JSON format requested. Running strict formatter...")
        
        json_prompt = f"""
        You are a JSON formatter. 
        User Query: "{input_query}"
        Data: {json.dumps(merged_results, default=str)}
        
        Task: Convert the data into a valid JSON object that answers the query.
        Rules: Output ONLY raw JSON. No markdown formatting. No code blocks.
        """
        raw_output = _call_llm_with_retry(json_prompt, model)
        
        # Clean potential markdown if LLM adds it anyway
        cleaned_json = raw_output.replace("```json", "").replace("```", "").strip()
        try:
            return json.loads(cleaned_json)
        except :
            return cleaned_json

    else:
        # === HUMAN READABLE PATH ===
        print("Debug: Natural language requested. Generating clean text...")
        
        nl_prompt = f"""
        You are a helpful assistant.
        User Query: "{input_query}"
        Data: {json.dumps(merged_results, default=str)}
        
        Instructions:
        1. Answer the query clearly using the data.
        2. STRICTLY PLAIN TEXT ONLY. 
        3. DO NOT use Markdown bolding (**), italics (*), or headers (#).
        4. Use simple indentation or dashes (-) for lists.
        5. Write exactly as a human would type in a plain text email.
        """
        
        raw_output = _call_llm_with_retry(nl_prompt, model)
        
        # Double-check safety: Strip markdown just in case
        clean_output = strip_markdown(raw_output)
        
        return f"{clean_output}"

def _call_llm_with_retry(prompt: str, model: str) -> str:
    """Helper for LLM calls with retry logic."""
    max_retries = 3
    base_delay = 1
    
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}]
            )
            return response.choices[0].message.content.strip()
        except APIError as e:
            if e.status_code == 429:
                time.sleep(base_delay * (2 ** attempt))
            else:
                raise e
    return "Error: LLM failed to respond."