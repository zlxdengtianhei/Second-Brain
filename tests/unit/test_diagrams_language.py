"""The vault tools read and write cards in either card language (en, zh)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from secondbrain import cards, cli, config
from secondbrain.diagrams import audit, rejudge
from secondbrain.diagrams.__main__ import main

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "diagrams"
ZH, EN = FIX / "vault", FIX / "vault_en"
TCP = "10_Knowledge/networking/TCP three-way handshake.md"
BUBBLE = "10_Knowledge/algorithms/Bubble sort.md"
DRINKS = "10_Knowledge/beverages/How tea and coffee keep you awake.md"
NEW = "%% diagram v1 props=E,P1,P2 sha=0123abcd\nflowchart TB\n  n1[\"client\"] -->|\"SYN\"| n2[\"server\"]"


def _config(tmp_path: Path, monkeypatch, language: str) -> Path:
    path = tmp_path / "sb.toml"
    path.write_text(f'[cards]\nlanguage = "{language}"\n', encoding="utf-8")
    monkeypatch.setenv("SECONDBRAIN_CONFIG", str(path))
    config.activate(None)
    return path


def test_card_format_uses_the_card_headings():
    assert audit.CardFormat.for_language("zh") == audit.DEFAULT_FORMAT
    en = audit.CardFormat.for_language("en", "Notes")
    h = cards.HEADINGS["en"]
    assert (en.knowledge_dir, en.essence, en.propositions, en.diagram, en.related) == (
        "Notes", h["essence"], h["propositions"], h["diagram"], h["related"])


def test_language_from_flag_then_config_then_headings(tmp_path, monkeypatch):
    assert audit.card_format(ZH)[1:] == ("zh", "card headings")
    assert audit.card_format(EN)[1:] == ("en", "card headings")
    (tmp_path / "empty" / "10_Knowledge").mkdir(parents=True)
    assert audit.card_format(tmp_path / "empty")[1:] == ("en", "default")
    cfg = _config(tmp_path, monkeypatch, "en")
    assert audit.card_format(EN)[1:] == ("en", f"[cards] language in {cfg}")
    with pytest.raises(ValueError, match="Pass --language zh"):
        audit.card_format(ZH)
    assert audit.card_format(ZH, "zh")[1:] == ("zh", "--language")
    with pytest.raises(ValueError, match="none has '## Essence'"):
        audit.card_format(ZH, "en")
    # only the cards a command touches are looked at
    assert audit.card_format(ZH, refs=[])[1:] == ("en", f"[cards] language in {cfg}")


def test_extract_reads_english_cards(tmp_path, capsys):
    out = tmp_path / "audit"
    assert main(["audit-extract", str(EN), str(out)]) == 0
    assert "card language en (from card headings)" in capsys.readouterr().err
    got = {c["ref"]: c for c in audit.read_jsonl(out / "cards.jsonl")}
    tcp = got[TCP]
    assert tcp["essence"] == "A client and a server exchange three segments before the connection is open."
    assert tcp["propositions"][2] == "After the client sends ACK, both sides enter ESTABLISHED."
    assert tcp["related_titles"] == ["TCP connection teardown", "Reliable transport"]
    assert tcp["has_diagram_section"] and got[BUBBLE]["has_diagram_section"]
    assert not got[DRINKS]["has_diagram_section"] and len(got[DRINKS]["propositions"]) == 2
    assert [b["id"] for b in audit.read_jsonl(out / "blocks.jsonl")] == [f"{BUBBLE}#0", f"{TCP}#0"]


def test_sb_diagrams_reads_english_cards(tmp_path):
    """The reported case: `sb diagrams audit-extract` on an English vault."""
    out = tmp_path / "audit"
    assert cli.main(["diagrams", "audit-extract", str(EN), str(out)]) == 0
    rows = list(audit.read_jsonl(out / "cards.jsonl"))
    assert len(rows) == 3 and all(r["essence"] and r["propositions"] for r in rows)


def test_wrong_language_stops_before_writing(tmp_path, capsys):
    with pytest.raises(SystemExit, match="Pass --language en"):
        main(["audit-extract", str(EN), str(tmp_path / "audit"), "--language", "zh"])
    assert not (tmp_path / "audit").exists()


def _tier(out: Path, tcp_check: dict, bubble_check: dict) -> None:
    rows = [{"id": f"{TCP}#0", **tcp_check}, {"id": f"{BUBBLE}#0", **bubble_check}]
    (out / "checks.jsonl").write_text("".join(json.dumps(c) + "\n" for c in rows), encoding="utf-8")
    assert main(["audit-tier", str(out)]) == 0


_HANDSHAKE = {"ok": True, "type": "flowchart-v2", "direction": "LR",
              "nodes": [{"id": "c", "label": "client", "shape": "square"},
                        {"id": "s", "label": "server", "shape": "diamond"}],
              "edges": [{"from": "c", "to": "s", "label": "SYN"}, {"from": "s", "to": "c", "label": "SYN+ACK"},
                        {"from": "c", "to": "s", "label": "ACK"}]}


def test_prune_on_an_english_vault(tmp_path, capsys):
    v, out = tmp_path / "v", tmp_path / "audit"
    shutil.copytree(EN, v)
    main(["audit-extract", str(v), str(out)])
    _tier(out, {**_HANDSHAKE, "width": 820},
          {"ok": True, "type": "flowchart-v2", "direction": "TB", "width": 200,
           "nodes": [{"id": "A", "label": "compare neighbours", "shape": "square"},
                     {"id": "B", "label": "swap if out of order", "shape": "square"}],
           "edges": [{"from": "A", "to": "B", "label": ""}]})
    capsys.readouterr()
    assert main(["prune", str(v), str(out), "--write"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["actions"] == {"delete_section": 1, "direction_tb": 1} and summary["status"] == {"written": 2}
    bubble = (v / BUBBLE).read_text(encoding="utf-8")
    assert "## Diagram" not in bubble and "## Code" in bubble and "## not a heading inside a code block" in bubble
    assert "flowchart TB" in (v / TCP).read_text(encoding="utf-8")
    assert (v / DRINKS).read_bytes() == (EN / DRINKS).read_bytes()


def test_pool_and_writeback_on_an_english_vault(tmp_path, capsys):
    v, out = tmp_path / "v", tmp_path / "audit"
    shutil.copytree(EN, v)
    main(["audit-extract", str(v), str(out)])
    _tier(out, {**_HANDSHAKE, "width": 500, "nodes": [{**n, "shape": "square"} for n in _HANDSHAKE["nodes"]]},
          {"ok": False, "error": "Parse error"})
    capsys.readouterr()
    assert main(["pool", str(out)]) == 0
    assert json.loads(capsys.readouterr().out) == {"review": 1, "redraw": 1}
    pool = [json.loads(x) for x in (out / "pool.jsonl").read_text(encoding="utf-8").splitlines()]
    assert pool[1]["essence"].startswith("A client and a server") and len(pool[1]["propositions"]) == 3
    outcomes = tmp_path / "outcomes.jsonl"
    outcomes.write_text(json.dumps({"ref": TCP, "status": "draw", "mermaid": NEW}) + "\n" +
                        json.dumps({"ref": BUBBLE, "status": "skip", "reason": "linear"}) + "\n", encoding="utf-8")
    assert main(["writeback", str(v), str(out), str(outcomes), "--write"]) == 0
    written = json.loads(capsys.readouterr().out)
    assert (written["actions"], written["status"]) == ({"remove": 1, "replace": 1}, {"written": 2})
    tcp = (v / TCP).read_text(encoding="utf-8")
    original = (EN / TCP).read_text(encoding="utf-8")
    head, tail = original.split("## Diagram\n", 1)
    assert tcp == head + "## Diagram\n```mermaid\n" + NEW + "\n```\n" + tail[tail.index("\n## "):]
    assert "## Diagram" not in (v / BUBBLE).read_text(encoding="utf-8")


def test_a_new_english_diagram_section_goes_after_the_propositions():
    fmt = audit.CardFormat.for_language("en")
    drinks = (EN / DRINKS).read_text(encoding="utf-8")
    assert rejudge.replace_section(drinks, "flowchart TB", fmt) == drinks.replace(
        "\n## Related", "\n## Diagram\n```mermaid\nflowchart TB\n```\n\n## Related")


def test_zh_output_does_not_depend_on_how_the_language_was_chosen(tmp_path, monkeypatch):
    """The Chinese vault reads the same with no setting, with --language zh, and with the library default."""
    runs = {}
    for name, extra in (("headings", []), ("flag", ["--language", "zh"])):
        assert main(["audit-extract", str(ZH), str(tmp_path / name), *extra]) == 0
        runs[name] = [(tmp_path / name / f).read_bytes() for f in ("cards.jsonl", "blocks.jsonl")]
    audit.extract(ZH, tmp_path / "library")
    runs["library"] = [(tmp_path / "library" / f).read_bytes() for f in ("cards.jsonl", "blocks.jsonl")]
    _config(tmp_path, monkeypatch, "zh")
    assert main(["audit-extract", str(ZH), str(tmp_path / "config")]) == 0
    runs["config"] = [(tmp_path / "config" / f).read_bytes() for f in ("cards.jsonl", "blocks.jsonl")]
    assert runs["headings"] == runs["flag"] == runs["library"] == runs["config"]


def test_knowledge_folder_from_flag_then_layout_then_default(tmp_path, monkeypatch, capsys):
    v = tmp_path / "v"
    shutil.copytree(EN / "10_Knowledge", v / "Notes")
    shutil.copytree(ZH / "10_Knowledge", v / "10_Knowledge")

    def extract(name: str, *extra: str) -> list[dict]:
        assert main(["audit-extract", str(v), str(tmp_path / name), *extra]) == 0
        return list(audit.read_jsonl(tmp_path / name / "cards.jsonl"))

    assert audit.knowledge_dir() == ("10_Knowledge", "default")
    rows = extract("none")
    assert "knowledge folder 10_Knowledge (from default)" in capsys.readouterr().err
    assert len(rows) == 3 and all(r["ref"].startswith("10_Knowledge/") and r["essence"] for r in rows)

    cfg = tmp_path / "sb.toml"
    cfg.write_text('[layout]\nknowledge = "Notes"\n\n[cards]\nlanguage = "en"\n', encoding="utf-8")
    monkeypatch.setenv("SECONDBRAIN_CONFIG", str(cfg))
    config.activate(None)
    assert audit.knowledge_dir() == ("Notes", f"[layout] knowledge in {cfg}")
    rows = extract("config")
    assert f"knowledge folder Notes (from [layout] knowledge in {cfg})" in capsys.readouterr().err
    assert len(rows) == 3 and all(r["ref"].startswith("Notes/") and r["essence"] for r in rows)

    rows = extract("flags", "--knowledge-dir", "10_Knowledge", "--language", "zh")
    assert "knowledge folder 10_Knowledge (from --knowledge-dir)" in capsys.readouterr().err
    assert [r["essence"] for r in rows] == [r["essence"] for r in audit.read_jsonl(tmp_path / "none" / "cards.jsonl")]
