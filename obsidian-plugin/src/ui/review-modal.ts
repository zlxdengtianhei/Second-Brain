// Review session modal: flip mode (question -> answer -> rating) and Q&A mode (answer -> LLM grade -> confirm).
import { Component, MarkdownRenderer, Modal, Notice } from "obsidian";
import { ratingLabel, t, timeUnits } from "../i18n";
import type SecondBrainReviewPlugin from "../main";
import { previewRatings, type ReviewItem } from "../registry";
import { formatInterval, SessionQueue } from "../session";
import type { IndexedCard } from "../vault/card-index";
import type { SessionMode } from "../main";
import { QaCardRunner } from "./qa";

export interface LoadedCard {
	card: IndexedCard;
	text: string;
	items: ReviewItem[];
	essence: string;
	propositions: string;
}

export class ReviewModal extends Modal {
	private queue: SessionQueue<IndexedCard>;
	private md = new Component();
	private startedAt = 0;
	private keyHandler: ((rating: number) => void) | null = null;
	private revealHandler: (() => void) | null = null;
	private runner: QaCardRunner | null = null;

	constructor(
		private plugin: SecondBrainReviewPlugin,
		private mode: SessionMode,
	) {
		super(plugin.app);
		const q = plugin.service.queue();
		this.queue = new SessionQueue(q.due, q.new);
	}

	onOpen(): void {
		this.md.load();
		this.modalEl.addClass("sbr-modal");
		this.titleEl.setText(this.mode === "flip" ? t("session.flip") : t("session.qa"));
		for (const r of [1, 2, 3, 4]) {
			this.scope.register([], String(r), (evt) => {
				if (isTyping(evt)) return true;
				this.keyHandler?.(r);
				return false;
			});
		}
		this.scope.register([], " ", (evt) => {
			if (isTyping(evt)) return true;
			this.revealHandler?.();
			return false;
		});
		void this.show();
	}

	onClose(): void {
		this.runner?.cancel();
		this.md.unload();
		this.contentEl.empty();
	}

	private async renderMarkdown(el: HTMLElement, markdown: string, sourcePath: string): Promise<void> {
		await MarkdownRenderer.render(this.app, markdown, el, sourcePath, this.md);
	}

	private header(root: HTMLElement, loaded: LoadedCard, kind: string): void {
		const meta = root.createDiv({ cls: "sbr-session-meta" });
		meta.createSpan({ text: t("session.progress", { i: this.queue.index + 1, n: this.queue.total }) });
		meta.createSpan({ text: t(`session.kind.${kind}` as "session.kind.due") });
		const title = root.createDiv({ cls: "sbr-card-title", text: loaded.card.title });
		title.title = loaded.card.path;
		const row = root.createDiv({ cls: "sbr-row" });
		const open = row.createEl("button", { cls: "sbr-link", text: t("session.openCard") });
		open.onclick = () => void this.app.workspace.openLinkText(loaded.card.path, "", "tab");
		const explain = row.createEl("button", { cls: "sbr-link", text: t("btn.explain") });
		explain.onclick = () => void this.plugin.explainCard(loaded.card);
		const skip = row.createEl("button", { cls: "sbr-link", text: t("session.skip") });
		skip.onclick = () => {
			this.queue.skip();
			void this.show();
		};
	}

	/** Rating buttons with the next interval of each rating. */
	ratingButtons(parent: HTMLElement, cardId: string, onRate: (r: number) => void, suggested?: number): void {
		const now = this.plugin.service.now();
		const preview = previewRatings(this.plugin.service.state(cardId), now);
		const box = parent.createDiv({ cls: "sbr-ratings" });
		for (const r of [1, 2, 3, 4]) {
			const b = box.createEl("button", { cls: r === suggested ? "sbr-suggested" : "" });
			b.createSpan({ text: `${r} ${ratingLabel(r)}` });
			b.createSpan({ cls: "sbr-when", text: formatInterval(preview[r] - now.ms, timeUnits()) });
			b.onclick = () => onRate(r);
		}
		this.keyHandler = onRate;
	}

	private async show(): Promise<void> {
		const root = this.contentEl;
		root.empty();
		root.addClass("sbr-session");
		this.keyHandler = null;
		this.revealHandler = null;
		const entry = this.queue.current();
		if (!entry) {
			root.createDiv({ cls: "sbr-card-title", text: t("session.done", { n: this.queue.reviewed.size }) });
			if (!this.queue.ratings) root.createDiv({ cls: "sbr-muted", text: t("queue.empty") });
			return;
		}
		let loaded: LoadedCard;
		try {
			loaded = await this.plugin.loadCard(entry.card);
		} catch (e) {
			new Notice(String(e));
			this.queue.skip();
			return this.show();
		}
		this.header(root, loaded, entry.kind);
		this.startedAt = Date.now();
		if (this.mode === "flip") {
			await this.showFlip(root, loaded);
			return;
		}
		this.runner = new QaCardRunner(this.plugin, this, root, loaded, async (state) => {
			this.runner = null;
			this.queue.rated(state, this.plugin.service.now());
			await this.show();
		});
		this.runner.start();
	}

	private async showFlip(root: HTMLElement, loaded: LoadedCard): Promise<void> {
		const body = root.createDiv();
		const qs = loaded.items.length ? loaded.items : null;
		if (!qs) body.createDiv({ cls: "sbr-muted", text: t("session.noItems", { section: this.plugin.settings.reviewSection }) });
		const answers: HTMLElement[] = [];
		if (qs) {
			for (const item of qs) {
				const q = body.createDiv({ cls: "sbr-question" });
				await this.renderMarkdown(q, item.question, loaded.card.path);
				const a = body.createDiv({ cls: "sbr-answer" });
				a.hide();
				await this.renderMarkdown(a, item.answer, loaded.card.path);
				answers.push(a);
			}
		} else {
			const a = body.createDiv({ cls: "sbr-answer" });
			a.hide();
			await this.renderMarkdown(a, loaded.essence || loaded.card.title, loaded.card.path);
			answers.push(a);
		}
		const actions = root.createDiv();
		const reveal = () => {
			if (!actions.isConnected || actions.dataset.revealed) return;
			actions.dataset.revealed = "1";
			for (const a of answers) a.show();
			actions.empty();
			this.ratingButtons(actions, loaded.card.id, (r) => void this.rateFlip(loaded, r));
		};
		const btn = actions.createEl("button", { cls: "mod-cta", text: t("session.showAnswer") });
		btn.onclick = reveal;
		this.revealHandler = reveal;
	}

	private busy = false;

	private async rateFlip(loaded: LoadedCard, rating: number): Promise<void> {
		if (this.busy) return;
		this.busy = true;
		try {
			const state = await this.plugin.service.record({ card: loaded.card.id, mode: "flip", rating, ms: Date.now() - this.startedAt });
			this.queue.rated(state, this.plugin.service.now());
		} catch (e) {
			new Notice(String(e));
		} finally {
			this.busy = false;
		}
		await this.show();
	}

	/** For the Q&A runner. */
	render(el: HTMLElement, markdown: string, path: string): Promise<void> {
		return this.renderMarkdown(el, markdown, path);
	}

	elapsed(): number {
		return Date.now() - this.startedAt;
	}

	setKeyHandler(fn: ((r: number) => void) | null): void {
		this.keyHandler = fn;
	}
}

function isTyping(evt: KeyboardEvent): boolean {
	const el = evt.target as HTMLElement | null;
	return !!el && (el.tagName === "TEXTAREA" || el.tagName === "INPUT" || el.isContentEditable);
}
