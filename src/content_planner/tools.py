"""Trusted, single-process tool workbench. No Agent or durable task runtime yet."""
from copy import deepcopy
from datetime import timedelta
import hashlib

from .artifacts import save_artifact
from .catalog import ToolError, canonical, fields, instant, integer, require, string, timestamp


def _failure(exc, status='failed'):
    return {'tool_status': status, 'result': None,
            'error': {'code': exc.code, 'message': str(exc)}}


class ToolWorkbench:
    def __init__(self, catalog, task, artifact_db, *, artifact_writer=None):
        fields(task, ['task_id', 'topic', 'budget_limit', 'start_time', 'deadline'])
        string(task['task_id'], 'task_id', 100)
        string(task['topic'], 'topic', 500)
        integer(task['budget_limit'], 'budget_limit')
        start, deadline = instant(task['start_time']), instant(task['deadline'])
        require(deadline > start, 'Deadline must be after start')
        self._catalog = deepcopy(catalog)
        self._task = deepcopy(task)
        self._now, self._deadline = start, deadline
        self._db = artifact_db
        self._artifact_writer = artifact_writer or save_artifact
        self._script_generator = None  # Runtime supplies an already obtained online result, never network in a transaction.
        self._spent = 0
        self._attempts, self._observations = [], []
        self._results, self._plans, self._success = {}, {}, {}
        self._saved, self._faults = {}, {}
        self._active, self._sequence = 0, 0
        self._last_estimate = None

    def _id(self, prefix):
        self._sequence += 1
        return f'{self._task["task_id"]}:{prefix}:{self._sequence}'

    def snapshot(self):
        unknown = [a for a in self._attempts if a['status'] == 'unknown']
        return deepcopy({'task_id': self._task['task_id'], 'spent_cost': self._spent,
                         'reserved_cost': sum(a['reserved_cost'] for a in unknown),
                         'logical_time': timestamp(self._now), 'active_plan_version': self._active,
                         'attempts': self._attempts, 'results': self._results})

    def observations(self):
        return deepcopy(self._observations)

    def inject_failure(self, operation):
        """Trusted test harness only. Never register as a model tool."""
        self._catalog.rate(operation, 'economy' if operation == 'generate_script' else 'default')
        self._faults[operation] = self._faults.get(operation, 0) + 1

    def _call(self, name, params, operation):
        try:
            response = operation()
        except ToolError as exc:
            response = _failure(exc)
        self._observations.append({'tool': name, 'params': deepcopy(params), 'response': deepcopy(response),
                                   'spent_cost': self._spent, 'logical_time': timestamp(self._now)})
        return deepcopy(response)

    def _check(self, rate):
        require(not any(a['status'] == 'unknown' for a in self._attempts),
                'Resolve uncertain attempt first', 'PENDING_RECONCILIATION')
        require(self._now < self._deadline, 'Deadline has been reached', 'DEADLINE_EXCEEDED')
        require(self._spent + max(rate['success_cost'], rate['failure_cost']) <= self._task['budget_limit'],
                'Insufficient remaining budget', 'BUDGET_EXCEEDED')
        require(max(rate['success_seconds'], rate['failure_seconds']) <= (self._deadline - self._now).total_seconds(),
                'Insufficient remaining time', 'DEADLINE_EXCEEDED')

    def _execute(self, operation, mode, payload, producer):
        rate = self._catalog.rate(operation, mode)
        self._check(rate)
        attempt = {'attempt_id': self._id('attempt'), 'operation': operation, 'mode_id': mode,
                   'payload': deepcopy(payload),
                   'status': 'running', 'input_fingerprint': hashlib.sha256(canonical(payload).encode()).hexdigest(),
                   'reserved_cost': max(rate['success_cost'], rate['failure_cost']),
                   'reserved_seconds': max(rate['success_seconds'], rate['failure_seconds']),
                   'cost_actual': None, 'duration_actual_seconds': None}
        self._attempts.append(attempt)
        try:
            if self._faults.get(operation, 0):
                self._faults[operation] -= 1
                raise ToolError('TOOL_FAILED', 'Injected deterministic tool failure')
            result = producer(rate)
            response = {'tool_status': 'succeeded', 'result': result, 'error': None}
        except ToolError as exc:
            uncertain = exc.code == 'PENDING_RECONCILIATION'
            response = _failure(exc, 'unknown' if uncertain else 'failed')
        outcome = response['tool_status']
        attempt['status'] = outcome
        if outcome != 'unknown':
            prefix = 'success' if outcome == 'succeeded' else 'failure'
            attempt['cost_actual'] = rate[prefix + '_cost']
            attempt['duration_actual_seconds'] = rate[prefix + '_seconds']
            attempt['reserved_cost'] = attempt['reserved_seconds'] = 0
            self._spent += attempt['cost_actual']
            self._now += timedelta(seconds=attempt['duration_actual_seconds'])
        attempt['error'] = response['error']
        attempt['result_id'] = response['result'].get('result_id') if response['result'] else None
        return response

    def search_materials(self, query):
        def run():
            matches = self._catalog.search(query)  # Validate before charging.
            def produce(rate):
                result_id = self._id('search')
                result = {'result_id': result_id, 'operation': 'search_materials',
                          'query': query, 'materials': matches, 'missing': [] if matches else ['materials'],
                          'tool_table_version': self._catalog.version}
                self._results[result_id] = deepcopy(result)
                return result
            return self._execute('search_materials', 'default', {'query': query}, produce)
        return self._call('search_materials', {'query': query}, run)

    def _validate_plan(self, steps):
        require(isinstance(steps, list) and 1 <= len(steps) <= 20, 'Expected 1..20 steps')
        by_id = {}
        for step in steps:
            fields(step, ['step_id', 'kind', 'mode_id', 'depends_on', 'input_refs'])
            string(step['step_id'], 'step_id', 100)
            require(step['step_id'] not in by_id, 'Duplicate step_id')
            require(isinstance(step['kind'], str) and step['kind'] in {'search_materials', 'generate_script', 'save_plan'}, 'Invalid kind')
            self._catalog.rate(step['kind'], step['mode_id'])
            for key in ('depends_on', 'input_refs'):
                require(isinstance(step[key], list) and len(step[key]) <= 20, 'Invalid references')
                for item in step[key]:
                    string(item, key, 200)
                require(len(step[key]) == len(set(step[key])), 'Duplicate reference')
            for ref in step['input_refs']:
                require(ref in self._results, 'Unknown or foreign result', 'DEPENDENCY_UNMET')
                require(self._results[ref]['operation'] == 'search_materials', 'Expected material search reference')
            if step['kind'] == 'search_materials':
                require(len(step['input_refs']) == 1, 'Search nodes must reference an already executed search')
                require(not step['depends_on'], 'An already executed search cannot depend on future steps')
            if step['kind'] == 'generate_script':
                require(step['input_refs'], 'Script requires material references', 'DEPENDENCY_UNMET')
            if step['kind'] == 'save_plan':
                require(not step['input_refs'], 'Save reads trusted generated results')
            by_id[step['step_id']] = step
        ordered, visiting, visited = [], set(), set()
        def visit(key):
            require(key in by_id, 'Unknown dependency')
            require(key not in visiting, 'Cyclic dependency')
            if key in visited:
                return
            visiting.add(key)
            for dep in by_id[key]['depends_on']:
                visit(dep)
            visiting.remove(key)
            visited.add(key)
            ordered.append(by_id[key])
        for key in by_id:
            visit(key)
        saves = [s for s in ordered if s['kind'] == 'save_plan']
        generated = {s['step_id'] for s in ordered if s['kind'] == 'generate_script'}
        require(len(saves) == 1 and generated, 'Plan requires generation and exactly one save')
        require(generated <= set(saves[0]['depends_on']), 'Save must depend on every script')
        require(ordered[-1]['kind'] == 'save_plan', 'Save must be the last step')
        return deepcopy(ordered)

    def _key(self, step, steps):
        upstream = []
        for dep in step['depends_on']:
            parent = next(s for s in steps if s['step_id'] == dep)
            upstream.append(self._key(parent, steps))
        return hashlib.sha256(canonical({'kind': step['kind'], 'mode': step['mode_id'],
                          'inputs': [self._results[r] for r in step['input_refs']],
                          'dependencies': upstream, 'catalog': self._catalog.version,
                          'topic': self._task['topic']}).encode()).hexdigest()

    def _done(self, step, steps):
        if step['kind'] == 'save_plan':
            return self._active in self._saved
        return step['kind'] == 'search_materials' or self._key(step, steps) in self._success

    def _projection(self, steps, current_time=None, spent=None):
        remaining_cost = remaining_seconds = 0
        details = []
        gaps = []
        needs_generation = False
        for step in steps:
            reused = self._done(step, steps)
            rate = self._catalog.rate(step['kind'], step['mode_id'])
            cost, seconds = (0, 0) if reused else (rate['success_cost'], rate['success_seconds'])
            remaining_cost += cost
            remaining_seconds += seconds
            needs_generation |= step['kind'] == 'generate_script' and not reused
            if step['kind'] == 'generate_script' and not any(self._results[r]['materials'] for r in step['input_refs']):
                gaps.append({'code': 'DEPENDENCY_UNMET', 'step_id': step['step_id'], 'missing': 'materials'})
            details.append({'step_id': step['step_id'], 'reused': reused, 'cost': cost, 'seconds': seconds})
        final_rate = self._catalog.rate('estimate_plan')
        if needs_generation:
            remaining_cost += final_rate['success_cost']
            remaining_seconds += final_rate['success_seconds']
        total = (self._spent if spent is None else spent) + remaining_cost
        now = self._now if current_time is None else current_time
        try:
            end = timestamp(now + timedelta(seconds=remaining_seconds))
        except OverflowError:
            end = None  # Beyond datetime's representable range, necessarily past deadline.
        if total > self._task['budget_limit']:
            gaps.append({'code': 'BUDGET_EXCEEDED', 'amount': total - self._task['budget_limit']})
        late_seconds = remaining_seconds - (self._deadline - now).total_seconds()
        if late_seconds > 0:
            gaps.append({'code': 'DEADLINE_EXCEEDED', 'seconds': late_seconds})
        return {'feasible': not gaps, 'gaps': gaps, 'steps': details,
                'remaining_cost_estimate': remaining_cost, 'projected_total_cost': total,
                'estimated_finish_time': end, 'final_estimate_pending': needs_generation}

    def estimate_plan(self, steps):
        def run():
            ordered = self._validate_plan(steps)
            def produce(rate):
                current = self._plans.get(self._active)
                if current != ordered:
                    self._active += 1
                    self._plans[self._active] = deepcopy(ordered)
                estimate = self._projection(ordered, self._now + timedelta(seconds=rate['success_seconds']),
                                            self._spent + rate['success_cost'])
                estimate.update(plan_id=self._task['task_id'] + ':plan', plan_version=self._active,
                                estimate_id=self._id('estimate'), spent_cost=self._spent + rate['success_cost'], reserved_cost=0)
                self._last_estimate = (self._active, len(self._attempts), deepcopy(estimate))
                return estimate
            return self._execute('estimate_plan', 'default', {'steps': ordered}, produce)
        return self._call('estimate_plan', {'steps': steps}, run)

    def _plan(self, plan_id, version):
        require(isinstance(plan_id, str) and plan_id == self._task['task_id'] + ':plan', 'Unknown plan', 'STALE_PLAN')
        require(type(version) is int and version == self._active and version in self._plans, 'Plan is no longer current', 'STALE_PLAN')
        return self._plans[version]

    def _dependencies(self, step, steps):
        for dep in step['depends_on']:
            parent = next(s for s in steps if s['step_id'] == dep)
            require(self._done(parent, steps), 'Dependency has not succeeded', 'DEPENDENCY_UNMET')

    def generate_script(self, plan_id, version, step_id):
        def run():
            steps = self._plan(plan_id, version)
            step = next((s for s in steps if s['step_id'] == step_id and s['kind'] == 'generate_script'), None)
            require(step is not None, 'Unknown generation step')
            self._dependencies(step, steps)
            key = self._key(step, steps)
            if key in self._success:
                return deepcopy(self._success[key])
            materials = {}
            for ref in step['input_refs']:
                for material in self._results[ref]['materials']:
                    materials[material['material_id']] = material
            require(materials, 'No usable materials', 'DEPENDENCY_UNMET')
            projected = self._projection(steps)
            require(projected['feasible'], 'Plan is infeasible', projected['gaps'][0]['code'] if projected['gaps'] else 'INVALID_REQUEST')
            def produce(rate):
                result_id = self._id('script')
                # Deliberately a template: no factual claims beyond catalog facts.
                text = '\n'.join(f'[{m["material_id"]}] {fact}' for m in materials.values() for fact in m['facts'])
                result = {'result_id': result_id, 'operation': 'generate_script', 'generator': 'offline_mock',
                          'mode_id': step['mode_id'], 'title': self._task['topic'], 'script': text,
                          'source_ids': sorted(materials), 'sources': list(materials.values()),
                          'tool_table_version': self._catalog.version}
                if self._script_generator is not None:
                    result.update(self._script_generator(self._task['topic'],step['mode_id'],deepcopy(list(materials.values()))))
                self._results[result_id] = deepcopy(result)
                return result
            response = self._execute('generate_script', step['mode_id'], {'plan_id': plan_id, 'version': version, 'step_id': step_id}, produce)
            if response['tool_status'] == 'succeeded':
                self._success[key] = deepcopy(response)
            return response
        return self._call('generate_script', {'plan_id': plan_id, 'plan_version': version, 'step_id': step_id}, run)

    def save_plan(self, plan_id, version):
        def run():
            steps = self._plan(plan_id, version)
            if version in self._saved:
                return deepcopy(self._saved[version])
            save = next(s for s in steps if s['kind'] == 'save_plan')
            self._dependencies(save, steps)
            # Automatically perform the required final estimate unless a current one exists.
            if not self._last_estimate or self._last_estimate[:2] != (version, len(self._attempts)):
                response = self.estimate_plan(steps)
                if response['tool_status'] != 'succeeded':
                    return response
            projection = self._projection(steps)
            require(projection['feasible'], 'Final plan is infeasible', projection['gaps'][0]['code'] if projection['gaps'] else 'INVALID_REQUEST')
            def produce(rate):
                scripts = [deepcopy(self._success[self._key(s, steps)]['result']) for s in steps if s['kind'] == 'generate_script']
                snapshot = {'task_id': self._task['task_id'], 'topic': self._task['topic'],
                            'plan_version': version, 'steps': deepcopy(steps), 'scripts': scripts,
                            'budget': {'limit': self._task['budget_limit'], 'spent_cost': self._spent + rate['success_cost']},
                            'timing': {'deadline': timestamp(self._deadline),
                                       'completed_at': timestamp(self._now + timedelta(seconds=rate['success_seconds']))},
                            'tool_table_version': self._catalog.version,
                            'execution_mode': 'bailian_live' if all(s['generator']=='bailian_live' for s in scripts) else 'offline_tool_workbench'}
                return self._artifact_writer(self._db, self._task['task_id'], version, snapshot)
            response = self._execute('save_plan', 'default', {'plan_id': plan_id, 'version': version}, produce)
            if response['tool_status'] == 'succeeded':
                self._saved[version] = deepcopy(response)
                self._success[self._key(save, steps)] = deepcopy(response)
            return response
        return self._call('save_plan', {'plan_id': plan_id, 'plan_version': version}, run)
