"""V9 regression: every emitted diagram parses and renders in Obsidian's Mermaid.

The corpus puts each hazardous label (Mermaid keywords, syntax characters,
entities, HTML, math, odd Unicode) into every label position of every form,
plus seeded random diagrams, and runs them all through tools/mermaid_check.

This test needs Node.js, the tool's npm dependencies and a Chromium binary.
It is skipped unless they are present:
  MERMAID_CHECK_BROWSER  path to Chrome/Chromium (or a standard install location)
  MERMAID_CHECK_MODULES  directory holding node_modules (default: the tool dir)
  MERMAID_CHECK_RANDOM   number of random diagrams (default 600)
"""
from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from secondbrain.diagrams.emit import emit
from secondbrain.diagrams.ir import DiagramIR, Group, Link, Node, clean_text

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "mermaid_check" / "check.mjs"

HAZARDS = [
    "plain", "中文标签", "外层循环满 k 次？", "dist[n] > 0x3f3f3f3f / 2？",
    # keywords of the flowchart, sequence and state grammars
    "end", "END", "End", "graph", "flowchart", "style", "call", "class", "click", "subgraph",
    "direction", "default", "linkStyle", "classDef", "participant", "actor", "Note", "note",
    "loop", "alt", "else", "opt", "par", "and", "rect", "critical", "break", "activate",
    "deactivate", "autonumber", "title", "box", "create", "destroy", "link", "links", "as",
    "state", "choice", "over", "right of", "left of", "TB", "LR", "n1", "g1", "P1",
    "end end", "A as B", "accTitle: t", "accDescr: d",
    # statements that look like syntax
    "[*]", "<<choice>>", "<<fork>>", "--", "-->", "->>", "-->>", "-)", "--x", "==>", "-.->",
    "---", "~~~", "o--o", "x--x", "a --> b", "a -> b", "n1->>n2: hi", "n1 --> n2",
    'state "x" as y', "Note over n1: x", "classDef x fill:#f00;", "style n1 fill:#f00;",
    "linkStyle 0 stroke:#f00;", "style:#quot;x#quot;", 'style:"a"', "classDef:#a;",
    "click n1 call f()", "subgraph x [y]",
    # punctuation and brackets
    "A;B", ";", "a#b", "#", "#quot;", "#35;", "#lt;", "#9829;", "&amp;", "&lt;b&gt;", "&#123;",
    "&", "&&", "R&D", '"quoted"', '"', "it's", "'", "{}", "{a}", "{{a}}", "[a]", "[[a]]",
    "[(a)]", "([a])", "((a))", "(a)", "f(x)", "dist[e.b]", "C(h,w)", "[/api/v1]", "[\\x]",
    ">a]", "a|b", "|a|", "||", "|", ":", "a: b", "::", "key:: value", ",", "a, b", ".", "...",
    "…", "?", "？", "!", "@", "a@b", "^", "*", "**bold**", "_x_", "~x~", "+", "+a", "-a", "- a",
    "1. item", "# heading", "## h", "> quote", "=", "==", "a=b", "/", "//", "\\", "}", "]", ")",
    "http://example.com/a?b=c&d=e#f", "C:\\path\\x", "a\\nb", "\\n",
    # html, markdown, math, comments, directives
    "<br>", "<br/>", "<b>bold</b>", "<script>alert(1)</script>", "<img src=x onerror=alert(1)>",
    "`code`", "`", "$x^2$", "$$x^2$$", "\\(x\\)", "%%", "%% c", "%%{init: {\"theme\":\"dark\"}}%%",
    "100%", "50% off", "%{", "---",
    # unicode and whitespace
    "C₋₁", "x²", "α→β", "→", "⇒", "😀", "👨\u200d👩\u200d👧", "العربية", "עברית", "é", "e\u0301",
    "a\u200bb", "tab\there", "line1\nline2", "  padded  ", "x" * 60, "长" * 40,
]

WORDS = ["请求", "响应", "缓存", "命中", "回表", "索引", "load", "store", "retry", "token",
         "状态", "超时", "ACK", "SYN", "页表", "偏移", "x", "mid", "lo", "hi"]
FLOW_ROLES = ["step", "decision", "store", "input", "output", "start", "end"]


def _flow(label: str, slot: str) -> tuple[DiagramIR, list[str]]:
    role = slot if slot in FLOW_ROLES else "step"
    nodes = [Node("a", label if slot in FLOW_ROLES + ["structure"] else "开始节点", role),
             Node("b", "第二步", "step"), Node("c", "第三步", "step")]
    edges = [Link("a", "b", label if slot == "edge" else "条件"), Link("b", "c"),
             Link("c", "c", label if slot == "selfloop" else "重试")]
    groups = (Group(label, ("b", "c")),) if slot == "group" else ()
    form = "structure" if slot == "structure" else "flow"
    return DiagramIR("draw", form=form, question="q", nodes=tuple(nodes), edges=tuple(edges),
                     groups=groups), [label]


def _sequence(label: str, slot: str) -> tuple[DiagramIR, list[str]]:
    nodes = (Node("u", label if slot == "alias" else "用户", "actor"), Node("s", "服务器", "actor"))
    msgs = [Link("u", "s", "请求")]
    if slot in ("sync", "reply", "async"):
        msgs.append(Link("s", "u", label, style=slot))
    elif slot == "note1":
        msgs.append(Link("s", "s", label, note=True))
    elif slot == "note2":
        msgs.append(Link("u", "s", label, note=True))
    return DiagramIR("draw", form="sequence", question="q", nodes=nodes, messages=tuple(msgs)), [label]


def _state(label: str, slot: str) -> tuple[DiagramIR, list[str]]:
    role = slot if slot in ("start", "end") else "state"
    nodes = (Node("a", label if slot in ("state", "start", "end") else "空闲", role),
             Node("b", "运行", "state"), Node("c", "结束态", "end" if role != "end" else "state"))
    edges = (Link("a", "b", label if slot == "transition" else "启动"), Link("b", "c", "完成"),
             Link("b", "b", label if slot == "selfloop" else "心跳"))
    return DiagramIR("draw", form="state", question="q", nodes=nodes, edges=edges), [label]


SLOTS = ([(_flow, s) for s in FLOW_ROLES + ["edge", "group", "structure", "selfloop"]]
         + [(_sequence, s) for s in ("alias", "sync", "reply", "async", "note1", "note2")]
         + [(_state, s) for s in ("state", "start", "end", "transition", "selfloop")])


def _random_label(rng: random.Random) -> str:
    parts = [rng.choice(WORDS) for _ in range(rng.randint(1, 3))]
    if rng.random() < 0.6:
        parts.insert(rng.randint(0, len(parts)), rng.choice(HAZARDS))
    label = rng.choice(["", " "]).join(parts)
    return label if clean_text(label) else "x"


def _random_ir(rng: random.Random) -> tuple[DiagramIR, list[str]]:
    form = rng.choice(["flow", "structure", "sequence", "state"])
    n = rng.randint(1, 9)
    keys = [f"k{i}" for i in range(n)]
    labels: list[str] = []
    if form == "sequence":
        nodes = tuple(Node(k, _random_label(rng), "actor") for k in keys[:5])
        msgs = []
        for _ in range(rng.randint(1, 8)):
            a, b = rng.choice(nodes).key, rng.choice(nodes).key
            msgs.append(Link(a, b, _random_label(rng), style=rng.choice(["sync", "reply", "async"]),
                             note=rng.random() < 0.2))
        labels = [x.label for x in nodes] + [m.label for m in msgs]
        return DiagramIR("draw", form=form, question="q", nodes=nodes, messages=tuple(msgs)), labels
    roles = {"flow": FLOW_ROLES, "structure": ["step", "store", "input", "output", "actor"],
             "state": ["state", "start", "end"]}[form]
    nodes = tuple(Node(k, _random_label(rng), rng.choice(roles)) for k in keys)
    edges: list[Link] = []
    for _ in range(rng.randint(0, 12)):
        a, b = rng.choice(keys), rng.choice(keys)
        if a == b and any(e.src == e.dst == a for e in edges):
            continue  # V1 allows one self-loop per node
        edges.append(Link(a, b, _random_label(rng) if rng.random() < 0.7 else ""))
    edges = tuple(edges)
    groups: list[Group] = []
    if form in ("flow", "structure") and n >= 3 and rng.random() < 0.5:
        free = keys[:]
        rng.shuffle(free)
        while len(free) >= 2 and rng.random() < 0.7:
            take = [free.pop() for _ in range(rng.randint(1, min(3, len(free))))]
            groups.append(Group(_random_label(rng), tuple(take)))
    labels = [x.label for x in nodes] + [e.label for e in edges if e.label] + [g.label for g in groups]
    return DiagramIR("draw", form=form, question="q", nodes=nodes, edges=edges, groups=tuple(groups)), labels


def corpus(n_random: int = 600, seed: int = 20261013) -> list[tuple[str, str, list[str]]]:
    """(id, mermaid, labels expected in the rendered text) for the whole corpus."""
    out = []
    for k, h in enumerate(dict.fromkeys(HAZARDS)):
        if not clean_text(h):
            continue
        for make, slot in SLOTS:
            ir, labels = make(h, slot)
            out.append((f"{ir.form}:{slot}:{k}:{h}", emit(ir), labels))
    rng = random.Random(seed)
    for i in range(n_random):
        ir, labels = _random_ir(rng)
        marker = "%% diagram v1 props=P1,P2 sha=0123abcd" if rng.random() < 0.5 else None
        out.append((f"random:{i}", emit(ir, marker=marker), labels))
    return out


def _squash(s: str) -> str:
    return "".join(clean_text(s).split())


def _tool_ready() -> tuple[str, Path] | None:
    node = shutil.which("node")
    modules = Path(os.environ.get("MERMAID_CHECK_MODULES") or TOOL.parent)
    if not node or not (modules / "node_modules" / "mermaid").is_dir():
        return None
    return node, modules


def test_corpus_is_deterministic_and_one_line_per_label():
    a, b = corpus(50), corpus(50)
    assert [x[1] for x in a] == [x[1] for x in b]
    for _, mm, _ in a:
        lines = mm.split("\n")
        body = lines[2:] if lines[0].startswith("%% ") else lines[1:]
        for line in body:
            assert line.startswith("  "), line


def run_tool(src: Path, out: Path, *flags: str, timeout: int = 3600) -> dict:
    """Run tools/mermaid_check on src; results by id.

    The tool exits 0 or 1 with one result per input line. On anything else (a
    browser that did not start or stopped responding on a loaded machine) it
    is run once more; if that fails too, the test fails with the exit code
    and the tool's own message instead of a bare count mismatch.
    """
    node, modules = _tool_ready()
    expected = sum(1 for line in src.read_text(encoding="utf-8").splitlines() if line.strip())
    tries = []
    for _ in range(2):
        out.unlink(missing_ok=True)
        try:
            proc = subprocess.run([node, str(TOOL), "--modules", str(modules), *flags, str(src), str(out)],
                                  capture_output=True, text=True, timeout=timeout)
            code, err = proc.returncode, proc.stderr
        except subprocess.TimeoutExpired as e:
            code, err = "timeout", f"no result within {timeout} s; {e.stderr or ''}"
        lines = out.read_text(encoding="utf-8").splitlines() if out.exists() else []
        if code in (0, 1) and len(lines) == expected:
            return {r["id"]: r for r in map(json.loads, lines)}
        tries.append(f"exit {code}, {len(lines)} of {expected} results; stderr: {str(err).strip()[-600:]}")
    pytest.fail("tools/mermaid_check failed twice:\n" + "\n".join(tries))


@pytest.mark.skipif(_tool_ready() is None, reason="tools/mermaid_check dependencies not installed")
def test_every_emitted_diagram_parses_and_keeps_its_labels(tmp_path):
    items = corpus(int(os.environ.get("MERMAID_CHECK_RANDOM", "600")))
    src = tmp_path / "corpus.jsonl"
    src.write_text("".join(json.dumps({"id": i, "text": mm}, ensure_ascii=False) + "\n"
                           for i, mm, _ in items), encoding="utf-8")
    results = run_tool(src, tmp_path / "results.jsonl", "--label-text")
    expected = {i: labels for i, _, labels in items}
    broken = [(i, r.get("error") or r.get("render_error")) for i, r in results.items()
              if not r["ok"] or r.get("render_error")]
    lost = [(i, label) for i, r in results.items() if r["ok"] and not r.get("render_error")
            for label in expected[i] if _squash(label) not in _squash(r.get("label_text", ""))]
    assert len(results) == len(items) == len(expected)
    assert not broken, broken[:10]
    assert not lost, lost[:10]


def _realistic_ir(rng: random.Random) -> DiagramIR:
    words = ["请求", "响应", "缓存命中", "索引", "状态超时", "页表", "偏移地址", "物理内存", "计算结果", "编码器",
             "注意力权重", "残差连接", "gradient", "loss", "update", "batch", "feature vector", "kernel"]
    def label() -> str:
        return "".join(rng.choice(words) for _ in range(rng.randint(1, 3)))
    if rng.random() < 0.25:
        nodes = tuple(Node(f"k{j}", label(), "actor") for j in range(rng.randint(2, 4)))
        msgs = tuple(Link(rng.choice(nodes).key, rng.choice(nodes).key, label()) for _ in range(rng.randint(3, 6)))
        return DiagramIR("draw", form="sequence", question="q", nodes=nodes, messages=msgs)
    form = rng.choice(["flow", "structure"])
    roles = ["step"] * 5 + (["decision", "store", "input", "output", "start"] if form == "flow" else ["store", "actor"])
    n = rng.randint(3, 9)
    nodes = tuple(Node(f"k{j}", label(), rng.choice(roles)) for j in range(n))
    edges = [Link(f"k{rng.randrange(max(0, j - 2), j)}", f"k{j}", label() if rng.random() < 0.5 else "")
             for j in range(1, n)]
    if rng.random() < 0.4:
        a, b = sorted(rng.sample(range(n), 2))
        edges.append(Link(f"k{a}", f"k{b}"))
    return DiagramIR("draw", form=form, question="q", nodes=nodes, edges=tuple(edges))


@pytest.mark.skipif(_tool_ready() is None, reason="tools/mermaid_check dependencies not installed")
def test_width_estimate_tracks_the_rendered_width(tmp_path):
    from secondbrain.diagrams.width import estimate_width
    rng = random.Random(7)
    irs = [_realistic_ir(rng) for _ in range(300)]
    src = tmp_path / "w.jsonl"
    src.write_text("".join(json.dumps({"id": i, "text": emit(ir)}, ensure_ascii=False) + "\n"
                           for i, ir in enumerate(irs)), encoding="utf-8")
    real = {i: r["width"] for i, r in run_tool(src, tmp_path / "w_out.jsonl", timeout=600).items()}
    rel = sorted(abs(estimate_width(ir) - real[i]) / real[i] for i, ir in enumerate(irs))
    assert rel[len(rel) // 2] < 0.10 and rel[int(len(rel) * 0.9)] < 0.25, (rel[len(rel) // 2], rel[int(len(rel) * 0.9)])
    over = [i for i, ir in enumerate(irs) if estimate_width(ir) > 700 and real[i] <= 650]
    assert len(over) <= 3, over  # the estimate rarely rejects a diagram that clearly fits


@pytest.mark.skipif(_tool_ready() is None or os.name != "posix", reason="tools/mermaid_check dependencies not installed")
def test_tool_says_when_the_browser_does_not_start(tmp_path):
    node, modules = _tool_ready()
    fake = tmp_path / "chrome"
    fake.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    fake.chmod(0o755)
    src, out = tmp_path / "in.jsonl", tmp_path / "out.jsonl"
    src.write_text(json.dumps({"id": 1, "text": "flowchart TB\n  a --> b"}) + "\n", encoding="utf-8")
    proc = subprocess.run([node, str(TOOL), "--modules", str(modules), "--browser", str(fake), str(src), str(out)],
                          capture_output=True, text=True, timeout=600)
    assert proc.returncode == 2
    assert "could not start the browser" in proc.stderr and "2 attempts" in proc.stderr, proc.stderr


# The browser retries (V3's surviving check.mjs mutants) -----------------------------

def _real_browser() -> str | None:
    cands = [os.environ.get("MERMAID_CHECK_BROWSER"), "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
             "/Applications/Chromium.app/Contents/MacOS/Chromium", "/usr/bin/google-chrome",
             "/usr/bin/google-chrome-stable", "/usr/bin/chromium", "/usr/bin/chromium-browser"]
    return next((c for c in cands if c and os.access(c, os.X_OK)), None)


_RETRY_READY = _tool_ready() is not None and _real_browser() is not None and os.name == "posix"


def _fake_browser(tmp_path: Path, body: str) -> Path:
    """A browser stand-in: a shell script that counts its starts in tmp_path/starts, then runs body ($REAL is
    the real browser)."""
    fake = tmp_path / "chrome"
    fake.write_text(f'#!/bin/sh\nREAL="{_real_browser()}"\necho x >> "{tmp_path}/starts"\n{body}\n', encoding="utf-8")
    fake.chmod(0o755)
    return fake


def _starts(tmp_path: Path) -> int:
    return len((tmp_path / "starts").read_text().splitlines())


def _blocks(tmp_path: Path, n: int) -> Path:
    src = tmp_path / "in.jsonl"
    src.write_text("".join(json.dumps({"id": i, "text": f"flowchart TB\n  a{i}[甲] --> b{i}[乙]"}) + "\n"
                           for i in range(n)), encoding="utf-8")
    return src


def _kill_after_first_result(tmp_path: Path, fake: Path, n: int, before_kill=lambda: None):
    """Run the tool on n blocks one at a time; when the first result is written, kill the browser."""
    import signal
    import time
    node, modules = _tool_ready()
    out = tmp_path / "out.jsonl"
    proc = subprocess.Popen([node, str(TOOL), "--modules", str(modules), "--browser", str(fake), "--batch", "1",
                             str(_blocks(tmp_path, n)), str(out)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True)
    deadline = time.time() + 300
    while not (out.exists() and out.read_text(encoding="utf-8").count("\n") >= 1):
        assert proc.poll() is None and time.time() < deadline, proc.stderr.read() if proc.poll() is not None else ""
        time.sleep(0.05)
    before_kill()
    os.kill(int((tmp_path / "pid").read_text()), signal.SIGKILL)
    _, err = proc.communicate(timeout=600)
    return proc.returncode, err, out.read_text(encoding="utf-8").splitlines()


@pytest.mark.skipif(not _RETRY_READY, reason="tools/mermaid_check dependencies or a browser not installed")
def test_tool_starts_the_browser_a_second_time(tmp_path):
    node, modules = _tool_ready()
    fake = _fake_browser(tmp_path, '[ "$(wc -l < "%s/starts" | tr -d " ")" -le 1 ] && exit 1\nexec "$REAL" "$@"' % tmp_path)
    out = tmp_path / "out.jsonl"
    proc = subprocess.run([node, str(TOOL), "--modules", str(modules), "--browser", str(fake),
                           str(_blocks(tmp_path, 2)), str(out)], capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stderr
    assert _starts(tmp_path) == 2 and len(out.read_text(encoding="utf-8").splitlines()) == 2


@pytest.mark.skipif(not _RETRY_READY, reason="tools/mermaid_check dependencies or a browser not installed")
def test_tool_waits_for_a_slow_browser(tmp_path):
    """A browser that takes 32 s to start is still used (the launch timeout is 120 s, not Puppeteer's 30 s)."""
    node, modules = _tool_ready()
    fake = _fake_browser(tmp_path, 'sleep 32\nexec "$REAL" "$@"')
    out = tmp_path / "out.jsonl"
    proc = subprocess.run([node, str(TOOL), "--modules", str(modules), "--browser", str(fake),
                           str(_blocks(tmp_path, 1)), str(out)], capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stderr
    assert _starts(tmp_path) == 1


@pytest.mark.skipif(not _RETRY_READY, reason="tools/mermaid_check dependencies or a browser not installed")
def test_tool_replaces_a_browser_that_dies(tmp_path):
    fake = _fake_browser(tmp_path, 'echo $$ > "%s/pid"\nexec "$REAL" "$@"' % tmp_path)
    code, err, lines = _kill_after_first_result(tmp_path, fake, 300)
    assert code == 0, err
    assert len(lines) == 300 and all(json.loads(x)["ok"] for x in lines)
    assert _starts(tmp_path) == 2


@pytest.mark.skipif(not _RETRY_READY, reason="tools/mermaid_check dependencies or a browser not installed")
def test_tool_says_how_far_it_got_when_the_browser_cannot_be_replaced(tmp_path):
    fake = _fake_browser(tmp_path, '[ -e "%s/dead" ] && exit 1\necho $$ > "%s/pid"\nexec "$REAL" "$@"'
                         % (tmp_path, tmp_path))
    code, err, lines = _kill_after_first_result(tmp_path, fake, 300, lambda: (tmp_path / "dead").touch())
    assert code == 2
    m = re.search(r"results written for (\d+) of 300 blocks", err)
    assert m and int(m.group(1)) == len(lines) and 1 <= len(lines) < 300, err
    assert "the browser stopped responding" in err and "could not start the browser" in err, err
