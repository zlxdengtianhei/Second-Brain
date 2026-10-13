// ReviewService: the plugin's single owner of review state.
// Cards come from the index, events from the store; state is always a replay of the events.
import {
	applyEvent,
	buildQueue,
	countEvent,
	DAY_START_HOUR,
	localNow,
	makeEvent,
	type NewEvent,
	orderEvents,
	parseEvent,
	type Queue,
	replay,
	type ReplayResult,
	emptyResult,
	type ReviewEvent,
	type Stamp,
	summarize,
	type Summary,
	type CardState,
} from "./registry";
import type { IndexedCard } from "./vault/card-index";
import type { EventStore } from "./vault/event-store";
import { domainMapTitles, emptyTaxonomy, rankFor, type Taxonomy } from "./vault/taxonomy";

export interface CardSource {
	all(): IndexedCard[];
	get(id: string): IndexedCard | undefined;
	version: number;
}

export interface ServiceOptions {
	newPerDay: () => number;
	studyDomains: () => string[];
	clock?: () => Stamp;
}

export class ReviewService {
	private events: ReviewEvent[] = [];
	private result: ReplayResult = emptyResult();
	private lastKey: ReviewEvent | null = null;
	private ranked: { version: string; cards: IndexedCard[] } | null = null;
	private listeners = new Set<() => void>();
	taxonomy: Taxonomy = emptyTaxonomy();
	loaded = false;

	constructor(
		readonly cards: CardSource,
		readonly store: EventStore,
		private opts: ServiceOptions,
	) {}

	now(): Stamp {
		return this.opts.clock ? this.opts.clock() : localNow();
	}

	onChange(fn: () => void): () => void {
		this.listeners.add(fn);
		return () => this.listeners.delete(fn);
	}

	notify(): void {
		for (const fn of this.listeners) fn();
	}

	setTaxonomy(tax: Taxonomy): void {
		this.taxonomy = tax;
		this.ranked = null;
		this.notify();
	}

	/** Re-read every log file and replay from scratch. */
	async reload(): Promise<void> {
		const { events, issues } = await this.store.readAll();
		this.events = events;
		this.result = replay(events);
		this.result.issues = issues;
		const ordered = orderEvents(events);
		this.lastKey = ordered[ordered.length - 1] ?? null;
		this.loaded = true;
		this.notify();
	}

	/** Cards with their tie-break rank for the new-card order (cached per index/settings version). */
	rankedCards(): IndexedCard[] {
		const domains = this.opts.studyDomains();
		const version = `${this.cards.version}|${domains.join(",")}|${this.taxonomy.pillars.size}`;
		if (this.ranked?.version === version) return this.ranked.cards;
		const all = this.cards.all();
		const order = new Map<string, number>();
		if (domains.length) domains.forEach((d, i) => order.set(d, i));
		else [...new Set(all.map((c) => c.domain))].sort().forEach((d, i) => order.set(d, i));
		const cards = all.map((c) => ({ ...c, rank: rankFor(c, this.taxonomy, order) }));
		this.ranked = { version, cards };
		return cards;
	}

	state(cardId: string): CardState | undefined {
		return this.result.states.get(cardId);
	}

	replayResult(): ReplayResult {
		return this.result;
	}

	summary(now: Stamp = this.now()): Summary {
		return summarize(this.cards.all(), this.result, now);
	}

	queue(now: Stamp = this.now()): Queue<IndexedCard> {
		return buildQueue(this.rankedCards(), this.result, now, {
			newPerDay: this.opts.newPerDay(),
			domains: this.opts.studyDomains(),
		});
	}

	domainTitles(): Map<string, string> {
		return domainMapTitles(this.cards.all(), this.taxonomy);
	}

	/** Recent events of one card, newest first. */
	history(cardId: string, limit = 5): ReviewEvent[] {
		return this.events
			.filter((e) => e.card === cardId)
			.sort((a, b) => b.ts.ms - a.ts.ms)
			.slice(0, limit);
	}

	/** Write one review (or import) for this device and update state. */
	async record(input: Omit<NewEvent, "device" | "now"> & { now?: Stamp }): Promise<CardState | undefined> {
		const obj = makeEvent({ ...input, device: this.store.device, now: input.now ?? this.now() });
		const path = await this.store.append(obj);
		const ev = parseEvent(obj, path.slice(path.lastIndexOf("/") + 1), Number.MAX_SAFE_INTEGER);
		this.events.push(ev);
		const after = this.lastKey === null || ev.ts.ms > this.lastKey.ts.ms;
		if (after) {
			this.result.states.set(ev.card, applyEvent(this.result.states.get(ev.card), ev, DAY_START_HOUR));
			countEvent(this.result, ev, DAY_START_HOUR);
			this.lastKey = ev;
		} else {
			const issues = this.result.issues;
			this.result = replay(this.events);
			this.result.issues = issues;
			const ordered = orderEvents(this.events);
			this.lastKey = ordered[ordered.length - 1] ?? null;
		}
		this.notify();
		return this.result.states.get(ev.card);
	}
}
