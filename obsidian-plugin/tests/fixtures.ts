// Loads the shared fixtures in <repo>/tests/fixtures/review/.
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// Path arithmetic instead of new URL(): under happy-dom the global URL is the DOM one.
export const FIXTURES = resolve(dirname(fileURLToPath(import.meta.url)), "../../tests/fixtures/review") + "/";

export function fixture<T = any>(name: string): T {
	return JSON.parse(readFileSync(FIXTURES + name, "utf8")) as T;
}

export function fixtureText(name: string): string {
	return readFileSync(FIXTURES + name, "utf8");
}

/** Same rule as the Python suite: lists become lines, objects are JSON-encoded. */
export function fileText(content: string | (string | object)[]): string {
	if (typeof content === "string") return content;
	return content.map((c) => (typeof c === "string" ? c : JSON.stringify(c))).join("\n") + "\n";
}
