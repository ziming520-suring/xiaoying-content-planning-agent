"""Constrained composition: every factual line must equal a source fact."""
from .catalog import fields,require,string

OPENING='大家好，欢迎观看功能介绍。'
CLOSING='感谢观看。'


def validate(result,materials):
    fields(result,['title','script','source_ids'])
    string(result['script'],'script',16000)
    require(result['title']=='功能介绍','Unapproved title','MODEL_GROUNDING_ERROR')
    ids=[m['material_id'] for m in materials]
    require(isinstance(result['source_ids'],list) and all(isinstance(v,str) for v in result['source_ids']) and len(result['source_ids'])==len(ids) and set(result['source_ids'])==set(ids),'Invalid citations','MODEL_GROUNDING_ERROR')
    facts={fact.rstrip('。') for m in materials for fact in m['facts']}
    lines=[line.strip().rstrip('。') for line in result['script'].splitlines() if line.strip()]
    allowed=facts|{OPENING.rstrip('。'),CLOSING.rstrip('。')}
    require(bool(lines) and all(line in allowed for line in lines),'Unsupported factual line','MODEL_GROUNDING_ERROR')
    require(len(lines)==len(set(lines)),'Repeated script lines','MODEL_GROUNDING_ERROR')
    require(all(any(fact.rstrip('。') in lines for fact in m['facts']) for m in materials) and bool(materials),'Missing material coverage','MODEL_GROUNDING_ERROR')
    return result
