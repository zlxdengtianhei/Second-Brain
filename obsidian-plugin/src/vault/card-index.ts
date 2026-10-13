// Card index built from Obsidian's metadata cache (front matter only, no file reads).
import type { App, CachedMetadata, EventRef, TAbstractFile, TFile } from "obsidian";
import type { CardMeta } from "../registry";

export interface Relation {
	type: string;
	target: string;
	tier?: string;
}

export interface IndexedCard extends CardMeta {
	id: string;
	title: string;
	domain: string;
	kind: string;
	status: string;
	path: string;
	pillar: string;
	topic: string;
	relationList: Relation[];
}

function str(v: unknown): string {
	return typeof v === "string" ? v.trim() : typeof v === "number" ? String(v) : "";
}

/** Build an index entry from parsed front matter; null when the note is not a card. */
export function cardFromFrontmatter(fm: Record<string, unknown> | undefined | null, path: string): IndexedCard | null {
	if (!fm) return null;
	const id = str(fm.id);
	if (!id) return null;
	const relationList: Relation[] = [];
	if (Array.isArray(fm.relations)) {
		for (const r of fm.relations) {
			if (typeof r !== "object" || r === null) continue;
			const rec = r as Record<string, unknown>;
			const type = str(rec.type);
			const target = str(rec.target);
			if (!type || !target) continue;
			const tier = str(rec.tier);
			relationList.push(tier ? { type, target, tier } : { type, target });
		}
	}
	const base = path.split("/").pop() ?? path;
	return {
		id,
		title: str(fm.title) || base.replace(/\.md$/, ""),
		domain: str(fm.domain),
		kind: str(fm.kind),
		status: str(fm.status),
		path,
		pillar: str(fm.pillar),
		topic: str(fm.topic),
		relationList,
		relations: relationList.map((r) => [r.type, r.target] as [string, string]),
	};
}

function inFolder(path: string, folder: string): boolean {
	const f = folder.replace(/^\/+|\/+$/g, "");
	return f === "" || path === f || path.startsWith(f + "/");
}

/**
 * Keeps id -> card for every Markdown file under the knowledge folder that has an `id`.
 * Updates itself from metadata-cache and vault events; call `rebuild()` after settings change.
 */
export class CardIndex {
	private byId = new Map<string, IndexedCard>();
	private idByPath = new Map<string, string>();
	private listeners = new Set<() => void>();
	version = 0;

	constructor(
		private app: App,
		private folder: () => string,
	) {}

	all(): IndexedCard[] {
		return [...this.byId.values()];
	}

	get(id: string): IndexedCard | undefined {
		return this.byId.get(id);
	}

	byPath(path: string): IndexedCard | undefined {
		const id = this.idByPath.get(path);
		return id ? this.byId.get(id) : undefined;
	}

	get size(): number {
		return this.byId.size;
	}

	onChange(fn: () => void): () => void {
		this.listeners.add(fn);
		return () => this.listeners.delete(fn);
	}

	private emit(): void {
		this.version++;
		for (const fn of this.listeners) fn();
	}

	rebuild(): void {
		this.byId.clear();
		this.idByPath.clear();
		const folder = this.folder();
		for (const file of this.app.vault.getMarkdownFiles()) {
			if (!inFolder(file.path, folder)) continue;
			this.put(file.path, this.app.metadataCache.getFileCache(file));
		}
		this.emit();
	}

	private put(path: string, cache: CachedMetadata | null): void {
		const card = cardFromFrontmatter(cache?.frontmatter as Record<string, unknown> | undefined, path);
		const old = this.idByPath.get(path);
		if (old && (!card || card.id !== old)) {
			this.byId.delete(old);
			this.idByPath.delete(path);
		}
		if (!card) return;
		this.byId.set(card.id, card);
		this.idByPath.set(path, card.id);
	}

	/** Register metadata-cache and vault listeners on the plugin. */
	watch(register: (ref: EventRef) => void): void {
		register(
			this.app.metadataCache.on("changed", (file: TFile, _data: string, cache: CachedMetadata) => {
				if (!inFolder(file.path, this.folder())) return;
				this.put(file.path, cache);
				this.emit();
			}),
		);
		register(
			this.app.vault.on("delete", (file: TAbstractFile) => {
				const id = this.idByPath.get(file.path);
				if (!id) return;
				this.byId.delete(id);
				this.idByPath.delete(file.path);
				this.emit();
			}),
		);
		register(
			this.app.vault.on("rename", (file: TAbstractFile, oldPath: string) => {
				const id = this.idByPath.get(oldPath);
				if (id) {
					this.byId.delete(id);
					this.idByPath.delete(oldPath);
				}
				if ("extension" in file && inFolder(file.path, this.folder())) {
					this.put(file.path, this.app.metadataCache.getFileCache(file as TFile));
				}
				this.emit();
			}),
		);
	}
}
