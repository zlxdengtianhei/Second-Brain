// @vitest-environment happy-dom
// Drives the real review modal (flip and Q&A) and checks the exact events written to the log.
// The plugin's own language detection, section names, card loading and templates run against an in-memory vault:
// a Chinese vault made before the language record (detected from its map pages), and an English vault.
import "./obsidian-dom";
import { TFile } from "obsidian";
import { afterEach, describe, expect, it } from "vitest";
import { type Lang, locale, setLocale } from "../src/i18n";
import type { ChatMessage, LlmClient, Readiness } from "../src/llm/ai";
import SecondBrainReviewPlugin from "../src/main";
import { parseStamp, questionId } from "../src/registry";
import { ReviewService } from "../src/service";
import { LANGUAGE_DEFAULTS, mergeSettings } from "../src/settings";
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

const cardText = (id: string, lang: Lang) => {
	const h = LANGUAGE_DEFAULTS[lang];
	return `---\nid: ${id}\n---\n## ${h.essenceSection}\nEssence of ${id}.\n\n## ${h.propositionsSection}\n- P1. Claim of ${id}.\n\n## ${h.reviewSection}\nFirst question of ${id}?::First answer.\nSecond question of ${id}?::Second answer.\n`;
};

afterEach(() => {
	setLocale("zh");
	window.localStorage.clear();
});

/** A vault in `lang` with no saved plugin settings; Obsidian itself runs in the other language. */
function setup(mode: "flip" | "qa", llmReply = "", lang: Lang = "zh") {
	const a = new MemoryAdapter();
	for (const [n, c] of Object.entries<any>(Q.files)) a.files.set(`_data/review/${n}`, fileText(c));
	if (lang === "zh") {
		a.files.set("_data/secondbrain/catalog.json", JSON.stringify({ schema: "secondbrain-catalog/1", tracks: [], domains: {} }));
		a.files.set("20_MOCs/算法.md", "# 算法\n\n## 分页索引（全部可达）\n");
	} else {
		a.files.set("_data/secondbrain/catalog.json", JSON.stringify({ schema: "secondbrain-catalog/1", tracks: [], domains: {}, language: "en" }));
	}
	window.localStorage.setItem("language", lang === "zh" ? "en" : "zh");
	const cards: IndexedCard[] = Q.cards.map((c: any) => ({
		id: c.id, title: c.title, domain: c.domain, kind: c.kind ?? "", status: c.status ?? "", path: `${c.id}.md`,
		pillar: "", topic: "", relationList: [], relations: (c.relations ?? []).map((r: string[]) => [r[0], r[1]]),
	}));
	const service = new ReviewService({ all: () => cards, get: (id) => cards.find((c) => c.id === id), version: 1 },
		new EventStore(a, () => "_data/review", "obs-ui", () => "jsonl"),
		{ newPerDay: () => 10, studyDomains: () => [], clock: () => parseStamp(Q.now) });
	for (const c of cards) a.files.set(c.path, cardText(c.id, lang));
	const plugin: SecondBrainReviewPlugin = Object.assign(Object.create(SecondBrainReviewPlugin.prototype), {
		app: {
			vault: {
				adapter: a,
				getAbstractFileByPath: (p: string) => (a.files.has(p) ? Object.assign(new TFile(), { path: p }) : null),
				cachedRead: async (f: { path: string }) => a.read(f.path),
			},
			workspace: { openLinkText: async () => undefined },
		},
		service,
		settings: mergeSettings(undefined),
		llm: new FakeLlm(llmReply),
		explainCard: async () => undefined,
	});
	return { a, plugin, service, open: async () => {
		await plugin.detectLanguage();
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
		expect(Array.from(root.querySelectorAll(".sbr-grade div")).map((d) => d.textContent)).toContain("评语：Half right.");
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

describe("a Chinese vault made before the language record", () => {
	it("is detected from its map pages; the interface, headings, calendar file and templates are the Chinese ones", async () => {
		const { plugin, open } = setup("flip");
		const modal = await open();
		expect([plugin.vaultLanguage, locale()]).toEqual(["zh", "zh"]);
		expect(plugin.sections()).toEqual({ review: "复习卡", essence: "本质", propositions: "命题" });
		expect(plugin.icsFile()).toBe("90_Meta/复习提醒.ics");
		expect((await plugin.gradeTemplate()).system).toContain("你是复习考官");
		expect(modal.titleEl.textContent).toBe("翻卡复习");
	});
});

describe("an English vault (language recorded in the catalog), Obsidian in Chinese", () => {
	const grade = JSON.stringify({ verdict: "partial", covered: ["a"], missing: ["the key step"], wrong: [], suggested_rating: 2, feedback: "Half right." });
	it("flip review in English writes the same events", async () => {
		const { a, plugin, open } = setup("flip", "", "en");
		const modal = await open();
		expect([plugin.vaultLanguage, locale()]).toEqual(["en", "en"]);
		expect(plugin.sections()).toEqual({ review: "Review cards", essence: "Essence", propositions: "Propositions" });
		expect(plugin.icsFile()).toBe("90_Meta/review-reminder.ics");
		const root = modal.contentEl;
		expect(modal.titleEl.textContent).toBe("Flip review");
		await until(() => root.textContent?.includes("First question of ex_array?"), "first due card");
		expect(root.querySelector(".sbr-session-meta")!.textContent).toMatch(/^Card 1 of \d+Due$/);
		(await until(() => button(root, "Show answer"), "show answer")).click();
		(await until(() => button(root, "3 Good"), "rating buttons")).click();
		const [ev1] = await until(() => written(a).length === 1 && written(a), "first event");
		expect(ev1).toMatchObject({ card: "ex_array", mode: "flip", rating: 3 });
		await until(() => root.textContent?.includes("First question of ex_tcp?"), "second due card");
		const keys = modal.scope as unknown as { press(key: string): void };
		keys.press(" ");
		await until(() => button(root, "2 Hard"), "rating buttons after space");
		keys.press("2");
		const evs = await until(() => written(a).length === 2 && written(a), "second event");
		expect(evs[1]).toMatchObject({ card: "ex_tcp", mode: "flip", rating: 2 });
	});
	it("Q&A review in English uses the English template and writes the same event", async () => {
		const { a, plugin, open } = setup("qa", grade, "en");
		const llm = plugin.llm as unknown as FakeLlm;
		const calls: ChatMessage[][] = [];
		llm.complete = async (m: ChatMessage[]) => (calls.push(m), grade);
		const modal = await open();
		const root = modal.contentEl;
		expect(modal.titleEl.textContent).toBe("Q&A review");
		const input = await until(() => root.querySelector("textarea") as HTMLTextAreaElement | null, "answer box");
		expect(input.getAttribute("placeholder")).toBe("Type your answer here (system dictation works).");
		input.value = "my answer";
		button(root, "Submit and grade")!.click();
		const suggested = await until(() => root.querySelector("button.sbr-suggested"), "graded result");
		expect(suggested.textContent).toContain("2 Hard");
		expect(Array.from(root.querySelectorAll(".sbr-grade div")).map((d) => d.textContent)).toEqual(
			expect.arrayContaining(["Feedback: Half right.", "Covered", "Missing", "Suggested: Hard"]),
		);
		const user = calls[0].find((m) => m.role === "user")!.content;
		expect(user).toContain("Question: First question of ex_array?");
		expect(user).toContain("Card propositions:\n- P1. Claim of ex_array.");
		button(root, "3 Good")!.click();
		(await until(() => root.textContent?.includes("Second question of ex_array?") && button(root, "I don't know, show the answer"), "second question")).click();
		(await until(() => button(root, "1 Again"), "self rating")).click();
		await until(() => button(root, "Next card"), "card summary");
		expect(root.textContent).toContain("Card rating: Again (the lowest of its questions). Next review in");
		const [ev] = written(a);
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
	});
	it("an interface language set in the settings wins; the card headings still follow the vault", async () => {
		const { plugin, open } = setup("flip", "", "en");
		plugin.settings.language = "zh";
		const modal = await open();
		expect([plugin.vaultLanguage, locale()]).toEqual(["en", "zh"]);
		expect(plugin.sections().review).toBe("Review cards");
		expect(modal.titleEl.textContent).toBe("翻卡复习");
		await until(() => modal.contentEl.textContent?.includes("First question of ex_array?"), "questions read under the English heading");
	});
});

describe("a vault with no recorded language and no maps", () => {
	it("follows Obsidian's language", async () => {
		for (const [code, lang] of [["zh", "zh"], ["de", "en"], [null, "en"]] as const) {
			const { a, plugin } = setup("flip", "", "en");
			a.files.delete("_data/secondbrain/catalog.json");
			window.localStorage.clear();
			if (code) window.localStorage.setItem("language", code);
			await plugin.detectLanguage();
			expect([plugin.vaultLanguage, locale(), plugin.sections().review], String(code)).toEqual([null, lang, LANGUAGE_DEFAULTS[lang].reviewSection]);
		}
	});
});
