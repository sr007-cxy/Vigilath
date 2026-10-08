// Media library -- the unified user-side entry.
//
// Difference from the rwj* methods in contentApi: those hit the supplier sites' live APIs (now restricted to ops),
// exposing vendor info like "Ruanwenjiang / Meijieku"; this hits the merged, deduplicated mirror table,
// so users see only the media themselves, with media_id as the row identifier (independent of supplier).
import { readApiError, localizedHeaders } from './apiError';

const API_BASE = '/api';

export type MediaResType = 'Website' | 'Wemedia' | 'Shortvideo' | 'Question' | 'Ghostwrite';
export type MediaSort = 'price_asc' | 'price_desc' | 'rate_desc' | 'weigh_desc';

export interface MediaRow {
  media_id: number;
  title: string;
  res_type: MediaResType;
  platform: string;
  channel: string;
  region: string;
  include: string;
  /** External quote (cents). Used for the list, selection totals and expected order price; the frontend applies no markup conversion. */
  price_cents: number;
  price: string;
  /** The reference price is older than the threshold since the last sync -- grayed out as a hint; the price is checked live at order time. */
  price_stale: boolean;
  publish_rate: number | null;
  publish_time: string;
  pc_weigh: number | null;
  wap_weigh: number | null;
  remarks: string;
  case_link: string;
}

export interface MediaSearchOut {
  available: boolean;
  rows: MediaRow[];
  total: number;
  page: number;
  page_size: number;
  res_type?: MediaResType;
  sort?: MediaSort;
  message?: string;
}

export interface MediaFacets {
  available: boolean;
  facets: { platform?: string[]; channel?: string[]; region?: string[]; include?: string[] };
  types?: MediaResType[];
  message?: string;
}

export interface MediaSearchQuery {
  res_type?: MediaResType;
  platform?: string;
  channel?: string;
  region?: string;
  include?: string;
  keyword?: string;
  price_min_cents?: number | null;
  price_max_cents?: number | null;
  sort?: MediaSort;
  page?: number;
  page_size?: number;
}

function auth(token: string) {
  return localizedHeaders({
    'Content-Type': 'application/json',
    Authorization: `Bearer ${token}`,
  });
}

export const mediaApi = {
  async facets(token: string, resType: MediaResType): Promise<MediaFacets> {
    const r = await fetch(`${API_BASE}/media/facets?res_type=${resType}`, { headers: auth(token) });
    if (!r.ok) throw new Error(await readApiError(r, '读取筛选项失败'));
    return r.json();
  },

  async search(token: string, q: MediaSearchQuery): Promise<MediaSearchOut> {
    const r = await fetch(`${API_BASE}/media/search`, {
      method: 'POST', headers: auth(token), body: JSON.stringify(q),
    });
    if (!r.ok) throw new Error(await readApiError(r, '媒体检索失败'));
    return r.json();
  },

  async pushDrafts(token: string, docId: number, mediaIds: number[]): Promise<{
    // Only the id is useful -- "Pick and send" uses it to continue with the batch order
    tasks: { id: number }[];
    failed: { media_name: string; message: string }[];
    // Images not carried over in the body -- the draft is still pushed, but the user must be told
    dropped_images?: { url: string; name: string; reason: string }[];
  }> {
    const r = await fetch(`${API_BASE}/media/docs/${docId}/push-draft`, {
      method: 'POST', headers: auth(token), body: JSON.stringify({ media_ids: mediaIds }),
    });
    if (!r.ok) throw new Error(await readApiError(r, '推送草稿失败'));
    return r.json();
  },
};

export const priceYuan = (cents: number): string => (cents / 100).toFixed(2);
