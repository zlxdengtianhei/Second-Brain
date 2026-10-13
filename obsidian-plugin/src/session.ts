// A review session: today's queue (due first, then new) plus cards that come back the same day.
import { DAY_START_HOUR, inOffset, learningDay, type CardState, type Stamp } from "./registry";

export type EntryKind = "due" | "new" | "again";

export interface SessionEntry<C> {
	card: C;
	kind: EntryKind;
}

export class SessionQueue<C extends { id: string }> {
	private entries: SessionEntry<C>[];
	private pos = 0;
	readonly reviewed = new Set<string>();
	ratings = 0;

	constructor(due: C[], fresh: C[]) {
		this.entries = [...due.map((card) => ({ card, kind: "due" as const })), ...fresh.map((card) => ({ card, kind: "new" as const }))];
	}

	get total(): number {
		return this.entries.length;
	}

	get index(): number {
		return this.pos;
	}

	current(): SessionEntry<C> | undefined {
		return this.entries[this.pos];
	}

	/** Move on after the current card was rated. A card still in (re)learning today comes back at the end. */
	rated(state: CardState | undefined, now: Stamp, dayStartHour = DAY_START_HOUR): void {
		const entry = this.entries[this.pos];
		if (!entry) return;
		this.reviewed.add(entry.card.id);
		this.ratings++;
		if (state && state.state !== "review" && learningDay(inOffset(state.due, now.offsetMin), dayStartHour) <= learningDay(now, dayStartHour)) {
			this.entries.push({ card: entry.card, kind: "again" });
		}
		this.pos++;
	}

	skip(): void {
		if (this.pos < this.entries.length) this.pos++;
	}

	get done(): boolean {
		return this.pos >= this.entries.length;
	}
}

export function formatInterval(ms: number, units: { min: string; hour: string; day: string; month: string; year: string }): string {
	const min = Math.max(1, Math.round(ms / 60_000));
	const fill = (s: string, n: number | string) => s.replace("{n}", String(n));
	if (min < 60) return fill(units.min, min);
	const hours = ms / 3_600_000;
	if (hours < 24) return fill(units.hour, Math.round(hours));
	const days = Math.round(ms / 86_400_000);
	if (days < 31) return fill(units.day, days);
	if (days < 365) return fill(units.month, (days / 30).toFixed(1).replace(/\.0$/, ""));
	return fill(units.year, (days / 365).toFixed(1).replace(/\.0$/, ""));
}
