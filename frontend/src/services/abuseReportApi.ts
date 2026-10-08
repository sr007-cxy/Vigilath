// Abuse report API -- item 13 of the filing correction list.
// Backend geo/api/abuse_report.py.
//   /report/*        public, **no token** (reporters are mostly not our users)
//   /admin/reports/* workbench, require_workbench
// Pages: pages/Report.tsx (user side), pages/Admin/AbuseReports.tsx (admin handling)

import { localizedHeaders, readApiError } from './apiError';

const API_BASE = (import.meta.env.VITE_API_URL as string) || '/api';

export interface ReportCategory { key: string; label: string }

export interface ReportMeta {
  categories: ReportCategory[];
  /** Published report email; the page and the user agreement reference the same value */
  abuse_email: string;
  /** Committed handling deadline (calendar days) */
  sla_days: number;
  max_attach_mb: number;
  allowed_ext: string[];
}

export interface ReportSubmitResult {
  report_no: string;
  status: string;
  status_label: string;
  due_at?: string | null;
  message: string;
}

/** Progress visible to the reporter -- excludes internal handling records */
export interface ReportPublic {
  report_no: string;
  category: string;
  category_label: string;
  status: string;
  status_label: string;
  created_at: string;
  due_at?: string | null;
  resolved_at?: string | null;
  reply: string;
}

/** Admin ticket -- adds contact info, source IP and internal handling records over ReportPublic */
export interface ReportAdmin extends Omit<ReportPublic, 'reply'> {
  id: number;
  target_url: string;
  target_trace_no: string;
  description: string;
  contact_name: string;
  contact_email: string;
  contact_phone: string;
  has_attachment: boolean;
  attachment_name: string;
  reporter_user_id?: number | null;
  client_ip: string;
  /** Not closed and past the committed deadline */
  overdue: boolean;
  handling: string;
  reply: string;
  handler_name: string;
  accepted_at?: string | null;
}

export interface ReportList {
  total: number;
  open_count: number;
  overdue_count: number;
  items: ReportAdmin[];
}

async function pub<T>(method: string, path: string, body?: BodyInit): Promise<T> {
  // Public endpoints carry no Authorization -- anonymous reporting is a hard requirement
  const resp = await fetch(`${API_BASE}${path}`, {
    method,
    headers: localizedHeaders({}),
    ...(body !== undefined ? { body } : {}),
  });
  if (!resp.ok) throw new Error(await readApiError(resp, `Request ${method} ${path} failed`));
  return resp.json() as Promise<T>;
}

async function adm<T>(method: string, path: string, token: string, body?: unknown): Promise<T> {
  const init: RequestInit = {
    method,
    headers: localizedHeaders({
      Authorization: `Bearer ${token}`,
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    }),
  };
  if (body !== undefined) init.body = JSON.stringify(body);
  const resp = await fetch(`${API_BASE}/admin/reports${path}`, init);
  if (!resp.ok) throw new Error(await readApiError(resp, `Request ${method} ${path} failed`));
  return resp.json() as Promise<T>;
}

export const abuseReportApi = {
  meta: () => pub<ReportMeta>('GET', '/report/meta'),

  /** Submit a report. Multipart because attachments are accepted; logged-in users send their token so the account can be linked */
  submit(form: FormData, token?: string) {
    return fetch(`${API_BASE}/report`, {
      method: 'POST',
      headers: localizedHeaders(token ? { Authorization: `Bearer ${token}` } : {}),
      body: form,
    }).then(async resp => {
      if (!resp.ok) throw new Error(await readApiError(resp, '提交失败'));
      return resp.json() as Promise<ReportSubmitResult>;
    });
  },

  /** Reporters check progress by ticket number + the email used at submission */
  query(reportNo: string, email: string) {
    const qs = new URLSearchParams({ email });
    return pub<ReportPublic>('GET', `/report/${encodeURIComponent(reportNo)}?${qs}`);
  },

  list: (token: string, status = '') =>
    adm<ReportList>('GET', status ? `?status=${status}` : '', token),
  accept: (id: number, token: string) =>
    adm<ReportAdmin>('POST', `/${id}/accept`, token, {}),
  resolve: (id: number, token: string, handling: string, reply: string) =>
    adm<ReportAdmin>('POST', `/${id}/resolve`, token, { handling, reply }),
  reject: (id: number, token: string, handling: string, reply: string) =>
    adm<ReportAdmin>('POST', `/${id}/reject`, token, { handling, reply }),
  attachmentUrl: (id: number) => `${API_BASE}/admin/reports/${id}/attachment`,
};
