// Device-local storage for the replay checkpoint (IndexedDB: not synced, not in the vault, no size issues).
import type { Checkpoint } from "../registry";
import type { CacheStore } from "../service";

const DB = "secondbrain-review";
const STORE = "checkpoints";

function request<T>(r: IDBRequest<T>): Promise<T> {
	return new Promise((resolve, reject) => {
		r.onsuccess = () => resolve(r.result);
		r.onerror = () => reject(r.error);
	});
}

export class IdbCache implements CacheStore {
	private db: Promise<IDBDatabase> | null = null;

	constructor(private key: () => string) {}

	private open(): Promise<IDBDatabase> {
		if (!this.db) {
			const r = indexedDB.open(DB, 1);
			r.onupgradeneeded = () => r.result.createObjectStore(STORE);
			this.db = request(r);
			this.db.catch(() => (this.db = null));
		}
		return this.db;
	}

	async load(): Promise<unknown> {
		if (typeof indexedDB === "undefined") return null;
		const db = await this.open();
		return (await request(db.transaction(STORE, "readonly").objectStore(STORE).get(this.key()))) ?? null;
	}

	async save(checkpoint: Checkpoint): Promise<void> {
		if (typeof indexedDB === "undefined") return;
		const db = await this.open();
		const tx = db.transaction(STORE, "readwrite");
		tx.objectStore(STORE).put(checkpoint, this.key());
		await new Promise<void>((resolve, reject) => {
			tx.oncomplete = () => resolve();
			tx.onerror = () => reject(tx.error);
		});
	}
}
