// Brand & monitoring config tab -- placed in the account center.
//
// 4 sections:
//   1. Brand info: brand name, stock ticker, aliases
//   2. Monitoring config: monitoring focus, keyword groups, excluded terms
//   3. Notification settings: notification email
//   4. Knowledge base: brand tone, legal red lines, response playbooks
//
// When not configured, show OnboardingWizard (brand creation form).

import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { PageHeader } from '../../components/ui';

import { mockAccount, mockKeywordGroups, mockKnowledge } from '../../mocks/sentiment';
import { isMockMode } from '../../services/sentimentApi';
import {
  useSentimentAccounts, useUpdateAccount, useDeleteAccount, useRunNow,
  useKnowledge, useUpsertKnowledge, useSentimentPlatforms,
} from '../../hooks/useSentiment';
import type { KeywordGroup, SentimentPlatform } from '../../types/sentiment';

import { TagInput } from '../Dashboard/sentiment/components/TagInput';
import { KeywordGroupsEditor } from '../Dashboard/sentiment/components/KeywordGroupsEditor';
import { KnowledgeEditor } from '../Dashboard/sentiment/components/KnowledgeEditor';
import { OnboardingWizard } from '../Dashboard/sentiment/OnboardingWizard';
import { groupByMediaType } from '../../constants/sentimentPlatforms';
import { uiConfirm } from '../../components/ui/dialogs';
import { toast } from '../../components/ui/toast';
import { UiSelect } from '../../components/ui/Select';
import { useAgencyContext } from '../../contexts/AgencyContext';

const cardStyle: React.CSSProperties = {
  background: 'var(--bg-card)',
  border: '1px solid var(--border-color)',
};

export function BrandSettingsTab() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const usingMock = isMockMode();
  const { canWrite, managedAgency } = useAgencyContext();

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
  const [intents, setIntents] = useState<string[]>([]);
  const [keywordGroups, setKeywordGroups] = useState<KeywordGroup[]>([]);
  const [excludes, setExcludes] = useState<string[]>([]);
  const [mediaAllowlist, setMediaAllowlist] = useState<string[]>([]);
  const [emails, setEmails] = useState<string[]>([]);
  const [weixinAlbumUrls, setWeixinAlbumUrls] = useState<string[]>([]);
  const [newsnowSources, setNewsnowSources] = useState<string[]>([]);
  // Hour of daily monitoring (Beijing time). '' = unspecified, follows the system cadence.
  const [runHour, setRunHour] = useState<string>('');

  useEffect(() => {
    if (!account) return;
    setTarget(account.target);
    setTicker(account.ticker);
    setAliases(account.aliases ?? []);
    setIntents(account.intent ? account.intent.split(/[,，、;\s]+/).filter(Boolean) : []);
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
    setMediaAllowlist(account.media_allowlist ?? []);
    setEmails(account.notify_emails ?? []);
    setWeixinAlbumUrls(account.weixin_album_urls ?? []);
    setNewsnowSources(account.newsnow_sources ?? []);
    setRunHour(account.run_hour == null ? '' : String(account.run_hour));
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

  // No brand configured -> show the creation form
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
    if (!canWrite) {
      return <div className="rounded-xl p-5 text-sm text-muted" style={cardStyle}>
        当前企业由「{managedAgency?.name || '代理组织'}」管理，品牌与监控配置只读且尚未建立。
      </div>;
    }
    return (
      <OnboardingWizard
        onSubmit={() => {
          accountsQuery.refetch();
        }}
      />
    );
  }

  const buildPayload = () => ({
    // Stock ticker is optional: empty string = explicit clear (the backend treats only undefined as "leave this column alone")
    target: target.trim(), ticker: ticker.trim(),
    aliases, intent: intents.length > 0 ? intents.join('、') : undefined,
    keyword_groups: keywordGroups,
    excludes, media_allowlist: mediaAllowlist, notify_emails: emails,
    weixin_album_urls: weixinAlbumUrls,
    newsnow_sources: newsnowSources,
    // Don't send it if unspecified -- Update's run_hour is Optional, and passing undefined leaves the column untouched in the backend
    ...(runHour === '' ? {} : { run_hour: Number(runHour) }),
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
      toast.success(t('account.brand.saved'));
      return;
    }
    try {
      await updateAccount.mutateAsync({ id: account!.id, payload: buildPayload() });
      await persistKnowledge();
      toast.success(t('account.brand.saved'));
    } catch (e) {
      toast.error(`${t('account.brand.saveFailed')}${e instanceof Error ? e.message : e}`);
    }
  };

  const handleSaveAndRun = async () => {
    if (usingMock) {
      toast.success(`${t('account.brand.saved')}\n${t('account.brand.runNowQueued')}`);
      return;
    }
    try {
      await updateAccount.mutateAsync({ id: account!.id, payload: buildPayload() });
      await persistKnowledge();
      // If update detects keyword/intent changes it auto-enqueues the pipeline,
      // in which case /run-now returning 409 is normal -- just ignore it.
      try {
        await runNowMutation.mutateAsync(account!.id);
      } catch {
        // 409 = task already running/queued -- doesn't affect the save succeeding
      }
      toast.success(`${t('account.brand.saved')}\n${t('account.brand.runNowQueued')}`);
      navigate('/sentiment');
    } catch (e) {
      toast.error(`${t('account.brand.saveFailed')}${e instanceof Error ? e.message : e}`);
    }
  };

  const handleDelete = async () => {
    if (!(await uiConfirm(t('account.brand.deleteConfirm'), { danger: true }))) return;
    if (usingMock) {
      toast.success(t('account.brand.deleted'));
      return;
    }
    try {
      await deleteAccount.mutateAsync(account!.id);
      toast.success(t('account.brand.deleted'));
      accountsQuery.refetch();
    } catch (e) {
      toast.error(`${t('account.brand.deleteFailed')}${e instanceof Error ? e.message : e}`);
    }
  };

  const saving = updateAccount.isPending || upsertKnowledge.isPending;
  const running = runNowMutation.isPending;

  return (
    <div className="space-y-5">
      {/* This page is the sentiment monitoring settings page (the component name BrandSettingsTab is historical); the title follows the workbench convention */}
      <PageHeader title="舆情设置" hint="品牌名与别名 · 监测重点与关键词分组 · 通知邮箱 · 品牌语调与法务红线" />
      {/* ── Section 1: brand info ── */}
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

      {/* ── Section 2: monitoring config ── */}
      <Section title={t('account.brand.sections.monitoring')}>
        <Field label={t('account.brand.fields.intent')}>
          <TagInput value={intents} onChange={setIntents} placeholder={t('account.brand.fields.intentPlaceholder')}
            suggestions={['做空报告', '业绩传闻', '高管变动', '海外业务', '竞品动态', '政策监管', '诉讼纠纷', '股价异动']} />
        </Field>
        <KeywordGroupsEditor value={keywordGroups} onChange={setKeywordGroups} />
        <Field label={t('account.brand.fields.excludes')}>
          <TagInput value={excludes} onChange={setExcludes} placeholder={t('account.brand.fields.tagPlaceholder')} />
        </Field>
        <Field label={t('account.brand.fields.mediaAllowlist')}>
          <MediaAllowlistEditor value={mediaAllowlist} onChange={setMediaAllowlist} />
        </Field>
      </Section>

      {/* ── Section 2.5: extended data sources (optional, off by default) ── */}
      <Section title={t('account.brand.sections.extraSources', '扩展数据源(可选)')}>
        <Field label={t('account.brand.fields.weixinAlbumUrls', '微信公众号合集 URL')}>
          <TagInput
            value={weixinAlbumUrls}
            onChange={setWeixinAlbumUrls}
            placeholder="https://mp.weixin.qq.com/mp/appmsgalbum?__biz=...&album_id=..."
            max={20}
          />
          <p className="text-[11px] mt-1.5" style={{ color: 'var(--text-muted)' }}>
            周级别盯防几个高价值公众号(竞品官号 / 行业 KOL / 监管口径号)。
            粘贴合集 URL 即可,无 cookie。空 = 不启用。
          </p>
        </Field>
        <Field label={t('account.brand.fields.newsnowSources', 'NewsNow 热榜源订阅')}>
          <TagInput
            value={newsnowSources}
            onChange={setNewsnowSources}
            placeholder="weibo / zhihu / v2ex / hupu / toutiao / ithome / ..."
            suggestions={['weibo', 'zhihu', 'v2ex', 'hupu', 'toutiao', 'ithome', 'douban', 'juejin', '36kr']}
          />
          <p className="text-[11px] mt-1.5" style={{ color: 'var(--text-muted)' }}>
            从自托管 newsnow 容器拉热榜,本地按品牌名 + 别名 + 关键词过滤标题。
            空 = 不启用。
          </p>
        </Field>
      </Section>

      {/* ── Section 3: notification settings ── */}
      <Section title={t('account.brand.sections.notify')}>
        <Field label={t('account.brand.fields.emails')}>
          <TagInput value={emails} onChange={setEmails} placeholder="ir@yourcompany.com" max={10} />
        </Field>
        <Field label={t('account.brand.fields.runHour', '每天监测时间')}>
          <UiSelect
            value={runHour}
            onChange={setRunHour}
            className="rounded-md px-3 py-2 text-sm"
            style={{
              background: 'var(--bg-surface)',
              border: '1px solid var(--border-color)',
              color: 'var(--text-primary)',
            }}
            options={[
              { value: '', label: '不指定(按系统节奏)' },
              ...Array.from({ length: 24 }, (_, h) => ({
                value: String(h),
                label: `${String(h).padStart(2, '0')}:00`,
              })),
            ]}
          />
          <p className="text-[11px] mt-1.5" style={{ color: 'var(--text-muted)' }}>
            北京时间。舆情监测每天只扣一次积分,选在几点跑不影响费用。
          </p>
        </Field>
      </Section>

      {/* ── Section 4: knowledge base ── */}
      <Section title={t('account.brand.sections.knowledge')}>
        <KnowledgeEditor knowledge={localKnowledge} onChange={setLocalKnowledge} />
      </Section>

      {/* ── Action bar ── */}
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

/* ── Local UI components ── */

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

/* ── Media whitelist group editor -- modeled on WisersOne's "media type" axis (web media/Weibo/WeChat/
   APP/forums/short video/long video/...), each group a subheading row + chips + select all/clear.
   The platform catalog is fetched from useSentimentPlatforms(); the DB is the single source of truth.

   value semantics: `[]` = all platforms (canonical "all"), a non-empty array = only the checked subset.
   The collapsed state hides the chip area by default, showing only the "advanced selection" entry + a current state summary. */
function MediaAllowlistEditor({
  value, onChange,
}: { value: string[]; onChange: (next: string[]) => void }) {
  const { t, i18n } = useTranslation();
  const platformsQuery = useSentimentPlatforms();
  const platforms = platformsQuery.data ?? [];
  const groups = groupByMediaType(platforms);
  const allCodes = useMemo(() => platforms.map(p => p.code), [platforms]);
  const total = allCodes.length;

  const [expanded, setExpanded] = useState(false);
  // Each media type group collapses independently -- all collapsed by default, to avoid 70+ chips flooding out on expand.
  // Users click the small arrow to expand the corresponding group.
  const [openGroups, setOpenGroups] = useState<Set<string>>(new Set());
  const toggleGroup = (mt: string) => {
    setOpenGroups(prev => {
      const next = new Set(prev);
      if (next.has(mt)) next.delete(mt); else next.add(mt);
      return next;
    });
  };

  // value=[] is treated as "select all" -- all chips lit, while keeping backend semantics unchanged (empty array = all).
  const isAll = value.length === 0;
  const effective = isAll ? allCodes : value;
  const selectedSet = useMemo(() => new Set(effective), [effective]);

  // Write back: when all are checked, normalize back to `[]`, aligned with "empty = all".
  const commit = (next: string[]) => {
    if (next.length === total && allCodes.every(c => next.includes(c))) {
      onChange([]);
    } else {
      onChange(next);
    }
  };

  const toggle = (code: string) => {
    if (selectedSet.has(code)) commit(effective.filter(c => c !== code));
    else commit([...effective, code]);
  };
  const setGroup = (codes: string[], select: boolean) => {
    const set = new Set(effective);
    for (const c of codes) {
      if (select) set.add(c); else set.delete(c);
    }
    commit(Array.from(set));
  };
  // Prefer the localized name from the DB; if empty fall back to i18n sourceLabels; then to code.
  const platformLabel = (p: SentimentPlatform): string => {
    const fromDb = i18n.language === 'zh' ? p.name_zh : p.name_en;
    if (fromDb) return fromDb;
    const k = `dashboard.sentiment.articles.sourceLabels.${p.code}`;
    const v = t(k);
    return v === k ? p.code : v;
  };

  const status = isAll
    ? t('account.brand.fields.mediaAllowlistAllOn', { total })
    : t('account.brand.fields.mediaAllowlistPartialStatus', { count: value.length, total });

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3 flex-wrap">
        <button
          type="button"
          onClick={() => setExpanded(v => !v)}
          className="text-xs font-semibold inline-flex items-center gap-1 hover:text-primary"
          style={{ color: 'var(--text-secondary)' }}
        >
          <span style={{ display: 'inline-block', width: 10 }}>{expanded ? '▾' : '▸'}</span>
          {t('account.brand.fields.mediaAllowlistAdvanced')}
        </button>
        <span className="text-[11px] text-muted">{status}</span>
        {expanded && !isAll && (
          <button
            type="button"
            onClick={() => onChange([])}
            className="ml-auto text-[11px] text-muted hover:text-primary"
          >
            {t('account.brand.fields.mediaAllowlistReset')}
          </button>
        )}
      </div>

      {expanded && (
        <div className="space-y-2">
          <p className="text-xs text-muted">
            {t('account.brand.fields.mediaAllowlistHint')}
          </p>
          <div
            className="rounded-md divide-y"
            style={{
              background: 'var(--bg-surface)',
              border: '1px solid var(--border-color)',
              borderColor: 'var(--border-color)',
            }}
          >
            {groups.map(({ mediaType, items }) => {
              const codes = items.map(i => i.code);
              const selectedCount = codes.filter(c => selectedSet.has(c)).length;
              const allOn = selectedCount === codes.length;
              const open = openGroups.has(mediaType);
              return (
                <div key={mediaType}>
                  {/* Row header: click the caret/title to expand; the "select all/clear" button on the right is independent */}
                  <div className="flex items-center gap-2 px-3 py-2">
                    <button
                      type="button"
                      onClick={() => toggleGroup(mediaType)}
                      className="flex items-center gap-2 flex-1 min-w-0 text-left hover:text-primary"
                      style={{ color: 'var(--text-secondary)' }}
                    >
                      <span style={{ display: 'inline-block', width: 10, fontSize: 10 }}>
                        {open ? '▾' : '▸'}
                      </span>
                      <span className="text-xs font-semibold uppercase tracking-wider truncate">
                        {t(`dashboard.sentiment.articles.mediaTypes.${mediaType}`)}
                      </span>
                      <span className="text-[11px] font-normal normal-case text-muted">
                        {selectedCount > 0 && selectedCount < codes.length
                          ? `${selectedCount}/${codes.length}`
                          : codes.length}
                      </span>
                    </button>
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); setGroup(codes, !allOn); }}
                      className="text-[11px] text-muted hover:text-primary whitespace-nowrap"
                    >
                      {allOn
                        ? t('account.brand.fields.mediaAllowlistClearGroup')
                        : t('account.brand.fields.mediaAllowlistSelectGroup')}
                    </button>
                  </div>
                  {/* Platform list -- vertical list items, so more platforms don't blow out the width;
                     scrolls with a height limit beyond ~16 items; selected rows get a light blue background + ✓ mark. */}
                  {open && (
                    <div
                      className={`flex flex-col ${items.length > 16 ? 'max-h-72 overflow-y-auto' : ''}`}
                      style={{ borderTop: '1px solid var(--border-color)' }}
                    >
                      {items.map((p, idx) => {
                        const active = selectedSet.has(p.code);
                        const label = platformLabel(p);
                        return (
                          <button
                            key={p.code}
                            type="button"
                            onClick={() => toggle(p.code)}
                            title={label}
                            className="flex items-center justify-between text-xs px-3 py-1.5 transition-colors text-left"
                            style={{
                              background: active ? 'rgba(99,102,241,0.10)' : 'transparent',
                              color: active ? 'var(--accent-primary)' : 'var(--text-secondary)',
                              borderTop: idx === 0 ? 'none' : '1px solid var(--border-color)',
                            }}
                          >
                            <span className="truncate">{label}</span>
                            <span
                              className="ml-2 text-[11px]"
                              style={{ color: active ? 'var(--accent-primary)' : 'var(--text-muted)' }}
                            >
                              {active ? '✓' : ''}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
