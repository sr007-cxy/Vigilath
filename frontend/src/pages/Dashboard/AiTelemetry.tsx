// AI 遥测 工作台页 — 配置话题 + 查看跑批结果.
//
// Tab 1「话题配置」:
//   - 列表:启用 / 话题名 / Query 数 / 引擎数 / 最近跑 / 状态 / 操作
//   - 新建/编辑 Modal:话题名 + Query 多行 + 引擎 10 复选(国内/海外分组)+ 启用开关
//   - 立即试跑:点「立即试跑一次」直接调 /run-now,结果就在 modal 底部展示
//
// Tab 2「跑批结果」:第一版占位 (Step 3 再做)
//
// 频率由后端固定为 daily,前端不暴露时间选择.
import { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';

import { PageHead } from '../../components/PageHead';
import {
  aiTelemetryApi, CN_ENGINES, GLOBAL_ENGINES,
  type EngineId, type Topic, type TopicPayload, type RunNowResult,
} from '../../services/aiTelemetryApi';

type TabKey = 'config' | 'results';

export function AiTelemetry() {
  const { t } = useTranslation();
  const token = localStorage.getItem('token') || '';
  const [tab, setTab] = useState<TabKey>('config');
  const [topics, setTopics] = useState<Topic[]>([]);
  const [loading, setLoading] = useState(false);
  // undefined = modal closed; null = creating; Topic = editing
  const [editing, setEditing] = useState<Topic | null | undefined>(undefined);

  const refresh = async () => {
    setLoading(true);
    try {
      setTopics(await aiTelemetryApi.listTopics(token));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { refresh(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

  const handleSave = async (payload: TopicPayload) => {
    if (editing && editing.id) {
      await aiTelemetryApi.updateTopic(editing.id, payload, token);
    } else {
      await aiTelemetryApi.createTopic(payload, token);
    }
    setEditing(undefined);
    refresh();
  };

  const handleDelete = async (id: number) => {
    if (!window.confirm(t('common.confirmDelete') || 'Delete?')) return;
    await aiTelemetryApi.deleteTopic(id, token);
    refresh();
  };

  return (
    <div className="space-y-4">
      <PageHead titleKey="dashboard.aiTelemetry.title" titleFallback="AI Telemetry" />

      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-primary">{t('dashboard.aiTelemetry.title')}</h1>
          <p className="text-sm text-secondary mt-1">{t('dashboard.aiTelemetry.subtitle')}</p>
        </div>
        <button
          type="button"
          onClick={() => setEditing(null)}
          className="px-3 py-1.5 text-sm rounded-md text-white"
          style={{ background: 'var(--accent-primary)' }}
        >
          + {t('dashboard.aiTelemetry.newTopic')}
        </button>
      </header>

      <div className="flex gap-1 border-b" style={{ borderColor: 'var(--border-color)' }}>
        {(['config', 'results'] as TabKey[]).map(k => (
          <button
            key={k}
            type="button"
            onClick={() => setTab(k)}
            className="px-3 py-2 text-sm -mb-px"
            style={{
              borderBottom: tab === k ? '2px solid var(--accent-primary)' : '2px solid transparent',
              color: tab === k ? 'var(--accent-primary)' : 'var(--text-secondary)',
            }}
          >
            {t(`dashboard.aiTelemetry.tab${k === 'config' ? 'Config' : 'Results'}`)}
          </button>
        ))}
      </div>

      {tab === 'config' && (
        <TopicTable topics={topics} loading={loading} onEdit={setEditing} onDelete={handleDelete} />
      )}
      {tab === 'results' && <ResultsPlaceholder />}

      {editing !== undefined && (
        <TopicModal
          initial={editing}
          token={token}
          onCancel={() => setEditing(undefined)}
          onSave={handleSave}
        />
      )}
    </div>
  );
}

// ── 话题列表 ───────────────────────────────────────────────────

function TopicTable({
  topics, loading, onEdit, onDelete,
}: {
  topics: Topic[]; loading: boolean;
  onEdit: (t: Topic) => void; onDelete: (id: number) => void;
}) {
  const { t } = useTranslation();
  if (loading) return <div className="py-12 text-center text-sm text-muted">…</div>;
  if (topics.length === 0) {
    return (
      <div
        className="py-12 text-center text-sm text-muted rounded-lg"
        style={{ background: 'var(--bg-card)', border: '1px dashed var(--border-color)' }}
      >
        {t('dashboard.aiTelemetry.empty')}
      </div>
    );
  }

  const c = (k: string) => t(`dashboard.aiTelemetry.col.${k}`);

  return (
    <div
      className="rounded-lg overflow-hidden"
      style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}
    >
      <table className="w-full text-sm">
        <thead>
          <tr style={{ background: 'var(--bg-secondary)', color: 'var(--text-secondary)' }}>
            <th className="text-left px-3 py-2 font-medium">{c('enabled')}</th>
            <th className="text-left px-3 py-2 font-medium">{c('name')}</th>
            <th className="text-left px-3 py-2 font-medium">{c('queries')}</th>
            <th className="text-left px-3 py-2 font-medium">{c('engines')}</th>
            <th className="text-left px-3 py-2 font-medium">{c('lastRun')}</th>
            <th className="text-left px-3 py-2 font-medium">{c('status')}</th>
            <th className="text-right px-3 py-2 font-medium">{c('actions')}</th>
          </tr>
        </thead>
        <tbody>
          {topics.map(tp => (
            <tr key={tp.id} style={{ borderTop: '1px solid var(--border-color)' }}>
              <td className="px-3 py-2">{tp.enabled ? '✓' : '—'}</td>
              <td className="px-3 py-2 text-primary">{tp.name}</td>
              <td className="px-3 py-2">{tp.queries.length}</td>
              <td className="px-3 py-2">{tp.engines.length}/10</td>
              <td className="px-3 py-2 text-secondary">{formatTime(tp.last_run_at)}</td>
              <td className="px-3 py-2">{renderStatus(tp.last_run_status)}</td>
              <td className="px-3 py-2 text-right space-x-2">
                <button className="text-xs text-secondary hover:text-primary" onClick={() => onEdit(tp)}>编辑</button>
                <button className="text-xs text-rose-500 hover:text-rose-400" onClick={() => onDelete(tp.id)}>删除</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatTime(iso?: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return d.toLocaleDateString();
}

function renderStatus(s?: string | null) {
  if (!s) return <span className="text-muted">—</span>;
  const map: Record<string, [string, string]> = {
    success: ['✓', 'text-emerald-500'],
    failed: ['✗', 'text-rose-500'],
    running: ['…', 'text-blue-500'],
  };
  const [icon, color] = map[s] || ['?', 'text-muted'];
  return <span className={color}>{icon} {s}</span>;
}

// ── 跑批结果占位 ──────────────────────────────────────────────

function ResultsPlaceholder() {
  return (
    <div
      className="py-12 text-center text-sm text-muted rounded-lg"
      style={{ background: 'var(--bg-card)', border: '1px dashed var(--border-color)' }}
    >
      跑批结果视图建设中(Step 3)
    </div>
  );
}

// ── 新建/编辑 Modal ───────────────────────────────────────────

interface TopicModalProps {
  initial: Topic | null;
  token: string;
  onCancel: () => void;
  onSave: (payload: TopicPayload) => Promise<void>;
}

function TopicModal({ initial, token, onCancel, onSave }: TopicModalProps) {
  const { t } = useTranslation();
  const [name, setName] = useState(initial?.name || '');
  const [queriesText, setQueriesText] = useState((initial?.queries || []).join('\n'));
  const [engines, setEngines] = useState<Set<EngineId>>(
    new Set(initial?.engines || ['deepseek', 'doubao', 'qwen', 'wenxin', 'yuanbao'])
  );
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);
  const [runResults, setRunResults] = useState<RunNowResult[] | null>(null);

  const queries = useMemo(
    () => queriesText.split('\n').map(s => s.trim()).filter(Boolean).slice(0, 10),
    [queriesText],
  );

  const valid = name.trim().length > 0 && queries.length > 0 && engines.size > 0;

  const toggleEngine = (e: EngineId) => {
    setEngines(prev => {
      const next = new Set(prev);
      if (next.has(e)) next.delete(e); else next.add(e);
      return next;
    });
  };

  const buildPayload = (): TopicPayload => ({
    name: name.trim(),
    queries,
    engines: Array.from(engines),
    enabled,
  });

  const handleSave = async () => {
    if (!valid) return;
    setSaving(true);
    try { await onSave(buildPayload()); }
    finally { setSaving(false); }
  };

  const handleRunNow = async () => {
    if (!valid) return;
    setRunning(true);
    try {
      const res = await aiTelemetryApi.runNow(buildPayload(), token);
      setRunResults(res);
    } finally {
      setRunning(false);
    }
  };

  const node = (
    <div
      className="fixed inset-0 z-[1100] flex items-center justify-center p-4"
      style={{ background: 'rgba(0,0,0,0.45)' }}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel(); }}
    >
      <div
        className="rounded-xl shadow-2xl w-full max-w-2xl max-h-[88vh] flex flex-col"
        style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}
      >
        <header
          className="px-5 py-3 flex items-center justify-between"
          style={{ borderBottom: '1px solid var(--border-color)' }}
        >
          <h3 className="text-sm font-semibold text-primary">
            {t(initial ? 'dashboard.aiTelemetry.editTopic' : 'dashboard.aiTelemetry.newTopic')}
          </h3>
          <button type="button" onClick={onCancel} className="text-muted hover:text-primary text-lg leading-none px-2">×</button>
        </header>

        <div className="px-5 py-4 space-y-4 overflow-y-auto">
          <label className="block">
            <span className="text-xs text-secondary">{t('dashboard.aiTelemetry.form.name')}*</span>
            <input
              type="text" value={name} onChange={e => setName(e.target.value)}
              placeholder={t('dashboard.aiTelemetry.form.namePlaceholder') || ''}
              className="mt-1 w-full px-3 py-1.5 rounded-md text-sm"
              style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
            />
          </label>

          <label className="block">
            <span className="text-xs text-secondary">{t('dashboard.aiTelemetry.form.queries')}*</span>
            <textarea
              rows={5} value={queriesText} onChange={e => setQueriesText(e.target.value)}
              placeholder={t('dashboard.aiTelemetry.form.queriesPlaceholder') || ''}
              className="mt-1 w-full px-3 py-1.5 rounded-md text-sm font-mono"
              style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
            />
            <span className="text-xs text-muted">{queries.length} / 10</span>
          </label>

          <div>
            <span className="text-xs text-secondary">{t('dashboard.aiTelemetry.form.engines')}*</span>
            <div className="mt-2 space-y-2">
              <EngineRow
                label={t('dashboard.aiTelemetry.form.enginesCN')}
                engines={CN_ENGINES} selected={engines} onToggle={toggleEngine}
              />
              <EngineRow
                label={t('dashboard.aiTelemetry.form.enginesGlobal')}
                engines={GLOBAL_ENGINES} selected={engines} onToggle={toggleEngine}
              />
            </div>
          </div>

          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />
            <span className="text-sm text-primary">{t('dashboard.aiTelemetry.form.enabled')}</span>
          </label>

          <p className="text-xs text-muted">{t('dashboard.aiTelemetry.form.scheduleNote')}</p>

          {runResults && (
            <div
              className="mt-2 rounded-md p-3 text-xs space-y-2 max-h-60 overflow-y-auto"
              style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}
            >
              {runResults.map((r, i) => (
                <div key={i} className="border-b pb-2 last:border-0" style={{ borderColor: 'var(--border-color)' }}>
                  <div className="text-primary font-medium">{r.engine} · {r.query}</div>
                  {r.error ? (
                    <div className="text-rose-500 mt-1">⚠ {r.error}</div>
                  ) : (
                    <>
                      <div className="text-secondary mt-1 line-clamp-3">{r.answer}</div>
                      {r.citations.length > 0 && (
                        <div className="text-muted mt-1">引用 {r.citations.length} 条</div>
                      )}
                    </>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        <footer
          className="px-5 py-3 flex items-center justify-end gap-2"
          style={{ borderTop: '1px solid var(--border-color)' }}
        >
          <button type="button" onClick={onCancel} className="px-3 py-1.5 text-sm rounded-md text-secondary">
            {t('dashboard.aiTelemetry.form.cancel')}
          </button>
          <button
            type="button" onClick={handleRunNow} disabled={!valid || running}
            className="px-3 py-1.5 text-sm rounded-md disabled:opacity-40"
            style={{ background: 'var(--bg-secondary)', color: 'var(--text-primary)', border: '1px solid var(--border-color)' }}
          >
            {running ? '…' : t('dashboard.aiTelemetry.form.runNow')}
          </button>
          <button
            type="button" onClick={handleSave} disabled={!valid || saving}
            className="px-3 py-1.5 text-sm rounded-md text-white disabled:opacity-40"
            style={{ background: 'var(--accent-primary)' }}
          >
            {saving ? '…' : t('dashboard.aiTelemetry.form.save')}
          </button>
        </footer>
      </div>
    </div>
  );

  return createPortal(node, document.body);
}

function EngineRow({
  label, engines, selected, onToggle,
}: {
  label: string; engines: EngineId[];
  selected: Set<EngineId>; onToggle: (e: EngineId) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex items-center gap-2 flex-wrap">
      <span className="text-xs text-muted w-10">{label}</span>
      {engines.map(e => {
        const active = selected.has(e);
        return (
          <button
            key={e} type="button" onClick={() => onToggle(e)}
            className="px-2 py-1 rounded text-xs"
            style={{
              background: active ? 'var(--accent-primary)' : 'var(--bg-input)',
              color: active ? 'white' : 'var(--text-secondary)',
              border: '1px solid var(--border-color)',
            }}
          >
            {t(`dashboard.aiTelemetry.engine.${e}`)}
          </button>
        );
      })}
    </div>
  );
}
