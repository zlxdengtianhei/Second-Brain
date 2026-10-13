// Study status, summary counts and the daily queue (docs/review-registry.md sections 5-6).
import { cmpCodePoint, DAY_START_HOUR, dayToDate, inOffset, learningDay, type Stamp } from "./events";
import type { CardState, ReplayResult } from "./scheduler";

export const MASTERED_STABILITY_DAYS = 21;
export const DEFAULT_NEW_PER_DAY = 10;

export type StudyState = "unlearned" | "learning" | "review" | "mastered";

export interface CardMeta {
	id: string;
	title?: string;
	domain?: string;
	kind?: string;
	status?: string;
	path?: string;
	/** [relation type, target id] */
	relations?: [string, string][];
	rank?: number;
}

/** Relation type -> true when the target comes first, false when the card comes first. */
export const PREREQ_TARGET_FIRST: Record<string, boolean> = {
	depends_on: true,
	follows: true,
	required_by: false,
	precedes: false,
};

export function studyState(st: CardState | undefined, masteredDays = MASTERED_STABILITY_DAYS): StudyState {
	if (!st) return "unlearned";
	if (st.state === "learning" || st.state === "relearning") return "learning";
	return st.stability >= masteredDays ? "mastered" : "review";
}

export function isDue(st: CardState | undefined, now: Stamp, dayStartHour = DAY_START_HOUR): boolean {
	if (!st) return false;
	return learningDay(inOffset(st.due, now.offsetMin), dayStartHour) <= learningDay(now, dayStartHour);
}

export interface Counts {
	total: number;
	unlearned: number;
	learning: number;
	review: number;
	mastered: number;
	learned: number;
	due: number;
	reviewedToday: number;
	dueNotReviewedToday: number;
}

export function emptyCounts(): Counts {
	return {
		total: 0,
		unlearned: 0,
		learning: 0,
		review: 0,
		mastered: 0,
		learned: 0,
		due: 0,
		reviewedToday: 0,
		dueNotReviewedToday: 0,
	};
}

export interface Summary {
	today: string;
	todayIndex: number;
	totals: Counts;
	domains: Map<string, Counts>;
	newToday: number;
	reviewsToday: number;
	streak: number;
	orphans: string[];
	issues: number;
}

export function streak(reviewDays: Set<number>, today: number): number {
	let day = reviewDays.has(today) ? today : today - 1;
	let n = 0;
	while (reviewDays.has(day)) {
		n++;
		day--;
	}
	return n;
}

function newToday(result: ReplayResult, known: Set<string>, today: number): number {
	let n = 0;
	for (const [card, day] of result.firstDay) if (day === today && known.has(card)) n++;
	return n;
}

export function summarize(
	cards: Iterable<CardMeta>,
	result: ReplayResult,
	now: Stamp,
	dayStartHour = DAY_START_HOUR,
	masteredDays = MASTERED_STABILITY_DAYS,
): Summary {
	const today = learningDay(now, dayStartHour);
	const totals = emptyCounts();
	const domains = new Map<string, Counts>();
	const known = new Set<string>();
	for (const meta of cards) {
		known.add(meta.id);
		const st = result.states.get(meta.id);
		const domain = meta.domain ?? "";
		let row = domains.get(domain);
		if (!row) domains.set(domain, (row = emptyCounts()));
		const s = studyState(st, masteredDays);
		const due = isDue(st, now, dayStartHour);
		const reviewed = st !== undefined && st.lastDay === today;
		for (const b of [totals, row]) {
			b.total++;
			b[s]++;
			if (s !== "unlearned") b.learned++;
			if (due) {
				b.due++;
				if (!reviewed) b.dueNotReviewedToday++;
			}
			if (reviewed) b.reviewedToday++;
		}
	}
	const sorted = new Map([...domains.entries()].sort((a, b) => cmpCodePoint(a[0], b[0])));
	return {
		today: dayToDate(today),
		todayIndex: today,
		totals,
		domains: sorted,
		newToday: newToday(result, known, today),
		reviewsToday: result.reviewsByDay.get(today) ?? 0,
		streak: streak(result.reviewDays, today),
		orphans: [...result.states.keys()].filter((c) => !known.has(c)).sort(cmpCodePoint),
		issues: result.issues.length,
	};
}

// ---------------------------------------------------------------------------
// Queue
// ---------------------------------------------------------------------------
type Key = [number, string];

function cmpKey(a: Key, b: Key): number {
	return a[0] - b[0] || cmpCodePoint(a[1], b[1]);
}

/** Small binary heap keyed by (rank, id). */
class KeyHeap {
	private items: Key[] = [];
	get size(): number {
		return this.items.length;
	}
	push(k: Key): void {
		const a = this.items;
		a.push(k);
		let i = a.length - 1;
		while (i > 0) {
			const p = (i - 1) >> 1;
			if (cmpKey(a[i], a[p]) >= 0) break;
			[a[i], a[p]] = [a[p], a[i]];
			i = p;
		}
	}
	pop(): Key | undefined {
		const a = this.items;
		if (!a.length) return undefined;
		const top = a[0];
		const last = a.pop()!;
		if (a.length) {
			a[0] = last;
			let i = 0;
			for (;;) {
				const l = 2 * i + 1;
				const r = l + 1;
				let m = i;
				if (l < a.length && cmpKey(a[l], a[m]) < 0) m = l;
				if (r < a.length && cmpKey(a[r], a[m]) < 0) m = r;
				if (m === i) break;
				[a[i], a[m]] = [a[m], a[i]];
				i = m;
			}
		}
		return top;
	}
}

/** Prerequisite topological order; returns the order and the cards taken to break cycles. */
export function learningOrder<T extends CardMeta>(cards: T[]): { ordered: T[]; cycleBreaks: string[] } {
	const byId = new Map(cards.map((c) => [c.id, c]));
	const key = new Map<string, Key>(cards.map((c) => [c.id, [c.rank ?? 0, c.id]]));
	const indeg = new Map(cards.map((c) => [c.id, 0]));
	const adj = new Map<string, Set<string>>();
	for (const c of cards) {
		for (const [type, target] of c.relations ?? []) {
			const first = PREREQ_TARGET_FIRST[type];
			if (first === undefined || target === c.id || !byId.has(target)) continue;
			const [src, dst] = first ? [target, c.id] : [c.id, target];
			let out = adj.get(src);
			if (!out) adj.set(src, (out = new Set()));
			if (!out.has(dst)) {
				out.add(dst);
				indeg.set(dst, indeg.get(dst)! + 1);
			}
		}
	}
	const ready = new KeyHeap();
	for (const [id, n] of indeg) if (n === 0) ready.push(key.get(id)!);
	const remaining = [...key.values()].sort(cmpKey);
	let pos = 0;
	const done = new Set<string>();
	const ordered: T[] = [];
	const cycleBreaks: string[] = [];
	while (done.size < byId.size) {
		let id: string;
		if (ready.size) {
			id = ready.pop()![1];
			if (done.has(id)) continue;
		} else {
			while (done.has(remaining[pos][1])) pos++;
			id = remaining[pos][1];
			cycleBreaks.push(id);
		}
		done.add(id);
		ordered.push(byId.get(id)!);
		for (const nxt of adj.get(id) ?? []) {
			if (done.has(nxt)) continue;
			const n = indeg.get(nxt)! - 1;
			indeg.set(nxt, n);
			if (n === 0) ready.push(key.get(nxt)!);
		}
	}
	return { ordered, cycleBreaks };
}

export function eligibleNew(meta: CardMeta, domains: readonly string[]): boolean {
	if (meta.kind === "record" || meta.status === "needs-review") return false;
	return domains.length === 0 || domains.includes(meta.domain ?? "");
}

export interface Queue<T extends CardMeta> {
	today: string;
	due: T[];
	new: T[];
	newQuota: number;
	newToday: number;
	newAvailable: number;
	cycleBreaks: string[];
}

export function buildQueue<T extends CardMeta>(
	cards: Iterable<T>,
	result: ReplayResult,
	now: Stamp,
	opts: { newPerDay?: number; domains?: readonly string[]; dayStartHour?: number } = {},
): Queue<T> {
	const dayStartHour = opts.dayStartHour ?? DAY_START_HOUR;
	const domains = opts.domains ?? [];
	const today = learningDay(now, dayStartHour);
	const metas = [...cards];
	const due: [number, string, T][] = [];
	const candidates: T[] = [];
	for (const meta of metas) {
		const st = result.states.get(meta.id);
		if (!st) {
			if (eligibleNew(meta, domains)) candidates.push(meta);
		} else if (isDue(st, now, dayStartHour)) {
			due.push([st.due, meta.id, meta]);
		}
	}
	due.sort((a, b) => a[0] - b[0] || cmpCodePoint(a[1], b[1]));
	const known = new Set(metas.map((m) => m.id));
	const nt = newToday(result, known, today);
	const quota = Math.max(0, (opts.newPerDay ?? DEFAULT_NEW_PER_DAY) - nt);
	const { ordered, cycleBreaks } = learningOrder(candidates);
	return {
		today: dayToDate(today),
		due: due.map((d) => d[2]),
		new: ordered.slice(0, quota),
		newQuota: quota,
		newToday: nt,
		newAvailable: candidates.length,
		cycleBreaks,
	};
}
