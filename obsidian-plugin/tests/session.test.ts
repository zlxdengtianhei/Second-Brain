import { describe, expect, it } from "vitest";
import { parseEvent, parseStamp, replay } from "../src/registry";
import { formatInterval, SessionQueue } from "../src/session";

const units = { min: "{n}m", hour: "{n}h", day: "{n}d", month: "{n}mo", year: "{n}y" };

describe("SessionQueue", () => {
	const ev = (card: string, ts: string, rating: number) => parseEvent({ v: 1, ts, device: "obs-a", card, mode: "flip", rating });
	it("runs due cards before new ones and brings back cards still learning today", () => {
		const q = new SessionQueue([{ id: "d1" }], [{ id: "n1" }, { id: "n2" }]);
		expect(q.current()).toEqual({ card: { id: "d1" }, kind: "due" });
		const now = parseStamp("2026-03-02T09:00:00.000+01:00");
		const learned = replay([ev("d1", "2026-03-02T09:00:00.000+01:00", 4)]).states.get("d1");
		q.rated(learned, now); // review state: does not come back
		const again = replay([ev("n1", "2026-03-02T09:00:00.000+01:00", 1)]).states.get("n1");
		q.rated(again, now); // learning, due in 1 min: comes back
		expect(q.current()?.card.id).toBe("n2");
		q.skip();
		expect(q.current()).toEqual({ card: { id: "n1" }, kind: "again" });
		expect(q.total).toBe(4);
		expect(q.reviewed.size).toBe(2);
	});
	it("a learning card due tomorrow does not come back", () => {
		const q = new SessionQueue([], [{ id: "n1" }]);
		const late = parseStamp("2026-03-03T03:58:00.000+01:00");
		const st = replay([ev("n1", "2026-03-03T03:58:00.000+01:00", 3)]).states.get("n1"); // due 04:08 = next learning day
		q.rated(st, late);
		expect(q.done).toBe(true);
	});
});

describe("formatInterval", () => {
	it("picks a readable unit", () => {
		expect(formatInterval(60_000, units)).toBe("1m");
		expect(formatInterval(6 * 60_000, units)).toBe("6m");
		expect(formatInterval(3 * 3_600_000, units)).toBe("3h");
		expect(formatInterval(8 * 86_400_000, units)).toBe("8d");
		expect(formatInterval(90 * 86_400_000, units)).toBe("3mo");
		expect(formatInterval(400 * 86_400_000, units)).toBe("1.1y");
	});
});
