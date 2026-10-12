"""Explicit model/engine selection and current-run evidence for live scenarios."""
from contextlib import contextmanager
import json
from pathlib import Path
import re
import tempfile

from absl import flags

DEFAULT_MODEL = 'chatgpt/gpt-6-luna'
FLAGS = flags.FLAGS
flags.DEFINE_string('llm_model', DEFAULT_MODEL, 'Exact provider/model identity; no fallback')
flags.DEFINE_enum('scenario_engine', 'native', ['native', 'linux'], 'Explicit SC2 backend; both run real games')


def run_directory(scenario, model):
    root = Path('logs/scenario-runs')
    root.mkdir(parents=True, exist_ok=True)
    label = re.sub(r'[^A-Za-z0-9_-]', '-', model)
    return Path(tempfile.mkdtemp(prefix=scenario + '-' + label + '-', dir=root)).resolve()


@contextmanager
def scenario_planner(model, directory):
    if model.startswith('chatgpt/'):
        from arena.chatgpt_planner import ChatGPTSubscriptionPlanner
        client = ChatGPTSubscriptionPlanner(model, Path(directory) / 'planner')
        try:
            yield client
        finally:
            client.close()
        return
    if model == 'deepseek/deepseek-flash':
        from arena.deepseek_planner import DeepSeekFlashPlanner
        client = DeepSeekFlashPlanner(reasoning_effort='low')
        if not client.p.configured:
            raise RuntimeError('DEEPSEEK_API_KEY missing; model fallback prohibited')
        yield client
        return
    raise ValueError('Unsupported scenario model: ' + model + '; use chatgpt/model or deepseek/deepseek-flash')


@contextmanager
def scenario_arena(engine, name):
    if engine == 'native':
        from arena.native_engine import NativeArena
        with NativeArena(names=(name,), races=['terran'], render=False,
                         difficulty='easy', game_speed='faster') as arena:
            yield arena
    elif engine == 'linux':
        from arena.raw_engine import RawArena
        arena = RawArena(races=['terran'], render=False, difficulty='easy')
        arena.closed = False
        try:
            arena.version = arena.controllers[0].ping().game_version
            yield arena
        finally:
            arena.close()
            arena.closed = True
    else:
        raise ValueError('Unknown explicit scenario engine: ' + engine)


def read_exchanges(path):
    """Parse actual JSON, including spaced StructuredOutput responses."""
    pending, exchanges = {}, []
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        entry = json.loads(line)
        request_id = entry.get('request_id')
        if entry.get('record') == 'request_queued' and isinstance(entry.get('prompt'), dict):
            pending[request_id] = entry['prompt']
        elif entry.get('record') == 'request_finished' and request_id in pending:
            prompt = pending.pop(request_id)
            if entry.get('error') or not entry.get('response'):
                continue
            try:
                plan = json.loads(entry['response'])
            except (ValueError, TypeError):
                continue
            if isinstance(plan, dict) and 'tasks' not in plan and isinstance(plan.get('skill'), str):
                plan = dict(tasks=[{key: value for key, value in plan.items() if key != 'reason'}],
                            reason=plan.get('reason', ''))
            if not isinstance(plan, dict) or not isinstance(plan.get('tasks'), list):
                continue
            tasks = [task for task in plan['tasks'] if isinstance(task, dict)]
            exchanges.append(dict(
                attacked=[(row['label'], row['visible_attackers'], row['urgency'])
                          for row in prompt.get('defense_report', []) if row['visible_attackers'] > 0],
                own=(prompt.get('world') or {}).get('own'),
                skills=sorted({task['skill'] for task in tasks if isinstance(task.get('skill'), str)}),
                defend_orders=[task for task in tasks if task.get('skill') == 'defend_base'],
                reason=plan.get('reason', '')))
    return exchanges


def acceptance(exchanges, requested):
    turns = [entry for entry in exchanges if entry['attacked']]
    first = next((n for n, entry in enumerate(turns, 1) if 'defend_base' in entry['skills']), None)
    economy = {'train_worker', 'build_supply', 'build_structure', 'gather_gas',
               'research', 'expand', 'gather', 'train_unit', 'morph_unit'}
    streak = worst = 0
    for entry in turns:
        streak = streak + 1 if entry['skills'] and set(entry['skills']) <= economy else 0
        worst = max(worst, streak)
    abandon = any(isinstance(entry['reason'], str) and any(
        word in entry['reason'] for word in ('放弃', '不守', '不救', '无法防守', '兵力不足'))
        for entry in turns[:3])
    verdict = 'inconclusive' if not turns else 'pass' if (first is not None and first <= 3) or abandon else 'fail'
    return dict(first_defence_turn=first, attacked_response_turns=len(turns),
                worst_economy_only_streak=worst, explicit_abandonment=abandon,
                requested=requested, verdict=verdict)


def report_exit_code(report):
    if report.get('error') or report.get('inconclusive') or report.get('client_fatal'):
        return 2
    verdict = report.get('acceptance', {}).get('verdict')
    return 0 if verdict == 'pass' else 1 if verdict == 'fail' else 2
