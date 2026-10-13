// Stand-in for @obsidian-ai-providers/sdk in unit tests (the real SDK requires the Obsidian runtime).
export function supportsVersion(service: { version?: number } | null | undefined, required: number): boolean {
	return typeof service?.version === "number" && service.version >= required;
}
export async function initAI(): Promise<void> {}
export async function waitForAI() {
	return { promise: new Promise<never>(() => undefined), cancel: () => undefined };
}
