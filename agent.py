"""
Multi-Agent Orchestration with LangGraph: Planner -> Tasks (dep-aware) -> Answer.

- Linear graph: query_agent (plan) -> orchestrator (validate) -> task_executor (execute_plan) -> END.
- Handles Tier 1-3: GA4/SEO single/hybrid; property_id from input.
- State: Accumulates task_results; final response from answer task.
- Edges: Invalid -> response; empty data graceful.
"""

from typing import Dict, Any, TypedDict, List

from langgraph.graph import StateGraph, END

from app.orchestrator import generate_plan, answer_agent, mock_execution_layer
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

def execute_task(task , agent):
    # seo or ga4
    print(task)
    print(agent)
    response = None
    if agent == 'seo':
        from app.services.seo_gsheet_service import execute_workbook_query
        response = execute_workbook_query(task['desc'])
    elif agent == 'ga4':
        from app.services.ga4_service import run_ga4_queries
        response = run_ga4_queries(task['inputs']['property_id'], task['desc'])
    return response

def task_executor(state: OrchestrationState) -> OrchestrationState:
    """
    Node 3: Execute tasks based on the plan and generate final response.
    Implements a DAG solver to handle dependencies and pass data between tasks.
    """
    plans = state.get("plans")

    # Guard: invalid or missing plan
    if not plans or plans.get("type") == "invalid":
        return state

    tasks = plans.get("tasks", [])
    # Ensure dependencies keys are integers if your IDs are integers
    # The JSON schema might return strings like "2", so we normalize.
    raw_deps = plans.get("dependencies", {})
    dependencies = {int(k): [int(v) for v in vals] for k, vals in raw_deps.items()}

    # Map tasks by ID for easy lookup
    task_map = {t["id"]: t for t in tasks}
    
    # Store results: { task_id: output }
    task_results = {}
    completed_ids = set()

    # ---- Execution Loop (DAG Solver) ---- #
    # This loop works for sequential, parallel (independent), and mixed flows.
    while len(completed_ids) < len(tasks):
        progress = False
        
        # Identify tasks that are ready to run
        # Ready = Not yet completed AND all dependencies are completed
        ready_tasks = []
        for task in tasks:
            tid = task["id"]
            if tid in completed_ids:
                continue
            
            # Check if all dependencies for this task are in completed_ids
            task_deps = dependencies.get(tid, [])
            if all(dep_id in completed_ids for dep_id in task_deps):
                ready_tasks.append(task)

        # Execution Phase
        if not ready_tasks:
            # If tasks remain but none are ready, we have a cyclic dependency or missing ID
            remaining = [t["id"] for t in tasks if t["id"] not in completed_ids]
            raise RuntimeError(f"Deadlock detected! Remaining tasks {remaining} have unresolved dependencies.")

        for task in ready_tasks:
            tid = task["id"]
            
            # --- CRITICAL FIX: Context Injection ---
            # Create a copy of inputs to avoid mutating the original plan
            # and inject outputs from dependency tasks.
            current_inputs = copy.deepcopy(task.get("inputs", {}))
            
            task_deps = dependencies.get(tid, [])
            if task_deps:
                # Collect results from all parent tasks
                parent_outputs = {
                    f"task_{dep_id}_output": task_results[dep_id] 
                    for dep_id in task_deps
                }
                # Merge parent outputs into current inputs
                # The agent receiving this must know to look for these keys or 'context'
                current_inputs["context"] = parent_outputs
                
                # OPTIONAL: If you want to merge directly into the root of inputs
                # current_inputs.update(parent_outputs)

            # Update task inputs temporarily for execution
            execution_payload = {**task, "inputs": current_inputs}
            
            # Execute
            # Pass the modified payload (with injected data) to your executor
            print(f"Executing Task {tid}: {task['desc']}") # Logging
            print(tasks)
            result = execute_task(execution_payload,tasks[1])
            
            # Store result and mark complete
            task_results[tid] = result
            completed_ids.add(tid)
            progress = True

    # ---- Final Answer ---- #
    # Pass the full context to the answer agent to format the NL response
    response = answer_agent(task_results, plans)

    return {
        **state,
        "task_results": task_results,
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


def run_graph(query: str, property_id: str = None) -> str:
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
        "response": ""
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