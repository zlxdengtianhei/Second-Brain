"""What the program does with a producer's answer before and after its checks: repair, keep what passes, ask again.

A producer answers a whole chunk at once: every card, the coverage table and the links between cards. Before, one
mechanical slip anywhere (two links between one pair of cards, a quote that lost its bold marks, a ``/`` in a title,
a card named by its id) refused the whole answer, and the next ask carried the errors but not the answer, so the model
wrote everything again and made other slips. Now, for every producer answer of a chunk (API and host mode alike):

1. :func:`repair` makes the fixes that write no knowledge text, by a fixed table of rules (:data:`RULES`), and records
   each one (``program_repairs``, shown to the reviewer and kept in the job result). A guard checks that the
   knowledge text (essence, propositions, intuition, contrasts, review cards) is unchanged but for card ids written
   as ``[[title]]``; a repair that touched more is a fault of the program and stops.
2. The answer is checked. When what is left names only some cards (:func:`split_problems`), the cards that pass are
   kept and the answer is refused for those cards alone (one refusal against the slot's budget): the next ask carries
   them as they were given, with their error lines (:func:`fix_packet`), and asks for those cards only; the answer is
   merged back (:func:`merge`) and checked as a whole. A problem of the whole chunk (the coverage table, new topics)
   refuses the answer as before.
3. The last answer the slot may give keeps the cards that pass (:func:`held_back`): the others wait for the owner
   with their error lines, never dropped without a word.
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from typing import Callable

from ..cards import TITLE_FORBIDDEN

#: Full-width look-alikes of the characters a title may not hold: an ASCII character's full-width form is U+FEE0 above it.
FULL_WIDTH = str.maketrans({c: chr(ord(c) + 0xFEE0) for c in TITLE_FORBIDDEN})
#: Marks a model may add to a quote that the source does not have (Markdown bold), and the shortest quote matched.
MARKS = ('**', '__')
MIN_QUOTE = 8


@dataclass
class Context:
    """What the repairs may read: the chunk, the catalog, and the cards a relation or an id may name."""
    text: str
    catalog: dict
    prefix: str
    targets: set = field(default_factory=set)  # existing ids and slugs of other cards a relation may name
    title_of: dict = field(default_factory=dict)  # {id: title} of existing cards


@dataclass(frozen=True)
class Rule:
    name: str
    what: str
    fn: Callable[[dict, Context], list]


def _cards(payload: dict) -> list:
    return [c for c in payload.get('cards') or [] if isinstance(c, dict)]


def _slug(card: dict):
    return card.get('slug') if isinstance(card.get('slug'), str) else None


# --- the rules --------------------------------------------------------------------------------------------------------
def _titles(payload: dict, ctx: Context) -> list:
    out, renamed = [], {}
    for c in _cards(payload):
        title = c.get('title')
        if isinstance(title, str) and title.translate(FULL_WIDTH) != title:
            c['title'] = renamed[title] = title.translate(FULL_WIDTH)
            out.append({'rule': 'title', 'card': _slug(c), 'before': title, 'after': c['title']})
    if renamed:  # links to a renamed card, in the answer's own text, follow it
        pattern = re.compile(r'\[\[(' + '|'.join(re.escape(t) for t in renamed) + r')(?=[\]|#])')
        for c in _cards(payload):
            _each_text(c, lambda s: pattern.sub(lambda m: '[[' + renamed[m.group(1)], s))
    return out


def _ids(payload: dict, ctx: Context) -> list:
    if not ctx.prefix:
        return []
    own = {ctx.prefix + c['slug']: c.get('title') for c in _cards(payload) if _slug(c)}
    names = {**ctx.title_of, **own}
    pattern = re.compile(re.escape(ctx.prefix) + r'[a-z0-9][a-z0-9-]*')
    out = []
    for c in _cards(payload):
        def sub(text, card=c):
            def one(m):
                title = names.get(m.group(0))
                if not isinstance(title, str):
                    return m.group(0)  # not a card it can name: the check says so
                out.append({'rule': 'card_id', 'card': _slug(card), 'before': m.group(0), 'after': f'[[{title}]]'})
                return f'[[{title}]]'
            return pattern.sub(one, text)
        _each_text(c, sub)
    return out


def _normalized(text: str) -> tuple[str, list]:
    chars, at, i = [], [], 0
    while i < len(text):
        if text.startswith(MARKS, i):
            i += 2
            continue
        if not text[i].isspace():
            chars.append(text[i])
            at.append(i)
        i += 1
    return ''.join(chars), at


def snap_quote(quote, text: str):
    """The slice of ``text`` that ``quote`` is once bold marks and blanks are set aside, when it is there exactly once
    (and long enough to mean something); else None. No fuzzy matching."""
    if not isinstance(quote, str) or quote in text:
        return None
    q, _ = _normalized(quote)
    t, at = _normalized(text)
    if len(q) < MIN_QUOTE:
        return None
    k = t.find(q)
    if k < 0 or t.find(q, k + 1) >= 0:
        return None
    start, end = at[k], at[k + len(q) - 1] + 1
    for mark in MARKS:  # a slice that would cut a bold span in two takes the mark next to it
        if text[start:end].count(mark) % 2:
            if text[max(0, start - 2):start] == mark:
                start -= 2
            elif text[end:end + 2] == mark:
                end += 2
    return text[start:end]


def _quotes(payload: dict, ctx: Context) -> list:
    out = []
    for c in _cards(payload):
        quotes = c.get('source_quotes')
        if isinstance(quotes, list):
            for i, q in enumerate(quotes):
                exact = snap_quote(q, ctx.text)
                if exact:
                    quotes[i] = exact
                    out.append({'rule': 'quote', 'card': _slug(c), 'before': q, 'after': exact})
    for i, row in enumerate(payload.get('coverage') or []):
        if isinstance(row, dict):
            exact = snap_quote(row.get('source_quote'), ctx.text)
            if exact:
                out.append({'rule': 'quote', 'coverage': i, 'before': row['source_quote'], 'after': exact})
                row['source_quote'] = exact
    return out


def _relations(card: dict) -> list:
    rels = card.get('relations')
    return rels if isinstance(rels, list) else []


def _targets(payload: dict, ctx: Context) -> list:
    slugs = {_slug(c) for c in _cards(payload)} - {None}
    out = []
    for c in _cards(payload):
        keep = []
        for r in _relations(c):
            target = r.get('target') if isinstance(r, dict) else None
            if isinstance(target, str) and target != _slug(c) and target not in slugs and target not in ctx.targets:
                out.append({'rule': 'relation_target', 'card': _slug(c), 'dropped': dict(r)})
                continue
            keep.append(r)
        if len(keep) != len(_relations(c)):
            c['relations'] = keep
    return out


def _order(ctx: Context) -> dict:
    from ..catalog import relation_types
    return {t: i for i, t in enumerate(relation_types(ctx.catalog))}


def _pairs(payload: dict, ctx: Context) -> list:
    """Two cards joined by more than one relation, counting the back link the program writes: the one first in the
    catalog's order of relation types is kept (on the card that holds it), the others dropped."""
    inverse = ctx.catalog.get('inverse_relations') or {}
    order = _order(ctx)
    by = {_slug(c): c for c in _cards(payload) if _slug(c)}
    groups: dict = {}
    for a, c in by.items():
        for r in _relations(c):
            if isinstance(r, dict) and isinstance(r.get('type'), str) and isinstance(r.get('target'), str):
                b = r['target']
                key = (a, b) if b not in by or a < b else (b, a)
                kind = r['type'] if key[0] == a else inverse.get(r['type'], r['type'])
                groups.setdefault(key, []).append((kind, a, r))
    out = []
    for (a, b), rows in sorted(groups.items()):
        kinds = {k for k, _, _ in rows}
        if len(kinds) < 2 or (len(kinds) == 2 and inverse.get(min(kinds)) == max(kinds)):
            continue  # one relation, or the same pair in opposite directions (the direction rule takes it)
        keep = min(rows, key=lambda row: (order.get(row[2]['type'], len(order)), row[1] != a))
        for kind, holder, r in rows:
            if r is keep[2]:
                continue
            by[holder]['relations'] = [x for x in by[holder]['relations'] if x is not r]
            out.append({'rule': 'relation_pair', 'card': holder, 'dropped': dict(r), 'kept': dict(keep[2])})
    return out


def _directions(payload: dict, ctx: Context) -> list:
    """Relations that say opposite things: a card holding a type and its inverse to one target keeps the one first in
    the catalog's order; "A t B" with "B t A" loses the one on the card later in slug order."""
    inverse = ctx.catalog.get('inverse_relations') or {}
    order = _order(ctx)
    by = {_slug(c): c for c in _cards(payload) if _slug(c)}
    out = []
    for a in sorted(by):
        for r in list(_relations(by[a])):
            if not (isinstance(r, dict) and isinstance(r.get('type'), str) and isinstance(r.get('target'), str)):
                continue
            other, b = inverse.get(r['type']), r['target']
            if not other or other == r['type'] or r not in _relations(by[a]):
                continue
            twin = next((x for x in _relations(by[a]) if isinstance(x, dict) and x.get('target') == b
                         and x.get('type') == other), None)
            if twin is not None:
                drop = max((r, twin), key=lambda x: order.get(x['type'], len(order)))
                by[a]['relations'] = [x for x in by[a]['relations'] if x is not drop]
                out.append({'rule': 'relation_direction', 'card': a, 'dropped': dict(drop)})
                continue
            mirror = next((x for x in _relations(by.get(b) or {}) if isinstance(x, dict) and x.get('target') == a
                           and x.get('type') == r['type']), None) if b in by and a < b else None
            if mirror is not None:
                by[b]['relations'] = [x for x in by[b]['relations'] if x is not mirror]
                out.append({'rule': 'relation_direction', 'card': b, 'dropped': dict(mirror)})
    return out


#: The repairs, in the order they run. Each writes no knowledge text; :func:`repair` checks that.
RULES = (
    Rule('title', 'a character a title may not hold is written full width (links to the card follow)', _titles),
    Rule('card_id', 'a card named by its id in the text is named by [[title]]', _ids),
    Rule('quote', 'a quote that is a slice of the chunk once bold marks and blanks are set aside, there once, '
                  'becomes that slice', _quotes),
    Rule('relation_target', 'a relation to a card that is neither an existing card nor a card of the source is '
                            'dropped', _targets),
    Rule('relation_pair', 'of two or more relations between one pair of cards (back links counted), the first in '
                          'the catalog\'s order of types is kept', _pairs),
    Rule('relation_direction', 'of two relations that say opposite things, one is dropped', _directions),
)

#: The fields that hold knowledge text; a repair may change them only by naming a card by [[title]] for its id.
KNOWLEDGE = ('essence', 'propositions', 'intuition', 'confusions', 'flashcards')


def _each_text(card: dict, fn) -> None:
    for key in ('essence', 'intuition'):
        if isinstance(card.get(key), str):
            card[key] = fn(card[key])
    for key in ('propositions', 'confusions'):
        if isinstance(card.get(key), list):
            card[key] = [fn(x) if isinstance(x, str) else x for x in card[key]]
    if isinstance(card.get('flashcards'), list):
        card['flashcards'] = [{**f, **{k: fn(f[k]) for k in ('q', 'a') if isinstance(f.get(k), str)}}
                              if isinstance(f, dict) else f for f in card['flashcards']]
    for r in _relations(card):
        if isinstance(r, dict) and isinstance(r.get('note'), str):
            r['note'] = fn(r['note'])


class RepairFault(RuntimeError):
    """A repair changed knowledge text: a fault of the program, never of the answer."""


def _canonical(payload: dict, ctx: Context) -> Callable[[str], str]:
    """One way to write the text a repair may change, built from the answer as given: a card id that names a card
    becomes ``[[title]]`` (its title written full width where a title may not hold a character), and a link to a
    card of the answer by its title as given becomes a link by that title written full width. The guard of
    :func:`repair` writes both sides so before it compares them; nothing is ever turned back, so a card that already
    names a card by ``[[title]]`` and also by its id compares equal once the id is repaired."""
    titles = {c['title']: c['title'].translate(FULL_WIDTH) for c in _cards(payload) if isinstance(c.get('title'), str)}
    names = {k: v for k, v in ctx.title_of.items() if isinstance(v, str)}
    if ctx.prefix:
        names.update({ctx.prefix + c['slug']: titles[c['title']] for c in _cards(payload)
                      if _slug(c) and isinstance(c.get('title'), str)})
    ids = re.compile(re.escape(ctx.prefix) + r'[a-z0-9][a-z0-9-]*') if ctx.prefix else None
    renamed = {t: f for t, f in titles.items() if t != f}
    links = re.compile(r'\[\[(' + '|'.join(re.escape(t) for t in renamed) + r')(?=[\]|#])') if renamed else None

    def canon(text: str) -> str:
        if links is not None:
            text = links.sub(lambda m: '[[' + renamed[m.group(1)], text)
        if ids is not None:
            text = ids.sub(lambda m: f'[[{names[m.group(0)]}]]' if m.group(0) in names else m.group(0), text)
        return text
    return canon


def _knowledge(card: dict, canon: Callable[[str], str]) -> str:
    """The card's knowledge text (:data:`KNOWLEDGE`), every string of it written by ``canon``."""
    view = copy.deepcopy({k: card.get(k) for k in KNOWLEDGE})
    _each_text(view, canon)
    return json.dumps(view, ensure_ascii=False, sort_keys=True)


def repair(payload, ctx: Context) -> tuple:
    """``(payload, records)``: a copy of ``payload`` with the :data:`RULES` applied, and what each changed. A payload
    that is not an object is given back as it is. Raises :class:`RepairFault` when the knowledge text of a card
    changed beyond naming a card by title: before and after are compared in one written form (:func:`_canonical`)."""
    if not isinstance(payload, dict):
        return payload, []
    out = copy.deepcopy(payload)
    canon = _canonical(payload, ctx)
    before = {id(c): (_slug(c), _knowledge(c, canon)) for c in _cards(out)}
    records = []
    for rule in RULES:
        records += rule.fn(out, ctx)
    for c in _cards(out):
        slug, was = before.get(id(c), (None, None))
        if was is not None and _knowledge(c, canon) != was:
            raise RepairFault(f'a repair changed the knowledge text of {slug!r}; nothing was kept')
    return out, records


# --- what is left after the checks --------------------------------------------------------------------------------------
_CARD_LINE = re.compile(r'^cards\[([^\]]+)\](?: and cards\[([^\]]+)\])?')


def split_problems(lines: list[str], payload: dict) -> tuple[dict, list]:
    """``({slug: [lines]}, [lines of the whole chunk])``: a line that names a card (``cards[<slug>]``, or two of them)
    is that card's; any other line is the chunk's. A card named by its position is named by its slug when it has one."""
    slugs = [_slug(c) if isinstance(c, dict) else None for c in payload.get('cards') or []]
    by, block = {}, []
    for line in lines:
        m = _CARD_LINE.match(line)
        if not m:
            block.append(line)
            continue
        for name in filter(None, m.groups()):
            slug = slugs[int(name)] if name.isdigit() and int(name) < len(slugs) else name
            by.setdefault(slug or name, []).append(line)
    return by, block


def problem_lines(message: str) -> list[str]:
    """The lines of a validator's refusal (``"... validation:\\n- a\\n- b"``)."""
    rows = [x[2:] for x in str(message).split('\n') if x.startswith('- ')]
    return rows or [str(message)]


def _place(row: dict) -> tuple:
    return row.get('domain'), row.get('pillar'), row.get('topic', row.get('name'))


def held_back(payload: dict, held: set) -> tuple[dict, list]:
    """``(payload, records)``: ``payload`` without the cards in ``held``; a coverage row loses them, a row left with
    no card is dropped, and so is a new topic only they were filed under (each recorded). Relations to them stay:
    like a card that waits for the owner after its review, the link is carried until the card is written."""
    out = copy.deepcopy(payload)
    out['cards'] = [c for c in out.get('cards') or [] if not (isinstance(c, dict) and _slug(c) in held)]
    used = {_place(c) for c in out['cards'] if isinstance(c, dict)}
    topics, records = [], []
    for t in out.get('new_topics') or []:
        if isinstance(t, dict) and _place(t) not in used:
            records.append({'rule': 'held_back', 'new_topic': dict(t)})
            continue
        topics.append(t)
    if 'new_topics' in out:
        out['new_topics'] = topics
    rows = []
    for i, row in enumerate(out.get('coverage') or []):
        if isinstance(row, dict) and row.get('disposition') == 'card' and set(row.get('cards') or []) & held:
            left = [s for s in row['cards'] if s not in held]
            if not left:
                records.append({'rule': 'held_back', 'coverage': i, 'dropped': dict(row)})
                continue
            row = {**row, 'cards': left}
        rows.append(row)
    out['coverage'] = rows
    return out, records


def merge(draft: dict, answer: dict) -> dict:
    """The answer kept from before (``draft``) with the cards of ``answer`` in place of the ones with their slugs
    (a new slug is added); a non-empty coverage table or list of new topics in ``answer`` replaces the draft's."""
    out = copy.deepcopy(draft)
    cards = list(out.get('cards') or [])
    at = {_slug(c): i for i, c in enumerate(cards) if isinstance(c, dict) and _slug(c)}
    for c in answer.get('cards') or []:
        if isinstance(c, dict) and _slug(c) in at:
            cards[at[_slug(c)]] = copy.deepcopy(c)
        else:
            cards.append(copy.deepcopy(c))
    out['cards'] = cards
    for key in ('coverage', 'new_topics'):
        if isinstance(answer.get(key), list) and answer[key]:
            out[key] = copy.deepcopy(answer[key])
    return out


def fix_packet(draft: dict) -> dict:
    """What the next ask shows of a draft: the cards that failed the checks, as they were given, with their lines."""
    held = draft.get('held') or {}
    cards = [c for c in (draft.get('payload') or {}).get('cards') or [] if isinstance(c, dict) and _slug(c) in held]
    return {'cards': cards, 'problems': held,
            'kept': sorted(_slug(c) for c in (draft.get('payload') or {}).get('cards') or []
                           if isinstance(c, dict) and _slug(c) and _slug(c) not in held)}


def strip_fence(text: str):
    """``text`` without one Markdown code fence around the whole of it (```` ```json ... ``` ````), or None when it is
    not wrapped so. Only the outermost fence goes; nothing else in the text is changed."""
    m = re.fullmatch(r'\s*```[A-Za-z0-9_-]*[ \t]*\r?\n(.*?)\r?\n```\s*', text, re.S)
    return m.group(1) if m else None
