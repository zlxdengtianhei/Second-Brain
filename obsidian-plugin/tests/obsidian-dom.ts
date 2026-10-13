// Obsidian adds helper methods to DOM elements (createEl, empty, setText, ...). Tests that render UI
// install the subset the plugin uses on top of happy-dom.
type DomInfo = string | { cls?: string | string[]; text?: string; attr?: Record<string, string | number | boolean | null>; title?: string; type?: string; value?: string };

function apply(el: HTMLElement, o?: DomInfo): void {
	if (!o) return;
	if (typeof o === "string") return void el.classList.add(...o.split(/\s+/).filter(Boolean));
	const cls = Array.isArray(o.cls) ? o.cls : (o.cls ?? "").split(/\s+/);
	el.classList.add(...cls.filter(Boolean));
	if (o.text !== undefined) el.textContent = o.text;
	if (o.title !== undefined) el.title = o.title;
	if (o.type !== undefined) el.setAttribute("type", o.type);
	if (o.value !== undefined) (el as HTMLInputElement).value = o.value;
	for (const [k, v] of Object.entries(o.attr ?? {})) if (v !== null && v !== false) el.setAttribute(k, String(v));
}

const P = HTMLElement.prototype as any;
P.createEl = function (tag: string, o?: DomInfo, cb?: (el: HTMLElement) => void) {
	const el = document.createElement(tag);
	apply(el, o);
	this.appendChild(el);
	cb?.(el);
	return el;
};
P.createDiv = function (o?: DomInfo, cb?: (el: HTMLElement) => void) {
	return this.createEl("div", o, cb);
};
P.createSpan = function (o?: DomInfo, cb?: (el: HTMLElement) => void) {
	return this.createEl("span", o, cb);
};
P.empty = function () {
	while (this.firstChild) this.removeChild(this.firstChild);
};
P.setText = function (t: string) {
	this.textContent = t;
};
P.addClass = function (...c: string[]) {
	this.classList.add(...c);
};
P.removeClass = function (...c: string[]) {
	this.classList.remove(...c);
};
P.show = function () {
	this.style.display = "";
};
P.hide = function () {
	this.style.display = "none";
};
P.setAttr = function (k: string, v: unknown) {
	this.setAttribute(k, String(v));
};
