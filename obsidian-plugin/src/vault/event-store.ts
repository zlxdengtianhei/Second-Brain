// Event log files in <data>/review/: read all devices' logs, append to this device's log.
import {
	cmpCodePoint,
	isValidDevice,
	type Issue,
	LOG_NAME_RE,
	type LogText,
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

	/** Every log file's text, in name order. */
	async readTexts(): Promise<LogText[]> {
		const dir = this.directory;
		if (!(await this.adapter.exists(dir))) return [];
		const listed = await this.adapter.list(dir);
		const names = listed.files
			.map((p) => p.slice(p.lastIndexOf("/") + 1))
			.filter((n) => LOG_NAME_RE.test(n))
			.sort(cmpCodePoint);
		const out: LogText[] = [];
		for (const name of names) {
			const path = `${dir}/${name}`;
			const text = await this.adapter.read(path);
			this.endsWithNewline.set(path, text === "" || text.endsWith("\n"));
			out.push({ name, text });
		}
		return out;
	}

	/** Read and parse every log file, in name order. */
	async readAll(): Promise<{ events: ReviewEvent[]; issues: Issue[]; files: number }> {
		const events: ReviewEvent[] = [];
		const issues: Issue[] = [];
		const texts = await this.readTexts();
		for (const { name, text } of texts) {
			const r = parseLogText(name, text);
			events.push(...r.events);
			issues.push(...r.issues);
		}
		return { events, issues, files: texts.length };
	}

	/** Append one validated event line to this device's log. Calls are serialized. */
	append(event: Record<string, unknown>): Promise<string> {
		return this.appendMany([event]);
	}

	/** Append several validated events with one write. */
	appendMany(events: Record<string, unknown>[]): Promise<string> {
		const run = this.queue.then(() => this.appendNow(events));
		this.queue = run.catch(() => undefined);
		return run;
	}

	private async appendNow(events: Record<string, unknown>[]): Promise<string> {
		if (events.some((e) => e.device !== this.device)) throw new Error("event device does not match this store");
		const path = this.ownPath();
		const line = events.map(serializeEvent).join("");
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
