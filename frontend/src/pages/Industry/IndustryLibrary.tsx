// Industry library · list page (public, no login) — grid of industry cards; click into /industry/:key for industry details.
// Data from /api/industry/list (allowlisted industries, currently only cleaning robots; industries are listed automatically once onboarded).

import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { PageHead } from '../../components/PageHead';
import { industryApi, type IndustrySummary } from '../../services/industryApi';
import { engineLabel } from '../../constants/engines';
import { useIndustryLang } from './lang';

const INK = '#0f172a';
const INK_SUB = '#64748b';

export function IndustryLibrary() {
  const L = useIndustryLang();
  const [items, setItems] = useState<IndustrySummary[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    industryApi.list().then(r => setItems(r.industries)).catch(() => setFailed(true));
  }, []);

  return (
    <div className="min-h-screen" style={{ background: '#f6f7fb' }}>
      <PageHead titleKey="pageMeta.industry.title" titleFallback={L.libTitle} />
      <div style={{ background: 'linear-gradient(160deg,#dfe9ff 0%,#ece5ff 45%,#f6f4ff 100%)' }}>
        <div className="max-w-6xl mx-auto px-4 pt-14 pb-12 text-center">
          <h1 className="text-3xl sm:text-4xl font-bold" style={{ color: INK }}>{L.libTitle}</h1>
          <p className="mt-3 max-w-2xl mx-auto text-[13.5px] leading-relaxed" style={{ color: INK_SUB }}>{L.libSubtitle}</p>
        </div>
      </div>

      <div className="max-w-6xl mx-auto px-4 py-10">
        {failed && <div className="text-center text-sm py-16" style={{ color: INK_SUB }}>{L.loadFailed}</div>}
        {!failed && items === null && <div className="text-center text-sm py-16" style={{ color: INK_SUB }}>{L.loading}</div>}
        {items !== null && items.length === 0 && (
          <div className="text-center text-sm py-16" style={{ color: INK_SUB }}>{L.libEmpty}</div>
        )}
        <div className="grid gap-5" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))' }}>
          {(items ?? []).map(ind => (
            <Link key={ind.key} to={`/industry/${encodeURIComponent(ind.key)}`}
              className="block rounded-2xl p-6 shadow-sm transition-shadow hover:shadow-md"
              style={{ background: '#fff', border: '1px solid rgba(15,23,42,0.07)', textDecoration: 'none' }}>
              <div className="text-[18px] font-bold" style={{ color: INK }}>{ind.name}</div>
              <div className="mt-2 text-[11.5px]" style={{ color: INK_SUB }}>
                {L.libCardBrands(ind.brand_count)} · {L.libCardKeywords(ind.keyword_count)} · {L.libCardEngines(ind.engines.length)}
              </div>
              <div className="mt-4">
                <div className="text-[10.5px] font-semibold mb-1.5" style={{ color: INK_SUB }}>{L.libCardTopBrands}</div>
                <div className="flex flex-wrap gap-1.5">
                  {ind.top_brands.map((b, i) => (
                    <span key={b} className="text-[11px] font-medium rounded-full px-2.5 py-1"
                      style={{ background: i === 0 ? 'rgba(79,124,247,0.12)' : 'rgba(15,23,42,0.045)', color: i === 0 ? '#3757c9' : INK }}>
                      {i + 1}. {b}
                    </span>
                  ))}
                </div>
              </div>
              <div className="mt-4 flex flex-wrap gap-1.5">
                {ind.engines.map(e => (
                  <span key={e} className="text-[10.5px] rounded px-1.5 py-0.5" style={{ background: 'rgba(15,23,42,0.035)', color: INK_SUB }}>
                    {engineLabel(e)}
                  </span>
                ))}
              </div>
              <div className="mt-5 flex items-center justify-between">
                <span className="text-[10.5px]" style={{ color: INK_SUB }}>{L.libCardUpdated(ind.updated)}</span>
                <span className="text-[12px] font-semibold rounded-lg px-3.5 py-1.5 text-white"
                  style={{ background: 'linear-gradient(135deg,#3b82f6,#6366f1)' }}>
                  {L.libCardView} →
                </span>
              </div>
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}
