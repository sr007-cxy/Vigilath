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

export const geoApi = {
  async runGeoCheck(url: string, includeFix: boolean = true): Promise<GeoTestResult> {
    const request = {
      url,
      include_fix: includeFix,
    };
    
    try {
      const response = await apiClient.post('/geo', request);
      return response.data;
    } catch (error) {
      if (axios.isAxiosError(error)) {
        throw new Error(error.response?.data?.detail || 'Failed to run GEO check');
      }
      throw new Error('Failed to run GEO check');
    }
  },

  runGeoCheckStream(url: string, includeFix: boolean = true, onUpdate: (data: any) => void, onError: (error: Error) => void): () => void {
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

    eventSource.addEventListener('error', (error) => {
      // Only close the connection if it's not a temporary error
      if (eventSource.readyState === EventSource.CLOSED) {
        onError(new Error('SSE connection error'));
        eventSource.close();
      }
    });

    return () => eventSource.close();
  },
};
