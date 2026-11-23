"""`sb explain`: a drill-down explanation of one concept, and a review path that ends at the top again.

Storage keeps three levels (domain, pillar, card; :mod:`secondbrain.levels`). An explanation may go deeper: from
the domain to the pillar to the card, then down the card's prerequisites and parts (``depends_on``, ``has_part``)
to the basic cards. The tree is a view, computed here from the taxonomy and the relations and never stored; a card
met a second time is a reference to where it already stands, so the tree shows the graph instead of copying it.
A pillar asked for as a whole is cut into groups of related cards (Louvain over their relations, as the split
proposals of :mod:`secondbrain.topics`); the groups are explanation steps only, not stored.

A model writes one text per node, from that node's own content and its children's: ``explain`` says what this
level is and names each child (``[[n3]]``) with what it adds, so the reader can take the next step down;
``recap`` (nodes with children) is one sentence read after the children, tying them back to this node. The
program checks every text (each node once, each child named, lengths); a reviewer of another run (another model
family in API mode) judges each node against the same content; revised nodes go back to the writer while the
review policy's repair rounds last (the maintenance-job driver, :func:`secondbrain.runner.run_job`). A node still
revised after that is written with a marker, never dropped.

The output is a Markdown and an HTML page with two parts: the drill-down (top to bottom) and the review path
(bottom to top): the reviewed flashcards of the cards, prerequisites first, and after each group of children the
step back up to their parent with its recap. Nothing is written into the knowledge folders.
"""
from __future__ import annotations

import html as html_lib
import json
import re
from pathlib import Path

from . import config, jobs, labels
from . import layout as layouts
from .cards import essence_of, flashcards_of, propositions_of
from .moc import learning_order
from .schemas import AnswerError
from .store import digest
from .vault import read_snapshot

KIND = 'explain'
METHOD = 'explain/1'
#: Relations that lead down from a card: what it needs first, what it is made of.
DOWN = ('depends_on', 'has_part')
#: Characters of one text, per language: (explain min, explain max, recap max).
LIMITS = {'zh': (20, 420, 200), 'en': (60, 1400, 600)}
#: Cards a pillar of at least this many is cut into groups for; fewer are listed directly.
GROUP_FROM = 9
MAX_GROUPS = 6
MARKER = re.compile(r'\[\[(n\d+)\]\]')

SCHEMA = {
    'type': 'object', 'required': ['nodes'], 'additionalProperties': False,
    'properties': {'nodes': {'type': 'array', 'items': {
        'type': 'object', 'required': ['id', 'explain'], 'additionalProperties': False,
        'properties': {'id': {'type': 'string'}, 'title': {'type': 'string'}, 'explain': {'type': 'string'},
                       'recap': {'type': 'string'}}}}}}
REVIEW_SCHEMA = {
    'type': 'object', 'required': ['items'], 'additionalProperties': False,
    'properties': {'items': {'type': 'array', 'items': {
        'type': 'object', 'required': ['id', 'decision', 'reason'], 'additionalProperties': False,
        'properties': {'id': {'type': 'string'}, 'decision': {'enum': ['accept', 'revise']},
                       'reason': {'type': 'string'}}}}}}

# --------------------------------------------------------------------------
# The tree
# --------------------------------------------------------------------------
def resolve(snapshot, *, query: str | None = None, ids=None, pillar: str | None = None,
            domain: str | None = None) -> dict:
    """What to explain: ``{'kind': 'card'|'pillar'|'domain', ...}``. A query names a card id, a pillar, a domain or
    its map; anything else is searched and its best card is taken."""
    domains = snapshot.catalog.get('domains') or {}
    if ids:
        missing = [i for i in ids if i not in snapshot.cards]
        if missing:
            raise ValueError(f'unknown card ids: {missing}')
        return {'kind': 'card', 'ids': list(ids)}
    if pillar:
        doms = [domain] if domain else sorted(domains)
        for d in doms:
            if any(c.domain == d and c.fm.get('pillar') == pillar for c in snapshot.cards.values()):
                return {'kind': 'pillar', 'domain': d, 'pillar': pillar}
        raise ValueError(f'no cards under pillar {pillar!r}')
    if domain and not query:
        if domain not in domains:
            raise ValueError(f'unknown domain: {domain}')
        return {'kind': 'domain', 'domain': domain}
    if not query or not query.strip():
        raise ValueError('give a concept, --ids, --pillar or --domain')
    q = query.strip()
    if q in snapshot.cards:
        return {'kind': 'card', 'ids': [q]}
    for d, meta in domains.items():
        if q in (d, meta.get('moc')):
            return {'kind': 'domain', 'domain': d}
    for c in snapshot.cards.values():
        if c.fm.get('pillar') == q:
            return {'kind': 'pillar', 'domain': c.domain, 'pillar': q}
    from .search import search
    hits = [h for h in search(snapshot, q, 5) if h.get('id') in snapshot.cards]
    if not hits:
        raise ValueError(f'no card matches {q!r}')
    return {'kind': 'card', 'ids': [hits[0]['id']], 'query': q, 'other_hits': [h['id'] for h in hits[1:]]}


def _clip(text: str, n: int) -> str:
    text = ' '.join((text or '').split())
    return text if len(text) <= n else text[:n - 1] + '…'


def _card_content(card) -> dict:
    return {'card_id': card.id, 'title': card.title, 'domain': card.domain, 'pillar': card.fm.get('pillar'),
            'essence': essence_of(card.body), 'propositions': (propositions_of(card.body) or [])[:10]}


def _down(card) -> list[tuple[str, str]]:
    out = []
    for rel in card.fm.get('relations') or []:
        if isinstance(rel, dict) and rel.get('type') in DOWN and isinstance(rel.get('target'), str):
            out.append((rel['type'], rel['target']))
    return out


def _groups(snapshot, ids: list[str]) -> list[list[str]]:
    """Groups of related cards among ``ids`` (Louvain, the seed of the split proposals); small groups join the group
    they link to most; at most :data:`MAX_GROUPS`, largest first."""
    from .topics import SEED, _graph
    try:
        from networkx.algorithms.community import louvain_communities
    except ImportError:  # without networkx one group
        return [ids]
    g = _graph(snapshot, set(ids))
    comms = sorted((sorted(c) for c in louvain_communities(g, seed=SEED)), key=lambda c: (-len(c), c[0]))
    keep = [set(c) for c in comms[:MAX_GROUPS] if len(c) >= 3] or [set(ids)]
    for c in comms:
        if any(set(c) <= k for k in keep):
            continue
        links = [sum(1 for x in c for y in g.neighbors(x) if y in k) for k in keep]
        keep[max(range(len(keep)), key=lambda i: (links[i], -i))] |= set(c)
    return [sorted(k) for k in sorted(keep, key=lambda k: (-len(k), min(k)))]


def build(snapshot, anchor: dict, *, depth: int = 3, max_children: int = 5, max_nodes: int = 40) -> list[dict]:
    """The drill-down tree of ``anchor`` as a list of nodes (``id``, ``kind``, ``parent``, ``children`` and the
    content a writer may use), root first."""
    domains = snapshot.catalog.get('domains') or {}
    order_index = {}
    nodes, by_id, placed = [], {}, {}

    def add(kind, parent, **content):
        node = {'id': f'n{len(nodes) + 1}', 'kind': kind, 'parent': parent, 'children': [], **content}
        nodes.append(node)
        by_id[node['id']] = node
        if parent:
            by_id[parent]['children'].append(node['id'])
        if kind == 'card':
            placed[content['card_id']] = node['id']
        return node

    def domain_node(d):
        meta = domains.get(d) or {}
        tax = snapshot.taxonomies.get(meta.get('moc')) or {}
        return add('domain', None, title=meta.get('moc') or d, domain=d, question=meta.get('question', ''),
                   narrative=tax.get('narrative', ''))

    def pillar_node(d, name, parent):
        meta = domains.get(d) or {}
        tax = snapshot.taxonomies.get(meta.get('moc')) or {}
        why = next((p.get('why', '') for p in tax.get('pillars') or [] if isinstance(p, dict) and p.get('name') == name), '')
        count = sum(1 for c in snapshot.cards.values() if c.domain == d and c.fm.get('pillar') == name)
        return add('pillar', parent, title=name, domain=d, why=why, cards=count)

    def ordered(ids):
        if not order_index:
            seq, _ = learning_order(list(snapshot.cards.values()))
            order_index.update({c.id: i for i, c in enumerate(seq)})
        return sorted(ids, key=lambda i: (order_index.get(i, 10 ** 6), i))

    def expand(start):  # prerequisites and parts, breadth first
        queue = [(start, 0)]
        while queue:
            node, level = queue.pop(0)
            if level >= depth:
                continue
            card = snapshot.cards[node['card_id']]
            targets = [t for _, t in _down(card) if t in snapshot.cards and t != card.id]
            seen_here = []
            for t in ordered(list(dict.fromkeys(targets)))[:max_children]:
                if len(nodes) >= max_nodes:
                    return
                if t in placed:
                    add('ref', node['id'], target=placed[t], title=snapshot.cards[t].title)
                    continue
                child = add('card', node['id'], **_card_content(snapshot.cards[t]))
                seen_here.append(child)
            queue += [(c, level + 1) for c in seen_here]

    kind = anchor['kind']
    if kind == 'card':
        first = snapshot.cards[anchor['ids'][0]]
        root = domain_node(first.domain)
        pillars = {}
        for cid in anchor['ids']:
            card = snapshot.cards[cid]
            key = (card.domain, card.fm.get('pillar'))
            if key not in pillars:
                parent = root['id'] if card.domain == first.domain else domain_node(card.domain)['id']
                pillars[key] = pillar_node(card.domain, key[1], parent)
            node = add('card', pillars[key]['id'], anchor=True, **_card_content(card))
        for cid in anchor['ids']:
            expand(by_id[placed[cid]])
    elif kind == 'pillar':
        root = domain_node(anchor['domain'])
        pnode = pillar_node(anchor['domain'], anchor['pillar'], root['id'])
        ids = ordered([c.id for c in snapshot.cards.values()
                       if c.domain == anchor['domain'] and c.fm.get('pillar') == anchor['pillar']])
        groups = _groups(snapshot, ids) if len(ids) >= GROUP_FROM else [ids]
        for group in groups:
            members = ordered(group)
            parent = pnode['id']
            if len(groups) > 1:
                parent = add('group', pnode['id'], members=[{'card_id': i, 'title': snapshot.cards[i].title,
                                                              'essence': _clip(essence_of(snapshot.cards[i].body), 160)}
                                                             for i in members])['id']
            shown = members[:max_children]
            for cid in shown:
                add('card', parent, **_card_content(snapshot.cards[cid]))
            if len(members) > len(shown):
                by_id[parent]['more'] = [snapshot.cards[i].title for i in members[len(shown):]]
    else:
        root = domain_node(anchor['domain'])
        meta = domains[anchor['domain']]
        tax = snapshot.taxonomies.get(meta.get('moc')) or {}
        for p in sorted((p for p in tax.get('pillars') or [] if isinstance(p, dict)), key=lambda p: p.get('order', 0)):
            pnode = pillar_node(anchor['domain'], p['name'], root['id'])
            ids = [c.id for c in snapshot.cards.values() if c.domain == anchor['domain'] and c.fm.get('pillar') == p['name']]
            degree = {i: sum(1 for r in snapshot.cards[i].fm.get('relations') or []
                             if isinstance(r, dict) and r.get('target') in ids) for i in ids}
            for cid in ordered(sorted(ids, key=lambda i: (-degree[i], i))[:3]):
                add('card', pnode['id'], **_card_content(snapshot.cards[cid]))
    return nodes


# --------------------------------------------------------------------------
# The job
# --------------------------------------------------------------------------
def prepare(root: Path, *, query=None, ids=None, pillar=None, domain=None, depth: int = 3,
            max_children: int = 5, out: Path | None = None) -> dict:
    """Freeze the tree of one explanation as a job; the same tree and options give the same job."""
    if not 1 <= depth <= 5 or not 1 <= max_children <= 8:
        raise ValueError('--depth must be 1-5 and --max-children 1-8')
    snapshot = read_snapshot(root)
    anchor = resolve(snapshot, query=query, ids=ids, pillar=pillar, domain=domain)
    nodes = build(snapshot, anchor, depth=depth, max_children=max_children)
    language = config.current().card_language
    out_dir = Path(out) if out else Path(root) / layouts.of(root).articles / 'explain'
    packet = {'kind': KIND, 'method_version': METHOD, 'language': language, 'anchor': anchor,
              'limits': dict(zip(('explain_min', 'explain_max', 'recap_max'), LIMITS[language])),
              'nodes': nodes, 'snapshot_fingerprint': snapshot.fingerprint}
    job_id = 'explain-' + digest(jobs.encoded(packet))[:16]
    # where the pages go stays out of the input a model is shown (a local path)
    state = jobs.create(root, job_id, KIND, packet, out=str(out_dir.absolute()))
    if out and state.get('out') != str(Path(out).absolute()):
        state = jobs.update(root, job_id, out=str(Path(out).absolute()))
    return state


def _writable(packet) -> list[dict]:
    return [n for n in packet['nodes'] if n['kind'] != 'ref']


def validate(packet: dict, candidate: dict) -> dict:
    """The program's check of a writer's answer (see the module text); raises :class:`AnswerError` with every
    problem found."""
    if not isinstance(candidate, dict) or not isinstance(candidate.get('nodes'), list):
        raise AnswerError('the answer must be {"nodes": [...]}')
    lim = packet['limits']
    want = {n['id']: n for n in _writable(packet)}
    problems, seen = [], set()
    for i, row in enumerate(candidate['nodes']):
        nid = row.get('id') if isinstance(row, dict) else None
        if nid not in want:
            problems.append(f'nodes[{i}]: id {nid!r} is not a node to write (refs take no text)')
            continue
        if nid in seen:
            problems.append(f'{nid}: written twice')
            continue
        seen.add(nid)
        node = want[nid]
        children = node['children']
        explain, recap = row.get('explain'), row.get('recap')
        if not isinstance(explain, str) or not lim['explain_min'] <= len(explain.strip()) <= lim['explain_max']:
            problems.append(f'{nid}: explain must be {lim["explain_min"]}-{lim["explain_max"]} characters')
            explain = explain if isinstance(explain, str) else ''
        named = set(MARKER.findall(explain))
        if named - set(children):
            problems.append(f'{nid}: explain names {sorted(named - set(children))}, which are not its children')
        if set(children) - named:
            problems.append(f'{nid}: explain must name each child as [[id]] with what it adds; missing '
                            f'{sorted(set(children) - named)}')
        if children:
            if not isinstance(recap, str) or not recap.strip() or len(recap) > lim['recap_max']:
                problems.append(f'{nid}: recap must be one sentence of at most {lim["recap_max"]} characters')
            else:
                tied = set(MARKER.findall(recap))
                need = len(children) if len(children) <= 4 else 3
                if tied - set(children) or len(tied & set(children)) < need:
                    problems.append(f'{nid}: recap must tie its children back to this node, naming '
                                    f'{"each of them" if need == len(children) else "at least 3"} as [[id]]')
        elif recap:
            problems.append(f'{nid}: a node without children has no recap')
        if node['kind'] == 'group':
            title = row.get('title')
            if not isinstance(title, str) or not 2 <= len(title.strip()) <= (24 if packet['language'] == 'zh' else 60):
                problems.append(f'{nid}: a group needs a short title naming what its cards answer')
        elif row.get('title'):
            problems.append(f'{nid}: only group nodes take a title')
    missing = sorted(set(want) - seen, key=lambda x: int(x[1:]))
    if missing:
        problems.append(f'nodes not written: {missing}')
    if problems:
        raise AnswerError('The explanation was refused:\n- ' + '\n- '.join(problems))
    return candidate


def check_review(packet: dict, candidate: dict, review: dict) -> dict:
    items = review.get('items') if isinstance(review, dict) else None
    ids = [x.get('id') for x in items or [] if isinstance(x, dict)]
    want = sorted(n['id'] for n in _writable(packet))
    if sorted(ids) != want:
        raise AnswerError(f'the review must judge every written node exactly once: {want}')
    for x in items:
        if x.get('decision') not in {'accept', 'revise'} or not str(x.get('reason', '')).strip():
            raise AnswerError(f'{x.get("id")}: decision accept|revise with a reason')
    return review


def apply(root: Path, job_id: str) -> dict:
    """Write the pages of a reviewed explanation (outside the knowledge folders); nodes the last review still
    revised are marked."""
    state = jobs.load(root, job_id)
    packet = jobs.read_input(root, job_id)
    candidate = validate(packet, jobs.artifact(root, state, 'candidate'))
    review = jobs.artifact(root, state, 'review')
    unresolved = {x['id']: x['reason'] for x in review['items'] if x['decision'] == 'revise'}
    snapshot = read_snapshot(root)
    page = assemble(packet, candidate, unresolved, snapshot)
    out = Path(state.get('out') or Path(root) / layouts.of(root).articles / 'explain')
    out.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r'[\\/:*?"<>|\s]+', '_', page['title'])[:80] + '_' + job_id[-8:]
    files = {'markdown': out / f'{stem}.md', 'html': out / f'{stem}.html', 'report': out / f'{stem}.report.json'}
    files['markdown'].write_text(to_markdown(page), encoding='utf-8')
    files['html'].write_text(to_html(page), encoding='utf-8')
    runs = [jobs.load_run(root, job_id, r) for r in state.get('runs', [])]
    report = {'job_id': job_id, 'anchor': packet['anchor'], 'nodes': len(packet['nodes']),
              'written': len(candidate['nodes']), 'levels': page['levels'], 'review_steps': len(page['path']),
              'unresolved': unresolved, 'repair_rounds': state.get('repair_round', 0),
              'runs': [{k: r.get(k) for k in ('role', 'step', 'status', 'executor', 'model_requested',
                                               'model_reported', 'same_family')} for r in runs]}
    files['report'].write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return jobs.update(root, job_id, status='applied', outputs={k: str(v) for k, v in files.items()},
                       unresolved=sorted(unresolved), applied_paths=[])


# --------------------------------------------------------------------------
# The pages
# --------------------------------------------------------------------------
def assemble(packet: dict, candidate: dict, unresolved: dict, snapshot) -> dict:
    """The page as data: the nodes with their texts and depth, and the review path (bottom up)."""
    words = labels.explain(packet['language'])
    texts = {r['id']: r for r in candidate['nodes']}
    nodes = {n['id']: dict(n) for n in packet['nodes']}
    for nid, n in nodes.items():
        t = texts.get(nid) or {}
        n['explain'], n['recap'] = t.get('explain', ''), t.get('recap', '')
        if n['kind'] == 'group':
            n['title'] = t.get('title') or n.get('title') or words['group']
        if n['kind'] == 'card':
            card = snapshot.cards.get(n['card_id'])
            n['flashcards'] = (flashcards_of(card.body) or [])[:4] if card else []
        n['unresolved'] = unresolved.get(nid)
    root = packet['nodes'][0]['id']

    def depth_of(nid, d=0):
        nodes[nid]['depth'] = d
        for c in nodes[nid]['children']:
            depth_of(c, d + 1)
    depth_of(root)

    def walk(nid):
        steps, node = [], nodes[nid]
        real = [c for c in node['children'] if nodes[c]['kind'] != 'ref']
        for c in real:
            steps += walk(c)
        if node['kind'] == 'card':
            steps.append({'kind': 'card', 'node': nid})
        if real:
            steps.append({'kind': 'back', 'node': nid})
        return steps
    anchor = packet['anchor']
    title = nodes[root]['title'] if anchor['kind'] == 'domain' else next(
        (n['title'] for n in nodes.values() if n.get('anchor')), None) or (
        anchor.get('pillar') or nodes[root]['title'])
    return {'title': title, 'language': packet['language'], 'root': root, 'nodes': nodes, 'path': walk(root),
            'levels': 1 + max(n['depth'] for n in nodes.values()), 'query': anchor.get('query')}


def _label(page, nid) -> str:
    n = page['nodes'][nid]
    return page['nodes'][n['target']]['title'] if n['kind'] == 'ref' else n['title']


def _once(page, text) -> str:
    """The same name twice: a writer that put a child's name just before the marker that shows it gets one.

    A marker becomes the child's title in bold, so a text written "from the index [[n12]]" reads "from the index
    **the index**" once the title is "the index". The name before a marker, in bold or plain and with only spaces
    between them, goes; the marker keeps the bold one.
    """
    text, out, at = text or '', [], 0
    for m in MARKER.finditer(text):
        nid, piece = m.group(1), text[at:m.start()]
        label = _label(page, nid) if nid in page['nodes'] else None
        if label:
            gap = piece[len(piece.rstrip()):]  # the spaces between the name and the marker stay
            head = piece[:len(piece) - len(gap)]
            for mark in ('**', '*', '_'):
                if len(head) >= len(label) + 2 * len(mark) and head.endswith(f'{mark}{label}{mark}'):
                    head = head[:-len(label) - 2 * len(mark)]
                    break
            else:
                if head.endswith(label):
                    head = head[:-len(label)]
            piece = head + gap
        out.extend((piece, m.group(0)))
        at = m.end()
    out.append(text[at:])
    return ''.join(out)


def _md_text(page, text) -> str:
    text = _once(page, text)
    return MARKER.sub(lambda m: f"**{_label(page, m.group(1))}**" if m.group(1) in page['nodes'] else m.group(0), text)


def to_markdown(page: dict) -> str:
    w = labels.explain(page['language'])
    out = [f"# {w['title'] % page['title']}", '', f"_{w['lead']}_", '', f"## {w['drill']}", '']

    def node(nid, number):
        n = page['nodes'][nid]
        level = n['depth']
        head = '#' * min(3 + level, 6)
        if n['kind'] == 'ref':
            out.extend([f"{'  ' * 0}- {w['seen'] % _label(page, nid)}", ''])
            return
        where = ''
        if n['kind'] == 'card' and n.get('pillar') and n['parent'] and page['nodes'][n['parent']]['kind'] == 'card':
            parent_pillar = page['nodes'][n['parent']].get('pillar')
            if n['pillar'] != parent_pillar:
                where = w['elsewhere'] % n['pillar']
        out.extend([f"{head} {number} {w[n['kind']]} · {n['title']}{where}", ''])
        if n.get('unresolved'):
            out.extend([f"> {w['unreviewed'] % n['unresolved']}", ''])
        out.extend([_md_text(page, n['explain']), ''])
        if n.get('more'):
            out.extend([f"_{w['more'] % w['more_sep'].join(n['more'])}_", ''])
        for i, c in enumerate(n['children'], 1):
            node(c, f'{number}.{i}')
    node(page['root'], '1')
    out.extend([f"## {w['path']}", '', f"_{w['path_lead']}_", ''])
    for i, step in enumerate(page['path'], 1):
        n = page['nodes'][step['node']]
        if step['kind'] == 'card':
            out.append(f"{i}. **{w['card']} · {n['title']}**")
            for fc in n.get('flashcards') or []:
                out.append(f"   - {w['q']}{w['qa_sep']}{fc.get('q', '')}")
                out.append(f"     {w['a']}{w['qa_sep']}{fc.get('a', '')}")
        else:
            out.append(f"{i}. **{w['back'] % n['title']}**")
            out.append(f"   {_md_text(page, n['recap'])}")
        out.append('')
    return '\n'.join(out).rstrip() + '\n'


def _html_text(page, text) -> str:
    esc = html_lib.escape(_once(page, text))
    esc = re.sub(r'`([^`]+)`', r'<code>\1</code>', esc)
    esc = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', esc)
    return MARKER.sub(lambda m: f'<a class="ref" href="#{m.group(1)}">{html_lib.escape(_label(page, m.group(1)))}</a>'
                      if m.group(1) in page['nodes'] else m.group(0), esc)


_CSS = """
:root{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6b66;--line:#e3e0d8;--accent:#2f6f5e;--soft:#eef5f2;--warn:#a5442a}
@media (prefers-color-scheme:dark){:root{--bg:#161716;--fg:#e9e7e1;--muted:#a2a09a;--line:#34352f;--accent:#7fc4ad;--soft:#1f2a26;--warn:#e08a6f}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.65 -apple-system,"PingFang SC","Noto Sans CJK SC",system-ui,sans-serif}
main{max-width:860px;margin:0 auto;padding:24px 16px 64px}h1{font-size:1.6rem;margin:.2em 0}.lead{color:var(--muted)}
.tabs{display:flex;gap:8px;margin:20px 0 12px}.tabs button{border:1px solid var(--line);background:none;color:var(--fg);padding:6px 14px;border-radius:999px;cursor:pointer;font:inherit}
.tabs button[aria-selected=true]{background:var(--accent);color:var(--bg);border-color:var(--accent)}
details{border-left:2px solid var(--line);margin:8px 0 8px 4px;padding:2px 0 2px 14px}details[open]>summary{margin-bottom:6px}
summary{cursor:pointer;list-style:none}summary::-webkit-details-marker{display:none}summary::before{content:"▸ ";color:var(--muted)}details[open]>summary::before{content:"▾ "}
.kind{display:inline-block;font-size:.75rem;color:var(--accent);border:1px solid var(--accent);border-radius:4px;padding:0 5px;margin-right:6px;vertical-align:1px}
.title{font-weight:600}.where{color:var(--muted);font-size:.9rem}.explain{margin:4px 0}.recap{background:var(--soft);padding:6px 10px;border-radius:6px;margin:6px 0}
.seen{color:var(--muted);margin:6px 0}.warn{color:var(--warn);font-size:.9rem}.more{color:var(--muted);font-size:.9rem}a.ref{color:var(--accent);text-decoration:none;border-bottom:1px dotted}
.step{border:1px solid var(--line);border-radius:10px;padding:16px;min-height:180px}.step.back{background:var(--soft)}.count{color:var(--muted);font-size:.9rem;margin-bottom:6px}
.qa{margin:10px 0}.q{font-weight:600}.a{display:none;margin-top:4px}.a.on{display:block}.nav{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}
.nav button,.showa{border:1px solid var(--line);background:none;color:var(--fg);padding:6px 14px;border-radius:8px;cursor:pointer;font:inherit}
@media (max-width:520px){details{padding-left:10px}main{padding:16px}}
"""

_JS = """
const steps=[...document.querySelectorAll('.step')];let at=0;
function show(i){at=Math.max(0,Math.min(steps.length-1,i));steps.forEach((s,k)=>s.hidden=k!==at);
document.querySelectorAll('.a').forEach(a=>a.classList.remove('on'));}
document.querySelectorAll('.showa').forEach(b=>b.onclick=()=>b.closest('.step').querySelectorAll('.a').forEach(a=>a.classList.add('on')));
document.querySelectorAll('[data-go]').forEach(b=>b.onclick=()=>show(at+Number(b.dataset.go)));
document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>{document.querySelectorAll('.tabs button').forEach(x=>x.setAttribute('aria-selected',x===b));
document.querySelectorAll('section[data-tab]').forEach(s=>s.hidden=s.dataset.tab!==b.dataset.tab);});
document.querySelectorAll('a.ref').forEach(a=>a.onclick=()=>{let d=document.querySelector(a.getAttribute('href'));while(d){if(d.tagName==='DETAILS')d.open=true;d=d.parentElement;}});
if(location.hash==='#review')document.querySelector('.tabs button[data-tab=path]').click();
show(0);
"""


def to_html(page: dict) -> str:
    w = labels.explain(page['language'])
    esc = html_lib.escape
    parts = []

    def node(nid):
        n = page['nodes'][nid]
        if n['kind'] == 'ref':
            parts.append(f'<p class="seen">↺ {esc(w["seen"] % _label(page, nid))} '
                         f'(<a class="ref" href="#{n["target"]}">{esc(w["ref"])}</a>)</p>')
            return
        where = ''
        if n['kind'] == 'card' and n['parent'] and page['nodes'][n['parent']]['kind'] == 'card' \
                and n.get('pillar') != page['nodes'][n['parent']].get('pillar'):
            where = f' <span class="where">{esc(w["elsewhere"] % n["pillar"])}</span>'
        opened = ' open' if n['depth'] <= 2 else ''
        parts.append(f'<details id="{nid}"{opened}><summary><span class="kind">{esc(w[n["kind"]])}</span>'
                     f'<span class="title">{esc(n["title"])}</span>{where}</summary>')
        if n.get('unresolved'):
            parts.append(f'<p class="warn">{esc(w["unreviewed"] % n["unresolved"])}</p>')
        parts.append(f'<p class="explain">{_html_text(page, n["explain"])}</p>')
        if n.get('more'):
            parts.append(f'<p class="more">{esc(w["more"] % w["more_sep"].join(n["more"]))}</p>')
        for c in n['children']:
            node(c)
        if n.get('recap'):
            parts.append(f'<p class="recap">↑ {_html_text(page, n["recap"])}</p>')
        parts.append('</details>')
    node(page['root'])
    drill = '\n'.join(parts)
    steps = []
    total = len(page['path'])
    for i, step in enumerate(page['path'], 1):
        n = page['nodes'][step['node']]
        nav = (f'<div class="nav"><button data-go="-1">{esc(w["prev"])}</button>'
               f'<button class="showa">{esc(w["show"])}</button><button data-go="1">{esc(w["next"])}</button></div>')
        if step['kind'] == 'card':
            qa = ''.join(f'<div class="qa"><div class="q">{esc(w["q"] + w["qa_sep"])}{_html_text(page, fc.get("q", ""))}</div>'
                         f'<div class="a">{esc(w["a"] + w["qa_sep"])}{_html_text(page, fc.get("a", ""))}</div></div>'
                         for fc in n.get('flashcards') or [])
            if not qa:
                qa = f'<div class="qa"><div class="a">{_html_text(page, n["explain"])}</div></div>'
            steps.append(f'<div class="step" hidden><div class="count">{esc(w["step"] % (i, total))}</div>'
                         f'<div><span class="kind">{esc(w["card"])}</span><b>{esc(n["title"])}</b></div>{qa}{nav}</div>')
        else:
            steps.append(f'<div class="step back" hidden><div class="count">{esc(w["step"] % (i, total))}</div>'
                         f'<div class="q">↑ {esc(w["back"] % n["title"])}</div>'
                         f'<div class="a">{_html_text(page, n["recap"])}</div>{nav}</div>')
    lang = 'zh-CN' if page['language'] == 'zh' else 'en'
    return f"""<!doctype html>
<html lang="{lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(w['title'] % page['title'])}</title><style>{_CSS}</style>
<script>window.MathJax={{tex:{{inlineMath:[['$','$']],displayMath:[['$$','$$']]}}}};</script>
<script defer src="https://cdn.jsdelivr.net/npm/mathjax@3.2.2/es5/tex-chtml-full.js"></script></head>
<body><main><h1>{esc(w['title'] % page['title'])}</h1><p class="lead">{esc(w['lead'])}</p>
<div class="tabs" role="tablist"><button data-tab="drill" aria-selected="true">{esc(w['tab_drill'])}</button>
<button data-tab="path" aria-selected="false">{esc(w['tab_path'])}</button></div>
<section data-tab="drill"><h2>{esc(w['drill'])}</h2>{drill}</section>
<section data-tab="path" hidden><h2>{esc(w['path'])}</h2><p class="lead">{esc(w['path_lead'])}</p>{''.join(steps)}</section>
</main><script>{_JS}</script></body></html>
"""
