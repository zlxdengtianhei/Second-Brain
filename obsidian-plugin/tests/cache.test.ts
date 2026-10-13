import { describe, expect, it } from "vitest";
import { type Checkpoint, type LogText, loadFull, loadLogs, makeEvent, parseStamp, stateToDict, type ReplayResult } from "../src/registry";
import { type CacheStore, ReviewService } from "../src/service";
import { EventStore } from "../src/vault/event-store";
import { fileText, fixture } from "./fixtures";
import { MemoryAdapter } from "./memory-adapter";

const VECTORS = fixture("vectors.json");

/** Everything a reader can observe, for comparing a checkpointed load with a full replay. */
function snapshot(r: ReplayResult) {
	return {
		states: [...r.states.entries()].sort().map(([c, s]) => [c, stateToDict(s), s.schedDay, s.lastDay, s.firstReview, s.events, s.recent, s.fsrsCard]),
		reviewDays: [...r.reviewDays].sort((a, b) => a - b),
		reviewsByDay: [...r.reviewsByDay.entries()].sort((a, b) => a[0] - b[0]),
		firstDay: [...r.firstDay.entries()].sort(),
		issues: r.issues,
	};
}

/** Checkpoints go through JSON, like any real store would serialize them. */
class MemoryCache implements CacheStore {
	data: string | null = null;
	saves = 0;
	async load() {
		return this.data ? JSON.parse(this.data) : null;
	}
	async save(cp: Checkpoint) {
		this.saves++;
		this.data = JSON.stringify(cp);
	}
}

function rng(seed: number) {
	let x = seed;
	return () => (x = (Math.imul(x, 1103515245) + 12345) >>> 0) / 2 ** 32;
}

function appendRandom(files: Map<string, string>, startMs: number, n: number, seed: number, devices = ["obs-a", "cli-b"]): number {
	const r = rng(seed);
	let ms = startMs;
	for (let i = 0; i < n; i++) {
		ms += [1, 7, 30, 600, 1440, 4000][Math.floor(r() * 6)] * 60_000;
		const device = devices[Math.floor(r() * devices.length)];
		const ev = makeEvent({ card: `ex_c${Math.floor(r() * 25)}`, device, now: { ms, offsetMin: 60 }, mode: "flip", rating: [1, 2, 3, 3, 3, 4][Math.floor(r() * 6)] });
		const name = `events-${device}.jsonl`;
		files.set(name, (files.get(name) ?? "") + JSON.stringify(ev) + "\n");
	}
	return ms;
}

const texts = (files: Map<string, string>): LogText[] => [...files.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1)).map(([name, text]) => ({ name, text }));

describe("checkpoint", () => {
	it("continuing from a checkpoint gives the same state as a full replay", () => {
		const files = new Map<string, string>();
		const bad = VECTORS.vectors.find((v: any) => v.name === "bad-lines-skipped");
		files.set("events-obs-a.jsonl", fileText(bad.files["events-obs-a.jsonl"]));
		let ms = appendRandom(files, Date.UTC(2026, 2, 3, 7), 300, 1);
		let cp = loadFull(texts(files)).checkpoint!;
		const same = (from: "cache" | "events") => {
			const out = loadLogs(texts(files), JSON.parse(JSON.stringify(cp)));
			expect(out.from).toBe(from);
			expect(snapshot(out.result)).toEqual(snapshot(loadFull(texts(files)).result));
			cp = out.checkpoint!;
		};
		same("cache");
		ms = appendRandom(files, ms, 120, 2, ["obs-a", "cli-b", "obs-new"]);
		same("cache");
		files.set("events-obs-a.jsonl", files.get("events-obs-a.jsonl") + "{broken\n");
		appendRandom(files, ms, 40, 3);
		same("cache");
	});
	it("falls back to a full replay when the logs changed in ways appends cannot explain", () => {
		const files = new Map<string, string>();
		const ms = appendRandom(files, Date.UTC(2026, 2, 3, 7), 200, 4);
		const cp = loadFull(texts(files)).checkpoint!;
		const check = (mutate: (f: Map<string, string>) => void) => {
			const f = new Map(files);
			mutate(f);
			const out = loadLogs(texts(f), JSON.parse(JSON.stringify(cp)));
			expect(out.from).toBe("events");
			expect(snapshot(out.result)).toEqual(snapshot(loadFull(texts(f)).result));
		};
		check((f) => appendRandom(f, ms - 30 * 86_400_000, 1, 9, ["obs-late"])); // an older event synced late
		check((f) => f.set("events-obs-a.jsonl", f.get("events-obs-a.jsonl")!.replace('"rating":3', '"rating":2'))); // edited in place
		check((f) => f.delete("events-cli-b.jsonl")); // a log disappeared
		check((f) => f.set("events-cli-b.jsonl", f.get("events-cli-b.jsonl")!.slice(0, -40))); // truncated
		expect(loadLogs(texts(files), { ...cp, meta: "other" }).from).toBe("events"); // parameters changed
		const torn = new Map(files);
		torn.set("events-cli-b.jsonl", torn.get("events-cli-b.jsonl") + '{"v":1,"ts":"2026-0');
		const out = loadLogs(texts(torn), cp);
		expect(out.from).toBe("events");
		expect(out.checkpoint).toBeNull(); // nothing is saved while a line is incomplete
	});
});

describe("ReviewService with a cache", () => {
	it("a second start uses the checkpoint, sees new lines from other devices, and matches a full replay", async () => {
		const a = new MemoryAdapter();
		const files = new Map<string, string>();
		const ms = appendRandom(files, Date.UTC(2026, 2, 3, 7), 150, 5);
		for (const [n, t] of files) a.files.set(`_data/review/${n}`, t);
		const cache = new MemoryCache();
		const cards = { all: () => [], get: () => undefined, version: 1 };
		const make = (device: string, c?: CacheStore) =>
			new ReviewService(cards, new EventStore(a, () => "_data/review", device, () => "jsonl"), {
				newPerDay: () => 10,
				studyDomains: () => [],
				clock: () => ({ ms: ms + 86_400_000, offsetMin: 60 }),
				cache: c,
			});
		const first = make("obs-me", cache);
		await first.reload();
		expect(first.loadedFrom).toBe("events");
		await first.record({ card: "ex_c3", mode: "flip", rating: 3 });
		const synced = new Map<string, string>();
		appendRandom(synced, ms + 86_400_000 + 60_000, 30, 6, ["obs-other"]);
		for (const [n, t] of synced) a.files.set(`_data/review/${n}`, t);
		const second = make("obs-me", cache);
		await second.reload();
		expect(second.loadedFrom).toBe("cache");
		const full = make("obs-me");
		await full.reload();
		expect(snapshot(second.replayResult())).toEqual(snapshot(full.replayResult()));
		expect(cache.saves).toBe(2);
	});
});

describe("new-card rank (shared with Python)", async () => {
	const { buildTaxonomy, rankCards } = await import("../src/vault/taxonomy");
	const { buildQueue, emptyResult } = await import("../src/registry");
	const RANK = fixture("rank.json");
	const tax = buildTaxonomy(RANK.taxonomy);
	const cards: any[] = RANK.cards.map((c: any) => ({ ...c, relations: (c.relations ?? []).map((r: string[]) => [r[0], r[1]]) }));
	for (const c of RANK.cases) {
		it(c.name, () => {
			const ranked: any[] = rankCards(cards, tax, c.study_domains);
			expect(Object.fromEntries(ranked.map((m: any) => [m.id, m.rank]))).toEqual(c.ranks);
			const q = buildQueue(ranked, emptyResult(), parseStamp(RANK.now), { newPerDay: RANK.new_per_day, domains: c.study_domains });
			expect(q.new.map((m: any) => m.id)).toEqual(c.new);
		});
	}
});
