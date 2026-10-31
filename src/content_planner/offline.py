"""Deterministic observer-driven test policy. This is NOT a real model."""


class OfflinePolicy:
    name = 'offline_mock'

    def next_action(self, context):
        def action(tool, arguments, reason):
            return {'tool': tool, 'arguments': arguments, 'reason': reason}
        def stop(reason):
            return action('finish', {'stop_reason': reason}, reason)
        if context['saved_plan']:
            return stop('completed')
        if context['unresolved_attempts']:
            return stop('pending_reconciliation')
        searches = [r for r in context['tool_results'].values() if r['operation']=='search_materials']
        if not searches:
            failures = [a for a in context['attempts'] if a['operation']=='search_materials' and a['status']=='failed']
            return stop('tool_failed') if len(failures)>=2 else action('search_materials', {'query': context['topic']}, 'Find supporting material')
        found = searches[-1]
        if not found['materials']:
            return stop('no_material')
        plan = context['active_plan']
        if plan and plan['estimate']['feasible']:
            args = {'plan_id': plan['plan_id'], 'plan_version': plan['plan_version']}
            pending = next((s for s in context['steps'] if s['kind']=='generate_script' and s['status']!='succeeded'), None)
            tool = 'generate_script' if pending else 'save_plan'
            failures = [a for a in context['attempts'] if a['operation']==tool and a['status']=='failed']
            if len(failures)>=2:
                return stop('tool_failed')
            if pending:
                args['step_id'] = pending['step_id']
            return action(tool, args, 'Execute feasible remaining step using retained results')
        tried = {s['mode_id'] for p in context['plan_history'] for s in p if s['kind']=='generate_script'}
        mode = next((m for m in ('premium', 'economy', 'quick') if m not in tried), None)
        if mode is None:
            return stop('no_feasible_plan')
        steps = [dict(step_id='generate', kind='generate_script', mode_id=mode, depends_on=[], input_refs=[found['result_id']]),
                 dict(step_id='save', kind='save_plan', mode_id='default', depends_on=['generate'], input_refs=[])]
        return action('estimate_plan', {'steps': steps}, 'Estimate an untried alternative against current cost and time')
