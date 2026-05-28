// 发文计划编辑器 — draft 态全功能编辑;confirmed 态用插入式编辑(同组件,提交不退回 draft).
// 增删行 / 改日期 / 改 query / 选模板(从抽屉里选) / 选平台 / 备注 / ↑↓ 调序.

import { useEffect, useMemo, useState } from 'react';
import {
  adminReviewApi, type ExecutionPlan, type PlanItemDraft, type PublishPlanItem,
} from '../../services/adminReviewApi';
import {
  contentTemplatesApi, type ContentTemplate,
} from '../../services/contentTemplatesApi';
import { TemplateDrawer } from './TemplateDrawer';

type RowDraft = {
  id?: string;           // 老行带后端 uuid,新行先空,提交时补
  localKey: string;      // 行内 React key — 永远稳定
  seq: number;
  publish_date: string;
  // seed-based 新流程:seed 非空,query 留空;legacy:query ∈ monitored,seed 为空.
  // 二选一,至少有一个非空.
  seed: string;
  query: string;
  template_id: number | null;
  platform: string;
  note: string;
};

const todayISO = () => new Date().toISOString().slice(0, 10);

const addDays = (iso: string, days: number): string => {
  const d = new Date(iso + 'T00:00:00');
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
};

const fromPlanItem = (it: PublishPlanItem): RowDraft => ({
  id: it.id,
  localKey: it.id,
  seq: it.seq,
  publish_date: it.publish_date,
  seed: it.seed || '',
  query: it.query,
  template_id: it.template_id ?? null,
  platform: it.platform || '',
  note: it.note || '',
});

interface Props {
  plan: ExecutionPlan;
  topicId: number;
  // confirmed 态下的「插入式编辑」走这个回调;draft 态用 onConfirmed
  mode: 'draft' | 'inline-edit';
  onSaved: (updated: ExecutionPlan) => void;       // 保存草稿 / 保存修改 都走它
  onConfirmed?: (updated: ExecutionPlan) => void;  // draft → confirmed
  onCancel?: () => void;                           // inline-edit 模式下的取消按钮
}

export function PublishingPlanEditor({
  plan, topicId, mode, onSaved, onConfirmed, onCancel,
}: Props) {
  const token = localStorage.getItem('token') || '';
  const monitored = plan.monitored_queries || [];

  const [rows, setRows] = useState<RowDraft[]>(() =>
    [...plan.publishing_plan].sort((a, b) => a.seq - b.seq).map(fromPlanItem),
  );
  const [templates, setTemplates] = useState<ContentTemplate[]>([]);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerTarget, setDrawerTarget] = useState<string | null>(null); // localKey of row
  const [savingDraft, setSavingDraft] = useState(false);
  const [savingConfirm, setSavingConfirm] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const PAGE_SIZE = 10;
  // 总页数随 rows 变化;在最后一页删行 / 切到非空 page 时回缩.
  const totalPages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [page, totalPages]);

  // 拉一次模板全集,row 的下拉用;抽屉里改了再 refresh
  const refreshTemplates = async () => {
    try {
      const [sys, tenant, topic] = await Promise.all([
        contentTemplatesApi.list(token, { scope: 'system' }),
        contentTemplatesApi.list(token, { scope: 'tenant' }),
        contentTemplatesApi.list(token, { scope: 'topic', topicId }),
      ]);
      setTemplates([...sys, ...tenant, ...topic]);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };
  useEffect(() => { refreshTemplates(); }, []);

  const tmplById = useMemo(() => {
    const m = new Map<number, ContentTemplate>();
    for (const t of templates) m.set(t.id, t);
    return m;
  }, [templates]);

  const newRow = (): RowDraft => {
    const last = rows[rows.length - 1];
    const date = last ? addDays(last.publish_date, 1) : todayISO();
    const defaultTmpl = templates.find(t => t.scope === 'system') || templates[0];
    const defaultPlat = defaultTmpl?.target_platforms?.[0] || '公众号';
    const localKey = `new-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    // 新版默认 seed-based:种子优先,query 留空(运营手填种子文本).
    // 老 legacy 行管理员若想加,把 query 切到 monitored 列表中.
    return {
      id: undefined,
      localKey,
      seq: rows.length,
      publish_date: date,
      seed: last?.seed || '',
      query: '',
      template_id: defaultTmpl ? defaultTmpl.id : null,
      platform: defaultPlat,
      note: '',
    };
  };

  const updateRow = (key: string, patch: Partial<RowDraft>) => {
    setRows(rs => rs.map(r => r.localKey === key ? { ...r, ...patch } : r));
  };

  const removeRow = (key: string) => {
    setRows(rs => rs.filter(r => r.localKey !== key).map((r, i) => ({ ...r, seq: i })));
  };

  const moveRow = (key: string, dir: -1 | 1) => {
    setRows(rs => {
      const idx = rs.findIndex(r => r.localKey === key);
      if (idx < 0) return rs;
      const tgt = idx + dir;
      if (tgt < 0 || tgt >= rs.length) return rs;
      const next = rs.slice();
      [next[idx], next[tgt]] = [next[tgt], next[idx]];
      return next.map((r, i) => ({ ...r, seq: i }));
    });
  };

  const validate = (): PlanItemDraft[] | null => {
    if (rows.length === 0) {
      setErr('发文表为空,请至少添加一行');
      return null;
    }
    const monSet = new Set(monitored);
    const drafts: PlanItemDraft[] = [];
    for (const r of rows) {
      const seed = r.seed.trim();
      const query = r.query.trim();
      if (!seed && !query) {
        setErr(`第 ${r.seq + 1} 行种子和 query 至少要填一个`);
        return null;
      }
      // legacy 行(只填 query)仍要求在监测列表里;seed-based 行(seed 非空)跳过该校验.
      if (!seed && query && !monSet.has(query)) {
        setErr(`第 ${r.seq + 1} 行 query 不在监测列表里`);
        return null;
      }
      if (!r.template_id) {
        setErr(`第 ${r.seq + 1} 行没选模板`);
        return null;
      }
      if (!r.platform) {
        setErr(`第 ${r.seq + 1} 行没选平台`);
        return null;
      }
      drafts.push({
        id: r.id || null,
        seq: r.seq,
        publish_date: r.publish_date,
        seed: seed || null,
        query,
        template_id: r.template_id,
        platform: r.platform,
        note: r.note || null,
      });
    }
    return drafts;
  };

  const handleSaveDraft = async () => {
    const drafts = validate();
    if (!drafts) return;
    setSavingDraft(true); setErr(null);
    try {
      const updated = await adminReviewApi.updateExecutionPlan(topicId, drafts, token);
      onSaved(updated);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSavingDraft(false);
    }
  };

  const handleConfirm = async () => {
    const drafts = validate();
    if (!drafts) return;
    setSavingConfirm(true); setErr(null);
    try {
      await adminReviewApi.updateExecutionPlan(topicId, drafts, token);
      const updated = await adminReviewApi.confirmExecutionPlan(topicId, token);
      onConfirmed?.(updated);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSavingConfirm(false);
    }
  };

  return (
    <div className="space-y-3">
      <header className="flex items-center gap-2">
        <h2 className="text-sm font-semibold text-primary">
          发文计划({mode === 'draft' ? '草稿' : '编辑中'} · {rows.length} 篇)
        </h2>
        <div className="flex-1" />
        <button type="button"
                onClick={() => { setDrawerTarget(null); setDrawerOpen(true); }}
                className="text-xs px-2.5 py-1 rounded-md"
                style={{ background: 'transparent', color: 'var(--text-secondary)',
                         border: '1px solid var(--border-color)' }}>
          📋 模板抽屉
        </button>
        <button type="button"
                onClick={() => {
                  setRows(rs => {
                    const next = [...rs, newRow()];
                    // 新行落到末页 — 调用方 setPage 在下个 tick 跑,直接按 next.length 算
                    setPage(Math.max(1, Math.ceil(next.length / PAGE_SIZE)));
                    return next;
                  });
                }}
                className="text-xs px-2.5 py-1 rounded-md"
                style={{ background: 'var(--accent-primary)', color: '#fff' }}>
          + 添加一行
        </button>
      </header>

      {err && (
        <div className="rounded-md p-2 text-xs"
             style={{ background: 'rgba(239,68,68,0.10)', color: '#ef4444' }}>
          {err}
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr style={{ color: 'var(--text-muted)' }}>
              <th className="py-2 px-1 text-left">#</th>
              <th className="py-2 px-1 text-left">日期</th>
              <th className="py-2 px-1 text-left">种子</th>
              <th className="py-2 px-1 text-left">Query (legacy)</th>
              <th className="py-2 px-1 text-left">命中%</th>
              <th className="py-2 px-1 text-left">模板</th>
              <th className="py-2 px-1 text-left">平台</th>
              <th className="py-2 px-1 text-left">备注</th>
              <th className="py-2 px-1 text-center">×</th>
            </tr>
          </thead>
          <tbody>
            {rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE).map((r, i) => {
              const globalIdx = (page - 1) * PAGE_SIZE + i;
              const tmpl = r.template_id ? tmplById.get(r.template_id) : null;
              const platOpts = tmpl?.target_platforms?.length
                ? tmpl.target_platforms
                : ['公众号', '小红书', '抖音', '视频号'];
              const orig = plan.publishing_plan.find(x => x.id === r.id);
              const cov = orig?.coverage_pct ?? 0;
              const tint = cov === 0 ? 'rgba(239,68,68,0.06)'
                : cov < 50 ? 'rgba(234,179,8,0.06)' : 'rgba(16,185,129,0.06)';
              const isSel = selected === r.localKey;
              return (
                <tr key={r.localKey}
                    onClick={() => setSelected(r.localKey)}
                    style={{
                      background: isSel ? 'var(--bg-tertiary)' : tint,
                      borderTop: '1px solid var(--border-color)',
                      cursor: 'pointer',
                    }}>
                  <td className="py-1.5 px-1 tabular-nums text-muted">
                    {globalIdx + 1}
                    <div className="flex flex-col">
                      <button type="button" onClick={(e) => {
                        e.stopPropagation();
                        moveRow(r.localKey, -1);
                        // 跨页:行被推到上一页时跟着翻页
                        if (globalIdx % PAGE_SIZE === 0 && page > 1) setPage(p => p - 1);
                      }}
                              className="text-[9px] leading-none text-muted hover:text-primary">▲</button>
                      <button type="button" onClick={(e) => {
                        e.stopPropagation();
                        moveRow(r.localKey, 1);
                        if (globalIdx % PAGE_SIZE === PAGE_SIZE - 1 && page < totalPages) setPage(p => p + 1);
                      }}
                              className="text-[9px] leading-none text-muted hover:text-primary">▼</button>
                    </div>
                  </td>
                  <td className="py-1.5 px-1">
                    <input type="date"
                           className="rounded-md px-1.5 py-1 w-[130px]"
                           style={{ background: 'var(--bg-tertiary)', color: 'var(--text-primary)',
                                    border: '1px solid var(--border-color)' }}
                           value={r.publish_date}
                           onClick={e => e.stopPropagation()}
                           onChange={e => updateRow(r.localKey, { publish_date: e.target.value })} />
                  </td>
                  <td className="py-1.5 px-1">
                    <input type="text"
                           className="rounded-md px-1.5 py-1 w-[200px]"
                           style={{ background: 'var(--bg-tertiary)', color: 'var(--text-primary)',
                                    border: '1px solid var(--border-color)' }}
                           placeholder="种子提示词文本…"
                           value={r.seed}
                           onClick={e => e.stopPropagation()}
                           onChange={e => updateRow(r.localKey, { seed: e.target.value })} />
                  </td>
                  <td className="py-1.5 px-1">
                    <select className="rounded-md px-1.5 py-1 w-[160px]"
                            style={{ background: 'var(--bg-tertiary)', color: 'var(--text-primary)',
                                     border: '1px solid var(--border-color)' }}
                            value={r.query}
                            onClick={e => e.stopPropagation()}
                            onChange={e => updateRow(r.localKey, { query: e.target.value })}>
                      <option value="">— 留空(seed-based)—</option>
                      {monitored.map(q => <option key={q} value={q}>{q}</option>)}
                    </select>
                  </td>
                  <td className="py-1.5 px-1 tabular-nums">
                    <span style={{ color: cov === 0 ? '#ef4444' : cov < 50 ? '#eab308' : '#10b981' }}>
                      {cov.toFixed(1)}%
                    </span>
                  </td>
                  <td className="py-1.5 px-1">
                    <div className="flex items-center gap-1">
                      <select className="rounded-md px-1.5 py-1 max-w-[160px]"
                              style={{ background: 'var(--bg-tertiary)', color: 'var(--text-primary)',
                                       border: '1px solid var(--border-color)' }}
                              value={r.template_id || ''}
                              onClick={e => e.stopPropagation()}
                              onChange={e => {
                                const newId = Number(e.target.value) || null;
                                const nt = newId ? tmplById.get(newId) : null;
                                // 模板换了:如果当前 platform 不在新模板平台列表里,自动切到第一个
                                const newPlat = (nt && nt.target_platforms.length && !nt.target_platforms.includes(r.platform))
                                  ? nt.target_platforms[0]
                                  : r.platform;
                                updateRow(r.localKey, { template_id: newId, platform: newPlat });
                              }}>
                        <option value="">—</option>
                        {templates.map(t => (
                          <option key={t.id} value={t.id}>{t.name}</option>
                        ))}
                      </select>
                      <button type="button"
                              onClick={(e) => { e.stopPropagation(); setDrawerTarget(r.localKey); setDrawerOpen(true); }}
                              className="text-[10px] px-1 py-0.5 rounded-md"
                              style={{ color: 'var(--text-secondary)' }}
                              title="打开模板抽屉">
                        📋
                      </button>
                    </div>
                  </td>
                  <td className="py-1.5 px-1">
                    <select className="rounded-md px-1.5 py-1"
                            style={{ background: 'var(--bg-tertiary)', color: 'var(--text-primary)',
                                     border: '1px solid var(--border-color)' }}
                            value={r.platform}
                            onClick={e => e.stopPropagation()}
                            onChange={e => updateRow(r.localKey, { platform: e.target.value })}>
                      {platOpts.map(p => <option key={p}>{p}</option>)}
                    </select>
                  </td>
                  <td className="py-1.5 px-1">
                    <input className="rounded-md px-1.5 py-1 w-[140px]"
                           style={{ background: 'var(--bg-tertiary)', color: 'var(--text-primary)',
                                    border: '1px solid var(--border-color)' }}
                           placeholder="备注…"
                           value={r.note}
                           onClick={e => e.stopPropagation()}
                           onChange={e => updateRow(r.localKey, { note: e.target.value })} />
                  </td>
                  <td className="py-1.5 px-1 text-center">
                    <button type="button" onClick={(e) => { e.stopPropagation(); removeRow(r.localKey); }}
                            className="text-muted hover:text-primary">×</button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {rows.length > PAGE_SIZE && (
        <div className="flex items-center justify-between text-xs text-secondary pt-1">
          <span className="tabular-nums text-muted">
            {(page - 1) * PAGE_SIZE + 1}-{Math.min(page * PAGE_SIZE, rows.length)} / {rows.length} 篇
          </span>
          <div className="flex items-center gap-2">
            <button type="button" disabled={page <= 1}
                    onClick={() => setPage(p => Math.max(1, p - 1))}
                    className="px-2 py-1 rounded-md disabled:opacity-40"
                    style={{ background: 'var(--bg-tertiary)' }}>
              上一页
            </button>
            <span className="tabular-nums">{page} / {totalPages}</span>
            <button type="button" disabled={page >= totalPages}
                    onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                    className="px-2 py-1 rounded-md disabled:opacity-40"
                    style={{ background: 'var(--bg-tertiary)' }}>
              下一页
            </button>
          </div>
        </div>
      )}

      <p className="text-[10px] text-muted">
        同一 Query 可排多行(不同时间 / 不同平台)。命中率 0% 行底色浅红,&lt;50% 浅黄,≥50% 浅绿。
      </p>

      <div className="flex justify-end gap-2">
        {onCancel && (
          <button type="button" onClick={onCancel}
                  className="text-xs px-3 py-1.5 rounded-md"
                  style={{ background: 'transparent', color: 'var(--text-secondary)',
                           border: '1px solid var(--border-color)' }}>
            取消
          </button>
        )}
        <button type="button" onClick={handleSaveDraft} disabled={savingDraft || savingConfirm}
                className="text-xs px-3 py-1.5 rounded-md"
                style={{ background: 'transparent', color: 'var(--text-secondary)',
                         border: '1px solid var(--border-color)',
                         opacity: savingDraft || savingConfirm ? 0.5 : 1 }}>
          {savingDraft ? '保存中…' : (mode === 'draft' ? '💾 保存草稿' : '💾 保存修改')}
        </button>
        {mode === 'draft' && (
          <button type="button" onClick={handleConfirm} disabled={savingDraft || savingConfirm}
                  className="text-xs px-3 py-1.5 rounded-md text-white"
                  style={{ background: 'var(--accent-primary)',
                           opacity: savingDraft || savingConfirm ? 0.5 : 1 }}>
            {savingConfirm ? '生成中…' : '✅ 确认并生成文章 →'}
          </button>
        )}
      </div>

      <TemplateDrawer open={drawerOpen} topicId={topicId}
                      onClose={() => { setDrawerOpen(false); setDrawerTarget(null); refreshTemplates(); }}
                      onPick={drawerTarget ? (t) => {
                        const plat = t.target_platforms[0] || '';
                        updateRow(drawerTarget, { template_id: t.id, platform: plat });
                        setDrawerOpen(false);
                        setDrawerTarget(null);
                        refreshTemplates();
                      } : undefined} />
    </div>
  );
}
