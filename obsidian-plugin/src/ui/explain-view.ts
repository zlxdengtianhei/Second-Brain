// Side view that explains a card with the model, then takes follow-up questions.
import { Component, ItemView, MarkdownRenderer, Notice, TFile, type WorkspaceLeaf } from "obsidian";
import { ratingLabel, t } from "../i18n";
import type { ChatMessage } from "../llm/ai";
import { explainVars, learnerState, selectRelations, siblingTitles } from "../llm/explain-context";
import { renderMessages } from "../llm/prompts";
import type SecondBrainReviewPlugin from "../main";
import { sectionText } from "../registry";
import type { IndexedCard } from "../vault/card-index";

export const VIEW_EXPLAIN = "secondbrain-review-explain";

export class ExplainView extends ItemView {
	private card: IndexedCard | null = null;
	private messages: ChatMessage[] = [];
	private abort: AbortController | null = null;
	private md = new Component();
	private logEl!: HTMLElement;
	private infoEl!: HTMLElement;
	private inputEl!: HTMLTextAreaElement;

	constructor(
		leaf: WorkspaceLeaf,
		private plugin: SecondBrainReviewPlugin,
	) {
		super(leaf);
	}

	getViewType(): string {
		return VIEW_EXPLAIN;
	}

	getDisplayText(): string {
		return this.card ? `${t("explain.title")}：${this.card.title}` : t("explain.title");
	}

	getIcon(): string {
		return "graduation-cap";
	}

	async onOpen(): Promise<void> {
		this.md.load();
		const root = this.contentEl;
		root.empty();
		root.addClass("sbr-explain");
		this.infoEl = root.createDiv({ cls: "sbr-muted", text: t("explain.noCard") });
		this.logEl = root.createDiv({ cls: "sbr-explain-log" });
		this.inputEl = root.createEl("textarea", { cls: "sbr-explain-input", attr: { placeholder: t("explain.ask") } });
		const row = root.createDiv({ cls: "sbr-row" });
		row.createEl("button", { cls: "mod-cta", text: t("explain.send") }).onclick = () => void this.followUp();
		row.createEl("button", { text: t("explain.again") }).onclick = () => {
			if (this.card) void this.explain(this.card);
		};
		row.createEl("button", { text: t("explain.stop") }).onclick = () => this.abort?.abort();
		this.inputEl.addEventListener("keydown", (e) => {
			if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
				e.preventDefault();
				void this.followUp();
			}
		});
	}

	async onClose(): Promise<void> {
		this.abort?.abort();
		this.md.unload();
	}

	/** Build the context and ask for an explanation of this card. */
	async explain(card: IndexedCard): Promise<void> {
		this.abort?.abort();
		this.card = card;
		this.messages = [];
		this.logEl.empty();
		(this.leaf as WorkspaceLeaf & { updateHeader?: () => void }).updateHeader?.();
		const messages = await this.buildMessages(card);
		if (!messages) return;
		this.messages = messages;
		await this.run();
	}

	private async buildMessages(card: IndexedCard): Promise<ChatMessage[] | null> {
		const p = this.plugin;
		const s = p.settings;
		let text: string;
		try {
			text = (await p.loadCard(card)).text;
		} catch (e) {
			new Notice(String(e));
			return null;
		}
		const related = [];
		for (const rel of selectRelations(card.relationList, s.explainMaxRelated)) {
			const target = p.index.get(rel.target);
			let essence = "";
			if (target) {
				const file = p.app.vault.getAbstractFileByPath(target.path);
				if (file instanceof TFile) essence = sectionText(await p.app.vault.cachedRead(file), "本质");
			}
			related.push({ title: target?.title ?? rel.target, type: rel.type, essence });
		}
		const svc = p.service;
		const vars = explainVars({
			card,
			cardText: text,
			related,
			pillar: svc.taxonomy.pillars.get(card.pillar),
			domainTitle: svc.domainTitles().get(card.domain) ?? card.domain,
			siblings: siblingTitles(card, svc.rankedCards().slice().sort((a, b) => (a.rank ?? 0) - (b.rank ?? 0)), s.explainSiblings),
			learnerState: learnerState(svc.history(card.id, 20), svc.state(card.id)?.reps ?? 0, {
				none: t("learner.none"),
				summary: (n, date, rating) => t("learner.summary", { n, date, rating: ratingLabel(rating) }),
				missing: (items) => t("learner.missing", { items }),
			}),
			limits: {
				maxRelated: s.explainMaxRelated,
				essenceChars: s.explainEssenceChars,
				siblings: s.explainSiblings,
				cardChars: s.explainCardChars,
				answerChars: s.explainAnswerChars,
			},
		});
		const messages = renderMessages(await p.explainTemplate(), vars);
		const chars = messages.reduce((n, m) => n + m.content.length, 0);
		this.infoEl.setText(`${card.title} · ${t("explain.context", { chars, related: related.length })}`);
		return messages;
	}

	private async followUp(): Promise<void> {
		const q = this.inputEl.value.trim();
		if (!q || !this.card || !this.messages.length) return;
		this.inputEl.value = "";
		this.messages.push({ role: "user", content: q });
		this.logEl.createDiv({ cls: "sbr-msg-user", text: q });
		await this.run();
	}

	private async run(): Promise<void> {
		const ready = await this.plugin.llm.ready();
		if (!ready.ok) {
			this.logEl.createDiv({ cls: "sbr-warn", text: ready.reason });
			return;
		}
		const out = this.logEl.createDiv({ cls: "sbr-msg-assistant sbr-streaming" });
		this.abort = new AbortController();
		const abort = this.abort;
		try {
			const text = await this.plugin.llm.complete(this.messages, {
				abort,
				onProgress: (sofar) => {
					out.setText(sofar);
					this.logEl.scrollTop = this.logEl.scrollHeight;
				},
			});
			this.messages.push({ role: "assistant", content: text });
			out.empty();
			out.removeClass("sbr-streaming");
			await MarkdownRenderer.render(this.app, text, out, this.card?.path ?? "", this.md);
		} catch (e) {
			if (!abort.signal.aborted) out.createDiv({ cls: "sbr-warn", text: t("llm.failed", { error: (e as Error).message ?? String(e) }) });
		} finally {
			if (this.abort === abort) this.abort = null;
		}
	}
}
