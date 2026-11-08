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
        return ["Undefined control sequence" if "\\bad" in tex else
                "Missing close brace" if tex.count("{") != tex.count("}") else None for tex, _ in items]


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
    # every unit is reviewed, and so are the introduction and the transitions the smoothing step wrote
    assert report["runs"]["calls"] == {"producer:produce:submitted": units + 1,
                                       "reviewer:review:submitted": units + 1}
    last = review.calls[-1]
    assert last.chunk == pipeline.SMOOTH and [p["id"] for p in last.packet["paragraphs"]] == ["intro/1",
                                                                                              "transition/用法"]
    assert last.packet["narrative"] == "合成叙事。" and report["smooth"]["status"] == "accepted"
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


def test_parallel_model_calls_overlap_and_only_the_controller_thread_touches_the_job_state(vault_root, monkeypatch):
    """Several threads read and wrote one state.json at once ("refusing a non-regular or hard-linked file", lost
    updates); now only the model calls run on worker threads."""
    import contextlib
    import threading
    from secondbrain import store
    controller, elsewhere = threading.current_thread(), []
    real = store._state_parent

    @contextlib.contextmanager
    def watched(root, rel, *, create):
        if threading.current_thread() is not controller:
            elsewhere.append(rel)
        with real(root, rel, create=create) as opened:
            yield opened
    monkeypatch.setattr(store, "_state_parent", watched)
    together, base = threading.Barrier(2, timeout=20), writer()

    def respond(task):
        if task.step == "produce" and task.chunk in {"u01", "u02"}:
            together.wait()  # raises unless the first two units are written at the same time
        return base(task)
    config.set_review_policy(ReviewPolicy(max_repair_rounds=1))
    job = pipeline.prepare(vault_root, domain=DOMAIN, mode="parallel")
    result = pipeline.advance(vault_root, job["job_id"], producer=FakeExecutor("gpt-writer", responder=respond),
                              reviewer=FakeExecutor("claude-reviewer", responder=reviewer()), checker=FakeMathJax(),
                              workers=4)
    assert result["status"] == "complete" and not together.broken
    assert elsewhere == []


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


def test_a_paragraph_still_failing_after_the_rounds_is_marked_in_the_article_and_listed_for_the_owner(
        vault_root, tmp_path):
    from secondbrain.service import Service
    job_id, result, _, _ = run(vault_root, revise_first={"u01"}, rounds=0, out=tmp_path / "o")
    report = result["report"]
    assert report["escalated_units"] == ["u01"]
    failing = [x["id"] for x in report["units"]["u01"]["unresolved"]["review"]]
    assert failing and [f["paragraph"] for f in report["flagged"]] == [f"u01/{pid}" for pid in failing]
    assert report["coverage"] == 1.0                              # the unit's text is still in the article
    md = Path(result["files"]["markdown"]).read_text(encoding="utf-8")
    meta = yaml.safe_load(md.split("---\n")[1])
    assert meta["escalated_units"] == 1 and meta["flagged_paragraphs"] == len(failing)
    mark = labels.export("zh")["flag"].format(reason="“取 X” 不在卡里")
    assert md.count(mark) == len(failing)
    first = md.split(mark + "\n\n", 1)[1].split("\n", 1)[0]    # the line under the first mark: a u01 paragraph
    assert first.startswith("合成卡 ") and first.endswith("]]")
    unflagged = md.split("## 用法", 1)[1]
    assert mark not in unflagged                                  # accepted paragraphs carry no mark
    items = [i for i in Service(vault_root).owner_items() if i.get("kind") == "export_paragraph"]
    assert [(i["owner_reason"], i["paragraph"]) for i in items] == [("pending", f"u01/{pid}") for pid in failing]
    assert items[0]["article"] == MOC and items[0]["job_id"] == job_id and items[0]["reason"] == "“取 X” 不在卡里"
    assert items[0]["file"] == result["files"]["markdown"]
    assert Service(vault_root).status()["owner_items"]["pending"] == len(failing)


def smooth_reviewer(revise: set, times: int):
    """The unit reviewer; on the smoothing step it sends back the paragraphs in ``revise`` ``times`` times."""
    base, asked = reviewer(), []

    def respond(task):
        if task.chunk != pipeline.SMOOTH:
            return base(task)
        asked.append(task)
        bad = len(asked) <= times
        return {"items": [{"id": p["id"], "decision": "revise" if bad and p["id"] in revise else "accept",
                           "reason": "“十个例子” 不在给出的段落里" if bad and p["id"] in revise else "与叙事一致"}
                          for p in task.packet["paragraphs"]]}
    return respond, asked


def test_the_introduction_and_transitions_go_back_to_the_smoothing_step_when_the_reviewer_asks(vault_root):
    config.set_review_policy(ReviewPolicy(max_repair_rounds=1))
    job = pipeline.prepare(vault_root, domain=DOMAIN, mode="parallel")
    respond, asked = smooth_reviewer({"intro/1"}, times=1)
    producer = FakeExecutor("gpt-writer", responder=writer())
    result = pipeline.advance(vault_root, job["job_id"], producer=producer,
                              reviewer=FakeExecutor("claude-reviewer", responder=respond), checker=FakeMathJax())
    repairs = [t for t in producer.calls if t.chunk == pipeline.SMOOTH and t.step == "repair"]
    assert len(asked) == 2 and len(repairs) == 1
    assert "十个例子" in repairs[0].prompt and repairs[0].packet["previous_candidate"]["intro"]
    assert result["report"]["smooth"]["status"] == "accepted" and result["report"]["flagged"] == []


def test_an_introduction_still_failing_after_the_rounds_is_marked_and_listed(vault_root, tmp_path):
    from secondbrain.service import Service
    config.set_review_policy(ReviewPolicy(max_repair_rounds=0))
    job = pipeline.prepare(vault_root, domain=DOMAIN, mode="parallel")
    respond, _ = smooth_reviewer({"intro/1"}, times=1)
    result = pipeline.advance(vault_root, job["job_id"], producer=FakeExecutor("gpt-writer", responder=writer()),
                              reviewer=FakeExecutor("claude-reviewer", responder=respond), checker=FakeMathJax(),
                              out_dir=tmp_path / "o")
    report = result["report"]
    assert report["escalated_units"] == [pipeline.SMOOTH] and report["flagged"][0]["paragraph"] == "intro/1"
    md = Path(result["files"]["markdown"]).read_text(encoding="utf-8")
    mark = labels.export("zh")["flag"].format(reason="“十个例子” 不在给出的段落里")
    assert f"# 合成图谱\n\n{mark}\n\n本文按支柱顺序讲合成图谱。" in md
    assert [i["paragraph"] for i in Service(vault_root).owner_items() if i.get("kind") == "export_paragraph"] == [
        "intro/1"]


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
        result = pipeline.advance(vault_root, job["job_id"], producer=host, reviewer=host, checker=FakeMathJax())
        assert result["status"] == "awaiting_submission"
        assert len(result["handoffs"]) == expected
        again = pipeline.advance(vault_root, job["job_id"], producer=host, reviewer=host, checker=FakeMathJax())
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
                         reviewer=FakeExecutor("glm-b", responder=reviewer()), checker=FakeMathJax())


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


# --- the MathJax parse check: checked before any model call, never skipped without a word ------------------------
class AlwaysOk:
    name = "always-ok"

    def check(self, items):
        return [None for _ in items]


@pytest.mark.parametrize("checker, why", [
    (None, "node is not on PATH"),
    (AlwaysOk(), "does not tell a valid formula from a broken one"),
])
def test_an_export_whose_formulas_cannot_be_checked_stops_before_any_model_call(vault_root, checker, why):
    job = pipeline.prepare(vault_root, domain=DOMAIN, mode="parallel")
    producer, review = FakeExecutor("gpt-writer", responder=writer()), FakeExecutor("claude-r", responder=reviewer())
    with pytest.raises(checks.CheckerUnavailable, match=why) as stopped:
        pipeline.advance(vault_root, job["job_id"], producer=producer, reviewer=review, checker=checker)
    assert producer.calls == [] and review.calls == [] and "--no-formula-check" in str(stopped.value)


@pytest.mark.skipif(not __import__("shutil").which("node"), reason="needs node")
def test_node_without_mathjax_stops_the_export_before_any_model_call(vault_root, tmp_path):
    script = Path(__file__).resolve().parents[1] / "tools" / "mathjax_check.js"
    checker = checks.NodeMathJax(script, node_path=str(tmp_path / "no_modules"))
    assert "Cannot find module" in checks.preflight(checker)
    job = pipeline.prepare(vault_root, domain=DOMAIN, mode="parallel")
    producer = FakeExecutor("gpt-writer", responder=writer())
    with pytest.raises(checks.CheckerUnavailable, match="Cannot find module"):
        pipeline.advance(vault_root, job["job_id"], producer=producer,
                         reviewer=FakeExecutor("claude-r", responder=reviewer()), checker=checker)
    assert producer.calls == []


def test_without_the_check_the_article_says_its_formulas_were_not_checked(vault_root, tmp_path):
    config.set_review_policy(ReviewPolicy(max_repair_rounds=0))
    job = pipeline.prepare(vault_root, domain=DOMAIN, mode="parallel")
    result = pipeline.advance(vault_root, job["job_id"], producer=FakeExecutor("gpt-writer", responder=writer()),
                              reviewer=FakeExecutor("claude-r", responder=reviewer()), checker=None,
                              out_dir=tmp_path / "o", formula_check=False)
    report = result["report"]
    assert report["formula_parse_checked"] is False and "node is not on PATH" in report["formula_check_skipped"]
    md = Path(result["files"]["markdown"]).read_text(encoding="utf-8")
    note = labels.export("zh")["unchecked"].format(reason=report["formula_check_skipped"])
    assert f"# 合成图谱\n\n{note}\n\n" in md


def test_the_command_reports_a_check_that_cannot_run_as_an_error_and_goes_on_only_when_told(vault_root, tmp_path,
                                                                                         monkeypatch, capsys):
    from secondbrain import cli
    monkeypatch.setattr(cli, "export_executors", lambda args, root: (
        FakeExecutor("gpt-writer", responder=writer()), FakeExecutor("claude-r", responder=reviewer())))
    monkeypatch.setattr(checks.NodeMathJax, "from_env", classmethod(lambda cls: None))
    base = ["--vault", str(vault_root), "export", "--domain", DOMAIN, "--out", str(tmp_path / "o"), "--max-rounds", "0"]
    assert cli.main(base) == 2
    assert "cannot be parse-checked" in json.loads(capsys.readouterr().err)["error"]
    assert cli.main(base + ["--no-formula-check"]) == 0
    assert json.loads(capsys.readouterr().out)["report"]["formula_check_skipped"]


def real_checker():
    checker = checks.NodeMathJax.from_env()
    return checker if checker is not None and checks.preflight(checker) is None else None


@pytest.mark.skipif(real_checker() is None,
                    reason="needs node and mathjax-full (SB_MATHJAX_NODE_PATH=<folder>/node_modules)")
def test_the_real_mathjax_checker_tells_valid_formulas_from_broken_ones(vault_root):
    checker = real_checker()
    assert checker.check([("a_{3}", False), ("\\bad", False), ("\\frac{a}{b", False), ("a \\mid b", True),
                          ("\\operatorname{softmax}(x)", False)]) == [None, checker.check([("\\bad", False)])[0],
                                                                       checker.check([("\\frac{a}{b", False)])[0],
                                                                       None, None]
    assert "Undefined control sequence" in checker.check([("\\bad", False)])[0]

    def bad_writer(task):
        out = writer()(task)
        if task.chunk == "u01" and task.step == "produce":
            out["parts"][-1]["paragraphs"].append("记作 $\\bad$。[[合成卡 003]]")
        return out
    config.set_review_policy(ReviewPolicy(max_repair_rounds=0))
    job = pipeline.prepare(vault_root, domain=DOMAIN, mode="parallel")
    result = pipeline.advance(vault_root, job["job_id"], producer=FakeExecutor("gpt-w", responder=bad_writer),
                              reviewer=FakeExecutor("claude-r", responder=reviewer()), checker=checker)
    assert result["report"]["finding_codes"]["formula_parse_error"] == 1 and result["report"]["checker"] == "mathjax"


def test_a_card_no_paragraph_cites_is_listed_at_the_end_of_the_article(vault_root, tmp_path):
    _, result, _, _ = run(vault_root, skip={"syn_003"}, rounds=0, out=tmp_path / "o")
    assert result["report"]["unused"] == ["合成卡 003"]
    md = Path(result["files"]["markdown"]).read_text(encoding="utf-8")
    assert md.endswith(f"## {labels.export('zh')['unused']}\n\n- [[合成卡 003]]\n")


def test_a_literal_backslash_n_outside_formulas_is_a_finding(vault_root):
    """Second verdict, not blocking 7: export reads TeX backslashes leniently (D-13), so a line-break escape before a
    letter in an answer comes in as a backslash and an n; the program check sends such a paragraph back."""
    from secondbrain.executors.api import parse_json_text
    index = checks.CardIndex.from_input(jobs.read_input(vault_root, pipeline.prepare(vault_root, domain=DOMAIN)["job_id"]))
    # strict JSON fails on the raw \mid, so the answer is read again with TeX backslashes doubled (D-13)
    text = parse_json_text('{"p": "第一行\\nline two，记作 $a\\mid b$。[[合成卡 001]]"}', tex_backslashes=True)["p"]
    assert "\\nline" in text
    assert [f["code"] for f in checks.check_paragraph("p1.1", text, index)
            if f["code"] == "literal_newline"] == ["literal_newline"]
    clean = "合成卡 1 的量取 101，记作 $a_{1}$ 与 $\\nabla f$。[[合成卡 001]]"
    assert not [f for f in checks.check_paragraph("p1.1", clean, index) if f["code"] == "literal_newline"]


def test_a_backslash_n_inside_inline_code_is_code_not_a_finding(vault_root):
    """Third verdict, not blocking 3: `s.rstrip("\\r\\n")` in a paragraph about strings is code; only a \\n in the
    prose is a line break read wrongly."""
    index = checks.CardIndex.from_input(jobs.read_input(vault_root, pipeline.prepare(vault_root, domain=DOMAIN)["job_id"]))
    code = '用 `s.rstrip("\\r\\n")` 去掉行尾，量取 101。[[合成卡 001]]'
    assert "\\n" in code
    assert not [f for f in checks.check_paragraph("p1.1", code, index) if f["code"] == "literal_newline"]
    prose = '第一行\\nline two，用 `x`，量取 101。[[合成卡 001]]'
    assert [f["code"] for f in checks.check_paragraph("p1.1", prose, index) if f["code"] == "literal_newline"] == \
        ["literal_newline"]
