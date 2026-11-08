"""The export job: write -> program check -> independent review -> repair -> smooth.

One job of kind ``export`` freezes the skeleton (:mod:`.skeleton`) as its
input. Each writing unit is a chunk of the job and goes through the same
producer/reviewer machinery as ingest (:func:`secondbrain.runner.call_executor`,
:func:`secondbrain.jobs.require_independent_review`): the writer's paragraphs
are checked by the program, a reviewer from another run (another model family
in API mode) judges each paragraph against the cards it cites, and a unit with
a ``revise`` or a program finding goes back to the writer while the review
policy's repair rounds last. A unit still failing after that is kept and
written with a marker line before each paragraph the review or the program
did not pass (``escalated``; the paragraphs are on the owner's list, `sb status
--owner-items`), never silently dropped or passed. Then one smoothing call
writes the introduction and the transitions and picks duplicate paragraphs to
drop; the same reviewer judges the introduction and the transitions, which go
back to the smoothing step under the same rules; the program re-checks the
whole article and writes it out.

``sequential`` mode writes unit after unit, each seeing the previous unit's
last paragraph; ``parallel`` mode writes every unit at once. Host executors
hand every runnable step off at the same time; call :func:`advance` again
after the sub-agents submitted.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from .. import config, jobs, schemas, vault
from ..executors.base import ExecutorFailure
from ..locks import job_lock
from ..runner import AwaitingSubmission, fail_call, finish_call, prompt, start_call
from ..store import digest, vault_lock, write_json
from . import checks, render, skeleton

KIND = 'export'
SMOOTH = 'smooth'
DONE = frozenset({'accepted', 'escalated'})
#: Executor failures a step may have before the export stops (each is a new run).
MAX_FAILURES = 3


# --------------------------------------------------------------------------
# Preparing
# --------------------------------------------------------------------------
def prepare(root: Path, *, domain: str | None = None, moc: str | None = None, ids: list[str] | None = None,
            mode: str = 'sequential', tag: str = '', language: str | None = None) -> dict:
    """Freeze the skeleton of one export as a job; idempotent for the same cards and options."""
    snapshot = vault.read_snapshot(root)
    packet = skeleton.build(snapshot, domain=domain, moc=moc, ids=ids, mode=mode,
                            language=language or config.current().card_language, tag=tag)
    job_id = 'export-' + digest(jobs.encoded(packet))[:16]
    return jobs.create(root, job_id, KIND, packet)


# --------------------------------------------------------------------------
# Paragraph ids and packets
# --------------------------------------------------------------------------
def paragraphs(candidate: dict) -> list[tuple[str, str, str]]:
    """``(paragraph id, part id, text)`` of a unit's answer; ids are ``p<part>.<n>``."""
    out = []
    for part in candidate.get('parts') or []:
        for n, text in enumerate(part.get('paragraphs') or [], 1):
            out.append((f"{part['part']}.{n}", part['part'], text))
    return out


def _unit(packet: dict, uid: str) -> dict:
    for unit in packet['units']:
        if unit['id'] == uid:
            return unit
    raise ValueError(f'The export has no unit {uid}')


def _unit_card_ids(unit: dict) -> list[str]:
    return [cid for part in unit['parts'] for cid in part['cards']]


def _outline(packet: dict, upto: int | None) -> list[dict]:
    rows = []
    for i, unit in enumerate(packet['units']):
        rows.append({'unit': unit['id'], 'section': unit['pillar'],
                     'headings': [p['heading'] for p in unit['parts'] if p['heading']],
                     'written': upto is not None and i < upto})
    return rows


def write_packet(packet: dict, unit: dict, previous: str | None) -> dict:
    """What the writer sees for one unit."""
    index = [u['id'] for u in packet['units']].index(unit['id'])
    sequential = packet['mode'] == 'sequential'
    return {'article_title': packet['moc'], 'language': packet['language'],
            'length_unit': packet['length_unit'], 'unit': unit['id'],
            'section': {'pillar': unit['pillar'], 'why': unit['why'],
                        'first_in_section': unit['first_in_section']},
            'parts': [{'part': p['part'], 'heading': p['heading'], 'cards': p['cards'],
                       'target_length': p['target_length']} for p in unit['parts']],
            'cards': [{k: packet['cards'][cid][k] for k in ('id', 'title', 'cite', 'body')}
                      for cid in _unit_card_ids(unit)],
            'relations': unit['relations'],
            'outline': _outline(packet, index if sequential else None),
            'previous_paragraph': previous if sequential else None}


def review_packet(packet: dict, unit: dict, candidate: dict) -> dict:
    """What the reviewer sees: the paragraphs, the cards they cite and the unit's cards."""
    index = checks.CardIndex.from_input(packet)
    shown, rows = [], []
    for pid, _, text in paragraphs(candidate):
        cites = checks.citations(text)
        rows.append({'id': pid, 'text': text, 'cites': cites})
        shown += [index.by_link[c]['id'] for c in cites if c in index.by_link]
    shown += _unit_card_ids(unit)
    ordered = list(dict.fromkeys(shown))
    return {'article_title': packet['moc'], 'language': packet['language'], 'unit': unit['id'],
            'paragraphs': rows,
            'cards': [{k: packet['cards'][cid][k] for k in ('id', 'title', 'cite', 'body')} for cid in ordered],
            'unit_cards': [packet['cards'][cid]['cite'] for cid in _unit_card_ids(unit)]}


# --------------------------------------------------------------------------
# Submission validation (called by jobs.submit for every run of this kind)
# --------------------------------------------------------------------------
def _validate_write(unit: dict, payload: dict) -> dict:
    parts = payload.get('parts')
    if not isinstance(parts, list):
        raise ValueError('the answer needs a "parts" list')
    want = [p['part'] for p in unit['parts']]
    got = [p.get('part') if isinstance(p, dict) else None for p in parts]
    if got != want:
        raise ValueError(f'parts must be exactly {want} in this order, got {got}')
    clean = []
    for part in parts:
        paras = part.get('paragraphs')
        if not isinstance(paras, list) or not paras or \
                not all(isinstance(t, str) and t.strip() for t in paras):
            raise ValueError(f"part {part['part']} needs a list of non-empty paragraphs")
        clean.append({'part': part['part'], 'paragraphs': [t.strip() for t in paras]})
    return {'parts': clean}


def smooth_paragraphs(candidate: dict) -> list[tuple[str, str]]:
    """``(id, text)`` of what the smoothing step wrote: ``intro/<n>`` paragraphs, then ``transition/<section>``."""
    intro = [p.strip() for p in str(candidate.get('intro') or '').split('\n\n') if p.strip()]
    return ([(f'intro/{i}', p) for i, p in enumerate(intro, 1)]
            + [(f"transition/{t['section']}", t['text']) for t in candidate.get('transitions') or []])


def _validate_review(candidate: dict, payload: dict, want: list[str] | None = None) -> dict:
    items = payload.get('items')
    if not isinstance(items, list):
        raise ValueError('the review needs an "items" list')
    want = [pid for pid, _, _ in paragraphs(candidate)] if want is None else want
    got = [x.get('id') if isinstance(x, dict) else None for x in items]
    if sorted(got, key=str) != sorted(want):
        missing = sorted(set(want) - set(got))
        extra = sorted(set(map(str, got)) - set(want))
        raise ValueError(f'one review item per paragraph; missing {missing}, unknown or repeated {extra}')
    for x in items:
        if x.get('decision') not in {'accept', 'revise'} or not str(x.get('reason') or '').strip():
            raise ValueError(f"item {x.get('id')}: decision must be accept or revise, with a reason")
    return payload


def _validate_smooth(packet: dict, payload: dict, allowed_drops: set) -> dict:
    if not isinstance(payload.get('intro'), str) or not payload['intro'].strip():
        raise ValueError('the answer needs a non-empty "intro"')
    names = [s['pillar'] for s in packet['sections']][1:]
    transitions = payload.get('transitions')
    if not isinstance(transitions, list):
        raise ValueError('"transitions" must be a list')
    seen = set()
    for row in transitions:
        if not isinstance(row, dict) or row.get('section') not in names or row['section'] in seen \
                or not str(row.get('text') or '').strip():
            raise ValueError(f'each transition names one later section once ({names}) and has text')
        seen.add(row['section'])
    drop = payload.get('drop')
    if not isinstance(drop, list) or not set(drop) <= allowed_drops:
        raise ValueError(f'"drop" may only name paragraphs listed in duplicates: {sorted(allowed_drops)}')
    return payload


def validate_submission(packet, payload, *, role, candidate, root, chunk, state):
    if chunk == SMOOTH:
        if role != 'producer':  # the same reviewer judges the introduction and the transitions
            return _validate_review(candidate, payload, [i for i, _ in smooth_paragraphs(candidate)])
        allowed = {d[k] for d in (state.get('smooth_duplicates') or []) for k in ('a', 'b')}
        return _validate_smooth(packet, payload, allowed)
    unit = _unit(packet, chunk)
    if role == 'producer':
        return _validate_write(unit, payload)
    return _validate_review(candidate, payload)


# --------------------------------------------------------------------------
# Program checks per unit
# --------------------------------------------------------------------------
def unit_findings(packet: dict, unit: dict, candidate: dict, errors: dict | None) -> list[dict]:
    index = checks.CardIndex.from_input(packet)
    found, cited = [], set()
    for pid, _, text in paragraphs(candidate):
        found += checks.check_paragraph(pid, text, index, errors)
        cited.update(index.by_link[c]['id'] for c in checks.citations(text) if c in index.by_link)
    for cid in _unit_card_ids(unit):
        if cid not in cited:
            found.append({'paragraph': None, 'code': 'card_not_cited',
                          'detail': f"[[{packet['cards'][cid]['cite']}]] is never cited"})
    return found


# --------------------------------------------------------------------------
# Driving the job
# --------------------------------------------------------------------------
def _set_chunk(root: Path, job_id: str, chunk: str, **fields) -> None:
    with vault_lock(root):
        state = jobs.load(root, job_id)
        state.setdefault('chunks', {}).setdefault(chunk, {}).update(fields)
        state['updated_at'] = jobs.now()
        write_json(root, jobs.location(job_id), state)


def _head(job_id: str) -> str:
    return prompt('common.md').format(kind=KIND, job_id=job_id) + '\n'


def _previous_paragraph(root, state, packet, index: int) -> str | None:
    if index == 0:
        return None
    prev = packet['units'][index - 1]['id']
    slot = (state.get('chunks') or {}).get(prev) or {}
    if not slot.get('candidate'):
        return None
    paras = paragraphs(jobs.artifact(root, state, 'candidate', prev))
    return paras[-1][2] if paras else None


def _plan(root, job_id, state, packet, errors_for, feedback) -> list[tuple]:
    """Steps that can run now, as ``(step, chunk, prompt, packet, schema, role)``; settles finished units.

    A unit whose review (or program check) asks for changes gets one repair
    step per round; the decision is recorded against that review's bytes, so
    planning again before the repair is submitted re-issues the same step.
    """
    chunks = state.get('chunks') or {}
    head = _head(job_id)
    steps, pending = [], []

    def note(chunk, kind, text):
        message = feedback.get((chunk, kind))
        if message:
            text += '\n## Your previous answer was rejected by the validator\n\n' + message + '\n'
        return text

    for i, unit in enumerate(packet['units']):
        uid = unit['id']
        slot = chunks.get(uid) or {}
        if slot.get('status') in DONE:
            continue
        if not slot.get('candidate'):
            pending.append(uid)
            if packet['mode'] == 'sequential' and i and not (chunks.get(packet['units'][i - 1]['id']) or {}).get('candidate'):
                continue
            steps.append(('produce', uid, note(uid, 'produce', head + prompt('export_write.md')),
                          write_packet(packet, unit, _previous_paragraph(root, state, packet, i)),
                          schemas.EXPORT_WRITE, 'producer'))
            continue
        candidate = jobs.artifact(root, state, 'candidate', uid)
        if not slot.get('review'):
            pending.append(uid)
            steps.append(('review', uid, note(uid, 'review', head + prompt('export_review.md')),
                          review_packet(packet, unit, candidate), schemas.REVIEW_BY_ID, 'reviewer'))
            continue
        review = jobs.require_independent_review(root, state, uid)
        revise = [x for x in review['items'] if x['decision'] == 'revise']
        found = unit_findings(packet, unit, candidate, errors_for([t for _, _, t in paragraphs(candidate)]))
        if _settled(root, job_id, uid, slot, review, revise, found):
            continue
        pending.append(uid)
        text = (head + prompt('export_write.md') + '\n' + prompt('export_repair.md') + '\n```json\n'
                + json.dumps({'review_findings': revise, 'program_findings': found}, ensure_ascii=False)
                + '\n```\n')
        repair = dict(write_packet(packet, unit, _previous_paragraph(root, state, packet, i)),
                      previous_candidate=candidate)
        steps.append(('repair', uid, note(uid, 'repair', text), repair, schemas.EXPORT_WRITE, 'producer'))
    if steps or pending:
        return steps
    smooth = chunks.get(SMOOTH) or {}
    if smooth.get('status') in DONE:
        return steps
    if not smooth.get('candidate'):
        steps.append(('produce', SMOOTH, note(SMOOTH, 'produce', head + prompt('export_smooth.md')),
                      _smooth_packet(root, job_id, state, packet), schemas.EXPORT_SMOOTH, 'producer'))
        return steps
    candidate = jobs.artifact(root, state, 'candidate', SMOOTH)
    if not smooth.get('review'):
        steps.append(('review', SMOOTH, note(SMOOTH, 'review', head + prompt('export_smooth_review.md')),
                      smooth_review_packet(root, job_id, state, packet, candidate), schemas.REVIEW_BY_ID,
                      'reviewer'))
        return steps
    review = jobs.require_independent_review(root, state, SMOOTH)
    revise = [x for x in review['items'] if x['decision'] == 'revise']
    if _settled(root, job_id, SMOOTH, smooth, review, revise, []):
        return steps
    text = (head + prompt('export_smooth.md') + '\n' + prompt('export_smooth_repair.md') + '\n```json\n'
            + json.dumps({'review_findings': revise}, ensure_ascii=False) + '\n```\n')
    steps.append(('repair', SMOOTH, note(SMOOTH, 'repair', text),
                  dict(_smooth_packet(root, job_id, state, packet), previous_candidate=candidate),
                  schemas.EXPORT_SMOOTH, 'producer'))
    return steps


def _settled(root, job_id, chunk, slot, review, revise, found) -> bool:
    """Settle ``chunk`` after its review, as ingest does: True when it is done (``accepted``, or ``escalated``
    when the repair rounds ran out: kept, marked and listed for the owner); False when it goes back for a repair
    round. Recorded against the review's bytes, so planning again before the repair is submitted asks the same."""
    review_sha = slot['review']['sha256']
    if slot.get('repair_for') == review_sha:
        return False
    rounds = int(slot.get('round', 0))
    history = list(slot.get('history') or []) + [{'round': rounds, 'revise': len(revise),
                                                   'paragraphs': len(review['items']), 'findings': len(found)}]
    if not revise and not found:
        _set_chunk(root, job_id, chunk, status='accepted', history=history, unresolved=None)
        return True
    if rounds >= config.review_policy().max_repair_rounds:
        _set_chunk(root, job_id, chunk, status='escalated', history=history,
                   unresolved={'review': revise, 'program': found})
        return True
    _set_chunk(root, job_id, chunk, round=rounds + 1, history=history, repair_for=review_sha)
    return False


def smooth_review_packet(root, job_id, state, packet, candidate) -> dict:
    """What the reviewer sees of the smoothing step: what that step was shown, and what it wrote."""
    shown = _smooth_packet(root, job_id, state, packet)
    return {'article_title': shown['article_title'], 'language': shown['language'], 'narrative': shown['narrative'],
            'sections': shown['sections'],
            'paragraphs': [{'id': pid, 'text': text} for pid, text in smooth_paragraphs(candidate)]}


def _final_units(root, state, packet) -> dict:
    return {u['id']: jobs.artifact(root, state, 'candidate', u['id']) for u in packet['units']}


def _smooth_packet(root, job_id, state, packet) -> dict:
    units = _final_units(root, state, packet)
    rows = [(uid, f'{uid}/{pid}', text) for uid, cand in units.items() for pid, _, text in paragraphs(cand)]
    dups = checks.duplicate_pairs(rows)
    if state.get('smooth_duplicates') != dups:
        jobs.update(root, job_id, smooth_duplicates=dups)
    text_of = {gid: text for _, gid, text in rows}
    sections = []
    for section in packet['sections']:
        entries = []
        for uid in section['units']:
            paras = paragraphs(units[uid])
            unit = _unit(packet, uid)
            entries.append({'unit': uid, 'headings': [p['heading'] for p in unit['parts'] if p['heading']],
                            'first_paragraph': paras[0][2], 'last_paragraph': paras[-1][2]})
        sections.append({'pillar': section['pillar'], 'why': section['why'], 'units': entries})
    return {'article_title': packet['moc'], 'language': packet['language'], 'narrative': packet['narrative'],
            'sections': sections,
            'duplicates': [{**d, 'a_text': text_of[d['a']], 'b_text': text_of[d['b']]} for d in dups]}


def _run_steps(root, job_id, steps, producer, reviewer, workers) -> tuple[list, list]:
    """Run ``steps``; returns ``(handoffs, failures)``.

    The runs are recorded and their answers submitted here, one after another; only the model calls run on the
    worker threads (up to ``workers`` at once), so the job's state is only read and written by this thread.
    """
    handoffs, failures, started = [], [], []
    for kind, chunk, text, model_packet, schema, role in steps:
        executor = producer if role == 'producer' else reviewer
        try:
            task = start_call(root, job_id, executor, role=role, step=kind, prompt_text=text, packet=model_packet,
                              output_schema=schema, chunk=chunk, tex_backslashes=True)
        except AwaitingSubmission as waiting:
            handoffs.append(waiting.handoff)
            continue
        started.append((chunk, kind, executor, task))

    def model(item):
        _, _, executor, task = item
        try:
            return executor.run(task), None
        except Exception as exc:  # recorded on the run below, then raised
            return None, exc
    host = 'host' in {producer.name, reviewer.name}
    if host or workers <= 1 or len(started) <= 1:
        results = [model(item) for item in started]
    else:
        with ThreadPoolExecutor(max_workers=min(workers, len(started))) as pool:
            results = list(pool.map(model, started))
    raised = None
    for (chunk, kind, _, task), (result, exc) in zip(started, results):
        if exc is not None:
            fail_call(root, task, exc)
            raised = raised or exc
            continue
        try:
            finish_call(root, task, result)
        except AwaitingSubmission as waiting:
            handoffs.append(waiting.handoff)
        except ExecutorFailure as failure:
            failures.append((chunk, kind, failure.error))
    if raised is not None:
        raise raised
    return handoffs, failures


def _has_formulas(packet: dict) -> bool:
    return any(checks.scan_math(c['body'])[0] for c in packet['cards'].values())


def advance(root: Path, job_id: str, *, producer, reviewer, checker=None, workers: int = 4,
            out_dir: Path | None = None, html: bool = False, formula_check: bool = True) -> dict:
    """Run every step that can run now; write the article once all steps are done.

    When the cards in scope have formulas, ``checker`` must work (:func:`checks.preflight`) before any model is
    asked; otherwise :class:`checks.CheckerUnavailable` is raised, unless ``formula_check`` is False: then the
    formulas are not parse-checked and the article and its report say so.
    """
    unchecked = None
    if _has_formulas(jobs.read_input(root, job_id)):
        why = checks.preflight(checker)
        if why and formula_check:
            raise checks.CheckerUnavailable(f'The formulas of this export cannot be parse-checked ({why}); no model '
                                            f'was asked. {checks.INSTALL}')
        if why or not formula_check:
            unchecked, checker = why or 'sb export --no-formula-check', None
    cache: dict = {}

    def errors_for(texts):
        if checker is None:
            return None
        todo = [t for t in texts if t not in cache]
        if todo:
            found = checks.parse_errors(todo, checker) or {}
            for t in todo:
                cache[t] = {k: v for k, v in found.items()
                            if any(m.tex == k[0] and m.display == k[1] for m in checks.scan_math(t)[0])}
        merged = {}
        for t in texts:
            merged.update(cache[t])
        return merged

    failures: dict = {}
    feedback: dict = {}
    with job_lock(root, job_id):
        while True:
            state = jobs.load(root, job_id)
            packet = jobs.read_input(root, job_id)
            steps = _plan(root, job_id, state, packet, errors_for, feedback)
            if not steps:
                break
            handoffs, failed = _run_steps(root, job_id, steps, producer, reviewer, workers)
            if handoffs:
                return {'job_id': job_id, 'status': 'awaiting_submission', 'handoffs': handoffs}
            for chunk, kind, error in failed:
                key = (chunk, kind)
                failures[key] = failures.get(key, 0) + 1
                if failures[key] >= MAX_FAILURES or not error.get('retryable', False):
                    raise ExecutorFailure(error)
                if error.get('code') == 'INVALID_OUTPUT':
                    feedback[key] = str(error.get('message', ''))[:2000]
        state = jobs.load(root, job_id)
        article, report = assemble(root, state, packet, errors_for, checker)
        if unchecked:
            article['unchecked'] = report['formula_check_skipped'] = unchecked
        result = {'job_id': job_id, 'status': 'complete', 'report': report}
        if out_dir is not None:
            result['files'] = write_files(article, report, Path(out_dir), html=html)
        jobs._internal_update(root, job_id, status='complete', report=report, files=result.get('files'))
        return result


# --------------------------------------------------------------------------
# Assembling, final checks, report
# --------------------------------------------------------------------------
def _runs_summary(root, job_id) -> dict:
    runs = jobs.list_runs(root, job_id)
    calls, tokens_in, tokens_out, models = {}, 0, 0, {}
    starts, ends = [], []
    for run in runs:
        key = f"{run['role']}:{run['step']}:{run['status']}"
        calls[key] = calls.get(key, 0) + 1
        usage = run.get('usage') or {}
        tokens_in += int(usage.get('input_tokens') or 0)
        tokens_out += int(usage.get('output_tokens') or 0)
        model = run.get('model_reported') or run.get('model_requested')
        if run['status'] == 'submitted':
            models.setdefault(run['role'], set()).add(model)
        starts.append(run['started_at'])
        if run.get('ended_at'):
            ends.append(run['ended_at'])
    span = None
    if starts and ends:
        span = round((datetime.fromisoformat(max(ends)) - datetime.fromisoformat(min(starts))).total_seconds())
    return {'calls': calls, 'calls_total': len(runs), 'input_tokens': tokens_in, 'output_tokens': tokens_out,
            'models': {k: sorted(map(str, v)) for k, v in models.items()}, 'seconds_first_to_last_run': span}


#: The longest reason a marker line shows (the first sentence of the reviewer's reason, or the program's finding).
FLAG_CHARS = 160
#: Ends of a sentence besides a full stop followed by a space: the CJK full stop, exclamation and question marks.
SENTENCE_ENDS = ''.join(map(chr, (0x3002, 0xFF01, 0xFF1F))) + '!?'


def _one_sentence(text) -> str:
    text = ' '.join(str(text or '').split())
    for i, ch in enumerate(text):
        if ch in SENTENCE_ENDS or (ch == '.' and (i + 1 == len(text) or text[i + 1] == ' ')):
            text = text[:i + 1]
            break
    return text if len(text) <= FLAG_CHARS else text[:FLAG_CHARS - 1] + '…'


def _flags(state) -> dict:
    """``{paragraph id: reason}`` of the paragraphs an escalated unit or smoothing step still had a ``revise`` or
    a program finding for (ids ``u01/p1.2``, ``intro/1``, ``transition/<section>``)."""
    out = {}
    for chunk, slot in (state.get('chunks') or {}).items():
        if slot.get('status') != 'escalated':
            continue
        unresolved = slot.get('unresolved') or {}
        prefix = '' if chunk == SMOOTH else f'{chunk}/'
        for item in unresolved.get('review') or []:
            out.setdefault(prefix + item['id'], _one_sentence(item.get('reason')))
        for finding in unresolved.get('program') or []:
            if finding.get('paragraph'):
                out.setdefault(prefix + finding['paragraph'], _one_sentence(f"{finding['code']}: {finding['detail']}"))
    return out


def assemble(root, state, packet, errors_for, checker) -> tuple[dict, dict]:
    """The article as blocks, after the smoothing answer; and the program-check report."""
    units = _final_units(root, state, packet)
    smooth = jobs.artifact(root, state, 'candidate', SMOOTH)
    index = checks.CardIndex.from_input(packet)
    cites_of = {f'{uid}/{pid}': {index.by_link[c]['id'] for c in checks.citations(text) if c in index.by_link}
                for uid, cand in units.items() for pid, _, text in paragraphs(cand)}
    dropped, refused = [], []
    for gid in smooth.get('drop') or []:
        rest = set().union(*(v for k, v in cites_of.items() if k != gid and k not in dropped))
        if cites_of.get(gid, set()) <= rest:
            dropped.append(gid)
        else:
            refused.append(gid)
    transitions = {t['section']: t['text'] for t in smooth.get('transitions') or []}
    flags = _flags(state)
    sections, rows = [], []
    for section in packet['sections']:
        blocks = []
        for uid in section['units']:
            unit = _unit(packet, uid)
            heading_of = {p['part']: p['heading'] for p in unit['parts']}
            for part in units[uid]['parts']:
                if heading_of.get(part['part']):
                    blocks.append({'heading': heading_of[part['part']]})
                for n, text in enumerate(part['paragraphs'], 1):
                    gid = f"{uid}/{part['part']}.{n}"
                    if gid not in dropped:
                        blocks.append({'id': gid, 'text': text, **({'flag': flags[gid]} if gid in flags else {})})
                        rows.append((gid, text, True))
        trans = transitions.get(section['pillar'])
        if trans:
            rows.append((f"transition/{section['pillar']}", trans, False))
        sections.append({'pillar': section['pillar'], 'transition': trans,
                         'transition_flag': flags.get(f"transition/{section['pillar']}"), 'blocks': blocks})
    intro_rows = [(pid, text) for pid, text in smooth_paragraphs(smooth) if pid.startswith('intro/')]
    intro = [text for _, text in intro_rows]
    rows += [(pid, text, False) for pid, text in intro_rows]

    errors = errors_for([t for _, t, _ in rows])
    findings, cited = [], set()
    formulas = parse_fail = 0
    numbers_all = checks.card_numbers(''.join(index.card_text(c) for c in packet['cards'].values()))
    formulas_all = {checks.squash(m.tex) for c in packet['cards'].values() for m in checks.scan_math(c['body'])[0]}
    for gid, text, needs_cite in rows:
        if needs_cite:
            found = checks.check_paragraph(gid, text, index, errors)
            cited.update(cites_of.get(gid, set()))
        else:  # intro and transitions cite nothing: check them against every card of the scope
            found = [f for f in checks.check_paragraph(gid, text, index, errors) if f['code'] != 'no_citation']
            found = [f for f in found if not (
                (f['code'] == 'number_not_in_cited_cards' and f['detail'] in numbers_all) or
                (f['code'] == 'formula_not_in_cited_cards'
                 and any(checks.squash(f['detail']) in x for x in formulas_all)))]
        findings += found
        for m in checks.scan_math(text)[0]:
            formulas += 1
            if errors is not None and (m.tex, m.display) in errors:
                parse_fail += 1
    codes: dict = {}
    for f in findings:
        codes[f['code']] = codes.get(f['code'], 0) + 1
    pandoc = sum(codes.get(c, 0) for c in ('inline_math_space_after_open', 'inline_math_space_before_close',
                                           'inline_math_digit_after_close', 'display_math_not_own_line',
                                           'table_math_pipe', 'stray_dollar'))
    unused = [packet['cards'][cid]['cite'] for cid in sorted(packet['cards']) if cid not in cited]
    chunks = state.get('chunks') or {}
    length = checks.char_count('\n'.join(t for _, t, _ in rows))
    report = {
        'moc': packet['moc'], 'mode': packet['mode'], 'tag': packet.get('tag', ''),
        'cards': len(packet['cards']), 'cards_cited': len(cited),
        'coverage': round(len(cited) / len(packet['cards']), 4) if packet['cards'] else None,
        'unused': unused, 'paragraphs': sum(1 for _, _, c in rows if c),
        'numbers_not_in_cited_cards': codes.get('number_not_in_cited_cards', 0),
        'formulas': formulas, 'formula_parse_checked': errors is not None,
        'formula_parse_ok': (formulas - parse_fail) if errors is not None else None,
        'formula_parse_rate': (round((formulas - parse_fail) / formulas, 4) if formulas else 1.0)
        if errors is not None else None,
        'formula_not_in_cited_cards': codes.get('formula_not_in_cited_cards', 0),
        'pandoc_rule_violations': pandoc,
        'unknown_links': codes.get('unknown_link', 0),
        'finding_codes': codes, 'findings': findings,
        'length': length,
        'units': {u['id']: {'status': (chunks.get(u['id']) or {}).get('status'),
                            'repair_rounds': int((chunks.get(u['id']) or {}).get('round', 0)),
                            'history': (chunks.get(u['id']) or {}).get('history'),
                            'unresolved': (chunks.get(u['id']) or {}).get('unresolved')}
                  for u in packet['units']},
        'escalated_units': sorted(u['id'] for u in packet['units']
                                  if (chunks.get(u['id']) or {}).get('status') == 'escalated')
        + ([SMOOTH] if (chunks.get(SMOOTH) or {}).get('status') == 'escalated' else []),
        'flagged': [{'paragraph': gid, 'reason': reason} for gid, reason in flags.items()
                    if gid not in dropped],
        'smooth': {'duplicates_found': len(state.get('smooth_duplicates') or []), 'dropped': dropped,
                   'drop_refused': refused, 'transitions': len(transitions),
                   'status': (chunks.get(SMOOTH) or {}).get('status'),
                   'repair_rounds': int((chunks.get(SMOOTH) or {}).get('round', 0)),
                   'unresolved': (chunks.get(SMOOTH) or {}).get('unresolved')},
        'runs': _runs_summary(root, state['job_id']),
        'checker': getattr(checker, 'name', None),
    }
    models = report['runs']['models']
    meta = {'title': packet['moc'], 'generated': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'writer': ', '.join(models.get('producer', [])) or None,
            'reviewer': ', '.join(models.get('reviewer', [])) or None,
            'mode': packet['mode'], 'cards': len(packet['cards']), 'unused_cards': len(unused),
            'escalated_units': len(report['escalated_units']), 'flagged_paragraphs': len(report['flagged']),
            'job': state['job_id'],
            'source': 'sb export (generated view; rebuild instead of editing)'}
    article = {'title': packet['moc'], 'language': packet['language'], 'meta': meta, 'intro': intro,
               'intro_flags': [flags.get(pid) for pid, _ in intro_rows], 'sections': sections, 'unused': unused}
    return article, report


def write_files(article: dict, report: dict, out_dir: Path, *, html: bool = False) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    date = time.strftime('%Y%m%d')
    stem = f"{article['title']}_{date}"
    md_text = render.to_markdown(article)
    files = {'markdown': out_dir / f'{stem}.md', 'report': out_dir / f'{stem}.report.json'}
    files['markdown'].write_text(md_text, encoding='utf-8')
    files['report'].write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if html:
        files['html'] = out_dir / f'{stem}.html'
        files['html'].write_text(render.to_html(md_text, article['title'], article['language']), encoding='utf-8')
    return {k: str(v) for k, v in files.items()}
