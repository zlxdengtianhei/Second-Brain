import { describe, expect, it } from "vitest";
import { buildIcs, foldLine, parseTime, reviewUri } from "../src/ics";
import { shouldNotify } from "../src/ui/reminder";

describe("ics", () => {
	const now = new Date(2026, 9, 13, 7, 30);
	const ics = buildIcs({ time: "09:00", uri: reviewUri("Second Brain"), summary: "每日复习", description: "打开今日复习, 共 3 张; 点链接", now });
	const lines = ics.split("\r\n");
	it("writes a daily floating-time event with a link and an alarm", () => {
		expect(lines[0]).toBe("BEGIN:VCALENDAR");
		expect(lines).toContain("DTSTART:20261013T090000");
		expect(lines).toContain("RRULE:FREQ=DAILY");
		expect(lines).toContain("URL:obsidian://secondbrain-review?vault=Second%20Brain");
		expect(lines).toContain("TRIGGER:PT0M");
		expect(ics.endsWith("END:VCALENDAR\r\n")).toBe(true);
	});
	it("escapes text and folds long lines at 75 octets without breaking characters", () => {
		expect(ics.replace(/\r\n /g, "")).toContain("DESCRIPTION:打开今日复习\\, 共 3 张\\; 点链接");
		const long = foldLine("DESCRIPTION:" + "复习".repeat(60));
		for (const part of long.split("\r\n")) expect(new TextEncoder().encode(part).length).toBeLessThanOrEqual(75);
		expect(long.replace(/\r\n /g, "")).toBe("DESCRIPTION:" + "复习".repeat(60));
	});
	it("rejects a bad time", () => {
		expect(parseTime("9:05")).toEqual({ h: 9, m: 5 });
		expect(parseTime("24:00")).toBeNull();
		expect(() => buildIcs({ time: "nine", uri: "x", summary: "s", description: "d", now })).toThrow();
	});
});

describe("startup notice", () => {
	it("shows once per learning day and only when there is work", () => {
		expect(shouldNotify(null, 100, { due: 1, fresh: 0 }, true)).toBe(true);
		expect(shouldNotify(100, 100, { due: 5, fresh: 5 }, true)).toBe(false);
		expect(shouldNotify(99, 100, { due: 0, fresh: 0 }, true)).toBe(false);
		expect(shouldNotify(99, 100, { due: 3, fresh: 0 }, false)).toBe(false);
	});
});
