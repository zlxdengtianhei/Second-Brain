// @vitest-environment happy-dom
// Runs the real plugin onload and start against an in-memory vault, and checks the language wiring:
// command names, Obsidian's language, re-reading the vault's language, and what reads the card headings.
import "./obsidian-dom";
import { TFile } from "obsidian";
import { notices, setAppLanguage } from "./obsidian-stub"; // the same module as "obsidian" in tests (vitest alias)
import { afterEach, describe, expect, it } from "vitest";
import { type Lang, locale, setLocale } from "../src/i18n";
import SecondBrainReviewPlugin from "../src/main";
import { LANGUAGE_DEFAULTS } from "../src/settings";
import { ReviewModal } from "../src/ui/review-modal";
import { MemoryAdapter } from "./memory-adapter";

const CATALOG = "_data/secondbrain/catalog.json";

afterEach(() => {
	setLocale("zh");
	setAppLanguage(undefined);
	window.localStorage.clear();
	notices.length = 0;
});

type CardSpec = { id: string; headings: Lang; questions?: boolean };

function cardText(c: CardSpec): string {
	const h = LANGUAGE_DEFAULTS[c.headings];
	const qa = c.questions === false ? "" : `\n## ${h.reviewSection}\nQuestion of ${c.id}?::Answer of ${c.id}.\n`;
	return `---\nid: ${c.id}\ntitle: Title ${c.id}\ndomain: algo\n---\n## ${h.essenceSection}\nEssence of ${c.id}.\n\n## ${h.propositionsSection}\n- P1. Claim of ${c.id}.\n${qa}`;
}

/** A vault: catalog language ("none" = catalog without a language), optional map page, cards. */
function vault(opts: { catalog?: Lang | "none" | "missing"; map?: Lang; cards: CardSpec[] }): MemoryAdapter {
	const a = new MemoryAdapter();
	if (opts.catalog !== "missing") {
		const lang = opts.catalog && opts.catalog !== "none" ? { language: opts.catalog } : {};
		a.files.set(CATALOG, JSON.stringify({ schema: "secondbrain-catalog/1", tracks: [], domains: {}, ...lang }));
	}
	if (opts.map === "zh") a.files.set("20_MOCs/示例图谱甲.md", "# 示例图谱甲\n\n## 分页索引（全部可达）\n");
	if (opts.map === "en") a.files.set("20_MOCs/Example map.md", "# Example map\n\n## Page index (every page)\n");
	for (const c of opts.cards) a.files.set(`10_Knowledge/algo/${c.id}.md`, cardText(c));
	return a;
}

function fakeApp(a: MemoryAdapter) {
	const handlers = new Map<string, ((file: unknown) => void)[]>();
	const local = new Map<string, unknown>();
	const file = (path: string) => Object.assign(new TFile(), { path, extension: "md" });
	const frontmatter = (text: string) => {
		const m = /^---\n([\s\S]*?)\n---/.exec(text);
		return m ? Object.fromEntries(m[1].split("\n").map((l) => [l.slice(0, l.indexOf(":")), l.slice(l.indexOf(":") + 1).trim()])) : undefined;
	};
	const app = {
		vault: {
			adapter: a,
			getName: () => "Test vault",
			getMarkdownFiles: () => [...a.files.keys()].filter((p) => p.endsWith(".md")).map(file),
			getAbstractFileByPath: (p: string) => (a.files.has(p) ? file(p) : null),
			cachedRead: async (f: { path: string }) => a.read(f.path),
			on: (name: string, fn: (file: unknown) => void) => {
				handlers.set(name, [...(handlers.get(name) ?? []), fn]);
				return {};
			},
		},
		metadataCache: {
			getFileCache: (f: { path: string }) => ({ frontmatter: frontmatter(a.files.get(f.path) ?? "") }),
			on: () => ({}),
		},
		workspace: { onLayoutReady: () => undefined, openLinkText: async () => undefined },
		loadLocalStorage: (k: string) => local.get(k) ?? null,
		saveLocalStorage: (k: string, v: unknown) => void local.set(k, v),
	};
	return { app, fire: (name: string, path: string) => handlers.get(name)?.forEach((fn) => fn(file(path))) };
}

/** onload (as Obsidian calls it), then start (what onLayoutReady runs). */
async function load(a: MemoryAdapter, saved: unknown = null) {
	const { app, fire } = fakeApp(a);
	const plugin = new SecondBrainReviewPlugin(app as any, {} as any);
	(plugin as any).data = saved;
	await plugin.onload();
	await (plugin as any).start();
	return { plugin, fire, stub: plugin as unknown as { commands: { name: string }[]; ribbons: string[]; statusBar: HTMLElement[] } };
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

const cards = (headings: Lang, n = 3): CardSpec[] => Array.from({ length: n }, (_, i) => ({ id: `c${i + 1}`, headings }));

describe("onload", () => {
	it("names commands, the ribbon button and the status bar in the vault's language", async () => {
		const en = await load(vault({ catalog: "en", cards: cards("en") }));
		expect(en.stub.commands.find((c) => (c as { id?: string }).id === "open-today")?.name).toBe("Open today's review");
		expect(en.stub.commands.map((c) => c.name)).toContain("Start Q&A review");
		expect(en.stub.ribbons).toEqual(["Today's review"]);
		expect(en.stub.statusBar[0].textContent).toBe("Review 3");
		setLocale("en");
		const zh = await load(vault({ catalog: "none", map: "zh", cards: cards("zh") }));
		expect(zh.stub.commands.map((c) => c.name)).toContain("打开今日复习");
		expect(zh.stub.ribbons).toEqual(["今日复习"]);
		expect(zh.stub.statusBar[0].textContent).toBe("复习 3");
	});
	it("Obsidian's language comes from getLanguage() when Obsidian has it, else from local storage", async () => {
		window.localStorage.setItem("language", "en");
		setAppLanguage("zh");
		const a = await load(vault({ catalog: "missing", cards: cards("zh") }));
		expect([a.plugin.vaultLanguage, locale()]).toEqual([null, "zh"]);
		setAppLanguage(undefined);
		const b = await load(vault({ catalog: "missing", cards: cards("zh") }));
		expect([b.plugin.vaultLanguage, locale()]).toEqual([null, "en"]);
	});
});

describe("the vault's language is read again", () => {
	it("on Refresh, and when the catalog changes", async () => {
		const a = vault({ catalog: "en", cards: cards("en") });
		const { plugin, fire, stub } = await load(a);
		expect(locale()).toBe("en");
		a.files.set(CATALOG, JSON.stringify({ language: "zh" }));
		await plugin.refreshAll();
		expect([plugin.vaultLanguage, locale()]).toEqual(["zh", "zh"]);
		a.files.set(CATALOG, JSON.stringify({ language: "en" }));
		fire("modify", "10_Knowledge/algo/c1.md");
		await new Promise((r) => setTimeout(r, 20));
		expect(locale()).toBe("zh");
		fire("modify", CATALOG);
		await until(() => locale() === "en", "catalog change");
		await until(() => stub.statusBar[0].textContent === "Review 3", "status bar in the new language");
	});
});

describe("what follows the vault's language when the settings are empty", () => {
	it("card sections, built-in templates and the calendar file", async () => {
		const en = await load(vault({ catalog: "en", cards: cards("en") }));
		const loaded = await en.plugin.loadCard(en.plugin.index.get("c1")!);
		expect([loaded.items.length, loaded.essence, loaded.propositions]).toEqual([1, "Essence of c1.", "- P1. Claim of c1."]);
		expect((await en.plugin.explainTemplate()).system).toMatch(/^You are this learner's tutor/);
		expect((await en.plugin.gradeTemplate()).system).toMatch(/^You are a review examiner/);
		await en.plugin.writeIcs();
		expect(en.plugin.app.vault.adapter).toBeInstanceOf(MemoryAdapter);
		const files = (en.plugin.app.vault.adapter as unknown as MemoryAdapter).files;
		expect(files.get("90_Meta/review-reminder.ics")).toContain("SUMMARY:Daily review");
		expect(notices.at(-1)).toMatch(/^Wrote 90_Meta\/review-reminder\.ics\. /);

		const zh = await load(vault({ catalog: "none", map: "zh", cards: cards("zh") }));
		const zhCard = await zh.plugin.loadCard(zh.plugin.index.get("c1")!);
		expect([zhCard.items.length, zhCard.essence, zhCard.propositions]).toEqual([1, "Essence of c1.", "- P1. Claim of c1."]);
		expect((await zh.plugin.explainTemplate()).system).toMatch(/^你是这位学习者的讲师/);
		await zh.plugin.writeIcs();
		expect((zh.plugin.app.vault.adapter as unknown as MemoryAdapter).files.get("90_Meta/复习提醒.ics")).toContain("SUMMARY:每日复习");
		expect(notices.at(-1)).toMatch(/^已写出 90_Meta\/复习提醒\.ics。/);
	});
	it("a card without question lines names the heading the plugin looked for", async () => {
		const { plugin } = await load(vault({ catalog: "en", cards: [{ id: "c1", headings: "en", questions: false }] }));
		const modal = new ReviewModal(plugin, "flip");
		modal.open();
		await until(() => modal.contentEl.textContent?.includes("Essence of c1.") || modal.contentEl.querySelector(".sbr-muted"), "card shown");
		expect(modal.contentEl.querySelector(".sbr-muted")!.textContent).toBe('This card has no question::answer lines under "Review cards". Test yourself on its essence.');
	});
});

describe("a wrong guess of the vault's language still reads the cards (empty settings)", () => {
	const check = async (a: MemoryAdapter, want: { vault: Lang | null; ui: Lang }) => {
		const { plugin } = await load(a);
		expect([plugin.vaultLanguage, locale()]).toEqual([want.vault, want.ui]);
		for (const c of plugin.index.all()) {
			const loaded = await plugin.loadCard(c);
			expect([loaded.items.length, loaded.essence, loaded.propositions], c.id).toEqual([1, `Essence of ${c.id}.`, `- P1. Claim of ${c.id}.`]);
		}
		return plugin;
	};
	it("a Chinese vault whose map pages cannot be read, with Obsidian in English: English interface, Chinese headings found", async () => {
		window.localStorage.setItem("language", "en");
		const plugin = await check(vault({ catalog: "none", cards: cards("zh") }), { vault: null, ui: "en" });
		expect(plugin.sections().review).toBe("Review cards");
		const modal = new ReviewModal(plugin, "flip");
		modal.open();
		await until(() => modal.contentEl.textContent?.includes("Question of c1?"), "the Chinese card's question in an English session");
		expect(modal.contentEl.querySelector(".sbr-muted")).toBeNull();
	});
	it("map pages that throw on read", async () => {
		window.localStorage.setItem("language", "en");
		const a = vault({ catalog: "none", map: "zh", cards: cards("zh") });
		const read = a.read.bind(a);
		a.read = async (p: string) => (p.startsWith("20_MOCs/") ? Promise.reject(new Error("EACCES")) : read(p));
		await check(a, { vault: null, ui: "en" });
	});
	it("a catalog that names the other language, and a vault with cards of both languages", async () => {
		await check(vault({ catalog: "en", cards: cards("zh") }), { vault: "en", ui: "en" });
		await check(vault({ catalog: "zh", cards: [...cards("zh", 2), { id: "e1", headings: "en" }] }), { vault: "zh", ui: "zh" });
	});
	it("a heading typed in the settings is the only one read", async () => {
		const { plugin } = await load(vault({ catalog: "none", cards: cards("zh") }), { reviewSection: "Review cards" });
		const loaded = await plugin.loadCard(plugin.index.get("c1")!);
		expect([loaded.items.length, loaded.essence]).toEqual([0, "Essence of c1."]);
	});
});
