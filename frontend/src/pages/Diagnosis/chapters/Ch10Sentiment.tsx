// Chapter 10 Sentiment analysis — risk level / positive and negative reputation / negative details / response suggestions
import { useTranslation } from 'react-i18next';
import { subData, StatBox, Prose, NoDataCard, ChapterDataStatus } from '../reportKit';
import type { ChapterCtx } from './types';
import type { SentimentPostItem } from '../../../services/fullDiagnosisApi';

export function Ch10Sentiment({ report, n, sub }: ChapterCtx) {
  const { t } = useTranslation();
  const s = subData(sub, 'sentiment');
  const summary = n.sentiment_summary;

  if (!s) {
    return (
      <>
        <ChapterDataStatus steps={report.progress?.steps} modes={['sentiment']} sub={sub} />
        <NoDataCard />
      </>
    );
  }

  const kpi = (s.kpi || {}) as Record<string, any>;
  const posts = (s.posts || []) as SentimentPostItem[];
  const negatives = (s.negative_posts || []) as SentimentPostItem[];
  const range = (s.evidence_range || {}) as { from?: string; to?: string };
  const riskColor = summary?.risk_level === '高' ? 'var(--danger-fg)'
    : summary?.risk_level === '中' ? 'var(--warn-fg)' : 'var(--success-fg)';

  return (
    <>
      <ChapterDataStatus steps={report.progress?.steps} modes={['sentiment']} sub={sub} />

      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 items-center">
        <StatBox label={t('diagnosis.report.sections.sentiment.riskLevel')}
                 value={<span style={{ color: riskColor }}>
                   {summary?.risk_level || t('diagnosis.report.noData')}
                 </span>} />
        <StatBox label={t('diagnosis.report.sections.sentiment.totalPosts')} value={posts.length} />
        <StatBox label={t('diagnosis.report.sections.sentiment.negativeCount')}
                 value={<span style={{ color: negatives.length ? 'var(--danger-fg)' : 'inherit' }}>
                   {Number(s.negative_count ?? negatives.length)}
                 </span>} />
        <StatBox label={t('diagnosis.report.sections.sentiment.highRisk')} value={kpi.high_risk ?? 0} />
        <PositiveRing data={s} posts={posts} />
      </div>

      <ToneGauge data={s} />

      {/* Evidence time range: Yuanbao WebSearch doesn't filter by time and may return decade-old news,
          so the real span must be shown most prominently, lest readers assume it all happened recently */}
      {(range.from || range.to) && (
        <p className="text-xs text-muted m-0">
          {t('diagnosis.report.sections.sentiment.evidenceRange', {
            from: range.from || '—', to: range.to || '—',
            recent: Number(s.recent_count ?? 0),
            window: Number(s.window_days ?? 30),
            historical: Number(s.historical_count ?? 0),
            undated: Number(s.undated_count ?? 0),
            defaultValue: '证据时间范围 {{from}} ~ {{to}};其中近 {{window}} 天内 {{recent}} 条、更早 {{historical}} 条、时间不详 {{undated}} 条。',
          })}
        </p>
      )}

      {(summary?.positive_points?.length ?? 0) > 0 && (
        <div>
          <h3 className="text-sm font-bold text-primary mb-2">
            ✦ {t('diagnosis.report.sections.sentiment.positivePoints')}
          </h3>
          <div className="flex flex-wrap gap-1.5">
            {summary!.positive_points!.map((p, i) => (
              <span key={i} className="px-2 py-0.5 rounded-md text-xs"
                    style={{ background: 'var(--success-bg)', color: 'var(--success-fg)' }}>{p}</span>
            ))}
          </div>
        </div>
      )}

      {summary?.summary && <Prose tinted>{summary.summary}</Prose>}

      {(summary?.negative_points?.length ?? 0) > 0 && (
        <div>
          <h3 className="text-sm font-bold text-primary mb-2">
            ⚠ {t('diagnosis.report.sections.sentiment.negativePoints')}
          </h3>
          <div className="flex flex-wrap gap-1.5">
            {summary!.negative_points!.map((p, i) => (
              <span key={i} className="px-2 py-0.5 rounded-md text-xs"
                    style={{ background: 'var(--danger-bg)', color: 'var(--danger-fg)' }}>{p}</span>
            ))}
          </div>
        </div>
      )}

      {negatives.length > 0 && (
        <div>
          <h3 className="text-sm font-bold text-primary mb-2">
            {t('diagnosis.report.sections.sentiment.negativeList')}
          </h3>
          <div className="space-y-2">
            {negatives.slice(0, 6).map((p, i) => {
              const source = displaySource(p.source);
              // Without a real date say "time unknown" plainly -- this used to backfill the batch-run date,
              // effectively stamping every old story with "today"
              const date = p.publish_time
                ? String(p.publish_time).slice(0, 10)
                : t('diagnosis.report.sections.sentiment.undatedTime', { defaultValue: '时间未知' });
              const aged = p.recency && p.recency !== 'recent'
                ? t('diagnosis.report.sections.sentiment.historicalTag', { defaultValue: '历史' })
                : '';
              const meta = [source, date, aged].filter(Boolean).join(' · ');
              return (
                <div key={i} className="p-3 rounded-lg"
                     style={{ background: 'var(--danger-bg)', borderLeft: '3px solid var(--danger-solid)' }}>
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="text-sm font-medium text-primary">{p.title || p.summary || '—'}</span>
                    {meta && <span className="text-xs text-muted whitespace-nowrap">{meta}</span>}
                  </div>
                  {p.summary && p.title && (
                    <p className="text-sm text-secondary leading-relaxed mt-1 m-0">{p.summary}</p>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {(summary?.suggestions?.length ?? 0) > 0 && (
        <div>
          <h3 className="text-sm font-bold text-primary mb-2">
            {t('diagnosis.report.sections.sentiment.suggestions')}
          </h3>
          <div className="space-y-1.5">
            {summary!.suggestions!.map((sg, i) => (
              <p key={i} className="text-sm text-primary leading-relaxed m-0">{i + 1}. {sg}</p>
            ))}
          </div>
        </div>
      )}
    </>
  );
}

function displaySource(source?: string | null) {
  const s = String(source || '').trim();
  return /yuanbao|websearch|searchpro|tencent/i.test(s) ? '' : s;
}

/** Overall sentiment scale bar: a dot on a -1~1 axis, green for positive, red for negative, gray within ±0.2 (essentially neutral).
 *  The value is the model's overall score across all search results when summarizing Yuanbao WebSearch (kpi.avg_sentiment),
 *  not a per-item average, and it doesn't feed sentiment health -- so it's on its own row, not with the five-dimension scores.
 *  The tier thresholds match the backend writing prompt (full_diagnosis_generator sentiment section); change one, sync the other. */
function ToneGauge({ data }: { data: Record<string, unknown> }) {
  const { t } = useTranslation();
  const raw = ((data.kpi || {}) as { avg_sentiment?: unknown }).avg_sentiment;
  // When the model gives no score and the label is unknown, the backend falls back to 0 -- that's "not judged", not "neutral", so don't draw it
  const label = String(((data.websearch || {}) as { sentiment_label?: unknown }).sentiment_label || '').toLowerCase();
  if (typeof raw !== 'number' || !Number.isFinite(raw) || (raw === 0 && label === 'unknown')) return null;

  const v = Math.max(-1, Math.min(1, raw));
  const k = 'diagnosis.report.sections.sentiment.';
  const [toneKey, color] =
    v >= 0.6 ? ['toneStrongPos', 'var(--success-solid)']
    : v >= 0.2 ? ['tonePos', 'var(--success-solid)']
    : v > -0.2 ? ['toneNeutral', '#9ca3af']
    : v > -0.6 ? ['toneNeg', 'var(--danger-solid)']
    : ['toneStrongNeg', 'var(--danger-solid)'];
  const left = `${((v + 1) / 2) * 100}%`;

  return (
    <div className="p-4 rounded-xl border border-soft">
      <div className="flex items-baseline justify-between mb-3">
        <span className="text-sm font-bold text-primary">{t(k + 'toneTitle')}</span>
        <span className="text-sm font-bold tabular-nums" style={{ color }}>
          {(v >= 0 ? '+' : '') + v.toFixed(2)} · {t(k + toneKey)}
        </span>
      </div>
      <div className="relative mx-2" style={{ height: 18 }}>
        {/* Track: red -> gray -> green, with ±0.2 in the middle as the neutral band */}
        <div className="absolute left-0 right-0 rounded-full" style={{
          top: 6, height: 6,
          background: 'linear-gradient(to right, rgba(239,68,68,0.45) 0%, rgba(239,68,68,0.15) 40%, rgba(156,163,175,0.25) 40%, rgba(156,163,175,0.25) 60%, rgba(34,197,94,0.15) 60%, rgba(34,197,94,0.45) 100%)',
        }} />
        <div className="absolute" style={{ left: '50%', top: 2, width: 1, height: 14, background: 'var(--text-muted)' }} />
        <div className="absolute rounded-full" style={{
          left, top: 0, width: 18, height: 18, marginLeft: -9,
          background: color, border: '2px solid var(--bg-card)', boxShadow: '0 1px 3px rgba(0,0,0,0.25)',
        }} />
      </div>
      <div className="relative mx-2 mt-1 text-xs text-muted tabular-nums" style={{ height: 16 }}>
        <span className="absolute" style={{ left: 0, transform: 'translateX(-50%)' }}>-1</span>
        <span className="absolute" style={{ left: '50%', transform: 'translateX(-50%)' }}>0</span>
        <span className="absolute" style={{ right: 0, transform: 'translateX(50%)' }}>+1</span>
      </div>
      <p className="text-xs text-muted mt-2 m-0">{t(k + 'toneNote')}</p>
    </div>
  );
}

/** Positive share ring: prefer the backend's three-class counts, fall back to computing from post labels */
function PositiveRing({ data, posts }: { data: Record<string, any>; posts: SentimentPostItem[] }) {
  const { t } = useTranslation();
  const stance = data.stance as { pos?: number; neg?: number; classified?: number } | undefined;

  let posPct: number | null = null;
  if (stance && (stance.classified ?? 0) > 0) {
    const pos = stance.pos ?? 0;
    const neg = stance.neg ?? 0;
    posPct = pos + neg > 0 ? Math.round((pos / (pos + neg)) * 100) : 50;
  } else {
    const labeled = posts.filter((p) => p.sentiment_label);
    if (labeled.length) {
      const neg = labeled.filter((p) =>
        ['bearish', 'negative', '负面'].includes((p.sentiment_label || '').toLowerCase())).length;
      posPct = Math.round(((labeled.length - neg) / labeled.length) * 100);
    }
  }
  if (posPct == null) return <div />;

  const r = 26, c = 2 * Math.PI * r;
  return (
    <div className="flex flex-col items-center">
      <div className="relative w-16 h-16">
        <svg viewBox="0 0 64 64" className="w-full h-full -rotate-90">
          <circle cx="32" cy="32" r={r} fill="none" stroke="rgba(239,68,68,0.25)" strokeWidth="6" />
          <circle cx="32" cy="32" r={r} fill="none" stroke="#10b981" strokeWidth="6"
                  strokeLinecap="round" strokeDasharray={`${c * posPct / 100} ${c}`} />
        </svg>
        <div className="absolute inset-0 flex items-center justify-center text-xs font-bold text-primary tabular-nums">
          {posPct}%
        </div>
      </div>
      <span className="text-xs text-muted mt-1">
        {t('diagnosis.report.sections.sentiment.positiveRatio')}
      </span>
    </div>
  );
}
