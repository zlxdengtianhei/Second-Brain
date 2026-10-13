// A daily calendar event (RFC 5545) whose link opens today's review. Works when Obsidian is closed.

export interface IcsOptions {
	/** Local time "HH:MM"; the event uses floating local time, so it follows the calendar's time zone. */
	time: string;
	uri: string;
	summary: string;
	description: string;
	now: Date;
	uid?: string;
}

const TIME_RE = /^([01]?\d|2[0-3]):([0-5]\d)$/;

export function parseTime(time: string): { h: number; m: number } | null {
	const m = TIME_RE.exec(time.trim());
	return m ? { h: Number(m[1]), m: Number(m[2]) } : null;
}

function escapeText(s: string): string {
	return s.replace(/\\/g, "\\\\").replace(/;/g, "\\;").replace(/,/g, "\\,").replace(/\r?\n/g, "\\n");
}

/** Fold a content line at 75 octets without splitting a UTF-8 character. */
export function foldLine(line: string): string {
	const enc = new TextEncoder();
	const parts: string[] = [];
	let cur = "";
	let bytes = 0;
	for (const ch of line) {
		const n = enc.encode(ch).length;
		const limit = parts.length ? 74 : 75;
		if (bytes + n > limit) {
			parts.push(cur);
			cur = "";
			bytes = 0;
		}
		cur += ch;
		bytes += n;
	}
	parts.push(cur);
	return parts.join("\r\n ");
}

const pad = (n: number, w = 2) => String(n).padStart(w, "0");

export function buildIcs(o: IcsOptions): string {
	const t = parseTime(o.time);
	if (!t) throw new Error(`time must be HH:MM: ${o.time}`);
	const d = o.now;
	const start = `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}T${pad(t.h)}${pad(t.m)}00`;
	const stamp = `${d.getUTCFullYear()}${pad(d.getUTCMonth() + 1)}${pad(d.getUTCDate())}T${pad(d.getUTCHours())}${pad(d.getUTCMinutes())}${pad(d.getUTCSeconds())}Z`;
	const lines = [
		"BEGIN:VCALENDAR",
		"VERSION:2.0",
		"PRODID:-//SecondBrain//Review//EN",
		"CALSCALE:GREGORIAN",
		"BEGIN:VEVENT",
		`UID:${o.uid ?? "secondbrain-review-daily"}`,
		`DTSTAMP:${stamp}`,
		`DTSTART:${start}`,
		"DURATION:PT15M",
		"RRULE:FREQ=DAILY",
		`SUMMARY:${escapeText(o.summary)}`,
		`DESCRIPTION:${escapeText(o.description)}`,
		`URL:${o.uri}`,
		"BEGIN:VALARM",
		"ACTION:DISPLAY",
		`DESCRIPTION:${escapeText(o.summary)}`,
		"TRIGGER:PT0M",
		"END:VALARM",
		"END:VEVENT",
		"END:VCALENDAR",
	];
	return lines.map(foldLine).join("\r\n") + "\r\n";
}

/** obsidian:// link that opens the today view of this vault. */
export function reviewUri(vaultName: string, action = "secondbrain-review"): string {
	return `obsidian://${action}?vault=${encodeURIComponent(vaultName)}`;
}
