// WechatSync / article sync assistant browser plugin bridge -- `window.$syncer` / `window.$poster`.
//
// The page -> browser plugin bridge, shaped as: types + `declare global` + has*Plugin() +
// a call wrapped with a timeout. Its predecessors WechatSyncSection.tsx and aitoearnPlugin.ts have both been deleted
// (2026-08-27); everything useful is here.
//
// The reason for extracting isn't reuse fastidiousness: the unified publish drawer (UnifiedTargetList) also needs to read plugin accounts, and
// `declare global { interface Window { $syncer } }` **can only exist in one place in a TS project** --
// writing it in two files is a duplicate global augmentation, and the slightest type mismatch fails compilation.
//
// 🔴 Plugin channel accounts **exist only in the browser on the ops machine**, and no backend endpoint can see them. So
// "one endpoint returning all publishable accounts" is architecturally impossible, and the three-source merge can only happen on the frontend (see
// the docstring of GET /api/publish-dispatch/targets).

export type PlatformRegion = 'domestic' | 'overseas';

export type CtripPoi = {
  poiId: string | number;
  poiName: string;
  poiType?: string | number;
  poiAddressInfo?: {
    typeName?: string;
    cityName?: string;
    address?: string;
  };
  [key: string]: unknown;
};

export type CtripLocation = {
  name: string;
  city?: string;
  address?: string;
};

export type CtripVerificationState =
  | 'reviewing' | 'published' | 'rejected' | 'draft' | 'not_found' | 'query_error';

export type CtripVerificationJob = {
  ownerKey: string;
  taskId: number;
  articleId: string;
  candidateUrl: string;
  nextAt: string;
  initialDelaySeconds: number;
  confirmationDelaySeconds: number;
  firstPublishedAt?: string | null;
};

export type CtripVerificationObservation = {
  id: string;
  ownerKey: string;
  taskId: number;
  articleId: string;
  phase: 'initial' | 'confirmation';
  state: CtripVerificationState;
  observedAt: string;
  rawStatus?: number;
  auditFailStr?: string;
  publishTime?: string;
  errorMessage?: string;
};

export type SyncerAccount = {
  type: string;
  accountKey?: string;
  slotId?: string;
  isCurrent?: boolean;
  isAuthenticated?: boolean;
  loginUrl?: string;
  title: string;
  displayName?: string;
  icon?: string;
  avatar?: string;
  uid?: string;
  home?: string;
  /** Platform region; when old plugin versions don't return it, the frontend infers it from the platform ID for compatibility. */
  region?: PlatformRegion;
  /** true means the platform can only save drafts; when old plugins don't return it, treat as supporting direct publishing for compatibility. */
  draftOnly?: boolean;
};

export type SyncerAccountStatus = SyncerAccount & {
  status?: 'pending' | 'uploading' | 'done' | 'failed';
  msg?: string;
  error?: string;
  editResp?: { draftLink?: string } | null;
  postId?: string;
  urlSource?: string;
};

export type SyncerTaskUpdate = {
  accounts?: SyncerAccountStatus[];
  error?: string;
};

export type PluginSessionExport = {
  storage_state?: {
    cookies?: unknown[];
    origins?: unknown[];
  };
  user_agent?: string;
  ua_platform?: string;
  account_handle?: string;
  account_key?: string;
};

function recordOf(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function parseMaybeJson(value: unknown): unknown {
  if (typeof value !== 'string') return value;
  const trimmed = value.trim();
  if (!trimmed) return value;
  try {
    return JSON.parse(trimmed);
  } catch {
    return value;
  }
}

function normalizePluginSession(value: unknown, platform: string): PluginSessionExport {
  const parsed = parseMaybeJson(value);
  const result = recordOf(parsed);
  const state = recordOf(parseMaybeJson(result?.storage_state));
  const cookies = parseMaybeJson(state?.cookies);
  const origins = parseMaybeJson(state?.origins);
  if (!result || !state) {
    throw new Error(`插件没有返回登录态：平台=${platform}`);
  }
  if (!Array.isArray(cookies)) {
    throw new Error(`插件返回的 cookies 不是数组：平台=${platform}。请重新加载最新版插件后重试。`);
  }
  if (!cookies.every(cookie => !!recordOf(cookie))) {
    throw new Error(`插件返回的 cookies 数组元素不是对象：平台=${platform}。请重新加载最新版插件后重试。`);
  }
  return {
    ...result,
    storage_state: {
      cookies,
      origins: Array.isArray(origins) ? origins : [],
    },
  } as PluginSessionExport;
}

export type SyncerApi = {
  getPluginInfo?: (
    cb: (arg1?: unknown, arg2?: unknown) => void,
  ) => void;
  getAccounts: (
    optionsOrCb: { forceRefresh?: boolean } | ((arg1?: unknown, arg2?: unknown) => void),
    cb?: (arg1?: unknown, arg2?: unknown) => void,
  ) => void;
  prewarmAccounts?: (
    optionsOrCb?: { forceRefresh?: boolean } | ((arg1?: unknown, arg2?: unknown) => void),
    cb?: (arg1?: unknown, arg2?: unknown) => void,
  ) => void;
  addTask: (
    task: {
      post: {
        title: string;
        content: string;
        markdown: string;
        thumb?: string;
        topics?: string;
        location?: CtripLocation;
        ctripPoi?: CtripPoi;
      };
      accounts: SyncerAccount[];
    },
    statusHandler: (task: SyncerTaskUpdate) => void,
    cb?: (arg1?: unknown, arg2?: unknown) => void,
  ) => void;
  exportSession?: (
    options: { platform: string; accountKey?: string; includeLocalStorage?: boolean },
    cb: (arg1?: unknown, arg2?: unknown) => void,
  ) => void;
  magicCall?: (
    data: {
      methodName: string;
      platform: string;
      keyword?: string;
      articleId?: string;
      location?: CtripLocation;
    },
    cb: (arg1?: unknown, arg2?: unknown) => void,
  ) => void;
  syncCtripVerificationJobs?: (
    jobs: CtripVerificationJob[], cb: (arg1?: unknown, arg2?: unknown) => void,
  ) => void;
  getCtripVerificationResults?: (
    ownerKey: string, cb: (arg1?: unknown, arg2?: unknown) => void,
  ) => void;
  ackCtripVerificationResults?: (
    ownerKey: string, ids: string[], cb: (arg1?: unknown, arg2?: unknown) => void,
  ) => void;
};

declare global {
  interface Window {
    $syncer?: SyncerApi;
    $poster?: SyncerApi;
  }
}

export function pluginApi(): SyncerApi | undefined {
  return typeof window === 'undefined' ? undefined : (window.$syncer || window.$poster);
}

export function hasWechatSyncPlugin(): boolean {
  return !!pluginApi();
}

export function ctripVerificationOwnerKey(userId: number): string {
  return `${window.location.origin}:${userId}`;
}

export const PLUGIN_MISSING_MSG =
  '未检测到 WechatSync 插件。请先安装并启用插件，然后刷新页面。';
const PLUGIN_INVALIDATED_MSG =
  'WechatSync 插件刚刚重新加载，请刷新账号池页面后重试。';

type PluginReleaseInfo = {
  version: string;
  downloadUrl: string;
  releaseNotes?: string;
};

export type PluginUpdateNotice = PluginReleaseInfo & {
  currentVersion: string | null;
};

const PLUGIN_RELEASE_URL = '/downloads/plugin/version.json';
const versionComparator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });

export async function detectPluginUpdate(): Promise<PluginUpdateNotice | null> {
  const api = pluginApi();
  if (!api) return null;

  const currentVersion = api.getPluginInfo
    ? await new Promise<string | null>(resolve => {
        const timer = window.setTimeout(() => resolve(null), 3000);
        api.getPluginInfo?.((arg1, arg2) => {
          window.clearTimeout(timer);
          const info = recordOf(arg1) || recordOf(arg2);
          resolve(typeof info?.version === 'string' ? info.version : null);
        });
      })
    : null;
  const response = await fetch(PLUGIN_RELEASE_URL, { cache: 'no-store' });
  if (!response.ok) return null;
  const release = await response.json() as Partial<PluginReleaseInfo>;
  if (typeof release.version !== 'string' || typeof release.downloadUrl !== 'string') return null;
  if (currentVersion && versionComparator.compare(currentVersion, release.version) >= 0) return null;
  return { ...release as PluginReleaseInfo, currentVersion };
}

function pluginBridgeError(value: unknown, fallback: string): Error {
  const message = value instanceof Error ? value.message : typeof value === 'string' ? value : fallback;
  if (/extension context invalidated/i.test(message)) return new Error(PLUGIN_INVALIDATED_MSG);
  if (/window is not defined/i.test(message)) {
    return new Error('WechatSync 插件后台脚本异常：插件代码在 Service Worker 中访问了 window。请重新加载最新插件后刷新账号池页面。');
  }
  if (/^(not found|platform not found|adapter not found)(:|$)/i.test(message.trim())) {
    return new Error(`WechatSync 插件未找到对应平台适配器：请确认当前选择的平台已被插件支持，并已重新加载最新插件。原始错误：${message}`);
  }
  return new Error(message || fallback);
}

export function accountName(acc: SyncerAccount): string {
  return acc.displayName || acc.type;
}

export function accountMedia(acc: SyncerAccount): string {
  if (acc.slotId) return acc.uid ? `${acc.title} · ${acc.uid}` : acc.title;
  return acc.uid || acc.title || '';
}

export function accountId(acc: SyncerAccount): string {
  return acc.accountKey || `${acc.type}:${acc.slotId || acc.uid || acc.title}`;
}

export function isCurrentPluginAccount(acc: SyncerAccount): boolean {
  return !acc.slotId;
}

/** Read the publishing platform states in the plugin. When the plugin is absent it **rejects**, so callers can tell "no plugin" from "no accounts in the plugin". */
export function readPluginAccounts(forceRefresh = false): Promise<SyncerAccount[]> {
  const api = pluginApi();
  if (!api) return Promise.reject(new Error(PLUGIN_MISSING_MSG));
  return new Promise((resolve, reject) => {
    const timeout = forceRefresh ? 60_000 : 12_000;
    const timer = window.setTimeout(() => reject(new Error('读取插件账号超时，请确认插件已启用。')), timeout);
    const done = (arg1?: unknown, arg2?: unknown) => {
      window.clearTimeout(timer);
      const errorResult = (arg1 && typeof arg1 === 'object' && 'error' in arg1)
        ? arg1
        : (arg2 && typeof arg2 === 'object' && 'error' in arg2)
          ? arg2
          : null;
      if (errorResult) {
        reject(pluginBridgeError((errorResult as { error?: unknown }).error, '读取插件账号失败'));
        return;
      }
      // Different plugin versions put the callback args in different positions -- use whichever is an array.
      const maybeAccounts = Array.isArray(arg1) ? arg1 : Array.isArray(arg2) ? arg2 : [];
      resolve(maybeAccounts as SyncerAccount[]);
    };
    if (api.getAccounts.length >= 2) {
      api.getAccounts({ forceRefresh }, done);
    } else {
      api.getAccounts(done);
    }
  });
}

export function searchCtripPois(keyword: string): Promise<CtripPoi[]> {
  const api = pluginApi();
  if (!api?.magicCall) return Promise.reject(new Error('当前发布插件不支持携程地点搜索，请升级插件。'));
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error('携程地点搜索超时，请确认携程已登录。')), 45_000);
    api.magicCall?.({ methodName: 'searchPois', platform: 'ctrip', keyword }, (arg1, arg2) => {
      window.clearTimeout(timer);
      const payload = (arg1 && typeof arg1 === 'object' ? arg1 : arg2) as {
        result?: unknown;
        error?: unknown;
      } | undefined;
      if (payload?.error) {
        reject(pluginBridgeError(payload.error, '携程地点搜索失败'));
        return;
      }
      const rows: unknown[] = Array.isArray(payload?.result) ? payload.result : [];
      resolve(rows.filter((row): row is CtripPoi => {
        const item = recordOf(row);
        return !!item
          && (typeof item.poiId === 'string' || typeof item.poiId === 'number')
          && typeof item.poiName === 'string';
      }));
    });
  });
}

export function resolveCtripPoi(location: CtripLocation): Promise<CtripPoi> {
  const api = pluginApi();
  if (!api?.magicCall) return Promise.reject(new Error('当前发布插件不支持携程地点自动匹配，请升级插件。'));
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error('携程地点自动匹配超时，请确认携程已登录。')), 45_000);
    api.magicCall?.({ methodName: 'resolvePoi', platform: 'ctrip', location }, (arg1, arg2) => {
      window.clearTimeout(timer);
      const payload = (arg1 && typeof arg1 === 'object' ? arg1 : arg2) as {
        result?: unknown;
        error?: unknown;
      } | undefined;
      if (payload?.error) {
        reject(pluginBridgeError(payload.error, '携程地点自动匹配失败'));
        return;
      }
      const poi = recordOf(payload?.result);
      if (!poi || (typeof poi.poiId !== 'string' && typeof poi.poiId !== 'number')
        || typeof poi.poiName !== 'string') {
        reject(new Error('携程地点自动匹配结果无效'));
        return;
      }
      resolve(poi as CtripPoi);
    });
  });
}

export function syncCtripVerificationJobs(jobs: CtripVerificationJob[]): Promise<void> {
  const api = pluginApi();
  if (!api?.syncCtripVerificationJobs) {
    return Promise.reject(new Error('当前发布插件不支持携程定时核验，请升级插件。'));
  }
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error('登记携程核验任务超时。')), 12_000);
    api.syncCtripVerificationJobs?.(jobs, (arg1, arg2) => {
      window.clearTimeout(timer);
      const payload = (arg1 && typeof arg1 === 'object' ? arg1 : arg2) as { error?: unknown } | undefined;
      if (payload?.error) {
        reject(pluginBridgeError(payload.error, '登记携程核验任务失败'));
        return;
      }
      resolve();
    });
  });
}

export function readCtripVerificationResults(ownerKey: string): Promise<CtripVerificationObservation[]> {
  const api = pluginApi();
  if (!api?.getCtripVerificationResults) return Promise.resolve([]);
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error('读取携程核验结果超时。')), 12_000);
    api.getCtripVerificationResults?.(ownerKey, (arg1, arg2) => {
      window.clearTimeout(timer);
      const payload = (arg1 && typeof arg1 === 'object' ? arg1 : arg2) as {
        results?: CtripVerificationObservation[]; error?: unknown;
      } | undefined;
      if (payload?.error) {
        reject(pluginBridgeError(payload.error, '读取携程核验结果失败'));
        return;
      }
      resolve(Array.isArray(payload?.results) ? payload.results : []);
    });
  });
}

export function ackCtripVerificationResults(ownerKey: string, ids: string[]): Promise<void> {
  const api = pluginApi();
  if (!api?.ackCtripVerificationResults || ids.length === 0) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error('确认携程核验结果超时。')), 12_000);
    api.ackCtripVerificationResults?.(ownerKey, ids, (arg1, arg2) => {
      window.clearTimeout(timer);
      const payload = (arg1 && typeof arg1 === 'object' ? arg1 : arg2) as { error?: unknown } | undefined;
      if (payload?.error) reject(pluginBridgeError(payload.error, '确认携程核验结果失败'));
      else resolve();
    });
  });
}

export function subscribeCtripVerificationResultsAvailable(onAvailable: () => void): () => void {
  const handler = (evt: MessageEvent) => {
    try {
      const action = typeof evt.data === 'string' ? JSON.parse(evt.data) : evt.data;
      if (action?.method === 'ctripVerificationResultsAvailable') onAvailable();
    } catch {
      // Ignore other postMessages on the page.
    }
  };
  window.addEventListener('message', handler);
  return () => window.removeEventListener('message', handler);
}

export function exportPluginSession(platform: string, accountKey?: string): Promise<PluginSessionExport> {
  const api = pluginApi();
  if (!api) return Promise.reject(new Error(PLUGIN_MISSING_MSG));
  if (!api.exportSession) return Promise.reject(new Error('当前 WechatSync 插件版本不支持导入账号池，请升级插件。'));
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error(`插件导出登录态超时：平台=${platform}。请确认已在当前浏览器登录该平台账号，并且插件已重新加载。`)), 12000);
    api.exportSession?.({ platform, accountKey, includeLocalStorage: false }, (arg1?: unknown, arg2?: unknown) => {
      window.clearTimeout(timer);
      const errorResult = (arg1 && typeof arg1 === 'object' && 'error' in arg1)
        ? arg1
        : (arg2 && typeof arg2 === 'object' && 'error' in arg2)
          ? arg2
          : null;
      if (errorResult) {
        const cause = pluginBridgeError((errorResult as { error?: unknown }).error, '插件导出登录态失败');
        reject(new Error(`插件导出登录态失败：平台=${platform}。${cause.message}`));
        return;
      }
      const result = (arg1 && typeof arg1 === 'object' && 'storage_state' in arg1)
        ? arg1
        : (arg2 && typeof arg2 === 'object' && 'storage_state' in arg2)
          ? arg2
          : null;
      if (result) {
        try {
          resolve(normalizePluginSession(result, platform));
          return;
        } catch (error) {
          reject(error);
          return;
        }
      }
      const cause = pluginBridgeError(arg1, '插件没有返回登录态');
      reject(new Error(`插件导出登录态失败：平台=${platform}。${cause.message}`));
    });
  });
}

/** Default cap for `waitForPlugin`. Extension cold start + MV3 service worker wake-up can take 1s+ on slow machines. */
const PLUGIN_DETECT_TIMEOUT_MS = 2500;
const PLUGIN_DETECT_INTERVAL_MS = 100;

/**
 * Wait for the browser plugin to inject `window.$syncer`.
 *
 * 🔴 Don't use a fixed delay to judge "is the plugin installed". Injection is asynchronous
 * (the content script dynamically inserts a `<script src=chrome.runtime.getURL('inject-api.js')>`),
 * and a fixed delay will inevitably miss it on slow machines or plugin cold starts -- the result is **telling users who already installed the plugin it's "not installed"**,
 * and users will think the product is broken. This now polls until it appears, deciding it's not installed only on timeout.
 *
 * @returns true = plugin ready; false = still absent at the cap, can be considered not installed
 */
export function waitForPlugin(timeoutMs = PLUGIN_DETECT_TIMEOUT_MS): Promise<boolean> {
  if (hasWechatSyncPlugin()) return Promise.resolve(true);
  return new Promise(resolve => {
    const deadline = Date.now() + timeoutMs;
    const timer = window.setInterval(() => {
      if (hasWechatSyncPlugin()) {
        window.clearInterval(timer);
        resolve(true);
      } else if (Date.now() >= deadline) {
        window.clearInterval(timer);
        resolve(false);
      }
    }, PLUGIN_DETECT_INTERVAL_MS);
  });
}

export function subscribePluginAccountsUpdate(
  onUpdate: (accounts: SyncerAccount[]) => void,
): () => void {
  const handler = (evt: MessageEvent) => {
    try {
      const action = typeof evt.data === 'string' ? JSON.parse(evt.data) : evt.data;
      if (action?.method !== 'accountsUpdate' || !Array.isArray(action.accounts)) return;
      onUpdate(action.accounts as SyncerAccount[]);
    } catch {
      // Ignore other postMessages on the page.
    }
  };
  window.addEventListener('message', handler);
  return () => window.removeEventListener('message', handler);
}

export function prewarmPluginAccounts(): Promise<boolean> {
  const api = pluginApi();
  if (!api?.prewarmAccounts) return Promise.resolve(false);
  return new Promise(resolve => {
    const timer = window.setTimeout(() => resolve(false), 12000);
    api.prewarmAccounts?.({}, () => {
      window.clearTimeout(timer);
      resolve(true);
    });
  });
}

/** Whether all checked accounts have reached a terminal state (done/failed) -- the plugin's progress callback relies on it to judge "this round is over". */
export function terminal(accounts: SyncerAccountStatus[], selected: Set<string>): boolean {
  if (selected.size === 0) return false;
  const selectedRows = accounts.filter(a => selected.has(accountId(a)));
  return selectedRows.length === selected.size
    && selectedRows.every(a => a.status === 'done' || a.status === 'failed');
}

export function publicUrlOf(acc: SyncerAccountStatus): string {
  const url = acc.editResp?.draftLink || '';
  return url.startsWith('http://') || url.startsWith('https://') ? url : '';
}
