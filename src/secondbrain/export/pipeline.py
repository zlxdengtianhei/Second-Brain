"""The export job: write -> program check -> independent review -> repair -> smooth.

One job of kind ``export`` freezes the skeleton (:mod:`.skeleton`) as its
input. Each writing unit is a chunk of the job and goes through the same
producer/reviewer machinery as ingest (:func:`secondbrain.runner.call_executor`,
:func:`secondbrain.jobs.require_independent_review`): the writer's paragraphs
are checked by the program, a reviewer from another run (another model family
in API mode) judges each paragraph against the cards it cites, and a unit with
a ``revise`` or a program finding goes back to the writer while the review
policy's repair rounds last. A unit still failing after that is kept and
reported (``escalated``), never silently dropped. Then one smoothing call
writes the introduction and the transitions and picks duplicate paragraphs to
drop; the program re-checks the whole article and writes it out.

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
from ..runner import AwaitingSubmission, call_executor, prompt
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


def _validate_review(candidate: dict, payload: dict) -> dict:
    items = payload.get('items')
    if not isinstance(items, list):
        raise ValueError('the review needs an "items" list')
    want = [pid for pid, _, _ in paragraphs(candidate)]
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
        if role != 'producer':
            raise ValueError('the smoothing step has no review')
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
        review_sha = slot['review']['sha256']
        if slot.get('repair_for') != review_sha:
            rounds = int(slot.get('round', 0))
            history = list(slot.get('history') or []) + [{'round': rounds, 'revise': len(revise),
                                                           'paragraphs': len(review['items']),
                                                           'findings': len(found)}]
            if not revise and not found:
                _set_chunk(root, job_id, uid, status='accepted', history=history, unresolved=None)
                continue
            if rounds >= config.review_policy().max_repair_rounds:
                _set_chunk(root, job_id, uid, status='escalated', history=history,
                           unresolved={'review': revise, 'program': found})
                continue
            _set_chunk(root, job_id, uid, round=rounds + 1, history=history, repair_for=review_sha)
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
    if not smooth.get('candidate'):
        steps.append(('produce', SMOOTH, note(SMOOTH, 'produce', head + prompt('export_smooth.md')),
                      _smooth_packet(root, job_id, state, packet), schemas.EXPORT_SMOOTH, 'producer'))
    return steps


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
    """Run ``steps``; returns ``(handoffs, failures)``."""
    def one(step):
        kind, chunk, text, model_packet, schema, role = step
        executor = producer if role == 'producer' else reviewer
        try:
            call_executor(root, job_id, executor, role=role, step=kind, prompt_text=text,
                          packet=model_packet, output_schema=schema, chunk=chunk, tex_backslashes=True)
            return None, None
        except AwaitingSubmission as waiting:
            return waiting.handoff, None
        except ExecutorFailure as failure:
            return None, (chunk, kind, failure.error)
    host = 'host' in {producer.name, reviewer.name}
    if host or workers <= 1 or len(steps) == 1:
        results = [one(s) for s in steps]
    else:
        with ThreadPoolExecutor(max_workers=min(workers, len(steps))) as pool:
            results = list(pool.map(one, steps))
    return [h for h, _ in results if h], [f for _, f in results if f]


def advance(root: Path, job_id: str, *, producer, reviewer, checker=None, workers: int = 4,
            out_dir: Path | None = None, html: bool = False) -> dict:
    """Run every step that can run now; write the article once all steps are done."""
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
        result = {'job_id': job_id, 'status': 'complete', 'report': report}
        if out_dir is not None:
            result['files'] = write_files(article, report, Path(out_dir), html=html)
        jobs._internal_update(root, job_id, status='complete', report=report)
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
                        blocks.append({'id': gid, 'text': text})
                        rows.append((gid, text, True))
        trans = transitions.get(section['pillar'])
        if trans:
            rows.append((f"transition/{section['pillar']}", trans, False))
        sections.append({'pillar': section['pillar'], 'transition': trans, 'blocks': blocks})
    intro = [p.strip() for p in smooth['intro'].split('\n\n') if p.strip()]
    rows += [(f'intro/{i}', p, False) for i, p in enumerate(intro, 1)]

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
                                  if (chunks.get(u['id']) or {}).get('status') == 'escalated'),
        'smooth': {'duplicates_found': len(state.get('smooth_duplicates') or []), 'dropped': dropped,
                   'drop_refused': refused, 'transitions': len(transitions)},
        'runs': _runs_summary(root, state['job_id']),
        'checker': getattr(checker, 'name', None),
    }
    models = report['runs']['models']
    meta = {'title': packet['moc'], 'generated': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'writer': ', '.join(models.get('producer', [])) or None,
            'reviewer': ', '.join(models.get('reviewer', [])) or None,
            'mode': packet['mode'], 'cards': len(packet['cards']), 'unused_cards': len(unused),
            'escalated_units': len(report['escalated_units']), 'job': state['job_id'],
            'source': 'sb export (generated view; rebuild instead of editing)'}
    article = {'title': packet['moc'], 'language': packet['language'], 'meta': meta, 'intro': intro,
               'sections': sections, 'unused': unused}
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
