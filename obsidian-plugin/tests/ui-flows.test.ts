// @vitest-environment happy-dom
// Drives the real review modal (flip and Q&A) and checks the exact events written to the log.
import "./obsidian-dom";
import { describe, expect, it } from "vitest";
import type { ChatMessage, LlmClient, Readiness } from "../src/llm/ai";
import { DEFAULT_GRADE_TEMPLATE, parseTemplate } from "../src/llm/prompts";
import { extractReviewItems, parseStamp, questionId, sectionText } from "../src/registry";
import { ReviewService } from "../src/service";
import { DEFAULT_SETTINGS } from "../src/settings";
import { ReviewModal } from "../src/ui/review-modal";
import type { IndexedCard } from "../src/vault/card-index";
import { EventStore } from "../src/vault/event-store";
import { fileText, fixture } from "./fixtures";
import { MemoryAdapter } from "./memory-adapter";

const Q = fixture("queue.json");
const LOG = "_data/review/events-obs-ui.jsonl";

class FakeLlm implements LlmClient {
	constructor(private reply: string) {}
	async ready(): Promise<Readiness> {
		return { ok: true, label: "Fake · m1" };
	}
	async complete(_m: ChatMessage[]) {
		return this.reply;
	}
}

const cardText = (id: string) => `---\nid: ${id}\n---\n## 本质\nEssence of ${id}.\n\n## 命题\n- P1. Claim of ${id}.\n\n## 复习卡\nFirst question of ${id}?::First answer.\nSecond question of ${id}?::Second answer.\n`;

function setup(mode: "flip" | "qa", llmReply = "") {
	const a = new MemoryAdapter();
	for (const [n, c] of Object.entries<any>(Q.files)) a.files.set(`_data/review/${n}`, fileText(c));
	const cards: IndexedCard[] = Q.cards.map((c: any) => ({
		id: c.id, title: c.title, domain: c.domain, kind: c.kind ?? "", status: c.status ?? "", path: `${c.id}.md`,
		pillar: "", topic: "", relationList: [], relations: (c.relations ?? []).map((r: string[]) => [r[0], r[1]]),
	}));
	const service = new ReviewService({ all: () => cards, get: (id) => cards.find((c) => c.id === id), version: 1 },
		new EventStore(a, () => "_data/review", "obs-ui", () => "jsonl"),
		{ newPerDay: () => 10, studyDomains: () => [], clock: () => parseStamp(Q.now) });
	const plugin = {
		app: { workspace: { openLinkText: async () => undefined } },
		service,
		settings: { ...DEFAULT_SETTINGS },
		llm: new FakeLlm(llmReply),
		gradeTemplate: async () => parseTemplate(DEFAULT_GRADE_TEMPLATE),
		explainCard: async () => undefined,
		loadCard: async (card: IndexedCard) => {
			const text = cardText(card.id);
			return { card, text, items: extractReviewItems(text), essence: sectionText(text, "本质"), propositions: sectionText(text, "命题") };
		},
	};
	return { a, plugin, service, open: async () => {
		await service.reload();
		const modal = new ReviewModal(plugin as any, mode);
		modal.open();
		return modal;
	} };
}

async function until<T>(fn: () => T | null | undefined | false, what: string, ms = 3000): Promise<T> {
	const end = Date.now() + ms;
	for (;;) {
		const v = fn();
		if (v) return v;
		if (Date.now() > end) throw new Error(`timed out waiting for ${what}`);
		await new Promise((r) => setTimeout(r, 5));
	}
}

const button = (root: HTMLElement, prefix: string) =>
	Array.from(root.querySelectorAll("button")).find((b) => (b.textContent ?? "").startsWith(prefix)) as HTMLButtonElement | undefined;

function written(a: MemoryAdapter): Record<string, unknown>[] {
	return (a.files.get(LOG) ?? "").trim().split("\n").filter(Boolean).map((l) => JSON.parse(l));
}

describe("flip review writes exactly one event per rating", () => {
	it("button and keyboard ratings", async () => {
		const { a, open } = setup("flip");
		const modal = await open();
		const root = modal.contentEl;
		await until(() => root.textContent?.includes("First question of ex_array?"), "first due card");
		(await until(() => button(root, "显示答案"), "show answer")).click();
		(await until(() => button(root, "3 记得"), "rating buttons")).click();
		const [ev1] = await until(() => written(a).length === 1 && written(a), "first event");
		expect(Object.keys(ev1).sort()).toEqual(["card", "device", "mode", "ms", "rating", "ts", "v"]);
		expect(ev1).toMatchObject({ v: 1, device: "obs-ui", card: "ex_array", mode: "flip", rating: 3 });
		expect(typeof ev1.ms).toBe("number");

		await until(() => root.textContent?.includes("First question of ex_tcp?"), "second due card");
		const keys = modal.scope as unknown as { press(key: string): void }; // test helper of the Obsidian stub
		keys.press(" ");
		await until(() => button(root, "2 困难"), "rating buttons after space");
		keys.press("2");
		const evs = await until(() => written(a).length === 2 && written(a), "second event");
		expect(evs[1]).toMatchObject({ card: "ex_tcp", mode: "flip", rating: 2 });
	});
});

describe("Q&A review writes one event per card with every question", () => {
	const grade = JSON.stringify({ verdict: "partial", covered: ["a"], missing: ["the key step"], wrong: [], suggested_rating: 2, feedback: "Half right." });
	it("model-graded answer, changed rating, and a don't-know answer", async () => {
		const { a, open } = setup("qa", grade);
		const modal = await open();
		const root = modal.contentEl;
		const input = await until(() => root.querySelector("textarea") as HTMLTextAreaElement | null, "answer box");
		input.value = "my answer";
		button(root, "提交并评分")!.click();
		const suggested = await until(() => root.querySelector("button.sbr-suggested"), "graded result");
		expect(suggested.textContent).toContain("2 困难");
		expect(root.textContent).toContain("Half right.");
		button(root, "3 记得")!.click(); // the learner overrides the suggestion
		(await until(() => root.textContent?.includes("Second question of ex_array?") && button(root, "不会，看答案"), "second question")).click();
		(await until(() => button(root, "1 重来"), "self rating")).click();
		await until(() => button(root, "下一张"), "card summary");
		const [ev] = written(a);
		expect(Object.keys(ev).sort()).toEqual(["card", "device", "grader", "items", "mode", "ms", "rating", "ts", "v"]);
		expect(ev).toMatchObject({
			card: "ex_array",
			mode: "qa",
			rating: 1,
			grader: "llm:Fake · m1",
			items: [
				{ q: questionId("First question of ex_array?"), rating: 3, answer: "my answer", verdict: "partial", grader: "llm:Fake · m1", feedback: "Half right.", missing: ["the key step"] },
				{ q: questionId("Second question of ex_array?"), rating: 1, grader: "self", verdict: "miss" },
			],
		});
		expect((ev.items as unknown[]).length).toBe(2);
	});
	it("a reply that breaks the JSON contract is not graded; the learner's rating is written", async () => {
		const { a, open } = setup("qa", "Looks fine to me!");
		const modal = await open();
		const root = modal.contentEl;
		for (const answer of ["first try", "second try"]) {
			const input = await until(() => root.querySelector("textarea") as HTMLTextAreaElement | null, "answer box");
			input.value = answer;
			button(root, "提交并评分")!.click();
			await until(() => root.textContent?.includes("未评分"), "ungraded notice");
			expect(root.querySelector("button.sbr-suggested")).toBeNull();
			button(root, "4 简单")!.click();
		}
		await until(() => button(root, "下一张"), "card summary");
		const [ev] = written(a);
		expect(ev).toMatchObject({
			card: "ex_array",
			mode: "qa",
			rating: 4,
			grader: "self",
			items: [
				{ q: questionId("First question of ex_array?"), rating: 4, answer: "first try", grader: "self" },
				{ q: questionId("Second question of ex_array?"), rating: 4, answer: "second try", grader: "self" },
			],
		});
		expect((ev.items as Record<string, unknown>[]).every((i) => !("verdict" in i))).toBe(true);
	});
});
