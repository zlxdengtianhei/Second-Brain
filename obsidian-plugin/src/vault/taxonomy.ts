import { cmpCodePoint } from "../registry";

// Taxonomy files (<taxonomy>/*.yaml): domain map title, pillars with `why`, topics in order.
// Pillar names are unique across the vault, so a card's `pillar` finds its map without the domain.

export interface PillarInfo {
	name: string;
	mapTitle: string;
	why: string;
	order: number;
	topics: string[];
}

export interface Taxonomy {
	pillars: Map<string, PillarInfo>;
	/** map title -> pillars in order */
	maps: Map<string, PillarInfo[]>;
	issues: string[];
}

export function emptyTaxonomy(): Taxonomy {
	return { pillars: new Map(), maps: new Map(), issues: [] };
}

/** Build the taxonomy from parsed YAML documents. */
export function buildTaxonomy(docs: { path: string; data: unknown }[]): Taxonomy {
	const tax = emptyTaxonomy();
	for (const { path, data } of docs) {
		if (typeof data !== "object" || data === null) {
			tax.issues.push(`${path}: not a mapping`);
			continue;
		}
		const d = data as Record<string, unknown>;
		const title = typeof d.title === "string" && d.title ? d.title : (path.split("/").pop() ?? path).replace(/\.ya?ml$/, "");
		const list: PillarInfo[] = [];
		const pillars = Array.isArray(d.pillars) ? d.pillars : [];
		pillars.forEach((p, i) => {
			if (typeof p !== "object" || p === null) return;
			const rec = p as Record<string, unknown>;
			const name = typeof rec.name === "string" ? rec.name.trim() : "";
			if (!name) return;
			const info: PillarInfo = {
				name,
				mapTitle: title,
				why: typeof rec.why === "string" ? rec.why.trim() : "",
				order: typeof rec.order === "number" ? rec.order : i + 1,
				topics: Array.isArray(rec.topics) ? rec.topics.filter((t): t is string => typeof t === "string") : [],
			};
			if (tax.pillars.has(name)) tax.issues.push(`${path}: pillar "${name}" also defined in ${tax.pillars.get(name)!.mapTitle}`);
			else tax.pillars.set(name, info);
			list.push(info);
		});
		list.sort((a, b) => a.order - b.order);
		tax.maps.set(title, list);
	}
	return tax;
}

/**
 * Tie-break rank for the new-card order: study-domain position, then pillar order, then topic position.
 * Cards without a known pillar or topic sort after the known ones in their domain.
 */
export function rankFor(
	card: { domain: string; pillar: string; topic: string },
	tax: Taxonomy,
	domainOrder: Map<string, number>,
): number {
	const d = domainOrder.get(card.domain) ?? domainOrder.size;
	const p = tax.pillars.get(card.pillar);
	const pillarPos = p ? Math.min(p.order, 998) : 999;
	const t = p ? p.topics.indexOf(card.topic) : -1;
	const topicPos = t >= 0 ? Math.min(t, 998) : 999;
	return d * 1_000_000 + pillarPos * 1_000 + topicPos;
}

/**
 * Cards with `rank` set (the Python twin is `rank_cards`). Domain order: the study domains as given,
 * else every domain sorted by code point.
 */
export function rankCards<C extends { domain: string; pillar: string; topic: string }>(
	cards: C[],
	tax: Taxonomy,
	studyDomains: readonly string[],
): (C & { rank: number })[] {
	const names = studyDomains.length ? [...studyDomains] : [...new Set(cards.map((c) => c.domain))].sort(cmpCodePoint);
	const order = new Map<string, number>();
	names.forEach((d, i) => order.set(d, i));
	return cards.map((c) => ({ ...c, rank: rankFor(c, tax, order) }));
}

/** Map title of each domain, by the most common pillar map among the domain's cards. */
export function domainMapTitles(cards: Iterable<{ domain: string; pillar: string }>, tax: Taxonomy): Map<string, string> {
	const votes = new Map<string, Map<string, number>>();
	for (const c of cards) {
		const p = tax.pillars.get(c.pillar);
		if (!p) continue;
		let m = votes.get(c.domain);
		if (!m) votes.set(c.domain, (m = new Map()));
		m.set(p.mapTitle, (m.get(p.mapTitle) ?? 0) + 1);
	}
	const out = new Map<string, string>();
	for (const [domain, m] of votes) {
		const best = [...m.entries()].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1))[0];
		if (best) out.set(domain, best[0]);
	}
	return out;
}
