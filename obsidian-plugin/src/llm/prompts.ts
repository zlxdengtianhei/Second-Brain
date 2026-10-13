// Prompt templates: a Markdown file with [system] and [user] parts and {{name}} placeholders.
// The defaults below are the same text as examples/prompts/grade.md, explain.md and their .en.md
// versions (a test checks this). The plugin uses the one in the interface language.
import type { Lang } from "../i18n";
import type { ChatMessage } from "./ai";

export interface Template {
	system: string;
	user: string;
}

export function parseTemplate(text: string): Template {
	let body = text.replace(/\r\n/g, "\n").replace(/<!--[\s\S]*?-->/g, "");
	if (body.startsWith("---\n")) {
		const end = body.indexOf("\n---", 4);
		if (end >= 0) body = body.slice(body.indexOf("\n", end + 4) + 1);
	}
	const sys = /^\[system\][ \t]*$/m.exec(body);
	const usr = /^\[user\][ \t]*$/m.exec(body);
	if (!usr) return { system: sys ? body.slice(sys.index + sys[0].length).trim() : "", user: sys ? "" : body.trim() };
	const user = body.slice(usr.index + usr[0].length).trim();
	const system = sys && sys.index < usr.index ? body.slice(sys.index + sys[0].length, usr.index).trim() : "";
	return { system, user };
}

export function fill(text: string, vars: Record<string, string>): string {
	return text.replace(/\{\{\s*(\w+)\s*\}\}/g, (_, k: string) => vars[k] ?? "");
}

export function renderMessages(tpl: Template, vars: Record<string, string>): ChatMessage[] {
	const out: ChatMessage[] = [];
	const system = fill(tpl.system, vars).trim();
	if (system) out.push({ role: "system", content: system });
	out.push({ role: "user", content: fill(tpl.user, vars).trim() });
	return out;
}

export const DEFAULT_GRADE_TEMPLATE = `[system]
你是复习考官。只依据下面的「参考答案」和「命题」判分。参考里没有的内容，不判错，也不加分。用中文，术语保留英文。只输出一个 JSON 对象。

[user]
问题：{{question}}
参考答案：{{answer}}
本卡命题：
{{propositions}}
学习者的回答：
{{user_answer}}

输出：
{"verdict":"hit|partial|miss",
 "covered":["学习者答到的要点"],
 "missing":["漏掉的关键要点"],
 "wrong":["说错的内容，引用学习者原话"],
 "suggested_rating":1|2|3,
 "feedback":"不超过 60 字，先说对在哪，再说缺什么"}
评级：没答出或主要错误为 1；答对一部分、漏关键点为 2；要点齐全为 3。`;

export const DEFAULT_EXPLAIN_TEMPLATE = `[system]
你是这位学习者的讲师。只依据下面提供的知识库内容讲解；资料里没有的，说明“卡片里没写”，需要补充常识时标“补充”。用中文，术语保留英文。

[user]
请讲解当前这张卡。

## 学习者状态
{{learner_state}}

## 当前卡
{{card_body}}

## 它在知识地图中的位置
- 领域：{{domain_moc_title}}
- 支柱：{{pillar_title}} —— {{pillar_summary}}
- 支柱下的主题：{{pillar_topics}}
- 主题：{{topic}}；同主题的其他卡：{{sibling_titles}}

## 相关卡（标题｜关系｜本质）
{{related_lines}}

## 讲解要求
1. 先用两三句话说清这张卡解决什么问题、核心结论是什么。
2. 按「命题」逐条解释为什么成立；需要先修知识时，点名上面相关卡中的哪一张。
3. 说明它在支柱和主题里的位置：前面接什么，后面引出什么。
4. 给一个具体例子或反例。
5. 最后提两个自测问题，不给答案，留作之后的问答复述。
控制在 {{max_chars}} 字以内。`;

export const DEFAULT_GRADE_TEMPLATE_EN = `[system]
You are a review examiner. Grade only against the reference answer and the card's propositions below. Content that is not in the reference is neither wrong nor extra credit. Reply with exactly one JSON object.

[user]
Question: {{question}}
Reference answer: {{answer}}
Card propositions:
{{propositions}}
Learner's answer:
{{user_answer}}

Output:
{"verdict":"hit|partial|miss",
 "covered":["points the learner got"],
 "missing":["key points the learner left out"],
 "wrong":["incorrect statements, quoting the learner"],
 "suggested_rating":1|2|3,
 "feedback":"at most 40 words: first what is right, then what is missing"}
Rating: no answer or mainly wrong = 1; partly right but a key point missing = 2; all key points = 3.`;

export const DEFAULT_EXPLAIN_TEMPLATE_EN = `[system]
You are this learner's tutor. Explain only from the vault content below. When something is not in it, say "not in the card"; mark general knowledge you add as "extra".

[user]
Please explain the current card.

## Learner state
{{learner_state}}

## Current card
{{card_body}}

## Its place in the knowledge map
- Domain: {{domain_moc_title}}
- Pillar: {{pillar_title}} — {{pillar_summary}}
- Topics in this pillar: {{pillar_topics}}
- Topic: {{topic}}; other cards in this topic: {{sibling_titles}}

## Related cards (title | relation | essence)
{{related_lines}}

## What to do
1. In two or three sentences, say what problem this card solves and its main conclusion.
2. Go through the propositions and explain why each holds; when prior knowledge is needed, name the related card above.
3. Explain where it sits in the pillar and topic: what comes before, what it leads to.
4. Give one concrete example or counterexample.
5. End with two self-test questions without answers, for later recall practice.
Keep it under {{max_chars}} words.`;

export const DEFAULT_TEMPLATES: Record<Lang, { grade: string; explain: string }> = {
	zh: { grade: DEFAULT_GRADE_TEMPLATE, explain: DEFAULT_EXPLAIN_TEMPLATE },
	en: { grade: DEFAULT_GRADE_TEMPLATE_EN, explain: DEFAULT_EXPLAIN_TEMPLATE_EN },
};
