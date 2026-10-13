"""The diagram prompt and the batch entry points, end to end on the fixture cards.

Card -> packet (prompt + card payloads) -> model answer (here: the fixture
IRs) -> validate -> Mermaid with marker -> parse in Obsidian's Mermaid build
(when tools/mermaid_check is installed).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from secondbrain.diagrams.__main__ import main
from secondbrain.diagrams.emit import emit
from secondbrain.diagrams.ir import Card
from secondbrain.diagrams.pipeline import load_prompt
from secondbrain.diagrams.staleness import marker
from secondbrain.diagrams.validate import LOOP_WORDS, Limits, validate

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures" / "diagrams"
TOOL = ROOT / "tools" / "mermaid_check" / "check.mjs"
PROMPT = load_prompt()


def _prompt_examples() -> list[tuple[Card, dict]]:
    """Each 'Card `ref`: E ... P1 ... P2 ...' line paired with the JSON block after it."""
    out = []
    for m in re.finditer(r"^Card `([^`]+)`( \((\w+)\))?(?:, `old_diagram: (\w+)`)?: (.+?)\n\n```json\n(.+?)\n```",
                         PROMPT, re.M | re.S):
        ref, kind, text, block = m.group(1), m.group(3) or "mechanism", m.group(5), m.group(6)
        parts = re.split(r"(?:^|(?<=\W))(E|P\d+) ", text)
        fields = dict(zip(parts[1::2], (p.strip() for p in parts[2::2])))
        props = [fields[k] for k in sorted((k for k in fields if k != "E"), key=lambda k: int(k[1:]))]
        card = Card(title=ref, essence=fields.get("E", ""), propositions=tuple(props), kind=kind, ref=ref)
        out.append((card, json.loads(block)))
    return out


# Token budget. The method write-up asked for about 1,000-1,500 tokens with one or
# two examples. The pilots grew the prompt to 2,233 and then 2,196 o200k tokens
# (4 draw and 2 skip examples). It was then cut back with every rule kept to
# 1,497 o200k and 2 flow examples; the cheapest structure example went back in
# because missed structure diagrams were pilot 1's main recall loss (1,677).
# The fourth round (V3's K1-K8 and the third pilot's questions) states every
# rule the validator enforces as it enforces it, and adds one skip example; the
# cap rose to 2,000 (2,000 o200k, 2,079 cl100k when written). New rules have to
# replace old text.
TOKEN_CAP = 2000
BYTE_CAP = 7200  # dependency-free proxy for the same budget (7,118 bytes when written)


def test_prompt_states_the_core_rules():
    for phrase in ("in its language", "Plain text, no TeX", "Start from E", "cite E", "2+ stated outcomes",
                   "draw the back edge", "Parallel options are not a decision", "fans out to at most 3",
                   "to nodes whose cited text mentions it", "a final `store`", "at most 700 px",
                   "a one-character label counts that character", "`old_diagram`", "unordered checks are a list",
                   "an \"otherwise\" exit counts only if the card states", '{"from","to","text","p"}',
                   # fourth round: V3's K3-K5 and the third pilot's questions
                   "citing E and 2+ propositions (all, if fewer)", "a decision whose 3+ exits all end at once",
                   "3+ named states and 3+ labelled transitions", "a chain of 3+ links citing 3+ different sources",
                   "structure 3+ links unless paths merge", "no edge labels overlapping", "state diagrams scale to fit",
                   "On skip, code handles the old diagram", "an overlapping pair of CJK characters",
                   "even when the text lists them in order", "credits it to each path", "would redraw its structure or one formula",
                   "steps whose order the card does not state", "at any length", "accumulator",
                   "Over the whole diagram", "paths that split and never meet are a list",
                   "any `output` has incoming edges only"):
        assert phrase in PROMPT, phrase


def test_prompt_lists_the_validators_words():
    """One word list for the prompt and the validator: loop words (V5) and exempt labels (V4)."""
    loop = re.search(r"says the work repeats \(([^)]*)\)", PROMPT).group(1).split()
    assert loop == list(LOOP_WORDS)
    exempt = re.search(r"Exempt, still cited: ([^.]*)\.", PROMPT).group(1).split()
    assert sorted(exempt) == sorted(Limits().exempt_labels)


def test_prompt_size_budget():
    assert len(PROMPT.encode("utf-8")) <= BYTE_CAP


def test_prompt_token_budget():
    tiktoken = pytest.importorskip("tiktoken")
    try:
        enc = tiktoken.get_encoding("o200k_base")
    except Exception as e:  # the encoding file is downloaded on first use
        pytest.skip(f"o200k_base not available: {e}")
    assert len(enc.encode(PROMPT)) <= TOKEN_CAP


def test_prompt_examples_pass_the_validator():
    examples = _prompt_examples()
    assert [(c.ref, ir["decision"], ir.get("form")) for c, ir in examples] == [
        ("nn/residual", "draw", "flow"), ("algo/rpn", "draw", "flow"), ("econ/inflation", "draw", "structure"),
        ("rag/router", "skip", None)]
    for card, ir in examples:
        report = validate(ir, card)
        assert report.ok, (card.ref, [str(e) for e in report.errors])
        assert not report.warnings, (card.ref, [str(w) for w in report.warnings])


def _tool() -> tuple[str, Path] | None:
    node = shutil.which("node")
    modules = Path(os.environ.get("MERMAID_CHECK_MODULES") or TOOL.parent)
    if not node or not (modules / "node_modules" / "mermaid").is_dir():
        return None
    return node, modules


def _example_mermaid() -> list[dict]:
    out = []
    for card, ir in _prompt_examples():
        parsed = validate(ir, card).ir
        if parsed.decision == "draw":
            out.append({"id": card.ref, "text": emit(parsed, marker(card, parsed))})
    return out


def test_skip_example_is_what_the_validator_rejects_as_a_draw():
    """The router card's skip: drawn, its 3 exits that all end at once are V13 (a list), not a decision."""
    card = next(c for c, ir in _prompt_examples() if c.ref == "rag/router")
    drawn = {"decision": "draw", "form": "flow", "question": "选哪种检索？",
             "nodes": [{"key": "d", "label": "问题类型？", "role": "decision", "p": ["E"]},
                       {"key": "a", "label": "关键词检索", "p": ["P1"]}, {"key": "b", "label": "向量检索", "p": ["P1"]},
                       {"key": "c", "label": "图检索", "p": ["P1"]}],
             "edges": [{"from": "d", "to": "a", "label": "事实问题", "p": ["P1"]},
                       {"from": "d", "to": "b", "label": "语义问题", "p": ["P1"]},
                       {"from": "d", "to": "c", "label": "多跳问题", "p": ["P1"]}]}
    assert [e.code for e in validate(drawn, card).errors] == ["V13"]


def test_prompt_examples_emit_with_marker():
    for block in _example_mermaid():
        assert block["text"].startswith("%% diagram v1 props=E,"), block["id"]
        assert block["text"].splitlines()[1] == "flowchart TB", block["id"]


@pytest.mark.skipif(_tool() is None, reason="tools/mermaid_check dependencies not installed")
def test_prompt_examples_render_in_obsidian_mermaid(tmp_path):
    blocks = _example_mermaid()
    src, res = tmp_path / "examples.jsonl", tmp_path / "checks.jsonl"
    src.write_text("".join(json.dumps(b, ensure_ascii=False) + "\n" for b in blocks), encoding="utf-8")
    node, modules = _tool()
    proc = subprocess.run([node, str(TOOL), "--modules", str(modules), str(src), str(res)],
                          capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stderr
    for c in (json.loads(x) for x in res.read_text(encoding="utf-8").splitlines()):
        assert c["ok"] and c["width"] <= 700 and not c.get("label_overlaps"), c


def _run_batch(tmp_path: Path, capsys) -> tuple[list[str], Path]:
    cards = json.loads((FIX / "cards.json").read_text(encoding="utf-8"))
    irs = {x["ref"]: x for x in json.loads((FIX / "irs.json").read_text(encoding="utf-8"))}
    (tmp_path / "cards.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cards),
                                          encoding="utf-8")
    out = tmp_path / "run"
    assert main(["packets", str(tmp_path / "cards.jsonl"), str(out)]) == 0
    assert json.loads(capsys.readouterr().out) == {"cards": 4, "packets": 1, "pregated": 1}
    packet = json.loads((out / "packet-001.json").read_text(encoding="utf-8"))
    refs = [c["ref"] for c in packet["input"]["cards"]]
    assert len(refs) == 3 and packet["prompt"] == PROMPT
    answer = {"results": [{"ref": r, "ir": irs[r]["ir"]} for r in refs]}
    (out / "packet-001.answer.json").write_text(json.dumps(answer, ensure_ascii=False), encoding="utf-8")
    assert main(["apply", str(tmp_path / "cards.jsonl"), str(out / "packet-001.answer.json"),
                 "--out", str(out / "round0")]) == 0
    assert json.loads(capsys.readouterr().out) == {"draw": 3}
    return refs, out / "round0" / "mermaid.jsonl"


def test_batch_entry_points_on_fixture_cards(tmp_path, capsys):
    refs, mermaid = _run_batch(tmp_path, capsys)
    pregated = (tmp_path / "run" / "pregated.jsonl").read_text(encoding="utf-8")
    assert json.loads(pregated)["ref"] == "operating-systems/process-vs-thread"
    blocks = [json.loads(x) for x in mermaid.read_text(encoding="utf-8").splitlines()]
    assert [b["id"] for b in blocks] == refs
    assert all(b["text"].startswith("%% diagram v1 props=") for b in blocks)


@pytest.mark.skipif(_tool() is None, reason="tools/mermaid_check dependencies not installed")
def test_batch_output_parses_in_obsidian_mermaid(tmp_path, capsys):
    _, mermaid = _run_batch(tmp_path, capsys)
    node, modules = _tool()
    res = tmp_path / "checks.jsonl"
    proc = subprocess.run([node, str(TOOL), "--modules", str(modules), str(mermaid), str(res)],
                          capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stderr
    checks = [json.loads(x) for x in res.read_text(encoding="utf-8").splitlines()]
    assert [c["ok"] for c in checks] == [True, True, True]
    assert [c["type"] for c in checks] == ["sequence", "flowchart-v2", "flowchart-v2"]


def test_rewrite_round_through_the_cli(tmp_path, capsys):
    cards = json.loads((FIX / "cards.json").read_text(encoding="utf-8"))
    search = next(c for c in cards if c["ref"] == "algorithms/binary-search")
    ir = next(x for x in json.loads((FIX / "irs.json").read_text(encoding="utf-8"))
              if x["ref"] == search["ref"])["ir"]
    (tmp_path / "cards.jsonl").write_text(json.dumps(search, ensure_ascii=False) + "\n", encoding="utf-8")
    out = tmp_path / "run"
    main(["packets", str(tmp_path / "cards.jsonl"), str(out)])
    bad = json.loads(json.dumps(ir))
    bad["nodes"][2]["label"] = "左指针右移"
    (out / "packet-001.answer.json").write_text(json.dumps({"results": [{"ref": search["ref"], "ir": bad}]},
                                                           ensure_ascii=False), encoding="utf-8")
    main(["apply", str(tmp_path / "cards.jsonl"), str(out / "packet-001.answer.json"), "--out", str(out / "r0")])
    capsys.readouterr()
    rewrite = json.loads((out / "r0" / "rewrite-001.json").read_text(encoding="utf-8"))
    assert rewrite["attempt"] == 1 and rewrite["input"]["cards"][0]["errors"][0].startswith("V4")
    shutil.copy(out / "r0" / "rewrite-001.json", out / "r1.json")
    (out / "r1.answer.json").write_text(json.dumps({"results": [{"ref": search["ref"], "ir": bad}]},
                                                   ensure_ascii=False), encoding="utf-8")
    main(["apply", str(tmp_path / "cards.jsonl"), str(out / "r1.answer.json"), "--out", str(out / "r1")])
    assert json.loads(capsys.readouterr().out) == {"skip": 1}
    final = json.loads((out / "r1" / "outcomes.jsonl").read_text(encoding="utf-8"))
    assert final["attempt"] == 1 and final["reason"].startswith("no valid IR after 2 attempt(s)")

