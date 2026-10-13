import { normalizePath, Notice, Plugin, parseYaml, type TAbstractFile, TFile, type WorkspaceLeaf } from "obsidian";
import { extractReviewItems, sectionText } from "./registry";
import { AiProvidersClient } from "./llm/ai";
import { DEFAULT_EXPLAIN_TEMPLATE, DEFAULT_GRADE_TEMPLATE, parseTemplate, type Template } from "./llm/prompts";
import { t } from "./i18n";
import { ReviewService } from "./service";
import { mergeSettings, reviewDir, type ReviewSettings } from "./settings";
import { type LoadedCard, ReviewModal } from "./ui/review-modal";
import { buildIcs, reviewUri } from "./ics";
import { planSrImport } from "./sr-import";
import { ConfirmModal } from "./ui/confirm";
import { ExplainView, VIEW_EXPLAIN } from "./ui/explain-view";
import { Reminder } from "./ui/reminder";
import { ReviewSettingTab } from "./ui/settings-tab";
import { TodayBlock, TodayView, VIEW_TODAY } from "./ui/today-view";
import type { IndexedCard } from "./vault/card-index";
import { CardIndex } from "./vault/card-index";
import { EventStore, newDeviceId } from "./vault/event-store";
import { IdbCache } from "./vault/idb-cache";
import { buildTaxonomy } from "./vault/taxonomy";

const DEVICE_KEY = "secondbrain-review:device";
export const URI_ACTION = "secondbrain-review";

export type SessionMode = "flip" | "qa";

export default class SecondBrainReviewPlugin extends Plugin {
	settings!: ReviewSettings;
	index!: CardIndex;
	service!: ReviewService;
	llm!: AiProvidersClient;
	private reloadTimer: number | null = null;

	async onload(): Promise<void> {
		this.settings = mergeSettings(await this.loadData());
		this.index = new CardIndex(this.app, () => this.settings.knowledge);
		const store = new EventStore(this.app.vault.adapter, () => reviewDir(this.settings), this.deviceId(), () => this.settings.logFormat);
		const cache = new IdbCache(() => `${(this.app as { appId?: string }).appId ?? this.app.vault.getName()}|${reviewDir(this.settings)}|${store.device}`);
		this.service = new ReviewService(this.index, store, {
			newPerDay: () => this.settings.newPerDay,
			studyDomains: () => this.settings.studyDomains,
			cache,
		});
		this.index.onChange(() => this.service.notify());
		AiProvidersClient.init(this.app, this);
		this.llm = new AiProvidersClient(this.app, () => this.settings.providerId);

		this.registerView(VIEW_TODAY, (leaf: WorkspaceLeaf) => new TodayView(leaf, this));
		this.registerView(VIEW_EXPLAIN, (leaf: WorkspaceLeaf) => new ExplainView(leaf, this));
		this.registerMarkdownCodeBlockProcessor("secondbrain-review", (_src, el, ctx) => {
			ctx.addChild(new TodayBlock(el, this));
		});
		this.registerObsidianProtocolHandler(URI_ACTION, (params) => {
			void this.openToday().then(() => {
				if (params.mode === "flip" || params.mode === "qa") void this.startSession(params.mode);
			});
		});
		this.addRibbonIcon("calendar-check", t("view.title"), () => void this.openToday());
		this.addCommand({ id: "open-today", name: t("cmd.open"), callback: () => void this.openToday() });
		this.addCommand({ id: "start-flip", name: t("cmd.flip"), callback: () => void this.startSession("flip") });
		this.addCommand({ id: "start-qa", name: t("cmd.qa"), callback: () => void this.startSession("qa") });
		this.addCommand({ id: "explain-card", name: t("cmd.explain"), callback: () => void this.explainActive() });
		this.addCommand({ id: "write-ics", name: t("cmd.ics"), callback: () => void this.writeIcs() });
		this.addCommand({ id: "import-sr", name: t("cmd.import"), callback: () => void this.importSr() });
		this.addSettingTab(new ReviewSettingTab(this.app, this));

		this.app.workspace.onLayoutReady(() => void this.start());
	}

	private async start(): Promise<void> {
		this.index.rebuild();
		this.index.watch((ref) => this.registerEvent(ref));
		await Promise.all([this.loadTaxonomy(), this.service.reload()]);
		new Reminder(this).start();
		const onFileChange = (file: TAbstractFile) => {
			if (this.service.store.isLogPath(file.path) && file.path !== this.service.store.ownPath()) this.scheduleReload();
			if (file instanceof TFile && /\.ya?ml$/.test(file.path) && file.path.startsWith(this.settings.taxonomy + "/")) {
				void this.loadTaxonomy();
			}
		};
		this.registerEvent(this.app.vault.on("modify", onFileChange));
		this.registerEvent(this.app.vault.on("create", onFileChange));
		this.registerEvent(this.app.vault.on("delete", onFileChange));
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

	async refreshAll(): Promise<void> {
		this.index.rebuild();
		await Promise.all([this.loadTaxonomy(), this.service.reload()]);
	}

	async openToday(): Promise<void> {
		const { workspace } = this.app;
		let leaf = workspace.getLeavesOfType(VIEW_TODAY)[0];
		if (!leaf) {
			leaf = workspace.getRightLeaf(false) ?? workspace.getLeaf(true);
			await leaf.setViewState({ type: VIEW_TODAY, active: true });
		}
		await workspace.revealLeaf(leaf);
	}

	async startSession(mode: SessionMode): Promise<void> {
		if (!this.service.loaded) await this.service.reload();
		new ReviewModal(this, mode).open();
	}

	/** Read a card file and pull out what review and explanation need. */
	async loadCard(card: IndexedCard): Promise<LoadedCard> {
		const file = this.app.vault.getAbstractFileByPath(card.path);
		if (!(file instanceof TFile)) throw new Error(`card file not found: ${card.path}`);
		const text = await this.app.vault.cachedRead(file);
		return {
			card,
			text,
			items: extractReviewItems(text, this.settings.reviewSection),
			essence: sectionText(text, this.settings.essenceSection),
			propositions: sectionText(text, this.settings.propositionsSection),
		};
	}

	/** Explain the card open in the editor. */
	async explainActive(): Promise<void> {
		const file = this.app.workspace.getActiveFile();
		if (!file) return void new Notice(t("explain.noCard"));
		const card = this.index.byPath(file.path);
		if (!card) return void new Notice(t("explain.notCard"));
		await this.explainCard(card);
	}

	async explainCard(card: IndexedCard): Promise<void> {
		const { workspace } = this.app;
		let leaf = workspace.getLeavesOfType(VIEW_EXPLAIN)[0];
		if (!leaf) {
			leaf = workspace.getRightLeaf(false) ?? workspace.getLeaf(true);
			await leaf.setViewState({ type: VIEW_EXPLAIN, active: true });
		}
		await workspace.revealLeaf(leaf);
		const view = leaf.view;
		if (view instanceof ExplainView) await view.explain(card);
	}

	/** A template from the vault, or the built-in default when the file is missing. */
	private async template(path: string, fallback: string): Promise<Template> {
		const adapter = this.app.vault.adapter;
		try {
			if (path && (await adapter.exists(path))) return parseTemplate(await adapter.read(path));
		} catch (e) {
			console.warn(`[secondbrain-review] cannot read template ${path}`, e);
		}
		return parseTemplate(fallback);
	}

	gradeTemplate(): Promise<Template> {
		return this.template(this.settings.gradeTemplate, DEFAULT_GRADE_TEMPLATE);
	}

	explainTemplate(): Promise<Template> {
		return this.template(this.settings.explainTemplate, DEFAULT_EXPLAIN_TEMPLATE);
	}

	/** Turn Spaced Repetition schedules in cards into import events (cards are not modified). */
	async importSr(): Promise<void> {
		if (!this.service.loaded) await this.service.reload();
		const scanning = new Notice(t("import.scan"), 0);
		const plan = await planSrImport(this.index.all(), {
			frontmatter: (c) => {
				const file = this.app.vault.getAbstractFileByPath(c.path);
				return file instanceof TFile ? (this.app.metadataCache.getFileCache(file)?.frontmatter as Record<string, unknown> | undefined) : undefined;
			},
			read: async (c) => {
				const file = this.app.vault.getAbstractFileByPath(c.path);
				return file instanceof TFile ? this.app.vault.cachedRead(file) : "";
			},
			hasEvents: (c) => this.service.state(c.id) !== undefined,
		});
		scanning.hide();
		if (!plan.found.length) return void new Notice(t("import.none"));
		new ConfirmModal(this.app, t("import.confirm", { n: plan.found.length, skipped: plan.skipped }), t("import.go"), async () => {
			const n = await this.service.recordBatch(plan.found.map((f) => ({ card: f.card.id, mode: "import", importSeed: f.seed })));
			new Notice(t("import.done", { n }));
		}).open();
	}

	/** Write the daily reminder calendar file into the vault. */
	async writeIcs(): Promise<void> {
		const s = this.settings;
		let text: string;
		try {
			const uri = reviewUri(this.app.vault.getName());
			text = buildIcs({ time: s.reminderTime, uri, summary: t("ics.summary"), description: t("ics.description", { uri }), now: new Date() });
		} catch (e) {
			return void new Notice(String((e as Error).message ?? e));
		}
		const path = normalizePath(s.icsPath);
		const dir = path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
		const adapter = this.app.vault.adapter;
		if (dir && !(await adapter.exists(dir))) await adapter.mkdir(dir);
		await adapter.write(path, text);
		new Notice(t("ics.done", { path, time: s.reminderTime }));
	}

	async saveSettings(): Promise<void> {
		await this.saveData(this.settings);
		await this.refreshAll();
	}

	onunload(): void {
		if (this.reloadTimer !== null) window.clearTimeout(this.reloadTimer);
	}
}
