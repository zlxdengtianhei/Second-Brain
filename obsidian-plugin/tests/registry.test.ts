import { describe, expect, it } from "vitest";
import {
	buildQueue,
	extractReviewItems,
	makeEvent,
	parseLogText,
	parseStamp,
	questionId,
	replay,
	type CardMeta,
	type Issue,
	type ReviewEvent,
	srSchedule,
	stateToDict,
	summarize,
} from "../src/registry";
import { fileText, fixture, fixtureText } from "./fixtures";

const VECTORS = fixture("vectors.json");
const QUEUE = fixture("queue.json");
const REL_TOL = 1e-6;

function load(files: Record<string, string | (string | object)[]>) {
	const events: ReviewEvent[] = [];
	const issues: Issue[] = [];
	const names = Object.keys(files).sort();
	for (const name of names) {
		const r = parseLogText(name, fileText(files[name]));
		events.push(...r.events);
		issues.push(...r.issues);
	}
	const result = replay(events);
	result.issues = issues;
	return result;
}

function checkFields(actual: Record<string, any>, expected: Record<string, any>, label: string) {
	for (const [key, want] of Object.entries(expected)) {
		if (key === "same_as") continue;
		const got = actual[key];
		if (key === "stability" || key === "difficulty") {
			expect(Math.abs(got - want) <= REL_TOL * Math.max(Math.abs(got), Math.abs(want)), `${label}.${key}: ${got} vs ${want}`).toBe(true);
		} else {
			expect(got, `${label}.${key}`).toEqual(want);
		}
	}
}

describe("replay vectors", () => {
	for (const v of VECTORS.vectors) {
		it(`${v.name}: hand-written expectations`, () => {
			const result = load(v.files);
			expect(result.issues.length, JSON.stringify(result.issues)).toBe(v.expect.issues);
			for (const [card, want] of Object.entries<any>(v.expect.cards)) {
				const st = result.states.get(card);
				expect(st, card).toBeDefined();
				const actual = stateToDict(st!);
				checkFields(actual, want, card);
				if (want.same_as) checkFields(actual, stateToDict(result.states.get(want.same_as)!), `${card}~${want.same_as}`);
			}
		});
		it(`${v.name}: matches the Python snapshot`, () => {
			const result = load(v.files);
			const snap = v.snapshot.cards as Record<string, any>;
			expect([...result.states.keys()].sort()).toEqual(Object.keys(snap).sort());
			for (const [card, want] of Object.entries(snap)) checkFields(stateToDict(result.states.get(card)!), want, card);
		});
	}
});

describe("question ids, review items, Spaced Repetition import", () => {
	for (const c of VECTORS.question_ids) {
		it(`question id of ${JSON.stringify(c.text)}`, () => {
			expect(questionId(c.text)).toBe(c.q ?? questionId(c.same_as));
		});
	}
	for (const c of VECTORS.review_items) {
		it(`review items of ${c.file}`, () => {
			const items = extractReviewItems(fixtureText(c.file));
			expect(items.map((i) => [i.question, i.answer])).toEqual(c.items);
			for (const i of items) expect(i.q).toBe(questionId(i.question));
		});
	}
	for (const c of VECTORS.sr_import) {
		it(`sr import ${c.name}`, () => {
			expect(srSchedule(c.text, c.frontmatter)).toEqual(c.seed);
		});
	}
});

function queueCards(): CardMeta[] {
	return QUEUE.cards.map((c: any) => ({
		id: c.id,
		title: c.title,
		domain: c.domain,
		kind: c.kind ?? "",
		status: c.status ?? "",
		rank: c.rank ?? 0,
		relations: (c.relations ?? []).map((r: string[]) => [r[0], r[1]]),
	}));
}

const snake = (o: Record<string, number>) =>
	Object.fromEntries(Object.entries(o).map(([k, v]) => [k.replace(/[A-Z]/g, (m) => "_" + m.toLowerCase()), v]));

describe("status and queue", () => {
	const now = parseStamp(QUEUE.now);
	it("summary", () => {
		const s = summarize(queueCards(), load(QUEUE.files), now);
		const want = QUEUE.summary;
		expect(s.today).toBe(want.today);
		expect(snake(s.totals as any)).toEqual(want.totals);
		expect(Object.fromEntries([...s.domains].map(([d, c]) => [d, snake(c as any)]))).toEqual(want.domains);
		expect([s.newToday, s.reviewsToday, s.streak]).toEqual([want.new_today, want.reviews_today, want.streak]);
		expect(s.orphans).toEqual(want.orphans);
	});
	for (const c of QUEUE.queues) {
		it(`queue ${c.name}`, () => {
			const q = buildQueue(queueCards(), load(QUEUE.files), now, { newPerDay: c.new_per_day, domains: c.domains });
			expect(q.due.map((m) => m.id)).toEqual(c.due);
			expect(q.new.map((m) => m.id)).toEqual(c.new);
			expect([q.newQuota, q.newAvailable, q.cycleBreaks]).toEqual([c.new_quota, c.new_available, c.cycle_breaks]);
		});
	}
});

describe("writing", () => {
	it("makeEvent takes the minimum item rating and validates", () => {
		const now = { ms: Date.UTC(2026, 2, 2, 8, 0, 0, 123), offsetMin: 60 };
		const ev = makeEvent({ card: "ex_a", device: "obs-1", now, mode: "qa", items: [{ q: "h:1", rating: 3 }, { q: "h:2", rating: 2 }] });
		expect(ev.rating).toBe(2);
		expect(ev.ts).toBe("2026-03-02T09:00:00.123+01:00");
		expect(() => makeEvent({ card: "ex_a", device: "obs-1", now, mode: "flip", rating: 5 })).toThrow();
		expect(() => makeEvent({ card: "ex_a", device: "bad id", now, mode: "flip", rating: 3 })).toThrow();
	});
	it("makeEvent round-trips through the parser", () => {
		const now = { ms: Date.UTC(2026, 2, 2, 8), offsetMin: -300 };
		const line = JSON.stringify(makeEvent({ card: "ex_b", device: "obs-1", now, mode: "flip", rating: 4 }));
		const { events, issues } = parseLogText("events-obs-1.jsonl", line);
		expect(issues).toEqual([]);
		expect(events[0].ts).toEqual(now);
	});
});
