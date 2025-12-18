# agent.py
# Multi-Agent Orchestration with LangGraph: Planner -> Tasks (dep-aware) -> Answer.
# - Linear graph: query_agent (plan) -> orchestrator (validate) -> task_executor (execute_plan) -> END.
# - Handles Tier 1-3: GA4/SEO single/hybrid; property_id from input.
# - State: Accumulates task_results; final response from answer task.
# - Edges: Invalid -> response; empty data graceful.

from typing import Dict, Any, TypedDict, List
import json
import sys
import os
# Add directory containing 'app' package to path so absolute imports work when running directly
# From app/agents/agent.py, go up to app/, then up to web-diagnostics-orchestration/
app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # app/
project_root = os.path.dirname(app_dir)  # web-diagnostics-orchestration/
sys.path.insert(0, project_root)

from langgraph.graph import StateGraph, END
from planner import plan_query
from orchestrator_agent import execute_plan

# Updated State
class OrchestrationState(TypedDict):
    query: str
    property_id: str  # From input
    plans: Dict[str, Any]  # Full plan from planner
    task_results: List[Dict[str, Any]]
    response: str

# Nodes
def query_agent(state: OrchestrationState) -> OrchestrationState:
    query = state['query']
    property_id = state.get('property_id', '123456789')  # Default; override in invoke
    if not query.strip():
        return {"plans": {"type": "invalid", "tasks": [], "dependencies": {}, "output_format": "nl"}, "task_results": [], "response": "Error: Empty query."}
    plans = plan_query(query, property_id=property_id)
    return {"plans": plans, "task_results": [], "property_id": property_id}

def orchestrator(state: OrchestrationState) -> OrchestrationState:
    print(f"[Orch] Plan type: {state['plans'].get('type', 'unknown')}")
    if state['plans']['type'] == 'invalid':
        state['response'] = "Invalid query type."
    return state

def task_executor(state: OrchestrationState) -> OrchestrationState:
    """Delegate to orchestrator for dep-aware exec."""
    property_id = state['property_id']
    return execute_plan(state, property_id)

# Build Graph
def build_orchestration_graph():
    workflow = StateGraph(OrchestrationState)
    workflow.add_node("query_agent", query_agent)
    workflow.add_node("orchestrator", orchestrator)
    workflow.add_node("task_executor", task_executor)
    workflow.set_entry_point("query_agent")
    workflow.add_edge("query_agent", "orchestrator")
    workflow.add_edge("orchestrator", "task_executor")
    workflow.add_edge("task_executor", END)
    return workflow.compile()

# Test (Hybrid Tier 3)
if __name__ == "__main__":
    graph = build_orchestration_graph()
    initial = {
        "query": "Top 10 pages by views last 14 days with their title tags? Output in JSON.",
        "property_id": "123456789",
        "plans": {},
        "task_results": [],
        "response": ""
    }
    result = graph.invoke(initial)
    print(result['response'])