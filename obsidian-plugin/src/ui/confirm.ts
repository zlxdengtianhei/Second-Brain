import { type App, Modal, Setting } from "obsidian";
import { t } from "../i18n";

export class ConfirmModal extends Modal {
	constructor(
		app: App,
		private message: string,
		private action: string,
		private onConfirm: () => Promise<void>,
	) {
		super(app);
	}

	onOpen(): void {
		this.contentEl.createEl("p", { text: this.message });
		new Setting(this.contentEl)
			.addButton((b) => b.setButtonText(t("cancel")).onClick(() => this.close()))
			.addButton((b) =>
				b
					.setButtonText(this.action)
					.setCta()
					.onClick(async () => {
						b.setDisabled(true);
						await this.onConfirm();
						this.close();
					}),
			);
	}

	onClose(): void {
		this.contentEl.empty();
	}
}
