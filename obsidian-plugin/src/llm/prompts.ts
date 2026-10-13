// Prompt templates: a Markdown file with [system] and [user] parts and {{name}} placeholders.
// The defaults below are the same text as examples/prompts/grade.md and explain.md (a test checks this).
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
