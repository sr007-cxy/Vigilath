import axios from 'axios';
import type { GeoTestResult } from '../types/geo';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api';

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 300000, // 300 seconds timeout
  headers: {
    'Content-Type': 'application/json',
  },
});

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
  async runGeoCheck(url: string, includeFix: boolean = true): Promise<GeoTestResult> {
    const request = {
      url,
      include_fix: includeFix,
    };
    const { path, headers } = resolveCheckRequest();
    try {
      const response = await apiClient.post(path, request, { headers });
      return response.data;
    } catch (error) {
      if (axios.isAxiosError(error)) {
        throw new Error(error.response?.data?.detail || error.response?.data?.message || 'Failed to run GEO check');
      }
      throw new Error('Failed to run GEO check');
    }
  },

  async checkGeo(data: any): Promise<GeoTestResult> {
    const { path, headers } = resolveCheckRequest();
    try {
      const response = await apiClient.post(path, data, { headers });
      return response.data;
    } catch (error) {
      if (axios.isAxiosError(error)) {
        throw new Error(error.response?.data?.detail || error.response?.data?.message || 'Failed to run GEO check');
      }
      throw new Error('Failed to run GEO check');
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
