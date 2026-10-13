// One-time import of Spaced Repetition plugin schedules (<!--SR:...--> comments, sr-* front matter).
import { type ImportSeed, srSchedule, validateImportSeed } from "./registry";

export interface ImportPlan<C> {
	found: { card: C; seed: ImportSeed }[];
	/** Cards that have SR data but already have events: never imported twice. */
	skipped: number;
	/** Cards whose SR data cannot be read; reported, not imported. */
	invalid: { card: C; reason: string }[];
}

/**
 * Sort cards into importable, already imported and unreadable (the Python twin is `plan_sr_import`).
 * Reads every card once (Obsidian serves cached text); one card's bad data never stops the others.
 */
export async function planSrImport<C extends { id: string }>(
	cards: C[],
	deps: {
		frontmatter: (card: C) => Record<string, unknown> | undefined;
		read: (card: C) => Promise<string>;
		hasEvents: (card: C) => boolean;
	},
): Promise<ImportPlan<C>> {
	const plan: ImportPlan<C> = { found: [], skipped: 0, invalid: [] };
	for (const card of cards) {
		let seed: ImportSeed | null = null;
		let error: string | null = null;
		try {
			seed = srSchedule(await deps.read(card), deps.frontmatter(card));
			if (seed) validateImportSeed(seed);
		} catch (e) {
			seed = null;
			error = e instanceof Error ? e.message : String(e);
		}
		if (!seed && error === null) continue;
		if (deps.hasEvents(card)) plan.skipped++;
		else if (error !== null) plan.invalid.push({ card, reason: error });
		else plan.found.push({ card, seed: seed! });
	}
	return plan;
}
