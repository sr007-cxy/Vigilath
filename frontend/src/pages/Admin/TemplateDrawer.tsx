// Content template drawer — slides in from the right, opened by the execution plan editor.
// Three scope tabs (system / this tenant / this topic); each template can be edited / locked / selected.

import { useEffect, useMemo, useState } from 'react';
import {
  contentTemplatesApi, type ContentTemplate, type TemplateScope,
  type TemplateScoreItem,
} from '../../services/contentTemplatesApi';
import { UiSelect } from '../../components/ui/Select';
import { uiConfirm } from '../../components/ui/dialogs';
import { toast } from '../../components/ui/toast';
import { overlayDismiss } from '../../components/ui/useOverlayDismiss';

interface Props {
  open: boolean;
  topicId?: number;
  onClose: () => void;
  // Template selection callback: return the template to a row of the publish table
  onPick?: (tmpl: ContentTemplate) => void;
}

const SCOPE_TABS: { key: TemplateScope; label: string }[] = [
  { key: 'system', label: '系统' },
  { key: 'tenant', label: '本租户' },
  { key: 'topic',  label: '本主题' },
];

export function TemplateDrawer({ open, topicId, onClose, onPick }: Props) {
  const token = localStorage.getItem('token') || '';
  const [scope, setScope] = useState<TemplateScope>('system');
  const [list, setList] = useState<ContentTemplate[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<ContentTemplate | 'new' | null>(null);
  // Template scorecard: template_id -> score; if unavailable, fall back to the old no-score look
  const [scores, setScores] = useState<Map<number, TemplateScoreItem>>(new Map());
  const [matureDays, setMatureDays] = useState(7);

  const refresh = async (s: TemplateScope = scope) => {
    setBusy(true); setErr(null);
    try {
      const rows = await contentTemplatesApi.list(token, {
        scope: s,
        topicId: s === 'topic' ? topicId : undefined,
      });
      setList(rows);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (open) refresh(scope);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, scope, topicId]);

  // The scorecard is scope-independent; fetch once per drawer open
  useEffect(() => {
    if (!open) return;
    let dead = false;
    contentTemplatesApi.scorecard(token, { topicId })
      .then((sc) => {
        if (dead) return;
        setMatureDays(sc.mature_days);
        setScores(new Map(sc.items.map((it) => [it.template_id, it])));
      })
      .catch(() => { /* Missing scorecard doesn't affect template management itself */ });
    return () => { dead = true; };
  }, [open, topicId, token]);

  if (!open) return null;

  return (
    <>
      {/* mask */}
      <div
        className="fixed inset-0 z-40"
        style={{ background: 'rgba(0,0,0,0.35)' }} {...overlayDismiss(onClose)}
      />
      {/* drawer */}
      <aside
        className="fixed top-0 right-0 z-50 h-full w-[440px] max-w-[92vw] flex flex-col"
        style={{
          background: 'var(--bg-card)',
          borderLeft: '1px solid var(--border-color)',
          boxShadow: '-8px 0 24px rgba(0,0,0,0.18)',
        }}
      >
        <header className="flex items-center justify-between px-4 py-3"
                style={{ borderBottom: '1px solid var(--border-color)' }}>
          <h2 className="text-sm font-semibold text-primary">内容模板</h2>
          <button type="button" onClick={onClose}
                  className="text-muted hover:text-primary text-lg leading-none">×</button>
        </header>

        {/* scope tabs */}
        <div className="flex gap-1 px-3 py-2"
             style={{ borderBottom: '1px solid var(--border-color)' }}>
          {SCOPE_TABS.filter(t => t.key !== 'topic' || topicId).map(t => (
            <button key={t.key} type="button"
                    onClick={() => setScope(t.key)}
                    className="text-xs px-2.5 py-1 rounded-md"
                    style={{
                      background: scope === t.key ? 'var(--accent-primary)' : 'transparent',
                      color: scope === t.key ? '#fff' : 'var(--text-secondary)',
                      border: '1px solid var(--border-color)',
                    }}>
              {t.label}
            </button>
          ))}
          <div className="flex-1" />
          <button type="button"
                  onClick={() => setEditing('new')}
                  className="text-xs px-2.5 py-1 rounded-md"
                  style={{ background: 'var(--accent-primary)', color: '#fff' }}>
            + 新建
          </button>
        </div>

        <div className="flex-1 overflow-auto px-3 py-3 space-y-2">
          {err && (
            <div className="rounded-md p-2 text-xs"
                 style={{ background: 'var(--danger-bg)', color: 'var(--danger-fg)' }}>
              {err}
            </div>
          )}
          {!err && busy && list.length === 0 && (
            <div className="text-xs text-muted py-6 text-center">…</div>
          )}
          {!busy && list.length === 0 && (
            <div className="text-xs text-muted py-6 text-center">暂无模板</div>
          )}
          {list.map(t => (
            <TemplateCard key={t.id} tmpl={t}
                          score={scores.get(t.id)}
                          matureDays={matureDays}
                          onEdit={() => setEditing(t)}
                          onLock={async () => {
                            try {
                              await contentTemplatesApi.lock(t.id, token);
                              await refresh();
                            } catch (e: unknown) {
                              setErr(e instanceof Error ? e.message : String(e));
                            }
                          }}
                          onUnlock={async () => {
                            if (!(await uiConfirm(`解除「${t.name}」的锁定?解除后 prompt 和类型可以再编辑。`))) return;
                            try {
                              await contentTemplatesApi.unlock(t.id, token);
                              await refresh();
                            } catch (e: unknown) {
                              setErr(e instanceof Error ? e.message : String(e));
                            }
                          }}
                          onDelete={async () => {
                            if (!(await uiConfirm(
                              `删除模板「${t.name}」?已用它出过的稿件不会被删,只会解绑;发文计划里选了这个模板的行需要重新选。`,
                            ))) return;
                            try {
                              await contentTemplatesApi.remove(t.id, token);
                              toast.success('模板已删除');
                              await refresh();
                            } catch (e: unknown) {
                              setErr(e instanceof Error ? e.message : String(e));
                            }
                          }}
                          onPick={onPick ? () => onPick(t) : undefined} />
          ))}
        </div>
      </aside>

      {editing !== null && (
        <TemplateEditorModal
          mode={editing === 'new' ? 'create' : 'edit'}
          initial={editing === 'new' ? null : editing}
          scope={scope}
          topicId={topicId}
          onCancel={() => setEditing(null)}
          onSaved={async () => { setEditing(null); await refresh(); }}
        />
      )}
    </>
  );
}

// Score colors: composite score dominated by citation rate; 80+ good, 60+ fair, below that the writing approach should change
function scoreColor(score: number): string {
  if (score >= 80) return '#10b981';
  if (score >= 60) return '#eab308';
  return '#ef4444';
}

const pct = (v: number | null): string => (v === null ? '—' : `${Math.round(v)}%`);

/** Template score bar — drafts / citation rate / deductions; hover to see the metric definition */
function ScoreBar({ s, matureDays }: { s: TemplateScoreItem; matureDays: number }) {
  const engines = Object.entries(s.cited_engines);
  return (
    <div className="mt-2 rounded-md px-2 py-1.5"
         style={{ background: 'var(--bg-card)', border: '1px dashed var(--border-color)' }}>
      <div className="flex items-center gap-2">
        {s.score === null ? (
          <span className="text-[10px] text-muted"
                title={`已发布满 ${matureDays} 天的稿不足,效果还没沉淀出来,先不打分`}>
            样本不足
          </span>
        ) : (
          <span className="text-[11px] font-bold" style={{ color: scoreColor(s.score) }}
                title={'被引用率 60 · 合规通过 20 · 人工过稿 10 · 平台过审 10(无数据的维度不扣分)'
                       + (s.cited_measurable ? '' : ' —— 本模板暂无可判定引用的稿,分数只含出稿质量')}>
            {s.score} 分{s.cited_measurable ? '' : '*'}
          </span>
        )}
        <span className="text-[10px] text-muted">
          出稿 {s.docs} · 已发 {s.published}
        </span>
        <div className="flex-1" />
        {s.cited_measurable ? (
          <span className="text-[10px]"
                style={{ color: s.rates.cited === null ? 'var(--text-muted)' : scoreColor(s.rates.cited) }}
                title={`发布满 ${matureDays} 天、且回填过投放链接的 ${s.trackable_published} 篇里,`
                       + `${s.cited_docs} 篇被 AI 引擎引用过`}>
            被引用 {pct(s.rates.cited)}
          </span>
        ) : (
          <span className="text-[10px] text-muted"
                title={`没有回填投放链接的稿件,引用比对无从下手(已发布 ${s.published} 篇,`
                       + `缺链接 ${s.untracked_published} 篇)。分数只反映出稿质量。`}>
            引用待测
          </span>
        )}
      </div>
      <div className="mt-1 text-[10px] text-muted flex flex-wrap gap-x-2.5">
        {/* Refinement review score: a leading metric available on the drafting day; look at it while citation rate hasn't settled yet. Not counted in the composite score. */}
        {typeof s.quality_avg === 'number' && (
          <span style={{ color: scoreColor(s.quality_avg) }}
                title={`多 agent 精写的评审分均值(${s.quality_docs} 篇有分)。`
                       + '不计入上面的综合分 —— 它是出稿当天的前置质量指标,'
                       + '被引用率才是最终结果。'}>
            精写 {s.quality_avg} 分
          </span>
        )}
        <span title="没被合规闸门退回的比例">合规 {pct(s.rates.compliance)}</span>
        <span title="人工审核通过(含已发布)的比例">过稿 {pct(s.rates.review)}</span>
        <span title="运营回填的平台审核结果;没回填就不参与算分">平台 {pct(s.rates.platform)}</span>
        {s.rejected_compliance > 0 && (
          <span style={{ color: 'var(--danger-fg)' }}>合规退回 {s.rejected_compliance}</span>
        )}
        {s.untracked_published > 0 && (
          <span title="这些稿发出去了但没回填投放链接,引用命中回填匹配不到它们">
            缺投放链接 {s.untracked_published}
          </span>
        )}
        {engines.length > 0 && (
          <span title="引用过本模板稿件的引擎">
            引擎:{engines.map(([e, n]) => `${e}×${n}`).join(' ')}
          </span>
        )}
      </div>
    </div>
  );
}

function TemplateCard({
  tmpl, score, matureDays, onEdit, onLock, onUnlock, onDelete, onPick,
}: {
  tmpl: ContentTemplate;
  score?: TemplateScoreItem;
  matureDays: number;
  onEdit: () => void;
  onLock: () => void;
  onUnlock: () => void;
  onDelete: () => void;
  onPick?: () => void;
}) {
  return (
    <div className="rounded-md p-3 text-xs"
         style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border-color)' }}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <span className="font-semibold text-primary">{tmpl.name}</span>
          {tmpl.locked && <span title="已锁定">🔒</span>}
        </div>
        <span className="text-[10px] px-1.5 py-0.5 rounded-full"
              style={{ background: 'var(--bg-card)', color: 'var(--text-secondary)' }}>
          {tmpl.kind}
        </span>
      </div>
      <div className="mt-1.5 text-[10px] text-muted">
        长度 {tmpl.length_min}–{tmpl.length_max} 字
        {tmpl.target_platforms.length > 0 && (
          <span className="ml-2">· {tmpl.target_platforms.join(' / ')}</span>
        )}
      </div>
      {score
        ? <ScoreBar s={score} matureDays={matureDays} />
        : (
          <div className="mt-2 text-[10px] text-muted">
            还没用这个模板出过稿,暂无成绩
          </div>
        )}
      <div className="mt-2 line-clamp-3 text-muted whitespace-pre-wrap">
        {tmpl.prompt_template}
      </div>
      <div className="mt-2 flex gap-2 justify-end">
        {onPick && (
          <button type="button" onClick={onPick}
                  className="text-[10px] px-2 py-1 rounded-md"
                  style={{ background: 'var(--accent-primary)', color: '#fff' }}>
            使用
          </button>
        )}
        <button type="button" onClick={onEdit}
                className="text-[10px] px-2 py-1 rounded-md"
                style={{ background: 'transparent', color: 'var(--text-secondary)',
                         border: '1px solid var(--border-color)' }}>
          {tmpl.locked ? '查看' : '编辑'}
        </button>
        {tmpl.locked ? (
          <button type="button" onClick={onUnlock}
                  className="text-[10px] px-2 py-1 rounded-md"
                  style={{ background: 'transparent', color: 'var(--text-secondary)',
                           border: '1px solid var(--border-color)' }}
                  title="解除锁定后 prompt / 类型可以再编辑">
            🔓 解除锁定
          </button>
        ) : (
          <button type="button" onClick={onLock}
                  className="text-[10px] px-2 py-1 rounded-md"
                  style={{ background: 'transparent', color: 'var(--text-secondary)',
                           border: '1px solid var(--border-color)' }}>
            🔒 锁定
          </button>
        )}
        <button type="button" onClick={onDelete}
                className="text-[10px] px-2 py-1 rounded-md"
                style={{ background: 'transparent', color: 'var(--danger-fg)',
                         border: '1px solid rgba(239,68,68,0.35)' }}
                title={tmpl.locked ? '锁定中,需先解除锁定' : '删除这个模板'}
                disabled={tmpl.locked}>
          🗑 删除
        </button>
      </div>
    </div>
  );
}

// 2026-08-05 — add kinds already in use but missing from the dropdown: ranking roundup (industry roundup template),
// trust long-form / decision ranking / leaderboard / research report from the open-source templates, plus two English kinds
// (English templates are excluded from auto-scheduling and can only be picked manually in the plan editor). If the dropdown lacks one,
// editing an old template silently resets it to the default, so this must match the actual values in the database.
const KIND_OPTIONS = [
  '长文', '短文', 'FAQ', '案例', '测评', '对比', '教程', '问答',
  '榜单合辑', '信任长文', '决策榜单', '排行榜单', '研究报告',
];
const PLATFORM_OPTIONS = [
  '公众号', '小红书', '抖音', '视频号', '知乎', '今日头条', '微博',
  '搜狐号', '百家号', '官网博客',
];

function TemplateEditorModal({
  mode, initial, scope, topicId, onCancel, onSaved,
}: {
  mode: 'create' | 'edit';
  initial: ContentTemplate | null;
  scope: TemplateScope;
  topicId?: number;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const token = localStorage.getItem('token') || '';
  const [name, setName] = useState(initial?.name || '');
  const [kind, setKind] = useState(initial?.kind || '长文');
  const [prompt, setPrompt] = useState(initial?.prompt_template || '');
  const [platforms, setPlatforms] = useState<string[]>(initial?.target_platforms || []);
  const [lenMin, setLenMin] = useState(initial?.length_min ?? 800);
  const [lenMax, setLenMax] = useState(initial?.length_max ?? 1500);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const locked = useMemo(() => initial?.locked === true, [initial]);

  const togglePlat = (p: string) => {
    setPlatforms(s => s.includes(p) ? s.filter(x => x !== p) : [...s, p]);
  };

  const handleSave = async () => {
    if (!name.trim() || !prompt.trim()) {
      setErr('名称和 prompt 都不能为空');
      return;
    }
    setBusy(true); setErr(null);
    try {
      if (mode === 'create') {
        await contentTemplatesApi.create({
          scope, topic_id: scope === 'topic' ? topicId : undefined,
          name, kind, prompt_template: prompt,
          target_platforms: platforms,
          length_min: lenMin, length_max: lenMax,
        }, token);
      } else if (initial) {
        await contentTemplatesApi.update(initial.id, {
          name,
          ...(locked ? {} : { kind, prompt_template: prompt }),
          target_platforms: platforms,
          length_min: lenMin, length_max: lenMax,
        }, token);
      }
      onSaved();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center px-4"
         style={{ background: 'rgba(0,0,0,0.45)' }}>
      <div className="w-[560px] max-w-full rounded-md p-4"
           style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-primary">
            {mode === 'create' ? '新建模板' : (locked ? '查看模板 🔒' : '编辑模板')}
          </h3>
          <button type="button" onClick={onCancel}
                  className="text-muted hover:text-primary text-lg leading-none">×</button>
        </div>

        {err && (
          <div className="rounded-md p-2 mb-3 text-xs"
               style={{ background: 'var(--danger-bg)', color: 'var(--danger-fg)' }}>
            {err}
          </div>
        )}

        <div className="space-y-3 text-xs">
          <div>
            <label className="block text-muted mb-1">名称</label>
            <input className="w-full rounded-md px-2 py-1.5"
                   style={{ background: 'var(--bg-tertiary)', color: 'var(--text-primary)',
                            border: '1px solid var(--border-color)' }}
                   value={name} onChange={e => setName(e.target.value)} />
          </div>

          <div className="flex gap-3">
            <div className="flex-1">
              <label className="block text-muted mb-1">类型</label>
              <UiSelect className="w-full rounded-md px-2 py-1.5"
                        style={{ background: 'var(--bg-tertiary)', color: 'var(--text-primary)',
                                 border: '1px solid var(--border-color)' }}
                        value={kind}
                        disabled={locked}
                        onChange={val => setKind(val)}
                        options={KIND_OPTIONS.map(k => ({ value: k, label: k }))} />
            </div>
            <div className="w-24">
              <label className="block text-muted mb-1">最短</label>
              <input type="number" className="w-full rounded-md px-2 py-1.5"
                     style={{ background: 'var(--bg-tertiary)', color: 'var(--text-primary)',
                              border: '1px solid var(--border-color)' }}
                     value={lenMin} onChange={e => setLenMin(Number(e.target.value) || 0)} />
            </div>
            <div className="w-24">
              <label className="block text-muted mb-1">最长</label>
              <input type="number" className="w-full rounded-md px-2 py-1.5"
                     style={{ background: 'var(--bg-tertiary)', color: 'var(--text-primary)',
                              border: '1px solid var(--border-color)' }}
                     value={lenMax} onChange={e => setLenMax(Number(e.target.value) || 0)} />
            </div>
          </div>

          <div>
            <label className="block text-muted mb-1">适用平台</label>
            <div className="flex flex-wrap gap-1.5">
              {PLATFORM_OPTIONS.map(p => (
                <button key={p} type="button"
                        onClick={() => togglePlat(p)}
                        className="text-[10px] px-2 py-1 rounded-md"
                        style={{
                          background: platforms.includes(p) ? 'var(--accent-primary)' : 'transparent',
                          color: platforms.includes(p) ? '#fff' : 'var(--text-secondary)',
                          border: '1px solid var(--border-color)',
                        }}>
                  {p}
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="block text-muted mb-1">
              Prompt 模板{locked && <span className="ml-1 text-[10px]">🔒 已锁定,不可改</span>}
            </label>
            <textarea className="w-full rounded-md px-2 py-1.5 font-mono"
                      style={{ background: 'var(--bg-tertiary)', color: 'var(--text-primary)',
                               border: '1px solid var(--border-color)',
                               minHeight: 180, fontSize: 12 }}
                      value={prompt}
                      disabled={locked}
                      onChange={e => setPrompt(e.target.value)} />
            <div className="text-[10px] text-muted mt-1">
              占位符:{'{{query}} {{brand}} {{industry}} {{platform}} {{length_min}} {{length_max}} {{brand_block}}'}
            </div>
          </div>
        </div>

        <div className="mt-4 flex justify-end gap-2">
          <button type="button" onClick={onCancel}
                  className="text-xs px-3 py-1.5 rounded-md"
                  style={{ background: 'transparent', color: 'var(--text-secondary)',
                           border: '1px solid var(--border-color)' }}>
            取消
          </button>
          <button type="button" onClick={handleSave} disabled={busy}
                  className="text-xs px-3 py-1.5 rounded-md text-white"
                  style={{ background: 'var(--accent-primary)', opacity: busy ? 0.5 : 1 }}>
            {busy ? '…' : '保存'}
          </button>
        </div>
      </div>
    </div>
  );
}
