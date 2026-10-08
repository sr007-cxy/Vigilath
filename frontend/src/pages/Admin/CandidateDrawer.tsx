// Flywheel P1-B -- candidate topic drawer: unhit prompts + cell insight suggestions; check them to insert into the publish plan in one click.
// Structure follows TemplateDrawer (home-made right slide-out); inserting uses POST /admin/topics/{id}/plan/insert-items,
// and on success the latest plan returned by the backend is passed back to the editor to merge (with origin provenance).
import { useEffect, useState } from 'react';
import {
  adminReviewApi, type ExecutionPlan, type InsertPlanItemReq,
  type PlanCandidates,
} from '../../services/adminReviewApi';
import { engineLabel } from '../../constants/engines';
import { overlayDismiss } from '../../components/ui/useOverlayDismiss';

interface Props {
  open: boolean;
  topicId: number;
  onClose: () => void;
  onInserted: (updated: ExecutionPlan) => void;
}

type PickKey = string; // "unhit:<query>" | "insight:<idx>"

export function CandidateDrawer({ open, topicId, onClose, onInserted }: Props) {
  const token = localStorage.getItem('token') || '';
  const [data, setData] = useState<PlanCandidates | null>(null);
  const [picked, setPicked] = useState<Set<PickKey>>(new Set());
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setData(null); setPicked(new Set()); setErr(null);
    adminReviewApi.getPlanCandidates(topicId, token)
      .then(setData)
      .catch(e => setErr(e.message));
  }, [open, topicId]);

  if (!open) return null;

  const toggle = (k: PickKey) => {
    setPicked(prev => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k); else next.add(k);
      return next;
    });
  };

  const onInsert = async () => {
    if (!data || picked.size === 0 || busy) return;
    setBusy(true); setErr(null);
    const items: InsertPlanItemReq[] = [];
    for (const u of data.unhit_queries) {
      if (picked.has(`unhit:${u.query}`)) {
        items.push({
          seed: u.seed || u.query, queries: [u.query],
          origin: { type: 'unhit_query', ref: u.query },
        });
      }
    }
    data.insight_recommendations.forEach((r, i) => {
      if (picked.has(`insight:${i}`)) {
        items.push({
          seed: r.title || r.query, queries: r.query ? [r.query] : [],
          note: r.action || null,
          origin: { type: 'cell_insight', ref: { query: r.query, engine: r.engine } },
        });
      }
    });
    try {
      const updated = await adminReviewApi.insertPlanItems(topicId, items, token);
      onInserted(updated);
      onClose();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
    setBusy(false);
  };

  const box = (active: boolean) => ({
    background: active ? 'rgba(59,130,246,0.08)' : 'var(--bg-input)',
    border: `1px solid ${active ? 'var(--accent-primary)' : 'var(--border-color)'}`,
  });

  return (
    <>
      <div className="fixed inset-0 z-40" style={{ background: 'rgba(0,0,0,0.35)' }} {...overlayDismiss(onClose)} />
      <aside className="fixed top-0 right-0 bottom-0 z-50 w-[460px] max-w-full flex flex-col"
        style={{ background: 'var(--bg-card)', borderLeft: '1px solid var(--border-color)' }}>
        <header className="flex items-center gap-2 px-4 py-3 border-b"
          style={{ borderColor: 'var(--border-color)' }}>
          <h3 className="text-sm font-semibold text-primary">🎯 候选选题</h3>
          <span className="text-[11px] text-muted">检测短板回流,勾选后插入发文计划</span>
          <div className="flex-1" />
          <button type="button" onClick={onClose} className="text-xs px-2 py-1 rounded"
            style={{ color: 'var(--text-secondary)' }}>✕</button>
        </header>

        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {err && (
            <div className="rounded-md p-2 text-xs"
              style={{ background: 'var(--danger-bg)', color: 'var(--danger-fg)' }}>{err}</div>
          )}
          {!data && !err && <div className="text-xs text-muted py-8 text-center">加载中…</div>}

          {data && (
            <>
              <section>
                <h4 className="text-xs font-medium text-primary mb-2">
                  未命中提示词({data.unhit_queries.length})
                  <span className="text-muted font-normal ml-1">— 投放了但 AI 还搜不到</span>
                </h4>
                {data.unhit_queries.length === 0 && (
                  <div className="text-[11px] text-muted">全部已命中 🎉</div>
                )}
                <div className="space-y-1.5">
                  {data.unhit_queries.map(u => {
                    const k = `unhit:${u.query}`;
                    const active = picked.has(k);
                    return (
                      <button key={k} type="button" onClick={() => toggle(k)}
                        className="w-full text-left px-2.5 py-2 rounded-md text-xs"
                        style={box(active)}>
                        <span className="text-primary">{u.query}</span>
                        {u.seed && <span className="text-muted ml-2 text-[10px]">种子:{u.seed}</span>}
                      </button>
                    );
                  })}
                </div>
              </section>

              <section>
                <h4 className="text-xs font-medium text-primary mb-2">
                  洞察建议({data.insight_recommendations.length})
                  <span className="text-muted font-normal ml-1">— 来自单元格 AI 诊断</span>
                </h4>
                {data.insight_recommendations.length === 0 && (
                  <div className="text-[11px] text-muted">暂无建议(先在矩阵页生成单元格洞察)</div>
                )}
                <div className="space-y-1.5">
                  {data.insight_recommendations.map((r, i) => {
                    const k = `insight:${i}`;
                    const active = picked.has(k);
                    return (
                      <button key={k} type="button" onClick={() => toggle(k)}
                        className="w-full text-left px-2.5 py-2 rounded-md text-xs"
                        style={box(active)}>
                        <div className="flex items-center gap-1.5">
                          <span className="px-1 py-0.5 rounded text-[10px] font-medium"
                            style={{ background: 'rgba(234,88,12,0.12)', color: '#ea580c' }}>
                            {r.priority || 'P1'}
                          </span>
                          <span className="text-primary font-medium">{r.title}</span>
                        </div>
                        <div className="text-[10px] text-muted mt-1">
                          {r.query} × {engineLabel(r.engine)}{r.action ? ` — ${r.action}` : ''}
                        </div>
                      </button>
                    );
                  })}
                </div>
              </section>
            </>
          )}
        </div>

        <footer className="px-4 py-3 border-t flex items-center gap-2"
          style={{ borderColor: 'var(--border-color)' }}>
          <span className="text-[11px] text-muted">已选 {picked.size} 条 → 明天起逐日排期</span>
          <div className="flex-1" />
          <button type="button" onClick={onInsert} disabled={picked.size === 0 || busy}
            className="text-xs px-3 py-1.5 rounded-md disabled:opacity-50"
            style={{ background: 'var(--accent-primary)', color: '#fff' }}>
            {busy ? '插入中…' : '插入发文计划'}
          </button>
        </footer>
      </aside>
    </>
  );
}
