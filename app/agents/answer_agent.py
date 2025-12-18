# agent.py (Updated: Integrate answer_agent; minor tweaks for graceful flow)
# - task_executor: Calls agents for non-answer tasks; then explicitly calls answer_agent.
# - No changes to graph (linear to task_executor -> END; answer inside executor).
# - Edge: If plan['tasks'] has no non-answer, still run answer (e.g., direct insight).

from typing import Dict, Any, TypedDict
from langgraph.graph import StateGraph, END
from planner import plan_query
from seo_agent import seo_agent
from ga4_agent import ga4_agent  # Assume imported; uses execute_ga4_task internally
from answer_agent import answer_agent  # New

# ... (State same as before)

def task_executor(state: OrchestrationState) -> OrchestrationState:
    """Sequential: Run non-answer tasks by agent; then aggregate via answer_agent."""
    plan = state['plans']
    tasks = [t for t in plan['tasks'] if t['agent'] != 'answer']  # Exclude final
    task_results = state.get('task_results', [])
    prev_data = None

    for task in tasks:
        agent_type = task['agent']
        inputs = task.get('inputs', {})
        if prev_data:
            inputs.update(prev_data)  # Hybrid deps
        if agent_type == 'seo':
            result = execute_seo_task(_load_seo_data(), task, prev_data)  # From seo_agent
        elif agent_type == 'ga4':
            result = execute_ga4_task(task, prev_data)  # From ga4_agent
        else:
            result = {'task_id': task['id'], 'data': {'error': f'Unknown agent: {agent_type}'}}
        task_results.append(result)
        prev_data = result.get('data', {}) if isinstance(result.get('data'), dict) else None

    state['task_results'] = task_results
    state = answer_agent(state)  # Final fusion
    return state

# ... (Graph unchanged; build_orchestration_graph same)

# Test Example (Hybrid: GA4 top pages + SEO titles)
if __name__ == "__main__":
    graph = build_orchestration_graph()
    initial = {
        "query": "Top 10 pages by views last 14 days with their title tags? Output in JSON.",
        "plans": {}, "task_results": [], "response": ""
    }
    result = graph.invoke(initial)
    print(result['response'])  # Should be JSON with merged data