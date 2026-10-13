// Side view and code-block renderer for the today dashboard.
import { ItemView, MarkdownRenderChild, type WorkspaceLeaf } from "obsidian";
import { t } from "../i18n";
import type SecondBrainReviewPlugin from "../main";
import { dashboardModel, renderDashboard, type DashboardActions } from "./dashboard";

export const VIEW_TODAY = "secondbrain-review-today";

function actions(plugin: SecondBrainReviewPlugin): DashboardActions {
	return {
		flip: () => void plugin.startSession("flip"),
		qa: () => void plugin.startSession("qa"),
		explain: () => void plugin.explainActive(),
		refresh: () => void plugin.refreshAll(),
		knowledgeFolder: plugin.settings.knowledge,
	};
}

export function renderToday(plugin: SecondBrainReviewPlugin, el: HTMLElement, compact: boolean): void {
	const svc = plugin.service;
	const model = dashboardModel(svc.summary(), svc.queue(), {
		loaded: svc.loaded,
		newPerDay: plugin.settings.newPerDay,
		studyDomains: plugin.settings.studyDomains,
		titles: svc.domainTitles(),
	});
	renderDashboard(el, model, actions(plugin), compact);
}

/** Re-render at most once per animation frame burst. */
function throttled(fn: () => void): () => void {
	let pending = false;
	return () => {
		if (pending) return;
		pending = true;
		window.setTimeout(() => {
			pending = false;
			fn();
		}, 150);
	};
}

export class TodayView extends ItemView {
	private off: (() => void) | null = null;

	constructor(
		leaf: WorkspaceLeaf,
		private plugin: SecondBrainReviewPlugin,
	) {
		super(leaf);
	}

	getViewType(): string {
		return VIEW_TODAY;
	}

	getDisplayText(): string {
		return t("view.title");
	}

	getIcon(): string {
		return "calendar-check";
	}

	async onOpen(): Promise<void> {
		const render = throttled(() => renderToday(this.plugin, this.contentEl, false));
		this.off = this.plugin.service.onChange(render);
		renderToday(this.plugin, this.contentEl, false);
	}

	async onClose(): Promise<void> {
		this.off?.();
		this.off = null;
	}
}

/** ```secondbrain-review``` block: a live dashboard inside any note (for example HOME.md). */
export class TodayBlock extends MarkdownRenderChild {
	private off: (() => void) | null = null;

	constructor(
		el: HTMLElement,
		private plugin: SecondBrainReviewPlugin,
	) {
		super(el);
	}

	onload(): void {
		const render = throttled(() => renderToday(this.plugin, this.containerEl, true));
		this.off = this.plugin.service.onChange(render);
		renderToday(this.plugin, this.containerEl, true);
	}

	onunload(): void {
		this.off?.();
		this.off = null;
	}
}
