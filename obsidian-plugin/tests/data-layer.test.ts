import { describe, expect, it } from "vitest";
import { parseLogText, parseStamp, stateToDict } from "../src/registry";
import { ReviewService } from "../src/service";
import { cardFromFrontmatter, type IndexedCard } from "../src/vault/card-index";
import { EventStore, type LogAdapter, newDeviceId } from "../src/vault/event-store";
import { buildTaxonomy, domainMapTitles, rankFor } from "../src/vault/taxonomy";
import { fileText, fixture } from "./fixtures";

/** In-memory stand-in for Obsidian's DataAdapter. */
export class MemoryAdapter implements LogAdapter {
	files = new Map<string, string>();
	dirs = new Set<string>();
	writes = 0;
	async exists(p: string) {
		return this.files.has(p) || this.dirs.has(p) || [...this.files.keys()].some((f) => f.startsWith(p + "/"));
	}
	async list(p: string) {
		const files = [...this.files.keys()].filter((f) => f.startsWith(p + "/") && !f.slice(p.length + 1).includes("/"));
		return { files, folders: [] };
	}
	async read(p: string) {
		const v = this.files.get(p);
		if (v === undefined) throw new Error("ENOENT " + p);
		return v;
	}
	async append(p: string, d: string) {
		this.writes++;
		this.files.set(p, (this.files.get(p) ?? "") + d);
	}
	async write(p: string, d: string) {
		this.writes++;
		this.files.set(p, d);
	}
	async mkdir(p: string) {
		this.dirs.add(p);
	}
}

const QUEUE = fixture("queue.json");

describe("cardFromFrontmatter", () => {
	it("reads id, relations with tier, and falls back to the file name for the title", () => {
		const c = cardFromFrontmatter(
			{ id: "ex_a", domain: "algo", kind: "concept", pillar: "P", topic: "T", relations: [{ type: "depends_on", target: "ex_b", tier: "extension" }, { type: "x" }, "junk"] },
			"10_Knowledge/algo/Arrays.md",
		)!;
		expect(c.title).toBe("Arrays");
		expect(c.relations).toEqual([["depends_on", "ex_b"]]);
		expect(c.relationList[0].tier).toBe("extension");
	});
	it("returns null for notes without an id", () => {
		expect(cardFromFrontmatter({ title: "x" }, "a.md")).toBeNull();
		expect(cardFromFrontmatter(undefined, "a.md")).toBeNull();
	});
});

describe("taxonomy", () => {
	const tax = buildTaxonomy([
		{ path: "t/Algorithms.yaml", data: { title: "Algorithms", pillars: [{ name: "Searching", order: 2, why: "Find things fast.", topics: ["Linear", "Divide and conquer search"] }, { name: "Basics", order: 1, why: "Ground.", topics: ["Arrays"] }] } },
		{ path: "t/Broken.yaml", data: "not a mapping" },
	]);
	it("indexes pillars by name with their map and order", () => {
		expect(tax.pillars.get("Searching")).toMatchObject({ mapTitle: "Algorithms", order: 2, why: "Find things fast." });
		expect(tax.maps.get("Algorithms")!.map((p) => p.name)).toEqual(["Basics", "Searching"]);
		expect(tax.issues.length).toBe(1);
	});
	it("ranks by study domain, pillar order and topic position", () => {
		const order = new Map([["algo", 0], ["net", 1]]);
		const a = rankFor({ domain: "algo", pillar: "Searching", topic: "Divide and conquer search" }, tax, order);
		const b = rankFor({ domain: "algo", pillar: "Basics", topic: "Arrays" }, tax, order);
		const c = rankFor({ domain: "net", pillar: "Basics", topic: "Arrays" }, tax, order);
		const d = rankFor({ domain: "algo", pillar: "Unknown", topic: "" }, tax, order);
		expect(b < a && a < d && d < c).toBe(true);
	});
	it("finds a domain's map title from its cards", () => {
		const t = domainMapTitles([{ domain: "algo", pillar: "Searching" }, { domain: "algo", pillar: "Basics" }, { domain: "x", pillar: "none" }], tax);
		expect(t.get("algo")).toBe("Algorithms");
		expect(t.has("x")).toBe(false);
	});
});

describe("EventStore", () => {
	it("creates the log, repairs a torn last line, and writes the .md header once", async () => {
		const a = new MemoryAdapter();
		const store = new EventStore(a, () => "_data/review", "obs-abc123", () => "jsonl");
		const ev = (min: number) => ({ v: 1, ts: `2026-03-02T09:${String(min).padStart(2, "0")}:00.000+01:00`, device: "obs-abc123", card: "ex_a", mode: "flip", rating: 3 });
		await store.append(ev(0));
		a.files.set("_data/review/events-obs-abc123.jsonl", a.files.get("_data/review/events-obs-abc123.jsonl") + '{"v":1,"ts":"2026-0');
		const fresh = new EventStore(a, () => "_data/review", "obs-abc123", () => "jsonl");
		await fresh.append(ev(10));
		const { events, issues } = await fresh.readAll();
		expect(events.length).toBe(2);
		expect(issues.map((i) => i.line)).toEqual([2]);

		const md = new EventStore(a, () => "_data/review", "obs-m", () => "md");
		await md.append({ ...ev(20), device: "obs-m" });
		await md.append({ ...ev(30), device: "obs-m" });
		const text = a.files.get("_data/review/events-obs-m.md")!;
		expect(text.startsWith("---\nformat: secondbrain-review-log\n---\n")).toBe(true);
		expect(parseLogText("events-obs-m.md", text).events.length).toBe(2);
	});
	it("serializes concurrent appends", async () => {
		const a = new MemoryAdapter();
		const store = new EventStore(a, () => "_data/review", "obs-c", () => "jsonl");
		await Promise.all(
			Array.from({ length: 30 }, (_, i) =>
				store.append({ v: 1, ts: new Date(Date.UTC(2026, 2, 2, 8, 0, i)).toISOString(), device: "obs-c", card: `ex_${i}`, mode: "flip", rating: 3 }),
			),
		);
		const { events, issues } = await store.readAll();
		expect(issues).toEqual([]);
		expect(events.length).toBe(30);
	});
	it("only treats events-*.jsonl|md directly in the folder as logs", () => {
		const store = new EventStore(new MemoryAdapter(), () => "_data/review/", "obs-x", () => "jsonl");
		expect(store.isLogPath("_data/review/events-obs-y.jsonl")).toBe(true);
		expect(store.isLogPath("_data/review/state.json")).toBe(false);
		expect(store.isLogPath("_data/review/sub/events-a.jsonl")).toBe(false);
	});
	it("generates device ids the format accepts", () => {
		expect(newDeviceId()).toMatch(/^obs-[0-9a-f]{6}$/);
		expect(() => new EventStore(new MemoryAdapter(), () => "d", "bad id", () => "jsonl")).toThrow();
	});
});

describe("ReviewService", () => {
	function setup() {
		const a = new MemoryAdapter();
		for (const [name, content] of Object.entries<any>(QUEUE.files)) a.files.set(`_data/review/${name}`, fileText(content));
		const cards: IndexedCard[] = QUEUE.cards.map((c: any) => ({
			id: c.id, title: c.title, domain: c.domain, kind: c.kind ?? "", status: c.status ?? "", path: `${c.id}.md`,
			pillar: "", topic: "", relationList: [], relations: (c.relations ?? []).map((r: string[]) => [r[0], r[1]]),
		}));
		const source = { all: () => cards, get: (id: string) => cards.find((c) => c.id === id), version: 1 };
		const store = new EventStore(a, () => "_data/review", "obs-svc", () => "jsonl");
		let now = parseStamp(QUEUE.now);
		const svc = new ReviewService(source, store, { newPerDay: () => 10, studyDomains: () => [], clock: () => now });
		return { a, svc, setNow: (s: string) => (now = parseStamp(s)) };
	}
	it("summarizes and queues after reload, as the shared fixture expects", async () => {
		const { svc } = setup();
		await svc.reload();
		expect(svc.summary().totals.due).toBe(QUEUE.summary.totals.due);
		expect(svc.queue().due.map((c) => c.id)).toEqual(QUEUE.queues[0].due);
	});
	it("records a review incrementally with the same result as a full replay", async () => {
		const { a, svc } = setup();
		await svc.reload();
		const st = await svc.record({ card: "ex_binary_search", mode: "qa", items: [{ q: "h:1", rating: 3 }, { q: "h:2", rating: 2 }], grader: "llm:test" });
		expect(st?.state).toBe("learning");
		expect(svc.summary().newToday).toBe(2);
		const incremental = stateToDict(svc.state("ex_binary_search")!);
		const again = new ReviewService(svc.cards, new EventStore(a, () => "_data/review", "obs-other", () => "jsonl"), { newPerDay: () => 10, studyDomains: () => [] });
		await again.reload();
		expect(stateToDict(again.state("ex_binary_search")!)).toEqual(incremental);
		expect(a.files.get("_data/review/events-obs-svc.jsonl")).toContain('"rating":2');
	});
	it("falls back to a full replay when a recorded event is older than the newest one", async () => {
		const { svc } = setup();
		await svc.reload();
		await svc.record({ card: "ex_tls", mode: "flip", rating: 3, now: parseStamp("2026-03-09T09:00:00.000+01:00") });
		expect(svc.state("ex_tls")?.reps).toBe(1);
		expect(svc.state("ex_tcp")?.reps).toBe(1);
	});
	it("refuses invalid events before writing", async () => {
		const { a, svc } = setup();
		await svc.reload();
		const before = a.writes;
		await expect(svc.record({ card: "ex_tls", mode: "flip", rating: 7 })).rejects.toThrow();
		expect(a.writes).toBe(before);
	});
});
