// The English and Chinese READMEs say the same things: same sections, same checklist steps,
// the same paths, commands and defaults, and interface labels that are the plugin's own strings.
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { LOCALES } from "../src/i18n";

const DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const README = { en: readFileSync(`${DIR}/README.md`, "utf8"), zh: readFileSync(`${DIR}/README.zh-CN.md`, "utf8") };

const spans = (s: string) => new Set([...s.matchAll(/`([^`\n]+)`/g)].map((m) => m[1]));
const headings = (s: string) => [...s.matchAll(/^## .+$/gm)].length;
const steps = (s: string) => [...s.matchAll(/^(\d+)\. \*\*/gm)].map((m) => m[1]);

/** Interface labels quoted in code spans: the same string in each README's language. */
const LABELS = (lang: "en" | "zh") => [
	LOCALES[lang]["status.bar"].replace("{n}", "N"),
	LOCALES[lang]["settings.language.auto"].replace(/\s*[(（]\{lang\}[)）]$/, ""),
];

describe("plugin READMEs", () => {
	it("link to each other", () => {
		expect(README.en).toContain("[中文说明](README.zh-CN.md)");
		expect(README.zh).toContain("[English](README.md)");
	});
	it("have the same sections and checklist steps", () => {
		expect(headings(README.zh)).toBe(headings(README.en));
		expect(steps(README.en)).toEqual(Array.from({ length: steps(README.en).length }, (_, i) => String(i + 1)));
		expect(steps(README.zh)).toEqual(steps(README.en));
	});
	it("quote the same paths, commands and defaults; interface labels are the plugin's strings", () => {
		const en = spans(README.en);
		const zh = spans(README.zh);
		for (const label of LABELS("en")) expect(en.has(label), label).toBe(true);
		for (const label of LABELS("zh")) expect(zh.has(label), label).toBe(true);
		expect([...en].filter((s) => !zh.has(s)).sort()).toEqual(LABELS("en").sort());
		expect([...zh].filter((s) => !en.has(s)).sort()).toEqual(LABELS("zh").sort());
	});
	it("relative links point to files that exist", () => {
		for (const text of Object.values(README)) {
			for (const [, target] of text.matchAll(/\]\(((?!https?:|#)[^)\s]+)\)/g)) {
				expect(existsSync(resolve(DIR, target)), target).toBe(true);
			}
		}
	});
});
