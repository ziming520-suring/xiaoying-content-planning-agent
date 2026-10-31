"""Strict synthetic catalog loading; deterministic retrieval, not semantic search."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path


class ToolError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def require(condition, message, code='INVALID_REQUEST'):
    if not condition:
        raise ToolError(code, message)


def integer(value, name):
    require(type(value) is int and 0 <= value <= 10**9, f'{name}: expected integer 0..10^9')
    return value


def string(value, name, limit=500):
    require(isinstance(value, str) and 0 < len(value.strip()) <= limit, f'{name}: invalid text')
    try:
        value.encode('utf-8')
    except UnicodeError as exc:
        raise ToolError('INVALID_REQUEST', f'{name}: invalid Unicode text') from exc
    return value


def fields(value, expected):
    require(isinstance(value, dict) and set(value) == set(expected), 'Unexpected or missing fields')


def instant(value):
    string(value, 'time', 64)
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        require(parsed.utcoffset() is not None, 'Timezone is required')
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise ToolError('INVALID_REQUEST', f'Invalid zoned time: {value}') from exc


def timestamp(value):
    return value.isoformat().replace('+00:00', 'Z')


def canonical(value):
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    try:
        text.encode('utf-8')
    except UnicodeError as exc:
        raise ToolError('INVALID_REQUEST', 'Invalid Unicode text') from exc
    return text


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'Duplicate JSON key')
        result[key] = value
    return result


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=_pairs,
                          parse_constant=lambda x: (_ for _ in ()).throw(ToolError('INVALID_REQUEST', x)))
    except (OSError, ValueError) as exc:
        raise ToolError('INVALID_REQUEST', f'Invalid catalog {Path(path).name}: {exc}') from exc


class Catalog:
    def __init__(self, materials, costs):
        fields(materials, ['schema_version', 'materials'])
        fields(costs, ['schema_version', 'version', 'rates'])
        for value in (materials['schema_version'], costs['schema_version']):
            require(type(value) is int and value == 1, 'Unsupported schema')
        string(costs['version'], 'version', 100)
        require(isinstance(materials['materials'], list) and materials['materials'], 'Empty materials')
        require(isinstance(costs['rates'], list) and costs['rates'], 'Empty rates')
        self._materials, self._rates = {}, {}
        for material in materials['materials']:
            fields(material, ['material_id', 'version', 'title', 'keywords', 'facts', 'source'])
            for key in ('material_id', 'version', 'title', 'source'):
                string(material[key], key, 200)
            for key in ('keywords', 'facts'):
                require(isinstance(material[key], list) and material[key], f'Empty {key}')
                for item in material[key]:
                    string(item, key, 1000)
            require(material['material_id'] not in self._materials, 'Duplicate material ID')
            self._materials[material['material_id']] = deepcopy(material)
        for rate in costs['rates']:
            fields(rate, ['operation', 'mode_id', 'success_cost', 'failure_cost', 'success_seconds', 'failure_seconds'])
            require(isinstance(rate['operation'], str) and rate['operation'] in {'search_materials', 'estimate_plan', 'generate_script', 'save_plan'}, 'Unknown operation')
            string(rate['mode_id'], 'mode_id', 100)
            if rate['operation'] != 'generate_script':
                require(rate['mode_id'] == 'default', 'Control tool mode must be default')
            for key in ('success_cost', 'failure_cost', 'success_seconds', 'failure_seconds'):
                integer(rate[key], key)
            key = (rate['operation'], rate['mode_id'])
            require(key not in self._rates, 'Duplicate rate')
            self._rates[key] = deepcopy(rate)
        require({op for op, _ in self._rates} == {'search_materials', 'estimate_plan', 'generate_script', 'save_plan'}, 'Missing tool rates')
        self.version = costs['version']

    @classmethod
    def load(cls, path):
        return cls(_read(Path(path) / 'materials.json'), _read(Path(path) / 'tool_costs.json'))

    def export(self):
        return {'materials': {'schema_version': 1, 'materials': deepcopy(list(self._materials.values()))},
                'costs': {'schema_version': 1, 'version': self.version, 'rates': deepcopy(list(self._rates.values()))}}

    def search(self, query):
        string(query, 'query')
        query = query.strip().casefold()
        hits = []
        for material in self._materials.values():
            candidates = [material['title'], *material['keywords']]
            if any(query in text.casefold() or text.casefold() in query for text in candidates):
                hits.append(deepcopy(material))
        return sorted(hits, key=lambda m: m['material_id'])

    def rate(self, operation, mode_id='default'):
        require(isinstance(operation, str) and isinstance(mode_id, str), 'Invalid tool mode')
        require((operation, mode_id) in self._rates, 'Unknown tool mode')
        return deepcopy(self._rates[(operation, mode_id)])
