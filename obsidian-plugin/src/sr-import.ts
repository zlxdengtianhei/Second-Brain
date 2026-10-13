// One-time import of Spaced Repetition plugin schedules (<!--SR:...--> comments, sr-* front matter).
import { type ImportSeed, srSchedule } from "./registry";

export interface ImportPlan<C> {
	found: { card: C; seed: ImportSeed }[];
	/** Cards that have SR data but already have events (not imported again). */
	skipped: number;
}

/** Find cards with Spaced Repetition data. Reads every card once (Obsidian serves cached text). */
export async function planSrImport<C extends { id: string }>(
	cards: C[],
	deps: {
		frontmatter: (card: C) => Record<string, unknown> | undefined;
		read: (card: C) => Promise<string>;
		hasEvents: (card: C) => boolean;
	},
): Promise<ImportPlan<C>> {
	const plan: ImportPlan<C> = { found: [], skipped: 0 };
	for (const card of cards) {
		const fm = deps.frontmatter(card);
		let text = "";
		try {
			text = await deps.read(card);
		} catch {
			continue;
		}
		if (!text.includes("<!--SR:") && !(fm && "sr-due" in fm)) continue;
		const seed = srSchedule(text, fm);
		if (!seed) continue;
		if (deps.hasEvents(card)) plan.skipped++;
		else plan.found.push({ card, seed });
	}
	return plan;
}
