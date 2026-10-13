// Replay of review events with ts-fsrs on day-aligned time (docs/review-registry.md section 3-4).
import { type Card, createEmptyCard, fsrs, type FSRS, generatorParameters, type Grade, State } from "ts-fsrs";
import {
	DAY_MS,
	DAY_START_HOUR,
	formatUtc,
	inOffset,
	learningDay,
	orderEvents,
	type Issue,
	type ReviewEvent,
	seedTime,
} from "./events";

export const FSRS_PARAMETERS = [
	0.212, 1.2931, 2.3065, 8.2956, 6.4133, 0.8334, 3.0194, 0.001, 1.8722, 0.1666, 0.796, 1.4835, 0.0614, 0.2629,
	1.6483, 0.6014, 1.8729, 0.5425, 0.0912, 0.0658, 0.1542,
];

export type SchedState = "learning" | "review" | "relearning";

export interface CardState {
	card: string;
	state: SchedState;
	stability: number;
	difficulty: number;
	/** UTC ms */
	due: number;
	/** UTC ms */
	lastReview: number;
	reps: number;
	lapses: number;
	firstReview: number | null;
	/** Learning day of the last non-import review (event's own offset). */
	lastDay: number | null;
	/** Scheduler day: never goes backwards. */
	schedDay: number;
	events: number;
	lastEvent: ReviewEvent | null;
	fsrsCard: Card;
}

export interface ReplayResult {
	states: Map<string, CardState>;
	issues: Issue[];
	reviewDays: Set<number>;
	reviewsByDay: Map<number, number>;
	/** card -> learning day of its first non-import review */
	firstDay: Map<string, number>;
}

let shared: FSRS | null = null;

export function scheduler(): FSRS {
	shared ??= fsrs(
		generatorParameters({
			w: FSRS_PARAMETERS,
			request_retention: 0.9,
			maximum_interval: 36500,
			enable_fuzz: false,
			enable_short_term: true,
			learning_steps: ["1m", "10m"],
			relearning_steps: ["10m"],
		}),
	);
	return shared;
}

const STATE_NAME: Record<number, SchedState> = {
	[State.Learning]: "learning",
	[State.Review]: "review",
	[State.Relearning]: "relearning",
};

function seed(ev: ReviewEvent, prev: CardState | undefined, dayStartHour: number): CardState {
	const s = ev.importSeed!;
	const off = ev.ts.offsetMin;
	const due = seedTime(s.due, off, dayStartHour).ms;
	const interval = s.interval;
	let stability: number;
	let difficulty: number;
	let last: number;
	if (s.from === "sr-sm2") {
		stability = Math.max(interval, 0.1);
		difficulty = Math.round(Math.min(10, Math.max(1, 10 - (9 * (s.ease! - 130)) / 220)) * 1e4) / 1e4;
		last = due - interval * DAY_MS;
	} else {
		stability = s.stability!;
		difficulty = s.difficulty!;
		last = s.last_review ? seedTime(s.last_review, off, dayStartHour).ms : due - interval * DAY_MS;
	}
	const schedDay = learningDay(inOffset(last, off), dayStartHour);
	const synLast = schedDay * DAY_MS;
	const fsrsCard: Card = {
		...createEmptyCard(new Date(synLast)),
		due: new Date(synLast + (due - last)),
		stability,
		difficulty,
		scheduled_days: Math.round(interval),
		learning_steps: 0,
		reps: 0,
		lapses: 0,
		state: State.Review,
		last_review: new Date(synLast),
	};
	return {
		card: ev.card,
		state: "review",
		stability,
		difficulty,
		due,
		lastReview: last,
		reps: 0,
		lapses: 0,
		firstReview: prev?.firstReview ?? null,
		lastDay: prev?.lastDay ?? null,
		schedDay,
		events: (prev?.events ?? 0) + 1,
		lastEvent: ev,
		fsrsCard,
	};
}

/** Apply one event to a card's previous state (events must arrive in replay order). */
export function applyEvent(prev: CardState | undefined, ev: ReviewEvent, dayStartHour = DAY_START_HOUR): CardState {
	if (ev.importSeed) return seed(ev, prev, dayStartHour);
	const day = learningDay(ev.ts, dayStartHour);
	const schedDay = prev ? Math.max(day, prev.schedDay) : day;
	const syn = new Date(schedDay * DAY_MS);
	const before = prev ? prev.fsrsCard : createEmptyCard(syn);
	const next = scheduler().next(before, syn, ev.rating as Grade).card;
	const due = ev.ts.ms + (next.due.getTime() - syn.getTime());
	return {
		card: ev.card,
		state: STATE_NAME[next.state] ?? "learning",
		stability: next.stability,
		difficulty: next.difficulty,
		due,
		lastReview: ev.ts.ms,
		reps: next.reps,
		lapses: next.lapses,
		firstReview: prev?.firstReview ?? ev.ts.ms,
		lastDay: day,
		schedDay,
		events: (prev?.events ?? 0) + 1,
		lastEvent: ev,
		fsrsCard: next,
	};
}

export function emptyResult(): ReplayResult {
	return { states: new Map(), issues: [], reviewDays: new Set(), reviewsByDay: new Map(), firstDay: new Map() };
}

/** Record a review event in the day statistics (not for imports). */
export function countEvent(result: ReplayResult, ev: ReviewEvent, dayStartHour = DAY_START_HOUR): void {
	if (ev.importSeed) return;
	const day = learningDay(ev.ts, dayStartHour);
	result.reviewDays.add(day);
	result.reviewsByDay.set(day, (result.reviewsByDay.get(day) ?? 0) + 1);
	if (!result.firstDay.has(ev.card)) result.firstDay.set(ev.card, day);
}

/** Replay events into per-card states (docs section 4). */
export function replay(events: Iterable<ReviewEvent>, dayStartHour = DAY_START_HOUR): ReplayResult {
	const result = emptyResult();
	for (const ev of orderEvents(events)) {
		result.states.set(ev.card, applyEvent(result.states.get(ev.card), ev, dayStartHour));
		countEvent(result, ev, dayStartHour);
	}
	return result;
}

/** The same fields as Python's CardState.as_dict(), for vectors and debugging. */
export function stateToDict(s: CardState): Record<string, unknown> {
	return {
		state: s.state,
		stability: s.stability,
		difficulty: s.difficulty,
		due: formatUtc(s.due),
		last_review: formatUtc(s.lastReview),
		reps: s.reps,
		lapses: s.lapses,
	};
}

/** Due time of each rating, for showing intervals on the rating buttons. */
export function previewRatings(prev: CardState | undefined, now: ReviewEvent["ts"], dayStartHour = DAY_START_HOUR): Record<number, number> {
	const day = learningDay(now, dayStartHour);
	const schedDay = prev ? Math.max(day, prev.schedDay) : day;
	const syn = new Date(schedDay * DAY_MS);
	const card = prev ? prev.fsrsCard : createEmptyCard(syn);
	const rec = scheduler().repeat(card, syn);
	const out: Record<number, number> = {};
	for (const g of [1, 2, 3, 4] as Grade[]) out[g] = now.ms + (rec[g].card.due.getTime() - syn.getTime());
	return out;
}
