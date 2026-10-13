// Plugin settings: defaults follow the vault layout of the Second Brain tool (config [layout]).
import type { Lang } from "./i18n";
import { hasHeading } from "./registry";
import type { LogFormat } from "./vault/event-store";

export type LanguageSetting = "auto" | Lang;

export interface ReviewSettings {
	/** Vault folders (same names as the CLI config's [layout] table). */
	knowledge: string;
	taxonomy: string;
	data: string;
	/** Interface language; "auto" = the vault's language, else Obsidian's. */
	language: LanguageSetting;
	/** Headings of the card sections the plugin reads. Empty = the heading in the vault's language. */
	reviewSection: string;
	essenceSection: string;
	propositionsSection: string;
	logFormat: LogFormat;
	newPerDay: number;
	/** Empty = all domains. Order sets which domain's new cards come first. */
	studyDomains: string[];
	/** AI Providers provider id; empty = ask the user to pick one. */
	providerId: string;
	gradeTemplate: string;
	explainTemplate: string;
	explainMaxRelated: number;
	explainEssenceChars: number;
	explainSiblings: number;
	explainCardChars: number;
	explainAnswerChars: number;
	reminderTime: string;
	/** Empty = the file name in the vault's language. */
	icsPath: string;
	startupNotice: boolean;
}

export const DEFAULT_SETTINGS: ReviewSettings = {
	knowledge: "10_Knowledge",
	taxonomy: "20_MOCs/_pillars",
	data: "_data",
	language: "auto",
	reviewSection: "",
	essenceSection: "",
	propositionsSection: "",
	logFormat: "jsonl",
	newPerDay: 10,
	studyDomains: [],
	providerId: "",
	gradeTemplate: "90_Meta/prompts/grade.md",
	explainTemplate: "90_Meta/prompts/explain.md",
	explainMaxRelated: 8,
	explainEssenceChars: 120,
	explainSiblings: 10,
	explainCardChars: 6000,
	explainAnswerChars: 600,
	reminderTime: "09:00",
	icsPath: "",
	startupNotice: true,
};

export function mergeSettings(saved: unknown): ReviewSettings {
	const s = { ...DEFAULT_SETTINGS, ...(typeof saved === "object" && saved ? (saved as Partial<ReviewSettings>) : {}) };
	if (!Array.isArray(s.studyDomains)) s.studyDomains = [];
	s.newPerDay = Number.isFinite(Number(s.newPerDay)) ? Math.max(0, Math.round(Number(s.newPerDay))) : DEFAULT_SETTINGS.newPerDay;
	if (s.logFormat !== "md") s.logFormat = "jsonl";
	if (s.language !== "en" && s.language !== "zh") s.language = "auto";
	for (const k of ["reviewSection", "essenceSection", "propositionsSection", "icsPath"] as const) {
		if (typeof s[k] !== "string") s[k] = "";
	}
	return s;
}

/**
 * Card section headings that `sb` writes in each vault language (src/secondbrain/cards.py HEADINGS:
 * flashcards, essence, propositions), and the default calendar file name.
 */
export const LANGUAGE_DEFAULTS: Record<Lang, { reviewSection: string; essenceSection: string; propositionsSection: string; icsPath: string }> = {
	zh: { reviewSection: "复习卡", essenceSection: "本质", propositionsSection: "命题", icsPath: "90_Meta/复习提醒.ics" },
	en: { reviewSection: "Review cards", essenceSection: "Essence", propositionsSection: "Propositions", icsPath: "90_Meta/review-reminder.ics" },
};

export interface SectionNames {
	review: string;
	essence: string;
	propositions: string;
}

/** The headings to read: a heading set in the settings wins over the one of the vault's language. */
export function sectionNames(s: ReviewSettings, lang: Lang): SectionNames {
	const d = LANGUAGE_DEFAULTS[lang];
	return {
		review: s.reviewSection || d.reviewSection,
		essence: s.essenceSection || d.essenceSection,
		propositions: s.propositionsSection || d.propositionsSection,
	};
}

export type SectionCandidates = { [K in keyof SectionNames]: string[] };

/**
 * Headings to try for each section, in order. A heading set in the settings is the only one;
 * an empty setting tries the heading of `lang`, then the other language's, so a wrong guess of
 * the vault's language still finds the card's sections.
 */
export function sectionCandidates(s: ReviewSettings, lang: Lang): SectionCandidates {
	const other: Lang = lang === "zh" ? "en" : "zh";
	const pick = (set: string, key: "reviewSection" | "essenceSection" | "propositionsSection") =>
		set ? [set] : [LANGUAGE_DEFAULTS[lang][key], LANGUAGE_DEFAULTS[other][key]];
	return {
		review: pick(s.reviewSection, "reviewSection"),
		essence: pick(s.essenceSection, "essenceSection"),
		propositions: pick(s.propositionsSection, "propositionsSection"),
	};
}

/** The headings one card uses: per section, the first candidate the card has, else the first candidate. */
export function cardSectionNames(markdown: string, c: SectionCandidates): SectionNames {
	const first = (names: string[]) => names.find((n) => hasHeading(markdown, n)) ?? names[0];
	return { review: first(c.review), essence: first(c.essence), propositions: first(c.propositions) };
}

export function icsFile(s: ReviewSettings, lang: Lang): string {
	return s.icsPath || LANGUAGE_DEFAULTS[lang].icsPath;
}

export function reviewDir(s: ReviewSettings): string {
	return `${s.data.replace(/\/+$/, "")}/review`;
}
