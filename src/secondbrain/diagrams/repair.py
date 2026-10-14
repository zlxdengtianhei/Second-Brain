"""Mechanical repair of old flowcharts that fail to parse.

Almost every broken diagram in the vault is a flowchart whose content is
fine and whose syntax trips over one of three things:
- a label with brackets, parentheses, braces or a pipe and no quotes:
  B[dp[i][j]], C[P(A|Bi)], B{左闭右闭 [L, R]};
- a node id that is a Mermaid keyword: call, style, graph, end...;
- several statements on one line without separators:
  flowchart LR A[x] --> B[y] C --> D[z].

repair() reads the source with a tolerant scanner (labels end at the closing
bracket that is followed by an arrow, another statement or the end of the
line, so nested brackets stay in the label), and writes it back one
statement per line with every label quoted and keyword ids renamed. It never
changes what a label says. Lines it does not understand (classDef, style,
click, comments) are kept as they are. The caller renders the result and
uses it only if it renders; a diagram that already renders is never passed
in.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Shape openers and their closers, longest first.
_SHAPES = [("(((", (")))",)), ("((", ("))",)), ("([", ("])",)), ("[[", ("]]",)), ("[(", (")]",)),
           ("[/", ("/]", "\\]")), ("[\\", ("\\]", "/]")), ("{{", ("}}",)), ("[", ("]",)), ("(", (")",)),
           ("{", ("}",)), (">", ("]",))]
_RESERVED = {"end", "graph", "flowchart", "subgraph", "style", "class", "classdef", "click", "call",
             "linkstyle", "default", "direction", "href", "callback", "interpolate", "acctitle", "accdescr"}
_KEYWORD_LINES = ("classdef", "class", "style", "linkstyle", "click", "direction")
_HEADER = re.compile(r"\s*(flowchart|graph)(?:[ \t]+(TB|TD|BT|RL|LR))?[ \t]*;?", re.I)
_ARROW = re.compile(r"[ \t]*(<?(?:-{2,}|={2,}|-\.+-|~{3,})(?:>|o|x)?)(?=[^>=.~-]|$)")
_TEXT_EDGE = re.compile(r"[ \t]*(<?)(--|==|-\.)(?![->.=])[ \t]*([^\n;]*?)[ \t]*(-->|---|==>|===|-\.->|\.->)")
_ID = re.compile(r"[^\s\[\](){}<>|;&\"=~:-]+(?:-(?![-.>=])[^\s\[\](){}<>|;&\"=~:-]+)*")
_PAIRS = {"(": ")", "[": "]", "{": "}"}
_SAFE_ID = re.compile(r"[A-Za-z0-9_\u4e00-\u9fff.]+(?:-[A-Za-z0-9_\u4e00-\u9fff.]+)*")


@dataclass(frozen=True)
class Repair:
    text: str
    fixes: tuple[str, ...]


def _balanced(s: str) -> bool:
    stack = []
    for ch in s:
        if ch in _PAIRS:
            stack.append(_PAIRS[ch])
        elif ch in _PAIRS.values():
            if not stack or stack.pop() != ch:
                return False
    return not stack


class _Scanner:
    def __init__(self, src: str):
        self.s = src
        self.i = 0
        self.fixes: set[str] = set()
        self.renamed: dict[str, str] = {}

    # -- low level --------------------------------------------------------
    def peek_boundary(self, j: int) -> bool:
        """True if position j (after a closer) ends a node reference."""
        k = j
        while k < len(self.s) and self.s[k] in " \t":
            k += 1
        if k >= len(self.s) or self.s[k] in "\n;&":
            return True
        rest = self.s[k:]
        if rest.startswith(":::") or _ARROW.match(rest) or _TEXT_EDGE.match(rest):
            return True
        m = _ID.match(rest)  # the next statement starts right here (a missing newline)
        if m:
            after = rest[m.end():].lstrip(" \t")
            return any(after.startswith(o) for o, _ in _SHAPES) or bool(_ARROW.match(after)) or after[:1] in ("", "\n", ";")
        return False

    def node_id(self) -> tuple[str, str | None] | None:
        """(id to write, label to give it if it has no shape) at the cursor."""
        m = _ID.match(self.s, self.i)
        if not m:
            return None
        self.i = m.end()
        raw = m.group()
        label = None
        if raw.lower() in _RESERVED:  # renamed; without a shape the node shows its old id, not the new one
            self.fixes.add("keyword id")
            if raw not in self.renamed:
                self.renamed[raw] = f"{raw}_n"
                label = raw
            new = self.renamed[raw]
        elif not _SAFE_ID.fullmatch(raw):  # e.g. C₋₁: Mermaid's lexer rejects the subscript
            self.fixes.add("id with symbols")
            if raw not in self.renamed:
                self.renamed[raw] = f"id{len(self.renamed) + 1}"
                label = raw
            new = self.renamed[raw]
        else:
            new = raw
        return new, label

    def shape(self) -> str | None:
        """'<open>"label"<close>' at the cursor, or None if there is no shape."""
        for opener, closers in _SHAPES:
            if not self.s.startswith(opener, self.i):
                continue
            start = self.i + len(opener)
            body = self.s[start:]
            stripped = body.lstrip(" \t")
            if stripped.startswith('"'):  # already quoted: keep up to the quote before the closer
                q0 = start + (len(body) - len(stripped))
                for closer in closers:
                    m = re.compile(r'"[ \t]*' + re.escape(closer)).search(self.s, q0 + 1)
                    if m and self.peek_boundary(m.end()):
                        self.i = m.end()
                        return f"{opener}{self.s[q0:m.start() + 1]}{closer}"
            cands = []
            for closer in closers:
                j = self.s.find(closer, start)
                while j != -1:
                    if "\n" in self.s[start:j]:
                        break
                    if self.peek_boundary(j + len(closer)):
                        cands.append((j, closer))
                    j = self.s.find(closer, j + 1)
            if not cands:
                continue
            cands.sort()
            j, closer = next(((j, c) for j, c in cands if _balanced(self.s[start:j])), cands[0])
            label = self.s[start:j]
            if any(ch in label for ch in "()[]{}|<>\"") or opener == ">":
                self.fixes.add("quoted label")
            self.i = j + len(closer)
            return f'{opener}"{label.strip().replace(chr(34), "#quot;")}"{closer}'
        return None

    def edge(self) -> str | None:
        m = _TEXT_EDGE.match(self.s, self.i)
        if m:
            self.i = m.end()
            self.fixes.add("edge text")
            text = m.group(3).strip().replace('"', "#quot;")
            return f" {m.group(1)}{m.group(4)}|\"{text}\"| "
        m = _ARROW.match(self.s, self.i)
        if not m:
            return None
        self.i = m.end()
        arrow = m.group(1)
        k = self.i
        while k < len(self.s) and self.s[k] in " \t":
            k += 1
        if k < len(self.s) and self.s[k] == "|":
            content_start = k + 1
            j = self.s.find("|", content_start)
            chosen = None
            while j != -1 and "\n" not in self.s[content_start:j]:
                if _balanced(self.s[content_start:j]):
                    chosen = j
                    break
                j = self.s.find("|", j + 1)
            if chosen is None:
                return None
            text = self.s[content_start:chosen].strip()
            if text.startswith('"') and text.endswith('"') and len(text) >= 2:
                text = text[1:-1]
            elif any(ch in text for ch in "()[]{}|<>"):
                self.fixes.add("quoted edge label")
            self.i = chosen + 1
            return f" {arrow}|\"{text.replace(chr(34), '#quot;')}\"| "
        return f" {arrow} "

    def skip_space(self) -> None:
        while self.i < len(self.s) and self.s[self.i] in " \t":
            self.i += 1

    # -- statements -------------------------------------------------------
    def node_ref(self) -> str | None:
        start = self.i
        got = self.node_id()
        if got is None:
            return None
        nid, label = got
        shape = self.shape()
        if shape is None and label is not None:
            shape = f'["{label.replace(chr(34), "#quot;")}"]'
        out = nid + (shape or "")
        if self.s.startswith(":::", self.i):
            m = re.compile(r":::[\w-]+").match(self.s, self.i)
            if m:
                out += m.group()
                self.i = m.end()
        if out == nid and self.i == start:
            return None
        return out

    def statement(self) -> str | None:
        parts = []
        ref = self.node_ref()
        if ref is None:
            return None
        parts.append(ref)
        while True:
            save = self.i
            self.skip_space()
            if self.s.startswith("&", self.i):
                self.i += 1
                self.skip_space()
                ref = self.node_ref()
                if ref is None:
                    self.i = save
                    break
                parts.append(f" & {ref}")
                continue
            self.i = save
            e = self.edge()
            if e is None:
                self.i = save
                break
            self.skip_space()
            ref = self.node_ref()
            if ref is None:
                return None
            parts.append(e)
            parts.append(ref)
        return "".join(parts).replace("  ", " ")


def _keyword_line(line: str, renamed: dict[str, str]) -> str:
    """classDef/class/style/click lines with renamed ids put in."""
    words = line.split(None, 2)
    if len(words) >= 2 and words[0].lower() in ("class", "style", "click"):
        ids = ",".join(renamed.get(x, x) for x in words[1].split(","))
        return "  " + " ".join([words[0], ids] + words[2:])
    return line


def repair(source: str) -> Repair | None:
    """A flowchart rewritten so it parses, or None if this is not a flowchart or it cannot be read."""
    m = _HEADER.match(source)
    if not m:
        return None
    header = " ".join(x for x in m.group(1, 2) if x)
    sc = _Scanner(source)
    sc.i = m.end()
    if source[m.end():].split("\n", 1)[0].strip():
        sc.fixes.add("statements on the header line")
    lines = [header]
    while sc.i < len(sc.s):
        while sc.i < len(sc.s) and sc.s[sc.i] in " \t\n;":
            sc.i += 1
        if sc.i >= len(sc.s):
            break
        rest_of_line = sc.s[sc.i:].split("\n", 1)[0]
        word = rest_of_line.split(None, 1)[0] if rest_of_line.strip() else ""
        low = word.lower()
        if rest_of_line.startswith("%%"):
            lines.append("  " + rest_of_line)
            sc.i += len(rest_of_line)
            continue
        after_word = rest_of_line[len(word):].lstrip(" \t")
        is_node = any(after_word.startswith(o) for o, _ in _SHAPES) or bool(_ARROW.match(after_word)) \
            or word[len(word.split("[")[0]):].startswith(("[", "(", "{"))
        if low == "subgraph":
            title = rest_of_line[len(word):].strip()
            mm = re.match(r"([^\s\[\]\"]+)\s*\[(.*)\]\s*$", title)
            if mm and not mm.group(2).startswith('"'):
                title = f'{mm.group(1)}["{mm.group(2).replace(chr(34), "#quot;")}"]'
                sc.fixes.add("quoted label")
            lines.append(f"  subgraph {title}")
            sc.i += len(rest_of_line)
            continue
        if low == "end" and not is_node:
            lines.append("  end")
            sc.i += len(word)
            continue
        if low in _KEYWORD_LINES and not is_node:
            stmt = rest_of_line.split(";")[0] if low not in ("style", "classdef", "linkstyle") else rest_of_line
            lines.append("  " + stmt.strip())
            sc.i += len(stmt)
            continue
        start = sc.i
        stmt = sc.statement()
        if stmt is None or sc.i == start:
            return None
        lines.append("  " + stmt.strip())
        if sc.i < len(sc.s) and sc.s[sc.i] not in " \t\n;" and not sc.s[sc.i - 1].isspace():
            sc.fixes.add("missing line break")
        nxt = sc.s[sc.i:].split("\n", 1)[0]
        if nxt.strip() and not nxt.strip().startswith((";", "%%")):
            sc.fixes.add("missing line break")
    lines = [lines[0]] + [_keyword_line(x, sc.renamed) if x.strip().split(None, 1)[0].lower() in
                          ("class", "style", "click") else x for x in lines[1:]]
    text = "\n".join(lines)
    return Repair(text, tuple(sorted(sc.fixes)))
