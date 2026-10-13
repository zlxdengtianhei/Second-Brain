// Today dashboard: one model, rendered in the side view and in ```secondbrain-review``` code blocks.
import type { Counts, Queue, Summary } from "../registry";
import { locale, t } from "../i18n";

export interface DomainRow {
	domain: string;
	label: string;
	studying: boolean;
	counts: Counts;
	/** Shares of the bar: unlearned, learning, review, mastered (sum 1, or all 0). */
	shares: [number, number, number, number];
}

export interface DashboardModel {
	loaded: boolean;
	date: string;
	due: number;
	/** Due today and not reviewed yet today (今日未复习). */
	dueNotReviewed: number;
	/** New cards started today, and the daily maximum. */
	newToday: number;
	newMax: number;
	/** New cards still in today's queue. */
	newLeft: number;
	doneToday: number;
	streak: number;
	totals: DomainRow;
	rows: DomainRow[];
	issues: number;
	orphans: number;
	emptyIndex: boolean;
}

function row(domain: string, label: string, counts: Counts, studying: boolean): DomainRow {
	const total = counts.total || 0;
	const s = (n: number) => (total ? n / total : 0);
	return {
		domain,
		label,
		studying,
		counts,
		shares: [s(counts.unlearned), s(counts.learning), s(counts.review), s(counts.mastered)],
	};
}

/** Build the numbers the dashboard shows. Study domains come first, then the rest by label. */
export function dashboardModel(
	summary: Summary,
	queue: Queue<{ id: string }>,
	opts: { loaded: boolean; newPerDay: number; studyDomains: string[]; titles: Map<string, string> },
): DashboardModel {
	const rows = [...summary.domains.entries()].map(([d, c]) =>
		row(d, opts.titles.get(d) ?? (d || "—"), c, opts.studyDomains.includes(d)),
	);
	rows.sort((a, b) => {
		if (a.studying !== b.studying) return a.studying ? -1 : 1;
		if (a.studying) return opts.studyDomains.indexOf(a.domain) - opts.studyDomains.indexOf(b.domain);
		return a.label.localeCompare(b.label, locale());
	});
	return {
		loaded: opts.loaded,
		date: summary.today,
		due: queue.due.length,
		dueNotReviewed: summary.totals.dueNotReviewedToday,
		newToday: summary.newToday,
		newMax: opts.newPerDay,
		newLeft: queue.new.length,
		doneToday: summary.totals.reviewedToday,
		streak: summary.streak,
		totals: row("", t("domain.total"), summary.totals, false),
		rows,
		issues: summary.issues,
		orphans: summary.orphans.length,
		emptyIndex: summary.totals.total === 0,
	};
}

export interface DashboardActions {
	flip(): void;
	qa(): void;
	explain(): void;
	refresh(): void;
	knowledgeFolder: string;
}

function stat(parent: HTMLElement, label: string, value: string, cls = ""): void {
	const box = parent.createDiv({ cls: `sbr-stat ${cls}` });
	box.createDiv({ cls: "sbr-stat-value", text: value });
	box.createDiv({ cls: "sbr-stat-label", text: label });
}

function bar(parent: HTMLElement, shares: DomainRow["shares"]): void {
	const el = parent.createDiv({ cls: "sbr-bar" });
	const names = ["unlearned", "learning", "review", "mastered"];
	shares.forEach((share, i) => {
		if (share <= 0) return;
		const seg = el.createDiv({ cls: `sbr-bar-${names[i]}` });
		seg.style.width = `${(share * 100).toFixed(2)}%`;
	});
}

export function renderDashboard(el: HTMLElement, m: DashboardModel, actions: DashboardActions, compact = false): void {
	el.empty();
	el.addClass("sbr-dashboard");
	if (!m.loaded) {
		el.createDiv({ cls: "sbr-muted", text: t("view.loading") });
		return;
	}
	const head = el.createDiv({ cls: "sbr-head" });
	head.createDiv({ cls: "sbr-title", text: `${t("view.title")} · ${m.date}` });
	const refresh = head.createEl("button", { cls: "sbr-link", text: t("btn.refresh") });
	refresh.onclick = () => actions.refresh();

	if (m.emptyIndex) {
		el.createDiv({ cls: "sbr-warn", text: t("empty.index", { folder: actions.knowledgeFolder }) });
		return;
	}

	const stats = el.createDiv({ cls: "sbr-stats" });
	stat(stats, t("stat.due"), t("stat.cards", { n: m.due }), m.due ? "sbr-accent" : "");
	stat(stats, t("stat.notReviewed"), t("stat.cards", { n: m.dueNotReviewed }), m.dueNotReviewed ? "sbr-accent" : "");
	stat(stats, t("stat.new"), t("stat.newQuota", { n: m.newToday, max: m.newMax }));
	stat(stats, t("stat.done"), t("stat.cards", { n: m.doneToday }));
	stat(stats, t("stat.streak"), t("stat.days", { n: m.streak }));

	const buttons = el.createDiv({ cls: "sbr-buttons" });
	const left = m.due + m.newLeft;
	const flip = buttons.createEl("button", { cls: "mod-cta", text: left ? t("btn.flipCount", { n: left }) : t("btn.flip") });
	flip.onclick = () => actions.flip();
	const qa = buttons.createEl("button", { text: t("btn.qa") });
	qa.onclick = () => actions.qa();
	const ex = buttons.createEl("button", { text: t("btn.explain") });
	ex.onclick = () => actions.explain();
	if (left === 0) el.createDiv({ cls: "sbr-muted", text: t("queue.empty") });

	{
		// In a note (compact) the table scrolls inside a fixed height, so long domain lists stay usable.
		const wrap = el.createDiv({ cls: compact ? "sbr-domains-wrap sbr-compact" : "sbr-domains-wrap" });
		const table = wrap.createEl("table", { cls: "sbr-domains" });
		const hr = table.createEl("thead").createEl("tr");
		for (const k of ["domain", "unlearned", "learning", "review", "mastered", "due"] as const) {
			hr.createEl("th", { text: t(`domain.header.${k}`) });
		}
		hr.createEl("th");
		const body = table.createEl("tbody");
		for (const r of [...m.rows, m.totals]) {
			const tr = body.createEl("tr", { cls: r === m.totals ? "sbr-total" : r.studying ? "sbr-studying" : "" });
			const name = tr.createEl("td", { text: r.label });
			if (r.studying) name.createSpan({ cls: "sbr-tag", text: t("domain.studying") });
			for (const n of [r.counts.unlearned, r.counts.learning, r.counts.review, r.counts.mastered, r.counts.due]) {
				tr.createEl("td", { cls: "sbr-num", text: String(n) });
			}
			bar(tr.createEl("td", { cls: "sbr-bar-cell" }), r.shares);
		}
	}
	if (m.issues) el.createDiv({ cls: "sbr-warn", text: t("issues.lines", { n: m.issues }) });
	if (m.orphans) el.createDiv({ cls: "sbr-muted", text: t("orphans", { n: m.orphans }) });
}
