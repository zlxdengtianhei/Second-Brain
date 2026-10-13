import type { LogAdapter } from "../src/vault/event-store";

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
