import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { mockAccount, mockKeywordGroups, mockKnowledge } from '../../../mocks/sentiment';
import { isMockMode } from '../../../services/sentimentApi';
import {
  useSentimentAccounts, useUpdateAccount, useDeleteAccount, useRunNow,
  useKnowledge, useUpsertKnowledge,
} from '../../../hooks/useSentiment';
import type { KeywordGroup } from '../../../types/sentiment';

import { TagInput } from './components/TagInput';
import { KeywordGroupsEditor } from './components/KeywordGroupsEditor';
import { KnowledgeEditor } from './components/KnowledgeEditor';

const cardStyle: React.CSSProperties = {
  background: 'var(--bg-card)',
  border: '1px solid var(--border-color)',
};

export function SettingsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const usingMock = isMockMode();

  const accountsQuery = useSentimentAccounts();
  const accounts = accountsQuery.data ?? [];
  // MVP:取首个账号(后续支持多账号选择 — 加 ?id= URL 参数)
  const account = usingMock ? mockAccount : accounts[0];

  const updateAccount = useUpdateAccount();
  const deleteAccount = useDeleteAccount();
  const runNowMutation = useRunNow();

  const knowledgeQuery = useKnowledge(usingMock ? null : (account?.id ?? null));
  const upsertKnowledge = useUpsertKnowledge();

  // ── form state(从账号 / mock 初始化) ──
  const [target, setTarget] = useState('');
  const [ticker, setTicker] = useState('');
  const [aliases, setAliases] = useState<string[]>([]);
  const [intent, setIntent] = useState('');
  const [keywordGroups, setKeywordGroups] = useState<KeywordGroup[]>([]);
  const [excludes, setExcludes] = useState<string[]>([]);
  const [emails, setEmails] = useState<string[]>([]);

  useEffect(() => {
    if (!account) return;
    setTarget(account.target);
    setTicker(account.ticker);
    setAliases(account.aliases ?? []);
    setIntent(account.intent ?? '');
    // 优先用结构化分组,空则用扁平 keywords 兜底为单一"自定义"组,
    // mock 模式直接展示完整的 mockKeywordGroups.
    if (usingMock) {
      setKeywordGroups(mockKeywordGroups);
    } else if (account.keyword_groups && account.keyword_groups.length > 0) {
      setKeywordGroups(account.keyword_groups);
    } else if ((account.keywords ?? []).length > 0) {
      setKeywordGroups([
        { name: '自定义', kind: 'custom', items: account.keywords.map((term) => ({ term })) },
      ]);
    } else {
      setKeywordGroups([]);
    }
    setExcludes(account.excludes ?? []);
    setEmails(account.notify_emails ?? []);
  }, [account, usingMock]);

  // ── 知识库:用 API 数据 vs mock 数据 ──
  const knowledgeFromApi = (knowledgeQuery.data ?? []).reduce(
    (acc, k) => ({ ...acc, [k.key]: k.body }),
    { brand_voice: '', legal_redlines: '', response_playbook: '' } as Record<string, string>,
  );
  const [localKnowledge, setLocalKnowledge] = useState(mockKnowledge);

  useEffect(() => {
    if (!usingMock && knowledgeQuery.data) {
      setLocalKnowledge({
        brand_voice: knowledgeFromApi.brand_voice || '',
        legal_redlines: knowledgeFromApi.legal_redlines || '',
        response_playbook: knowledgeFromApi.response_playbook || '',
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [knowledgeQuery.data, usingMock]);

  if (!usingMock && accountsQuery.isLoading) {
    return <div className="rounded-xl py-12 text-center text-secondary text-sm" style={cardStyle}>
      {t('common.loading') || 'Loading...'}
    </div>;
  }
  if (!account) {
    return (
      <div className="rounded-xl py-12 text-center text-secondary text-sm" style={cardStyle}>
        没有舆情账号。请先在工作台创建。
        <div className="mt-3">
          <Link to="/sentiment" className="btn-solid rounded-md px-4 py-2 text-sm font-semibold">
            返回工作台
          </Link>
        </div>
      </div>
    );
  }

  // ── 保存动作 ──
  const buildPayload = () => ({
    target, ticker,
    aliases, intent: intent.trim() || undefined,
    keyword_groups: keywordGroups,
    excludes, notify_emails: emails,
  });

  const persistKnowledge = async () => {
    if (usingMock) return;
    const tasks: Promise<unknown>[] = [];
    for (const key of ['brand_voice', 'legal_redlines', 'response_playbook'] as const) {
      const newBody = localKnowledge[key] ?? '';
      const oldBody = knowledgeFromApi[key] ?? '';
      if (newBody !== oldBody) {
        tasks.push(upsertKnowledge.mutateAsync({ accountId: account.id, key, body: newBody }));
      }
    }
    await Promise.all(tasks);
  };

  const handleSave = async () => {
    if (usingMock) {
      alert(t('dashboard.sentiment.settings.saved'));
      return;
    }
    try {
      await updateAccount.mutateAsync({ id: account.id, payload: buildPayload() });
      await persistKnowledge();
      alert(t('dashboard.sentiment.settings.saved'));
    } catch (e) {
      alert(`保存失败:${e instanceof Error ? e.message : e}`);
    }
  };

  const handleSaveAndRun = async () => {
    if (usingMock) {
      alert(`${t('dashboard.sentiment.settings.saved')}\n${t('dashboard.sentiment.settings.runNowQueued')}`);
      return;
    }
    try {
      await updateAccount.mutateAsync({ id: account.id, payload: buildPayload() });
      await persistKnowledge();
      await runNowMutation.mutateAsync(account.id);
      alert(`${t('dashboard.sentiment.settings.saved')}\n${t('dashboard.sentiment.settings.runNowQueued')}`);
      navigate('/sentiment');
    } catch (e) {
      alert(`保存失败:${e instanceof Error ? e.message : e}`);
    }
  };

  const handleDelete = async () => {
    if (!confirm('确定要删除这个监测账号吗?(数据库内的舆情数据不会被立即清除)')) return;
    if (usingMock) {
      alert(t('dashboard.sentiment.settings.deleted'));
      return;
    }
    try {
      await deleteAccount.mutateAsync(account.id);
      alert(t('dashboard.sentiment.settings.deleted'));
      navigate('/sentiment');
    } catch (e) {
      alert(`删除失败:${e instanceof Error ? e.message : e}`);
    }
  };

  const saving = updateAccount.isPending || upsertKnowledge.isPending;
  const running = runNowMutation.isPending;

  return (
    <div className="space-y-4">
      <header className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <Link to="/sentiment" className="text-xs text-secondary hover:text-primary">
            {t('dashboard.sentiment.settings.back')}
          </Link>
          <h1 className="text-2xl font-bold text-primary mt-1">
            {t('dashboard.sentiment.settings.title')}
          </h1>
        </div>
      </header>

      <Section title={t('dashboard.sentiment.settings.sections.target')}>
        <Grid>
          <Field label={t('dashboard.sentiment.settings.fields.target')} required>
            <Input value={target} onChange={setTarget} />
          </Field>
          <Field label={t('dashboard.sentiment.settings.fields.ticker')}>
            <Input value={ticker} onChange={setTicker} mono />
          </Field>
        </Grid>
        <Field label={t('dashboard.sentiment.settings.fields.aliases')}>
          <TagInput value={aliases} onChange={setAliases} placeholder={t('dashboard.sentiment.settings.fields.tagPlaceholder')} />
        </Field>
        <Field label={t('dashboard.sentiment.settings.fields.intent')}>
          <textarea value={intent} onChange={(e) => setIntent(e.target.value)} rows={2}
            className="w-full px-3 py-2 rounded text-sm resize-y"
            style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
            placeholder={t('dashboard.sentiment.settings.fields.intentPlaceholder')} />
        </Field>
      </Section>

      <Section title={t('dashboard.sentiment.settings.sections.keywords')}>
        <KeywordGroupsEditor value={keywordGroups} onChange={setKeywordGroups} />
        <Field label={t('dashboard.sentiment.settings.fields.excludes')}>
          <TagInput value={excludes} onChange={setExcludes} placeholder={t('dashboard.sentiment.settings.fields.tagPlaceholder')} />
        </Field>
      </Section>

      <Section title={t('dashboard.sentiment.settings.sections.notify')}>
        <Field label={t('dashboard.sentiment.settings.fields.emails')}>
          <TagInput value={emails} onChange={setEmails} placeholder="ir@yourcompany.com" max={10} />
        </Field>
      </Section>

      <Section title={t('dashboard.sentiment.settings.sections.knowledge')}>
        <KnowledgeEditor knowledge={localKnowledge} onChange={setLocalKnowledge} />
      </Section>

      <section className="rounded-xl p-5 flex items-center justify-between flex-wrap gap-3" style={cardStyle}>
        <div className="flex gap-2">
          <button type="button" onClick={handleSave} disabled={saving}
            className="rounded-md px-4 py-2 text-sm font-semibold disabled:opacity-50"
            style={{ background: 'var(--bg-tertiary)', color: 'var(--text-primary)' }}>
            {saving ? '...' : t('dashboard.sentiment.settings.save')}
          </button>
          <button type="button" onClick={handleSaveAndRun} disabled={saving || running}
            className="btn-solid rounded-md px-4 py-2 text-sm font-semibold disabled:opacity-50">
            {(saving || running) ? '...' : t('dashboard.sentiment.settings.saveAndRun')}
          </button>
        </div>
        <button type="button" onClick={handleDelete}
          className="text-xs font-semibold" style={{ color: '#dc2626' }}>
          {t('dashboard.sentiment.settings.delete')}
        </button>
      </section>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl p-5 space-y-3" style={cardStyle}>
      <h2 className="text-sm font-semibold text-primary uppercase tracking-wide">{title}</h2>
      {children}
    </section>
  );
}

function Field({ label, required, children }: { label: string; required?: boolean; children: React.ReactNode }) {
  return (
    <div>
      <label className="text-xs font-semibold text-secondary uppercase tracking-wider block mb-1.5">
        {label} {required && <span style={{ color: '#dc2626' }}>*</span>}
      </label>
      {children}
    </div>
  );
}

function Grid({ children }: { children: React.ReactNode }) {
  return <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">{children}</div>;
}

function Input({ value, onChange, mono }: { value: string; onChange: (v: string) => void; mono?: boolean }) {
  return (
    <input type="text" value={value} onChange={(e) => onChange(e.target.value)}
      className={`w-full px-3 py-2 rounded text-sm ${mono ? 'font-mono' : ''}`}
      style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }} />
  );
}
