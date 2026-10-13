// Minimal runtime stand-in for the "obsidian" module in unit tests (the npm package has types only).
export class Plugin {}
export class PluginSettingTab {}
export class Notice {
	constructor(public message: string) {}
}
export function parseYaml(_: string): unknown {
	throw new Error("parseYaml is not available in unit tests");
}
export function normalizePath(p: string): string {
	return p.replace(/\\/g, "/").replace(/\/+/g, "/").replace(/^\/|\/$/g, "");
}
