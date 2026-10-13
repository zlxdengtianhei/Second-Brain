// Settings tab.
import { type App, PluginSettingTab, Setting } from "obsidian";
import { t } from "../i18n";
import type SecondBrainReviewPlugin from "../main";

export class ReviewSettingTab extends PluginSettingTab {
	constructor(
		app: App,
		private plugin: SecondBrainReviewPlugin,
	) {
		super(app, plugin);
	}

	display(): void {
		const { containerEl } = this;
		const p = this.plugin;
		const s = p.settings;
		containerEl.empty();
		const save = async () => {
			await p.saveData(s);
			p.service.notify();
		};
		const text = (name: string, value: string, set: (v: string) => void, desc = "") =>
			new Setting(containerEl)
				.setName(name)
				.setDesc(desc)
				.addText((c) =>
					c.setValue(value).onChange(async (v) => {
						set(v.trim());
						await p.saveData(s);
					}),
				);
		const num = (name: string, value: number, set: (v: number) => void, min = 0) =>
			new Setting(containerEl).setName(name).addText((c) => {
				c.inputEl.type = "number";
				c.setValue(String(value)).onChange(async (v) => {
					const n = Math.round(Number(v));
					if (Number.isFinite(n) && n >= min) {
						set(n);
						await p.saveData(s);
					}
				});
			});

		new Setting(containerEl).setName(t("settings.study")).setHeading();
		num(t("settings.newPerDay"), s.newPerDay, (v) => (s.newPerDay = v));
		const domainsBox = new Setting(containerEl).setName(t("settings.domains")).setDesc(t("settings.domains.desc"));
		const titles = p.service.domainTitles();
		const domains = [...new Set(p.index.all().map((c) => c.domain))].filter(Boolean).sort();
		const list = containerEl.createDiv({ cls: "sbr-domain-list" });
		for (const d of domains) {
			new Setting(list).setName(titles.get(d) ?? d).setDesc(d).addToggle((tg) =>
				tg.setValue(s.studyDomains.includes(d)).onChange(async (on) => {
					s.studyDomains = on ? [...s.studyDomains.filter((x) => x !== d), d] : s.studyDomains.filter((x) => x !== d);
					await save();
				}),
			);
		}
		if (!domains.length) domainsBox.setDesc(t("empty.index", { folder: s.knowledge }));

		new Setting(containerEl).setName(t("settings.llm")).setHeading();
		const provider = new Setting(containerEl).setName(t("settings.provider")).setDesc(t("settings.provider.desc"));
		void p.llm.providers().then((choices) => {
			if (!choices.length) {
				provider.setDesc(`${t("settings.provider.desc")} ${t("llm.missing")} / ${t("llm.noProvider")}`);
				return;
			}
			provider.addDropdown((dd) => {
				dd.addOption("", t("settings.provider.none"));
				for (const c of choices) dd.addOption(c.id, c.label);
				dd.setValue(s.providerId).onChange(async (v) => {
					s.providerId = v;
					await p.saveData(s);
				});
			});
		});
		text(t("settings.gradeTemplate"), s.gradeTemplate, (v) => (s.gradeTemplate = v), t("settings.template.desc"));
		text(t("settings.explainTemplate"), s.explainTemplate, (v) => (s.explainTemplate = v), t("settings.template.desc"));
		new Setting(containerEl).setName(t("settings.explainLimits")).then((row) => {
			const field = (value: number, set: (n: number) => void) =>
				row.addText((c) => {
					c.inputEl.type = "number";
					c.inputEl.style.width = "5em";
					c.setValue(String(value)).onChange(async (v) => {
						const n = Math.round(Number(v));
						if (Number.isFinite(n) && n > 0) {
							set(n);
							await p.saveData(s);
						}
					});
				});
			field(s.explainMaxRelated, (n) => (s.explainMaxRelated = n));
			field(s.explainEssenceChars, (n) => (s.explainEssenceChars = n));
			field(s.explainSiblings, (n) => (s.explainSiblings = n));
			field(s.explainCardChars, (n) => (s.explainCardChars = n));
		});
		num(t("settings.explainAnswerChars"), s.explainAnswerChars, (v) => (s.explainAnswerChars = v), 50);

		new Setting(containerEl).setName(t("settings.reminder")).setHeading();
		new Setting(containerEl).setName(t("settings.startupNotice")).addToggle((tg) =>
			tg.setValue(s.startupNotice).onChange(async (v) => {
				s.startupNotice = v;
				await p.saveData(s);
			}),
		);
		text(t("settings.reminderTime"), s.reminderTime, (v) => (s.reminderTime = v));
		new Setting(containerEl)
			.setName(t("settings.icsPath"))
			.addText((c) =>
				c.setValue(s.icsPath).onChange(async (v) => {
					s.icsPath = v.trim();
					await p.saveData(s);
				}),
			)
			.addButton((b) => b.setButtonText(t("settings.icsWrite")).onClick(() => void p.writeIcs()));

		new Setting(containerEl).setName(t("settings.folders")).setHeading();
		text(t("settings.knowledge"), s.knowledge, (v) => (s.knowledge = v));
		text(t("settings.taxonomy"), s.taxonomy, (v) => (s.taxonomy = v));
		text(t("settings.data"), s.data, (v) => (s.data = v));
		text(t("settings.section"), s.reviewSection, (v) => (s.reviewSection = v));
		new Setting(containerEl)
			.setName(t("settings.logFormat"))
			.setDesc(t("settings.logFormat.desc"))
			.addDropdown((dd) =>
				dd
					.addOption("jsonl", "jsonl")
					.addOption("md", "md")
					.setValue(s.logFormat)
					.onChange(async (v) => {
						s.logFormat = v === "md" ? "md" : "jsonl";
						await p.saveData(s);
					}),
			);
		new Setting(containerEl)
			.setName(t("settings.import"))
			.setDesc(t("settings.import.desc"))
			.addButton((b) => b.setButtonText(t("import.go")).onClick(() => void p.importSr()));
		new Setting(containerEl).setDesc(t("settings.device", { device: p.service.store.device }));
	}

	hide(): void {
		// Folder and plan changes take effect after the tab closes.
		void this.plugin.saveSettings();
	}
}
