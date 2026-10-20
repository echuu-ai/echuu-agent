"""Provider-neutral JSON schemas for clip planning and spoken output."""
def obj(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}
def strings(*names):
    return {name: {'type': 'string'} for name in names}
def array(items):
    return {'type': 'array', 'items': items}
PLAN_SCHEMA = obj({
    'candidates': array(obj(strings('id', 'premise', 'expectation', 'surprise', 'resolution', 'character_choice', 'share_reason'))),
    **strings('selected_id', 'selection_reason'),
    'voice_design': obj({**strings('provenance', 'frequency_limit'), 'habits': array({'type':'string'}), 'optional_phrases': array({'type':'string'})}),
})
DRAFT_SCHEMA = obj({
    'lines': array(obj(strings('id', 'text'))),
    'quotables': array(obj(strings('line_id', 'quote', 'why_shareable'))),
})
DRAFT_SCHEMA['properties']['lines'].update(minItems=5, maxItems=10)
DRAFT_SCHEMA['properties']['lines']['items']['properties']['id']['pattern'] = r'^L\d{3}$'
DRAFT_SCHEMA['properties']['lines']['items']['properties']['text']['minLength'] = 1
DRAFT_SCHEMA['properties']['quotables'].update(minItems=1, maxItems=2)
