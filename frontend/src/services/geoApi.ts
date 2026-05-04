import axios from 'axios';
import type { GeoTestResult } from '../types/geo';
import type {
  AdvancedMode,
  AdvancedRequestBody,
  AdvancedResponseOf,
} from '../types/advanced';
import { extractAxiosErrorMessage, currentLocale } from './apiError';

const API_BASE_URL = import.meta.env.VITE_API_URL || '/api';

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 900000, // 900 seconds (15 min) — 慢站 25 类全跑常超 300s,后端会写 L1 缓存,
                   // 临时拉长避免前端过早 abort;后续切 SSE 后此值可降回
  headers: {
    'Content-Type': 'application/json',
  },
});

// Stamp every outgoing request with the current UI locale so the backend
// error handler can return localized error messages.
apiClient.interceptors.request.use((config) => {
  config.headers = config.headers || {};
  config.headers['X-Locale'] = currentLocale();
  return config;
});

/** Error thrown by geoApi calls with the HTTP status attached so callers can
 *  branch on specific cases (e.g. 429 quota exceeded). Message is the
 *  backend-supplied string when available, otherwise a generic fallback. */
export class ApiError extends Error {
  status?: number;
  constructor(message: string, status?: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

function throwApiError(error: unknown, fallback: string): never {
  if (axios.isAxiosError(error)) {
    const message = extractAxiosErrorMessage(error, fallback);
    throw new ApiError(message, error.response?.status);
  }
  throw new ApiError(fallback);
}

// Route-path segment for each advanced mode. Keep in sync with backend
// backend/app/api/advanced.py paths.
const ADVANCED_PATH: Record<AdvancedMode, string> = {
  aeo: '/check/advanced/aeo',
  compare: '/check/advanced/compare',
  crawlTest: '/check/advanced/crawl-test',
  authority: '/check/advanced/authority',
  citation: '/check/advanced/citation',
  visibility: '/check/advanced/visibility',
  entity: '/check/advanced/entity',
};

/**
 * Pick the check endpoint + headers based on whether the caller is logged in.
 *
 * - Logged in → POST /api/check with Bearer token. Backend enforces per-user
 *   quota and runs the user's tier's allowed_check_categories.
 * - Not logged in → POST /api/check/anonymous. No quota, always 5-category free
 *   run, returns tier='free'.
 */
function resolveCheckRequest() {
  const token = typeof window !== 'undefined' ? localStorage.getItem('token') : null;
  if (token) {
    return {
      path: '/check',
      headers: { Authorization: `Bearer ${token}` },
    };
  }
  return { path: '/check/anonymous', headers: {} };
}

export const geoApi = {
  async runGeoCheck(
    url: string,
    includeFix: boolean = true,
    signal?: AbortSignal,
  ): Promise<GeoTestResult> {
    const request = {
      url,
      include_fix: includeFix,
    };
    const { path, headers } = resolveCheckRequest();
    try {
      const response = await apiClient.post(path, request, { headers, signal });
      return response.data;
    } catch (error) {
      throwApiError(error, 'Failed to run GEO check');
    }
  },

  async runAdvancedCheck<M extends AdvancedMode>(
    mode: M,
    body: AdvancedRequestBody<M>,
    signal?: AbortSignal,
  ): Promise<AdvancedResponseOf<M>> {
    const token = typeof window !== 'undefined' ? localStorage.getItem('token') : null;
    const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};
    try {
      const response = await apiClient.post(ADVANCED_PATH[mode], body, { headers, signal });
      return response.data as AdvancedResponseOf<M>;
    } catch (error) {
      throwApiError(error, 'Failed to run advanced check');
    }
  },

  async checkGeo(data: any, signal?: AbortSignal): Promise<GeoTestResult> {
    const { path, headers } = resolveCheckRequest();
    try {
      const response = await apiClient.post(path, data, { headers, signal });
      return response.data;
    } catch (error) {
      throwApiError(error, 'Failed to run GEO check');
    }
  },

  runGeoCheckStream(
    url: string,
    includeFix: boolean = true,
    onUpdate: (data: any) => void,
    onError: (error: Error) => void,
  ): () => void {
    // EventSource doesn't support custom headers — we can't attach Bearer auth.
    // For authenticated-tier SSE we'd need a short-lived query token or switch
    // to fetch streaming. For now the SSE endpoint falls through to the free
    // tier for logged-in users, which is a minor limitation until the post-MVP
    // SSE auth story is designed.
    const encodedUrl = encodeURIComponent(url);
    const eventSource = new EventSource(`${API_BASE_URL}/geo/stream?url=${encodedUrl}&include_fix=${includeFix}`);

    eventSource.addEventListener('status', (event) => {
      try {
        const data = JSON.parse(event.data);
        onUpdate(data);

        if (data.status === 'completed' || data.status === 'failed') {
          eventSource.close();
        }
      } catch (error) {
        onError(new Error('Failed to parse SSE event'));
        eventSource.close();
      }
    });

    eventSource.addEventListener('error', () => {
      if (eventSource.readyState === EventSource.CLOSED) {
        onError(new Error('SSE connection error'));
        eventSource.close();
      }
    });

    return () => eventSource.close();
  },
};
