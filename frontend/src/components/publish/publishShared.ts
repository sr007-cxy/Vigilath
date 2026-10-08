// Shared types and status copy for the publish center (moved out of Dashboard/Content.tsx,
// shared by the content management / content review pages, removing the coupling of the Admin page importing back from the Dashboard page).

import type { ContentDoc, PublishTask, PublishTaskStatus } from '../../services/contentApi';

// The panel/publish center only depends on minimal draft fields.
// 2026-09-04 added title / summary / body_markdown: the full publish center page previously didn't show a single word about
// which article was being published; users could only guess from the breadcrumbs.
export type PublishPanelDoc = Pick<ContentDoc, 'id' | 'topic_id' | 'status'>
  & Partial<Pick<ContentDoc, 'title' | 'summary' | 'body_markdown' | 'created_at'>>;

export const PT_STATUS_LABEL: Record<PublishTaskStatus, string> = {
  planned: '待执行',
  pending_publish: '待手工发布',
  publishing: '发布中',
  pending_url: '待回填 URL',
  draft_saved: '已保存草稿',
  url_submitted: 'URL 校验中',
  published: '已投放',
  failed: '回填异常',
  cancelled: '已取消',
};

export const RWJ_PLATFORM = '软文匠';
export const MJK_PLATFORM = '媒介库';

// ── Paid placement vendors ───────────────────────────────────────────────
// The UI never shows vendors' full names (more will be added later), only a one-character code as a badge;
// **the data layer is unchanged** -- platform values are still the vendors' Chinese names and channel keys are still provider; only display changes.
// When adding a new vendor: add one row { channel key, platform value, code } here, and every page follows automatically.
export type PaidVendor = { key: string; platform: string; code: string; label: string };

export const PAID_VENDORS: PaidVendor[] = [
  { key: 'rwj', platform: RWJ_PLATFORM, code: '匠', label: '付费代发(匠)' },
  { key: 'mjk', platform: MJK_PLATFORM, code: '库', label: '付费代发(库)' },
];

/** Platform value (软文匠/媒介库, the vendor names) or channel key (rwj/mjk) -> vendor; returns null if not paid placement.
 *
 * ⚠️ **Don't use it to decide whether a task is paid placement** -- the `platform` regular users receive has already been
 * replaced by the backend with 「付费媒体」 ("paid media") (not a single character of the vendor name is sent), so this returns null. To check the type, use
 * `isPaidMediaTask`. This function only makes sense where the vendor name is available (staff / plan editor).
 */
export function paidVendorOf(v?: string | null): PaidVendor | null {
  if (!v) return null;
  return PAID_VENDORS.find((x) => x.key === v || x.platform === v) ?? null;
}

/** Whether this task is paid placement. The boolean flag from the backend wins; only when unavailable fall back to the platform name. */
export function isPaidMediaTask(tk: PublishTask): boolean {
  if (typeof tk.is_paid_media === 'boolean') return tk.is_paid_media;
  return !!paidVendorOf(tk.platform);
}

/** Platform value -> UI display name: paid placement channels don't reveal the vendor's full name, only 「付费代发(匠)」 ("paid placement (匠)") */
export function platformLabel(v: string): string {
  return paidVendorOf(v)?.label ?? v;
}

/** Display name of a placement task.
 *
 * For regular users the backend sends `platform_display` (always 「付费媒体」 ("paid media") for paid tasks), for staff
 * it doesn't -- so use it when present, and the user side doesn't even see the code; staff still see the 匠/库 badge.
 */
export function taskDisplayName(tk: PublishTask): string {
  if (isPaidMediaTask(tk)) {
    const vendor = paidVendorOf(tk.platform);        // only staff get this
    const name = tk.media_name || '待选媒体';
    return vendor ? `${name} ${vendor.code}` : name;
  }
  return `${tk.platform}${tk.media_name ? `·${tk.media_name}` : ''}`;
}

// Paid placement task statuses have different semantics from manual tasks, so they get their own copy
export const RWJ_STATUS_LABEL: Partial<Record<PublishTaskStatus, string>> = {
  planned: '待发布(付费)',
  publishing: '下单结果未知·待对账',
  pending_url: '已下单·待回执',
  failed: '下单失败',
};

// aitoearn multi-platform distribution tasks: identified by channel_meta.channel (platform is the target platform's Chinese name)
export function isAitoearnTask(tk: PublishTask): boolean {
  return tk.channel_meta?.channel === 'aitoearn';
}

export const AITOEARN_STATUS_LABEL: Partial<Record<PublishTaskStatus, string>> = {
  planned: '待发布',
  publishing: '提交结果未知·待对账',
  pending_url: '已提交·等待平台链接',
  failed: '发布失败',
};

export function taskStatusLabel(tk: PublishTask): string {
  if (tk.status === 'failed'
      && (tk.channel_type === 'auto_account'
          || tk.channel_meta?.candidate_url
          || tk.channel_meta?.url_verification?.candidate_url)) {
    return '推送失败';
  }
  if (isPaidMediaTask(tk) && RWJ_STATUS_LABEL[tk.status]) {
    return RWJ_STATUS_LABEL[tk.status] as string;
  }
  if (isAitoearnTask(tk) && AITOEARN_STATUS_LABEL[tk.status]) {
    return AITOEARN_STATUS_LABEL[tk.status] as string;
  }
  return PT_STATUS_LABEL[tk.status] || tk.status;
}
