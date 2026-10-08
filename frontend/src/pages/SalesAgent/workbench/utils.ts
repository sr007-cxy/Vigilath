// Value access, time, text and clipboard utilities unrelated to Sales Agent business logic.
import { toast } from '../../../components/ui/toast';

export async function runWithConcurrency<T>(items: T[], limit: number, worker: (item: T, index: number) => Promise<void>) {
  let cursor = 0;
  const workers = Array.from({ length: Math.min(Math.max(1, limit), items.length) }, async () => {
    while (cursor < items.length) {
      const index = cursor;
      cursor += 1;
      await worker(items[index], index);
    }
  });
  await Promise.all(workers);
}

export function isJwtExpired(t: string): boolean {
  try {
    const b = t.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    const pad = b.length % 4 ? '='.repeat(4 - (b.length % 4)) : '';
    const p = JSON.parse(atob(b + pad));
    return typeof p.exp === 'number' && p.exp * 1000 <= Date.now();
  } catch { return false; }
}

export function linkifyPlainText(text: string): string {
  return (text || '').replace(/(?<!\]\()https?:\/\/[^\s)）]+/g, url => `[${url}](${url})`);
}

export function displayDate(raw?: string | null): Date | null {
  if (!raw) return null;
  try {
    const normalized = /^\d{4}-\d{2}-\d{2}T/.test(raw) && !/(Z|[+-]\d{2}:?\d{2})$/i.test(raw) ? `${raw}Z` : raw;
    const d = new Date(normalized);
    return Number.isNaN(d.getTime()) ? null : d;
  } catch { return null; }
}

export function fmtTime(raw?: string | null): string {
  const d = displayDate(raw);
  if (!d) return raw || '';
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

export function isTodayTime(raw?: string | null): boolean {
  const d = displayDate(raw);
  if (!d) return false;
  const now = new Date();
  return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
}

export function textOf(v: unknown, fallback = '-'): string {
  if (v === null || v === undefined || v === '') return fallback;
  if (Array.isArray(v)) return v.map(x => textOf(x, '')).filter(Boolean).join('、') || fallback;
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

export function shortText(value: unknown, max = 120): string {
  const text = textOf(value, '');
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

export function jsonArrayTextParts(content: string): string[] {
  const raw = (content || '').trim();
  if (!raw.startsWith('[')) return [];
  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.map(item => String(item || '').trim()).filter(Boolean);
  } catch {
    return [];
  }
}

export function uniqueTexts(values: string[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  values.forEach(value => {
    const text = String(value || '').trim();
    const key = text.toLowerCase();
    if (!text || seen.has(key)) return;
    seen.add(key);
    result.push(text);
  });
  return result;
}

export function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

export function sanitizeEmailHtml(value: unknown): string {
  const raw = textOf(value, '').trim();
  if (!raw || typeof document === 'undefined') return '';
  const allowedTags = new Set(['A', 'B', 'BR', 'DIV', 'EM', 'H1', 'H2', 'H3', 'HR', 'LI', 'OL', 'P', 'SPAN', 'STRONG', 'TABLE', 'TBODY', 'TD', 'TH', 'THEAD', 'TR', 'UL']);
  const allowedAttrs = new Set(['href', 'target', 'rel', 'colspan', 'rowspan']);
  const template = document.createElement('template');
  template.innerHTML = raw;
  template.content.querySelectorAll('*').forEach(el => {
    if (!allowedTags.has(el.tagName)) {
      el.replaceWith(document.createTextNode(el.textContent || ''));
      return;
    }
    [...el.attributes].forEach(attr => {
      const name = attr.name.toLowerCase();
      const val = attr.value || '';
      if (!allowedAttrs.has(name) || name.startsWith('on')) el.removeAttribute(attr.name);
      if (name === 'href' && !/^(https?:\/\/|mailto:|#)/i.test(val)) el.setAttribute('href', '#');
      if (name === 'style' && /(url\s*\(|expression\s*\(|javascript:|behavior\s*:)/i.test(val)) el.removeAttribute(attr.name);
    });
    if (el.tagName === 'A') {
      el.setAttribute('target', '_blank');
      el.setAttribute('rel', 'noreferrer');
      el.setAttribute('style', 'color: var(--accent-primary); text-decoration: underline; word-break: break-all;');
    } else if (el.tagName === 'P' || el.tagName === 'DIV') {
      el.setAttribute('style', 'margin: 0 0 14px; line-height: 1.86; color: var(--text-primary);');
    } else if (el.tagName === 'H1' || el.tagName === 'H2' || el.tagName === 'H3') {
      el.setAttribute('style', 'margin: 18px 0 10px; line-height: 1.45; font-size: 16px; font-weight: 800; color: var(--text-primary);');
    } else if (el.tagName === 'UL' || el.tagName === 'OL') {
      el.setAttribute('style', 'margin: 6px 0 14px; padding-left: 20px; color: var(--text-primary);');
    } else if (el.tagName === 'LI') {
      el.setAttribute('style', 'margin: 4px 0; line-height: 1.76; color: var(--text-primary);');
    } else if (el.tagName === 'SPAN' || el.tagName === 'STRONG' || el.tagName === 'B' || el.tagName === 'EM') {
      el.setAttribute('style', 'color: inherit;');
    } else if (el.tagName === 'TABLE') {
      el.setAttribute('style', 'width: 100%; border-collapse: collapse; margin: 10px 0 14px; font-size: 13px; color: var(--text-primary);');
    } else if (el.tagName === 'TD' || el.tagName === 'TH') {
      el.setAttribute('style', 'border: 1px solid var(--border-color); padding: 6px 8px; text-align: left; color: var(--text-primary);');
    }
  });
  return template.innerHTML;
}

export async function copyTextToClipboard(text: string, label = '内容') {
  const value = text.trim();
  if (!value) return;
  try {
    await navigator.clipboard.writeText(value);
    toast.success(`已复制${label}`);
  } catch {
    toast.error(`复制${label}失败`);
  }
}

export function asList<T = unknown>(value: unknown): T[] {
  return Array.isArray(value) ? value as T[] : [];
}

export function numberOf(v: unknown, fallback = 0): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

export function looksLikeEmail(value: string): boolean {
  return /[^@\s]+@[^@\s]+\.[^@\s]+/.test(value || '');
}

export function friendlyError(msg: string): string {
  const text = (msg || '').trim();
  if (/not found/i.test(text)) return 'Sales Agent 服务暂未连接,请确认后端服务已启动。';
  if (/未登录|过期|401/.test(text)) return '请先登录后再使用 Sales Agent。';
  if (/503|暂不可用/i.test(text)) return 'Sales Agent 服务暂不可用,请稍后再试。';
  if (/model|provider|pydantic|openrouter|openai|anthropic|deepseek|qwen|moonshot|glm|siliconflow|api[_ -]?key|token|traceback|exception|source_report|detection_records|full_diagnosis|context=|模型|供应商|密钥|令牌/i.test(text)) {
    return 'Sales Agent 本次处理失败,请稍后重试或换个问题。';
  }
  if (!text || text.length > 240) return '请求失败,请稍后再试。';
  return text;
}

export function boolOf(v: unknown, fallback = false): boolean {
  if (typeof v === 'boolean') return v;
  if (typeof v === 'string') return ['true', '1', 'yes'].includes(v.toLowerCase());
  if (typeof v === 'number') return v !== 0;
  return fallback;
}
