"""Formula normalization for card bodies (pure functions, no I/O).

Every card body is passed through :func:`normalize_markdown` before it is
written, so formula notation is fixed in one place instead of relying on each
writer. It repairs three problems and is idempotent, so re-rendering also
heals text that is already on disk.

一起修的三类问题（2026-08-21 实测，183 张卡）：
1. 裸下标：正文写 ``W_Q、W_K、W_V`` 而不是 ``$W_Q$``。Markdown 把一行里的
   两个 ``_`` 当成斜体标记，下标不渲染，中间的中文还会变斜体。
2. 多字符下标没花括号：``d_model`` 即使包进 ``$…$``，MathJax 也只把 ``m``
   当下标，渲染成 d·ₘodel。必须 ``d_{model}``。
3. 裸宏名：抽取器写出伪 LaTeX ``prod_t``、``log``、``q_{theta}``，包进
   ``$…$`` 也渲染成正体字母串；必须补反斜杠（MathJax 无内置的软名走
   ``\\operatorname{}``）。

为什么按区段包而不是逐 token：``P(w_t|h)=C(h,w_t)/C(h)`` 逐 token 包会切成
``P($w_t$|h)=C(h,$w_t$)/C(h)``，比不包还难读。所以先按中日韩字符与受保护
片段切段，段内再按空格切块，只有连续的数学块才并成一个区段。

保守策略（宁可漏，不可错）：
- 只动正文，frontmatter 不碰；代码围栏、行内 ``code``、``[[wiki]]``、URL、
  HTML 注释、markdown 链接全部跳过。
- 一行里 ``$`` 为奇数、出现 ``$`` 紧跟数字（金额）、或含 ``$$``，整行跳过。
- 数学块判据：块内带 ``_``/``^``、是已知宏名、或每段字母只有一个字母；区段
  还须至少含一个真正带下标且非标识符的块才落笔。
- 区段两端裁空格与游离标点，括号不平衡裁到平衡（``$…$`` 内首尾禁空格）。
"""
from __future__ import annotations

import re

# --------------------------------------------------------------------------
# 常量与正则（与旧实现逐一对应；本域反例经三轮核验收敛，勿随手扩表）

GREEK = "Ͱ-Ͽ"
# 区段边界＝中日韩文字与全角标点（`::` 另由 normalize_math 开头的硬切分处理，
# 不能只靠字符表：`:` 在 `w_{1:T}` 里合法，移出表会让下标从中间断开）。
CJK = "\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef" \
      "\u2018\u2019\u201c\u201d\u2014\u2013"
# MATH_CHARS 已含前导 ^，是负字符类：除边界与空白外都可进区段
MATH_CHARS = rf"^{CJK}\s"
MATH_CHUNK_RE = re.compile(rf"^[{MATH_CHARS}]+$")
LETTER_RUN_RE = re.compile(r"[A-Za-z]+")

# 下标锚点：拉丁/希腊字母之外，还认 ∑∏∫∮ 这类算子与组合符（`C̃` = C+U+0303），
# 否则 `∑_x P(x)=1` 判不出「带下标」，整段留在数学模式外。
ANCHOR = rf"A-Za-z{GREEK}\u2200-\u22ff\u0300-\u036f"
BASE = rf"[{ANCHOR}][{ANCHOR}0-9]*"
# 下标形态：花括号组 / `\theta` 这类命令（归一后的产物，二次扫必须仍认得它，
# 否则 `$q_\theta$` 会被判成「没有下标」而被剥掉 $，幂等在第二遍上来回拉锯）/
# 圆括号组（`∂h^(k)` 这种写法，不认它就判不出「带上标」）/ 裸字母数字。
SUB = rf"(?:\{{[^}}\n]{{1,24}}\}}|\\[A-Za-z]+|\([^)\n]{{1,16}}\)|[<>]?[{ANCHOR}0-9]+(?:[+\-][0-9]+)?)"
MATH_TOKEN = re.compile(rf"{BASE}[_^]{SUB}")

# 裸宏名 → LaTeX 命令（只收本域高频且无歧义的；`pi`/`in` 等易撞变量名的不收）
MACROS = [
    "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta",
    "iota", "kappa", "lambda", "mu", "nu", "xi", "rho", "sigma", "tau",
    "phi", "chi", "psi", "omega",
    "Gamma", "Delta", "Theta", "Lambda", "Sigma", "Phi", "Psi", "Omega",
    "sum", "prod", "int", "oint", "log", "ln", "exp", "max", "min",
    "sin", "cos", "tan", "tanh", "sinh", "cosh", "sigmoid", "relu",
    "sqrt", "frac", "infty", "approx", "cdot", "times", "neq", "leq", "geq",
    "sim", "propto", "partial", "nabla", "mathbb", "text",
    # 符号类命令名——写成裸词会渲染成斜体字母串（`bullet` → b·u·l·l·e·t）。
    # 只收无歧义的：`to`/`mid`/`cap`/`cup` 这些同时是英文常用词的不收。
    "bullet", "circ", "oplus", "otimes", "ldots", "cdots", "vdots",
    "emptyset", "forall", "exists", "equiv", "parallel", "perp",
    "langle", "rangle", "mapsto", "subseteq", "supseteq", "bigl", "bigr",
]
# softmax / argmax / argmin 不是 MathJax 内置命令，写 \softmax 会渲成红字未
# 定义控制序列；它们走 \operatorname{}。
OPERATORS = ["softmax", "argmax", "argmin"]
MACRO_RE = re.compile(r"(?<![\\A-Za-z])(" + "|".join(MACROS) + r")(?![A-Za-z])")
OPERATOR_RE = re.compile(r"(?<![\\A-Za-z{])(" + "|".join(OPERATORS) + r")(?![A-Za-z}])")
MACRO_WORDS = set(MACROS) | set(OPERATORS)

# 标识符形态（frontmatter 字段、关系类型、路径等）永远不算数学
IDENT_PREFIX = ("kc_", "sr_", "cl_", "cr_", "co_", "req_", "task_", "session_")
IDENT_WORDS = {
    "depends_on", "part_of", "contrasts_with", "similar_to", "generalizes",
    "follows", "from_claims", "merged_claims", "claim_id", "source_file",
    "notes_registry", "review_events", "kb_review", "generated_by",
    "domain_hint", "merge_log", "skipped_claims", "links_only", "flashcards",
    "sr_due", "sr_interval", "sr_ease", "self_attention", "cross_entropy",
    "log_softmax", "10_Knowledge", "20_MOCs", "30_Exercises", "50_Reviews",
}
SNAKE = re.compile(r"^[a-z]{2,}(_[a-z]{2,})+$")

# 受保护片段：行内 code、[[wiki]]、URL、HTML 注释、markdown 链接
PROTECT = [
    re.compile(p) for p in (
        r"`[^`\n]*`", r"\[\[[^\]]*\]\]", r"https?://\S+", r"<!--.*?-->",
        r"\[[^\]]*\]\([^)]*\)",
    )
]
DOLLAR_INLINE = re.compile(r"\$([^$\n]+)\$")

ENUM_RE = re.compile(r"^[A-Za-z]?\d*\.$")
LATEX_CMD = re.compile(r"\\[a-zA-Z]{2,}")
# `\text{}` 这类命令的花括号内允许任意字符（含中文），整组当作数学的一部分
TEXTLIKE_CMD = re.compile(r"\\(?:text|mathrm|mathbf|mathit|operatorname)\{[^{}]*\}")


def _protected(line: str) -> list[tuple[int, int]]:
    """收集受保护片段（行内 code / wiki 链接 / URL / HTML 注释 / 链接）的跨度。"""
    spans: list[tuple[int, int]] = []
    for pat in PROTECT:
        spans += [(m.start(), m.end()) for m in pat.finditer(line)]
    return spans


def _is_identifier(tok: str) -> bool:
    return tok in IDENT_WORDS or tok.startswith(IDENT_PREFIX) or bool(SNAKE.match(tok))


def _is_math_chunk(chunk: str) -> bool:
    """一个空格分隔块是否属于公式。

    判据：块里带 `_`/`^`（下标本来就可以是多字母，如 `d_model`），或它是已知
    宏名，或它的每段字母都只有一个字母。**两字母不放行**——放行会把 `of`/`is`/
    `in` 这类英文虚词吸进公式，实测把整句 `of d_model is 512` 包进了 $…$。
    枚举标记（`P1.`、`3.`）永远不算公式，否则 `- P1. W_Q` 会被整体包起来。
    """
    if not chunk or ENUM_RE.match(chunk):
        return False
    # 花括号组保持原子后，块里可能合法地含空格（`\mathbb{E}_{X\sim p}`）。
    # 字符表检查前先把花括号组挖空，否则这类块会被判成非数学、整段公式丢掉 $。
    probe = re.sub(r"\{[^{}]*\}", "{}", chunk)
    if not MATH_CHUNK_RE.match(probe):
        return False
    if "_" in chunk or "^" in chunk or LATEX_CMD.search(chunk):
        return True
    # 反斜杠命令里的字母不参与「字母段长度」判据——`\mid` 的 mid 是命令名不是
    # 英文词。不剔除的话 `P(D\mid\theta)` 会被判成非数学块，整段按字面显示。
    stripped = re.sub(r"\\[a-zA-Z]+", "", chunk)
    return all(len(r) == 1 or r in MACRO_WORDS for r in LETTER_RUN_RE.findall(stripped))


def _carries_subscript(chunk: str) -> bool:
    """块里有没有「这确实是公式」的证据：真下标/上标，或 LaTeX 命令。

    只认下标还不够——`P(D\\mid\\theta)` 有命令没下标，判不出来就整段留在数学模
    式外，`\\mid` 按字面显示（2026-08-22 独立核验在 14 行上实测到这个形态）。
    """
    if any(not _is_identifier(m.group(0)) for m in MATH_TOKEN.finditer(chunk)):
        return True
    return bool(LATEX_CMD.search(chunk))


def _balanced(s: str) -> bool:
    depth = {"(": 0, "[": 0, "{": 0}
    pair = {")": "(", "]": "[", "}": "{"}
    for ch in s:
        if ch in depth:
            depth[ch] += 1
        elif ch in pair:
            depth[pair[ch]] -= 1
            if depth[pair[ch]] < 0:
                return False
    return all(v == 0 for v in depth.values())


def _trim(expr: str) -> str:
    """裁掉两端空格、游离标点，再裁到括号平衡。"""
    expr = expr.strip()
    while expr and expr[-1] in ".,;:":
        expr = expr[:-1]
    while expr and not _balanced(expr):
        if expr[-1] in ")]}":
            expr = expr[:-1]
        elif expr[0] in "([{":
            expr = expr[1:]
        else:
            break
    return expr.strip()


def _latexify(expr: str) -> str:
    """区段内的写法归一：补花括号下标 + 裸宏名补反斜杠。"""
    expr = MACRO_RE.sub(lambda m: "\\" + m.group(1), expr)
    expr = OPERATOR_RE.sub(lambda m: "\\operatorname{" + m.group(1) + "}", expr)
    expr = re.sub(r"\\sqrt\(([^()\n]{1,30})\)",
                  lambda m: "\\sqrt{" + m.group(1) + "}", expr)
    # `√d_k` → `\sqrt{d_k}`。`√` 只是一个字符，横线不覆盖后面的内容——渲染出来
    # 像「根号 乘 d_k」。`$…$` 内出现裸 `√` 基本必错。
    expr = re.sub(r"√\s*\(([^()\n]{1,30})\)", lambda m: "\\sqrt{" + m.group(1) + "}", expr)
    expr = re.sub(r"√\s*(\{[^{}\n]{1,30}\})", lambda m: "\\sqrt" + m.group(1), expr)
    expr = re.sub(rf"√\s*([{ANCHOR}][{ANCHOR}0-9]*(?:[_^](?:\{{[^}}\n]{{1,20}}\}}|[{ANCHOR}0-9]+))?)",
                  lambda m: "\\sqrt{" + m.group(1) + "}", expr)
    # `^(k)` → `^{(k)}`。TeX 的 `^` 只抓下一个 token，`(` 就是一个 token——
    # `R^(1×D)` 会渲染成「R 的上标是一个左括号」，`1×D)` 掉回基线。
    # 这一条必须与 SUB 里的圆括号组同时存在：只让检测侧认它、归一侧不转，
    # 会把这些片段拉进数学模式而进去之后是坏的（2026-08-22 第四轮核验，净增 9 行）。
    expr = re.sub(r"([_^])(\([^()\n]{1,20}\))",
                  lambda m: f"{m.group(1)}{{{m.group(2)}}}", expr)
    # `d_model` → `d_{model}`；`w_<t` → `w_{<t}`；已带花括号或单字符下标不动
    expr = re.sub(rf"([_^])([<>]?[A-Za-z0-9{GREEK}]{{2,}})",
                  lambda m: f"{m.group(1)}{{{m.group(2)}}}", expr)
    expr = re.sub(rf"([_^])([<>][A-Za-z0-9{GREEK}])",
                  lambda m: f"{m.group(1)}{{{m.group(2)}}}", expr)
    # \theta 之后紧跟 } 之类不用管；\frac 等命令后的花括号本来就在原文里
    return expr


def _split_outside_braces(seg: str) -> list[str]:
    r"""按空格切块，但花括号组内的空格不切——`\mathbb{E}_{X\sim p}` 是一个块，
    切开会让后半截落单、$ 收在花括号里面。"""
    out, cur, depth = [], [], 0
    for ch in seg:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        if ch == " " and depth == 0:
            out.append("".join(cur)); cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


def _wrap_segment(seg: str) -> str:
    """段内按空格切块，连续数学块并成区段，含真下标的区段才包 $。"""
    parts = _split_outside_braces(seg)
    math_flag = [_is_math_chunk(p) for p in parts]
    outs: list[str] = []
    i = 0
    while i < len(parts):
        if not math_flag[i]:
            outs.append(parts[i])
            i += 1
            continue
        j = i
        while j < len(parts) and math_flag[j]:
            j += 1
        group = parts[i:j]
        if any(_carries_subscript(p) for p in group):
            raw = " ".join(group)
            expr = _trim(raw)
            if expr and _carries_subscript(expr):
                lead = raw[:raw.index(expr)]
                tail = raw[raw.index(expr) + len(expr):]
                outs.append(f"{lead}${_latexify(expr)}${tail}")
            else:
                outs.append(raw)
        else:
            outs.append(" ".join(group))
        i = j
    return " ".join(outs)


def normalize_math(line: str) -> tuple[str, int]:
    """把一行里的裸公式区段包进 $…$ 并归一写法。返回 (新行, 区段数)。"""
    # spaced-repetition 的 `::` 是硬边界：问题与答案是两段独立文本，公式区段
    # 绝不能跨过它（2026-08 曾把 `::` 吞进公式，19 张卡两面各留一个不闭合的 $）。
    if "::" in line and not line.lstrip().startswith("<!--"):
        head, tail = line.split("::", 1)
        a, na = normalize_math(head)
        b, nb = normalize_math(tail)
        return f"{a}::{b}", na + nb
    if "$$" in line or re.search(r"\$\d", line) or line.count("$") % 2:
        return line, 0  # 块级公式行 / 金额 / 落单的 $：整行不碰

    spans = _protected(line)
    # 先把已有的行内 $…$ 摘掉，让相邻碎片能并进同一区段；受保护区内的 $ 不摘
    stripped = []
    cursor = 0
    out: list[str] = []
    for m in DOLLAR_INLINE.finditer(line):
        if any(s < m.end() and m.start() < e for s, e in spans):
            continue
        if not _carries_subscript(m.group(1)):
            continue   # 剥了包不回去（如 `$A$`）——不剥，当受保护区留着
        out.append(line[cursor:m.start()])
        out.append(m.group(1))
        cursor = m.end()
        stripped.append(m.group(1))
    out.append(line[cursor:])
    plain = "".join(out)
    if stripped:
        spans = _protected(plain)

    # 按受保护片段与非数学字符切段
    mask = [True] * len(plain)  # True = 可进公式区段
    for s, e in spans:
        for i in range(s, min(e, len(plain))):
            mask[i] = False
    # `\text{中文}` / `\mathrm{}` / `\operatorname{}` 里的内容是数学的一部分，
    # 哪怕是中文。不这样处理，中文会被当成区段边界，把 `C(\text{上下文},\text{下一词})`
    # 从中间劈开，$ 落进花括号内（2026-08-22 实测）。
    opaque: list[tuple[int, int]] = []
    for m in TEXTLIKE_CMD.finditer(plain):
        opaque.append((m.start(), m.end()))
    for i, ch in enumerate(plain):
        if not mask[i]:
            continue
        if any(s <= i < e for s, e in opaque):
            continue
        if ch == " " or re.match(rf"[{MATH_CHARS}]", ch):
            continue
        mask[i] = False

    result: list[str] = []
    i = 0
    n = 0
    while i < len(plain):
        if not mask[i]:
            result.append(plain[i])
            i += 1
            continue
        j = i
        while j < len(plain) and mask[j]:
            j += 1
        seg = plain[i:j]
        result.append(_wrap_segment(seg))
        n += result[-1].count("$") // 2
        i = j
    new = "".join(result)
    # 守卫：本函数为了让相邻碎片能并段，先把已有的 $…$ 剥掉再重包。若某个原本
    # 就包好的片段重包后落在了 $…$ 之外（比如单符号 `$A$`），那就是把对的改坏
    # 了。这种情况整行原样退回，宁可不优化也不倒退。
    return (new, n) if new != line else (line, 0)


def normalize_markdown(text: str) -> tuple[str, int, list[str]]:
    """整份 markdown 的公式归一：跳过 frontmatter 与代码围栏，逐行 normalize_math。

    返回 (归一文本, 改动区段总数, issues)。issues 是改动样例（最多 2 条，每条
    「旧行\\n新行」两行字符串），无改动为空——与旧 fix_math 的 samples 同义。

    幂等：归一结果再过一遍不变（自测见 tests/test_rendering.py），渲染器每次
    重渲可无脑调用：既修新文本也自愈旧文本。
    """
    if text.startswith("---\n") and "\n---\n" in text[4:]:
        cut = text.index("\n---\n", 4) + len("\n---\n")
        head, body = text[:cut], text[cut:]
    else:
        head, body = "", text
    lines = body.split("\n")
    in_fence = False
    total = 0
    issues: list[str] = []
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not ln.strip():
            continue
        new, k = normalize_math(ln)
        if k:
            total += k
            if len(issues) < 2:
                issues.append(f"    旧 {ln.strip()[:78]}\n    新 {new.strip()[:78]}")
            lines[i] = new
    return head + "\n".join(lines), total, issues
