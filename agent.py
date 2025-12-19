"""
Multi-Agent Orchestration with LangGraph: Planner -> Tasks (dep-aware) -> Answer.

- Linear graph: query_agent (plan) -> orchestrator (validate) -> task_executor (execute_plan) -> END.
- Handles Tier 1-3: GA4/SEO single/hybrid; property_id from input.
- State: Accumulates task_results; final response from answer task.
- Edges: Invalid -> response; empty data graceful.
"""

from typing import Dict, Any, TypedDict, List

from langgraph.graph import StateGraph, END

from app.orchestrator import generate_plan, answer_agent
from app.config import get_settings

settings = get_settings()

# State definition for LangGraph
class OrchestrationState(TypedDict):
    property_id: str
    query: str
    property_id: str  # From input
    plans: Dict[str, Any]  # Full plan from planner
    task_results: List[Dict[str, Any]]
    response: str
    isJson : bool

def if_seo_only(state: OrchestrationState) -> bool:
    """
    Condition to check if the query is SEO-only based on the plan type.
    """
    return 0 if state[property_id] else 1

# Nodes
def query_agent(state: OrchestrationState) -> OrchestrationState:
    """
    Node 1: Plan the query by classifying and decomposing it into tasks.
    Uses generate_plan from app.orchestrator.
    """
    query = state['query']
    property_id = state.get('property_id', settings.GA4_PROPERTY_ID)
    
    if not query.strip():
        return {
            "plans": {"type": "invalid", "tasks": [], "dependencies": {}, "output_format": "nl"},
            "task_results": [],
            "response": "Error: Empty query."
        }
    
    plans = generate_plan(query, property_id=property_id)
    return {"plans": plans, "task_results": [], "property_id": property_id}


def orchestrator_node(state: OrchestrationState) -> OrchestrationState:
    """
    Node 2: Validate the plan and handle invalid queries.
    """
    plan_type = state['plans'].get('type', 'unknown')
    print(f"[Orchestrator] Plan type: {plan_type}")
    
    if plan_type == 'invalid':
        state['response'] = "Invalid query type. Please ask about GA4 analytics or SEO data."
    return state


import copy
import json
# agent.py

# ... imports ...

def execute_task(task, agent):
    """
    Executes a single task. 
    Crucially, implies context into the prompt description so the specific
    Service LLM (GA4 parser or SEO Python generator) knows about previous data.
    """
    print(f"--- Running Agent: {agent} ---")
    
    # --- FIX 2: Prepare the Prompt with Context ---
    description = task.get('desc', '')
    inputs = task.get('inputs', {})
    
    # If there is context (results from previous tasks), append it to the query
    # so the downstream LLM knows what to calculate percentages OF.
    if 'context' in inputs and inputs['context']:
        context_str = json.dumps(inputs['context'], default=str)
        # We append this to the description so the service LLM sees it
        description += f"\n\nCONTEXT_DATA_FROM_PREVIOUS_STEPS:\n{context_str}\n\nINSTRUCTION: Use the context data above if required to answer: {task['desc']}"

    response = None
    try:
        if agent == 'seo':
            from app.services.seo_gsheet_service import execute_workbook_query
            # Pass the description + context string
            response = execute_workbook_query(description)
        elif agent == 'ga4':
            from app.services.ga4_service import run_ga4_queries
            # Pass the description + context string
            response = run_ga4_queries(inputs.get('property_id'), description)
    except Exception as e:
        response = f"Error executing task: {str(e)}"
        
    return response

def task_executor(state: OrchestrationState) -> OrchestrationState:
    """
    Node 3: Execute tasks based on the plan and generate final response.
    """
    plans = state.get("plans")

    if not plans or plans.get("type") == "invalid":
        return state

    tasks = plans.get("tasks", [])
    raw_deps = plans.get("dependencies", {})
    dependencies = {int(k): [int(v) for v in vals] for k, vals in raw_deps.items()}
    
    # Store results: { task_id: output }
    task_results = {}
    completed_ids = set()

    # ---- Execution Loop ---- #
    while len(completed_ids) < len(tasks):
        # ... (logic to find ready_tasks is fine) ...
        ready_tasks = []
        for task in tasks:
            tid = task["id"]
            if tid in completed_ids: continue
            
            task_deps = dependencies.get(tid, [])
            if all(dep_id in completed_ids for dep_id in task_deps):
                ready_tasks.append(task)

        if not ready_tasks:
            # Handle tasks that are the 'answer' agent separately if using that pattern,
            # or raise error if deadlock.
            # In your plan, the last task is 'answer', which we skip here and handle at the end.
            remaining = [t for t in tasks if t["id"] not in completed_ids]
            if all(t['agent'] == 'answer' for t in remaining):
                break # Exit loop, we are done with tools
            raise RuntimeError(f"Deadlock detected! Remaining: {[t['id'] for t in remaining]}")

        for task in ready_tasks:
            tid = task["id"]
            
            # Skip the final answer task in the execution loop
            if task['agent'] == 'answer':
                completed_ids.add(tid)
                continue

            # Context Injection Logic
            current_inputs = copy.deepcopy(task.get("inputs", {}))
            task_deps = dependencies.get(tid, [])
            
            if task_deps:
                parent_outputs = {
                    f"task_{dep_id}_output": task_results.get(dep_id) 
                    for dep_id in task_deps
                }
                current_inputs["context"] = parent_outputs

            execution_payload = {**task, "inputs": current_inputs}
            
            print(f"Executing Task {tid}: {task['desc']}")
            
            # --- FIX 3: Dynamic Agent Selection ---
            # ERROR WAS: result = execute_task(execution_payload, tasks[1])
            # FIX IS: Use the agent defined in the specific task
            result = execute_task(execution_payload, task['agent'])
            
            task_results[tid] = result
            completed_ids.add(tid)

    # ---- Final Answer ---- #
    # Pass the populated task_results dict to the answer agent
    response = answer_agent(task_results,state["query"], state["isJson"] ,plans)

    return {
        **state,
        "task_results": task_results, # This is a Dict {1:..., 2:...}
        "response": response
    }

# Build Graph
def build_orchestration_graph():
    """
    Constructs the LangGraph workflow for multi-agent orchestration.
    
    Flow: query_agent -> orchestrator_node -> task_executor -> END
    """
    workflow = StateGraph(OrchestrationState)
    
    # Add nodes
    workflow.add_node("query_agent", query_agent)
    workflow.add_node("orchestrator", orchestrator_node)
    workflow.add_node("task_executor", task_executor)
    
    # Set entry point
    workflow.set_entry_point("query_agent")
    
    # Add edges
    workflow.add_edge("query_agent", "orchestrator")
    workflow.add_edge("orchestrator", "task_executor")
    workflow.add_edge("task_executor", END)
    
    return workflow.compile()


def run_graph(query: str, property_id: str = None, isJson : bool = False) -> str:
    """
    Convenience function to run the orchestration graph with a query.
    
    Args:
        query: The user's natural language query about GA4/SEO data.
        property_id: Optional GA4 property ID.
    
    Returns:
        The final response string.
    """
    graph = build_orchestration_graph()
    initial_state = {
        "query": query,
        "property_id": property_id or settings.GA4_PROPERTY_ID or "123456789",
        "plans": {},
        "task_results": [],
        "response": "",
        "isJson" : isJson
    }
    result = graph.invoke(initial_state)
    return result['response']


# Test (Hybrid Tier 3)
if __name__ == "__main__":
    test_query = "Top 10 pages by views last 14 days with their title tags? Output in JSON."
    test_property_id = "123456789"
    
    output = run_graph(test_query, test_property_id)
    print("\nFINAL OUTPUT:")
    print(output)