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
from secondbrain.diagrams.ir import Card
from secondbrain.diagrams.pipeline import load_prompt
from secondbrain.diagrams.validate import validate

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures" / "diagrams"
TOOL = ROOT / "tools" / "mermaid_check" / "check.mjs"
PROMPT = load_prompt()


def _prompt_examples() -> list[tuple[Card, dict]]:
    """Each 'Card `ref`: E ... P1 ... P2 ...' line paired with the JSON block after it."""
    out = []
    for m in re.finditer(r"^Card `([^`]+)`( \((\w+)\))?: (.+?)\n\n```json\n(.+?)\n```", PROMPT, re.M | re.S):
        ref, kind, text, block = m.group(1), m.group(3) or "mechanism", m.group(4), m.group(5)
        parts = re.split(r"(?:^|(?<=\W))(E|P\d+) ", text)
        fields = dict(zip(parts[1::2], (p.strip() for p in parts[2::2])))
        props = [fields[k] for k in sorted((k for k in fields if k != "E"), key=lambda k: int(k[1:]))]
        card = Card(title=ref, essence=fields.get("E", ""), propositions=tuple(props), kind=kind, ref=ref)
        out.append((card, json.loads(block)))
    return out


def test_prompt_states_the_language_rule_and_stays_short():
    assert "in the card's language" in PROMPT and "Do not write Mermaid" in PROMPT
    cjk = len(re.findall(r"[一-鿿]", PROMPT))
    words = len(re.findall(r"[A-Za-z]+", PROMPT))
    assert cjk + 1.3 * words < 2400  # rough token estimate; the method budget is about 1,000-1,500 for rules


def test_prompt_examples_pass_the_validator():
    examples = _prompt_examples()
    assert [(c.ref, ir["decision"]) for c, ir in examples] == [
        ("net/retry", "draw"), ("net/dns", "draw"), ("net/tcp-vs-udp", "skip")]
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
