// The vault's language and the interface language. Same rule as the Python side
// (src/secondbrain/labels.py vault_language): the language recorded in the catalog,
// else the language of the generated map pages, else unknown.
import type { Lang } from "../i18n";
import type { LanguageSetting } from "../settings";

export interface ReadAdapter {
	exists(path: string): Promise<boolean>;
	read(path: string): Promise<string>;
	list(path: string): Promise<{ files: string[]; folders: string[] }>;
}

/** The heading every generated map root page has (labels.py MOC[lang]["page_index"]), checked in this order. */
export const MAP_MARKERS: [Lang, string][] = [
	["zh", "## 分页索引（全部可达）"],
	["en", "## Page index (every page)"],
];

const trim = (p: string) => p.replace(/^\/+|\/+$/g, "");

/** `<data>/secondbrain/catalog.json`, as in the Python layout. */
export function catalogPath(data: string): string {
	return `${trim(data)}/secondbrain/catalog.json`;
}

/** The `language` recorded in the catalog (`sb init`, `sb language --set`), if any. */
export function recordedLanguage(catalogText: string | null): Lang | null {
	if (!catalogText) return null;
	try {
		const value = (JSON.parse(catalogText) as { language?: unknown } | null)?.language;
		return value === "en" || value === "zh" ? value : null;
	} catch {
		return null;
	}
}

/** Language of the first map root page (in name order) that has a page index heading. */
export function mapLanguage(pages: string[]): Lang | null {
	for (const text of pages) {
		for (const [lang, marker] of MAP_MARKERS) if (text.includes(marker)) return lang;
	}
	return null;
}

async function readOrNull(adapter: ReadAdapter, path: string): Promise<string | null> {
	try {
		return (await adapter.exists(path)) ? await adapter.read(path) : null;
	} catch {
		return null;
	}
}

/**
 * The vault's language, or null when it is not recorded and there are no generated maps.
 * Map root pages are the `.md` files in the folder that holds the taxonomy folder
 * (`20_MOCs` for the default `20_MOCs/_pillars`); they are read one by one until one has a marker.
 */
export async function detectVaultLanguage(adapter: ReadAdapter, folders: { data: string; taxonomy: string }): Promise<Lang | null> {
	const recorded = recordedLanguage(await readOrNull(adapter, catalogPath(folders.data)));
	if (recorded) return recorded;
	const taxonomy = trim(folders.taxonomy);
	const mocs = taxonomy.includes("/") ? taxonomy.slice(0, taxonomy.lastIndexOf("/")) : "";
	if (!mocs) return null;
	let files: string[];
	try {
		if (!(await adapter.exists(mocs))) return null;
		files = (await adapter.list(mocs)).files;
	} catch {
		return null;
	}
	const name = (p: string) => p.slice(p.lastIndexOf("/") + 1);
	const pages = files.filter((p) => p.endsWith(".md")).sort((a, b) => (name(a) < name(b) ? -1 : name(a) > name(b) ? 1 : 0));
	for (const path of pages) {
		const lang = mapLanguage([(await readOrNull(adapter, path)) ?? ""]);
		if (lang) return lang;
	}
	return null;
}

/** Obsidian's language code ("zh", "zh-TW", "en", "de", ...) as one of the plugin's languages. */
export function appLanguage(code: string | null | undefined): Lang {
	return typeof code === "string" && code.toLowerCase().startsWith("zh") ? "zh" : "en";
}

/** The interface language: the setting, or for "auto" the vault's language, else Obsidian's. */
export function interfaceLanguage(setting: LanguageSetting, vault: Lang | null, app: Lang): Lang {
	return setting === "en" || setting === "zh" ? setting : (vault ?? app);
}

/** The language of the card headings and the calendar file name: the vault's, else the interface's. */
export function contentLanguage(vault: Lang | null, ui: Lang): Lang {
	return vault ?? ui;
}
