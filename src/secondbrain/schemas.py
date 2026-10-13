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
_REL = {'type': 'object', 'required': ['type', 'target'],
        'properties': {'type': _STR, 'target': _STR, 'note': _NSTR,
                       'tier': {'type': ['string', 'null'], 'enum': ['extension', None]}}}
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
                'source_quotes': {'type': 'array', 'items': _STR}, 'reason': _STR}}},
        'new_topics': {'type': 'array', 'items': {
            'type': 'object', 'required': ['domain', 'pillar', 'name', 'boundary', 'reason'],
            'properties': {'domain': _STR, 'pillar': _STR, 'name': _STR, 'boundary': _STR, 'reason': _STR}}},
        'coverage': {'type': 'array', 'items': {
            'type': 'object', 'required': ['source_quote', 'disposition', 'reason'],
            'properties': {'source_quote': _STR,
                           'disposition': {'type': 'string', 'enum': ['card', 'duplicate', 'out_of_scope', 'needs_taxonomy']},
                           'cards': {'type': 'array', 'items': _STR},
                           'duplicate_of': {'type': 'array', 'items': _STR},
                           'suggested_pillar': _NSTR, 'reason': _STR}}},
    }}
REVIEW_CARDS = {
    'type': 'object', 'required': ['cards', 'coverage'],
    'properties': {
        'cards': {'type': 'array', 'items': {
            'type': 'object', 'required': ['slug', 'decision', 'reason'],
            'properties': {'slug': _STR, 'decision': {'type': 'string', 'enum': ['accept', 'revise', 'duplicate']},
                           'duplicate_of': _NSTR, 'reason': _STR}}},
        'new_topics': {'type': 'array', 'items': {
            'type': 'object', 'required': ['domain', 'pillar', 'name', 'decision', 'reason'],
            'properties': {'domain': _STR, 'pillar': _STR, 'name': _STR, 'decision': _DECISION, 'reason': _STR}}},
        'coverage': {'type': 'object', 'required': ['decision', 'reason'],
                     'properties': {'decision': _DECISION, 'reason': _STR,
                                    'missing': {'type': 'array', 'items': _STR}}},
    }}
