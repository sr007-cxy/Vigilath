// One-off sentiment quick check -- just give a brand name to run, no account, no subscription.
//
// Why this panel exists: quick check used to be callable only by the conversational agent; web users couldn't reach it and had to first
// "enable daily health monitoring" -- "taking a look" was blocked behind "create an account first", and conversion broke right there.
//
// It runs as "start + poll" rather than waiting inline: a round can take up to 360s, so a synchronous wait would surely time out.
import { useCallback, useEffect, useRef, useState } from 'react';
import { sentimentApi, type SentimentOnceState } from '../../../../services/sentimentApi';
import { toast } from '../../../../components/ui/toast';

const cardStyle: React.CSSProperties = {
  background: 'var(--bg-card)',
  border: '1px solid var(--border-color)',
};

const POLL_MS = 5000;

interface Post {
  title?: string;
  source?: string;
  url?: string;
  publish_time?: string;
  risk_level?: string;
  summary?: string;
}

interface Kpi {
  total_today?: number;
  high_risk?: number;
  avg_sentiment?: number;
  active_sources?: number;
}

function negatives(result: Record<string, unknown> | undefined): Post[] {
  const neg = (result?.negative_posts as Post[] | undefined) || [];
  if (neg.length) return neg;
  const all = (result?.posts as Post[] | undefined) || [];
  return all.filter(p => ['负面', 'negative', 'bearish', '看跌', '利空']
    .includes(String((p as Record<string, unknown>).sentiment_label ?? '').toLowerCase()));
}

export function QuickCheckPanel({ defaultBrand = '' }: { defaultBrand?: string }) {
  const [brand, setBrand] = useState(defaultBrand);
  const [keywords, setKeywords] = useState('');
  const [state, setState] = useState<SentimentOnceState | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const timer = useRef<number | null>(null);

  const token = localStorage.getItem('token') || '';
  const terms = keywords.split(/[,，\s]+/).map(s => s.trim()).filter(Boolean);

  const stop = () => {
    if (timer.current) { window.clearTimeout(timer.current); timer.current = null; }
  };
  useEffect(() => stop, []);

  const poll = useCallback(async (b: string, ts: string[]) => {
    try {
      const st = await sentimentApi.getOnce(b, ts, token);
      setState(st);
      if (st.state === 'running' || st.state === 'started') {
        timer.current = window.setTimeout(() => poll(b, ts), POLL_MS);
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, [token]);

  const run = async () => {
    const b = brand.trim();
    if (!b) { toast.error('请先填写品牌名'); return; }
    stop();
    setBusy(true); setErr(''); setState(null);
    try {
      const st = await sentimentApi.startOnce(b, terms, token);
      setState(st);
      if (st.state === 'running' || st.state === 'started') {
        timer.current = window.setTimeout(() => poll(b, terms), POLL_MS);
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const result = state?.state === 'ready'
    ? (state.result as Record<string, unknown> | undefined)
    : undefined;
  const kpi = (result?.kpi as Kpi | undefined) || {};
  const neg = negatives(result);
  const range = result?.evidence_range as { from?: string; to?: string } | undefined;
  const running = state?.state === 'running' || state?.state === 'started';

  return (
    <section className="rounded-xl p-5 space-y-4" style={cardStyle}>
      <div>
        <h2 className="text-lg font-bold text-primary">先看一眼:单次舆情快检</h2>
        <p className="text-sm text-secondary mt-1">
          报个品牌名就能跑,不用先开通监测。抓一轮公开信息,给出负面条目与原帖链接。
          要每天自动盯 + 负面预警 + 历史回溯,再去开通日常监测。
        </p>
      </div>

      <div className="flex flex-wrap gap-2">
        <input
          type="text" value={brand} onChange={e => setBrand(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') run(); }}
          placeholder="品牌名,如「威吉力」"
          className="px-3 py-2 rounded-lg text-sm text-primary flex-1 min-w-[180px]"
          style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)' }}
        />
        <input
          type="text" value={keywords} onChange={e => setKeywords(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') run(); }}
          placeholder="别名 / 产品名(可空,逗号分隔)"
          className="px-3 py-2 rounded-lg text-sm text-primary flex-1 min-w-[180px]"
          style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)' }}
        />
        <button
          type="button" disabled={busy || running} onClick={run}
          className="px-5 py-2 rounded-lg text-sm font-semibold text-white disabled:opacity-50"
          style={{ background: 'var(--accent-primary)' }}
        >
          {busy ? '发起中…' : running ? '正在跑…' : '开始快检'}
        </button>
      </div>

      {err && <p className="text-sm" style={{ color: 'var(--danger-fg)' }}>{err}</p>}

      {running && (
        <p className="text-sm text-secondary">
          正在抓取,约 1–5 分钟。这一页会自己刷新,不用守着。
          {state?.elapsed_s ? ` 已跑 ${state.elapsed_s}s。` : ''}
        </p>
      )}

      {(state?.state === 'failed' || state?.state === 'stale' || state?.state === 'timeout') && (
        <p className="text-sm" style={{ color: 'var(--warn-fg)' }}>
          这一单没取到结果,再跑一次试试。
        </p>
      )}

      {result && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {[
              ['条目数', kpi.total_today],
              ['中高风险', kpi.high_risk],
              ['平均情感分', kpi.avg_sentiment],
              ['信源数', kpi.active_sources],
            ].map(([label, v]) => (
              <div key={String(label)} className="rounded-lg p-3"
                   style={{ background: 'var(--bg-surface)' }}>
                <p className="text-xs text-secondary">{label}</p>
                <p className="text-xl font-bold text-primary tabular-nums">
                  {v ?? '—'}
                </p>
              </div>
            ))}
          </div>

          {range?.from && (
            // State the real time span the evidence covers honestly -- don't hardcode "last 30 days"
            <p className="text-xs text-secondary">
              证据时间范围:{range.from} ~ {range.to || '今天'}
            </p>
          )}

          {neg.length > 0 ? (
            <div className="space-y-2">
              <h3 className="text-sm font-semibold text-primary">负面条目</h3>
              {neg.slice(0, 8).map((p, i) => (
                <div key={i} className="rounded-lg p-3 text-sm"
                     style={{ background: 'var(--bg-surface)' }}>
                  <div className="flex items-start justify-between gap-3">
                    <a href={p.url || '#'} target="_blank" rel="noreferrer"
                       className="text-primary font-medium hover:underline">
                      {p.title || '(无标题)'}
                    </a>
                    {p.risk_level && (
                      <span className="text-xs shrink-0" style={{ color: 'var(--warn-fg)' }}>
                        {p.risk_level}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-secondary mt-1">
                    {p.source || '未知来源'}
                    {p.publish_time ? ` · ${p.publish_time}` : ''}
                  </p>
                  {p.summary && (
                    <p className="text-xs text-secondary mt-1 line-clamp-2">{p.summary}</p>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-secondary">这一轮没抓到负面条目。</p>
          )}

          <p className="text-xs text-secondary">
            单次快检是一次性抓取,不含历史与预警。
          </p>
        </div>
      )}
    </section>
  );
}
