// Model access through the AI Providers plugin (the user configures providers there once).
import { initAI, supportsVersion, waitForAI } from "@obsidian-ai-providers/sdk";
import type { IAIProvider, IAIProvidersService } from "@obsidian-ai-providers/sdk";
import type { App, Plugin } from "obsidian";
import { t } from "../i18n";

export interface ChatMessage {
	role: "system" | "user" | "assistant";
	content: string;
}

export type Readiness = { ok: true; label: string } | { ok: false; reason: string };

export interface CompleteOptions {
	onProgress?: (text: string) => void;
	abort?: AbortController;
}

/** What grading and explanation need from a model. Tests use a fake. */
export interface LlmClient {
	ready(): Promise<Readiness>;
	complete(messages: ChatMessage[], opts?: CompleteOptions): Promise<string>;
}

export interface ProviderChoice {
	id: string;
	label: string;
}

/** Execute needs service API 3+ (Promise-returning execute with onProgress). */
const MIN_API = 3;
const WAIT_MS = 1500;

export class AiProvidersClient implements LlmClient {
	constructor(
		private app: App,
		private providerId: () => string,
	) {}

	/** Call once in onload. Does not block the plugin when AI Providers is missing. */
	static init(app: App, plugin: Plugin): void {
		void initAI(app, plugin, async () => undefined, { disableFallback: true });
	}

	private async service(): Promise<IAIProvidersService | null> {
		const existing = (this.app as App & { aiProviders?: IAIProvidersService }).aiProviders;
		if (existing) return existing;
		const resolver = await waitForAI();
		let timer = 0;
		const timeout = new Promise<null>((resolve) => {
			timer = window.setTimeout(() => resolve(null), WAIT_MS);
		});
		try {
			return await Promise.race([resolver.promise, timeout]);
		} catch {
			return null;
		} finally {
			window.clearTimeout(timer);
		}
	}

	async providers(): Promise<ProviderChoice[]> {
		const ai = await this.service();
		return (ai?.providers ?? []).map((p) => ({ id: p.id, label: providerLabel(p) }));
	}

	private async pick(): Promise<{ ai: IAIProvidersService; provider: IAIProvider } | { reason: string }> {
		const ai = await this.service();
		if (!ai || !supportsVersion(ai, MIN_API)) return { reason: t("llm.missing") };
		if (!ai.providers.length) return { reason: t("llm.noProvider") };
		const id = this.providerId();
		const provider = ai.providers.find((p) => p.id === id) ?? (ai.providers.length === 1 ? ai.providers[0] : undefined);
		if (!provider) return { reason: t("llm.pick") };
		return { ai, provider };
	}

	async ready(): Promise<Readiness> {
		const r = await this.pick();
		return "reason" in r ? { ok: false, reason: r.reason } : { ok: true, label: providerLabel(r.provider) };
	}

	async complete(messages: ChatMessage[], opts: CompleteOptions = {}): Promise<string> {
		const r = await this.pick();
		if ("reason" in r) throw new Error(r.reason);
		return r.ai.execute({
			provider: r.provider,
			messages,
			onProgress: (_chunk: string, text: string) => opts.onProgress?.(text),
			abortController: opts.abort ?? new AbortController(),
		});
	}
}

function providerLabel(p: IAIProvider): string {
	return p.model ? `${p.name} · ${p.model}` : p.name;
}

/** Model name for the event's `grader` field. */
export function graderName(label: string): string {
	return `llm:${label.replace(/\s+/g, " ").trim()}`;
}
