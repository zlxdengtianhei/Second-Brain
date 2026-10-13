// Review events: format v1, parsing, validation and time helpers.
// Specification: docs/review-registry.md. Python twin: src/secondbrain/review_registry.py.

export const SCHEMA_VERSION = 1;
/**
 * Revision of the parsing and replay rules. Increment it here and in review_registry.py whenever a
 * change makes any log parse or replay differently; state caches written under another revision are
 * ignored. 2: timestamps and SR dates accept ASCII digits only.
 */
export const RULES_VERSION = 2;
export const DAY_START_HOUR = 4;
export const DAY_MS = 86_400_000;
export const MODE_IMPORT = "import";

const DEVICE_RE = /^[A-Za-z0-9_-]{1,64}$/;
const TS_RE =
	/^(\d{4})-(\d{2})-(\d{2})[Tt ](\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?([Zz]|[+-]\d{2}:\d{2})$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
export const LOG_NAME_RE = /^events-.+\.(jsonl|md)$/;
export const MD_HEADER = "---\nformat: secondbrain-review-log\n---\n";

/** A point in time together with the UTC offset it was written in. */
export interface Stamp {
	/** UTC milliseconds since the epoch. */
	ms: number;
	/** Offset from UTC in minutes (+60 for +01:00). */
	offsetMin: number;
}

export interface ImportSeed {
	from: "sr-sm2" | "sr-fsrs";
	due: string;
	interval: number;
	ease?: number;
	stability?: number;
	difficulty?: number;
	last_review?: string;
}

export interface ReviewEvent {
	ts: Stamp;
	device: string;
	card: string;
	mode: string;
	/** Card rating 1-4; null only for import events. */
	rating: number | null;
	raw: Record<string, unknown>;
	source: string;
	line: number;
	importSeed?: ImportSeed;
}

export interface Issue {
	source: string;
	line: number;
	reason: string;
}

export class EventError extends Error {}

// ---------------------------------------------------------------------------
// Time
// ---------------------------------------------------------------------------
export function parseStamp(value: unknown): Stamp {
	if (typeof value !== "string") throw new EventError(`ts is not RFC 3339 with an offset: ${String(value)}`);
	const m = TS_RE.exec(value);
	if (!m) throw new EventError(`ts is not RFC 3339 with an offset: ${value}`);
	const [y, mo, d, h, mi, s] = [1, 2, 3, 4, 5, 6].map((i) => Number(m[i]));
	const frac = Number((m[7] ?? "").padEnd(3, "0").slice(0, 3));
	const off = m[8];
	if (off !== "Z" && off !== "z" && (Number(off.slice(1, 3)) > 23 || Number(off.slice(4, 6)) > 59)) {
		throw new EventError(`ts has an invalid UTC offset: ${value}`);
	}
	const offsetMin = off === "Z" || off === "z" ? 0 : (off[0] === "+" ? 1 : -1) * (Number(off.slice(1, 3)) * 60 + Number(off.slice(4, 6)));
	const wall = Date.UTC(y, mo - 1, d, h, mi, s, frac);
	const check = new Date(wall);
	if (
		check.getUTCFullYear() !== y || check.getUTCMonth() !== mo - 1 || check.getUTCDate() !== d ||
		h > 23 || mi > 59 || s > 59 || Math.abs(offsetMin) >= 24 * 60
	) {
		throw new EventError(`ts is not a valid time: ${value}`);
	}
	return { ms: wall - offsetMin * 60_000, offsetMin };
}

/** Learning-day index of a stamp, in the stamp's own offset. */
export function learningDay(stamp: Stamp, dayStartHour = DAY_START_HOUR): number {
	return Math.floor((stamp.ms + stamp.offsetMin * 60_000 - dayStartHour * 3_600_000) / DAY_MS);
}

/** The same instant seen in another offset. */
export function inOffset(ms: number, offsetMin: number): Stamp {
	return { ms, offsetMin };
}

/** A stamp for "now" in the local time zone of this device. */
export function localNow(date: Date = new Date()): Stamp {
	return { ms: date.getTime(), offsetMin: -date.getTimezoneOffset() };
}

export function dayToDate(day: number): string {
	return new Date(day * DAY_MS).toISOString().slice(0, 10);
}

export function formatUtc(ms: number): string {
	return new Date(ms).toISOString();
}

export function formatLocal(stamp: Stamp): string {
	const wall = new Date(stamp.ms + stamp.offsetMin * 60_000).toISOString().slice(0, 23);
	const sign = stamp.offsetMin >= 0 ? "+" : "-";
	const abs = Math.abs(stamp.offsetMin);
	return `${wall}${sign}${String(Math.floor(abs / 60)).padStart(2, "0")}:${String(abs % 60).padStart(2, "0")}`;
}

/** Start of a learning day as a stamp in the given offset. */
export function seedTime(value: string, offsetMin: number, dayStartHour = DAY_START_HOUR): Stamp {
	if (DATE_RE.test(value)) {
		const [y, mo, d] = value.split("-").map(Number);
		const wall = Date.UTC(y, mo - 1, d, dayStartHour);
		return { ms: wall - offsetMin * 60_000, offsetMin };
	}
	return parseStamp(value);
}

/** Compare strings by Unicode code point (same order as Python). */
export function cmpCodePoint(a: string, b: string): number {
	if (a === b) return 0;
	const n = Math.min(a.length, b.length);
	for (let i = 0; i < n; i++) {
		const x = a.codePointAt(i)!;
		const y = b.codePointAt(i)!;
		if (x !== y) return x < y ? -1 : 1;
		if (x > 0xffff) i++;
	}
	return a.length < b.length ? -1 : a.length > b.length ? 1 : 0;
}

// ---------------------------------------------------------------------------
// Questions
// ---------------------------------------------------------------------------
export function normalizeQuestion(text: string): string {
	return text.normalize("NFKC").toLowerCase().split(/\s+/u).filter(Boolean).join(" ");
}

/** "h:" + FNV-1a 32-bit hash (8 hex digits) of the normalized question. */
export function questionId(text: string): string {
	let h = 0x811c9dc5;
	for (const b of new TextEncoder().encode(normalizeQuestion(text))) {
		h ^= b;
		h = Math.imul(h, 0x01000193) >>> 0;
	}
	return `h:${h.toString(16).padStart(8, "0")}`;
}

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------
function isObject(v: unknown): v is Record<string, unknown> {
	return typeof v === "object" && v !== null && !Array.isArray(v);
}

function ratingOf(v: unknown, where: string): number {
	if (typeof v !== "number" || !Number.isInteger(v) || v < 1 || v > 4) {
		throw new EventError(`${where} must be an integer 1-4`);
	}
	return v;
}

function nonEmpty(obj: Record<string, unknown>, key: string): string {
	const v = obj[key];
	if (typeof v !== "string" || v.trim() === "") throw new EventError(`${key} must be a non-empty string`);
	return v;
}

function positiveNumber(v: unknown, where: string, allowZero = false): number {
	if (typeof v !== "number" || !Number.isFinite(v) || (allowZero ? v < 0 : v <= 0)) {
		throw new EventError(`${where} must be a ${allowZero ? "number >= 0" : "positive number"}`);
	}
	return v;
}

function checkSeedTime(v: unknown, key: string): void {
	if (typeof v !== "string" || !(DATE_RE.test(v) || TS_RE.test(v))) {
		throw new EventError(`import.${key} must be a YYYY-MM-DD date or an RFC 3339 timestamp`);
	}
	if (DATE_RE.test(v)) {
		const [y, mo, d] = v.split("-").map(Number);
		const dt = new Date(Date.UTC(y, mo - 1, d));
		if (dt.getUTCFullYear() !== y || dt.getUTCMonth() !== mo - 1 || dt.getUTCDate() !== d) {
			throw new EventError(`import.${key} is not a valid date`);
		}
	} else {
		parseStamp(v);
	}
}

function parseImport(seed: unknown): ImportSeed {
	if (!isObject(seed)) throw new EventError("import must be an object");
	const from = seed.from;
	if (from !== "sr-sm2" && from !== "sr-fsrs") throw new EventError("import.from must be sr-sm2 or sr-fsrs");
	positiveNumber(seed.interval, "import.interval", true);
	checkSeedTime(seed.due, "due");
	if (seed.last_review !== undefined && seed.last_review !== null && seed.last_review !== "") {
		checkSeedTime(seed.last_review, "last_review");
	}
	if (from === "sr-sm2") positiveNumber(seed.ease, "import.ease");
	else {
		positiveNumber(seed.stability, "import.stability");
		positiveNumber(seed.difficulty, "import.difficulty");
	}
	return { ...(seed as unknown as ImportSeed) };
}

/** Check an import seed with the same rules the event parser applies. Throws EventError. */
export function validateImportSeed(seed: unknown): ImportSeed {
	return parseImport(seed);
}

/** Validate one decoded JSON value and return an event. Throws EventError. */
export function parseEvent(obj: unknown, source = "", line = 0): ReviewEvent {
	if (!isObject(obj)) throw new EventError("event must be a JSON object");
	if (obj.v !== SCHEMA_VERSION) throw new EventError(`unsupported version: ${JSON.stringify(obj.v)}`);
	const ts = parseStamp(obj.ts);
	const device = nonEmpty(obj, "device");
	if (!DEVICE_RE.test(device)) throw new EventError(`device id is not allowed: ${device}`);
	const card = nonEmpty(obj, "card");
	const mode = nonEmpty(obj, "mode");
	let rating: number | null = null;
	let importSeed: ImportSeed | undefined;
	if (mode === MODE_IMPORT) {
		importSeed = parseImport(obj.import);
	} else {
		const items = obj.items;
		const itemRatings: number[] = [];
		if (items !== undefined) {
			if (!Array.isArray(items) || items.length === 0) throw new EventError("items must be a non-empty array");
			items.forEach((item, i) => {
				if (!isObject(item)) throw new EventError(`items[${i}] must be an object`);
				if (typeof item.q !== "string" || item.q === "") throw new EventError(`items[${i}].q must be a non-empty string`);
				itemRatings.push(ratingOf(item.rating, `items[${i}].rating`));
			});
		}
		if ("rating" in obj) rating = ratingOf(obj.rating, "rating");
		else if (itemRatings.length) rating = Math.min(...itemRatings);
		else throw new EventError("rating is required when items are absent");
	}
	return { ts, device, card, mode, rating, raw: obj, source, line, importSeed };
}

/** Parse one log file's text (.jsonl or .md), or the tail of it that starts at line `firstLine`. */
export function parseLogText(name: string, text: string, firstLine = 1): { events: ReviewEvent[]; issues: Issue[] } {
	const isMd = name.endsWith(".md");
	const events: ReviewEvent[] = [];
	const issues: Issue[] = [];
	const lines = text.replace(/^\uFEFF+/, "").split("\n");
	lines.forEach((raw, idx) => {
		const no = idx + firstLine;
		const line = raw.trim();
		if (!line) return;
		if (isMd && !line.startsWith("{")) return;
		let obj: unknown;
		try {
			obj = JSON.parse(line);
		} catch (e) {
			issues.push({ source: name, line: no, reason: `invalid JSON: ${(e as Error).message}` });
			return;
		}
		try {
			events.push(parseEvent(obj, name, no));
		} catch (e) {
			// One bad line must never stop the replay of the others.
			issues.push({ source: name, line: no, reason: e instanceof EventError ? e.message : `invalid event: ${String(e)}` });
		}
	});
	return { events, issues };
}

/** Remove duplicates (same device, card, instant; first read wins) and sort. */
export function orderEvents(events: Iterable<ReviewEvent>): ReviewEvent[] {
	const seen = new Set<string>();
	const unique: ReviewEvent[] = [];
	for (const ev of events) {
		const key = `${ev.device}\u0000${ev.card}\u0000${ev.ts.ms}`;
		if (seen.has(key)) continue;
		seen.add(key);
		unique.push(ev);
	}
	return unique.sort(
		(a, b) =>
			a.ts.ms - b.ts.ms ||
			cmpCodePoint(a.device, b.device) ||
			cmpCodePoint(a.card, b.card) ||
			cmpCodePoint(a.source, b.source) ||
			a.line - b.line,
	);
}

// ---------------------------------------------------------------------------
// Writing
// ---------------------------------------------------------------------------
export interface EventItem {
	q: string;
	rating: number;
	verdict?: "hit" | "partial" | "miss";
	grader?: string;
	answer?: string;
	feedback?: string;
	missing?: string[];
}

export interface NewEvent {
	card: string;
	device: string;
	now: Stamp;
	mode: string;
	rating?: number;
	items?: EventItem[];
	grader?: string;
	answer?: string;
	feedback?: string;
	ms?: number;
	importSeed?: ImportSeed;
}

/** Build and validate an event object ready to be written. */
export function makeEvent(e: NewEvent): Record<string, unknown> {
	const obj: Record<string, unknown> = {
		v: SCHEMA_VERSION,
		ts: formatLocal(e.now),
		device: e.device,
		card: e.card,
		mode: e.mode,
	};
	if (e.mode === MODE_IMPORT) {
		obj.import = { ...e.importSeed };
	} else {
		let rating = e.rating;
		if (rating === undefined && e.items?.length) rating = Math.min(...e.items.map((i) => i.rating));
		if (rating !== undefined) obj.rating = rating;
		if (e.items?.length) obj.items = e.items.map((i) => ({ ...i }));
	}
	if (e.grader !== undefined) obj.grader = e.grader;
	if (e.answer !== undefined) obj.answer = e.answer;
	if (e.feedback !== undefined) obj.feedback = e.feedback;
	if (e.ms !== undefined) obj.ms = Math.round(e.ms);
	parseEvent(obj);
	return obj;
}

export function serializeEvent(obj: Record<string, unknown>): string {
	return JSON.stringify(obj) + "\n";
}

export function isValidDevice(device: string): boolean {
	return DEVICE_RE.test(device);
}
