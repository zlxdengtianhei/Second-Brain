// ReviewService: the plugin's single owner of review state.
// Cards come from the index, events from the store; state is always a replay of the events,
// started from this device's checkpoint when one is valid (docs/review-registry.md section 4.1).
import {
	applyEvent,
	buildQueue,
	type CardState,
	type Checkpoint,
	countEvent,
	DAY_START_HOUR,
	emptyResult,
	learningDay,
	loadLogs,
	localNow,
	makeEvent,
	type NewEvent,
	parseEvent,
	type Queue,
	type RecentEvent,
	type ReplayResult,
	type Stamp,
	summarize,
	type Summary,
} from "./registry";
import type { IndexedCard } from "./vault/card-index";
import type { EventStore } from "./vault/event-store";
import { domainMapTitles, emptyTaxonomy, rankCards, type Taxonomy } from "./vault/taxonomy";

export interface CardSource {
	all(): IndexedCard[];
	get(id: string): IndexedCard | undefined;
	version: number;
}

/** Device-local storage for the replay checkpoint. Losing it only costs time. */
export interface CacheStore {
	load(): Promise<unknown>;
	save(checkpoint: Checkpoint): Promise<void>;
}

export interface ServiceOptions {
	newPerDay: () => number;
	studyDomains: () => string[];
	clock?: () => Stamp;
	cache?: CacheStore;
}

type EventKey = { ms: number };

export class ReviewService {
	private result: ReplayResult = emptyResult();
	private lastKey: EventKey | null = null;
	private ranked: { version: string; cards: IndexedCard[] } | null = null;
	private listeners = new Set<() => void>();
	/** Bumped whenever events or the taxonomy change; keys the queue and summary caches. */
	private version = 0;
	private cache = new Map<string, { key: string; value: unknown }>();
	taxonomy: Taxonomy = emptyTaxonomy();
	loaded = false;
	/** "events" after a full replay, "cache" after continuing from the checkpoint. */
	loadedFrom: "events" | "cache" | "" = "";

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
		this.version++;
		this.ranked = null;
		this.notify();
	}

	/** Read the logs and rebuild state; `full` ignores the checkpoint. */
	async reload(full = false): Promise<void> {
		const files = await this.store.readTexts();
		const saved = full || !this.opts.cache ? null : await this.opts.cache.load().catch(() => null);
		const out = loadLogs(files, saved as Checkpoint | null);
		this.result = out.result;
		this.lastKey = out.watermark ? { ms: out.watermark[0] } : null;
		this.loadedFrom = out.from;
		this.loaded = true;
		this.version++;
		if (out.checkpoint && this.opts.cache) {
			await this.opts.cache.save(out.checkpoint).catch((e) => console.warn("[secondbrain-review] cannot save review cache", e));
		}
		this.notify();
	}

	/** Cards with their tie-break rank for the new-card order (cached per index/settings version). */
	rankedCards(): IndexedCard[] {
		const domains = this.opts.studyDomains();
		const version = `${this.cards.version}|${domains.join(",")}|${this.version}`;
		if (this.ranked?.version === version) return this.ranked.cards;
		const cards = rankCards(this.cards.all(), this.taxonomy, domains);
		this.ranked = { version, cards };
		return cards;
	}

	state(cardId: string): CardState | undefined {
		return this.result.states.get(cardId);
	}

	replayResult(): ReplayResult {
		return this.result;
	}

	/** Same inputs (events, cards, settings, learning day) give the same answer: compute once. */
	private memo<T>(name: string, now: Stamp, compute: () => T): T {
		const key = [this.version, this.cards.version, this.opts.newPerDay(), this.opts.studyDomains().join(","), learningDay(now), now.offsetMin].join("|");
		const hit = this.cache.get(name);
		if (hit && hit.key === key) return hit.value as T;
		const value = compute();
		this.cache.set(name, { key, value });
		return value;
	}

	summary(now: Stamp = this.now()): Summary {
		return this.memo("summary", now, () => summarize(this.cards.all(), this.result, now));
	}

	queue(now: Stamp = this.now()): Queue<IndexedCard> {
		return this.memo("queue", now, () =>
			buildQueue(this.rankedCards(), this.result, now, {
				newPerDay: this.opts.newPerDay(),
				domains: this.opts.studyDomains(),
			}),
		);
	}

	domainTitles(): Map<string, string> {
		return domainMapTitles(this.cards.all(), this.taxonomy);
	}

	/** The last few events of one card, newest first. */
	history(cardId: string): RecentEvent[] {
		return [...(this.result.states.get(cardId)?.recent ?? [])].reverse();
	}

	/**
	 * Apply events this device just wrote. They normally come after everything replayed so far;
	 * if not (the clock went backwards), replay all logs instead.
	 */
	private async applyWritten(objs: Record<string, unknown>[], path: string): Promise<void> {
		const source = path.slice(path.lastIndexOf("/") + 1);
		const events = objs.map((obj) => parseEvent(obj, source, Number.MAX_SAFE_INTEGER));
		let last = this.lastKey?.ms ?? -Infinity;
		const inOrder = events.every((ev) => {
			const ok = ev.ts.ms > last;
			last = ev.ts.ms;
			return ok;
		});
		if (!inOrder) return this.reload(true);
		for (const ev of events) {
			this.result.states.set(ev.card, applyEvent(this.result.states.get(ev.card), ev, DAY_START_HOUR));
			countEvent(this.result, ev, DAY_START_HOUR);
		}
		this.lastKey = { ms: last };
		this.version++;
		this.notify();
	}

	/** Write many events at once (imports). */
	async recordBatch(inputs: (Omit<NewEvent, "device" | "now"> & { now?: Stamp })[]): Promise<number> {
		if (!inputs.length) return 0;
		const base = this.now();
		// Distinct instants keep the order of the batch and avoid duplicate keys.
		const objs = inputs.map((input, i) =>
			makeEvent({ ...input, device: this.store.device, now: input.now ?? { ms: base.ms + i, offsetMin: base.offsetMin } }),
		);
		const path = await this.store.appendMany(objs);
		await this.applyWritten(objs, path);
		return objs.length;
	}

	/** Write one review (or import) for this device and update state. */
	async record(input: Omit<NewEvent, "device" | "now"> & { now?: Stamp }): Promise<CardState | undefined> {
		const obj = makeEvent({ ...input, device: this.store.device, now: input.now ?? this.now() });
		const path = await this.store.append(obj);
		await this.applyWritten([obj], path);
		return this.result.states.get(input.card);
	}
}
