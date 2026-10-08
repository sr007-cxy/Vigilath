// Media library — image overview and handling page under the workbench "Content & publishing" group.
// Route: /ops/media   Backend: geo/api/admin_media.py (require_workbench)
//
// Before this, images could only be viewed per topic in the article editor's upload panel: no cross-topic view, AI-generated and
// user-uploaded were indistinguishable, and content labels and external review results were not shown. This page puts all three on one screen:
//   source (AI-generated / user-uploaded) · content label (ID / metadata / watermark) · external review result (Alibaba Cloud content security)
// Only three actions: resubmit for review, re-apply label, delete -- no per-image "approve/reject"; article approval
// still belongs to the article review desk, and images here are just "usable assets", not "items under review".

import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { PageHead } from '../../components/PageHead';
import { uiConfirm } from '../../components/ui/dialogs';
import { toast } from '../../components/ui/toast';
import { fmtTime } from '../../utils/datetime';
import {
  mediaLibraryApi, type MediaItem, type MediaStats, type MediaFilters,
} from '../../services/mediaLibraryApi';
import { UiSelect } from '../../components/ui/Select';
import { overlayDismiss } from '../../components/ui/useOverlayDismiss';

const card: React.CSSProperties = {
  background: 'var(--bg-card)', border: '1px solid var(--border-color)',
};
const inputStyle: React.CSSProperties = {
  background: 'var(--bg-tertiary)', color: 'var(--text-primary)',
  border: '1px solid var(--border-color)',
};

const PAGE = 60;

const SOURCES: { key: NonNullable<MediaFilters['source']>; label: string }[] = [
  { key: 'all', label: '全部来源' },
  { key: 'ai', label: 'AI 生成' },
  { key: 'user', label: '用户上传' },
];
const RISKS: { key: NonNullable<MediaFilters['risk']>; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'risky', label: '有风险(高/中)' },
  { key: 'none', label: '已检无风险' },
  { key: 'unscanned', label: '未送检' },
];
const MARKS: { key: NonNullable<MediaFilters['mark']>; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'marked', label: '已打标识' },
  { key: 'unmarked', label: 'AI图缺标识' },
];

function Badge({ tone, children }: { tone: 'ok' | 'warn' | 'ban' | 'mute'; children: React.ReactNode }) {
  const palette = {
    ok:   { background: 'var(--success-bg)',  color: 'var(--success-fg)' },
    warn: { background: 'var(--warn-bg)', color: 'var(--warn-fg)' },
    ban:  { background: 'var(--danger-bg)',  color: 'var(--danger-fg)' },
    mute: { background: 'var(--bg-tertiary)',    color: 'var(--text-muted)' },
  }[tone];
  return (
    <span className="text-[11px] px-1.5 py-0.5 rounded-md whitespace-nowrap" style={palette}>
      {children}
    </span>
  );
}

// Explicit image label — same metric definition as the text-side AiDisclosureBar (ContentReview.tsx): the label is shown only
// in the **display UI**, never burned into image pixels. AI images are auto-published to external media along with articles by the publish channel;
// burning 「内容由AI生成,仅供参考」 ("AI-generated content, for reference only") onto the image would get it published along with the image (so image_marking's
// GEO_IMAGE_WATERMARK is off by default). Item 8 of the filing correction list literally asks for "an image generation
// **explicit-label UI** (including "内容由AI生成,仅供参考")" — a UI label satisfies it, and files on disk
// stay clean. Implicit labels (EXIF / PNG tEXt, including the filing entity's full name) are still written; see ledger §4.4.
function AiImageDisclosure({ src, alt, ai, compact, className, style, present = true }: {
  src: string; alt: string; ai: boolean;
  compact?: boolean; className?: string; style?: React.CSSProperties;
  /** Backend stat says the file is not on this host -- don't request it, draw a placeholder so reviewers aren't guessing at a broken image */
  present?: boolean;
}) {
  const [broken, setBroken] = useState(false);
  if (!present || broken) {
    return (
      <div className={`flex flex-col items-center justify-center text-center ${className || ''}`}
           style={{ ...style, background: 'var(--bg-tertiary)', color: 'var(--warn-fg)', padding: 8 }}
           title="库里有这条记录,但文件不在当前这台服务器上(测试站与生产各自一块盘)">
        <div style={{ fontSize: compact ? 11 : 13 }}>⚠ 文件不在本机</div>
        {!compact && <div className="text-muted" style={{ fontSize: 11 }}>行在库里,文件在另一台机 —— 不是图坏了</div>}
      </div>
    );
  }
  const img = <img src={src} alt={alt} loading="lazy" className={className} style={style}
                   onError={() => setBroken(true)} />;
  if (!ai) return img;
  return (
    <div className="relative w-full h-full">
      {img}
      <div className="absolute left-0 right-0 bottom-0 text-center text-white"
           style={{
             background: 'rgba(17,24,39,0.62)',
             padding: compact ? '2px 4px' : '6px 10px',
             fontSize: compact ? 10 : 13,
             lineHeight: 1.4,
             letterSpacing: compact ? 0 : 0.3,
           }}>
        内容由AI生成,仅供参考
      </div>
    </div>
  );
}

/** External review badge -- not submitted / clean / risk hit / degraded, each of the four states has its own wording */
function RiskBadge({ m }: { m: MediaItem }) {
  if (!m.external_status) return <Badge tone="mute">未送检</Badge>;
  if (m.external_status === 'skipped') return <Badge tone="mute">未送检</Badge>;
  if (m.external_status === 'degraded') return <Badge tone="warn">外检降级</Badge>;
  if (!m.external_risk) return <Badge tone="ok">✓ 无风险</Badge>;
  const tone = m.external_risk === 'low' ? 'warn' : 'ban';
  const zh = { high: '高风险', medium: '中风险', low: '低风险' }[m.external_risk] || m.external_risk;
  return <Badge tone={tone}>{zh}{m.external_labels.length ? ` · ${m.external_labels.join('/')}` : ''}</Badge>;
}

function StatTile({ v, k, tone }: { v: number; k: string; tone?: 'warn' | 'ban' }) {
  const color = tone === 'ban' ? '#ef4444' : tone === 'warn' ? '#f59e0b' : 'var(--text-primary)';
  return (
    <div className="rounded-xl px-4 py-3" style={card}>
      <div className="text-xl font-semibold" style={{ color }}>{v}</div>
      <div className="text-[11px] text-muted mt-0.5">{k}</div>
    </div>
  );
}

/** Detail drawer -- file metadata is read only when it is opened (reading files per row on the list page would drag the list down) */
function DetailDrawer({ id, token, onClose, onChanged }: {
  id: number; token: string; onClose: () => void; onChanged: () => void;
}) {
  const [m, setM] = useState<MediaItem | null>(null);
  const [busy, setBusy] = useState('');

  const load = useCallback(() => {
    mediaLibraryApi.get(id, token).then((r) => setM(r)).catch((e) => toast.error(String(e)));
  }, [id, token]);
  useEffect(load, [load]);

  const act = async (kind: 'rescan' | 'remark') => {
    setBusy(kind);
    try {
      const r = kind === 'rescan'
        ? await mediaLibraryApi.rescan(id, token)
        : await mediaLibraryApi.remark(id, token);
      if (r) setM(r);
      toast.success(kind === 'rescan' ? '已重新送检' : '已补打内容标识');
      onChanged();
      load();
    } catch (e) {
      toast.error(String(e));
    } finally {
      setBusy('');
    }
  };

  const del = async () => {
    if (!(await uiConfirm('删除后图片文件一并清除,已引用这张图的文章正文会变成裂图。确定删除?',
                          { danger: true, okText: '删除' }))) return;
    try {
      await mediaLibraryApi.remove(id, token);
      toast.success('已删除');
      onChanged();
      onClose();
    } catch (e) {
      toast.error(String(e));
    }
  };

  if (!m) return null;
  const rows: [string, React.ReactNode][] = [
    ['内容编号', <code className="text-primary">{m.gen_trace_no || '—'}</code>],
    ['来源', m.is_ai_generated ? '本平台 AI 生成合成' : '用户上传素材'],
    ['出图模型', m.gen_model || '—'],
    ['所属项目', `${m.topic_name || '—'}(#${m.topic_id})`],
    ['归属客户', m.owner_email || '—'],
    ['文件', <span>
        {m.filename} · {m.mime} · {Math.round((m.size || 0) / 1024)} KB
        {!m.file_present && <> <Badge tone="warn">文件不在本机</Badge></>}
      </span>],
    ['说明', m.description || <span className="text-muted">(未填)</span>],
    ['入库时间', fmtTime(m.uploaded_at)],
    ['显式标识', m.is_ai_generated
      ? <span className="flex flex-wrap items-center gap-1.5">
          <Badge tone="ok">展示界面已标注</Badge>
          <Badge tone={m.has_explicit_mark ? 'warn' : 'mute'}>
            {m.has_explicit_mark ? '图上另有可见水印' : '图上不烧水印'}
          </Badge>
        </span>
      : <span className="text-muted">不适用</span>],
    ['隐式标识', !m.is_ai_generated
      ? <span className="text-muted">不适用</span>
      : !m.file_present
        ? <Badge tone="warn">文件不在本机,无法读元数据</Badge>
        : <Badge tone={m.has_implicit_mark ? 'ok' : 'ban'}>{m.has_implicit_mark ? '元数据可读回' : '读不到元数据'}</Badge>],
    ['外检结论', <RiskBadge m={m} />],
  ];

  return (
    <div className="fixed inset-0 z-50 flex justify-end" style={{ background: 'rgba(0,0,0,0.45)' }} {...overlayDismiss(onClose)}>
      <div className="w-full max-w-2xl h-full overflow-y-auto p-5" style={{ background: 'var(--bg-card)' }}
           onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <div className="text-base font-semibold">素材详情 #{m.id}</div>
          <button onClick={onClose} className="text-sm text-muted hover:text-primary">关闭</button>
        </div>

        {m.url && (
          <div className="rounded-xl overflow-hidden mb-4"
               style={{ border: '1px solid var(--border-color)' }}>
            <AiImageDisclosure src={m.url} alt={m.filename} ai={m.is_ai_generated}
                               present={m.file_present}
                               className="w-full block"
                               style={{ maxHeight: 420, minHeight: 120, objectFit: 'contain' }} />
          </div>
        )}

        <table className="w-full text-sm mb-4">
          <tbody>
            {rows.map(([k, v]) => (
              <tr key={k} style={{ borderTop: '1px solid var(--border-color)' }}>
                <td className="py-2 pr-3 text-muted whitespace-nowrap align-top w-28">{k}</td>
                <td className="py-2 break-all">{v}</td>
              </tr>
            ))}
          </tbody>
        </table>

        {m.external_descriptions.length > 0 && (
          <div className="rounded-lg px-3 py-2 mb-4 text-[12px]" style={{ background: 'var(--bg-tertiary)' }}>
            <div className="text-muted mb-1">外检说明(阿里云内容安全)</div>
            {m.external_descriptions.map((d) => <div key={d}>· {d}</div>)}
          </div>
        )}

        {Object.keys(m.meta).length > 0 && (
          <details className="mb-4">
            <summary className="text-sm cursor-pointer text-muted">文件内嵌元数据({Object.keys(m.meta).length} 项)</summary>
            <table className="w-full text-[12px] mt-2">
              <tbody>
                {Object.entries(m.meta).map(([k, v]) => (
                  <tr key={k} style={{ borderTop: '1px solid var(--border-color)' }}>
                    <td className="py-1.5 pr-3 text-muted whitespace-nowrap align-top">{k}</td>
                    <td className="py-1.5 break-all">{v}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        )}

        <div className="flex flex-wrap gap-2">
          <button onClick={() => act('rescan')} disabled={!!busy || m.kind !== 'image'}
                  className="px-3 py-1.5 rounded-lg text-sm" style={inputStyle}>
            {busy === 'rescan' ? '送检中…' : '重新送外检'}
          </button>
          {m.is_ai_generated && !m.gen_trace_no && (
            <button onClick={() => act('remark')} disabled={!!busy}
                    className="px-3 py-1.5 rounded-lg text-sm" style={inputStyle}>
              {busy === 'remark' ? '打标中…' : '补打内容标识'}
            </button>
          )}
          <button onClick={del} className="px-3 py-1.5 rounded-lg text-sm"
                  style={{ background: 'var(--danger-bg)', color: 'var(--danger-fg)' }}>
            删除素材
          </button>
        </div>
      </div>
    </div>
  );
}

export function AdminMediaLibrary() {
  const token = localStorage.getItem('token') || '';
  const [params] = useSearchParams();
  const topicId = Number(params.get('topic') || 0) || undefined;

  const [items, setItems] = useState<MediaItem[]>([]);
  const [total, setTotal] = useState(0);
  const [stats, setStats] = useState<MediaStats | null>(null);
  const [f, setF] = useState<MediaFilters>({ kind: 'image', source: 'all', risk: 'all', mark: 'all' });
  const [kw, setKw] = useState('');
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(false);
  const [openId, setOpenId] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await mediaLibraryApi.list(token, {
        ...f, topicId, q: kw || undefined, limit: PAGE, offset: page * PAGE,
      });
      setItems(r?.items || []);
      setTotal(r?.total || 0);
      setStats(await mediaLibraryApi.stats(token, topicId));
    } catch (e) {
      toast.error(String(e));
    } finally {
      setLoading(false);
    }
  }, [token, f, topicId, kw, page]);
  useEffect(() => { void load(); }, [load]);

  return (
    <div className="p-4 md:p-6">
      <PageHead titleKey="admin.mediaLibrary.title" titleFallback="素材库" />
      <div className="mb-1 text-lg font-semibold">素材库</div>
      <div className="mb-4 text-[12px] text-muted">
        平台生成与客户上传的全部图片素材。AI 生成的图带内容编号与文件内嵌元数据;
        图片外检结论来自阿里云内容安全,是**软信号**,不阻塞发布,只供人工判断。
      </div>

      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-4">
          <StatTile v={stats.total} k="图片总数" />
          <StatTile v={stats.ai_generated} k="AI 生成" />
          <StatTile v={stats.missing_mark} k="AI图缺标识" tone={stats.missing_mark ? 'ban' : undefined} />
          <StatTile v={stats.risky} k="外检有风险" tone={stats.risky ? 'ban' : undefined} />
          <StatTile v={stats.unscanned} k="未送检" tone={stats.unscanned ? 'warn' : undefined} />
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2 mb-4">
        {([['source', SOURCES], ['risk', RISKS], ['mark', MARKS]] as const).map(([key, opts]) => (
          <UiSelect key={key} value={String(f[key])} style={inputStyle}
                    className="px-2.5 py-1.5 rounded-lg text-sm"
                    onChange={(val) => { setPage(0); setF({ ...f, [key]: val as never }); }}
                    options={opts.map((o) => ({ value: o.key, label: o.label }))} />
        ))}
        <input value={kw} placeholder="文件名 / 内容编号" style={inputStyle}
               className="px-2.5 py-1.5 rounded-lg text-sm flex-1 min-w-[180px]"
               onChange={(e) => setKw(e.target.value)}
               onKeyDown={(e) => { if (e.key === 'Enter') { setPage(0); void load(); } }} />
        <button onClick={() => { setPage(0); void load(); }} style={inputStyle}
                className="px-3 py-1.5 rounded-lg text-sm">查询</button>
      </div>

      {loading && <div className="text-sm text-muted mb-3">加载中…</div>}
      {!loading && items.length === 0 && (
        <div className="text-sm text-muted py-10 text-center rounded-xl" style={card}>
          没有符合条件的素材
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-6 gap-3">
        {items.map((m) => (
          <button key={m.id} onClick={() => setOpenId(m.id)}
                  className="rounded-xl overflow-hidden text-left" style={card}>
            <div className="w-full aspect-video overflow-hidden" style={{ background: 'var(--bg-tertiary)' }}>
              {m.kind === 'image' && m.url
                ? <AiImageDisclosure src={m.url} alt={m.filename} ai={m.is_ai_generated} compact
                                     present={m.file_present}
                                     className="w-full h-full" style={{ objectFit: 'cover' }} />
                : <div className="w-full h-full flex items-center justify-center text-muted text-xs">视频</div>}
            </div>
            <div className="p-2">
              <div className="text-[12px] truncate" title={m.description || m.filename}>{m.description || m.filename || `#${m.id}`}</div>
              <div className="text-[11px] text-muted truncate mt-0.5">{m.topic_name || `#${m.topic_id}`}</div>
              <div className="flex flex-wrap gap-1 mt-1.5">
                <Badge tone={m.is_ai_generated ? 'ok' : 'mute'}>{m.is_ai_generated ? 'AI 生成' : '用户上传'}</Badge>
                {!m.file_present && <Badge tone="warn">不在本机</Badge>}
                {m.is_ai_generated && !m.gen_trace_no && <Badge tone="ban">缺标识</Badge>}
                <RiskBadge m={m} />
              </div>
            </div>
          </button>
        ))}
      </div>

      {total > PAGE && (
        <div className="flex items-center justify-center gap-3 mt-5 text-sm">
          <button disabled={page === 0} style={inputStyle} className="px-3 py-1.5 rounded-lg disabled:opacity-40"
                  onClick={() => setPage(page - 1)}>上一页</button>
          <span className="text-muted">{page * PAGE + 1}–{Math.min((page + 1) * PAGE, total)} / {total}</span>
          <button disabled={(page + 1) * PAGE >= total} style={inputStyle}
                  className="px-3 py-1.5 rounded-lg disabled:opacity-40"
                  onClick={() => setPage(page + 1)}>下一页</button>
        </div>
      )}

      {openId !== null && (
        <DetailDrawer id={openId} token={token}
                      onClose={() => setOpenId(null)} onChanged={() => void load()} />
      )}
    </div>
  );
}
