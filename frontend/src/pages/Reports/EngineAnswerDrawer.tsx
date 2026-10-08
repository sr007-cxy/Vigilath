// Engine answer "brand drawer" — slides in from the right, rendering a single (model × question) answer in each engine's real web visual style.
// Each engine has its own brand color / logo / vendor subtitle / layout:
//   · Engines with citations (DeepSeek / Yuanbao etc.) get a "Sources" card grid at the top + body, close to their real result pages;
//   · Conversational engines (Doubao / Wenxin etc.) get a "question bubble + answer" chat layout.
// Data from ResponseRow (engine / query / answer / citations / hit / error / brand_rank / created_at).

import { useEffect, useState, type CSSProperties } from 'react';
import { ENGINES, engineLabel, normalizeEngine } from '../../constants/engines';
import type { ResponseRow } from '../../services/aiTelemetryApi';
import { overlayDismiss } from '../../components/ui/useOverlayDismiss';

// Each engine's brand color (taken from each vendor's website/product visuals); unmatched falls back to a neutral color.
const ACCENT: Record<string, string> = {
  deepseek: '#4d6bfe',
  qwen: '#615ced',
  doubao: '#0a6cff',
  wenxin: '#4e6ef2',
  yuanbao: '#0052d9',
  kimi: '#111827',
};

// Engines using the top "Sources" card layout (products centered on search/citations). The rest use the chat bubble layout.
const SOURCE_FIRST = new Set(['deepseek', 'yuanbao']);

function slugOf(engine: string): { slug: string; ext: string; sub: string } | null {
  const d = ENGINES.find(e => e.key === normalizeEngine(engine));
  if (!d) return null;
  return { slug: d.slug, ext: d.ext, sub: d.sub_zh };
}

function fmtDateTime(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

function faviconUrl(domain: string): string {
  return `https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=64`;
}

export function EngineAnswerDrawer({
  row, onClose, zh,
}: { row: ResponseRow | null; onClose: () => void; zh: boolean }) {
  const T = (z: string, e: string) => (zh ? z : e);
  const [videoOpen, setVideoOpen] = useState(false);

  useEffect(() => {
    if (!row) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      if (videoOpen) setVideoOpen(false);  // When the player is open, ESC closes the player first
      else onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [row, onClose, videoOpen]);

  // Reset the player when switching rows/closing the drawer
  useEffect(() => { setVideoOpen(false); }, [row?.id]);

  if (!row) return null;

  const eng = normalizeEngine(row.engine);   // Collection channel keys like doubao_browser are normalized to Doubao itself
  const accent = ACCENT[eng] || 'var(--accent-primary)';
  const meta = slugOf(eng);
  const name = engineLabel(eng);
  const sub = meta?.sub || '';
  const sourceFirst = SOURCE_FIRST.has(eng);
  const citations = row.citations || [];

  // Header brand bar: pale tint of the brand color + logo + name
  const headTint = `color-mix(in srgb, ${accent} 14%, var(--bg-card))`;
  const chipStyle: CSSProperties = row.error
    ? { background: 'var(--danger-bg)', color: '#b91c1c' }
    : row.hit
      ? { background: 'var(--success-bg)', color: '#15803d' }
      : { background: 'rgba(148,163,184,0.18)', color: '#475569' };

  return (
    <div className="fixed inset-0 z-[60] flex justify-end" style={{ background: 'rgba(0,0,0,0.45)' }} {...overlayDismiss(onClose)}>
      <aside
        className="h-full w-full flex flex-col shadow-2xl animate-[drawerIn_.22s_ease-out]"
        style={{ maxWidth: 'min(620px, 94vw)', background: 'var(--bg-card)', borderLeft: `1px solid var(--border-color)` }}
        onClick={e => e.stopPropagation()}
      >
        <style>{`@keyframes drawerIn{from{transform:translateX(24px);opacity:.4}to{transform:none;opacity:1}}`}</style>

        {/* Brand header */}
        <div className="px-5 pt-4 pb-3.5 flex items-start gap-3" style={{ background: headTint, borderBottom: `2px solid ${accent}` }}>
          <span className="w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0 overflow-hidden"
            style={{ background: 'var(--bg-card)', border: `1px solid ${accent}` }}>
            {meta ? (
              <img src={`/image/engines/${meta.slug}.${meta.ext}`} alt={name} className="w-6 h-6 object-contain"
                onError={e => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }} />
            ) : (
              <span className="text-sm font-bold" style={{ color: accent }}>{name.slice(0, 1)}</span>
            )}
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="text-[15px] font-semibold" style={{ color: accent }}>{name}</span>
              {sub && <span className="text-[11px] text-muted">{sub}</span>}
            </div>
            <div className="flex flex-wrap items-center gap-2 mt-1 text-[11px]">
              <span className="px-1.5 py-0.5 rounded font-medium" style={chipStyle}>
                {row.error ? T('失败', 'Failed') : row.hit ? T('命中', 'Hit') : T('未命中', 'Miss')}
              </span>
              {row.brand_rank != null && (
                <span className="text-secondary">{T('品牌排名', 'Rank')} #{row.brand_rank}</span>
              )}
              <span className="text-muted tabular-nums">{fmtDateTime(row.created_at)}</span>
            </div>
          </div>
          <button onClick={onClose} className="text-muted text-sm px-1.5 py-0.5 rounded hover:bg-black/5" title={T('关闭', 'Close')}>✕</button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          {/* Question (mimicking each vendor's input box) */}
          <div className="mb-4">
            <div className="text-[11px] text-muted mb-1.5">{T('提问', 'Prompt')}</div>
            <div className="rounded-xl px-3.5 py-2.5 text-sm text-primary"
              style={{ background: 'var(--bg-input)', border: `1px solid var(--border-color)` }}>
              {row.query}
            </div>
          </div>

          {/* Evidence: public link to the original conversation / browser recording, to verify the answer really came from this engine */}
          {(row.source_url || row.video_url) && (
            <div className="mb-4 flex flex-wrap items-center gap-2">
              <span className="text-[11px] text-muted">{T('证据', 'Evidence')}</span>
              {row.source_url && (
                <a href={row.source_url} target="_blank" rel="noreferrer"
                  className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11.5px] font-medium hover:opacity-85 transition-opacity"
                  style={{ background: `color-mix(in srgb, ${accent} 12%, transparent)`, color: accent, border: `1px solid ${accent}` }}
                  title={row.source_url}>
                  <ExtLinkIcon />{T('查看原始对话', 'Open original chat')}
                </a>
              )}
              {row.video_url && (
                <button type="button" onClick={() => setVideoOpen(true)}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11.5px] font-medium hover:opacity-85 transition-opacity"
                  style={{ background: `color-mix(in srgb, ${accent} 12%, transparent)`, color: accent, border: `1px solid ${accent}` }}>
                  <PlayIcon />{T('查看录像', 'Watch recording')}
                </button>
              )}
            </div>
          )}

          {/* Error state */}
          {row.error ? (
            <div className="rounded-lg px-3.5 py-3 text-xs whitespace-pre-wrap break-words"
              style={{ background: 'var(--danger-bg)', border: '1px solid rgba(239,68,68,0.35)', color: '#b91c1c' }}>
              {row.error}
            </div>
          ) : (
            <>
              {/* Sources-first layout: card grid first, then body (Perplexity / DeepSeek style) */}
              {sourceFirst && citations.length > 0 && (
                <SourceGrid citations={citations} accent={accent} label={T('来源', 'Sources')} />
              )}

              {/* Answer body */}
              <div className="mb-4">
                <div className="text-[11px] text-muted mb-1.5 flex items-center gap-1.5">
                  <span className="inline-block w-2.5 h-2.5 rounded-full" style={{ background: accent }} />
                  {T('回答', 'Answer')}
                </div>
                <div className="rounded-xl px-4 py-3.5 text-[13.5px] leading-relaxed text-primary whitespace-pre-wrap break-words"
                  style={{ background: 'var(--bg-input)', border: `1px solid var(--border-color)`, borderLeft: `3px solid ${accent}` }}>
                  {row.answer || T('(空答复)', '(empty answer)')}
                </div>
              </div>

              {/* Conversational engines: citations after the body, as a list */}
              {!sourceFirst && citations.length > 0 && (
                <SourceGrid citations={citations} accent={accent} label={T('引用来源', 'Citations')} />
              )}
            </>
          )}
        </div>
      </aside>

      {/* Recording playback modal: video element plays inline (ignores the attachment header, no download) */}
      {videoOpen && row.video_url && (
        <div className="fixed inset-0 z-[70] flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.8)' }}
          onClick={(e) => { e.stopPropagation(); setVideoOpen(false); }}>
          <div className="relative w-full" style={{ maxWidth: 'min(960px, 94vw)' }} onClick={e => e.stopPropagation()}>
            <div className="flex items-center gap-2 mb-2">
              <span className="text-[13px] font-medium text-white/90 inline-flex items-center gap-1.5">
                <PlayIcon />{T('检测录像', 'Detection recording')} · {name}
              </span>
              <a href={row.video_url} target="_blank" rel="noreferrer"
                className="text-[11px] text-white/60 hover:text-white/90 hover:underline">
                {T('新标签打开', 'Open in new tab')} ↗
              </a>
              <button onClick={() => setVideoOpen(false)}
                className="ml-auto text-white/80 hover:text-white text-lg leading-none px-2" title={T('关闭', 'Close')}>✕</button>
            </div>
            <video src={row.video_url} controls autoPlay
              className="w-full rounded-lg bg-black" style={{ maxHeight: '80vh' }}>
              {T('你的浏览器不支持内嵌播放', 'Your browser cannot play this video')}
            </video>
          </div>
        </div>
      )}
    </div>
  );
}

function ExtLinkIcon() {
  return (
    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
      <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 6H5a1 1 0 00-1 1v12a1 1 0 001 1h12a1 1 0 001-1v-8.5M15 4h5v5M20 4l-9 9" />
    </svg>
  );
}

function PlayIcon() {
  return (
    <svg className="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24">
      <path d="M8 5v14l11-7z" />
    </svg>
  );
}

function SourceGrid({
  citations, accent, label,
}: { citations: ResponseRow['citations']; accent: string; label: string }) {
  return (
    <div className="mb-4">
      <div className="text-[11px] text-muted mb-2 flex items-center gap-1.5">
        <span style={{ color: accent }}>◆</span>{label}
        <span className="text-muted">· {citations.length}</span>
      </div>
      <div className="grid gap-2" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))' }}>
        {citations.map((c, i) => (
          <a key={i} href={c.url} target="_blank" rel="noreferrer"
            className="rounded-lg px-2.5 py-2 flex flex-col gap-1 hover:shadow-sm transition-shadow"
            style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}
            title={c.title || c.url}>
            <div className="flex items-center gap-1.5">
              <span className="text-[10px] tabular-nums w-4 h-4 rounded flex items-center justify-center flex-shrink-0 font-semibold"
                style={{ background: `color-mix(in srgb, ${accent} 16%, transparent)`, color: accent }}>{i + 1}</span>
              <img src={faviconUrl(c.domain || '')} alt="" className="w-3.5 h-3.5 rounded-sm flex-shrink-0"
                onError={e => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }} />
              <span className="text-[11px] text-muted truncate">{c.domain || c.url}</span>
            </div>
            {c.title && <span className="text-[11.5px] text-secondary line-clamp-2 leading-snug">{c.title}</span>}
          </a>
        ))}
      </div>
    </div>
  );
}
