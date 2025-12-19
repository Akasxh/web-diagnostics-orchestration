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
    query: str
    property_id: str  # From input
    plans: Dict[str, Any]  # Full plan from planner
    task_results: List[Dict[str, Any]]
    response: str


# Nodes
def query_agent(state: OrchestrationState) -> OrchestrationState:
    """
    Node 1: Plan the query by classifying and decomposing it into tasks.
    Uses generate_plan from app.orchestrator.
    """
    query = state['query']
    property_id = state.get('property_id', settings.GA4_PROPERTY_ID or '123456789')
    
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


def task_executor(state: OrchestrationState) -> OrchestrationState:
    """
    Node 3: Execute tasks based on the plan and generate final response.
    Uses mock_execution_layer and answer_agent from app.orchestrator.
    """
    plans = state['plans']
    
    # Skip execution for invalid plans
    if plans.get('type') == 'invalid':
        return state
    
    # Execute tasks (currently using mock layer)
    task_results = mock_execution_layer(plans)
    
    # Generate final answer
    response = answer_agent(task_results, plans)
    
    return {"task_results": task_results, "response": response}


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