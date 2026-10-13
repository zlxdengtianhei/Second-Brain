// Minimal runtime stand-in for the "obsidian" module in unit tests (the npm package has types only).
// Classes that touch the DOM are only constructed in tests that run under happy-dom.
export class Component {
	load(): void {}
	unload(): void {}
	addChild<T>(c: T): T {
		return c;
	}
	register(): void {}
	registerEvent(): void {}
}
export class Plugin extends Component {}
export class PluginSettingTab {}
export class ItemView extends Component {}
export class MarkdownRenderChild extends Component {}
export class TFile {}
export const notices: string[] = [];
export class Notice {
	noticeEl = { addEventListener() {} };
	constructor(public message: string) {
		notices.push(message);
	}
	hide(): void {}
}
export class Scope {
	keys: { mods: string[]; key: string; fn: (evt: KeyboardEvent) => boolean | void }[] = [];
	register(mods: string[], key: string, fn: (evt: KeyboardEvent) => boolean | void) {
		this.keys.push({ mods, key, fn });
		return {};
	}
	/** Test helper: press a key as Obsidian would route it to the modal. */
	press(key: string, target: EventTarget | null = null): void {
		for (const k of this.keys) if (k.key === key) k.fn({ target } as unknown as KeyboardEvent);
	}
}
export class Modal extends Component {
	app: unknown;
	modalEl: HTMLElement;
	titleEl: HTMLElement;
	contentEl: HTMLElement;
	scope = new Scope();
	constructor(app: unknown) {
		super();
		this.app = app;
		this.modalEl = document.createElement("div");
		this.titleEl = this.modalEl.appendChild(document.createElement("div"));
		this.contentEl = this.modalEl.appendChild(document.createElement("div"));
		document.body.appendChild(this.modalEl);
	}
	onOpen(): void {}
	onClose(): void {}
	open(): void {
		this.onOpen();
	}
	close(): void {
		this.onClose();
	}
}
export const MarkdownRenderer = {
	async render(_app: unknown, markdown: string, el: HTMLElement): Promise<void> {
		el.appendChild(document.createTextNode(markdown));
	},
};
export function parseYaml(_: string): unknown {
	throw new Error("parseYaml is not available in unit tests");
}
export function normalizePath(p: string): string {
	return p.replace(/\\/g, "/").replace(/\/+/g, "/").replace(/^\/|\/$/g, "");
}
