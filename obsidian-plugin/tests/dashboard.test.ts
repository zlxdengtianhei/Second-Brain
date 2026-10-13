import { describe, expect, it } from "vitest";
import { buildQueue, parseStamp, replay, summarize, parseLogText, type CardMeta } from "../src/registry";
import { dashboardModel } from "../src/ui/dashboard";
import { fileText, fixture } from "./fixtures";

const Q = fixture("queue.json");

function model(studyDomains: string[]) {
	const cards: CardMeta[] = Q.cards.map((c: any) => ({ ...c, relations: (c.relations ?? []).map((r: string[]) => [r[0], r[1]]) }));
	const events = Object.entries<any>(Q.files).flatMap(([n, c]) => parseLogText(n, fileText(c)).events);
	const res = replay(events);
	const now = parseStamp(Q.now);
	return dashboardModel(summarize(cards, res, now), buildQueue(cards, res, now, { newPerDay: 10, domains: studyDomains }), {
		loaded: true,
		newPerDay: 10,
		studyDomains,
		titles: new Map([["algo", "Algorithms"], ["net", "Networking"]]),
	});
}

describe("dashboardModel", () => {
	it("shows due, new quota used, done today, streak and per-domain rows", () => {
		const m = model([]);
		expect([m.due, m.dueNotReviewed, m.newToday, m.newMax, m.newLeft, m.doneToday, m.streak]).toEqual([2, 1, 1, 10, 8, 1, 3]);
		expect(m.rows.map((r) => r.label)).toEqual(["Algorithms", "Networking"]);
		expect(m.totals.counts.total).toBe(14);
		const net = m.rows[1];
		expect(net.shares.reduce((a, b) => a + b, 0)).toBeCloseTo(1);
		expect(net.shares[3]).toBeCloseTo(0.25);
	});
	it("puts study domains first and marks them", () => {
		const m = model(["net"]);
		expect(m.rows.map((r) => [r.domain, r.studying])).toEqual([["net", true], ["algo", false]]);
		expect(m.newLeft).toBe(1);
	});
});
