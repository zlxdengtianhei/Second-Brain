// Grading one answer: build the prompt, call the model, parse its JSON verdict.
import { t } from "../i18n";
import type { LlmClient } from "./ai";
import { renderMessages, type Template } from "./prompts";

export type Verdict = "hit" | "partial" | "miss";

export interface GradeResult {
	verdict: Verdict;
	covered: string[];
	missing: string[];
	wrong: string[];
	/** 1-3; Easy (4) is only for the learner to choose. */
	suggestedRating: number;
	feedback: string;
}

export type GradeOutcome = { graded: true; result: GradeResult; model: string } | { graded: false; reason: string };

function strings(v: unknown): string[] {
	return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string" && x.trim() !== "").map((s) => s.trim()) : [];
}

/** Parse the model's reply. Returns null when it is not the required JSON (the learner then rates alone). */
export function parseGrade(text: string): GradeResult | null {
	const cleaned = text.replace(/```(?:json)?/gi, "");
	const start = cleaned.indexOf("{");
	const end = cleaned.lastIndexOf("}");
	if (start < 0 || end <= start) return null;
	let obj: unknown;
	try {
		obj = JSON.parse(cleaned.slice(start, end + 1));
	} catch {
		return null;
	}
	if (typeof obj !== "object" || obj === null) return null;
	const o = obj as Record<string, unknown>;
	if (o.verdict !== "hit" && o.verdict !== "partial" && o.verdict !== "miss") return null;
	const r = typeof o.suggested_rating === "string" ? Number(o.suggested_rating) : o.suggested_rating;
	if (typeof r !== "number" || !Number.isInteger(r) || r < 1 || r > 4) return null;
	return {
		verdict: o.verdict,
		covered: strings(o.covered),
		missing: strings(o.missing),
		wrong: strings(o.wrong),
		suggestedRating: Math.min(3, r),
		feedback: typeof o.feedback === "string" ? o.feedback.trim() : "",
	};
}

export interface GradeInput {
	question: string;
	answer: string;
	propositions: string;
	userAnswer: string;
}

export async function gradeAnswer(llm: LlmClient, tpl: Template, input: GradeInput, abort?: AbortController): Promise<GradeOutcome> {
	const ready = await llm.ready();
	if (!ready.ok) return { graded: false, reason: ready.reason };
	let text: string;
	try {
		text = await llm.complete(
			renderMessages(tpl, {
				question: input.question,
				answer: input.answer,
				propositions: input.propositions || t("ctx.noPropositions"),
				user_answer: input.userAnswer,
			}),
			{ abort },
		);
	} catch (e) {
		return { graded: false, reason: t("llm.failed", { error: (e as Error).message ?? String(e) }) };
	}
	const result = parseGrade(text);
	return result ? { graded: true, result, model: ready.label } : { graded: false, reason: t("llm.invalid") };
}
