// State cache (docs/review-registry.md section 4.1): the replay result plus how far each log was read.
// A load starts from the checkpoint and parses only appended lines; anything unexpected means a full replay.
import { type Card, FSRSVersion, State } from "ts-fsrs";
import { cmpCodePoint, DAY_START_HOUR, type Issue, orderEvents, parseLogText, type ReviewEvent, SCHEMA_VERSION } from "./events";
import { applyEvent, type CardState, countEvent, emptyResult, FSRS_PARAMETERS, type ReplayResult } from "./scheduler";

export const CACHE_FORMAT = 1;

export interface LogText {
	name: string;
	text: string;
}

/** How much of a log file the checkpoint has consumed: UTF-16 length, line count, FNV-1a hash of that prefix. */
export interface FileMark {
	chars: number;
	lines: number;
	hash: number;
}

type Watermark = [ms: number, device: string, card: string, source: string, line: number];

interface SavedState extends Omit<CardState, "fsrsCard"> {
	fsrsCard: Omit<Card, "due" | "last_review"> & { due: number; last_review: number | null };
}

export interface Checkpoint {
	meta: string;
	files: Record<string, FileMark>;
	watermark: Watermark | null;
	states: SavedState[];
	reviewDays: number[];
	reviewsByDay: [number, number][];
	firstDay: [string, number][];
	issues: Issue[];
}

export interface LoadOutcome {
	result: ReplayResult;
	/** null when a line is still incomplete: nothing should be saved. */
	checkpoint: Checkpoint | null;
	from: "events" | "cache";
	watermark: Watermark | null;
}

export function cacheMeta(dayStartHour = DAY_START_HOUR): string {
	return JSON.stringify({ cache: CACHE_FORMAT, schema: SCHEMA_VERSION, dayStart: dayStartHour, fsrs: FSRSVersion, params: FSRS_PARAMETERS });
}

const FNV_OFFSET = 0x811c9dc5;

/** FNV-1a over UTF-16 code units of text[from, to), continuing from `h`. */
export function fnv(text: string, from = 0, to = text.length, h = FNV_OFFSET): number {
	for (let i = from; i < to; i++) {
		h ^= text.charCodeAt(i);
		h = Math.imul(h, 0x01000193) >>> 0;
	}
	return h >>> 0;
}

function countNewlines(text: string, from = 0): number {
	let n = 0;
	for (let i = text.indexOf("\n", from); i >= 0; i = text.indexOf("\n", i + 1)) n++;
	return n;
}

const keyOf = (ev: ReviewEvent): Watermark => [ev.ts.ms, ev.device, ev.card, ev.source, ev.line];

function sortIssues(issues: Issue[]): Issue[] {
	return issues.sort((a, b) => cmpCodePoint(a.source, b.source) || a.line - b.line);
}

function saveState(s: CardState): SavedState {
	return {
		...s,
		recent: s.recent.map((r) => ({ ...r, missing: [...r.missing] })),
		fsrsCard: { ...s.fsrsCard, due: s.fsrsCard.due.getTime(), last_review: s.fsrsCard.last_review ? s.fsrsCard.last_review.getTime() : null },
	};
}

function restoreState(s: SavedState): CardState {
	const c = s.fsrsCard;
	return {
		...s,
		recent: s.recent.map((r) => ({ ...r, missing: [...r.missing] })),
		fsrsCard: { ...c, state: c.state as State, due: new Date(c.due), last_review: c.last_review === null ? undefined : new Date(c.last_review) } as Card,
	};
}

export function toCheckpoint(result: ReplayResult, files: Record<string, FileMark>, watermark: Watermark | null, dayStartHour = DAY_START_HOUR): Checkpoint {
	return {
		meta: cacheMeta(dayStartHour),
		files,
		watermark,
		states: [...result.states.values()].map(saveState),
		reviewDays: [...result.reviewDays],
		reviewsByDay: [...result.reviewsByDay.entries()],
		firstDay: [...result.firstDay.entries()],
		issues: result.issues.map((i) => ({ ...i })),
	};
}

function fromCheckpoint(cp: Checkpoint): ReplayResult {
	return {
		states: new Map(cp.states.map((s) => [s.card, restoreState(s)])),
		issues: cp.issues.map((i) => ({ ...i })),
		reviewDays: new Set(cp.reviewDays),
		reviewsByDay: new Map(cp.reviewsByDay),
		firstDay: new Map(cp.firstDay),
	};
}

/** Parse and replay everything. */
export function loadFull(files: LogText[], dayStartHour = DAY_START_HOUR): LoadOutcome {
	const events: ReviewEvent[] = [];
	const issues: Issue[] = [];
	const marks: Record<string, FileMark> = {};
	let clean = true;
	for (const f of files) {
		const r = parseLogText(f.name, f.text);
		events.push(...r.events);
		issues.push(...r.issues);
		marks[f.name] = { chars: f.text.length, lines: countNewlines(f.text), hash: fnv(f.text) };
		clean &&= f.text === "" || f.text.endsWith("\n");
	}
	const ordered = orderEvents(events);
	const result = emptyResult();
	for (const ev of ordered) {
		result.states.set(ev.card, applyEvent(result.states.get(ev.card), ev, dayStartHour));
		countEvent(result, ev, dayStartHour);
	}
	result.issues = sortIssues(issues);
	const watermark = ordered.length ? keyOf(ordered[ordered.length - 1]) : null;
	return { result, checkpoint: clean ? toCheckpoint(result, marks, watermark, dayStartHour) : null, from: "events", watermark };
}

/** Continue from a checkpoint; null when a full replay is needed. */
export function loadIncremental(files: LogText[], cp: Checkpoint, dayStartHour = DAY_START_HOUR): LoadOutcome | null {
	if (cp.meta !== cacheMeta(dayStartHour)) return null;
	const names = new Set(files.map((f) => f.name));
	if (Object.keys(cp.files).some((n) => !names.has(n))) return null; // a log disappeared
	const newEvents: ReviewEvent[] = [];
	const newIssues: Issue[] = [];
	const marks: Record<string, FileMark> = {};
	for (const f of files) {
		const mark = cp.files[f.name];
		let start = 0;
		let line0 = 0;
		let h = FNV_OFFSET;
		if (mark) {
			if (f.text.length < mark.chars) return null; // truncated
			h = fnv(f.text, 0, mark.chars);
			if (h !== mark.hash) return null; // the part already read changed
			start = mark.chars;
			line0 = mark.lines;
		}
		const rest = f.text.slice(start);
		if (rest && !rest.endsWith("\n")) return null; // a line is still being written or was cut off
		if (rest) {
			const r = parseLogText(f.name, rest, line0 + 1);
			newEvents.push(...r.events);
			newIssues.push(...r.issues);
		}
		marks[f.name] = { chars: f.text.length, lines: line0 + countNewlines(rest), hash: fnv(f.text, start, f.text.length, h) };
	}
	const ordered = orderEvents(newEvents);
	if (ordered.length && cp.watermark && ordered[0].ts.ms <= cp.watermark[0]) return null; // a late, older event
	const result = fromCheckpoint(cp);
	for (const ev of ordered) {
		result.states.set(ev.card, applyEvent(result.states.get(ev.card), ev, dayStartHour));
		countEvent(result, ev, dayStartHour);
	}
	result.issues = sortIssues([...result.issues, ...newIssues]);
	const watermark = ordered.length ? keyOf(ordered[ordered.length - 1]) : cp.watermark;
	return { result, checkpoint: toCheckpoint(result, marks, watermark, dayStartHour), from: "cache", watermark };
}

/** Use the checkpoint when it is valid, otherwise replay everything. */
export function loadLogs(files: LogText[], cp: Checkpoint | null | undefined, dayStartHour = DAY_START_HOUR): LoadOutcome {
	if (cp && typeof cp === "object" && cp.meta === cacheMeta(dayStartHour)) {
		try {
			const out = loadIncremental(files, cp, dayStartHour);
			if (out) return out;
		} catch (e) {
			console.warn("[secondbrain-review] review cache unusable, replaying all logs", e);
		}
	}
	return loadFull(files, dayStartHour);
}
