import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { geoApi } from '../services/geoApi';
import { useMembership } from '../hooks/useMembership';
import { PaymentModal } from '../components/PaymentModal';
import type {
  AdvancedMode,
  CompareResponse,
  CrawlTestResponse,
  AuthorityAuditResponse,
  CitationCheckResponse,
  AiVisibilityResponse,
  EntityAuditResponse,
} from '../types/advanced';

// Must match backend MODE_MIN_TIER in app/models/advanced.py.
const MODE_MIN_TIER: Record<AdvancedMode, 'pro' | 'starter'> = {
  compare: 'pro',
  crawlTest: 'pro',
  authority: 'pro',
  citation: 'pro',
  visibility: 'starter',
  entity: 'starter',
};

const VALID_MODES: AdvancedMode[] = [
  'compare',
  'crawlTest',
  'authority',
  'citation',
  'visibility',
  'entity',
];

function isValidUrl(s: string): boolean {
  try {
    new URL(s);
    return true;
  } catch {
    return false;
  }
}

type AnyAdvancedResult =
  | CompareResponse
  | CrawlTestResponse
  | AuthorityAuditResponse
  | CitationCheckResponse
  | AiVisibilityResponse
  | EntityAuditResponse;

export function Advanced() {
  const { mode: modeParam } = useParams<{ mode: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const { t } = useTranslation();
  const { token, isLoggedIn, isUnlocked, refresh } = useMembership();

  const mode = VALID_MODES.includes(modeParam as AdvancedMode)
    ? (modeParam as AdvancedMode)
    : null;

  const initialUrl = (location.state as { url?: string } | null)?.url || '';

  // Per-mode form state
  const [urlInput, setUrlInput] = useState<string>(initialUrl || 'https://example.com');
  const [compareUrls, setCompareUrls] = useState<string[]>(() => {
    if (initialUrl) return [initialUrl, 'https://example.org'];
    return ['https://example.com', 'https://example.org'];
  });
  const [customQueries, setCustomQueries] = useState<string>('');
  const [entityName, setEntityName] = useState<string>('');
  const [entityType, setEntityType] = useState<'brand' | 'product' | 'person'>('brand');

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>('');
  const [result, setResult] = useState<AnyAdvancedResult | null>(null);
  const [showPaymentModal, setShowPaymentModal] = useState(false);

  useEffect(() => {
    if (!isLoggedIn) {
      navigate('/login', { replace: true });
    }
  }, [isLoggedIn, navigate]);

  if (!mode) {
    return (
      <div className="min-h-screen grid-background flex items-center justify-center p-6">
        <div className="bg-card border border-[#3f4143] rounded-2xl p-8 max-w-md text-center">
          <h2 className="text-2xl font-bold mb-3 gradient-text">
            {t('common.error', { defaultValue: 'Error' })}
          </h2>
          <p className="text-secondary mb-6">Unknown advanced mode: {modeParam}</p>
          <button
            onClick={() => navigate('/')}
            className="gradient-bg text-white rounded-full py-2.5 px-6 font-semibold hover:opacity-90 transition"
          >
            {t('result.buttons.checkAnother', { defaultValue: 'Back to home' })}
          </button>
        </div>
      </div>
    );
  }

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError('');

    // Client-side validation per mode
    if (mode === 'compare') {
      const cleaned = compareUrls.map((u) => u.trim()).filter(Boolean);
      if (cleaned.length < 2) {
        setError('Enter at least 2 URLs to compare.');
        return;
      }
      for (const u of cleaned) {
        if (!isValidUrl(u)) {
          setError(`Invalid URL: ${u}`);
          return;
        }
      }
    } else if (mode === 'entity') {
      if (!entityName.trim()) {
        setError('Entity name is required.');
        return;
      }
    } else {
      if (!urlInput.trim()) {
        setError(t('home.error.empty', { defaultValue: 'URL is required' }));
        return;
      }
      if (!isValidUrl(urlInput)) {
        setError(t('home.error.invalid', { defaultValue: 'Invalid URL format' }));
        return;
      }
    }

    if (!isUnlocked) {
      setShowPaymentModal(true);
      return;
    }

    setLoading(true);
    setResult(null);
    try {
      let data: AnyAdvancedResult;
      if (mode === 'compare') {
        data = await geoApi.runAdvancedCheck('compare', {
          urls: compareUrls.map((u) => u.trim()).filter(Boolean),
        });
      } else if (mode === 'visibility') {
        const queries = customQueries
          .split('\n')
          .map((q) => q.trim())
          .filter(Boolean);
        data = await geoApi.runAdvancedCheck('visibility', {
          url: urlInput.trim(),
          custom_queries: queries.length ? queries : undefined,
        });
      } else if (mode === 'entity') {
        data = await geoApi.runAdvancedCheck('entity', {
          entity_name: entityName.trim(),
          entity_type: entityType,
        });
      } else {
        data = await geoApi.runAdvancedCheck(mode, { url: urlInput.trim() });
      }
      setResult(data);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Unexpected error';
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  const modeTitle = t(`home.advanced.cards.${mode}.title`);
  const modeDesc = t(`home.advanced.cards.${mode}.desc`);

  return (
    <div className="min-h-screen grid-background">
      <div className="bg-glow bg-glow-1"></div>
      <div className="bg-glow bg-glow-2"></div>
      <div className="bg-glow bg-glow-3"></div>

      <main className="flex-1 px-4 py-10 hero-gradient relative z-10">
        <div className="w-full max-w-5xl mx-auto animate-fade-in">
          <button
            onClick={() => navigate('/')}
            className="text-sm text-secondary hover:text-primary mb-4 inline-flex items-center gap-1"
          >
            ← {t('result.buttons.checkAnother', { defaultValue: 'Back' })}
          </button>

          <div className="mb-8">
            <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-accent-primary/10 border border-accent-primary/20 mb-3">
              <span className="text-xs font-semibold text-accent-primary">
                {t('home.advanced.badge')} · {MODE_MIN_TIER[mode].toUpperCase()}+
              </span>
            </div>
            <h1 className="text-3xl sm:text-4xl font-bold gradient-text mb-2">{modeTitle}</h1>
            <p className="text-secondary text-sm sm:text-base max-w-2xl">{modeDesc}</p>
          </div>

          {!isUnlocked && (
            <div className="mb-6 bg-amber-500/10 border border-amber-500/30 rounded-2xl p-5">
              <p className="text-amber-300 text-sm mb-3">
                This feature requires a <strong>{MODE_MIN_TIER[mode]}</strong> membership or higher.
              </p>
              <button
                onClick={() => setShowPaymentModal(true)}
                className="gradient-bg text-white rounded-full py-2 px-5 text-sm font-semibold"
              >
                {t('home.advanced.upgrade')}
              </button>
            </div>
          )}

          <form
            onSubmit={handleSubmit}
            className="bg-card border border-[#3f4143] rounded-2xl p-6 mb-8"
          >
            <ModeForm
              mode={mode}
              urlInput={urlInput}
              setUrlInput={setUrlInput}
              compareUrls={compareUrls}
              setCompareUrls={setCompareUrls}
              customQueries={customQueries}
              setCustomQueries={setCustomQueries}
              entityName={entityName}
              setEntityName={setEntityName}
              entityType={entityType}
              setEntityType={setEntityType}
              loading={loading}
            />

            {error && (
              <div className="mt-4 bg-red-900/40 border border-red-800 rounded-xl p-3 text-sm text-red-300">
                {error}
              </div>
            )}

            <div className="mt-5 flex gap-3">
              <button
                type="submit"
                disabled={loading}
                className="gradient-bg text-white rounded-full py-2.5 px-6 font-semibold hover:opacity-90 transition disabled:opacity-60"
              >
                {loading
                  ? t('home.analyzing', { defaultValue: 'Analyzing...' })
                  : t('home.button', { defaultValue: 'Run check' })}
              </button>
            </div>
          </form>

          {result && <ResultPanel mode={mode} result={result} />}
        </div>
      </main>

      {showPaymentModal && token && (
        <PaymentModal
          token={token}
          onClose={() => setShowPaymentModal(false)}
          onSuccess={() => {
            setShowPaymentModal(false);
            refresh();
          }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Form per mode
// ---------------------------------------------------------------------------

interface ModeFormProps {
  mode: AdvancedMode;
  urlInput: string;
  setUrlInput: (v: string) => void;
  compareUrls: string[];
  setCompareUrls: (v: string[]) => void;
  customQueries: string;
  setCustomQueries: (v: string) => void;
  entityName: string;
  setEntityName: (v: string) => void;
  entityType: 'brand' | 'product' | 'person';
  setEntityType: (v: 'brand' | 'product' | 'person') => void;
  loading: boolean;
}

function ModeForm(props: ModeFormProps) {
  const { mode, urlInput, setUrlInput, compareUrls, setCompareUrls, loading } = props;

  const inputClass =
    'w-full bg-[#141416] border border-[#3f4143] rounded-xl px-4 py-3 text-primary placeholder-muted focus:outline-none focus:border-accent-primary';

  if (mode === 'compare') {
    return (
      <div>
        <label className="text-sm font-semibold text-primary mb-3 block">URLs to compare</label>
        {compareUrls.map((u, i) => (
          <div key={i} className="flex gap-2 mb-2">
            <input
              type="text"
              value={u}
              onChange={(e) => {
                const next = [...compareUrls];
                next[i] = e.target.value;
                setCompareUrls(next);
              }}
              placeholder="https://example.com"
              disabled={loading}
              className={inputClass}
            />
            {compareUrls.length > 2 && (
              <button
                type="button"
                onClick={() => setCompareUrls(compareUrls.filter((_, idx) => idx !== i))}
                className="text-rose-400 hover:text-rose-300 text-sm px-3"
              >
                ×
              </button>
            )}
          </div>
        ))}
        {compareUrls.length < 5 && (
          <button
            type="button"
            onClick={() => setCompareUrls([...compareUrls, ''])}
            className="text-sm text-accent-primary hover:text-primary mt-1"
          >
            + Add another URL
          </button>
        )}
      </div>
    );
  }

  if (mode === 'entity') {
    return (
      <div className="space-y-4">
        <div>
          <label className="text-sm font-semibold text-primary mb-2 block">Entity name</label>
          <input
            type="text"
            value={props.entityName}
            onChange={(e) => props.setEntityName(e.target.value)}
            placeholder="e.g. Tesla, iPhone, Elon Musk"
            disabled={loading}
            className={inputClass}
          />
        </div>
        <div>
          <label className="text-sm font-semibold text-primary mb-2 block">Entity type</label>
          <select
            value={props.entityType}
            onChange={(e) => props.setEntityType(e.target.value as 'brand' | 'product' | 'person')}
            disabled={loading}
            className={inputClass}
          >
            <option value="brand">Brand</option>
            <option value="product">Product</option>
            <option value="person">Person</option>
          </select>
        </div>
      </div>
    );
  }

  // Single-URL modes
  return (
    <div className="space-y-4">
      <div>
        <label className="text-sm font-semibold text-primary mb-2 block">Target URL</label>
        <input
          type="text"
          value={urlInput}
          onChange={(e) => setUrlInput(e.target.value)}
          placeholder="https://example.com"
          disabled={loading}
          className={inputClass}
        />
      </div>
      {mode === 'visibility' && (
        <div>
          <label className="text-sm font-semibold text-primary mb-2 block">
            Custom queries (optional, one per line)
          </label>
          <textarea
            value={props.customQueries}
            onChange={(e) => props.setCustomQueries(e.target.value)}
            placeholder={'best AI payment tools\nx402 alternatives'}
            disabled={loading}
            rows={3}
            className={inputClass}
          />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Result per mode
// ---------------------------------------------------------------------------

function ResultPanel({ mode, result }: { mode: AdvancedMode; result: AnyAdvancedResult }) {
  switch (mode) {
    case 'compare':
      return <CompareResult data={result as CompareResponse} />;
    case 'crawlTest':
      return <CrawlTestResult data={result as CrawlTestResponse} />;
    case 'authority':
      return <AuthorityResult data={result as AuthorityAuditResponse} />;
    case 'citation':
      return <CitationResult data={result as CitationCheckResponse} />;
    case 'visibility':
      return <VisibilityResult data={result as AiVisibilityResponse} />;
    case 'entity':
      return <EntityResult data={result as EntityAuditResponse} />;
  }
}

function SectionCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="bg-card border border-[#3f4143] rounded-2xl p-6 mb-6">
      <h3 className="text-lg font-bold text-primary mb-4">{title}</h3>
      {children}
    </div>
  );
}

function ScoreBar({ label, value, max }: { label: string; value: number; max: number }) {
  const pct = max > 0 ? Math.round((value / max) * 100) : 0;
  const color = pct >= 60 ? 'bg-emerald-500' : pct >= 30 ? 'bg-amber-500' : 'bg-rose-500';
  return (
    <div className="mb-3">
      <div className="flex justify-between text-xs text-secondary mb-1">
        <span>{label}</span>
        <span className="font-mono">{value}/{max}</span>
      </div>
      <div className="h-2 bg-[#1a1a1e] rounded-full overflow-hidden">
        <div className={`h-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

// --- Compare ---
function CompareResult({ data }: { data: CompareResponse }) {
  return (
    <SectionCard title="Competitive Comparison">
      {data.winner && (
        <div className="mb-4 p-3 rounded-xl bg-emerald-500/10 border border-emerald-500/30 text-sm">
          <strong className="text-emerald-400">{data.winner.domain}</strong> wins with{' '}
          {data.winner.score}/100 (Grade {data.winner.grade})
          {data.winner.lead > 0 && <span className="text-secondary"> · +{data.winner.lead} over runner-up</span>}
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left border-b border-[#3f4143]">
              <th className="py-2 pr-4 text-secondary font-medium">Category</th>
              {data.results.map((r) => (
                <th key={r.url} className="py-2 px-2 text-primary font-semibold">{r.domain}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.categories.map((cat) => {
              const cells = data.results.map((r) => {
                const v = r.categories?.[cat];
                const earned = v?.earned ?? 0;
                const max = v?.max ?? 0;
                const pct = max > 0 ? Math.round((earned / max) * 100) : 0;
                return { domain: r.domain, earned, max, pct };
              });
              const topPct = Math.max(...cells.map((c) => c.pct));
              return (
                <tr key={cat} className="border-b border-[#2a2a2e]">
                  <td className="py-2 pr-4 text-secondary">{cat}</td>
                  {cells.map((c, i) => (
                    <td
                      key={i}
                      className={`py-2 px-2 tabular-nums ${c.pct === topPct && topPct > 0 ? 'text-emerald-400 font-semibold' : 'text-primary'}`}
                    >
                      {c.earned}/{c.max} ({c.pct}%)
                    </td>
                  ))}
                </tr>
              );
            })}
            <tr className="border-t-2 border-[#3f4143]">
              <td className="py-3 pr-4 font-bold text-primary">TOTAL</td>
              {data.results.map((r) => (
                <td key={r.url} className="py-3 px-2 font-bold gradient-text">
                  {r.score}/100 ({r.grade})
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
    </SectionCard>
  );
}

// --- Crawl test ---
function CrawlTestResult({ data }: { data: CrawlTestResponse }) {
  return (
    <>
      <SectionCard title={`Summary — ${data.domain}`}>
        <p className="text-sm">
          <span className="text-secondary">Total issues detected: </span>
          <span className={data.total_issues === 0 ? 'text-emerald-400' : 'text-rose-400'}>
            {data.total_issues}
          </span>
        </p>
      </SectionCard>
      <SectionCard title="robots.txt rules">
        {!data.robots.found && (
          <p className="text-sm text-amber-400 mb-2">robots.txt not found — all bots allowed by default.</p>
        )}
        <ul className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-sm">
          {data.robots.bots.map((b) => (
            <li key={b.bot} className="flex justify-between">
              <span className="text-secondary">{b.bot}</span>
              <span className={b.status === 'blocked' ? 'text-rose-400' : 'text-emerald-400'}>
                {b.status}
              </span>
            </li>
          ))}
        </ul>
      </SectionCard>
      <SectionCard title="Simulated bot access (WAF/CDN test)">
        <p className="text-xs text-secondary mb-3">
          Baseline: status {data.waf.baseline_status ?? 'n/a'}, size {data.waf.baseline_size.toLocaleString()} bytes
        </p>
        <ul className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-sm">
          {data.waf.bots.map((b) => {
            const color =
              b.result === 'allowed'
                ? 'text-emerald-400'
                : b.result === 'suspicious'
                ? 'text-amber-400'
                : 'text-rose-400';
            return (
              <li key={b.bot} className="flex justify-between">
                <span className="text-secondary">{b.bot}</span>
                <span className={color}>
                  {b.result}
                  {b.status_code ? ` (${b.status_code})` : ''}
                </span>
              </li>
            );
          })}
        </ul>
      </SectionCard>
      <SectionCard title="Common Crawl index">
        {data.common_crawl.found ? (
          <p className="text-sm text-emerald-400">
            Found {data.common_crawl.count} page(s) in Common Crawl.
          </p>
        ) : (
          <p className="text-sm text-amber-400">
            Not found in Common Crawl{data.common_crawl.error ? ` — ${data.common_crawl.error}` : ''}.
          </p>
        )}
        {data.common_crawl.samples.length > 0 && (
          <ul className="mt-2 space-y-1 text-xs text-secondary">
            {data.common_crawl.samples.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
        )}
      </SectionCard>
    </>
  );
}

// --- Authority ---
function AuthorityResult({ data }: { data: AuthorityAuditResponse }) {
  return (
    <>
      <SectionCard title={`Authority Audit — ${data.brand}`}>
        <div className="flex items-end gap-4 mb-4">
          <div>
            <div className="text-4xl font-bold gradient-text">
              {data.score}/{data.max_score}
            </div>
            <div className="text-sm text-secondary">{data.grade}</div>
          </div>
          <div className="flex-1">
            <ScoreBar label="Overall" value={data.score} max={data.max_score} />
          </div>
        </div>
      </SectionCard>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <SectionCard title="Online reviews">
          <p className="text-sm text-secondary mb-2">
            Review schema present: <span className={data.reviews.has_schema ? 'text-emerald-400' : 'text-amber-400'}>{data.reviews.has_schema ? 'yes' : 'no'}</span>
          </p>
          <p className="text-sm text-secondary mb-1">Platforms found:</p>
          <ul className="text-sm">
            {data.reviews.platforms.length === 0 && <li className="text-amber-400">None</li>}
            {data.reviews.platforms.map((p) => (
              <li key={p} className="text-emerald-400">• {p}</li>
            ))}
          </ul>
        </SectionCard>
        <SectionCard title="Awards & trust signals">
          <p className="text-sm text-secondary mb-2">
            Signal count: <span className="text-primary font-semibold">{data.awards.signal_count}</span>
          </p>
          {data.awards.keywords.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-2">
              {data.awards.keywords.slice(0, 12).map((k) => (
                <span key={k} className="text-xs bg-[#1a1a1e] border border-[#3f4143] rounded-full px-2 py-0.5">
                  {k}
                </span>
              ))}
            </div>
          )}
        </SectionCard>
        <SectionCard title="Google authority">
          <p className="text-sm mb-1">
            <span className="text-secondary">Indexed pages: </span>
            <span className="text-primary">{data.google.indexed_pages ?? 'unknown'}</span>
          </p>
          <p className="text-sm mb-1">
            <span className="text-secondary">Org schema fields: </span>
            <span className="text-primary">{data.google.kg_schema_fields}/9</span>
          </p>
          <p className="text-sm">
            <span className="text-secondary">Wikipedia / Wikidata: </span>
            <span className={data.google.wikipedia_or_wikidata ? 'text-emerald-400' : 'text-amber-400'}>
              {data.google.wikipedia_or_wikidata ? 'yes' : 'no'}
            </span>
          </p>
        </SectionCard>
        <SectionCard title="Authoritative mentions">
          <ul className="text-sm">
            {data.mentions.platforms.length === 0 && <li className="text-amber-400">None</li>}
            {data.mentions.platforms.map((p) => (
              <li key={p} className="text-emerald-400">• {p}</li>
            ))}
          </ul>
        </SectionCard>
      </div>
    </>
  );
}

// --- Citation ---
function CitationResult({ data }: { data: CitationCheckResponse }) {
  return (
    <>
      <SectionCard title={`${data.engine} citations for ${data.domain}`}>
        <div className="flex items-end gap-4 mb-3">
          <div className="text-4xl font-bold gradient-text">{data.citation_rate.toFixed(0)}%</div>
          <div className="text-sm text-secondary pb-1">
            {data.cited_queries}/{data.valid_queries} queries cited · Grade {data.grade}
          </div>
        </div>
        <p className="text-xs text-secondary">Direct citations: {data.total_citations}</p>
      </SectionCard>
      <SectionCard title="Query-by-query breakdown">
        <ul className="space-y-2 text-sm">
          {data.queries.map((q, i) => (
            <li key={i} className="border-b border-[#2a2a2e] pb-2">
              <div className="flex items-start justify-between gap-2">
                <span className="text-primary">"{q.query}"</span>
                <span
                  className={
                    q.error
                      ? 'text-amber-400 text-xs'
                      : q.cited
                      ? 'text-emerald-400 text-xs'
                      : 'text-rose-400 text-xs'
                  }
                >
                  {q.error ? 'ERROR' : q.cited ? 'CITED' : 'NOT CITED'}
                </span>
              </div>
              {q.citations.length > 0 && (
                <ul className="mt-1 ml-3 text-xs text-secondary">
                  {q.citations.slice(0, 3).map((c, j) => (
                    <li key={j}>→ {c}</li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      </SectionCard>
    </>
  );
}

// --- Visibility ---
function VisibilityResult({ data }: { data: AiVisibilityResponse }) {
  const categories = useMemo(
    () => [
      { label: 'Prompt Visibility', value: data.scores.visibility },
      { label: 'Entity Clarity', value: data.scores.entity },
      { label: 'Competitor Position', value: data.scores.competitor },
      { label: 'Answer Stability', value: data.scores.stability },
      { label: 'Content Gaps', value: data.scores.content_gap },
    ],
    [data.scores],
  );
  return (
    <>
      <SectionCard title={`AI Visibility — ${data.brand}`}>
        <div className="flex items-end gap-4 mb-4">
          <div>
            <div className="text-4xl font-bold gradient-text">
              {data.total_score}/{data.max_score}
            </div>
            <div className="text-sm text-secondary">{data.grade_label}</div>
          </div>
          <div className="text-xs text-secondary pb-1">
            {data.engines.join(' · ')} · {data.query_count} queries × {data.stability_runs} runs
          </div>
        </div>
        {categories.map((c) => (
          <ScoreBar key={c.label} label={c.label} value={c.value} max={20} />
        ))}
      </SectionCard>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <SectionCard title="Per-engine visibility">
          <ul className="text-sm space-y-2">
            {Object.entries(data.per_engine_rates).map(([eng, rate]) => (
              <li key={eng}>
                <div className="flex justify-between mb-1">
                  <span className="text-secondary">{eng}</span>
                  <span className="text-primary font-mono">{rate.toFixed(0)}%</span>
                </div>
                <div className="h-1.5 bg-[#1a1a1e] rounded-full overflow-hidden">
                  <div className="h-full bg-accent-primary" style={{ width: `${rate}%` }} />
                </div>
              </li>
            ))}
          </ul>
        </SectionCard>
        <SectionCard title="Top competitors cited alongside">
          {data.top_competitors.length === 0 ? (
            <p className="text-sm text-secondary">No competitors extracted.</p>
          ) : (
            <ul className="text-sm space-y-1">
              {data.top_competitors.slice(0, 10).map((c) => (
                <li key={c.domain} className="flex justify-between">
                  <span className="text-primary">{c.domain}</span>
                  <span className="text-secondary">{c.mentions}×</span>
                </li>
              ))}
            </ul>
          )}
        </SectionCard>
        <SectionCard title="Brand framing">
          <ul className="text-sm space-y-1">
            {Object.entries(data.framings).map(([f, n]) => (
              <li key={f} className="flex justify-between">
                <span className="text-secondary capitalize">{f.replace('_', ' ')}</span>
                <span className="text-primary">{n}</span>
              </li>
            ))}
          </ul>
        </SectionCard>
        <SectionCard title="Content gaps">
          {data.content_gaps.length === 0 ? (
            <p className="text-sm text-emerald-400">No major gaps.</p>
          ) : (
            <ul className="text-xs space-y-1">
              {data.content_gaps.slice(0, 10).map((g, i) => (
                <li key={i} className="text-amber-400">• {g}</li>
              ))}
            </ul>
          )}
        </SectionCard>
      </div>
    </>
  );
}

// --- Entity ---
function EntityResult({ data }: { data: EntityAuditResponse }) {
  return (
    <>
      <SectionCard title={`Entity GEO — ${data.entity} (${data.entity_type})`}>
        <div className="flex items-end gap-4 mb-4">
          <div>
            <div className="text-4xl font-bold gradient-text">
              {data.total_score}/{data.max_score}
            </div>
            <div className="text-sm text-secondary">Grade {data.grade} · {data.percent}%</div>
          </div>
        </div>
        {Object.entries(data.scores).map(([name, v]) => (
          <ScoreBar key={name} label={name} value={v} max={20} />
        ))}
      </SectionCard>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <SectionCard title="Knowledge graph">
          <ul className="text-sm space-y-1">
            <li>
              Wikipedia:{' '}
              <span className={data.knowledge_graph.wikipedia ? 'text-emerald-400' : 'text-rose-400'}>
                {data.knowledge_graph.wikipedia ? 'found' : 'not found'}
              </span>
            </li>
            <li>
              Wikidata:{' '}
              <span className={data.knowledge_graph.wikidata ? 'text-emerald-400' : 'text-rose-400'}>
                {data.knowledge_graph.wikidata ? `found (${data.knowledge_graph.wikidata_id})` : 'not found'}
              </span>
            </li>
            <li>
              Google KG:{' '}
              <span className={data.knowledge_graph.google_kg ? 'text-emerald-400' : 'text-rose-400'}>
                {data.knowledge_graph.google_kg ? 'inferred' : 'not found'}
              </span>
            </li>
          </ul>
        </SectionCard>
        <SectionCard title="Platform footprint">
          <p className="text-sm text-secondary mb-2">
            {data.platforms.found.length}/{data.platforms.found.length + data.platforms.not_found.length} platforms
          </p>
          <div className="flex flex-wrap gap-1">
            {data.platforms.found.map((p) => (
              <span key={p} className="text-xs bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 rounded-full px-2 py-0.5">
                {p}
              </span>
            ))}
            {data.platforms.not_found.map((p) => (
              <span key={p} className="text-xs bg-rose-500/10 text-rose-400 border border-rose-500/30 rounded-full px-2 py-0.5">
                {p}
              </span>
            ))}
          </div>
        </SectionCard>
        <SectionCard title="Sentiment & framing">
          <p className="text-sm">
            <span className="text-secondary">Overall sentiment: </span>
            <span className="text-primary capitalize">{data.sentiment}</span>
          </p>
          <p className="text-sm">
            <span className="text-secondary">Best framing: </span>
            <span className="text-primary capitalize">{data.best_framing.replace('_', ' ')}</span>
          </p>
          <p className="text-sm">
            <span className="text-secondary">Recognition rate: </span>
            <span className="text-primary">{data.recognition_rate}%</span>
          </p>
        </SectionCard>
        <SectionCard title="Content gaps">
          {data.content_gaps.length === 0 ? (
            <p className="text-sm text-emerald-400">No gaps detected.</p>
          ) : (
            <ul className="text-xs space-y-1">
              {data.content_gaps.map((g, i) => (
                <li key={i} className="text-amber-400">• {g}</li>
              ))}
            </ul>
          )}
        </SectionCard>
      </div>
    </>
  );
}
