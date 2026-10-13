// Plugin settings: defaults follow the vault layout of the Second Brain tool (config [layout]).
import type { LogFormat } from "./vault/event-store";

export interface ReviewSettings {
	/** Vault folders (same names as the CLI config's [layout] table). */
	knowledge: string;
	taxonomy: string;
	data: string;
	/** Heading of the section that holds `question::answer` lines. */
	reviewSection: string;
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
	reminderTime: string;
	icsPath: string;
	startupNotice: boolean;
}

export const DEFAULT_SETTINGS: ReviewSettings = {
	knowledge: "10_Knowledge",
	taxonomy: "20_MOCs/_pillars",
	data: "_data",
	reviewSection: "复习卡",
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
	reminderTime: "09:00",
	icsPath: "90_Meta/复习提醒.ics",
	startupNotice: true,
};

export function mergeSettings(saved: unknown): ReviewSettings {
	const s = { ...DEFAULT_SETTINGS, ...(typeof saved === "object" && saved ? (saved as Partial<ReviewSettings>) : {}) };
	if (!Array.isArray(s.studyDomains)) s.studyDomains = [];
	s.newPerDay = Number.isFinite(Number(s.newPerDay)) ? Math.max(0, Math.round(Number(s.newPerDay))) : DEFAULT_SETTINGS.newPerDay;
	if (s.logFormat !== "md") s.logFormat = "jsonl";
	return s;
}

export function reviewDir(s: ReviewSettings): string {
	return `${s.data.replace(/\/+$/, "")}/review`;
}
