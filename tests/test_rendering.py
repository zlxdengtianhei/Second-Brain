"""Tests for the formula-normalizing renderer (pure functions).

Two input groups:
1. The 33 self-test cases of the original implementation, which pin the
   repair semantics.
2. Representative lines that must stay byte-identical: code fences, inline
   code, wiki links, URLs, HTML comments and tables (protection duties).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from secondbrain.rendering import normalize_markdown, normalize_math

# ---------------------------------------------------------------------------
# 旧仓 fix_math.py SELF_TEST（33 条，逐字）。注释为旧文件中的分类。
# ---------------------------------------------------------------------------
OLD_SAMPLES = [
    # 该修的
    ("三组参数 W_Q、W_K、W_V 从同一份输入",
     "三组参数 $W_Q$、$W_K$、$W_V$ 从同一份输入"),
    ("即 Q = XW_Q，K = XW_K", "即 $Q = XW_Q$，$K = XW_K$"),
    ("形状为 [S, d_model] 的输入", "形状为 $[S, d_{model}]$ 的输入"),
    ("GPT 用 P_θ(w_t | w_1,...,w_{t-1}) 建模",
     "GPT 用 $P_θ(w_t | w_1,...,w_{t-1})$ 建模"),
    ("每个 v_i 乘以 weight_i 求和", "每个 $v_i$ 乘以 $weight_i$ 求和"),
    # 整段并成一个公式，而不是切碎
    ("条件概率来自计数比 P(w_t|h)=C(h,w_t)/C(h)；",
     "条件概率来自计数比 $P(w_t|h)=C(h,w_t)/C(h)$；"),
    ("已经切碎的 P($w_t$|h)=C(h,$w_t$)/C(h) 要并回去",
     "已经切碎的 $P(w_t|h)=C(h,w_t)/C(h)$ 要并回去"),
    # 裸宏名补反斜杠
    ("交叉熵为 H(p,q_{theta})=-E_{w~p} log q_{theta}(w)",
     "交叉熵为 $H(p,q_{\\theta})=-E_{w~p} \\log q_{\\theta}(w)$"),
    ("对似然 prod_t P_theta(w_t|w_<t) 取对数",
     "对似然 $\\prod_t P_\\theta(w_t|w_{<t})$ 取对数"),
    ("- P1. 投影矩阵 W_Q 是可学习的", "- P1. 投影矩阵 $W_Q$ 是可学习的"),
    ("- P2. 分数除以 sqrt(d_k) 做缩放", "- P2. 分数除以 $\\sqrt{d_k}$ 做缩放"),
    # 不该动的
    ("关系类型 depends_on 与 part_of", "关系类型 depends_on 与 part_of"),
    ("已经写好的 $W_Q$ 不重复包", "已经写好的 $W_Q$ 不重复包"),
    ("行内代码 `d_model` 不动", "行内代码 `d_model` 不动"),
    ("链接 [[多头注意力]] 不动", "链接 [[多头注意力]] 不动"),
    ("frontmatter 字段 sr_due 不动", "frontmatter 字段 sr_due 不动"),
    ("路径 10_Knowledge/attention/ 不动", "路径 10_Knowledge/attention/ 不动"),
    ("n-gram 只保留最近 n-1 个词", "n-gram 只保留最近 n-1 个词"),
    ("masked self-attention 处理长依赖", "masked self-attention 处理长依赖"),
    ("The value of d_model is 512 and heads", "The value of $d_{model}$ is 512 and heads"),
    ("预算 $500 超支 $200", "预算 $500 超支 $200"),
    # 回归样例：`::` 曾把公式吞进去、两面各留一个不闭合的 $（19 张卡中招）
    ("每个头的维度是多少？::d_head = d_model / a",
     "每个头的维度是多少？::$d_{head} = d_{model} / a$"),
    ("链式法则：p_\\theta(w_{1:T}) 是整段概率",
     "链式法则：$p_\\theta(w_{1:T})$ 是整段概率"),
    # `::` 硬切分与花括号原子化的边界（自查时逐条实测过，钉在这里防回归）
    ("行内 code 里的 `a::b` 与 W_Q", "行内 code 里的 `a::b` 与 $W_Q$"),
    ("C++ 的 std::vector 与 d_model", "C++ 的 std::vector 与 $d_{model}$"),
    ("嵌套花括号 e^{z_{i,j}} 的形状", "嵌套花括号 $e^{z_{i,j}}$ 的形状"),
    ("链接 [[某卡::别名]] 不动 与 v_i", "链接 [[某卡::别名]] 不动 与 $v_i$"),
    ("三个冒号 a:::b 与 y_t", "三个冒号 a:::b 与 $y_t$"),
    # 第三轮核验点名的三个根因：枚举字符表漏 `⊙`、`∑`(U+2211) 不能当下标锚点、
    # 组合字符 `C̃`(C+U+0303) 不在任何表里。取反式字符集之后三条都成立。
    ("LSTM 按 C_t=C_{t-1}⊙f_t+i_t⊙C̃_t 更新",
     "LSTM 按 $C_t=C_{t-1}⊙f_t+i_t⊙C̃_t$ 更新"),
    ("概率必须满足 ∑_x P(x)=1，需要归一化",
     "概率必须满足 $∑_x P(x)=1$，需要归一化"),
    ("最终 h_t=o_t⊙tanh(C_t)。", "最终 $h_t=o_t⊙\\tanh(C_t)$。"),
    # 第四轮：`^(…)` 的上标只抓左括号，检测侧（SUB 的圆括号组）与归一侧必须同改
    ("隐藏状态 h∈R^(1×D) 经输出层", "隐藏状态 $h∈R^{(1×D)}$ 经输出层"),
    ("连乘项 ∏_{k=1}^{T} tanh(z^(k))W 会衰减",
     "连乘项 $∏_{k=1}^{T} \\tanh(z^{(k)})W$ 会衰减"),
]


@pytest.mark.parametrize("src,want", OLD_SAMPLES)
def test_old_self_test_cases(src, want):
    """旧内置样例逐一对应旧实现（fix_math.py:462-515）的修复语义。"""
    new, count = normalize_math(src)
    assert new == want
    # 旧计数语义：改动的行，区段数＝$…$ 对数；未改动的行恒为 0
    if new != src:
        assert count == want.count("$") // 2, (src, count)
    else:
        assert count == 0


@pytest.mark.parametrize("src,want", OLD_SAMPLES)
def test_old_self_test_idempotent(src, want):
    """一遍与二遍结果一致（旧 self_test 的幂等检查，防 $…$ 剥了包不回去）。"""
    once, _ = normalize_math(src)
    twice, _ = normalize_math(once)
    assert twice == once


# ---------------------------------------------------------------------------
# Protection duties: lines whose formulas live in inline code, links, tables
# or URLs must come out byte-identical.
# ---------------------------------------------------------------------------
REAL_LINES = [
    ("The judge explains it in its FAQ: [candidate notes](https://example.com/help/articles/12345-run-code-and-debug)", 1),
    ("Check `[2, 2]` by hand: the map starts empty, store `{2: 0}`; the second 2 finds its partner and returns `[0, 1]`. Time `O(n)`, extra space `O(n)`.", 2),
    ("| Scaled dot-product attention | `QKᵀ / √d`, mask, softmax, times V |", 3),
    ("We build a Pre-LN block step by step; it has no KV cache or position encoding. Compare with [the original paper](https://arxiv.org/abs/1706.03762).", 4),
    ("Notation: `B` batch, `H` heads, `T` sequence length, `D` model width, `d = D // H` per-head width.", 5),
    ("| `Q @ K.transpose(-2, -1)` | `[B, H, T, T]` | every query scores every key |", 6),
    ("- `dim=-1` applies softmax along the key axis.", 7),
    ("Sanity check: equal scores give uniform weights. softmax is not score over sum: `softmax([51,49])` is about `[0.8808,0.1192]`.", 8),
    ("Data flow first: `x → Q/K/V projections → split into H heads → attention → merge → output projection`.", 9),
    ("`Linear(D, D)` maps the last axis, so `[B,T,D]` stays `[B,T,D]`. Q/K/V come from the same x with three parameter sets; never `reshape(B,T,D)` before swapping back to `[B,T,H,d]`.", 10),
    ("On paper with `B=2,T=4,D=8,H=2`: `[2,4,8] → [2,4,2,4] → [2,2,4,4]`.", 11),
    ("The FFN maps each position `D → d_ff → D`; `norm1` and `norm2` are separate modules.", 12),
    ("Post-LN means `x = norm1(x + attn(x))`, then `x = norm2(x + ffn(x))`. [norm_first docs](https://docs.example.org/nn/encoder-layer.html)", 13),
    ("Attention time is about `O(B*T²*D)` and score memory about `O(B*H*T²)`.", 14),
    ("- **NumPy-only**: same data flow with `@`, `swapaxes`, `reshape`, a stable softmax and explicit weights; no `nn.Module`.", 15),
]


@pytest.mark.parametrize("line_no", [no for _, no in REAL_LINES])
def test_real_source_lines_unchanged(line_no):
    """真实来源代表性行逐字不变（原文全部用行内 code，公式区段不得误伤）。"""
    line = next(s for s, no in REAL_LINES if no == line_no)
    assert normalize_math(line) == (line, 0)


def test_code_fence_content_never_touched():
    """Everything inside a fenced block stays byte-identical."""
    doc = "\n".join(["Intro with W_Q.", "```python", "x_y = d_model ** 2", "W_Q = W_K @ W_V",
                     "```", "Outro with d_model.", ""])
    normalized, _, _ = normalize_markdown(doc)
    assert "x_y = d_model ** 2\nW_Q = W_K @ W_V" in normalized
    assert normalized != doc


# ---------------------------------------------------------------------------
# Composite document: frontmatter with underscores and paths, a code fence with
# underscores and operators, and a body with bare subscripts. Frontmatter and
# fence must stay; the body is fixed; counts and issues keep their meaning.
# ---------------------------------------------------------------------------
FRONTMATTER = """---
id: card_attention_qkv
title: 示例卡：张量流
kind: mechanism
domain: attention
tags: [review, flashcards/attention]
aliases: [QKV, 查询键值流]
sources:
  - path: 40_Sources/sessions/session_example_01.md
    sha256: <hash>
    locator: "示例定位句"
relations:
  - type: part_of
    target: card_block
status: confirmed
created: 2026-08-21
---
"""

FENCE = """def scaled_attention(q, k, v, causal=False):
    d = q.size(-1)
    scores = (q @ k.transpose(-2, -1)) / (d ** 0.5)

    if causal:
        t = q.size(-2)
        blocked = torch.ones(t, t, dtype=torch.bool,
                             device=q.device).triu(diagonal=1)
        scores = scores.masked_fill(blocked, float('-inf'))

    weights = torch.softmax(scores, dim=-1)
    return weights @ v, weights"""

BODY_BEFORE = "正文：$W_Q$ 与 d_model 与 W_K。"
BODY_AFTER = "$W_Q$ 与 $d_{model}$ 与 $W_K$。"


def _composite_doc() -> str:
    return "\n".join([
        FRONTMATTER.rstrip("\n"),
        "",
        BODY_BEFORE,
        "",
        "```python",
        FENCE,
        "```",
        "",
        "形状为 [S, d_model] 与 Q = XW_Q。",
        "",
    ])


def test_frontmatter_and_fence_preserved_body_fixed():
    doc = _composite_doc()
    normalized, total, issues = normalize_markdown(doc)
    # frontmatter 逐字不动（含其内部的下划线字段与路径）
    assert normalized.startswith(FRONTMATTER)
    assert "40_Sources/sessions/session_example_01.md" in normalized
    assert "card_block" in normalized
    # 代码围栏逐字不动（围栏内的 `_`、`**`、softmax 等一律不碰）
    fence_marker = normalized.index("```python")
    fence_rest = normalized.index("```", fence_marker + len("```python"))
    assert normalized[fence_marker + len("```python"):fence_rest].strip("\n") == FENCE
    assert "torch.softmax(scores, dim=-1)" in normalized
    # 正文修复
    assert BODY_AFTER in normalized
    assert "形状为 $[S, d_{model}]$ 与 $Q = XW_Q$。" in normalized
    # 计数：正文 3 段 + 尾行 2 段
    assert total == 5


def test_normalize_markdown_interface_and_issues():
    """新接口 (normalized, change_count, issues) 的语义：issues 是旧 samples 的
    同义（最多 2 条「旧行\\n新行」样例），无改动为空；幂等。"""
    doc = _composite_doc()
    normalized, total, issues = normalize_markdown(doc)
    assert isinstance(normalized, str)
    assert isinstance(total, int) and total > 0
    assert isinstance(issues, list) and len(issues) == 2
    assert all(i.startswith("    旧 ") and "\n    新 " in i for i in issues)
    # 无改动文档：零计数、空 issues、文本原样
    untouched, t0, i0 = normalize_markdown("纯中文正文，没有公式。\n\n`O(n)` 与 [[链接]] 与 https://example.com/x_y\n")
    assert untouched == "纯中文正文，没有公式。\n\n`O(n)` 与 [[链接]] 与 https://example.com/x_y\n"
    assert t0 == 0 and i0 == []
    # 幂等：二遍 == 一遍
    again, t2, _ = normalize_markdown(normalized)
    assert again == normalized and t2 == 0


def test_skip_guard_lines_untouched():
    """文档记录的整行跳过守卫：块级 `$$`、金额 `$` 紧跟数字、落单的奇/偶 `$`。"""
    for line in [
        "$$E = mc^2$$ 块级公式行整体跳过",
        "预算 $500 超支 $200",
        "$3000 是金额",
        "只有一个 $ 落单",
        "这里 $ 配不上的 $ 不对、",
    ]:
        assert normalize_math(line) == (line, 0), line


def test_html_comment_protected_around_fix():
    """HTML 注释不被 `::` 硬切分切到、注释体不动，只修注释外的裸下标。"""
    line = "<!-- Q::K 与 d_model 是注释 -->然后 W_Q 与 d_model"
    new, count = normalize_math(line)
    assert new == "<!-- Q::K 与 d_model 是注释 -->然后 $W_Q$ 与 $d_{model}$"
    assert count == 2


def test_sqrt_parentheses_become_a_radical_group():
    text = '按 sqrt(d_k) 缩放；代码 `sqrt(d_k)` 保留。'
    expected = '按 $\\sqrt{d_k}$ 缩放；代码 `sqrt(d_k)` 保留。'
    assert normalize_math(text)[0] == expected
    assert normalize_math(expected)[0] == expected
