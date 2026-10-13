// In-app reminders: status bar count and one Notice on the first launch of each learning day.
import { Notice } from "obsidian";
import { t } from "../i18n";
import type SecondBrainReviewPlugin from "../main";
import { learningDay } from "../registry";

const NOTICE_KEY = "secondbrain-review:notice-day";

export interface ReminderCounts {
	due: number;
	fresh: number;
}

/** True when a notice should be shown now; the caller then stores today's index. */
export function shouldNotify(lastDay: number | null, today: number, counts: ReminderCounts, enabled: boolean): boolean {
	return enabled && lastDay !== today && counts.due + counts.fresh > 0;
}

export class Reminder {
	private el: HTMLElement | null = null;

	constructor(private plugin: SecondBrainReviewPlugin) {}

	start(): void {
		this.el = this.plugin.addStatusBarItem();
		this.el.addClass("mod-clickable");
		this.el.onclick = () => void this.plugin.openToday();
		this.plugin.service.onChange(() => this.update());
		this.update();
		this.notifyOnce();
	}

	private counts(): ReminderCounts {
		const q = this.plugin.service.queue();
		return { due: q.due.length, fresh: q.new.length };
	}

	update(): void {
		if (!this.el || !this.plugin.service.loaded) return;
		const c = this.counts();
		this.el.setText(t("status.bar", { n: c.due + c.fresh }));
		this.el.setAttr("aria-label", t("status.tooltip", { due: c.due, new: c.fresh }));
	}

	private notifyOnce(): void {
		const app = this.plugin.app;
		const today = learningDay(this.plugin.service.now());
		const saved = app.loadLocalStorage(NOTICE_KEY);
		const last = typeof saved === "number" ? saved : null;
		const c = this.counts();
		if (!shouldNotify(last, today, c, this.plugin.settings.startupNotice)) return;
		app.saveLocalStorage(NOTICE_KEY, today);
		const n = new Notice(t("notice.today", { due: c.due, new: c.fresh }), 10_000);
		n.noticeEl?.addEventListener("click", () => void this.plugin.openToday());
	}
}
