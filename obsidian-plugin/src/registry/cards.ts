// Card text helpers: review questions and Spaced Repetition schedules (docs sections 2.1, 2.2).
import { type ImportSeed, questionId } from "./events";

export const DEFAULT_REVIEW_SECTION = "复习卡";

export interface ReviewItem {
	q: string;
	question: string;
	answer: string;
}

const SR_COMMENT_RE = /\s*<!--SR:.*?-->/g;
const HEADING_RE = /^(#{1,6})\s+(.*?)\s*#*\s*$/;

/** The `question::answer` lines of a card's review section. */
export function extractReviewItems(markdown: string, section = DEFAULT_REVIEW_SECTION): ReviewItem[] {
	const items: ReviewItem[] = [];
	let inSection = false;
	let level = 0;
	let inFence = false;
	for (const raw of markdown.split(/\r?\n/)) {
		const line = raw.trimEnd();
		const lead = line.trimStart();
		if (lead.startsWith("```") || lead.startsWith("~~~")) {
			inFence = !inFence;
			continue;
		}
		if (inFence) continue;
		const m = HEADING_RE.exec(line);
		if (m) {
			if (inSection && m[1].length <= level) inSection = false;
			if (!inSection && m[2].trim() === section) {
				inSection = true;
				level = m[1].length;
			}
			continue;
		}
		if (!inSection || !line.includes("::")) continue;
		let text = line.replace(SR_COMMENT_RE, "").trim();
		if (text.startsWith("- ") || text.startsWith("* ")) text = text.slice(2).trim();
		const at = text.indexOf("::");
		const question = text.slice(0, at).trim();
		const answer = text.slice(at + 2).replace(/^:+/, "").trim();
		if (question && answer) items.push({ q: questionId(question), question, answer });
	}
	return items;
}

/** Text of a level-2 section such as "本质" or "命题" (empty when missing). */
export function sectionText(markdown: string, name: string): string {
	const lines = markdown.split(/\r?\n/);
	const out: string[] = [];
	let inSection = false;
	let level = 0;
	let inFence = false;
	for (const line of lines) {
		const lead = line.trimStart();
		if (lead.startsWith("```") || lead.startsWith("~~~")) inFence = !inFence;
		const m = inFence ? null : HEADING_RE.exec(line.trimEnd());
		if (m) {
			if (inSection && m[1].length <= level) break;
			if (!inSection && m[2].trim() === name) {
				inSection = true;
				level = m[1].length;
				continue;
			}
		}
		if (inSection) out.push(line);
	}
	return out.join("\n").trim();
}

/** Remove the YAML front matter block. */
export function stripFrontmatter(markdown: string): string {
	if (!markdown.startsWith("---")) return markdown;
	const end = markdown.indexOf("\n---", 3);
	if (end < 0) return markdown;
	const after = markdown.indexOf("\n", end + 4);
	return after < 0 ? "" : markdown.slice(after + 1);
}

const SR_BLOCK_RE = /<!--SR:([\s\S]*?)-->/g;
const SM2_RE = /^!(\d{4}-\d{2}-\d{2}),(\d+),(\d+)/;

/** Merge the Spaced Repetition schedules of one card into one import seed (or null). */
export function srSchedule(text: string, frontmatter: Record<string, unknown> | null | undefined): ImportSeed | null {
	const sm2: [string, number, number][] = [];
	const fsrsRows: { due: string; interval: number; stability: number; difficulty: number; last_review: string }[] = [];
	for (const m of text.matchAll(SR_BLOCK_RE)) {
		for (const seg of m[1].split("!").slice(1)) {
			const parts = seg.split(",");
			if (parts[0] === "fsrs" && parts.length >= 10) {
				const interval = Math.trunc(Number(parts[2]));
				const stability = Number(parts[3]);
				const difficulty = Number(parts[4]);
				if ([interval, stability, difficulty].some((n) => !Number.isFinite(n)) || parts[2].trim() === "") continue;
				const lr = parts[9].trim();
				fsrsRows.push({ due: parts[1], interval, stability, difficulty, last_review: lr === "-" ? "" : lr });
				continue;
			}
			const sm = SM2_RE.exec("!" + seg);
			if (sm) sm2.push([sm[1], Number(sm[2]), Number(sm[3])]);
		}
	}
	const fm = frontmatter ?? {};
	const due = fm["sr-due"];
	const ivl = fm["sr-interval"];
	const ease = fm["sr-ease"];
	if (due && ivl !== undefined && ivl !== null && ease !== undefined && ease !== null) {
		const i = Math.trunc(Number(ivl));
		const e = Math.trunc(Number(ease));
		if (Number.isFinite(i) && Number.isFinite(e)) sm2.push([String(due).slice(0, 10), i, e]);
	}
	if (fsrsRows.length) {
		const rows = [...fsrsRows].sort((a, b) => (a.due < b.due ? -1 : a.due > b.due ? 1 : a.interval - b.interval));
		const first = rows[0];
		const seed: ImportSeed = {
			from: "sr-fsrs",
			due: first.due,
			interval: Math.min(...rows.map((r) => r.interval)),
			stability: Math.min(...rows.map((r) => r.stability)),
			difficulty: Math.max(...rows.map((r) => r.difficulty)),
		};
		if (first.last_review) seed.last_review = first.last_review;
		return seed;
	}
	if (sm2.length) {
		return {
			from: "sr-sm2",
			due: sm2.map((r) => r[0]).sort()[0],
			interval: Math.max(1, Math.min(...sm2.map((r) => r[1]))),
			ease: Math.min(...sm2.map((r) => r[2])),
		};
	}
	return null;
}
