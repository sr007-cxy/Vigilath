import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { aiTelemetryApi, type Topic } from '../../services/aiTelemetryApi';

export function AdminAccountTopics() {
  const { userId } = useParams<{ userId: string }>();
  const token = localStorage.getItem('token') || '';
  const [topics, setTopics] = useState<Topic[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<Topic | null>(null);

  const reload = () => {
    if (!userId) return;
    aiTelemetryApi.adminListUserTopics(Number(userId), token)
      .then(setTopics).catch(() => setTopics([])).finally(() => setLoading(false));
  };

  useEffect(reload, [userId, token]);

  if (!userId) return null;

  return (
    <div className="max-w-[1100px] mx-auto p-6">
      <div className="text-xs text-muted mb-2">
        <Link to="/workbench/accounts" className="hover:underline">账户管理</Link>
        {' / '}用户 #{userId} 的主题
      </div>
      <h1 className="text-xl font-semibold text-primary mb-4">主题配置(admin)</h1>

      {loading ? (
        <div className="text-xs text-muted text-center py-10">加载中…</div>
      ) : topics.length === 0 ? (
        <div className="text-xs text-muted text-center py-10">该用户暂无主题</div>
      ) : (
        <ul className="grid gap-3">
          {topics.map(t => (
            <li key={t.id} className="p-4 rounded-lg"
              style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
              <div className="flex justify-between mb-2">
                <span className="text-sm font-medium text-primary">{t.name}</span>
                <span className="text-xs text-muted">
                  target: {t.target} · {t.queries.length} queries · {t.engines.length} engines
                </span>
              </div>
              <div className="text-xs text-secondary mb-2">
                行业: {t.industry || '—'} · 状态: {t.last_run_status || 'never_run'}
              </div>
              <div className="text-xs">
                <span className="text-muted">扩展提示: </span>
                {(t.prompt_extension && t.prompt_extension.trim()) ? (
                  <span className="text-primary">{t.prompt_extension}</span>
                ) : (
                  <span className="text-muted">未配置</span>
                )}
              </div>
              <div className="mt-3">
                <button
                  type="button"
                  onClick={() => setEditing(t)}
                  className="text-xs px-3 py-1 rounded"
                  style={{ background: 'var(--accent-primary)', color: 'white' }}
                >
                  编辑扩展提示
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {editing && (
        <PromptExtensionEditor
          topic={editing}
          token={token}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); reload(); }}
        />
      )}
    </div>
  );
}

function PromptExtensionEditor({
  topic, token, onClose, onSaved,
}: {
  topic: Topic; token: string; onClose: () => void; onSaved: () => void;
}) {
  const [val, setVal] = useState(topic.prompt_extension || '');
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const save = async () => {
    setSaving(true);
    setErr(null);
    try {
      await aiTelemetryApi.updateTopic(topic.id, {
        name: topic.name,
        target: topic.target,
        target_aliases: topic.target_aliases,
        industry: topic.industry,
        queries: topic.queries,
        engines: topic.engines,
        enabled: topic.enabled,
        prompt_extension: val.trim() || null,
      }, token);
      onSaved();
    } catch (e) {
      setErr((e as Error)?.message || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center" onClick={onClose}>
      <div className="w-full max-w-xl p-5 rounded-lg" onClick={e => e.stopPropagation()}
        style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
        <h3 className="text-sm font-medium text-primary mb-2">扩展提示 · {topic.name}</h3>
        <p className="text-xs text-muted mb-3">
          这段文本会拼到每条 query 末尾发给 AI 引擎,用于补充上下文(例:"侧重生成式 AI 法律咨询场景")。
          普通用户看不到此字段。
        </p>
        <textarea
          value={val} onChange={e => setVal(e.target.value)}
          rows={6} maxLength={2000}
          className="w-full p-2 text-sm rounded"
          style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
          placeholder="留空则跑批不附加 prompt_extension"
        />
        <div className="text-[10px] text-muted text-right">{val.length} / 2000</div>
        {err && <div className="text-xs text-red-500 mt-2">{err}</div>}
        <div className="flex justify-end gap-2 mt-4">
          <button type="button" onClick={onClose} className="text-xs px-3 py-1 rounded text-muted">取消</button>
          <button
            type="button" onClick={save} disabled={saving}
            className="text-xs px-3 py-1 rounded"
            style={{ background: 'var(--accent-primary)', color: 'white', opacity: saving ? 0.6 : 1 }}
          >
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </div>
    </div>
  );
}
