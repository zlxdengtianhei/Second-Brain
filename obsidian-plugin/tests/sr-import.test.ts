import { describe, expect, it } from "vitest";
import { parseStamp } from "../src/registry";
import { ReviewService } from "../src/service";
import { planSrImport } from "../src/sr-import";
import { EventStore } from "../src/vault/event-store";
import { MemoryAdapter } from "./memory-adapter";

describe("Spaced Repetition import", () => {
	const cards = [
		{ id: "ex_a", text: "Q::A <!--SR:!2026-03-20,5,250-->" },
		{ id: "ex_b", text: "no schedule" },
		{ id: "ex_c", text: "Q::A", fm: { "sr-due": "2026-03-22", "sr-interval": 7, "sr-ease": 270 } },
		{ id: "ex_d", text: "Q::A <!--SR:!2026-03-21,2,230-->" },
	];
	it("plans one import per card with data and skips cards that already have events", async () => {
		const plan = await planSrImport(cards, {
			frontmatter: (c) => c.fm,
			read: async (c) => c.text,
			hasEvents: (c) => c.id === "ex_d",
		});
		expect(plan.found.map((f) => [f.card.id, f.seed.from, f.seed.interval])).toEqual([["ex_a", "sr-sm2", 5], ["ex_c", "sr-sm2", 7]]);
		expect(plan.skipped).toBe(1);
	});
	it("writes all import events with one append and replays them as seeded review cards", async () => {
		const a = new MemoryAdapter();
		const index = { all: () => [], get: () => undefined, version: 1 };
		const svc = new ReviewService(index, new EventStore(a, () => "_data/review", "obs-imp", () => "jsonl"), {
			newPerDay: () => 10,
			studyDomains: () => [],
			clock: () => parseStamp("2026-03-15T10:00:00.000+01:00"),
		});
		await svc.reload();
		const n = await svc.recordBatch([
			{ card: "ex_a", mode: "import", importSeed: { from: "sr-sm2", due: "2026-03-20", interval: 5, ease: 250 } },
			{ card: "ex_c", mode: "import", importSeed: { from: "sr-sm2", due: "2026-03-22", interval: 7, ease: 270 } },
		]);
		expect(n).toBe(2);
		expect(a.writes).toBe(1);
		expect(svc.state("ex_a")).toMatchObject({ state: "review", stability: 5, reps: 0 });
		expect(new Date(svc.state("ex_a")!.due).toISOString()).toBe("2026-03-20T03:00:00.000Z");
		expect(svc.summary().reviewsToday).toBe(0);
	});
});
