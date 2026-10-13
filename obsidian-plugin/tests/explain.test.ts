import { describe, expect, it } from "vitest";
import { cardBody, explainVars, learnerState, selectRelations, siblingTitles, truncate } from "../src/llm/explain-context";
import { DEFAULT_EXPLAIN_TEMPLATE, parseTemplate, renderMessages } from "../src/llm/prompts";
import { parseEvent } from "../src/registry";
import { cardFromFrontmatter, type IndexedCard } from "../src/vault/card-index";
import { buildTaxonomy } from "../src/vault/taxonomy";
import { fixtureText } from "./fixtures";

const rel = (type: string, target: string, tier?: string) => (tier ? { type, target, tier } : { type, target });

describe("selectRelations", () => {
	it("puts prerequisite types first, keeps card order otherwise, drops duplicate targets", () => {
		const picked = selectRelations([rel("similar_to", "a"), rel("depends_on", "b"), rel("contrasts_with", "c"), rel("follows", "d"), rel("similar_to", "b")], 8);
		expect(picked.map((r) => r.target)).toEqual(["b", "d", "a", "c"]);
	});
	it("uses extension-tier relations only to fill up to three, and caps the total", () => {
		const few = selectRelations([rel("depends_on", "a"), rel("similar_to", "x", "extension"), rel("similar_to", "y", "extension"), rel("similar_to", "z", "extension")], 8);
		expect(few.map((r) => r.target)).toEqual(["a", "x", "y"]);
		const many = selectRelations(Array.from({ length: 12 }, (_, i) => rel("similar_to", `c${i}`)), 8);
		expect(many).toHaveLength(8);
		const enough = selectRelations([rel("depends_on", "a"), rel("follows", "b"), rel("similar_to", "c"), rel("similar_to", "x", "extension")], 8);
		expect(enough.map((r) => r.target)).toEqual(["a", "b", "c"]);
	});
});

describe("explain context", () => {
	const text = fixtureText("cards/binary-search.md");
	const fm = { id: "ex_binary_search", title: "Binary search", kind: "mechanism", domain: "algo", pillar: "Searching", topic: "Divide and conquer search", relations: [{ type: "depends_on", target: "ex_array" }] };
	const card = cardFromFrontmatter(fm, "10_Knowledge/algo/Binary search.md")!;
	const tax = buildTaxonomy([{ path: "Algorithms.yaml", data: { title: "Algorithms", pillars: [{ name: "Searching", why: "Find items in sublinear time.", topics: ["Linear", "Divide and conquer search"] }] } }]);
	const limits = { maxRelated: 8, essenceChars: 20, siblings: 10, cardChars: 6000, answerChars: 600 };
	it("drops source hashes, keeps the body, and adds a header", () => {
		const body = cardBody(card, text, 6000);
		expect(body).not.toContain("sha256");
		expect(body).toContain("## 命题");
		expect(body.startsWith("标题：Binary search\n类型：mechanism")).toBe(true);
		expect(cardBody(card, text, 50)).toContain("…（已截断）");
	});
	it("fills every placeholder of the explain template", () => {
		const vars = explainVars({
			card, cardText: text, limits,
			related: [{ title: "Arrays", type: "depends_on", essence: "An array stores elements in contiguous memory with O(1) index access." }],
			pillar: tax.pillars.get("Searching"), domainTitle: "Algorithms", siblings: ["Ternary search"], learnerState: "还没有复习过这张卡。",
		});
		expect(vars.related_lines).toBe("- Arrays｜depends_on｜An array stores ele…");
		expect(vars.pillar_summary).toBe("Find items in sublinear time.");
		expect(vars.pillar_topics).toBe("Linear、Divide and conquer search");
		const user = renderMessages(parseTemplate(DEFAULT_EXPLAIN_TEMPLATE), vars).find((m) => m.role === "user")!.content;
		expect(user).not.toMatch(/\{\{\w+\}\}/);
		expect(user).toContain("控制在 600 字以内");
		expect(user.length).toBeLessThan(3000);
	});
	it("lists same-topic siblings of the same pillar", () => {
		const mk = (id: string, topic: string, pillar = "Searching") => ({ ...card, id, title: id, topic, pillar }) as IndexedCard;
		expect(siblingTitles(card, [card, mk("t1", card.topic), mk("t2", "Linear"), mk("t3", card.topic, "Other"), mk("t4", card.topic)], 10)).toEqual(["t1", "t4"]);
	});
	it("summarizes the learner's last review and missed points", () => {
		const ev = (ts: string, rating: number, items?: unknown) => parseEvent({ v: 1, ts, device: "obs-a", card: "c", mode: items ? "qa" : "flip", rating, ...(items ? { items } : {}) });
		const labels = { none: "none", summary: (n: number, d: string, r: number) => `${n}|${d}|${r}`, missing: (s: string) => `missing ${s}` };
		expect(learnerState([], 0, labels)).toBe("none");
		const history = [ev("2026-03-05T08:00:00.000+01:00", 3), ev("2026-03-04T08:00:00.000+01:00", 2, [{ q: "h:1", rating: 2, missing: ["loop invariant"] }])];
		expect(learnerState(history, 2, labels)).toBe("2|2026-03-05|3\nmissing loop invariant");
	});
	it("truncates on characters, not bytes", () => {
		expect(truncate("二分查找每一步把区间减半", 5)).toBe("二分查找…");
	});
});
