import { afterEach, describe, expect, it } from "vitest";
import { setLocale } from "../src/i18n";
import type { ChatMessage, LlmClient, Readiness } from "../src/llm/ai";
import { gradeAnswer, parseGrade } from "../src/llm/grading";
import { DEFAULT_EXPLAIN_TEMPLATE, DEFAULT_GRADE_TEMPLATE, DEFAULT_TEMPLATES, fill, parseTemplate, renderMessages } from "../src/llm/prompts";
import { itemRecord, qaQuestions } from "../src/ui/qa";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const PROMPTS = fileURLToPath(new URL("../../examples/prompts/", import.meta.url));

afterEach(() => setLocale("zh"));

class FakeLlm implements LlmClient {
	calls: ChatMessage[][] = [];
	constructor(
		private reply: string | Error,
		private readiness: Readiness = { ok: true, label: "Fake · m1" },
	) {}
	async ready() {
		return this.readiness;
	}
	async complete(messages: ChatMessage[]) {
		this.calls.push(messages);
		if (this.reply instanceof Error) throw this.reply;
		return this.reply;
	}
}

describe("templates", () => {
	it("built-in defaults equal the example files", () => {
		expect(parseTemplate(readFileSync(PROMPTS + "grade.md", "utf8"))).toEqual(parseTemplate(DEFAULT_GRADE_TEMPLATE));
		expect(parseTemplate(readFileSync(PROMPTS + "explain.md", "utf8"))).toEqual(parseTemplate(DEFAULT_EXPLAIN_TEMPLATE));
	});
	it("English built-in defaults equal the English example files", () => {
		expect(parseTemplate(readFileSync(PROMPTS + "grade.en.md", "utf8"))).toEqual(parseTemplate(DEFAULT_TEMPLATES.en.grade));
		expect(parseTemplate(readFileSync(PROMPTS + "explain.en.md", "utf8"))).toEqual(parseTemplate(DEFAULT_TEMPLATES.en.explain));
		expect(DEFAULT_TEMPLATES.zh).toEqual({ grade: DEFAULT_GRADE_TEMPLATE, explain: DEFAULT_EXPLAIN_TEMPLATE });
	});
	it("English examples use the same placeholders", () => {
		const names = (s: string) => [...new Set([...s.matchAll(/\{\{(\w+)\}\}/g)].map((m) => m[1]))].sort();
		for (const f of ["grade", "explain"]) {
			const zh = readFileSync(PROMPTS + `${f}.md`, "utf8").replace(/<!--[\s\S]*?-->/, "");
			const en = readFileSync(PROMPTS + `${f}.en.md`, "utf8").replace(/<!--[\s\S]*?-->/, "");
			expect(names(en)).toEqual(names(zh));
		}
	});
	it("splits [system] and [user], strips comments, fills placeholders", () => {
		const tpl = parseTemplate("<!-- note -->\n[system]\nBe brief.\n\n[user]\nQ: {{question}} / {{ missing }}");
		expect(tpl).toEqual({ system: "Be brief.", user: "Q: {{question}} / {{ missing }}" });
		expect(renderMessages(tpl, { question: "Why?" })).toEqual([
			{ role: "system", content: "Be brief." },
			{ role: "user", content: "Q: Why? /" },
		]);
		expect(parseTemplate("just a user prompt")).toEqual({ system: "", user: "just a user prompt" });
		expect(fill("{{a}}{{b}}", { a: "1" })).toBe("1");
	});
});

describe("parseGrade", () => {
	const good = '{"verdict":"partial","covered":["sorted"],"missing":["invariant"],"wrong":[],"suggested_rating":2,"feedback":"ok"}';
	it("accepts the JSON contract, also inside a code fence", () => {
		expect(parseGrade(good)).toEqual({ verdict: "partial", covered: ["sorted"], missing: ["invariant"], wrong: [], suggestedRating: 2, feedback: "ok" });
		expect(parseGrade("Here:\n```json\n" + good + "\n```")?.suggestedRating).toBe(2);
	});
	it("caps a suggested Easy at Good, accepts a numeric string", () => {
		expect(parseGrade('{"verdict":"hit","suggested_rating":4}')?.suggestedRating).toBe(3);
		expect(parseGrade('{"verdict":"hit","suggested_rating":"3"}')?.suggestedRating).toBe(3);
	});
	it("rejects replies that break the contract", () => {
		for (const bad of ["no json here", "{not json}", '{"verdict":"great","suggested_rating":3}', '{"verdict":"hit"}', '{"verdict":"hit","suggested_rating":2.5}', "[1,2]"]) {
			expect(parseGrade(bad), bad).toBeNull();
		}
	});
});

describe("gradeAnswer", () => {
	const tpl = parseTemplate(DEFAULT_GRADE_TEMPLATE);
	const input = { question: "Q?", answer: "A.", propositions: "- P1", userAnswer: "my answer" };
	it("sends the filled template and returns the verdict with the model label", async () => {
		const llm = new FakeLlm('{"verdict":"hit","suggested_rating":3,"feedback":"good"}');
		const out = await gradeAnswer(llm, tpl, input);
		expect(out).toMatchObject({ graded: true, model: "Fake · m1", result: { verdict: "hit", suggestedRating: 3 } });
		const user = llm.calls[0].find((m) => m.role === "user")!.content;
		expect(user).toContain("问题：Q?");
		expect(user).toContain("my answer");
		expect(user).toContain("- P1");
	});
	it("with the English interface, the English template and an English note for a card without propositions", async () => {
		setLocale("en");
		const llm = new FakeLlm('{"verdict":"miss","suggested_rating":1}');
		await gradeAnswer(llm, parseTemplate(DEFAULT_TEMPLATES.en.grade), { ...input, propositions: "" });
		const user = llm.calls[0].find((m) => m.role === "user")!.content;
		expect(user).toContain("Question: Q?");
		expect(user).toContain("Card propositions:\n(this card has no propositions section)");
		const failed = await gradeAnswer(new FakeLlm(new Error("timeout")), tpl, input);
		expect(!failed.graded && failed.reason).toBe("The model call failed: timeout");
	});
	it("a card without propositions gets the same Chinese note as before", async () => {
		const llm = new FakeLlm('{"verdict":"miss","suggested_rating":1}');
		await gradeAnswer(llm, tpl, { ...input, propositions: "" });
		expect(llm.calls[0].find((m) => m.role === "user")!.content).toContain("本卡命题：\n（本卡没有命题节）");
	});
	it("falls back to self-rating when the model is missing, fails, or breaks the format", async () => {
		expect(await gradeAnswer(new FakeLlm("x", { ok: false, reason: "no provider" }), tpl, input)).toEqual({ graded: false, reason: "no provider" });
		const failed = await gradeAnswer(new FakeLlm(new Error("timeout")), tpl, input);
		expect(failed.graded).toBe(false);
		expect(!failed.graded && failed.reason).toContain("timeout");
		const invalid = await gradeAnswer(new FakeLlm("I think it is fine."), tpl, input);
		expect(invalid.graded).toBe(false);
	});
});

describe("Q&A records", () => {
	it("uses the card title and essence when the card has no question lines", () => {
		const qs = qaQuestions({ card: { title: "Binary search" }, items: [], essence: "Halve the interval." });
		expect(qs).toHaveLength(1);
		expect(qs[0].answer).toBe("Halve the interval.");
		expect(qs[0].q).toMatch(/^h:[0-9a-f]{8}$/);
		expect(qs[0].question).toBe("复述：Binary search");
		setLocale("en");
		expect(qaQuestions({ card: { title: "Binary search" }, items: [], essence: "" })[0]).toMatchObject({ question: "Recall: Binary search", answer: "Binary search" });
	});
	it("records grader, verdict and missing points", () => {
		const item = { q: "h:1", question: "Q", answer: "A" };
		const graded = itemRecord(item, 2, " typed ", { graded: true, model: "Fake · m1", result: { verdict: "partial", covered: [], missing: ["x"], wrong: [], suggestedRating: 2, feedback: "f" } });
		expect(graded).toEqual({ q: "h:1", rating: 2, answer: "typed", verdict: "partial", grader: "llm:Fake · m1", feedback: "f", missing: ["x"] });
		expect(itemRecord(item, 1, "", null)).toEqual({ q: "h:1", rating: 1, grader: "self", verdict: "miss" });
	});
});

describe("AiProvidersClient", async () => {
	const { AiProvidersClient } = await import("../src/llm/ai");
	const provider = (id: string, model?: string) => ({ id, name: `P${id}`, type: "openai" as const, model });
	function client(service: unknown, chosen = "") {
		(globalThis as any).window ??= globalThis;
		return new AiProvidersClient({ aiProviders: service } as any, () => chosen);
	}
	it("uses the chosen provider, or the only one", async () => {
		const svc = { version: 6, providers: [provider("1", "m-a"), provider("2")], execute: async (p: any) => `${p.provider.id}:${p.messages.length}` };
		expect(await client(svc, "2").ready()).toEqual({ ok: true, label: "P2" });
		expect(await client(svc, "2").complete([{ role: "user", content: "hi" }])).toBe("2:1");
		expect((await client(svc, "").ready()).ok).toBe(false);
		const single = { ...svc, providers: [provider("9", "m-z")] };
		expect(await client(single, "").ready()).toEqual({ ok: true, label: "P9 · m-z" });
	});
	it("reports a missing or too old AI Providers plugin and empty provider lists", async () => {
		expect((await client({ version: 2, providers: [provider("1")] }).ready()).ok).toBe(false);
		expect((await client({ version: 6, providers: [] }).ready()).ok).toBe(false);
	});
});
