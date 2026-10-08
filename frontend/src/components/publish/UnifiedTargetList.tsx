// Unified publish target list (F8 / dev step 8) -- a **single multi-select list** across platforms and channels.
//
// Ops only check "which accounts to publish to"; the channel is determined by each account's own channel, invisible to ops. Two sections:
//   Top: account table grouped by **platform** (channel only shown as a badge for troubleshooting, not for ops to choose by)
//   Bottom: publish results -- per-account success/failure table, failures show the reason + can be retried individually
//
// ## Currently enabled: server_pool backend channel + plugin channel
//
// When admins/ops open the backend channel, the UI reads backend-visible targets, currently showing only
// `server_pool` and the ops' local plugin accounts. Regular users only read the ops' local plugin accounts.
// Distribution still creates tasks per channel; the execution layer doesn't merge them.
//
// ## "Unified" means unified presentation and orchestration, not merged execution
//
// The three channels create tasks from fundamentally different sources; merging them would break the (doc_id, platform, account_db_id) idempotency
// unique key, showing up as the same draft published twice to the same account (irreversible):
//
//   server_pool  visible to backend -> POST /publish-dispatch/tasks   one batch, pulled by backend workers
//   aggregator   visible to backend -> POST /api/aitoearn/docs/{id}/publish
//   plugin       **invisible to backend** -> window.$syncer.addTask, executed in the ops' local browser
//
// When the plugin is offline the group shows "plugin not connected" rather than **disappearing** -- so ops know the path exists but is unavailable now.
//
// ## Two hard constraints at submit time
//
// 1. 🔴 Each of the three groups has its own try/catch; one group failing entirely doesn't affect the other two (F8 acceptance: 1 failure doesn't affect the other 4)
// 2. 🔴 The plugin group **must not run concurrently**: the platform cookie in one browser is a global login session, and concurrency would
//    publish articles to the wrong account. The right way is one addTask handing all checked currently-logged-in platforms to the plugin.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { marked } from 'marked';
import { contentApi, type AitoearnAccount, type ContentDoc } from '../../services/contentApi';
import { aiTelemetryApi } from '../../services/aiTelemetryApi';
import {
  publishPoolApi, type PublishChannel, type PublishTargetRow,
} from '../../services/publishPoolApi';
import {
  accountId as pluginAccountId, accountMedia, accountName, hasWechatSyncPlugin, waitForPlugin,
  detectPluginUpdate, isCurrentPluginAccount, pluginApi, publicUrlOf, readPluginAccounts,
  resolveCtripPoi, searchCtripPois, subscribePluginAccountsUpdate, terminal,
  syncCtripVerificationJobs,
  type CtripLocation, type CtripPoi, type PlatformRegion, type SyncerAccount,
  type SyncerAccountStatus, type PluginUpdateNotice,
} from '../../services/wechatSyncPlugin';
import { uiAlert, uiConfirm } from '../ui/dialogs';
import type { PublishPanelDoc } from './publishShared';
import { absolutizeHtmlImageUrls, absolutizeMarkdownImageUrls } from './wechatSyncContent';

// ── Channel badge ─────────────────────────────────────────────────────
// The badge is a **troubleshooting clue**, not a selection basis -- so it's small, gray and unobtrusive. Ops should only look at platform and account.
const CHANNEL_META: Record<PublishChannel, { label: string; color: string; hint: string }> = {
  server_pool: { label: '自建池', color: '#3b82f6', hint: '我们自己的号,后端 worker 无人值守发布' },
  aggregator: { label: '聚合', color: '#8b5cf6', hint: 'Aitoearn 侧授权,由它的服务端发布' },
  plugin: { label: '插件', color: '#f59e0b', hint: '运营本机浏览器插件发布,需要浏览器开着' },
};

// In-site install guide page (pages/PluginInstall.tsx). This used to be an example.com placeholder,
// rendered directly to users in the three places below -- clicking it gave a 404.
const WECHATSYNC_INSTALL_URL = '/plugin';
const CTRIP_RECENT_POI_KEY = 'ctripRecentPoi';
const LOGIN_ONLY_LABEL_TYPES = new Set(['douyin', 'xiaohongshu']);
const OVERSEAS_PLUGIN_TYPES = new Set(['x', 'twitter', 'reddit', 'medium', 'substack']);
const AITOEARN_PLATFORM_LABELS: Record<string, string> = {
  facebook: 'Facebook',
  instagram: 'Instagram',
  threads: 'Threads',
  linkedin: 'LinkedIn',
  tiktok: 'TikTok',
};

function pluginAccountLabel(acc: SyncerAccount): string {
  const media = accountMedia(acc);
  if (media) return media;
  return acc.isAuthenticated !== false && LOGIN_ONLY_LABEL_TYPES.has(acc.type) ? '已登录' : acc.type;
}

function pluginRegion(acc: SyncerAccount): PlatformRegion {
  return acc.region
    || (OVERSEAS_PLUGIN_TYPES.has(acc.type.toLowerCase()) ? 'overseas' : 'domestic');
}

function loadRecentCtripPoi(): CtripPoi | null {
  try {
    const value = JSON.parse(localStorage.getItem(CTRIP_RECENT_POI_KEY) || 'null') as Partial<CtripPoi> | null;
    return value && (typeof value.poiId === 'string' || typeof value.poiId === 'number')
      && typeof value.poiName === 'string' ? value as CtripPoi : null;
  } catch {
    return null;
  }
}

function ctripPoiDescription(poi: CtripPoi): string {
  const address = poi.poiAddressInfo;
  return [address?.typeName, address?.cityName, address?.address].filter(Boolean).join(' · ');
}

/** One row in the list -- the normalized shape across the three sources. */
type Row = {
  /** React key + key in the checked set. Unique across channels (channel prefix prevents collisions). */
  key: string;
  channel: PublishChannel;
  region: PlatformRegion;
  /** Display platform name used for grouping */
  platformName: string;
  accountLabel: string;
  /** Status phrase (shows remaining quota when publishable now, explains the queue reason when waiting). */
  statusText: string;
  /** Whether it can be submitted; may still be true when server_pool is temporarily unable to execute. */
  selectable: boolean;
  blockReason: string | null;
  remainingToday: number | null;
  /** Reason for content-side rejection (unrelated to account availability; penalizes content, not the account) */
  rejectReason: string | null;
  warnings: string[];
  /** Only for server_pool / aggregator */
  target?: PublishTargetRow;
  aitoearnAccount?: AitoearnAccount;
  /** Placeholder row for Aitoearn platforms not yet authorized; clicking starts authorization. */
  aitoearnAuthPlatform?: string;
  /** Only for plugin */
  pluginAccount?: SyncerAccount;
};

type OutcomeState = 'ok' | 'fail' | 'skip' | 'running';

type Outcome = {
  key: string;
  label: string;
  channel: PublishChannel;
  state: OutcomeState;
  detail: string;
};

type ActiveChannel = 'plugin' | 'server_pool';

export function UnifiedTargetList({
  doc, token, onPublished, scheduleMode = false, allowBackendChannels = false,
  verificationOwnerKey,
}: {
  doc: PublishPanelDoc;
  token: string;
  onPublished: () => void | Promise<void>;
  /** Only admin/operator open server_pool; regular users only see WechatSync plugin accounts. */
  allowBackendChannels?: boolean;
  verificationOwnerKey?: string;
  /**
   * Scheduling mode (F9 / step 9). This step only puts the plugin's **disabled state** under scheduling into the UI:
   * scheduled publishing depends on the ops' local browser being open at that moment, which the system can't guarantee.
   */
  scheduleMode?: boolean;
}) {
  const [targets, setTargets] = useState<PublishTargetRow[]>([]);
  const [aitoearnAccounts, setAitoearnAccounts] = useState<AitoearnAccount[]>([]);
  const [aitoearnBindings, setAitoearnBindings] = useState<AitoearnAccount[]>([]);
  const [aitoearnAuthPlatforms, setAitoearnAuthPlatforms] = useState<string[]>([]);
  const [authenticatingPlatform, setAuthenticatingPlatform] = useState<string | null>(null);
  const [failedAuthPlatforms, setFailedAuthPlatforms] = useState<Set<string>>(new Set());
  const [pluginAccounts, setPluginAccounts] = useState<SyncerAccount[]>([]);
  const [pluginUpdate, setPluginUpdate] = useState<PluginUpdateNotice | null>(null);
  // Reason the plugin is offline. null = online and accounts were read. Deliberately separate from err: plugin not installed is **normal**,
  // not an error -- mixing them would make ops think the whole list is broken.
  const [pluginErr, setPluginErr] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [ctripPoi, setCtripPoi] = useState<CtripPoi | null>(null);
  const [ctripPoiSource, setCtripPoiSource] = useState<'auto' | 'manual' | null>(null);
  const [ctripLocation, setCtripLocation] = useState<CtripLocation | null>(null);
  const [ctripTopic, setCtripTopic] = useState('');
  const [ctripKeyword, setCtripKeyword] = useState('');
  const [ctripPoiResults, setCtripPoiResults] = useState<CtripPoi[]>([]);
  const [ctripPoiLoading, setCtripPoiLoading] = useState(false);
  const [ctripPoiError, setCtripPoiError] = useState<string | null>(null);
  const [outcomes, setOutcomes] = useState<Outcome[]>([]);
  const [busy, setBusy] = useState(false);
  const [busyStage, setBusyStage] = useState<'preflight' | 'publish' | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [pluginProgress, setPluginProgress] = useState<string | null>(null);
  const [activeChannel, setActiveChannel] = useState<ActiveChannel>('plugin');
  const [activePluginRegion, setActivePluginRegion] = useState<PlatformRegion>('domestic');
  const [expandedPlatforms, setExpandedPlatforms] = useState<Set<string>>(new Set());
  const finalizedRef = useRef(false);
  const pluginInstallPromptedRef = useRef(false);
  const aitoearnAuthTimerRef = useRef<number | null>(null);
  const ctripAutoMatchRef = useRef(0);
  const ctripSearchRef = useRef(0);

  const showPluginInstallPrompt = useCallback(async () => {
    const go = await uiConfirm(
      <div className="space-y-2">
        <p>用自己浏览器里已登录的账号发布，需要先安装发布插件。</p>
        <p style={{ color: 'var(--text-secondary)', fontSize: 13 }}>
          安装引导页会分渠道给出步骤，并当场检测是否装好。
        </p>
      </div>,
      { title: '需要安装发布插件', okText: '去安装', cancelText: '稍后' },
    );
    if (go) window.open(WECHATSYNC_INSTALL_URL, '_blank', 'noopener,noreferrer');
  }, []);

  const loadTargets = useCallback(async (forceRefresh = false) => {
    setLoading(true);
    setErr(null);
    try {
      const [accounts, bindings, status, pool] = await Promise.allSettled([
        contentApi.aitoearnAccounts(token, forceRefresh),
        contentApi.aitoearnBindings(token),
        contentApi.aitoearnStatus(token),
        allowBackendChannels ? publishPoolApi.targets(doc.id, token) : Promise.resolve({ targets: [] }),
      ]);
      setAitoearnAccounts(accounts.status === 'fulfilled' ? accounts.value.accounts : []);
      setAitoearnBindings(bindings.status === 'fulfilled' ? bindings.value.accounts : []);
      setAitoearnAuthPlatforms(status.status === 'fulfilled'
        ? (status.value.auth_platforms || []).filter(platform => platform !== 'twitter') : []);
      setTargets(pool.status === 'fulfilled'
        ? pool.value.targets.filter(t => t.channel === 'server_pool') : []);
      const failures = [accounts, bindings, status, pool]
        .filter((result): result is PromiseRejectedResult => result.status === 'rejected')
        .map(result => result.reason instanceof Error ? result.reason.message : String(result.reason));
      if (failures.length > 0) setErr(failures.join('；'));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [allowBackendChannels, doc.id, token]);

  const loadPlugin = useCallback(async (forceRefresh = false) => {
    try {
      const rows = (await readPluginAccounts(forceRefresh)).filter(isCurrentPluginAccount);
      setPluginAccounts(rows);
      setPluginErr(rows.length === 0
        ? '插件已连接,但里面没有已登录的平台账号 —— 请先在目标平台登录后刷新。'
        : null);
      void detectPluginUpdate().then(setPluginUpdate).catch(() => setPluginUpdate(null));
    } catch (e) {
      setPluginAccounts([]);
      setPluginUpdate(null);
      setPluginErr(e instanceof Error ? e.message : String(e));
      if (!hasWechatSyncPlugin() && !pluginInstallPromptedRef.current) {
        pluginInstallPromptedRef.current = true;
        void showPluginInstallPrompt();
      }
    }
  }, [showPluginInstallPrompt]);

  useEffect(() => { void loadTargets(); }, [loadTargets]);
  useEffect(() => {
    let cancelled = false;
    setCtripTopic('');
    void aiTelemetryApi.getTopic(doc.topic_id, token)
      .then(topic => {
        if (cancelled) return;
        setCtripTopic(topic.profile?.profile_name?.trim() || '');
        const saved = topic.profile?.ctrip_location;
        const name = saved?.name?.trim() || '';
        if (name) {
          setCtripLocation({
            name,
            ...(saved?.city?.trim() ? { city: saved.city.trim() } : {}),
            ...(saved?.address?.trim() ? { address: saved.address.trim() } : {}),
          });
        } else {
          setCtripLocation(null);
          const recent = loadRecentCtripPoi();
          if (recent) {
            setCtripPoi(current => current || recent);
            setCtripPoiSource(current => current || 'manual');
          }
        }
      })
      .catch(() => {
        const recent = loadRecentCtripPoi();
        if (!cancelled && recent) {
          setCtripPoi(current => current || recent);
          setCtripPoiSource(current => current || 'manual');
        }
      });
    return () => { cancelled = true; };
  }, [doc.topic_id, token]);
  // Wait for the plugin to inject window.$syncer before asking it for accounts. The extension injects asynchronously (the content script dynamically inserts
  // inject-api.js), so asking immediately always fails -- but **a fixed delay can't be used** here: on slow machines or plugin cold start
  // the delay isn't enough, and users who already installed the plugin would get "not installed", taking the product as broken.
  // waitForPlugin polls until it appears, and only judges it not installed at the limit.
  useEffect(() => () => {
    if (aitoearnAuthTimerRef.current !== null) window.clearTimeout(aitoearnAuthTimerRef.current);
  }, []);
  useEffect(() => {
    let cancelled = false;
    void waitForPlugin().then(() => { if (!cancelled) void loadPlugin(); });
    return () => { cancelled = true; };
  }, [loadPlugin]);
  useEffect(() => subscribePluginAccountsUpdate(accounts => {
    const rows = accounts.filter(isCurrentPluginAccount);
    setPluginAccounts(rows);
    setPluginErr(rows.length === 0
      ? '插件已连接,但里面没有已登录的平台账号 —— 请先在目标平台登录后刷新。'
      : null);
  }), []);

  const reloadAll = useCallback(async () => {
    await Promise.all([loadTargets(true), loadPlugin(true)]);
  }, [loadTargets, loadPlugin]);

  const pollAitoearnAuth = async (attemptId: string, platform: string, popup: Window) => {
    try {
      const result = await contentApi.aitoearnAuthStatus(attemptId, token);
      if (result.status === 'completed') {
        popup.close();
        setFailedAuthPlatforms(prev => {
          const next = new Set(prev);
          next.delete(platform);
          return next;
        });
        setAuthenticatingPlatform(null);
        await loadTargets(true);
        return;
      }
      if (result.status === 'failed') {
        popup.close();
        setFailedAuthPlatforms(prev => new Set(prev).add(platform));
        setErr(result.error || `${AITOEARN_PLATFORM_LABELS[platform] || platform} 授权失败`);
        setAuthenticatingPlatform(null);
        return;
      }
      if (result.requires_selection && result.selectable_accounts?.length) {
        await contentApi.aitoearnSubmitAuthSelections(
          attemptId,
          result.selectable_accounts.map(account => ({
            platformUid: account.platformUid,
            account: account.account,
          })),
          token,
        );
      }
      if (popup.closed && !result.requires_selection) {
        setFailedAuthPlatforms(prev => new Set(prev).add(platform));
        setErr(`${AITOEARN_PLATFORM_LABELS[platform] || platform} 授权失败，请重新尝试`);
        setAuthenticatingPlatform(null);
        return;
      }
      aitoearnAuthTimerRef.current = window.setTimeout(
        () => void pollAitoearnAuth(attemptId, platform, popup), 2000,
      );
    } catch (e) {
      setFailedAuthPlatforms(prev => new Set(prev).add(platform));
      setErr(e instanceof Error ? e.message : String(e));
      setAuthenticatingPlatform(null);
    }
  };

  const startAitoearnAuth = async (platform: string) => {
    if (authenticatingPlatform) return;
    const popup = window.open('', '_blank', 'width=720,height=760');
    if (!popup) {
      setErr('浏览器拦截了授权窗口，请允许弹窗后重试。');
      return;
    }
    popup.opener = null;
    setFailedAuthPlatforms(prev => {
      const next = new Set(prev);
      next.delete(platform);
      return next;
    });
    setAuthenticatingPlatform(platform);
    setErr(null);
    try {
      const result = await contentApi.aitoearnStartAuth(platform, token);
      popup.location.href = result.url;
      await pollAitoearnAuth(result.attempt_id, platform, popup);
    } catch (e) {
      popup.close();
      setFailedAuthPlatforms(prev => new Set(prev).add(platform));
      setErr(e instanceof Error ? e.message : String(e));
      setAuthenticatingPlatform(null);
    }
  };

  const unbindAitoearn = async (account: AitoearnAccount) => {
    if (!account.binding_id || busy) return;
    const confirmed = await uiConfirm(
      `确认解绑“${account.nickname || account.platform}”？解绑后将无法继续发布。`,
      { danger: true },
    );
    if (!confirmed) return;
    setBusy(true);
    setErr(null);
    try {
      await contentApi.aitoearnUnbind(account.binding_id, token);
      setSelected(prev => {
        const next = new Set(prev);
        next.delete(`aggregator:${account.account_id}`);
        return next;
      });
      await loadTargets(true);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const switchChannel = (channel: ActiveChannel) => {
    setActiveChannel(channel);
    setSelected(new Set());
    setOutcomes([]);
    setPluginProgress(null);
  };

  // ── Normalize the three sources ───────────────────────────────────────────────────
  const rows = useMemo<Row[]>(() => {
    const out: Row[] = [];
    for (const t of targets) {
      // 🔴 Content rejected -> not checkable. Previously it created tasks even when checked, only judging content_rejected at claim time --
      // a wasted round trip, while this check costs nothing here.
      const reject = t.preflight.reject_reason;
      const blocked = reject ? `内容不满足平台要求:${reject}` : t.queue_block_reason;
      const waiting = t.channel === 'server_pool' && t.queueable && !t.schedulable;
      out.push({
        key: `${t.channel}:${t.account_db_id}`,
        channel: t.channel,
        region: 'domestic',
        platformName: t.platform_name || t.platform,
        accountLabel: t.account_handle,
        statusText: waiting
          ? `可排队 · ${t.unschedulable_reason || '等待调度'}`
          : (t.remaining_today === null ? '不限量' : `今日余 ${t.remaining_today}`),
        selectable: t.queueable && !reject,
        blockReason: t.queueable && !reject ? null : (blocked || '不可排队'),
        remainingToday: t.remaining_today,
        rejectReason: reject,
        warnings: t.preflight.warnings,
        target: t,
      });
    }
    const aitoearnByPlatform = new Map<string, AitoearnAccount>();
    for (const acc of aitoearnAccounts) {
      if (acc.account_type === 'twitter') continue;
      const current = aitoearnByPlatform.get(acc.account_type);
      if (!current || (acc.binding_id || 0) > (current.binding_id || 0)) {
        aitoearnByPlatform.set(acc.account_type, acc);
      }
    }
    const aitoearnPlatforms = new Set([
      ...aitoearnAuthPlatforms,
      ...aitoearnByPlatform.keys(),
    ]);
    for (const accountType of aitoearnPlatforms) {
      const acc = aitoearnByPlatform.get(accountType);
      if (!acc) {
        out.push({
          key: `aggregator:auth:${accountType}`,
          channel: 'aggregator',
          region: 'overseas',
          platformName: AITOEARN_PLATFORM_LABELS[accountType] || accountType,
          accountLabel: '未授权账号',
          statusText: '未登录',
          selectable: false,
          blockReason: '请先授权登录',
          remainingToday: null,
          rejectReason: null,
          warnings: [],
          aitoearnAuthPlatform: accountType,
        });
        continue;
      }
      out.push({
        key: `aggregator:${acc.account_id}`,
        channel: 'aggregator',
        region: 'overseas',
        platformName: acc.platform,
        accountLabel: acc.nickname || '未命名账号',
        statusText: '已登录',
        selectable: true,
        blockReason: null,
        remainingToday: null,
        rejectReason: null,
        warnings: [],
        aitoearnAccount: acc,
      });
    }
    for (const acc of pluginAccounts) {
      // Plugin accounts have **no** backend pre-check: the backend doesn't know which platform account the plugin will publish to, nor that
      // platform's quota config. So we don't falsely claim "pre-check passed" here; statusText only describes the login session.
      const blocked = acc.isAuthenticated === false
        ? '插件识别为未登录，请先在目标平台登录后刷新账号'
        : scheduleMode
        ? '排期发布不支持插件通道 —— 它要求运营本机浏览器在发布那一刻开着,系统无法保证'
        : null;
      out.push({
        key: `plugin:${pluginAccountId(acc)}`,
        channel: 'plugin',
        region: pluginRegion(acc),
        platformName: accountName(acc),
        accountLabel: pluginAccountLabel(acc),
        statusText: acc.isAuthenticated === false ? '未登录' : '已登录',
        selectable: !blocked,
        blockReason: blocked,
        remainingToday: null,
        rejectReason: null,
        warnings: [],
        pluginAccount: acc,
      });
    }
    return out;
  }, [targets, aitoearnAccounts, aitoearnAuthPlatforms, pluginAccounts, scheduleMode]);

  const visibleRows = useMemo(() => {
    return activeChannel === 'plugin'
      ? rows.filter(r => (r.channel === 'plugin' || r.channel === 'aggregator')
        && r.region === activePluginRegion)
      : rows.filter(r => r.channel === 'server_pool');
  }, [activeChannel, activePluginRegion, rows]);

  const unlistedAitoearnBindings = useMemo(() => {
    const listed = new Set(rows.flatMap(r => r.aitoearnAccount?.binding_id ?? []));
    return aitoearnBindings.filter(account =>
      account.binding_id !== undefined && !listed.has(account.binding_id));
  }, [aitoearnBindings, rows]);

  // Grouped by **platform** within the current channel. Admins pick a channel first; for server_pool, click a platform to expand accounts.
  const groups = useMemo(() => {
    const m = new Map<string, Row[]>();
    for (const r of visibleRows) {
      const list = m.get(r.platformName);
      if (list) list.push(r); else m.set(r.platformName, [r]);
    }
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0], 'zh'));
  }, [visibleRows]);

  const byKey = useMemo(() => new Map(rows.map(r => [r.key, r])), [rows]);
  const visibleKeySet = useMemo(() => new Set(visibleRows.map(r => r.key)), [visibleRows]);
  // Whether multiple channels really coexist in the current tab -- decides whether to show channel badges.
  const multiChannel = useMemo(
    () => new Set(visibleRows.map(r => r.channel)).size > 1, [visibleRows]);
  const picked = useMemo(
    () => [...selected]
      .filter(k => visibleKeySet.has(k))
      .map(k => byKey.get(k))
      .filter((r): r is Row => !!r),
    [selected, visibleKeySet, byKey],
  );
  const ctripSelected = useMemo(
    () => picked.some(row => row.channel === 'plugin' && row.pluginAccount?.type === 'ctrip'),
    [picked],
  );
  const missingCtripLocation = ctripSelected && !ctripPoi;

  // The checked set must converge as data changes -- if an account becomes unqueueable after refresh, it can't stay checked.
  useEffect(() => {
    setSelected(prev => {
      const next = new Set([...prev].filter(k => byKey.get(k)?.selectable));
      return next.size === prev.size ? prev : next;
    });
  }, [byKey]);

  useEffect(() => {
    if (!ctripSelected || !ctripLocation?.name.trim() || ctripPoi || ctripKeyword.trim()) return;
    const requestId = ++ctripAutoMatchRef.current;
    setCtripPoiLoading(true);
    setCtripPoiError(null);
    void resolveCtripPoi(ctripLocation)
      .then(poi => {
        if (ctripAutoMatchRef.current !== requestId) return;
        setCtripPoi(poi);
        setCtripPoiSource('auto');
        setCtripPoiResults([]);
      })
      .catch(error => {
        if (ctripAutoMatchRef.current === requestId) {
          setCtripPoiError(error instanceof Error ? error.message : String(error));
        }
      })
      .finally(() => {
        if (ctripAutoMatchRef.current === requestId) setCtripPoiLoading(false);
      });
    return () => {
      if (ctripAutoMatchRef.current === requestId) ctripAutoMatchRef.current += 1;
    };
  }, [ctripKeyword, ctripLocation, ctripPoi, ctripSelected]);

  useEffect(() => {
    const keyword = ctripKeyword.trim();
    if (!ctripSelected || ctripPoi || !keyword) {
      ctripSearchRef.current += 1;
      return;
    }
    const requestId = ++ctripSearchRef.current;
    const timer = window.setTimeout(() => {
      setCtripPoiLoading(true);
      setCtripPoiError(null);
      void searchCtripPois(keyword)
        .then(results => {
          if (ctripSearchRef.current === requestId) setCtripPoiResults(results);
        })
        .catch(error => {
          if (ctripSearchRef.current === requestId) {
            setCtripPoiResults([]);
            setCtripPoiError(error instanceof Error ? error.message : String(error));
          }
        })
        .finally(() => {
          if (ctripSearchRef.current === requestId) setCtripPoiLoading(false);
        });
    }, 400);
    return () => {
      window.clearTimeout(timer);
      if (ctripSearchRef.current === requestId) ctripSearchRef.current += 1;
    };
  }, [ctripKeyword, ctripPoi, ctripSelected]);

  const togglePlatform = (platformName: string) => {
    const key = `${activeChannel}:${platformName}`;
    setExpandedPlatforms(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  };

  const toggle = (key: string) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
    setOutcomes([]);
  };

  const setPlatformSelected = (list: Row[], checked: boolean) => {
    setSelected(prev => {
      const next = new Set(prev);
      for (const r of list) {
        if (!r.selectable) continue;
        if (checked) next.add(r.key); else next.delete(r.key);
      }
      return next;
    });
    setOutcomes([]);
  };

  // ── Three-way dispatch ───────────────────────────────────────────────────
  // 🔴 Each group has its own try/catch. When one group fails entirely (API 500 / plugin dropped), the other two must still finish --
  // this is where F8's "1 failure doesn't affect the other 4" is implemented.
  const runPublish = async () => {
    if (picked.length === 0) return;
    if (missingCtripLocation) {
      setErr('请先在品牌资料填写地点，或手动选择携程地点。');
      return;
    }
    setBusy(true);
    setBusyStage('preflight');
    setErr(null);
    setOutcomes([]);
    setPluginProgress(null);
    const collected: Outcome[] = [];
    // Flush existing results as each group finishes, **without waiting for the slowest group**. The plugin group can take up to 10 minutes
    // (waiting for the ops' local browser to switch accounts and publish one by one), while server_pool usually creates its tasks within 1 second --
    // holding everything to display at the end would make ops think the 1-second group was stuck too.
    const flush = () => setOutcomes([...collected]);

    const sp = picked.filter(r => r.channel === 'server_pool');
    const agg = picked.filter(r => r.channel === 'aggregator');
    const plg = picked.filter(r => r.channel === 'plugin');

    let publishableAgg = agg;
    let preflightToken: string | undefined;
    if (agg.length > 0) {
      try {
        const preview = await contentApi.aitoearnPreview(
          doc.id, agg.map(r => r.aitoearnAccount!.account_id), token,
        );
        preflightToken = preview.preflight_token;
        const byAccount = new Map(preview.rows.map(row => [row.account_id, row]));
        const blockedRows = preview.rows.filter(row => row.status === 'blocked');
        publishableAgg = agg.filter(row => {
          const check = byAccount.get(row.aitoearnAccount!.account_id);
          if (!check) return false;
          if (check.status !== 'blocked' && check.ok !== false) return true;
          collected.push({
            key: row.key, label: rowLabel(row), channel: row.channel, state: 'fail',
            detail: check?.reject_reason || '图片预检未通过，未发布',
          });
          return false;
        });
        for (const row of agg) {
          if (byAccount.has(row.aitoearnAccount!.account_id)) continue;
          collected.push({
            key: row.key, label: rowLabel(row), channel: row.channel, state: 'fail',
            detail: '后端没有返回这个账号的预检结果，未发布',
          });
        }

        if (blockedRows.length > 0) {
          const details = (
            <div className="space-y-2 text-left">
              {blockedRows.map(row => (
                <div key={row.account_id}>
                  <p className="font-medium">
                    不可发布：{row.platform} / {row.nickname || '未命名账号'}
                  </p>
                  <ul className="list-disc pl-5 text-sm" style={{ color: 'var(--text-secondary)' }}>
                    {row.issues.map((issue, index) => (
                      <li key={`${issue.code}-${issue.media_index ?? 0}-${index}`}>{issue.message}</li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          );
          const canContinue = publishableAgg.length + sp.length + plg.length > 0;
          if (!canContinue) {
            await uiAlert(details, { title: '图片预检未通过', okText: '关闭' });
            setOutcomes([...collected]);
            setBusy(false);
            setBusyStage(null);
            return;
          }
          const confirmed = await uiConfirm(details, {
            title: '发布预检',
            okText: publishableAgg.length < agg.length ? '发布可用账号' : '继续发布',
            cancelText: '返回修改',
          });
          if (!confirmed) {
            setOutcomes([...collected]);
            setBusy(false);
            setBusyStage(null);
            return;
          }
        }
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        for (const row of agg) {
          collected.push({ key: row.key, label: rowLabel(row), channel: row.channel,
                           state: 'fail', detail: `图片预检失败:${msg}` });
        }
        publishableAgg = [];
        await uiAlert(`聚合账号图片预检失败，已取消这些账号的发布：${msg}`, {
          title: '预检失败',
        });
      }
    }
    setBusyStage('publish');

    // The three groups start **in parallel** (no inter-group dependencies), but each guards its own failures internally.
    // try/finally is the last safeguard: all three dispatch* swallow their own exceptions, but if someone later introduces
    // a stray throw path, the "Publish" button must never stay disabled forever (then ops couldn't even click retry).
    try {
      await Promise.all([
        dispatchServerPool(sp, collected).then(flush),
        dispatchAggregator(publishableAgg, collected, preflightToken).then(flush),
        dispatchPlugin(plg, collected).then(flush),
      ]);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      flush();
      // Remove successful ones from the checked set; keep failures so ops can retry directly.
      const okKeys = new Set(collected.filter(o => o.state === 'ok').map(o => o.key));
      setSelected(prev => new Set([...prev].filter(k => !okKeys.has(k))));
      // After the retry API returns, refresh placement progress first, then end the busy state, so a stale failed state doesn't linger for a frame.
      await Promise.all([loadTargets(), Promise.resolve(onPublished())]);
      setBusy(false);
      setBusyStage(null);
      setPluginProgress(null);
    }
  };

  const dispatchServerPool = async (group: Row[], collected: Outcome[]) => {
    if (group.length === 0) return;
    try {
      const out = await publishPoolApi.createDispatchTasks(
        doc.id,
        group.map(r => r.target!.account_db_id),
        token,
      );
      const byAcct = new Map(out.results.map(r => [r.account_db_id, r]));
      for (const r of group) {
        const res = byAcct.get(r.target!.account_db_id);
        if (!res) {
          collected.push({ key: r.key, label: rowLabel(r), channel: r.channel,
                           state: 'fail', detail: '后端没有返回这个账号的结果' });
          continue;
        }
        collected.push({
          key: r.key, label: rowLabel(r), channel: r.channel,
          state: res.action === 'skip' ? 'skip' : 'ok',
          detail: res.detail || (res.action === 'created'
            ? `已入队(任务 #${res.task_id})`
            : `复用已有任务 #${res.task_id}`),
        });
      }
    } catch (e) {
      // Whole group failed -- mark it row by row rather than showing only a top-level error: ops need to know none of these 3 accounts published.
      const msg = e instanceof Error ? e.message : String(e);
      for (const r of group) {
        collected.push({ key: r.key, label: rowLabel(r), channel: r.channel,
                         state: 'fail', detail: `自建池建任务失败:${msg}` });
      }
    }
  };

  const dispatchAggregator = async (
    group: Row[], collected: Outcome[], preflightToken?: string,
  ) => {
    if (group.length === 0) return;
    try {
      const out = await contentApi.aitoearnPublish(
        doc.id, group.map(r => r.aitoearnAccount!.account_id), token, [], preflightToken,
      );
      out.results.forEach((result, index) => {
        const row = group[index];
        if (!row) return;
        collected.push({
          key: row.key, label: rowLabel(row), channel: row.channel,
          state: result.action === 'skip' ? 'skip'
            : ['submitted'].includes(result.action) ? 'ok' : 'fail',
          detail: result.detail,
        });
      });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      for (const r of group) {
        collected.push({ key: r.key, label: rowLabel(r), channel: r.channel,
                         state: 'fail', detail: `聚合发布失败:${msg}` });
      }
    }
  };

  const dispatchPlugin = async (group: Row[], collected: Outcome[]) => {
    if (group.length === 0) return;
    const api = pluginApi();
    if (!api) {
      await showPluginInstallPrompt();
      for (const r of group) {
        collected.push({ key: r.key, label: rowLabel(r), channel: r.channel, state: 'fail',
                         detail: '插件已断开 —— 请重新启用插件并刷新页面' });
      }
      return;
    }
    finalizedRef.current = false;
    const accounts = group.map(r => r.pluginAccount!).filter(Boolean);
    const keys = new Set(accounts.map(pluginAccountId));
    try {
      setPluginProgress('正在准备插件发布任务…');
      const fullDoc: ContentDoc = await contentApi.getDoc(doc.id, token);
      const baseUrl = `${window.location.origin}/`;
      const markdown = absolutizeMarkdownImageUrls(fullDoc.body_markdown || '', baseUrl);
      const html = absolutizeHtmlImageUrls(String(await marked.parse(markdown)), baseUrl);

      // 🔴 One addTask hands all currently logged-in platforms to the plugin. Don't loop calling addTask here:
      // the platform cookie in one browser is a global login session, and concurrency would publish articles to the wrong account.
      await new Promise<void>(resolve => {
        const timeout = window.setTimeout(() => {
          if (finalizedRef.current) return;
          finalizedRef.current = true;
          for (const r of group) {
            collected.push({ key: r.key, label: rowLabel(r), channel: r.channel, state: 'fail',
                             detail: '插件发布等待超时 —— 请到目标平台确认是否已发出,'
                                     + '必要时手动回填链接(切勿直接重发)' });
          }
          resolve();
        }, 10 * 60 * 1000);

        const finish = async (statuses: SyncerAccountStatus[]) => {
          if (finalizedRef.current) return;
          finalizedRef.current = true;
          window.clearTimeout(timeout);
          let verificationDetails = new Map<string, string>();
          try {
            verificationDetails = await persistPluginResults(statuses, keys);
          } catch (e) {
            setErr(e instanceof Error ? e.message : String(e));
          }
          const byId = new Map(statuses.map(s => [pluginAccountId(s), s]));
          for (const r of group) {
            const st = byId.get(pluginAccountId(r.pluginAccount!));
            const savedAsDraft = st?.draftOnly === true || r.pluginAccount?.draftOnly === true;
            collected.push({
              key: r.key, label: rowLabel(r), channel: r.channel,
              state: st?.status === 'done' ? 'ok' : 'fail',
              detail: st?.status === 'done'
                ? verificationDetails.get(pluginAccountId(r.pluginAccount!)) || (savedAsDraft
                  ? (publicUrlOf(st) ? `已保存草稿：${publicUrlOf(st)}` : '已保存到平台草稿箱')
                  : (publicUrlOf(st) || '插件报告已发布(未拿到链接,需手工回填)'))
                : (st?.error || st?.msg || '插件未报告完成'),
            });
          }
          resolve();
        };

        api.addTask(
          {
            post: {
              title: fullDoc.title,
              content: html,
              markdown,
              ...(group.some(row => row.pluginAccount?.type === 'ctrip')
                ? {
                  ...(ctripTopic ? { topics: ctripTopic } : {}),
                  ...(ctripPoi ? { ctripPoi } : ctripLocation ? { location: ctripLocation } : {}),
                }
                : {}),
            },
            accounts,
          },
          task => {
            const statuses = task.accounts || [];
            const running = statuses.find(s => keys.has(pluginAccountId(s))
              && (s.status === 'pending' || s.status === 'uploading'));
            if (running) setPluginProgress(`插件正在发布 ${accountName(running)}…`);
            if (terminal(statuses, keys)) void finish(statuses);
          },
          () => {},
        );
      });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      if (!finalizedRef.current) {
        finalizedRef.current = true;
        for (const r of group) {
          collected.push({ key: r.key, label: rowLabel(r), channel: r.channel,
                           state: 'fail', detail: `插件通道发布失败:${msg}` });
        }
      }
    }
  };

  /** After the plugin publishes, persist the results as placement tasks + backfill URLs. Serial: one at a time, don't overwhelm the backend. */
  const persistPluginResults = async (statuses: SyncerAccountStatus[], keys: Set<string>) => {
    const verificationDetails = new Map<string, string>();
    // Draft links are not public article URLs and can't enter "published" tasks or the re-test loop.
    const okRows = statuses.filter(
      s => keys.has(pluginAccountId(s)) && s.status === 'done' && s.draftOnly !== true,
    );
    if (okRows.length === 0) return verificationDetails;
    const created = await contentApi.createPublishTasks(
      doc.id,
      okRows.map(acc => ({
        platform: accountName(acc),
        media_name: accountMedia(acc),
        channel_type: 'auto_account',
      })),
      token,
    );
    for (const acc of okRows) {
      const url = publicUrlOf(acc);
      if (!url) continue;
      const task = created.find(
        t => t.platform === accountName(acc) && t.media_name === accountMedia(acc));
      if (!task) continue;
      const platformCode = acc.type.toLowerCase();
      const externalPostId = acc.postId || (platformCode === 'ctrip'
        ? new URL(url).searchParams.get('articleId') || undefined
        : undefined);
      const verificationMethod = platformCode === 'ctrip' ? 'platform_api' : 'url';
      const submitted = await contentApi.submitPublishUrl(
        task.id,
        {
          url,
          verify_before_publish: !['weixin', 'wxgzh'].includes(platformCode),
          proof: {
            note: `WechatSync 插件发布:${acc.type}`,
            platform_code: platformCode,
            external_post_id: externalPostId,
            verification_method: verificationMethod,
            ...(platformCode === 'ctrip' ? { verification_executor: 'plugin' } : {}),
          },
        },
        token,
      );
      if (platformCode === 'ctrip' && externalPostId) {
        window.dispatchEvent(new Event('vigilath:ctrip-verification-sync-requested'));
        const verification = submitted.channel_meta?.url_verification;
        try {
          if (!verificationOwnerKey || !verification?.next_at) {
            throw new Error('缺少携程定时核验信息');
          }
          await syncCtripVerificationJobs([{
            ownerKey: verificationOwnerKey,
            taskId: submitted.id,
            articleId: externalPostId,
            candidateUrl: url,
            nextAt: verification.next_at,
            initialDelaySeconds: verification.initial_delay_seconds || 3600,
            confirmationDelaySeconds: verification.confirmation_delay_seconds || 86400,
            firstPublishedAt: verification.first_published_at,
          }]);
          const label = '携程已提交，将按计划自动查询审核状态';
          verificationDetails.set(pluginAccountId(acc), `${label} · ${url}`);
          setPluginProgress(label);
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error);
          verificationDetails.set(pluginAccountId(acc), `携程已提交，定时核验登记失败：${message}`);
          setErr(`携程已提交，但定时核验登记失败：${message}`);
        }
      }
    }
    return verificationDetails;
  };

  // ── Render ───────────────────────────────────────────────────────
  const selectableCount = visibleRows.filter(r => r.selectable).length;
  const pluginCount = rows.filter(r => r.channel === 'plugin' || r.channel === 'aggregator').length;
  const serverPoolCount = rows.filter(r => r.channel === 'server_pool').length;
  const domesticCount = rows.filter(r =>
    (r.channel === 'plugin' || r.channel === 'aggregator') && r.region === 'domestic').length;
  const overseasCount = rows.filter(r =>
    (r.channel === 'plugin' || r.channel === 'aggregator') && r.region === 'overseas').length;
  const showPluginPanel = activeChannel === 'plugin';
  const showServerPoolPanel = allowBackendChannels && activeChannel === 'server_pool';
  const pluginCapabilityGroups = [
    {
      title: '支持直发',
      rows: visibleRows.filter(r => r.channel === 'aggregator' || r.pluginAccount?.draftOnly !== true),
    },
    {
      title: '仅支持发布草稿',
      rows: visibleRows.filter(r => r.channel === 'plugin' && r.pluginAccount?.draftOnly === true),
    },
  ];

  return (
    <div className="space-y-2">
      {err && <div className="text-[11px]" style={{ color: '#ef4444' }}>{err}</div>}

      {allowBackendChannels && (
        <div className="inline-flex rounded-lg p-1 gap-1"
             style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)' }}>
          <button type="button" onClick={() => switchChannel('plugin')}
                  className="text-xs px-3 py-1.5 rounded-md transition-colors"
                  style={{
                    background: activeChannel === 'plugin' ? 'var(--bg-card)' : 'transparent',
                    color: activeChannel === 'plugin' ? 'var(--text-primary)' : 'var(--text-muted)',
                  }}>
            插件{pluginCount > 0 ? ` (${pluginCount})` : ''}
          </button>
          <button type="button" onClick={() => switchChannel('server_pool')}
                  className="text-xs px-3 py-1.5 rounded-md transition-colors"
                  style={{
                    background: activeChannel === 'server_pool' ? 'var(--bg-card)' : 'transparent',
                    color: activeChannel === 'server_pool' ? 'var(--text-primary)' : 'var(--text-muted)',
                    boxShadow: activeChannel === 'server_pool' ? '0 1px 2px rgba(15,23,42,0.08)' : 'none',
                  }}>
            账号池{serverPoolCount > 0 ? ` (${serverPoolCount})` : ''}
          </button>
        </div>
      )}

      {showPluginPanel && (
        <div className="inline-flex rounded-lg p-1 gap-1"
             style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)' }}>
          {([
            ['domestic', '国内', domesticCount],
            ['overseas', '国外', overseasCount],
          ] as const).map(([region, label, count]) => (
            <button key={region} type="button"
                    onClick={() => {
                      setActivePluginRegion(region);
                      setSelected(new Set());
                      setOutcomes([]);
                      setPluginProgress(null);
                    }}
                    className="text-xs px-3 py-1.5 rounded-md transition-colors"
                    style={{
                      background: activePluginRegion === region ? 'var(--bg-card)' : 'transparent',
                      color: activePluginRegion === region ? 'var(--text-primary)' : 'var(--text-muted)',
                    }}>
              {label} ({count})
            </button>
          ))}
        </div>
      )}

      <div className="flex items-center justify-between gap-2">
        <p className="text-[11px] text-muted">
          {showServerPoolPanel
            ? '选择 server_pool 平台并展开账号。忙碌、冷却或限流账号也可继续排队。'
            : '请先登录或者授权账号，才可以进行发布'}
        </p>
        <button type="button" disabled={busy || loading} onClick={() => void reloadAll()}
                className="text-[11px] px-2 py-0.5 rounded-md whitespace-nowrap"
                style={{ background: 'var(--bg-input)', color: 'var(--text-secondary)',
                         border: '1px solid var(--border-color)',
                         opacity: busy || loading ? 0.5 : 1 }}>
          {loading ? '加载中...' : '刷新账号'}
        </button>
      </div>

      {/* Plugin channel status. 🔴 When offline, **show a hint rather than disappearing** -- so ops know this path exists. */}
      {showPluginPanel && pluginErr && (
        <div className="rounded-md px-2.5 py-1.5 text-[11px] flex items-start gap-2"
             style={{ background: 'rgba(245,158,11,0.08)',
                      border: '1px solid rgba(245,158,11,0.35)', color: '#f59e0b' }}>
          <span>{hasWechatSyncPlugin() ? '插件已连接' : '插件未连接'}</span>
          <span className="text-muted">{pluginErr}</span>
        </div>
      )}

      {showPluginPanel && pluginUpdate && (
        <div className="rounded-md px-2.5 py-2 text-[11px] flex items-center gap-2"
             style={{ background: 'rgba(245,158,11,0.08)',
                      border: '1px solid rgba(245,158,11,0.35)', color: '#f59e0b' }}>
          <span className="flex-1">
            插件有新版本 v{pluginUpdate.version}，当前版本{pluginUpdate.currentVersion
              ? `为 v${pluginUpdate.currentVersion}` : '过旧，无法读取版本号'}。
          </span>
          <a href={pluginUpdate.downloadUrl} target="_blank" rel="noopener noreferrer"
             className="font-medium whitespace-nowrap underline">
            安装最新版
          </a>
        </div>
      )}

      {showPluginPanel && activePluginRegion === 'overseas' && unlistedAitoearnBindings.length > 0 && (
        <div className="rounded-md p-2 space-y-1"
             style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)' }}>
          <p className="text-xs font-medium text-primary">其他已授权账号</p>
          <p className="text-[11px] text-muted">这些账号当前不支持在此发布，仍可解除授权。</p>
          {unlistedAitoearnBindings.map(account => (
            <div key={account.binding_id} className="flex items-center gap-2 rounded-md px-2.5 py-1.5 text-xs"
                 style={{ background: 'var(--bg-card)' }}>
              {account.avatar && <img src={account.avatar} alt="" className="w-4 h-4 rounded-sm" />}
              <span className="text-primary font-medium whitespace-nowrap">{account.platform}</span>
              <span className="text-muted truncate">{account.nickname || '未命名账号'}</span>
              <button type="button" disabled={busy}
                      onClick={() => void unbindAitoearn(account)}
                      className="ml-auto text-[11px] whitespace-nowrap underline disabled:opacity-50"
                      style={{ color: '#ef4444' }}>
                解绑
              </button>
            </div>
          ))}
        </div>
      )}

      {!loading && visibleRows.length === 0 && showPluginPanel && !pluginErr
        && (activePluginRegion === 'domestic' || unlistedAitoearnBindings.length === 0) && (
        <p className="text-[11px] text-muted">
          {activePluginRegion === 'domestic'
            ? '暂无国内平台。请确认浏览器已安装并启用 WechatSync 插件。'
            : '暂无国外平台。请确认插件已启用，或授权 AiToEarn 国外账号。'}
        </p>
      )}

      {!loading && visibleRows.length === 0 && showServerPoolPanel && (
        <p className="text-[11px] text-muted">
          server_pool 暂无可发布账号。请确认后端账号池已配置账号、账号可调度,并且当前文章满足平台预检。
        </p>
      )}

      {showPluginPanel && visibleRows.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {pluginCapabilityGroups.map(group => (
            <section key={group.title} className="rounded-md p-2 space-y-2"
                     style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)' }}>
              <p className="text-xs font-medium text-primary">
                {group.title} <span className="text-muted">({group.rows.length})</span>
              </p>
              {group.rows.length === 0 && (
                <p className="py-4 text-center text-[11px] text-muted">暂无平台</p>
              )}
              {group.rows.map(r => {
                const active = selected.has(r.key);
                const acc = r.pluginAccount;
                const authPlatform = r.aitoearnAuthPlatform;
                const icon = acc?.icon || acc?.avatar || r.aitoearnAccount?.avatar || '';
                const loginUrl = acc?.loginUrl || acc?.home || '';
                const isLoggedOut = !!authPlatform || acc?.isAuthenticated === false;
                const authing = authPlatform === authenticatingPlatform;
                const authFailed = !!authPlatform && failedAuthPlatforms.has(authPlatform);
                const meta = CHANNEL_META[r.channel];
                return (
                  <label key={r.key}
                         title={r.blockReason || meta.hint}
                         className="flex items-center gap-2 rounded-md px-2.5 py-1.5 text-xs"
                         style={{
                           background: r.selectable ? 'var(--bg-card)' : 'var(--bg-input)',
                           border: `1px solid ${active ? 'var(--accent-primary)' : 'var(--border-color)'}`,
                           cursor: r.selectable ? 'pointer' : 'not-allowed',
                           opacity: r.selectable ? 1 : 0.62,
                         }}>
                    <input type="checkbox" checked={active}
                           disabled={busy || !r.selectable}
                           onChange={() => toggle(r.key)} />
                    {icon && <img src={icon} alt="" className="w-4 h-4 rounded-sm" />}
                    <span className="text-primary font-medium whitespace-nowrap">
                      {acc ? accountName(acc) : r.platformName}
                    </span>
                    <span className="text-muted truncate">{r.accountLabel}</span>
                    {r.blockReason && !authPlatform && (
                      <span className="text-[11px] truncate" style={{ color: '#ef4444' }}>
                        {r.blockReason}
                      </span>
                    )}
                    {authPlatform ? (
                      <button type="button"
                              disabled={!!authenticatingPlatform}
                              onClick={e => {
                                e.preventDefault();
                                e.stopPropagation();
                                void startAitoearnAuth(authPlatform);
                              }}
                              className="ml-auto text-[11px] whitespace-nowrap underline disabled:opacity-50"
                              style={{ color: '#ef4444' }}>
                        {authing ? '授权中…' : authFailed ? '授权失败' : '未登录'}
                      </button>
                    ) : isLoggedOut && loginUrl ? (
                      <button type="button"
                              onClick={e => {
                                e.preventDefault();
                                e.stopPropagation();
                                window.open(loginUrl, '_blank', 'noopener,noreferrer');
                              }}
                              className="ml-auto text-[11px] whitespace-nowrap underline"
                              style={{ color: '#ef4444' }}>
                        未登录
                      </button>
                    ) : (
                      <span className="ml-auto inline-flex items-center gap-2 text-[11px] whitespace-nowrap">
                        <span style={{ color: isLoggedOut ? '#ef4444' : 'var(--text-muted)' }}>
                          {r.statusText}
                        </span>
                        {r.aitoearnAccount?.binding_id && (
                          <button type="button" disabled={busy}
                                  onClick={e => {
                                    e.preventDefault();
                                    e.stopPropagation();
                                    void unbindAitoearn(r.aitoearnAccount!);
                                  }}
                                  className="underline disabled:opacity-50"
                                  style={{ color: '#ef4444' }}>
                            解绑
                          </button>
                        )}
                      </span>
                    )}
                  </label>
                );
              })}
            </section>
          ))}
        </div>
      )}

      {showPluginPanel && ctripSelected && (
        <div className="rounded-xl p-3 space-y-2"
             style={{ background: 'color-mix(in srgb, var(--accent-primary) 7%, var(--bg-card))', border: '1px solid color-mix(in srgb, var(--accent-primary) 28%, var(--border-color))' }}>
          <div className="flex items-center gap-2 text-sm font-medium text-primary">
            <span aria-hidden="true" style={{ color: '#10b981', fontSize: 18 }}>⌖</span>
            携程地点 <span style={{ color: '#ef4444' }}>*</span>
          </div>
          {ctripPoi ? (
            <div className="flex items-center gap-2 rounded-lg px-3 py-2"
                 style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
              <div className="min-w-0 flex-1">
                <div className="text-sm text-primary truncate">{ctripPoi.poiName}</div>
                {ctripPoiDescription(ctripPoi) && (
                  <div className="text-[11px] text-muted truncate mt-0.5">{ctripPoiDescription(ctripPoi)}</div>
                )}
              </div>
              <button type="button" aria-label="更换携程地点" title="更换地点"
                      className="text-xl leading-none text-muted"
                      onClick={() => {
                        setCtripKeyword(ctripPoi.poiName);
                        setCtripPoi(null);
                        setCtripPoiSource(null);
                      }}>
                ×
              </button>
            </div>
          ) : (
            <div className="space-y-1">
              <div className="relative">
                <input autoFocus value={ctripKeyword}
                       onChange={event => setCtripKeyword(event.target.value)}
                       placeholder="输入景点、酒店或城市名称"
                       className="w-full rounded-lg px-3 py-2 text-sm"
                       style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }} />
                {ctripPoiLoading && <span className="absolute right-3 top-2 text-xs text-muted">搜索中…</span>}
              </div>
              {ctripPoiError && <p className="text-[11px]" style={{ color: '#ef4444' }}>{ctripPoiError}</p>}
              {ctripPoiResults.length > 0 && (
                <div className="max-h-48 overflow-y-auto rounded-lg"
                     style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
                  {ctripPoiResults.map(poi => (
                    <button type="button" key={`${poi.poiId}-${poi.poiType || ''}`}
                            className="block w-full px-3 py-2 text-left hover:bg-[var(--bg-surface)]"
                            onClick={() => {
                              setCtripPoi(poi);
                              setCtripPoiSource('manual');
                              setCtripKeyword('');
                              setCtripPoiResults([]);
                              setCtripPoiError(null);
                              localStorage.setItem(CTRIP_RECENT_POI_KEY, JSON.stringify(poi));
                            }}>
                      <div className="text-sm text-primary">{poi.poiName}</div>
                      {ctripPoiDescription(poi) && (
                        <div className="text-[11px] text-muted mt-0.5">{ctripPoiDescription(poi)}</div>
                      )}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
          <p className="text-xs text-muted">
            {ctripPoi
              ? ctripPoiSource === 'auto'
                ? `已根据品牌资料中的“${ctripLocation?.name || ''}”自动匹配，可随时更换`
                : '已手动选择携程地点，可随时更换'
              : ctripPoiLoading && ctripLocation
                ? `正在根据品牌资料中的“${ctripLocation.name}”自动匹配…`
                : ctripLocation
                  ? '自动匹配失败，可在上方手动选择'
                : '请从携程返回的真实地点中选择'}
          </p>
        </div>
      )}

      {showServerPoolPanel && groups.map(([platformName, list]) => {
        const selectable = list.filter(r => r.selectable);
        const selectedInPlatform = selectable.filter(r => selected.has(r.key)).length;
        const allSelected = selectable.length > 0 && selectedInPlatform === selectable.length;
        const expanded = expandedPlatforms.has(`${activeChannel}:${platformName}`);
        return (
        <div key={platformName} className="rounded-md p-2 space-y-1"
             style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-color)' }}>
          <div className="w-full flex items-center justify-between gap-2">
            <button type="button"
                    onClick={() => togglePlatform(platformName)}
                    className="flex items-center gap-1.5 min-w-0 text-left"
                    style={{ cursor: 'pointer' }}>
                {showServerPoolPanel && (
                  <span className="text-[10px] text-muted w-3">
                    {expanded ? '▾' : '▸'}
                  </span>
                )}
                <span className="text-xs font-medium text-primary truncate">{platformName}</span>
            </button>
            <div className="flex items-center gap-2 shrink-0">
              <span className="text-[10px] text-muted">
                {selectedInPlatform}/{selectable.length} 已选 · {selectable.length}/{list.length} 可提交
              </span>
              <label className="inline-flex items-center gap-1 text-[11px] text-secondary cursor-pointer"
                     title={selectable.length === 0 ? '该平台当前没有可排队账号' : '选择该平台全部可排队账号'}>
                <input type="checkbox"
                       checked={allSelected}
                       disabled={busy || selectable.length === 0}
                       onChange={e => setPlatformSelected(list, e.currentTarget.checked)} />
                <span>全选</span>
              </label>
            </div>
          </div>
          {expanded && list.map(r => {
            const active = selected.has(r.key);
            const meta = CHANNEL_META[r.channel];
            return (
              <label key={r.key}
                     title={r.blockReason || meta.hint}
                     className="flex items-center gap-2 rounded-md px-2.5 py-1.5 text-xs"
                     style={{
                       background: r.selectable ? 'var(--bg-card)' : 'var(--bg-input)',
                       border: `1px solid ${active ? 'var(--accent-primary)' : 'var(--border-color)'}`,
                       cursor: r.selectable ? 'pointer' : 'not-allowed',
                       opacity: r.selectable ? 1 : 0.62,
                     }}>
                <input type="checkbox" checked={active}
                       disabled={busy || !r.selectable}
                       onChange={() => toggle(r.key)} />
                <span className="text-primary truncate">{r.accountLabel}</span>
                {/* Channel badges only make sense when **multiple channels coexist** -- with only the plugin left,
                    a "plugin" tag on every row is pure noise and squeezes account names narrower. */}
                {multiChannel && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded-full whitespace-nowrap"
                        style={{ background: `${meta.color}1f`, color: meta.color }}>
                    {meta.label}
                  </span>
                )}
                {r.warnings.length > 0 && !r.rejectReason && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded-full whitespace-nowrap"
                        title={r.warnings.join('\n')}
                        style={{ background: 'rgba(245,158,11,0.12)', color: '#f59e0b' }}>
                    {r.warnings.length} 条渲染警告
                  </span>
                )}
                <span className="ml-auto text-[11px] text-right whitespace-nowrap"
                      style={{ color: r.selectable ? 'var(--text-muted)' : '#ef4444' }}>
                  {r.blockReason || r.statusText}
                </span>
              </label>
            );
          })}
        </div>
        );
      })}

      {pluginProgress && (
        <div className="text-[11px]" style={{ color: '#f59e0b' }}>{pluginProgress}</div>
      )}

      {selectableCount > 0 && (
        <button type="button" disabled={busy || picked.length === 0 || missingCtripLocation} onClick={() => void runPublish()}
                className="text-xs px-3 py-1.5 rounded-md text-white"
                style={{ background: '#3b82f6', opacity: busy || picked.length === 0 || missingCtripLocation ? 0.5 : 1 }}>
          {busy ? (busyStage === 'preflight' ? '预检中...' : '发布中...')
            : missingCtripLocation ? '请设置携程地点' : `发布到 ${picked.length} 个账号`}
        </button>
      )}

      {/* Per-account results. Failures stay checked; clicking "Publish" again is the retry. */}
      {outcomes.length > 0 && (
        <div className="rounded-md p-2 space-y-1"
             style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
          <p className="text-[11px] text-muted">
            本次结果:成功 {outcomes.filter(o => o.state === 'ok').length} ·
            跳过 {outcomes.filter(o => o.state === 'skip').length} ·
            失败 {outcomes.filter(o => o.state === 'fail').length}
            {outcomes.some(o => o.state === 'fail') && ' —— 失败的账号仍在勾选中,可直接再点发布重试'}
          </p>
          {outcomes.map(o => (
            <div key={o.key} className="flex items-start gap-2 text-[11px]">
              <span style={{ color: o.state === 'ok' ? '#10b981'
                : o.state === 'fail' ? '#ef4444' : 'var(--text-muted)' }}>
                {o.state === 'ok' ? '✓' : o.state === 'fail' ? '✕' : '—'}
              </span>
              <span className="text-primary whitespace-nowrap">{o.label}</span>
              <span className="text-muted">{o.detail}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function rowLabel(r: Row): string {
  return `${r.platformName} / ${r.accountLabel}`;
}
