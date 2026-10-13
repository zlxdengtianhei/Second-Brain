// Context for "explain this card": the card, its related cards' essence, its place in the map, learner state.
// Labels are in the interface language, the same language as the built-in template.
import { t } from "../i18n";
import { stripFrontmatter } from "../registry";
import type { IndexedCard, Relation } from "../vault/card-index";
import type { PillarInfo } from "../vault/taxonomy";

/** Relation types that say what to learn first; they are listed before the others. */
export const PREREQ_TYPES = new Set(["depends_on", "part_of", "follows", "precedes", "required_by"]);
const MIN_RELATED = 3;

export interface ExplainLimits {
	maxRelated: number;
	essenceChars: number;
	siblings: number;
	cardChars: number;
	answerChars: number;
}

export interface RelatedLine {
	title: string;
	type: string;
	essence: string;
}

export interface LearnerFacts {
	reviews: number;
	lastDate?: string;
	lastRating?: number;
	lastMissing?: string[];
}

export interface ExplainInputs {
	card: IndexedCard;
	cardText: string;
	related: RelatedLine[];
	pillar?: PillarInfo;
	domainTitle: string;
	siblings: string[];
	learnerState: string;
	limits: ExplainLimits;
}

/**
 * Pick the related cards to describe: prerequisite types first, then the rest in card order;
 * `tier: extension` relations only fill up to three; at most `limit`; one line per target.
 */
export function selectRelations(relations: Relation[], limit: number): Relation[] {
	const seen = new Set<string>();
	const rank = (r: Relation) => (PREREQ_TYPES.has(r.type) ? 0 : 1);
	const ordered = relations
		.map((r, i) => ({ r, i }))
		.sort((a, b) => rank(a.r) - rank(b.r) || a.i - b.i)
		.map((x) => x.r);
	const core: Relation[] = [];
	const ext: Relation[] = [];
	for (const r of ordered) {
		if (seen.has(r.target)) continue;
		seen.add(r.target);
		(r.tier === "extension" ? ext : core).push(r);
	}
	const picked = core.slice(0, limit);
	for (const r of ext) {
		if (picked.length >= Math.min(MIN_RELATED, limit)) break;
		picked.push(r);
	}
	return picked;
}

export function truncate(text: string, max: number): string {
	const s = text.replace(/\s+/g, " ").trim();
	return s.length <= max ? s : s.slice(0, Math.max(0, max - 1)) + "…";
}

/** Card text for the model: a short header from the front matter, then the body (no sources, no hashes). */
export function cardBody(card: IndexedCard, text: string, maxChars: number): string {
	const head = [
		t("ctx.title", { v: card.title }),
		card.kind && t("ctx.kind", { v: card.kind }),
		card.domain && t("ctx.domain", { v: card.domain }),
		card.pillar && t("ctx.pillar", { v: card.pillar }),
		card.topic && t("ctx.topic", { v: card.topic }),
	].filter(Boolean);
	let body = stripFrontmatter(text).trim();
	if (body.length > maxChars) body = body.slice(0, maxChars) + "\n" + t("ctx.truncated");
	return `${head.join("\n")}\n\n${body}`;
}

/** Template variables (see examples/prompts/explain.md). */
export function explainVars(inp: ExplainInputs): Record<string, string> {
	const lim = inp.limits;
	const sep = t("ctx.relatedSep");
	const related = inp.related.slice(0, lim.maxRelated).map((r) => `- ${r.title}${sep}${r.type}${sep}${truncate(r.essence || t("ctx.noEssence"), lim.essenceChars)}`);
	const unknown = t("ctx.unknown");
	const none = t("ctx.none");
	return {
		learner_state: inp.learnerState,
		card_body: cardBody(inp.card, inp.cardText, lim.cardChars),
		domain_moc_title: inp.domainTitle || inp.card.domain || unknown,
		pillar_title: inp.pillar?.name ?? inp.card.pillar ?? unknown,
		pillar_summary: inp.pillar?.why || t("ctx.noPillar"),
		pillar_topics: inp.pillar?.topics.join(t("list.sep")) || none,
		topic: inp.card.topic || unknown,
		sibling_titles: inp.siblings.slice(0, lim.siblings).join(t("list.sep")) || none,
		related_lines: related.join("\n") || none,
		max_chars: String(lim.answerChars),
	};
}

/** Titles of other cards in the same pillar and topic, in index order. */
export function siblingTitles(card: IndexedCard, all: Iterable<IndexedCard>, limit: number): string[] {
	const out: string[] = [];
	if (!card.topic) return out;
	for (const c of all) {
		if (c.id === card.id || c.topic !== card.topic || c.pillar !== card.pillar) continue;
		out.push(c.title);
		if (out.length >= limit) break;
	}
	return out;
}

/** One or two lines about the learner's history with this card. Events: newest first. */
export function learnerState(
	events: { mode: string; rating: number | null; ms: number; offsetMin: number; missing: string[] }[],
	reps: number,
	labels: { none: string; summary: (n: number, date: string, rating: number) => string; missing: (items: string) => string },
): string {
	const reviews = events.filter((e) => e.mode !== "import" && e.rating !== null);
	if (!reviews.length) return labels.none;
	const last = reviews[0];
	const date = new Date(last.ms + last.offsetMin * 60_000).toISOString().slice(0, 10);
	const lines = [labels.summary(Math.max(reps, reviews.length), date, last.rating!)];
	const withMissing = reviews.find((e) => e.missing.length);
	if (withMissing) lines.push(labels.missing(withMissing.missing.slice(0, 6).join(t("ctx.missingSep"))));
	return lines.join("\n");
}
