"""`sb export`: skeleton, program checks, write/review/repair loop, output files.

Only synthetic cards are used here.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from secondbrain import config, jobs, labels, vault
from secondbrain.config import ReviewPolicy
from secondbrain.executors.fake import FakeExecutor
from secondbrain.executors.host import HostExecutor
from secondbrain.export import checks, pipeline, render, skeleton

MOC = "合成图谱"
DOMAIN = "synth"
#: (pillar, why, [(topic, card count)])
PLAN = [("基础", "先认识部件。", [("小主题", 2), ("部件", 5), ("连接", 12)]),
        ("用法", "再看怎样组合。", [("组合", 20)])]


def _write_card(root: Path, n: int, pillar: str, topic: str, rels: list) -> None:
    cid = f"syn_{n:03d}"
    title = f"合成卡 {n:03d}"
    fm = {"id": cid, "title": title, "kind": "concept", "domain": DOMAIN, "pillar": pillar, "topic": topic,
          "relations": rels, "status": "confirmed"}
    body = (f"## 本质\n合成卡 {n} 的量取 {n + 100}，记作 $a_{{{n}}}$。\n\n"
            f"## 命题\n- P1. 第 {n} 个部件有 {n + 7} 个槽。\n\n"
            f"## 相关\n- 相关行不给写作者\n\n## 复习卡\n问题::答案\n")
    path = root / "10_Knowledge" / DOMAIN / f"{title}.md"
    path.write_text("---\n" + yaml.safe_dump(fm, allow_unicode=True, sort_keys=False) + "---\n" + body,
                    encoding="utf-8")


def build(root: Path) -> Path:
    root = root.resolve()
    (root / "10_Knowledge" / DOMAIN).mkdir(parents=True)
    (root / "20_MOCs" / "_pillars").mkdir(parents=True)
    (root / "_data" / "secondbrain").mkdir(parents=True)
    catalog = {"schema": "secondbrain-catalog/1", "tracks": [{"id": "t", "title": "T", "domains": [DOMAIN]}],
               "domains": {DOMAIN: {"moc": MOC, "question": "?"}},
               "relation_types": ["depends_on", "required_by", "contrasts_with"],
               "inverse_relations": {"depends_on": "required_by", "required_by": "depends_on",
                                     "contrasts_with": "contrasts_with"}, "domain_relations": {}}
    (root / "_data" / "secondbrain" / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False),
                                                                 encoding="utf-8")
    taxonomy = {"title": MOC, "narrative": "合成叙事。",
                "pillars": [{"name": p, "order": i + 1, "why": why, "topics": [t for t, _ in topics]}
                            for i, (p, why, topics) in enumerate(PLAN)]}
    (root / "20_MOCs" / "_pillars" / f"{MOC}.yaml").write_text(yaml.safe_dump(taxonomy, allow_unicode=True),
                                                                encoding="utf-8")
    n = 0
    for pillar, _, topics in PLAN:
        for topic, count in topics:
            for k in range(count):
                n += 1
                # inside 部件, card 4 depends on card 5 (5 must come first)
                rels = [{"type": "depends_on", "target": f"syn_{n + 1:03d}"}] if (topic == "部件" and k == 1) else []
                _write_card(root, n, pillar, topic, rels)
    return root


@pytest.fixture
def vault_root(tmp_path, monkeypatch):
    monkeypatch.setenv("SECONDBRAIN_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SECONDBRAIN_NO_CACHE", "1")
    config.activate(config.Settings(card_language="zh"))
    yield build(tmp_path / "vault")
    config.activate(None)
    config.set_review_policy(None)


def _snapshot(root):
    return vault.read_snapshot(root)


# --------------------------------------------------------------------------
# Skeleton
# --------------------------------------------------------------------------
def test_skeleton_covers_every_card_once_in_units_of_at_most_fifteen(vault_root):
    k = skeleton.build(_snapshot(vault_root), domain=DOMAIN)
    ids = [cid for u in k["units"] for p in u["parts"] for cid in p["cards"]]
    assert sorted(ids) == sorted(k["cards"]) and len(ids) == len(set(ids)) == 39
    assert all(1 <= sum(len(p["cards"]) for p in u["parts"]) <= skeleton.MAX_UNIT for u in k["units"])
    assert [s["pillar"] for s in k["sections"]] == ["基础", "用法"]
    # 20 cards of one topic: two units of 10; the heading is printed once
    use = [u for u in k["units"] if u["pillar"] == "用法"]
    assert [len(u["parts"][0]["cards"]) for u in use] == [10, 10]
    assert [u["parts"][0]["heading"] for u in use] == ["组合", None]
    assert [u["first_in_section"] for u in use] == [True, False]


def test_small_topic_opens_the_pillar_without_heading_and_prerequisites_come_first(vault_root):
    k = skeleton.build(_snapshot(vault_root), domain=DOMAIN)
    first = k["units"][0]
    assert first["parts"][0]["topic"] is None and first["parts"][0]["heading"] is None
    assert first["parts"][0]["cards"] == ["syn_001", "syn_002"]
    parts = first["parts"][1]
    assert parts["heading"] == "部件"
    assert parts["cards"].index("syn_005") < parts["cards"].index("syn_004")
    assert {"from": "合成卡 004", "type": "depends_on", "to": "合成卡 005"} in first["relations"]


def test_body_view_drops_links_flashcards_and_diagram():
    body = "## 本质\n甲\n\n## 图\n```mermaid\nA-->B\n```\n\n## 命题\n- P1. 乙\n\n## 相关\n- x\n\n## 复习卡\nq::a\n"
    assert skeleton.body_view(body) == "## 本质\n甲\n\n## 命题\n- P1. 乙\n"


def test_cut_points_prefer_topic_boundaries_and_cap_units():
    assert skeleton._cuts([6, 4, 4, 6, 6]) == [14]          # 26 cards -> 14 + 12, at a topic boundary
    cuts = skeleton._cuts([38])                              # 38 cards of one topic -> three even units
    sizes = [b - a for a, b in zip([0, *cuts], [*cuts, 38])]
    assert len(sizes) == 3 and max(sizes) <= skeleton.MAX_UNIT and min(sizes) >= skeleton.MIN_UNIT


def test_skeleton_rejects_unknown_scope(vault_root):
    with pytest.raises(skeleton.SkeletonError):
        skeleton.build(_snapshot(vault_root), domain="nope")
    with pytest.raises(skeleton.SkeletonError):
        skeleton.build(_snapshot(vault_root), domain=DOMAIN, ids=["syn_999"])


# --------------------------------------------------------------------------
# Program checks
# --------------------------------------------------------------------------
def test_pandoc_inline_rules_and_display_form():
    maths, stray = checks.scan_math("好 $a$ 与 $ b$ 和 $c $ 以及 $d$5 还有 5$ 结尾")
    problems = {m.tex: m.problems for m in maths}
    assert problems["a"] == ()
    assert "inline_math_space_after_open" in problems["b"]
    assert "inline_math_space_before_close" in problems["c"]
    assert "inline_math_digit_after_close" in problems["d"]
    assert len(stray) == 1
    good, _ = checks.scan_math("前文\n$$\nx^2\n$$\n后文")
    bad, _ = checks.scan_math("前文 $$x^2$$ 后文")
    assert good[0].display and good[0].problems == ()
    assert bad[0].problems == ("display_math_not_own_line",)
    table, _ = checks.scan_math("| 列 | $|x|$ |")
    assert "table_math_pipe" in table[0].problems
    escaped, stray = checks.scan_math(r"价格 \$5 与 `$x$` 代码")
    assert escaped == [] and stray == []


def test_numbers_skip_formulas_citations_and_names():
    text = "Model-base 有 12 层，BM25 的 k1 取 1.2，top-5 召回 95%，$x_1$ 共 1,000 条 [[卡 12]]"
    assert checks.numbers(text) == ["12", "1.2", "5", "95", "1000"]


def test_check_paragraph_flags_each_problem(vault_root):
    k = skeleton.build(_snapshot(vault_root), domain=DOMAIN)
    index = checks.CardIndex.from_input(k)
    ok = "量取 103，记作 $a_{3}$，有 10 个槽。[[合成卡 003]]"
    assert checks.check_paragraph("p1.1", ok, index) == []
    bad = "量取 999，记作 $b_{3}$。[[合成卡 003]][[不存在的卡]]"
    codes = sorted(f["code"] for f in checks.check_paragraph("p1.2", bad, index, {("b_{3}", False): "boom"}))
    assert codes == ["formula_not_in_cited_cards", "formula_parse_error", "number_not_in_cited_cards",
                     "unknown_link"]
    assert [f["code"] for f in checks.check_paragraph("p1.3", "没有出处。", index)] == ["no_citation"]


def test_duplicate_pairs_only_across_units():
    same = "这是一段很长的重复文字，讲同一件事情的同一个细节，没有任何不同的地方。"
    rows = [("u01", "u01/p1.1", same), ("u01", "u01/p1.2", same), ("u02", "u02/p1.1", same + "[[甲]]"),
            ("u02", "u02/p1.2", "完全不同的内容在这里出现，讲的是另一回事情。")]
    pairs = checks.duplicate_pairs(rows)
    assert {(p["a"], p["b"]) for p in pairs} == {("u01/p1.1", "u02/p1.1"), ("u01/p1.2", "u02/p1.1")}


# --------------------------------------------------------------------------
# The loop, with fake models
# --------------------------------------------------------------------------
def _cards_of(task):
    return {c["id"]: c for c in task.packet["cards"]}


def writer(skip: set | None = None):
    """Writes one faithful paragraph per card (number and formula copied), skipping ids in ``skip``."""
    def respond(task):
        if task.chunk == pipeline.SMOOTH:
            dups = [d["b"] for d in task.packet["duplicates"]]
            return {"intro": "本文按支柱顺序讲合成图谱。", "drop": dups[:1],
                    "transitions": [{"section": s["pillar"], "text": "接下来看用法。"}
                                    for s in task.packet["sections"][1:]]}
        cards = _cards_of(task)
        parts = []
        for part in task.packet["parts"]:
            paras = []
            for cid in part["cards"]:
                if skip and cid in skip and task.step == "produce":
                    continue
                n = int(cid.split("_")[1])
                paras.append(f"合成卡 {n} 的量取 {n + 100}，记作 $a_{{{n}}}$。[[{cards[cid]['cite']}]]")
            parts.append({"part": part["part"], "paragraphs": paras or ["空段。[[合成卡 001]]"]})
        return {"parts": parts}
    return respond


def reviewer(revise_first: set | None = None):
    seen = set()

    def respond(task):
        items = []
        for p in task.packet["paragraphs"]:
            first = (task.chunk, p["id"]) not in seen
            seen.add((task.chunk, p["id"]))
            bad = revise_first and task.chunk in revise_first and first
            items.append({"id": p["id"], "decision": "revise" if bad else "accept",
                          "reason": "“取 X” 不在卡里" if bad else "与卡一致"})
        return {"items": items}
    return respond


class FakeMathJax:
    name = "fake-mathjax"

    def check(self, items):
        return ["Undefined control sequence" if "\\bad" in tex else None for tex, _ in items]


def run(root, *, mode="parallel", skip=None, revise_first=None, rounds=1, out=None, html=False):
    config.set_review_policy(ReviewPolicy(max_repair_rounds=rounds))
    job = pipeline.prepare(root, domain=DOMAIN, mode=mode)
    producer = FakeExecutor("gpt-writer", responder=writer(skip))
    review = FakeExecutor("claude-reviewer", responder=reviewer(revise_first))
    result = pipeline.advance(root, job["job_id"], producer=producer, reviewer=review, checker=FakeMathJax(),
                              workers=4, out_dir=out, html=html)
    return job["job_id"], result, producer, review


def test_parallel_export_end_to_end_writes_markdown_and_html(vault_root, tmp_path):
    job_id, result, producer, review = run(vault_root, out=tmp_path / "out", html=True)
    report = result["report"]
    assert result["status"] == "complete"
    assert report["coverage"] == 1.0 and report["unused"] == []
    assert report["numbers_not_in_cited_cards"] == 0 and report["pandoc_rule_violations"] == 0
    assert report["formula_parse_rate"] == 1.0 and report["unknown_links"] == 0
    assert set(u["status"] for u in report["units"].values()) == {"accepted"}
    units = len(jobs.read_input(vault_root, job_id)["units"])
    assert report["runs"]["calls"] == {"producer:produce:submitted": units + 1, "reviewer:review:submitted": units}
    md = Path(result["files"]["markdown"]).read_text(encoding="utf-8")
    front, rest = md.split("---\n", 2)[1:]
    meta = yaml.safe_load(front)
    assert meta["cards"] == 39 and meta["unused_cards"] == 0 and meta["writer"] == "gpt-writer"
    assert rest.startswith(labels.export("zh")["hint"] + "\n\n# 合成图谱\n\n本文按支柱顺序讲合成图谱。")
    assert "## 基础" in rest and "### 部件" in rest and "\n## 用法\n\n接下来看用法。\n" in rest
    assert "$a_{12}$" in rest                       # formulas are kept exactly as written
    html = Path(result["files"]["html"]).read_text(encoding="utf-8")
    assert '<span class="arithmatex">\\(a_{12}\\)</span>' in html and "mathjax@3" in html
    assert '<span class="cite">合成卡 012</span>' in html and labels.export("zh")["hint"] not in html


def test_sequential_mode_shows_the_previous_paragraph(vault_root):
    _, result, producer, _ = run(vault_root, mode="sequential")
    writes = [t for t in producer.calls if t.chunk != pipeline.SMOOTH]
    assert [t.chunk for t in writes] == sorted(t.chunk for t in writes)     # one unit after the other
    last_card = writes[0].packet["parts"][-1]["cards"][-1]
    assert writes[0].packet["previous_paragraph"] is None
    assert writes[1].packet["previous_paragraph"].endswith(f"[[合成卡 {last_card[4:]}]]")
    assert writes[1].packet["outline"][0]["written"] is True
    assert result["report"]["coverage"] == 1.0


def test_review_revise_and_missing_card_go_back_once_then_pass(vault_root):
    _, result, producer, review = run(vault_root, skip={"syn_003"}, revise_first={"u02"})
    units = result["report"]["units"]
    assert units["u01"]["repair_rounds"] == 1 and units["u01"]["status"] == "accepted"
    assert units["u01"]["history"][0]["findings"] >= 1          # card_not_cited sent it back
    assert units["u02"]["repair_rounds"] == 1 and units["u02"]["history"][0]["revise"] >= 1
    repairs = [t for t in producer.calls if t.step == "repair"]
    assert {t.chunk for t in repairs} == {"u01", "u02"}
    assert "card_not_cited" in repairs[0].prompt or "card_not_cited" in repairs[1].prompt
    assert all("previous_candidate" in t.packet for t in repairs)


def test_unit_still_failing_after_the_rounds_is_kept_and_reported(vault_root, tmp_path):
    _, result, _, _ = run(vault_root, revise_first={"u01"}, rounds=0, out=tmp_path / "o")
    report = result["report"]
    assert report["escalated_units"] == ["u01"]
    assert report["units"]["u01"]["unresolved"]["review"]
    assert report["coverage"] == 1.0                              # the unit's text is still in the article
    assert yaml.safe_load(Path(result["files"]["markdown"]).read_text().split("---\n")[1])["escalated_units"] == 1


def test_parse_errors_are_findings(vault_root):
    def bad_writer(task):
        out = writer()(task)
        if task.chunk == "u01" and task.step == "produce":
            out["parts"][-1]["paragraphs"].append("记作 $\\bad$。[[合成卡 003]]")
        return out
    config.set_review_policy(ReviewPolicy(max_repair_rounds=0))
    job = pipeline.prepare(vault_root, domain=DOMAIN, mode="parallel")
    result = pipeline.advance(vault_root, job["job_id"], producer=FakeExecutor("gpt-w", responder=bad_writer),
                              reviewer=FakeExecutor("claude-r", responder=reviewer()), checker=FakeMathJax())
    report = result["report"]
    assert report["finding_codes"]["formula_parse_error"] == 1 and report["formula_parse_rate"] < 1.0
    assert report["escalated_units"] == ["u01"]


def test_host_mode_hands_off_all_parallel_units_but_one_sequential_unit(vault_root):
    for mode, expected in (("parallel", 4), ("sequential", 1)):
        job = pipeline.prepare(vault_root, domain=DOMAIN, mode=mode)
        host = HostExecutor(command=["sb"])
        result = pipeline.advance(vault_root, job["job_id"], producer=host, reviewer=host)
        assert result["status"] == "awaiting_submission"
        assert len(result["handoffs"]) == expected
        again = pipeline.advance(vault_root, job["job_id"], producer=host, reviewer=host)
        assert [h["run_id"] for h in again["handoffs"]] == [h["run_id"] for h in result["handoffs"]]
        assert Path(result["handoffs"][0]["packet_path"]).read_text(encoding="utf-8").count("<task_packet>") == 1


def test_submission_validator_rejects_bad_shapes(vault_root):
    job = pipeline.prepare(vault_root, domain=DOMAIN, mode="parallel")
    packet = jobs.read_input(vault_root, job["job_id"])
    kwargs = dict(candidate=None, root=vault_root, chunk="u01", state={})
    with pytest.raises(ValueError, match="parts must be exactly"):
        pipeline.validate_submission(packet, {"parts": [{"part": "p2", "paragraphs": ["x"]}]},
                                     role="producer", **kwargs)
    cand = {"parts": [{"part": "p1", "paragraphs": ["a", "b"]}]}
    with pytest.raises(ValueError, match="one review item per paragraph"):
        pipeline.validate_submission(packet, {"items": [{"id": "p1.1", "decision": "accept", "reason": "ok"}]},
                                     role="reviewer", **{**kwargs, "candidate": cand})
    with pytest.raises(ValueError, match="drop"):
        pipeline.validate_submission(packet, {"intro": "x", "transitions": [], "drop": ["u01/p1.1"]},
                                     role="producer", **{**kwargs, "chunk": pipeline.SMOOTH})


def test_same_family_writer_and_reviewer_are_refused_in_api_attestation(vault_root):
    config.set_review_policy(ReviewPolicy(max_repair_rounds=0))
    job = pipeline.prepare(vault_root, domain=DOMAIN, mode="parallel")
    with pytest.raises(ValueError, match="different model families"):
        pipeline.advance(vault_root, job["job_id"], producer=FakeExecutor("glm-a", responder=writer()),
                         reviewer=FakeExecutor("glm-b", responder=reviewer()))


def test_cli_prepare_only(vault_root, tmp_path, capsys):
    from secondbrain.cli import main
    cfg = tmp_path / "c.toml"
    cfg.write_text('[cards]\nlanguage = "zh"\n', encoding="utf-8")
    assert main(["--vault", str(vault_root), "--config", str(cfg), "export", "--domain", DOMAIN,
                 "--mode", "parallel", "--prepare-only"]) == 0
    state = json.loads(capsys.readouterr().out)
    assert state["kind"] == "export" and state["status"] == "prepared"
    assert re.fullmatch(r"export-[0-9a-f]{16}", state["job_id"])


# --------------------------------------------------------------------------
# Defaults (decision D-11): sequential writing; the writer and its reviewer are the owner's choice
# --------------------------------------------------------------------------
WRITER = ('[export.writer]\nkind = "openai"\nbase_url = "https://api.example.com/v1"\nmodel = "glm-writer"\n'
          'api_key_env = "SB_TEST_WRITER"\n')
ENDPOINTS = ('[executor.producer]\nkind = "openai"\nbase_url = "https://api.example.com/v1"\nmodel = "gpt-a"\n'
             'api_key_env = "SB_TEST_P"\n[executor.reviewer]\nkind = "anthropic"\n'
             'base_url = "https://api.example.com"\nmodel = "claude-b"\napi_key_env = "SB_TEST_R"\n')


def _prepared_mode(vault_root, tmp_path, capsys, toml, *extra):
    from secondbrain.cli import main
    cfg = tmp_path / "mode.toml"
    cfg.write_text('[cards]\nlanguage = "zh"\n' + toml, encoding="utf-8")
    assert main(["--vault", str(vault_root), "--config", str(cfg), "export", "--domain", DOMAIN,
                 "--prepare-only", *extra]) == 0
    state = json.loads(capsys.readouterr().out)
    return jobs.read_input(vault_root, state["job_id"])["mode"]


def test_an_export_is_written_unit_after_unit_unless_the_config_or_the_command_says_otherwise(vault_root, tmp_path,
                                                                                              capsys):
    assert config.ExportConfig().mode == "sequential"
    assert _prepared_mode(vault_root, tmp_path, capsys, "") == "sequential"
    assert _prepared_mode(vault_root, tmp_path, capsys, '[export]\nmode = "parallel"\n') == "parallel"
    assert _prepared_mode(vault_root, tmp_path, capsys, '[export]\nmode = "parallel"\n',
                          "--mode", "sequential") == "sequential"


def test_the_export_writer_and_reviewer_come_from_export_else_from_the_executor(tmp_path):
    from types import SimpleNamespace
    from secondbrain.cli import export_executors
    cfg = tmp_path / "w.toml"
    cfg.write_text(ENDPOINTS + WRITER, encoding="utf-8")
    config.activate(config.load(cfg))
    writer, reviewer = export_executors(SimpleNamespace(executor="api"), tmp_path)
    assert writer.endpoint.model == "glm-writer" and reviewer.endpoint.model == "claude-b"
    cfg.write_text(ENDPOINTS, encoding="utf-8")
    config.activate(config.load(cfg))
    writer, reviewer = export_executors(SimpleNamespace(executor="api"), tmp_path)
    assert writer.endpoint.model == "gpt-a"


@pytest.mark.parametrize("toml, problem", [
    ('[export]\nmode = "both"\n', "export.mode must be one of sequential, parallel"),
    ('[export]\nwriter_model = "x"\n', "Unknown \\[export\\] keys"),
    ('[export.writer]\nkind = "openai"\nmodel = "m"\napi_key = "secret"\n', "must not contain api_key"),
])
def test_the_export_section_is_checked(tmp_path, toml, problem):
    cfg = tmp_path / "bad.toml"
    cfg.write_text(toml, encoding="utf-8")
    with pytest.raises(ValueError, match=problem):
        config.load(cfg)


def test_the_writer_reads_cards_through_their_view(vault_root):
    card = next(iter(_snapshot(vault_root).cards.values()))
    entry = skeleton._card_entry(card)
    from secondbrain.cards import view
    assert entry["body"] == skeleton.body_view(view(card, "body")["body"]) and "复习卡" not in entry["body"]
