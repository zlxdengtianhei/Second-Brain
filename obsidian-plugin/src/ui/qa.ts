// Q&A mode for one card: ask each question, grade the typed answer with the model, let the learner confirm.
import { Notice } from "obsidian";
import { ratingLabel, t, timeUnits } from "../i18n";
import { graderName } from "../llm/ai";
import { gradeAnswer, type GradeOutcome } from "../llm/grading";
import type SecondBrainReviewPlugin from "../main";
import { type CardState, type EventItem, questionId, type ReviewItem } from "../registry";
import { formatInterval } from "../session";
import type { LoadedCard, ReviewModal } from "./review-modal";

const MAX_STORED_ANSWER = 2000;

/** The questions to ask; a card without question lines is recalled from its title. */
export function qaQuestions(loaded: { card: { title: string }; items: ReviewItem[]; essence: string }): ReviewItem[] {
	if (loaded.items.length) return loaded.items;
	const question = `复述：${loaded.card.title}`;
	return [{ q: questionId(question), question, answer: loaded.essence || loaded.card.title }];
}

/** Item record written into the event for one answered question. */
export function itemRecord(item: ReviewItem, rating: number, userAnswer: string, outcome: GradeOutcome | null): EventItem {
	const rec: EventItem = { q: item.q, rating };
	if (userAnswer.trim()) rec.answer = userAnswer.trim().slice(0, MAX_STORED_ANSWER);
	if (outcome?.graded) {
		rec.verdict = outcome.result.verdict;
		rec.grader = graderName(outcome.model);
		if (outcome.result.feedback) rec.feedback = outcome.result.feedback;
		if (outcome.result.missing.length) rec.missing = outcome.result.missing;
	} else {
		rec.grader = "self";
		if (!userAnswer.trim()) rec.verdict = "miss";
	}
	return rec;
}

export class QaCardRunner {
	private questions: ReviewItem[];
	private records: EventItem[] = [];
	private i = 0;
	private abort: AbortController | null = null;

	constructor(
		private plugin: SecondBrainReviewPlugin,
		private modal: ReviewModal,
		private root: HTMLElement,
		private loaded: LoadedCard,
		private onDone: (state: CardState | undefined) => Promise<void>,
	) {
		this.questions = qaQuestions(loaded);
	}

	start(): void {
		this.area = this.root.createDiv();
		void this.ask();
	}

	private area!: HTMLElement;

	private async ask(): Promise<void> {
		const area = this.area;
		area.empty();
		this.modal.setKeyHandler(null);
		const item = this.questions[this.i];
		area.createDiv({ cls: "sbr-session-meta", text: t("qa.question", { i: this.i + 1, n: this.questions.length }) });
		const q = area.createDiv({ cls: "sbr-question" });
		await this.modal.render(q, item.question, this.loaded.card.path);
		const input = area.createEl("textarea", { cls: "sbr-answer-input", attr: { placeholder: t("qa.placeholder") } });
		const row = area.createDiv({ cls: "sbr-row" });
		const submit = row.createEl("button", { cls: "mod-cta", text: t("qa.submit") });
		const dontKnow = row.createEl("button", { text: t("qa.dontKnow") });
		submit.onclick = () => void this.grade(item, input.value, area);
		dontKnow.onclick = () => void this.showResult(item, "", null, area);
		input.addEventListener("keydown", (e) => {
			if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
				e.preventDefault();
				submit.click();
			}
		});
		window.setTimeout(() => input.focus(), 0);
	}

	private async grade(item: ReviewItem, answer: string, area: HTMLElement): Promise<void> {
		if (!answer.trim()) return this.showResult(item, "", null, area);
		area.querySelectorAll("button").forEach((b) => ((b as HTMLButtonElement).disabled = true));
		const status = area.createDiv({ cls: "sbr-muted", text: t("qa.grading") });
		this.abort = new AbortController();
		const tpl = await this.plugin.gradeTemplate();
		const outcome = await gradeAnswer(
			this.plugin.llm,
			tpl,
			{ question: item.question, answer: item.answer, propositions: this.loaded.propositions, userAnswer: answer },
			this.abort,
		);
		this.abort = null;
		status.remove();
		await this.showResult(item, answer, outcome, area);
	}

	private async showResult(item: ReviewItem, answer: string, outcome: GradeOutcome | null, area: HTMLElement): Promise<void> {
		area.querySelectorAll("textarea, .sbr-row").forEach((el) => el.remove());
		if (answer.trim()) area.createDiv({ cls: "sbr-msg-user", text: answer });
		let suggested: number | undefined = answer.trim() ? undefined : 1;
		if (outcome?.graded) {
			const r = outcome.result;
			suggested = r.suggestedRating;
			const box = area.createDiv({ cls: "sbr-grade" });
			if (r.feedback) box.createDiv({ text: `${t("qa.feedback")}：${r.feedback}` });
			const list = (label: string, xs: string[]) => {
				if (!xs.length) return;
				box.createDiv({ cls: "sbr-muted", text: label });
				const ul = box.createEl("ul");
				for (const x of xs) ul.createEl("li", { text: x });
			};
			list(t("qa.covered"), r.covered);
			list(t("qa.missing"), r.missing);
			list(t("qa.wrong"), r.wrong);
			box.createDiv({ cls: "sbr-muted", text: t("qa.suggested", { rating: ratingLabel(r.suggestedRating) }) });
		} else if (outcome && !outcome.graded) {
			area.createDiv({ cls: "sbr-warn", text: t("qa.ungraded", { reason: outcome.reason }) });
		}
		area.createDiv({ cls: "sbr-muted", text: t("qa.reference") });
		const ref = area.createDiv({ cls: "sbr-answer" });
		await this.modal.render(ref, item.answer, this.loaded.card.path);

		const box = area.createDiv({ cls: "sbr-ratings" });
		const rate = (r: number) => {
			this.modal.setKeyHandler(null);
			this.records.push(itemRecord(item, r, answer, outcome));
			this.i++;
			if (this.i < this.questions.length) void this.ask();
			else void this.finish();
		};
		for (const r of [1, 2, 3, 4]) {
			const b = box.createEl("button", { cls: r === suggested ? "sbr-suggested" : "" });
			b.createSpan({ text: `${r} ${ratingLabel(r)}` });
			b.onclick = () => rate(r);
		}
		this.modal.setKeyHandler(rate);
	}

	private async finish(): Promise<void> {
		const area = this.area;
		area.empty();
		const graders = new Set(this.records.map((r) => r.grader ?? "self"));
		const llm = [...graders].find((g) => g.startsWith("llm:"));
		let state: CardState | undefined;
		try {
			state = await this.plugin.service.record({
				card: this.loaded.card.id,
				mode: "qa",
				items: this.records,
				grader: llm ?? "self",
				ms: this.modal.elapsed(),
			});
		} catch (e) {
			new Notice(String(e));
		}
		const rating = Math.min(...this.records.map((r) => r.rating));
		const now = this.plugin.service.now();
		const next = state ? t("session.next", { when: formatInterval(state.due - now.ms, timeUnits()) }) : "";
		area.createDiv({ cls: "sbr-card-title", text: t("qa.cardDone", { rating: ratingLabel(rating), next }) });
		const go = area.createEl("button", { cls: "mod-cta", text: t("qa.continue") });
		go.onclick = () => void this.onDone(state);
		this.modal.setKeyHandler(null);
		window.setTimeout(() => go.focus(), 0);
	}

	cancel(): void {
		this.abort?.abort();
	}
}
