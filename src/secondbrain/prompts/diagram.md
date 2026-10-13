# Card diagram: draw or skip, then describe the diagram as IR

You receive finished cards: an essence `E` and propositions `P1..Pn`. For each card, decide whether a diagram shows structure that the text does not. If so, describe it as a small JSON object (the IR). Do not write Mermaid: code generates it and checks every rule below.

## 1. Draw or skip

Draw only when the propositions contain one of these structures:

| Form | Draw when the propositions have | How |
|---|---|---|
| `flow` | a process or algorithm with a branch, a loop or a merge ("if ... else", "until", "each round", "in parallel, then combine") | decision nodes have role `decision`; every edge leaving one states its condition |
| `sequence` | 2 or more parties and 3 or more messages between them | participants are nodes; interactions are `messages` |
| `state` | one object with 3 or more named states and the events that move it | edges are transitions labelled with the event |
| `structure` | a whole with 3 or more parts that act on or depend on each other | `groups` for containment; labelled edges for what one part does to another |

Skip (`"decision": "skip"`, with a `reason` naming the rule) when the card is:

- a contrast: the comparison belongs in a 2-4 row table in the text;
- a single fact, definition or number, or parts with no relations between them: a list is clearer;
- a derivation or formula: an aligned formula is clearer;
- vocabulary or language material;
- a linear process of 4 steps or fewer: "A → B → C" in the text says it;
- about relations to other cards: those belong in the card's relations.

When unsure, skip: a diagram that restates the text wastes the reader's time.

## 2. Rules for the IR

1. Every node, every labelled edge and every message lists in `p` the propositions it rests on (`P1`..`Pn`, or `E` for the essence).
2. Labels reuse the card's words in the card's language (Chinese card, Chinese labels; terms such as `ETag` stay as written). At least half of each label's words come from the cited propositions.
3. Add no fact the card does not state; if the diagram needs one, skip.
4. The proposition the card most wants remembered must be visible in the diagram.
5. One arrow meaning per diagram: control or data flow in `flow`, action or dependency in `structure`. Show containment only with `groups`.
6. Size: `flow` and `structure` 4-9 nodes, `state` 3-8 states, `sequence` 2-5 participants. If the card has 2 or more propositions, cite at least 2.
7. Short labels. Width limits in characters, Latin letters counting half: node 20, decision 12, edge 14, message 20. Leave detail in the text.
8. Edges in `structure` and `state` need labels. Never label an edge with a relation name such as `depends_on`, and do not add a node per related card. At most one self-loop per node.

Roles: `flow` uses `step`, `decision`, `data`, `start`, `end`; `structure` uses `step`, `data`, `actor`; `state` uses `state`, and `start` / `end` for the initial and final states; `sequence` uses `actor`. Message `style` is `sync` (default), `reply` or `async`; `"note": true` puts the text in a note over `from` (and `to`).

## 3. Output

Return JSON only, one result per input card, in input order:

```json
{"results": [{"ref": "<the card's ref>", "ir": {"decision": "draw", "form": "flow", "question": "the one question this diagram answers", "groups": [], "nodes": [], "edges": []}}]}
```

A `sequence` IR has `messages` instead of `edges`. A skip is `{"decision": "skip", "reason": "..."}`.

If an input card carries `previous` and `errors`, your earlier IR failed the checks: fix every error, or skip. There is one rewrite; after that the card gets no diagram.

## 4. Examples

Card `net/retry`: E 失败请求按指数退避重试。P1 发送请求；成功则返回结果，失败则等待时间翻倍后重试。P2 达到重试上限后放弃并报错。

```json
{"decision": "draw", "form": "flow", "question": "失败的请求何时重试、何时放弃？",
 "nodes": [{"key": "send", "label": "发送请求", "role": "start", "p": ["P1"]},
           {"key": "ok", "label": "请求成功？", "role": "decision", "p": ["P1"]},
           {"key": "done", "label": "返回结果", "role": "end", "p": ["P1"]},
           {"key": "cap", "label": "达到重试上限？", "role": "decision", "p": ["P2"]},
           {"key": "wait", "label": "等待时间翻倍", "p": ["P1"]},
           {"key": "fail", "label": "放弃并报错", "role": "end", "p": ["P2"]}],
 "edges": [{"from": "send", "to": "ok"},
           {"from": "ok", "to": "done", "label": "是", "p": ["P1"]},
           {"from": "ok", "to": "cap", "label": "否", "p": ["P1"]},
           {"from": "cap", "to": "fail", "label": "是", "p": ["P2"]},
           {"from": "cap", "to": "wait", "label": "否", "p": ["P2"]},
           {"from": "wait", "to": "send"}]}
```

Card `net/dns`: E A recursive resolver walks the DNS hierarchy for the client. P1 The client asks the recursive resolver for a name. P2 The resolver queries the root server, then the TLD server, then the authoritative server. P3 The resolver caches the answer and returns it to the client.

```json
{"decision": "draw", "form": "sequence", "question": "Who asks whom, in what order, to resolve a name?",
 "nodes": [{"key": "c", "label": "client", "p": ["P1"]}, {"key": "r", "label": "recursive resolver", "p": ["P1"]},
           {"key": "root", "label": "root server", "p": ["P2"]}, {"key": "tld", "label": "TLD server", "p": ["P2"]},
           {"key": "auth", "label": "authoritative server", "p": ["P2"]}],
 "messages": [{"from": "c", "to": "r", "text": "asks for a name", "p": ["P1"]},
              {"from": "r", "to": "root", "text": "queries the root server", "p": ["P2"]},
              {"from": "r", "to": "tld", "text": "then the TLD server", "p": ["P2"]},
              {"from": "r", "to": "auth", "text": "then the authoritative server", "p": ["P2"]},
              {"from": "r", "to": "c", "text": "caches and returns the answer", "style": "reply", "p": ["P3"]}]}
```

Card `net/tcp-vs-udp` (contrast): P1 TCP 建立连接并重传丢失的报文，UDP 不建立连接也不重传。

```json
{"decision": "skip", "reason": "contrast card: the two sides belong in a comparison table"}
```
