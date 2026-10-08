// Media library -- user-side media selection.
//
// What users see is **one media library**: one list, one price, one submit button. There's no concept of "supplier" --
// if the same media is available from two sources, the system automatically uses the cheaper one, and the list shows just one row.
// The row id is media_id (independent of source); at order time the server resolves the actual source and resource number,
// which the frontend can neither see nor change.
//
// Prices are always **retail quotes** (backend rwj_pricing is the single metric definition). Rows with stale reference prices are grayed out,
// and the price is re-checked in real time against the source before actually ordering.
//
// Selections persist across pages / filter changes; switching resource type clears them -- different types are different publishing formats and can't be mixed.
import { useCallback, useEffect, useState } from 'react';
import {
  mediaApi,
  type MediaResType, type MediaRow, type MediaSort,
} from '../../services/mediaApi';
import type { PublishAttributionRow } from '../../services/reportsApi';
import type { PublishPanelDoc } from './publishShared';
import { UiSelect } from '../ui/Select';
import { uiConfirm } from '../ui/dialogs';

// Max number of outlets selectable at once -- aligned with backend RWJ_MULTI_MAX (each needs a real-time price check before ordering)
const MAX_PICK = 10;

const TYPE_TABS: { key: MediaResType; label: string }[] = [
  { key: 'Website', label: '网站媒体' },
  { key: 'Wemedia', label: '自媒体' },
  { key: 'Shortvideo', label: '短视频' },
  { key: 'Question', label: '问答' },
  { key: 'Ghostwrite', label: '代写' },
];

// Column headers vary by type
const COL_LABELS: Record<MediaResType, { channel: string; include: string }> = {
  Website: { channel: '频道', include: '收录' },
  Wemedia: { channel: '平台', include: '认证' },
  Shortvideo: { channel: '平台', include: '投放方式' },
  Question: { channel: '平台', include: '收录保障' },
  Ghostwrite: { channel: '文体', include: '篇幅' },
};

// Price ranges are bucketed by **retail quote** (what users enter is the number they see).
// The unit matches the list in credits -- 1 yuan = 100 credits, so none of the bucket values change, only the labels:
// everywhere else on the page (confirm dialog, publish button) always said credits; only these few spots printed ¥, two units on one screen.
const PRICE_PRESETS: { label: string; min: number | null; max: number | null }[] = [
  { label: '不限', min: null, max: null },
  { label: '2000 积分以内', min: null, max: 2000 },
  { label: '2000-5000', min: 2000, max: 5000 },
  { label: '5000-10000', min: 5000, max: 10000 },
  { label: '10000-20000', min: 10000, max: 20000 },
  { label: '20000 积分以上', min: 20000, max: null },
];

type FacetKey = 'platform' | 'channel' | 'region' | 'include';

const FACET_LABELS: Record<FacetKey, string> = {
  platform: '平台', channel: '频道', region: '地区', include: '收录',
};

const PAGE_SIZE = 20;

export function MediaLibrarySection({
  doc, token, onPushed, onPushedAndPay, mediaAttribution = [],
}: {
  doc: PublishPanelDoc;
  token: string;
  onPushed: () => void;
  /** "Publish once selected": after pushing drafts, hand the newly created task ids to the parent to batch-order immediately (charging).
   *  If not passed, only the "Add to pending placement" button is shown. */
  onPushedAndPay?: (taskIds: number[]) => Promise<void>;
  /** Historical placement attribution, for the "media that worked before" recommendation */
  mediaAttribution?: PublishAttributionRow[];
}) {
  const [rtype, setRtype] = useState<MediaResType>('Website');
  const [facets, setFacets] = useState<Partial<Record<FacetKey, string[]>>>({});
  const [picks, setPicks] = useState<Partial<Record<FacetKey, string>>>({});
  const [showFilters, setShowFilters] = useState(false);
  const [keyword, setKeyword] = useState('');
  const [priceMin, setPriceMin] = useState<number | null>(null);
  const [priceMax, setPriceMax] = useState<number | null>(null);
  const [sort, setSort] = useState<MediaSort>('price_asc');
  const [page, setPage] = useState(1);
  const [rows, setRows] = useState<MediaRow[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [available, setAvailable] = useState(true);
  const [unavailableMsg, setUnavailableMsg] = useState('');
  // Multi-select stores whole rows -- after paging / filter changes the list changes, but the selected count and quotes must be kept
  const [picked, setPicked] = useState<Map<number, MediaRow>>(new Map());
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const search = useCallback(async (
    toPage: number,
    override?: Partial<{
      res_type: MediaResType; keyword: string; picks: Partial<Record<FacetKey, string>>;
      sort: MediaSort; priceMin: number | null; priceMax: number | null;
    }>,
  ) => {
    setBusy(true); setErr(null);
    const ty = override?.res_type ?? rtype;
    const pk = override?.picks ?? picks;
    try {
      const out = await mediaApi.search(token, {
        res_type: ty,
        platform: pk.platform || '',
        channel: pk.channel || '',
        region: pk.region || '',
        include: pk.include || '',
        keyword: (override?.keyword ?? keyword).trim(),
        price_min_cents: override?.priceMin !== undefined ? override.priceMin : priceMin,
        price_max_cents: override?.priceMax !== undefined ? override.priceMax : priceMax,
        sort: override?.sort ?? sort,
        page: toPage,
        page_size: PAGE_SIZE,
      });
      setAvailable(out.available);
      setUnavailableMsg(out.message || '');
      setRows(out.rows); setTotal(out.total); setPage(out.page || toPage);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  }, [rtype, picks, keyword, priceMin, priceMax, sort, token]);

  const loadFacets = useCallback((ty: MediaResType) => {
    mediaApi.facets(token, ty)
      .then(f => setFacets(f.facets || {}))
      .catch(() => setFacets({}));
  }, [token]);

  useEffect(() => {
    loadFacets('Website');
    search(1, { res_type: 'Website' });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  const switchType = (ty: MediaResType) => {
    setRtype(ty);
    setPicks({}); setKeyword('');
    // Different resource type = different publishing format, can't be mixed -- switching type clears selections
    setRows([]); setTotal(null); setPicked(new Map()); setNote(null);
    loadFacets(ty);
    search(1, { res_type: ty, keyword: '', picks: {} });
  };

  const toggleFacet = (key: FacetKey, val: string) => {
    const next = { ...picks };
    if (next[key] === val) delete next[key];
    else next[key] = val;
    setPicks(next);
    search(1, { picks: next });
  };

  const applyPricePreset = (min: number | null, max: number | null) => {
    setPriceMin(min); setPriceMax(max);
    search(1, { priceMin: min, priceMax: max });
  };

  const togglePick = (r: MediaRow) => {
    const next = new Map(picked);
    if (next.has(r.media_id)) next.delete(r.media_id);
    else {
      if (next.size >= MAX_PICK) {
        setErr(`一次最多勾选 ${MAX_PICK} 家媒体,请分批投放`);
        return;
      }
      next.set(r.media_id, r);
    }
    setErr(null);
    setPicked(next);
  };

  const togglePageAll = () => {
    const next = new Map(picked);
    if (rows.length > 0 && rows.every(r => next.has(r.media_id))) {
      rows.forEach(r => next.delete(r.media_id));
    } else {
      for (const r of rows) {
        if (next.has(r.media_id)) continue;
        if (next.size >= MAX_PICK) { setErr(`一次最多勾选 ${MAX_PICK} 家媒体,请分批投放`); break; }
        next.set(r.media_id, r);
      }
    }
    setPicked(next);
  };

  // pay=false: only push drafts and create planned tasks (no charge)
  // pay=true : after pushing, immediately hand this batch of tasks to the parent to batch-order -- the user selects once, clicks once and it's sent,
  //            without re-selecting in the task list below. Charging is irreversible, so confirm once first.
  const submit = async (pay: boolean) => {
    if (picked.size === 0) return;
    if (pay) {
      const ok = await uiConfirm(
        `将对勾选的 ${picked.size} 家媒体立即下单发布,预计扣 ${sumCents} 积分。`
        + '实际按下单那一刻的实时报价结算,发布后不可撤销。',
        { title: '确认立即发布', okText: '确认发布', cancelText: '再想想' });
      if (!ok) return;
    }
    setBusy(true); setErr(null); setNote(null);
    try {
      const out = await mediaApi.pushDrafts(token, doc.id, [...picked.keys()]);
      if (out.failed?.length) {
        setErr(`${out.failed.length} 家未能加入:` +
               out.failed.map(f => `${f.media_name || '该媒体'}(${f.message})`).join(';'));
      }
      const ids = (out.tasks || []).map(t => t.id).filter(id => typeof id === 'number');
      // Missing images don't block the flow but must be stated -- previously it only logged a warning server-side, and users found out there were no images only after the media published
      const dropped = out.dropped_images || [];
      const droppedNote = dropped.length
        ? `;⚠ ${dropped.length} 张配图没带上(${dropped.map(d => d.reason).filter((r, i, a) => a.indexOf(r) === i).join('、')}),稿件按无图推送`
        : '';
      if (ids.length > 0) {
        setPicked(new Map());
        if (pay && onPushedAndPay) {
          // Some outlets not accepting the draft shouldn't block the ones that did -- publish as many as succeeded
          setNote(`已加入 ${ids.length} 条,正在下单…${droppedNote}`);
          await onPushedAndPay(ids);
        } else {
          setNote(`已加入 ${ids.length} 条待投放任务,在下方任务列表确认发布${droppedNote}`);
          onPushed();
        }
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  };

  const pickedRows = [...picked.values()];
  const sumCents = pickedRows.reduce((s, r) => s + r.price_cents, 0);
  const disabled = busy || picked.size === 0 || !available;
  const effective = mediaAttribution.filter(r => r.media && r.cited_docs > 0)
    .sort((a, b) => b.cited_docs - a.cited_docs).slice(0, 6);
  const activeFacetCount = Object.keys(picks).length;
  const cols = COL_LABELS[rtype];
  const lastPage = Math.max(1, Math.ceil((total ?? 0) / PAGE_SIZE));

  return (
    <div className="space-y-2.5">
      <p className="text-[11px] text-muted">
        加入待投放不扣费;正式发布时按当时的实时报价从积分余额扣(1 元 = 100 积分)。
      </p>
      {err && <div className="text-[11px]" style={{ color: 'var(--danger-fg)' }}>{err}</div>}
      {!available && (
        <div className="text-[11px]" style={{ color: 'var(--warn-fg)' }}>
          {unavailableMsg || '媒体库暂不可用,请稍后再试'}
        </div>
      )}

      {/* Resource type tabs */}
      <div className="flex items-center gap-1 flex-wrap">
        {TYPE_TABS.map(tb => {
          const active = rtype === tb.key;
          return (
            <button key={tb.key} type="button" onClick={() => switchType(tb.key)}
                    disabled={busy}
                    className="text-xs px-3 py-1 rounded-md font-medium transition-all inline-flex items-center gap-1"
                    style={{
                      background: active ? 'var(--info-solid)' : 'rgba(59,130,246,0.08)',
                      color: active ? '#ffffff' : 'var(--info-fg)',
                      border: `1px solid ${active ? '#3b82f6' : 'rgba(59,130,246,0.28)'}`,
                      boxShadow: active ? '0 0 0 2px rgba(59,130,246,0.16)' : 'none',
                    }}>
              {tb.label}
              {active && <span className="text-[10px] opacity-90">›</span>}
            </button>
          );
        })}
      </div>

      {/* Recommendations: media placed before and cited by AI */}
      {effective.length > 0 && (
        <div className="rounded-md p-2 space-y-1"
             style={{ background: 'rgba(16,185,129,0.06)', border: '1px solid rgba(16,185,129,0.3)' }}>
          <p className="text-[11px]" style={{ color: 'var(--success-fg)' }}>投过且被 AI 引用过的媒体(点击定位):</p>
          <div className="flex flex-wrap gap-1.5">
            {effective.map((r, i) => (
              <button key={i} type="button"
                      onClick={() => { setKeyword(r.media); search(1, { keyword: r.media }); }}
                      className="text-[11px] px-2 py-0.5 rounded-full"
                      style={{ background: 'var(--success-bg)', color: 'var(--success-fg)' }}>
                {r.media} · 被引 {r.cited_docs}/{r.published_docs}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Search toolbar */}
      <div className="flex flex-wrap items-center gap-1.5">
        <input
          type="text" value={keyword} onChange={e => setKeyword(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') search(1); }}
          placeholder="媒体名称关键词"
          className="text-xs px-2.5 py-1.5 rounded-md w-[180px] max-w-full"
          style={{ background: 'var(--bg-input)', color: 'var(--text-primary)', border: '1px solid var(--border-color)' }}
        />
        <UiSelect value={sort}
                  onChange={val => {
                    const v = val as MediaSort;
                    setSort(v); search(1, { sort: v });
                  }}
                  className="text-xs px-2 py-1.5 rounded-md"
                  style={{ background: 'var(--bg-input)', color: 'var(--text-primary)', border: '1px solid var(--border-color)' }}
                  options={[
                    { value: 'price_asc', label: '积分↑' },
                    { value: 'price_desc', label: '积分↓' },
                    { value: 'rate_desc', label: '出稿率优先' },
                    { value: 'weigh_desc', label: '权重优先' },
                  ]} />
        <button type="button" disabled={busy} onClick={() => search(1)}
                className="text-xs px-3 py-1.5 rounded-md text-white"
                style={{ background: 'var(--accent-primary)', opacity: busy ? 0.5 : 1 }}>
          {busy ? '…' : '查询'}
        </button>
        <button type="button" onClick={() => setShowFilters(v => !v)}
                className="text-xs px-2.5 py-1.5 rounded-md font-medium inline-flex items-center gap-1.5"
                style={{
                  background: (showFilters || activeFacetCount > 0) ? 'var(--success-solid)' : 'var(--success-bg)',
                  color: (showFilters || activeFacetCount > 0) ? '#ffffff' : 'var(--success-fg)',
                  border: `1px solid ${(showFilters || activeFacetCount > 0) ? '#10b981' : 'rgba(16,185,129,0.28)'}`,
                }}>
          <span>{showFilters ? '收起筛选' : `筛选条件${activeFacetCount > 0 ? ` (${activeFacetCount})` : ''}`}</span>
          <span className="text-[13px] leading-none"
                style={{ transform: showFilters ? 'rotate(90deg) translateY(-1px)' : 'translateY(-1px)' }}>›</span>
        </button>
      </div>

      {/* Collapsible filter panel */}
      {showFilters && (
        <div className="rounded-md p-2.5 space-y-2"
             style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
          {(Object.keys(FACET_LABELS) as FacetKey[]).map(key => {
            const opts = (facets[key] || []).slice(0, 30);
            if (opts.length === 0) return null;
            return (
              <div key={key} className="flex items-start gap-1.5 text-[11px]">
                <span className="text-muted whitespace-nowrap pt-0.5 w-14 shrink-0">
                  {FACET_LABELS[key]}
                </span>
                <div className="flex flex-wrap gap-1">
                  <button type="button" onClick={() => toggleFacet(key, picks[key] || '')}
                          className="px-2 py-0.5 rounded-full font-medium"
                          style={{
                            background: picks[key] == null ? 'var(--success-bg)' : 'transparent',
                            color: picks[key] == null ? 'var(--success-fg)' : 'var(--text-secondary)',
                            border: `1px solid ${picks[key] == null ? 'rgba(16,185,129,0.38)' : 'transparent'}`,
                          }}>
                    不限
                  </button>
                  {opts.map(o => {
                    const active = picks[key] === o;
                    return (
                      <button key={o} type="button" onClick={() => toggleFacet(key, o)}
                              className="px-2 py-0.5 rounded-full font-medium"
                              style={{
                                background: active ? 'var(--success-solid)' : 'transparent',
                                color: active ? '#ffffff' : 'var(--text-secondary)',
                                border: active ? '1px solid #10b981' : '1px solid transparent',
                              }}>
                        {o}
                      </button>
                    );
                  })}
                </div>
              </div>
            );
          })}

          <div className="flex items-start gap-1.5 text-[11px]">
            <span className="text-muted whitespace-nowrap pt-0.5 w-14 shrink-0">积分区间</span>
            <div className="flex flex-wrap gap-1">
              {PRICE_PRESETS.map(pp => {
                const active = priceMin === pp.min && priceMax === pp.max;
                return (
                  <button key={pp.label} type="button" onClick={() => applyPricePreset(pp.min, pp.max)}
                          className="px-2 py-0.5 rounded-full font-medium"
                          style={{
                            background: active ? 'var(--success-solid)' : 'transparent',
                            color: active ? '#ffffff' : 'var(--text-secondary)',
                            border: active ? '1px solid #10b981' : '1px solid transparent',
                          }}>
                    {pp.label}
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {/* Results area */}
      {total != null && (
        <p className="text-[11px] text-muted">
          共 {total} 家媒体,第 {page}/{lastPage} 页(下单前会再次核对当时的实时报价)
        </p>
      )}

      {rows.length > 0 && (
        <div className="rounded-md overflow-x-auto" style={{ border: '1px solid var(--border-color)' }}>
          <table className="w-full text-xs" style={{ borderCollapse: 'collapse' }}>
            <thead>
              <tr className="text-muted" style={{ background: 'var(--bg-surface)' }}>
                <th className="text-left pl-2.5 py-1.5 font-normal" style={{ width: 28 }}>
                  <input type="checkbox" title="本页全选 / 取消"
                         checked={rows.length > 0 && rows.every(r => picked.has(r.media_id))}
                         onChange={togglePageAll} />
                </th>
                <th className="text-left px-2.5 py-1.5 font-normal">媒体名称</th>
                <th className="text-right px-2.5 py-1.5 font-normal">积分</th>
                <th className="text-left px-2.5 py-1.5 font-normal">{cols.channel}</th>
                <th className="text-left px-2.5 py-1.5 font-normal">地区</th>
                <th className="text-left px-2.5 py-1.5 font-normal">{cols.include}</th>
                <th className="text-left px-2.5 py-1.5 font-normal">成功率</th>
                <th className="text-left px-2.5 py-1.5 font-normal">备注</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(r => {
                const isSel = picked.has(r.media_id);
                return (
                  <tr key={r.media_id} onClick={() => togglePick(r)}
                      className="cursor-pointer"
                      style={{
                        borderTop: '1px solid var(--border-color)',
                        background: isSel ? 'rgba(59,130,246,0.10)' : undefined,
                      }}>
                    <td className="pl-2.5 py-1.5">
                      <input type="checkbox" checked={isSel} readOnly tabIndex={-1} />
                    </td>
                    <td className="px-2.5 py-1.5 text-primary whitespace-nowrap">{r.title}</td>
                    <td className="px-2.5 py-1.5 text-right whitespace-nowrap"
                        style={{ color: r.price_stale ? 'var(--text-secondary)' : 'var(--warn-fg)' }}
                        title={r.price_stale ? '参考价可能已变动,下单前会实时核价' : undefined}>
                      {r.price_cents} 积分{r.price_stale && ' *'}
                    </td>
                    <td className="px-2.5 py-1.5 text-muted whitespace-nowrap">{r.channel || '—'}</td>
                    <td className="px-2.5 py-1.5 text-muted whitespace-nowrap">{r.region || '—'}</td>
                    <td className="px-2.5 py-1.5 text-muted whitespace-nowrap">{r.include || '—'}</td>
                    <td className="px-2.5 py-1.5 text-muted whitespace-nowrap">{r.publish_rate ?? '—'}</td>
                    <td className="px-2.5 py-1.5 text-muted" style={{ maxWidth: 180 }}>
                      <span className="line-clamp-2">{r.remarks || '—'}</span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {rows.length > 0 && lastPage > 1 && (
        <div className="flex items-center gap-1.5">
          <button type="button" disabled={busy || page <= 1} onClick={() => search(page - 1)}
                  className="text-xs px-2.5 py-1 rounded-md"
                  style={{ background: 'var(--bg-input)', color: 'var(--text-secondary)', opacity: (busy || page <= 1) ? 0.5 : 1 }}>
            上一页
          </button>
          <button type="button" disabled={busy || page >= lastPage} onClick={() => search(page + 1)}
                  className="text-xs px-2.5 py-1 rounded-md"
                  style={{ background: 'var(--bg-input)', color: 'var(--text-secondary)', opacity: (busy || page >= lastPage) ? 0.5 : 1 }}>
            下一页
          </button>
        </div>
      )}

      {picked.size > 0 && (
        <div className="rounded-md px-2.5 py-2 text-[11px]"
             style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)' }}>
          <div className="flex items-center justify-between gap-2 mb-1">
            <span className="text-muted">
              已勾选 {picked.size} 家 · 合计 <b style={{ color: 'var(--warn-fg)' }}>{sumCents} 积分</b>
            </span>
            <button type="button" onClick={() => setPicked(new Map())}
                    className="text-muted underline">清空</button>
          </div>
          <div className="flex flex-wrap gap-1">
            {pickedRows.map(r => (
              <span key={r.media_id} onClick={() => togglePick(r)}
                    className="px-1.5 py-0.5 rounded cursor-pointer" title="点击移除"
                    style={{ background: 'rgba(59,130,246,0.12)', color: 'var(--text-secondary)' }}>
                {r.title} {r.price_cents} 积分 ×
              </span>
            ))}
          </div>
        </div>
      )}

      {note && <p className="text-[11px]" style={{ color: 'var(--success-fg)' }}>{note}</p>}

      <div className="flex items-center justify-between gap-2 flex-wrap">
        <span className="text-xs text-muted">
          {picked.size > 0
            ? <>已勾选 {picked.size} 家 · 合计 <b style={{ color: 'var(--warn-fg)' }}>{sumCents} 积分</b></>
            : `勾选表格里要投放的媒体,可多选(最多 ${MAX_PICK} 家)`}
        </span>
        <div className="flex items-center gap-1.5 flex-wrap">
          {/* Secondary action: just stage without spending, for scenarios that accumulate a batch to publish together */}
          <button type="button" disabled={disabled} onClick={() => submit(false)}
                  className="text-xs px-3 py-1.5 rounded-md"
                  style={{ background: 'var(--bg-input)', color: 'var(--text-secondary)',
                           border: '1px solid var(--border-color)', opacity: disabled ? 0.5 : 1 }}>
            加入待投放(不扣费)
          </button>
          {/* Primary action: publish right after selecting, saving the trip of "re-selecting in the task list" */}
          {onPushedAndPay && (
            <button type="button" disabled={disabled} onClick={() => submit(true)}
                    className="text-xs px-3 py-1.5 rounded-md text-white"
                    style={{ background: 'var(--accent-primary)', opacity: disabled ? 0.5 : 1 }}>
              {busy ? '…' : `立即发布${picked.size > 0 ? ` · ${picked.size} 家 · 扣 ${sumCents} 积分` : ''}`}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
