// Interface language and the vault-language defaults (section headings, calendar file).
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";
import { LOCALES, locale, type MessageKey, setLocale, t } from "../src/i18n";
import { extractReviewItems, sectionText } from "../src/registry";
import { cardSectionNames, DEFAULT_SETTINGS, icsFile, LANGUAGE_DEFAULTS, mergeSettings, sectionCandidates, sectionNames } from "../src/settings";
import {
	appLanguage,
	catalogPath,
	contentLanguage,
	detectVaultLanguage,
	interfaceLanguage,
	MAP_MARKERS,
	type ReadAdapter,
	recordedLanguage,
} from "../src/vault/language";
import { fixture, fixtureText } from "./fixtures";
import { MemoryAdapter } from "./memory-adapter";

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const VECTORS = fixture("vectors.json");
const CJK = /[　-鿿＀-￯]/;

/** The values these settings had in every build before the language defaults (up to c9098bb). */
const OLD_DEFAULTS = { reviewSection: "复习卡", essenceSection: "本质", propositionsSection: "命题", icsPath: "90_Meta/复习提醒.ics" };

/** Read-only view of a folder on disk with the adapter calls the plugin uses. */
class FsAdapter implements ReadAdapter {
	constructor(private root: string) {}
	async exists(p: string) {
		return existsSync(join(this.root, p));
	}
	async read(p: string) {
		return readFileSync(join(this.root, p), "utf8");
	}
	async list(p: string) {
		const files: string[] = [];
		const folders: string[] = [];
		for (const name of readdirSync(join(this.root, p))) (statSync(join(this.root, p, name)).isDirectory() ? folders : files).push(`${p}/${name}`);
		return { files, folders };
	}
}

afterEach(() => setLocale("zh"));

describe("interface strings", () => {
	const placeholders = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
	it("English has every key with the same placeholders, and no Chinese text", () => {
		const keys = Object.keys(LOCALES.zh) as MessageKey[];
		expect(Object.keys(LOCALES.en).sort()).toEqual([...keys].sort());
		for (const k of keys) {
			expect(placeholders(LOCALES.en[k]), k).toEqual(placeholders(LOCALES.zh[k]));
			expect(LOCALES.en[k], k).not.toMatch(CJK);
			expect(LOCALES.en[k].trim(), k).not.toBe("");
		}
	});
	it("t() follows the locale; the default locale is Chinese", () => {
		expect(locale()).toBe("zh");
		expect(t("btn.flipCount", { n: 3 })).toBe("开始复习（3）");
		setLocale("en");
		expect(t("btn.flipCount", { n: 3 })).toBe("Start review (3)");
		expect(t("rating.3")).toBe("Good");
	});
});

describe("the vault's language", () => {
	const folders = { data: DEFAULT_SETTINGS.data, taxonomy: DEFAULT_SETTINGS.taxonomy };
	const catalog = (extra: object) => JSON.stringify({ schema: "secondbrain-catalog/1", tracks: [], domains: {}, ...extra });
	it("is the language recorded in the catalog", async () => {
		for (const lang of ["en", "zh"] as const) {
			const a = new MemoryAdapter();
			a.files.set("_data/secondbrain/catalog.json", catalog({ language: lang }));
			a.files.set("20_MOCs/Algorithms.md", `# Algorithms\n\n${MAP_MARKERS.find(([l]) => l !== lang)![1]}\n`);
			expect(await detectVaultLanguage(a, folders)).toBe(lang);
		}
		expect(recordedLanguage(catalog({ language: "de" }))).toBeNull();
		expect(recordedLanguage("{not json")).toBeNull();
		expect(catalogPath("my data/")).toBe("my data/secondbrain/catalog.json");
	});
	it("without a record, is the language of the first map root page with a page index (a vault made before the record)", async () => {
		const a = new MemoryAdapter();
		a.files.set("_data/secondbrain/catalog.json", catalog({}));
		a.files.set("20_MOCs/Atlas 示例图谱甲.md", "# Atlas 示例图谱甲\n\n## 分页索引（全部可达）\n- [[x]]\n");
		a.files.set("20_MOCs/示例图谱乙.md", "# 示例图谱乙\n\n## Page index (every page)\n");
		a.files.set("20_MOCs/_pillars/示例图谱乙.yaml", "title: 示例图谱乙\n");
		a.files.set("20_MOCs/notes.txt", "## Page index (every page)");
		expect(await detectVaultLanguage(a, folders)).toBe("zh");
		a.files.set("20_MOCs/A plain note.md", "no marker here");
		a.files.set("20_MOCs/Accounting.md", "# Accounting\n\n## Page index (every page)\n");
		expect(await detectVaultLanguage(a, folders)).toBe("en");
		a.files.set("_data/secondbrain/catalog.json", "{broken");
		expect(await detectVaultLanguage(a, folders)).toBe("en");
	});
	it("uses the configured data and taxonomy folders", async () => {
		const a = new MemoryAdapter();
		a.files.set("meta/secondbrain/catalog.json", catalog({ language: "en" }));
		a.files.set("Maps/Algorithms.md", "## 分页索引（全部可达）");
		expect(await detectVaultLanguage(a, { data: "meta", taxonomy: "Maps/pillars" })).toBe("en");
		expect(await detectVaultLanguage(a, { data: "_data", taxonomy: "Maps/pillars" })).toBe("zh");
		expect(await detectVaultLanguage(a, { data: "_data", taxonomy: "pillars" })).toBeNull();
	});
	it("a page with both markers counts as Chinese, as in Python", async () => {
		const a = new MemoryAdapter();
		a.files.set("20_MOCs/Both.md", "## Page index (every page)\n\n## 分页索引（全部可达）\n");
		expect(await detectVaultLanguage(a, folders)).toBe("zh");
	});
	it("files that cannot be read or listed are skipped, not thrown", async () => {
		class Failing extends MemoryAdapter {
			unreadable = new Set<string>();
			listFails = false;
			async read(p: string) {
				if (this.unreadable.has(p)) throw new Error("EACCES " + p);
				return super.read(p);
			}
			async list(p: string) {
				if (this.listFails) throw new Error("EACCES " + p);
				return super.list(p);
			}
		}
		const a = new Failing();
		a.files.set("_data/secondbrain/catalog.json", catalog({ language: "en" }));
		a.files.set("20_MOCs/A first.md", "## Page index (every page)");
		a.files.set("20_MOCs/B second.md", "## 分页索引（全部可达）");
		a.unreadable.add("_data/secondbrain/catalog.json");
		a.unreadable.add("20_MOCs/A first.md");
		expect(await detectVaultLanguage(a, folders)).toBe("zh");
		a.unreadable.add("20_MOCs/B second.md");
		expect(await detectVaultLanguage(a, folders)).toBeNull();
		a.listFails = true;
		a.unreadable.clear();
		a.files.set("_data/secondbrain/catalog.json", catalog({}));
		expect(await detectVaultLanguage(a, folders)).toBeNull();
	});
	it("is unknown in an empty vault", async () => {
		expect(await detectVaultLanguage(new MemoryAdapter(), folders)).toBeNull();
	});
});

describe("interface language", () => {
	it("a set language wins; auto takes the vault's, else Obsidian's", () => {
		expect(interfaceLanguage("en", "zh", "zh")).toBe("en");
		expect(interfaceLanguage("zh", "en", "en")).toBe("zh");
		expect(interfaceLanguage("auto", "zh", "en")).toBe("zh");
		expect(interfaceLanguage("auto", "en", "zh")).toBe("en");
		expect(interfaceLanguage("auto", null, "zh")).toBe("zh");
		expect(interfaceLanguage("auto", null, "en")).toBe("en");
	});
	it("maps Obsidian's language codes", () => {
		expect([appLanguage("zh"), appLanguage("zh-TW"), appLanguage("en"), appLanguage("de"), appLanguage(null), appLanguage(undefined)]).toEqual(["zh", "zh", "en", "en", "en", "en"]);
	});
	it("card headings follow the vault even when the interface is set to another language", () => {
		expect(contentLanguage("zh", "en")).toBe("zh");
		expect(contentLanguage(null, "en")).toBe("en");
	});
	it("an unknown language setting is auto", () => {
		expect(mergeSettings({ language: "fr" }).language).toBe("auto");
		expect(mergeSettings({ language: "en" }).language).toBe("en");
		expect(mergeSettings(undefined).language).toBe("auto");
	});
});

describe("section headings and calendar file follow the vault's language", () => {
	const resolved = (saved: unknown, lang: "en" | "zh") => {
		const s = mergeSettings(saved);
		const n = sectionNames(s, lang);
		return { reviewSection: n.review, essenceSection: n.essence, propositionsSection: n.propositions, icsPath: icsFile(s, lang) };
	};
	it("a Chinese vault without saved settings reads exactly what earlier builds read", () => {
		expect(resolved(undefined, "zh")).toEqual(OLD_DEFAULTS);
		expect(resolved({}, "zh")).toEqual(OLD_DEFAULTS);
	});
	it("settings saved by an earlier build (every field written) keep their values", () => {
		const oldSaved = { ...DEFAULT_SETTINGS, ...OLD_DEFAULTS, newPerDay: 15 };
		expect(resolved(oldSaved, "zh")).toEqual(OLD_DEFAULTS);
		expect(resolved(oldSaved, "en")).toEqual(OLD_DEFAULTS);
		expect(mergeSettings(oldSaved).newPerDay).toBe(15);
	});
	it("an English vault reads the English headings that sb writes", () => {
		expect(resolved(undefined, "en")).toEqual({ reviewSection: "Review cards", essenceSection: "Essence", propositionsSection: "Propositions", icsPath: "90_Meta/review-reminder.ics" });
	});
	it("headings set in the settings win in both languages", () => {
		const custom = { reviewSection: "Flashcards", essenceSection: "Gist", propositionsSection: "Claims", icsPath: "Calendar/review.ics" };
		expect(resolved(custom, "zh")).toEqual(custom);
		expect(resolved(custom, "en")).toEqual(custom);
		expect(resolved({ essenceSection: "Gist" }, "zh")).toEqual({ ...OLD_DEFAULTS, essenceSection: "Gist" });
	});
	it("an empty setting also accepts the other language's heading, per card; a typed heading is the only one", () => {
		const zhCard = "## 本质\nE.\n\n## 命题\n- P1.\n\n## 复习卡\nQ?::A.\n";
		const enCard = "## Essence\nE.\n\n## Propositions\n- P1.\n\n## Review cards\nQ?::A.\n";
		const both = "## Essence\nE.\n\n## 本质\nZ.\n";
		const fenced = "```\n## 复习卡\n```\n";
		const unset = mergeSettings(undefined);
		expect(sectionCandidates(unset, "en").review).toEqual(["Review cards", "复习卡"]);
		expect(sectionCandidates(unset, "zh").essence).toEqual(["本质", "Essence"]);
		expect(cardSectionNames(zhCard, sectionCandidates(unset, "en"))).toEqual({ review: "复习卡", essence: "本质", propositions: "命题" });
		expect(cardSectionNames(enCard, sectionCandidates(unset, "zh"))).toEqual({ review: "Review cards", essence: "Essence", propositions: "Propositions" });
		expect(cardSectionNames(both, sectionCandidates(unset, "zh")).essence).toBe("本质");
		expect(cardSectionNames(both, sectionCandidates(unset, "en")).essence).toBe("Essence");
		expect(cardSectionNames(fenced, sectionCandidates(unset, "en")).review).toBe("Review cards");
		expect(cardSectionNames("no headings", sectionCandidates(unset, "zh"))).toEqual(sectionNames(unset, "zh"));
		const typed = mergeSettings({ reviewSection: "Flashcards" });
		expect(sectionCandidates(typed, "en").review).toEqual(["Flashcards"]);
		expect(cardSectionNames(zhCard, sectionCandidates(typed, "en")).review).toBe("Flashcards");
	});
	it("an empty or non-string saved heading means the vault's default", () => {
		expect(resolved({ reviewSection: "", essenceSection: null, propositionsSection: 3, icsPath: undefined }, "zh")).toEqual(OLD_DEFAULTS);
	});
	it("the Chinese defaults read the shared fixtures exactly like the Python default section", () => {
		const names = sectionNames(mergeSettings(undefined), "zh");
		for (const c of VECTORS.review_items) {
			const items = extractReviewItems(fixtureText(c.file), names.review);
			expect(items.map((i) => [i.question, i.answer])).toEqual(c.items);
			expect(items).toEqual(extractReviewItems(fixtureText(c.file)));
		}
		expect(sectionText(fixtureText("cards/binary-search.md"), names.essence)).toContain("Binary search finds a target");
	});
	it("the tables match the Python sources (card headings and map markers)", () => {
		const cards = readFileSync(join(REPO, "src/secondbrain/cards.py"), "utf8");
		const labels = readFileSync(join(REPO, "src/secondbrain/labels.py"), "utf8");
		for (const lang of ["zh", "en"] as const) {
			const d = LANGUAGE_DEFAULTS[lang];
			const row = cards.split("\n").find((l) => l.includes(`'${lang}': {'essence'`))!;
			expect(row, lang).toContain(`'essence': '${d.essenceSection}', 'propositions': '${d.propositionsSection}'`);
			expect(cards).toContain(`'flashcards': '${d.reviewSection}'`);
		}
		for (const [, marker] of MAP_MARKERS) expect(labels).toContain(`'page_index': '${marker}'`);
	});
});

describe("the English example vault", () => {
	const vault = join(REPO, "examples/vault");
	const dataJson = join(vault, ".obsidian/plugins/secondbrain-review/data.json");
	it("is detected as English, and finds every card's questions with or without its plugin settings file", async () => {
		const adapter = new FsAdapter(vault);
		const lang = await detectVaultLanguage(adapter, DEFAULT_SETTINGS);
		expect(lang).toBe("en");
		const withoutFile = sectionNames(mergeSettings(undefined), lang!);
		const saved = existsSync(dataJson) ? JSON.parse(readFileSync(dataJson, "utf8")) : undefined;
		expect(sectionNames(mergeSettings(saved), lang!)).toEqual(withoutFile);
		expect(icsFile(mergeSettings(saved), lang!)).toBe(icsFile(mergeSettings(undefined), lang!));
		const cards: string[] = [];
		const walk = (dir: string) => {
			for (const name of readdirSync(dir)) {
				const p = join(dir, name);
				if (statSync(p).isDirectory()) walk(p);
				else if (name.endsWith(".md")) cards.push(p);
			}
		};
		walk(join(vault, DEFAULT_SETTINGS.knowledge));
		expect(cards.length).toBeGreaterThan(0);
		for (const p of cards) {
			const text = readFileSync(p, "utf8");
			expect(extractReviewItems(text, withoutFile.review).length, p).toBeGreaterThan(0);
			expect(sectionText(text, withoutFile.essence), p).not.toBe("");
			expect(sectionText(text, withoutFile.propositions), p).not.toBe("");
		}
	});
});
