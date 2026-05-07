// 品牌与监控配置 Tab — 统一放在账户中心.
//
// 4 个区块:
//   1. 品牌信息: 品牌名、股票代码、别名
//   2. 监控配置: 监测重点、关键词分组、排除词
//   3. 通知设置: 通知邮箱
//   4. 知识库:   品牌语调、法务红线、响应剧本
//
// 未配置时展示 OnboardingWizard(品牌创建表单).

import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { mockAccount, mockKeywordGroups, mockKnowledge } from '../../mocks/sentiment';
import { isMockMode } from '../../services/sentimentApi';
import {
  useSentimentAccounts, useUpdateAccount, useDeleteAccount, useRunNow,
  useKnowledge, useUpsertKnowledge,
} from '../../hooks/useSentiment';
import type { KeywordGroup } from '../../types/sentiment';

import { TagInput } from '../Dashboard/sentiment/components/TagInput';
import { KeywordGroupsEditor } from '../Dashboard/sentiment/components/KeywordGroupsEditor';
import { KnowledgeEditor } from '../Dashboard/sentiment/components/KnowledgeEditor';
import { OnboardingWizard } from '../Dashboard/sentiment/OnboardingWizard';

const cardStyle: React.CSSProperties = {
  background: 'var(--bg-card)',
  border: '1px solid var(--border-color)',
};

export function BrandSettingsTab() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const usingMock = isMockMode();

  const accountsQuery = useSentimentAccounts();
  const accounts = accountsQuery.data ?? [];
  const account = usingMock ? mockAccount : accounts[0];

  const updateAccount = useUpdateAccount();
  const deleteAccount = useDeleteAccount();
  const runNowMutation = useRunNow();

  const knowledgeQuery = useKnowledge(usingMock ? null : (account?.id ?? null));
  const upsertKnowledge = useUpsertKnowledge();

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

  // 未配置品牌 → 显示创建表单
  if (!usingMock && accountsQuery.isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div
          className="animate-spin rounded-full h-8 w-8 border-t-2 border-b-2"
          style={{ borderColor: 'var(--accent-primary)' }}
        />
      </div>
    );
  }

  if (!usingMock && !account) {
    return (
      <OnboardingWizard
        onSubmit={() => {
          accountsQuery.refetch();
        }}
      />
    );
  }

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
        tasks.push(upsertKnowledge.mutateAsync({ accountId: account!.id, key, body: newBody }));
      }
    }
    await Promise.all(tasks);
  };

  const handleSave = async () => {
    if (usingMock) {
      alert(t('account.brand.saved'));
      return;
    }
    try {
      await updateAccount.mutateAsync({ id: account!.id, payload: buildPayload() });
      await persistKnowledge();
      alert(t('account.brand.saved'));
    } catch (e) {
      alert(`${t('account.brand.saveFailed')}${e instanceof Error ? e.message : e}`);
    }
  };

  const handleSaveAndRun = async () => {
    if (usingMock) {
      alert(`${t('account.brand.saved')}\n${t('account.brand.runNowQueued')}`);
      return;
    }
    try {
      await updateAccount.mutateAsync({ id: account!.id, payload: buildPayload() });
      await persistKnowledge();
      await runNowMutation.mutateAsync(account!.id);
      alert(`${t('account.brand.saved')}\n${t('account.brand.runNowQueued')}`);
      navigate('/sentiment');
    } catch (e) {
      alert(`${t('account.brand.saveFailed')}${e instanceof Error ? e.message : e}`);
    }
  };

  const handleDelete = async () => {
    if (!confirm(t('account.brand.deleteConfirm'))) return;
    if (usingMock) {
      alert(t('account.brand.deleted'));
      return;
    }
    try {
      await deleteAccount.mutateAsync(account!.id);
      alert(t('account.brand.deleted'));
      accountsQuery.refetch();
    } catch (e) {
      alert(`${t('account.brand.deleteFailed')}${e instanceof Error ? e.message : e}`);
    }
  };

  const saving = updateAccount.isPending || upsertKnowledge.isPending;
  const running = runNowMutation.isPending;

  return (
    <div className="space-y-5">
      {/* ── 区块 1: 品牌信息 ── */}
      <Section title={t('account.brand.sections.brandInfo')}>
        <Grid>
          <Field label={t('account.brand.fields.target')} required>
            <Input value={target} onChange={setTarget} />
          </Field>
          <Field label={t('account.brand.fields.ticker')}>
            <Input value={ticker} onChange={setTicker} mono />
          </Field>
        </Grid>
        <Field label={t('account.brand.fields.aliases')}>
          <TagInput value={aliases} onChange={setAliases} placeholder={t('account.brand.fields.tagPlaceholder')} />
        </Field>
      </Section>

      {/* ── 区块 2: 监控配置 ── */}
      <Section title={t('account.brand.sections.monitoring')}>
        <Field label={t('account.brand.fields.intent')}>
          <textarea value={intent} onChange={(e) => setIntent(e.target.value)} rows={2}
            className="w-full px-3 py-2 rounded text-sm resize-y"
            style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
            placeholder={t('account.brand.fields.intentPlaceholder')} />
        </Field>
        <KeywordGroupsEditor value={keywordGroups} onChange={setKeywordGroups} />
        <Field label={t('account.brand.fields.excludes')}>
          <TagInput value={excludes} onChange={setExcludes} placeholder={t('account.brand.fields.tagPlaceholder')} />
        </Field>
      </Section>

      {/* ── 区块 3: 通知设置 ── */}
      <Section title={t('account.brand.sections.notify')}>
        <Field label={t('account.brand.fields.emails')}>
          <TagInput value={emails} onChange={setEmails} placeholder="ir@yourcompany.com" max={10} />
        </Field>
      </Section>

      {/* ── 区块 4: 知识库 ── */}
      <Section title={t('account.brand.sections.knowledge')}>
        <KnowledgeEditor knowledge={localKnowledge} onChange={setLocalKnowledge} />
      </Section>

      {/* ── 操作栏 ── */}
      <section className="rounded-2xl p-5 flex items-center justify-between flex-wrap gap-3" style={cardStyle}>
        <div className="flex gap-2">
          <button type="button" onClick={handleSave} disabled={saving}
            className="rounded-md px-4 py-2 text-sm font-semibold disabled:opacity-50"
            style={{ background: 'var(--bg-tertiary)', color: 'var(--text-primary)' }}>
            {saving ? '...' : t('account.brand.save')}
          </button>
          <button type="button" onClick={handleSaveAndRun} disabled={saving || running}
            className="btn-solid rounded-md px-4 py-2 text-sm font-semibold disabled:opacity-50">
            {(saving || running) ? '...' : t('account.brand.saveAndRun')}
          </button>
        </div>
        <button type="button" onClick={handleDelete}
          className="text-xs font-semibold" style={{ color: '#dc2626' }}>
          {t('account.brand.delete')}
        </button>
      </section>
    </div>
  );
}

/* ── 本地 UI 组件 ── */

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-2xl p-5 space-y-3" style={cardStyle}>
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
