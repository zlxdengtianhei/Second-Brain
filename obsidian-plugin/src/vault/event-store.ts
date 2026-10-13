// Event log files in <data>/review/: read all devices' logs, append to this device's log.
import {
	cmpCodePoint,
	isValidDevice,
	type Issue,
	LOG_NAME_RE,
	MD_HEADER,
	parseLogText,
	type ReviewEvent,
	serializeEvent,
} from "../registry";

/** The subset of Obsidian's DataAdapter the store uses (paths are vault-relative). */
export interface LogAdapter {
	exists(path: string): Promise<boolean>;
	list(path: string): Promise<{ files: string[]; folders: string[] }>;
	read(path: string): Promise<string>;
	append(path: string, data: string): Promise<void>;
	write(path: string, data: string): Promise<void>;
	mkdir(path: string): Promise<void>;
}

export type LogFormat = "jsonl" | "md";

export function newDeviceId(random: () => number = Math.random): string {
	let s = "";
	for (let i = 0; i < 6; i++) s += Math.floor(random() * 16).toString(16);
	return `obs-${s}`;
}

export class EventStore {
	private endsWithNewline = new Map<string, boolean>();
	private queue: Promise<unknown> = Promise.resolve();

	constructor(
		private adapter: LogAdapter,
		private dir: () => string,
		readonly device: string,
		private format: () => LogFormat,
	) {
		if (!isValidDevice(device)) throw new Error(`device id is not allowed: ${device}`);
	}

	get directory(): string {
		return this.dir().replace(/\/+$/, "");
	}

	ownPath(): string {
		return `${this.directory}/events-${this.device}.${this.format()}`;
	}

	isLogPath(path: string): boolean {
		const dir = this.directory;
		if (!path.startsWith(dir + "/")) return false;
		const name = path.slice(dir.length + 1);
		return !name.includes("/") && LOG_NAME_RE.test(name);
	}

	/** Read and parse every log file, in name order. */
	async readAll(): Promise<{ events: ReviewEvent[]; issues: Issue[]; files: number }> {
		const dir = this.directory;
		const events: ReviewEvent[] = [];
		const issues: Issue[] = [];
		if (!(await this.adapter.exists(dir))) return { events, issues, files: 0 };
		const listed = await this.adapter.list(dir);
		const names = listed.files
			.map((p) => p.slice(p.lastIndexOf("/") + 1))
			.filter((n) => LOG_NAME_RE.test(n))
			.sort(cmpCodePoint);
		for (const name of names) {
			const path = `${dir}/${name}`;
			const text = await this.adapter.read(path);
			this.endsWithNewline.set(path, text === "" || text.endsWith("\n"));
			const r = parseLogText(name, text);
			events.push(...r.events);
			issues.push(...r.issues);
		}
		return { events, issues, files: names.length };
	}

	/** Append one validated event line to this device's log. Calls are serialized. */
	append(event: Record<string, unknown>): Promise<string> {
		const run = this.queue.then(() => this.appendNow(event));
		this.queue = run.catch(() => undefined);
		return run;
	}

	private async appendNow(event: Record<string, unknown>): Promise<string> {
		if (event.device !== this.device) throw new Error("event device does not match this store");
		const path = this.ownPath();
		const line = serializeEvent(event);
		const dir = this.directory;
		if (!(await this.adapter.exists(path))) {
			if (!(await this.adapter.exists(dir))) await this.adapter.mkdir(dir);
			await this.adapter.write(path, (this.format() === "md" ? MD_HEADER : "") + line);
			this.endsWithNewline.set(path, true);
			return path;
		}
		let clean = this.endsWithNewline.get(path);
		if (clean === undefined) {
			const text = await this.adapter.read(path);
			clean = text === "" || text.endsWith("\n");
		}
		await this.adapter.append(path, (clean ? "" : "\n") + line);
		this.endsWithNewline.set(path, true);
		return path;
	}
}
