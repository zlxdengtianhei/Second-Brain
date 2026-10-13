"""Batch command line: the layout gate in apply, with and without tools/mermaid_check."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from secondbrain.diagrams.__main__ import main

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "mermaid_check" / "check.mjs"


def _tool() -> tuple[str, Path] | None:
    node = shutil.which("node")
    modules = Path(os.environ.get("MERMAID_CHECK_MODULES") or TOOL.parent)
    if not node or not (modules / "node_modules" / "mermaid").is_dir():
        return None
    return node, modules


def _wide_case(tmp_path: Path) -> tuple[Path, Path]:
    props = ["第一条路径很长很长很长很长很长很长。", "第二条路径很长很长很长很长很长很长。", "第三条路径很长很长很长很长很长很长。"]
    card = {"ref": "wide", "title": "t", "kind": "mechanism", "domain": "d", "essence": "三条路径汇合到结果。",
            "propositions": props, "related_titles": []}
    nodes = [{"key": "s", "label": "三条路径", "p": ["E"]}, {"key": "t", "label": "结果", "p": ["E"]}]
    nodes += [{"key": f"m{i}", "label": props[i][:14], "p": [f"P{i + 1}"]} for i in range(3)]
    edges = [{"from": "s", "to": f"m{i}", "label": "汇合到结果", "p": ["E"]} for i in range(3)]
    edges += [{"from": f"m{i}", "to": "t", "label": "汇合到结果", "p": ["E"]} for i in range(3)]
    ir = {"decision": "draw", "form": "structure", "question": "q", "nodes": nodes, "edges": edges}
    (tmp_path / "cards.jsonl").write_text(json.dumps(card, ensure_ascii=False) + "\n", encoding="utf-8")
    main(["packets", str(tmp_path / "cards.jsonl"), str(tmp_path / "p")])
    answer = tmp_path / "p" / "packet-001.answer.json"
    answer.write_text(json.dumps({"results": [{"ref": "wide", "ir": ir}]}, ensure_ascii=False), encoding="utf-8")
    return tmp_path / "cards.jsonl", answer


def test_apply_without_checker_gates_on_the_width_estimate(tmp_path, capsys):
    cards, answer = _wide_case(tmp_path)
    main(["apply", str(cards), str(answer), "--out", str(tmp_path / "o")])
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1]) == {"rewrite": 1}
    row = json.loads((tmp_path / "o" / "outcomes.jsonl").read_text(encoding="utf-8"))
    assert row["errors"][0].startswith("V14 estimated")


@pytest.mark.skipif(_tool() is None, reason="tools/mermaid_check dependencies not installed")
def test_apply_with_checker_gates_on_the_rendered_width(tmp_path, capsys):
    _, modules = _tool()
    cards, answer = _wide_case(tmp_path)
    main(["apply", str(cards), str(answer), "--out", str(tmp_path / "o"), "--checker", str(TOOL),
          "--checker-modules", str(modules)])
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary == {"rewrite": 1, "layout_gate": {"rendered": 1, "turned_back": 1}}
    row = json.loads((tmp_path / "o" / "outcomes.jsonl").read_text(encoding="utf-8"))
    assert row["errors"][0].startswith("V14 renders")
    assert (tmp_path / "o" / "render_checks.jsonl").exists()
