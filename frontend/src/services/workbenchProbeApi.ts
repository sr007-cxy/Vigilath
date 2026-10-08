// Workbench "engine live test" API client (admin + ops) — submit a question -> browser automation runs it ->
// poll for the answer + execution recording. The recording endpoint requires login auth, and the <video> tag can't carry request headers,
// so fetchVideoBlob gets the Blob and then createObjectURL plays it.
import { localizedHeaders, readApiError } from './apiError';

const API_BASE = (import.meta.env.VITE_API_URL as string) || '/api';

export interface ProbeEngine {
  key: string;
  name: string;
}

export interface ProbeCitation {
  url: string;
  domain: string;
  title: string;
  snippet: string;
  position: number;
}

export interface ProbeRun {
  id: number;
  engine: string;
  query: string;
  status: 'queued' | 'running' | 'done' | 'failed';
  answer: string | null;
  citations: ProbeCitation[];
  video_url: string | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

function authHeaders(token: string) {
  return localizedHeaders({ Authorization: `Bearer ${token}` });
}

export const workbenchProbeApi = {
  async listEngines(token: string): Promise<ProbeEngine[]> {
    const resp = await fetch(`${API_BASE}/workbench/probe/engines`, {
      headers: authHeaders(token),
    });
    if (!resp.ok) throw new Error(await readApiError(resp, '加载引擎列表失败'));
    return resp.json() as Promise<ProbeEngine[]>;
  },

  async createRun(token: string, engine: string, query: string): Promise<ProbeRun> {
    const resp = await fetch(`${API_BASE}/workbench/probe/runs`, {
      method: 'POST',
      headers: localizedHeaders({
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      }),
      body: JSON.stringify({ engine, query }),
    });
    if (!resp.ok) throw new Error(await readApiError(resp, '提交执行失败'));
    return resp.json() as Promise<ProbeRun>;
  },

  async getRun(token: string, id: number): Promise<ProbeRun> {
    const resp = await fetch(`${API_BASE}/workbench/probe/runs/${id}`, {
      headers: authHeaders(token),
    });
    if (!resp.ok) throw new Error(await readApiError(resp, '查询执行状态失败'));
    return resp.json() as Promise<ProbeRun>;
  },

  async listRuns(token: string, limit = 20): Promise<ProbeRun[]> {
    const resp = await fetch(`${API_BASE}/workbench/probe/runs?limit=${limit}`, {
      headers: authHeaders(token),
    });
    if (!resp.ok) throw new Error(await readApiError(resp, '加载执行记录失败'));
    return resp.json() as Promise<ProbeRun[]>;
  },

  // Recording: fetch the whole .webm with auth; the caller calls createObjectURL and hands it to <video> for playback
  async fetchVideoBlob(token: string, id: number): Promise<Blob> {
    const resp = await fetch(`${API_BASE}/workbench/probe/runs/${id}/video`, {
      headers: authHeaders(token),
    });
    if (!resp.ok) throw new Error(await readApiError(resp, '加载录像失败'));
    return resp.blob();
  },
};
