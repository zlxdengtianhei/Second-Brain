import { Plugin, parseYaml, type TAbstractFile, TFile } from "obsidian";
import { ReviewService } from "./service";
import { mergeSettings, reviewDir, type ReviewSettings } from "./settings";
import { CardIndex } from "./vault/card-index";
import { EventStore, newDeviceId } from "./vault/event-store";
import { buildTaxonomy } from "./vault/taxonomy";

const DEVICE_KEY = "secondbrain-review:device";

export default class SecondBrainReviewPlugin extends Plugin {
	settings!: ReviewSettings;
	index!: CardIndex;
	service!: ReviewService;
	private reloadTimer: number | null = null;

	async onload(): Promise<void> {
		this.settings = mergeSettings(await this.loadData());
		this.index = new CardIndex(this.app, () => this.settings.knowledge);
		const store = new EventStore(this.app.vault.adapter, () => reviewDir(this.settings), this.deviceId(), () => this.settings.logFormat);
		this.service = new ReviewService(this.index, store, {
			newPerDay: () => this.settings.newPerDay,
			studyDomains: () => this.settings.studyDomains,
		});
		this.index.onChange(() => this.service.notify());
		this.app.workspace.onLayoutReady(() => void this.start());
	}

	private async start(): Promise<void> {
		this.index.rebuild();
		this.index.watch((ref) => this.registerEvent(ref));
		await Promise.all([this.loadTaxonomy(), this.service.reload()]);
		const onLogChange = (file: TAbstractFile) => {
			if (this.service.store.isLogPath(file.path) && file.path !== this.service.store.ownPath()) this.scheduleReload();
			if (file instanceof TFile && file.extension === "yaml" && file.path.startsWith(this.settings.taxonomy + "/")) void this.loadTaxonomy();
		};
		this.registerEvent(this.app.vault.on("modify", onLogChange));
		this.registerEvent(this.app.vault.on("create", onLogChange));
		this.registerEvent(this.app.vault.on("delete", onLogChange));
	}

	/** This device's writer id; kept in device-local storage so sync never copies it. */
	private deviceId(): string {
		const saved = this.app.loadLocalStorage(DEVICE_KEY);
		if (typeof saved === "string" && /^obs-[0-9a-f]{6}$/.test(saved)) return saved;
		const id = newDeviceId(() => crypto.getRandomValues(new Uint32Array(1))[0] / 2 ** 32);
		this.app.saveLocalStorage(DEVICE_KEY, id);
		return id;
	}

	scheduleReload(): void {
		if (this.reloadTimer !== null) window.clearTimeout(this.reloadTimer);
		this.reloadTimer = window.setTimeout(() => {
			this.reloadTimer = null;
			void this.service.reload();
		}, 1000);
	}

	async loadTaxonomy(): Promise<void> {
		const folder = this.settings.taxonomy.replace(/\/+$/, "");
		const adapter = this.app.vault.adapter;
		const docs: { path: string; data: unknown }[] = [];
		if (await adapter.exists(folder)) {
			const listed = await adapter.list(folder);
			for (const path of listed.files.filter((p) => /\.ya?ml$/.test(p)).sort()) {
				try {
					docs.push({ path, data: parseYaml(await adapter.read(path)) });
				} catch (e) {
					console.warn(`[secondbrain-review] cannot parse ${path}`, e);
				}
			}
		}
		this.service.setTaxonomy(buildTaxonomy(docs));
	}

	async saveSettings(): Promise<void> {
		await this.saveData(this.settings);
		this.index.rebuild();
		await Promise.all([this.loadTaxonomy(), this.service.reload()]);
	}

	onunload(): void {
		if (this.reloadTimer !== null) window.clearTimeout(this.reloadTimer);
	}
}
