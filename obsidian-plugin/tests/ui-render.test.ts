// @vitest-environment happy-dom
// Rendering checks that need a DOM: the dashboard in a note, and the explanation context.
import "./obsidian-dom";
import { afterEach, describe, expect, it } from "vitest";
import { type Lang, setLocale } from "../src/i18n";
import type { ChatMessage, LlmClient } from "../src/llm/ai";
import { DEFAULT_TEMPLATES, parseTemplate } from "../src/llm/prompts";
import { type Counts, emptyCounts, parseStamp } from "../src/registry";
import { ReviewService } from "../src/service";
import { DEFAULT_SETTINGS, type ReviewSettings, sectionNames } from "../src/settings";
import { dashboardModel, renderDashboard } from "../src/ui/dashboard";
import { ExplainView } from "../src/ui/explain-view";
import type { IndexedCard } from "../src/vault/card-index";
import { EventStore } from "../src/vault/event-store";
import { TFile } from "obsidian";
import { MemoryAdapter } from "./memory-adapter";

const actions = { flip() {}, qa() {}, explain() {}, refresh() {}, knowledgeFolder: "10_Knowledge" };

afterEach(() => setLocale("zh"));

function renderSample(): HTMLElement {
	const domains = new Map<string, Counts>([["algo", { ...emptyCounts(), total: 2, unlearned: 2 }]]);
	const totals = { ...emptyCounts(), total: 2, unlearned: 2, due: 3, dueNotReviewedToday: 2, reviewedToday: 1 };
	const summary = { today: "2026-03-10", todayIndex: 0, totals, domains, newToday: 1, reviewsToday: 1, streak: 4, orphans: ["x"], issues: 2 };
	const queue = { today: "2026-03-10", due: [{ id: "a" }, { id: "b" }, { id: "c" }], new: [], newQuota: 10, newToday: 1, newAvailable: 0, cycleBreaks: [] };
	const el = document.createElement("div");
	renderDashboard(el, dashboardModel(summary, queue, { loaded: true, newPerDay: 10, studyDomains: ["algo"], titles: new Map([["algo", "Algorithms"]]) }), actions);
	return el;
}

const texts = (el: HTMLElement, sel: string) => Array.from(el.querySelectorAll(sel)).map((x) => x.textContent);

describe("dashboard text in both languages", () => {
	it("Chinese is the same as before", () => {
		const el = renderSample();
		expect(texts(el, ".sbr-stat")).toEqual(["3 张到期", "2 张今日未复习", "1 / 10新卡", "1 张今日已复习", "4 天连续天数"]);
		expect(texts(el, ".sbr-buttons button")).toEqual(["开始复习（3）", "问答复习", "讲解当前卡"]);
		expect(texts(el, "th")).toEqual(["领域", "未学", "学习中", "复习期", "已掌握", "今日到期", ""]);
		expect(texts(el, "tbody td:first-child")).toEqual(["Algorithms在学", "合计"]);
		expect(el.textContent).toContain("日志里有 2 行无法解析，已跳过（详见开发者控制台）。");
	});
	it("English", () => {
		setLocale("en");
		const el = renderSample();
		expect(texts(el, ".sbr-title")).toEqual(["Today's review · 2026-03-10"]);
		expect(texts(el, ".sbr-stat")).toEqual(["3Due", "2Not reviewed today", "1 / 10New cards", "1Reviewed today", "4Day streak"]);
		expect(texts(el, ".sbr-buttons button")).toEqual(["Start review (3)", "Q&A review", "Explain this card"]);
		expect(texts(el, "th")).toEqual(["Domain", "Unlearned", "Learning", "Review", "Mastered", "Due today", ""]);
		expect(texts(el, "tbody td:first-child")).toEqual(["Algorithmsstudying", "Total"]);
		expect(el.textContent).toContain("Log lines that cannot be read and were skipped: 2.");
		expect(el.textContent).toContain("Cards with review events that are not in the card index: 1.");
		expect(el.textContent).not.toMatch(/[\u3000-\u9fff\uff00-\uffef]/);
	});
});

describe("dashboard in a note (compact)", () => {
	it("shows every domain row in a scrolling table, and the not-reviewed count", () => {
		const domains = new Map<string, Counts>();
		for (let i = 0; i < 25; i++) domains.set(`d${String(i).padStart(2, "0")}`, { ...emptyCounts(), total: 10, unlearned: 10 });
		const totals = { ...emptyCounts(), total: 250, unlearned: 247, learning: 3, learned: 3, due: 3, dueNotReviewedToday: 2, reviewedToday: 1 };
		const summary = { today: "2026-03-10", todayIndex: 0, totals, domains, newToday: 0, reviewsToday: 1, streak: 1, orphans: [], issues: 0 };
		const queue = { today: "2026-03-10", due: [{ id: "a" }, { id: "b" }, { id: "c" }], new: [], newQuota: 10, newToday: 0, newAvailable: 0, cycleBreaks: [] };
		const model = dashboardModel(summary, queue, { loaded: true, newPerDay: 10, studyDomains: [], titles: new Map() });
		const el = document.createElement("div");
		renderDashboard(el, model, actions, true);
		const wrap = el.querySelector(".sbr-domains-wrap.sbr-compact");
		expect(wrap).not.toBeNull();
		expect(wrap!.querySelectorAll("tbody tr").length).toBe(26); // 25 domains + total
		const stats = Array.from(el.querySelectorAll(".sbr-stat")).map((s) => s.textContent);
		expect(stats).toContain("2 张今日未复习");
	});
});

class RecordingLlm implements LlmClient {
	calls: ChatMessage[][] = [];
	async ready() {
		return { ok: true as const, label: "Fake" };
	}
	async complete(messages: ChatMessage[]) {
		this.calls.push(messages);
		return "explained";
	}
}

async function explainWith(settings: ReviewSettings, vaultLang: Lang, texts: Record<string, string>) {
	const card = (id: string, extra: Partial<IndexedCard> = {}): IndexedCard => ({
		id, title: `Title ${id}`, domain: "algo", kind: "concept", status: "", path: `${id}.md`, pillar: "", topic: "",
		relationList: [], relations: [], ...extra,
	});
	const main = card("ex_main", { relationList: [{ type: "depends_on", target: "ex_rel" }], relations: [["depends_on", "ex_rel"]] });
	const rel = card("ex_rel");
	const files = new Map(Object.keys(texts).map((p) => [p, Object.assign(new TFile(), { path: p })]));
	const cards = [main, rel];
	const service = new ReviewService({ all: () => cards, get: (id) => cards.find((c) => c.id === id), version: 1 },
		new EventStore(new MemoryAdapter(), () => "_data/review", "obs-x", () => "jsonl"),
		{ newPerDay: () => 10, studyDomains: () => [], clock: () => parseStamp("2026-03-10T12:00:00.000+01:00") });
	await service.reload();
	const llm = new RecordingLlm();
	const plugin = {
		settings,
		sections: () => sectionNames(settings, vaultLang),
		service,
		index: { get: (id: string) => cards.find((c) => c.id === id) },
		app: { vault: { getAbstractFileByPath: (p: string) => files.get(p) ?? null, cachedRead: async (f: { path: string }) => texts[f.path] } },
		llm,
		loadCard: async (c: IndexedCard) => ({ card: c, text: texts[c.path], items: [], essence: "", propositions: "" }),
	};
	const view = new ExplainView({} as any, plugin as any);
	(view as any).app = plugin.app;
	return { view, main, llm, plugin };
}

describe("explain view", () => {
	it("in an English vault reads ## Essence by default and sends English context with the English template", async () => {
		setLocale("en");
		const { view, main, llm, plugin } = await explainWith({ ...DEFAULT_SETTINGS }, "en", {
			"ex_main.md": "---\nid: ex_main\n---\n## Essence\nMain essence.\n",
			"ex_rel.md": "---\nid: ex_rel\n---\n## Essence\nRelated essence.\n",
		});
		(plugin as any).explainTemplate = async () => parseTemplate(DEFAULT_TEMPLATES.en.explain);
		await view.onOpen();
		expect(texts(view.contentEl, "button")).toEqual(["Send", "Explain again", "Stop"]);
		await view.explain(main);
		expect(view.getDisplayText()).toBe("Explain: Title ex_main");
		expect(view.contentEl.querySelector(".sbr-muted")!.textContent).toMatch(/^Title ex_main · Context sent: \d+ characters, related cards: 1$/);
		const user = llm.calls[0].find((m) => m.role === "user")!.content;
		expect(user).toContain("Title: Title ex_main\nKind: concept\nDomain: algo");
		expect(user).toContain("- Title ex_rel | depends_on | Related essence.");
		expect(user).toContain("Not reviewed yet.");
		expect(user).not.toMatch(/[\u3000-\u9fff\uff00-\uffef]/);
	});
	it("reads related cards' essence under the configured section name", async () => {
		const card = (id: string, extra: Partial<IndexedCard> = {}): IndexedCard => ({
			id, title: `Title ${id}`, domain: "algo", kind: "concept", status: "", path: `${id}.md`, pillar: "", topic: "",
			relationList: [], relations: [], ...extra,
		});
		const main = card("ex_main", { relationList: [{ type: "depends_on", target: "ex_rel" }], relations: [["depends_on", "ex_rel"]] });
		const rel = card("ex_rel");
		const texts: Record<string, string> = {
			"ex_main.md": "---\nid: ex_main\n---\n## Essence\nMain essence.\n\n## Propositions\n- P1. Claim.\n",
			"ex_rel.md": "---\nid: ex_rel\n---\n## 本质\nWrong section.\n\n## Essence\nRelated essence under a custom heading.\n",
		};
		const files = new Map(Object.keys(texts).map((p) => [p, Object.assign(new TFile(), { path: p })]));
		const cards = [main, rel];
		const service = new ReviewService({ all: () => cards, get: (id) => cards.find((c) => c.id === id), version: 1 },
			new EventStore(new MemoryAdapter(), () => "_data/review", "obs-x", () => "jsonl"),
			{ newPerDay: () => 10, studyDomains: () => [], clock: () => parseStamp("2026-03-10T12:00:00.000+01:00") });
		await service.reload();
		const llm = new RecordingLlm();
		const plugin = {
			settings: { ...DEFAULT_SETTINGS, essenceSection: "Essence", propositionsSection: "Propositions" },
			// A Chinese vault whose essence heading is set in the settings: the setting wins.
			sections: () => sectionNames({ ...DEFAULT_SETTINGS, essenceSection: "Essence", propositionsSection: "Propositions" }, "zh"),
			service,
			index: { get: (id: string) => cards.find((c) => c.id === id) },
			app: { vault: { getAbstractFileByPath: (p: string) => files.get(p) ?? null, cachedRead: async (f: { path: string }) => texts[f.path] } },
			llm,
			loadCard: async (c: IndexedCard) => ({ card: c, text: texts[c.path], items: [], essence: "", propositions: "" }),
			explainTemplate: async () => parseTemplate(DEFAULT_TEMPLATES.zh.explain),
		};
		const view = new ExplainView({} as any, plugin as any);
		(view as any).app = plugin.app;
		await view.onOpen();
		await view.explain(main);
		const user = llm.calls[0].find((m) => m.role === "user")!.content;
		expect(user).toContain("- Title ex_rel｜depends_on｜Related essence under a custom heading.");
		expect(user).not.toContain("Wrong section.");
	});
});
