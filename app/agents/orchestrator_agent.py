# orchestrator_agent.py
# Dependency-aware task execution for plans from planner.py.
# - Uses topological sort via Kahn's algorithm (queue for ready tasks).
# - Resolves inputs: Templates {{q0.output.field}} -> results['q0']['field']; {{q0.output}} -> results['q0'].
# - Dispatches: ga4/seo via their execute_* funcs; answer sets response.
# - State update: Appends to task_results; final response from answer task.
# - Edges: Cycles raise ValueError; missing deps error; parallel via queue order.
# - Reuse: _resolve_template simple str-based (no jinja dep); assumes LLM refs clean.
# - Config: None; injects property_id to ga4 inputs.
# Usage: In agent.py task_executor: return execute_plan(state, property_id)

from typing import Dict, Any, List
from collections import deque
import re
import json
import sys
import os
# Add directory containing 'app' package to path so absolute imports work when running directly
# From app/agents/orchestrator_agent.py, go up to app/, then up to web-diagnostics-orchestration/
app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # app/
project_root = os.path.dirname(app_dir)  # web-diagnostics-orchestration/
sys.path.insert(0, project_root)

from seo_agent import execute_seo_task, _load_seo_data, _llm_call
from ga4_agent import execute_ga4_task

def _resolve_template(val: Any, results: Dict[str, Any]) -> Any:
    """Resolve {{qX.output[.field]}} in str vals; return as-is if not str or no match."""
    if not isinstance(val, str):
        return val
    
    # Check if entire string is a single template (e.g., "{{q0.output}}")
    full_match = re.match(r'^\{\{([^}]+)\}\}$', val.strip())
    if full_match:
        # Return the actual object, not a string
        ref = full_match.group(1)
        if ref.endswith('.output'):
            tid = ref[:-7]  # q0.output -> q0
            return results.get(tid, {})
        if '.output.' in ref:
            tid, field = ref.split('.output.', 1)
            data = results.get(tid, {})
            return data.get(field) if data else None
        tid = ref[:-7] if ref.endswith('.output') else ref
        return results.get(tid, {})
    
    # Multiple templates or mixed content - stringify replacements
    def repl(match):
        ref = match.group(1)
        resolved = None
        if ref.endswith('.output'):
            tid = ref[:-7]  # q0.output -> q0
            resolved = results.get(tid, {})
        elif '.output.' in ref:
            tid, field = ref.split('.output.', 1)
            data = results.get(tid, {})
            resolved = data.get(field) if data else None
        else:
            tid = ref[:-7] if ref.endswith('.output') else ref
            resolved = results.get(tid, {})
        
        # Convert to string for re.sub replacement
        if resolved is None:
            return "None"
        if isinstance(resolved, (dict, list)):
            return json.dumps(resolved)
        return str(resolved)
    
    return re.sub(r'\{\{([^}]+)\}\}', repl, val)

def _resolve_inputs(inputs: Dict[str, Any], results: Dict[str, Any]) -> Dict[str, Any]:
    """Deep resolve templates in inputs dict."""
    resolved = {}
    for k, v in inputs.items():
        if isinstance(v, dict):
            resolved[k] = _resolve_inputs(v, results)
        else:
            resolved[k] = _resolve_template(v, results)
    return resolved

def execute_plan(state: Dict[str, Any], property_id: str = None) -> Dict[str, Any]:
    """Execute tasks respecting dependencies; update state in-place."""
    plan = state['plans']
    tasks = {t['id']: t for t in plan['tasks']}
    deps = plan['dependencies']  # {id: [dep_ids]}
    task_results: List[Dict[str, Any]] = state.get('task_results', [])
    results: Dict[str, Any] = {}  # {task_id: data}

    # Kahn's algo: indegree for topo
    indegree = {tid: len(dids) for tid, dids in deps.items()}
    queue = deque([tid for tid, deg in indegree.items() if deg == 0])

    while queue:
        tid = queue.popleft()
        task = tasks[tid]
        resolved_inputs = _resolve_inputs(task['inputs'], results)

        if task['agent'] == 'ga4':
            result = execute_ga4_task(task, resolved_inputs)
            data = result['data']
        elif task['agent'] == 'seo':
            seo_df = _load_seo_data()
            result = execute_seo_task(seo_df, task, resolved_inputs)
            data = result['data']
        elif task['agent'] == 'answer':
            format_ = resolved_inputs.get('format', 'nl')
            prompt = resolved_inputs.get('prompt', 'Aggregate results.')
            prev_results = {k: v for k, v in results.items()}
            # Simple LLM fusion if nl, json dump if json
            if format_ == 'json':
                data = {'response': json.dumps(prev_results, indent=2, default=str)}
            else:
                fusion_prompt = f"{prompt}\n\nData: {json.dumps(prev_results, default=str)}\nOutput natural language summary."
                data = {'response': _llm_call(fusion_prompt)}  # Assume _llm_call imported or defined
            state['response'] = data['response']
            task_results.append({'task_id': tid, 'data': data})
            results[tid] = data
            continue  # Skip dependents for answer (final)
        else:
            data = {'error': f'Unknown agent: {task["agent"]}'}
            result = {'task_id': tid, 'data': data}

        results[tid] = data
        task_results.append(result)

        # Update dependents
        for next_tid, next_deps in deps.items():
            if tid in next_deps:
                indegree[next_tid] -= 1
                if indegree[next_tid] == 0:
                    queue.append(next_tid)

    if len(results) != len(tasks):
        raise ValueError(f"[Orch] Cycle or missing deps: executed {len(results)}/{len(tasks)} tasks")

    state['task_results'] = task_results
    return state