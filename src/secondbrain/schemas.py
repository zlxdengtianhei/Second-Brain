"""Output JSON Schemas handed to executors (structured output).

These describe the shape only; the pipelines' validators remain the authority
on meaning (verbatim quotes, legal categories, full coverage).
"""
from __future__ import annotations

_STR = {'type': 'string'}
_NSTR = {'type': ['string', 'null']}
_DECISION = {'type': 'string', 'enum': ['accept', 'revise']}


def _items(item: dict) -> dict:
    return {'type': 'object', 'required': ['items'],
            'properties': {'items': {'type': 'array', 'items': item}}}


CLASSIFY = _items({'type': 'object', 'required': ['id', 'disposition', 'evidence_quote', 'reason'],
                   'properties': {'id': _STR, 'input_sha256': _STR,
                                  'disposition': {'type': 'string', 'enum': ['assigned', 'taxonomy_gap', 'needs_domain_review']},
                                  'pillar': _NSTR, 'topic': _NSTR, 'evidence_quote': _STR, 'reason': _STR,
                                  'suggested_pillar': _NSTR, 'suggested_topic': _NSTR, 'suggested_domain': _NSTR}})
REVIEW_BY_ID = _items({'type': 'object', 'required': ['id', 'decision', 'reason'],
                       'properties': {'id': _STR, 'decision': _DECISION, 'reason': _STR}})
MOVE = _items({'type': 'object', 'required': ['id', 'input_sha256', 'disposition', 'evidence_quote', 'reason'],
               'properties': {'id': _STR, 'input_sha256': _STR,
                              'disposition': {'type': 'string', 'enum': ['assigned', 'taxonomy_gap']},
                              'domain': _NSTR, 'pillar': _NSTR, 'topic': _NSTR,
                              'evidence_quote': _STR, 'reason': _STR}})
_TAXONOMY = {'type': 'object', 'required': ['title', 'narrative', 'pillars'],
             'properties': {'title': _STR, 'narrative': _STR,
                            'pillars': {'type': 'array', 'items': {
                                'type': 'object', 'required': ['name', 'order', 'why', 'topics'],
                                'properties': {'name': _STR, 'order': {'type': 'integer'}, 'why': _STR,
                                               'topics': {'type': 'array', 'items': _STR}}}},
                            'bridges': {'type': 'array'}}}
TAXONOMY = _items({'type': 'object', 'required': ['domain', 'taxonomy', 'reason', 'coverage_ids'],
                   'properties': {'domain': _STR, 'taxonomy': _TAXONOMY, 'reason': _STR,
                                  'coverage_ids': {'type': 'array', 'items': _STR}}})
REVIEW_BY_DOMAIN = _items({'type': 'object', 'required': ['domain', 'decision', 'reason'],
                           'properties': {'domain': _STR, 'decision': _DECISION, 'reason': _STR}})
DOMAIN_CREATE = {'type': 'object',
                 'required': ['domain', 'moc', 'question', 'track', 'taxonomy', 'migrations',
                              'boundary_reason', 'difference_reason', 'impact_reason'],
                 'properties': {'domain': _STR, 'moc': _STR, 'question': _STR, 'track': _STR,
                                'taxonomy': _TAXONOMY,
                                'relation_words': {'type': 'array', 'items': {
                                    'type': 'object', 'required': ['type', 'inverse', 'gloss'],
                                    'properties': {'type': _STR, 'inverse': _STR, 'gloss': _STR}}},
                                'migrations': {'type': 'array', 'items': {
                                    'type': 'object', 'required': ['id', 'pillar', 'topic'],
                                    'properties': {'id': _STR, 'pillar': _STR, 'topic': _STR}}},
                                'boundary_reason': _STR, 'difference_reason': _STR, 'impact_reason': _STR}}

_QA = {'type': 'object', 'required': ['q', 'a'], 'properties': {'q': _STR, 'a': _STR}}
#: A disagreement on a fact with an existing card: its id, and what differs.
_CONFLICTS = {'type': 'array', 'items': {'type': 'object', 'required': ['with', 'detail'],
                                         'properties': {'with': _STR, 'detail': _STR}}}
_REL = {'type': 'object', 'required': ['type', 'target'],
        'properties': {'type': _STR, 'target': _STR, 'note': _NSTR,
                       'tier': {'type': ['string', 'null'], 'enum': ['extension', None]}}}
from .overlap import COVERAGE as _COVERAGE, LEVELS as _LEVELS  # noqa: E402

CARDS = {
    'type': 'object', 'required': ['cards', 'coverage'],
    'properties': {
        'cards': {'type': 'array', 'items': {
            'type': 'object',
            'required': ['slug', 'title', 'kind', 'domain', 'pillar', 'topic', 'essence', 'propositions',
                         'flashcards', 'source_quotes', 'reason'],
            'properties': {
                'slug': _STR, 'title': _STR, 'aliases': {'type': 'array', 'items': _STR},
                'kind': {'type': 'string', 'enum': ['concept', 'mechanism', 'contrast', 'derivation', 'fact']},
                'status': {'type': 'string', 'enum': ['confirmed', 'needs-review']},
                'domain': _STR, 'pillar': _STR, 'topic': _STR, 'essence': _STR,
                'propositions': {'type': 'array', 'items': _STR},
                'intuition': _NSTR, 'confusions': {'type': 'array', 'items': _STR},
                'flashcards': {'type': 'array', 'items': _QA},
                'relations': {'type': 'array', 'items': _REL},
                'source_quotes': {'type': 'array', 'items': _STR}, 'reason': _STR, 'conflicts': _CONFLICTS,
                'overlap_hint': {'type': ['object', 'null'], 'properties': {
                    'overlap': {'type': 'string', 'enum': list(_LEVELS)},
                    'with': {'type': 'array', 'items': _STR}}}}}},
        'new_topics': {'type': 'array', 'items': {
            'type': 'object', 'required': ['domain', 'pillar', 'name', 'boundary', 'reason'],
            'properties': {'domain': _STR, 'pillar': _STR, 'name': _STR, 'boundary': _STR, 'reason': _STR}}},
        'coverage': {'type': 'array', 'items': {
            'type': 'object', 'required': ['source_quote', 'disposition', 'reason'],
            'properties': {'source_quote': _STR,
                           'disposition': {'type': 'string', 'enum': list(_COVERAGE)},
                           'cards': {'type': 'array', 'items': _STR},
                           'with': {'type': 'array', 'items': _STR},
                           'suggested_pillar': _NSTR, 'reason': _STR}}},
    }}
def _amend_schema() -> dict:
    from .pipelines.amend import SCHEMA
    return SCHEMA


#: The problems a focused repair-round review sends a card back for, beyond an earlier finding not fixed.
BLOCKING = ('fact', 'unreadable', 'quote', 'not_a_unit')
AMEND = _amend_schema()

REVIEW_CARDS = {
    'type': 'object', 'required': ['cards', 'coverage'],
    'properties': {
        'cards': {'type': 'array', 'items': {
            'type': 'object', 'required': ['slug', 'decision', 'reason'],
            'properties': {'slug': _STR, 'decision': _DECISION, 'suggested_domain': _NSTR, 'reason': _STR,
                           'overlap': {'type': ['string', 'null'], 'enum': [*_LEVELS, None]},
                           'with': {'type': ['array', 'null'], 'items': _STR}, 'conflicts': _CONFLICTS,
                           'amend': AMEND, 'notes': {'type': ['array', 'null'], 'items': _STR},
                           'fixed': {'type': ['array', 'null'], 'items': {
                               'type': 'object', 'required': ['finding', 'fixed'],
                               'properties': {'finding': _STR, 'fixed': {'type': 'boolean'}}}},
                           'blocking': {'type': ['string', 'null'], 'enum': [*BLOCKING, None]}}}},
        'new_topics': {'type': 'array', 'items': {
            'type': 'object', 'required': ['domain', 'pillar', 'name', 'decision', 'reason'],
            'properties': {'domain': _STR, 'pillar': _STR, 'name': _STR, 'decision': _DECISION, 'reason': _STR}}},
        'coverage': {'type': 'object', 'required': ['decision', 'reason'],
                     'properties': {'decision': _DECISION, 'reason': _STR,
                                    'missing': {'type': 'array', 'items': _STR}}},
    }}

#: `sb export`: one writing unit (paragraphs per part), and the smoothing step.
EXPORT_WRITE = {'type': 'object', 'required': ['parts'],
                'properties': {'parts': {'type': 'array', 'items': {
                    'type': 'object', 'required': ['part', 'paragraphs'],
                    'properties': {'part': _STR, 'paragraphs': {'type': 'array', 'items': _STR}}}}}}
EXPORT_SMOOTH = {'type': 'object', 'required': ['intro', 'transitions', 'drop'],
                 'properties': {'intro': _STR,
                                'transitions': {'type': 'array', 'items': {
                                    'type': 'object', 'required': ['section', 'text'],
                                    'properties': {'section': _STR, 'text': _STR}}},
                                'drop': {'type': 'array', 'items': _STR}}}


_TYPES = {'string': (str,), 'array': (list,), 'object': (dict,), 'boolean': (bool,), 'null': (type(None),),
          'integer': (int,), 'number': (int, float)}
_SAID = {'string': 'a string', 'array': 'a list', 'object': 'an object', 'boolean': 'true or false',
         'integer': 'an integer', 'number': 'a number'}


def _has_type(value, kind: str) -> bool:
    if isinstance(value, bool) and kind in {'integer', 'number'}:
        return False
    return isinstance(value, _TYPES.get(kind, (object,)))


def type_problems(value, schema: dict, where: str = '') -> list[str]:
    """Where ``value`` has another JSON type than ``schema`` gives that place, e.g. ``cards[0].topic must be a
    string, not dict``.

    Only types: a missing value or a null is left to the pipeline's validator, and so are enums and required
    keys. A validator runs this first, before anything puts an answer's value in a set or sorts it, so an
    object where a name belongs is refused and asked again rather than stopping the run.
    """
    if value is None:
        return []
    kinds = schema.get('type')
    kinds = [kinds] if isinstance(kinds, str) else list(kinds or [])
    if kinds and not any(_has_type(value, k) for k in kinds):
        wanted = ' or '.join(_SAID[k] for k in kinds if k in _SAID)
        return [f'{where or "the answer"} must be {wanted}, not {type(value).__name__}']
    out = []
    if isinstance(value, dict):
        for key, sub in (schema.get('properties') or {}).items():
            if key in value:
                out += type_problems(value[key], sub, f'{where}.{key}' if where else key)
    elif isinstance(value, list) and isinstance(schema.get('items'), dict):
        for i, item in enumerate(value):
            out += type_problems(item, schema['items'], f'{where}[{i}]')
    return out
