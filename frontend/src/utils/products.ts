// Seed <-> product-in-profile matching (2026-09-22). Same metric definition as backend models.ai_telemetry.find_product:
// exact match on name / alias (excluding aliases equal to its own category) wins first; otherwise the product term is contained in the seed, or the seed is most of the product term
// (>=3 chars and >= half the length). Only used to decide whether the frontend shows a hint; the real matching happens on the backend.
import type { BrandProduct } from '../services/aiTelemetryApi';

export function findProduct(products: BrandProduct[] | undefined, seed: string): BrandProduct | null {
  const s = (seed || '').trim().toLowerCase();
  if (s.length < 2) return null;
  let best: BrandProduct | null = null;
  let bestLen = 0;
  for (const p of products || []) {
    const cat = (p.category || '').trim().toLowerCase();
    const terms = [p.name, ...(p.aliases || [])]
      .map(x => (x || '').trim().toLowerCase())
      .filter(x => x.length >= 2 && x !== cat);
    for (const n of terms) {
      if (n === s) return p;
      const contained = s.includes(n) || (s.length >= 3 && n.includes(s) && s.length * 2 >= n.length);
      if (contained && n.length > bestLen) { best = p; bestLen = n.length; }
    }
  }
  return best;
}
