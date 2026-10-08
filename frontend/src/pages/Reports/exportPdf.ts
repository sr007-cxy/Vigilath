// Full-page PDF export for report pages.
// Preferred: real screenshot by the backend headless browser (/reports/export-pdf; the rendering engine is a browser, so zero layout/chart distortion);
// Fallback: frontend html-to-image captures the container + jsPDF pagination (html2canvas is the fallback's fallback).

import { toCanvas } from 'html-to-image';
import { jsPDF } from 'jspdf';

const API_BASE = (import.meta.env.VITE_API_URL as string) || '/api';

// Backend real-screenshot export: returns true on success; false on failure (endpoint unavailable/timeout), letting the caller use the frontend fallback
export async function exportReportPdfServer(token: string, topicId: number, filename: string): Promise<boolean> {
  try {
    const resp = await fetch(`${API_BASE}/ai-telemetry/reports/export-pdf`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        topic_id: topicId,
        path: window.location.pathname + window.location.search,
        lang: localStorage.getItem('i18nextLng') || 'zh',
        theme: localStorage.getItem('theme') || '',
      }),
    });
    if (!resp.ok) return false;
    const blob = await resp.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
    URL.revokeObjectURL(a.href);
    return true;
  } catch {
    return false;
  }
}

// 1x1 transparent pixel: placeholder when external images (source favicons etc.) fail to load, so the whole export isn't aborted
const BLANK_PIXEL =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';

async function renderCanvas(el: HTMLElement): Promise<HTMLCanvasElement> {
  const backgroundColor = getComputedStyle(document.body).backgroundColor || '#ffffff';
  try {
    return await toCanvas(el, {
      pixelRatio: 2,
      backgroundColor,
      imagePlaceholder: BLANK_PIXEL,
    });
  } catch (err) {
    console.warn('[exportPdf] html-to-image render failed, falling back to html2canvas:', err);
    const { default: html2canvas } = await import('html2canvas');
    return await html2canvas(el, { scale: 2, useCORS: true, backgroundColor });
  }
}

// Blocks with scrollbars (the weekly report's "This week's response details" etc.) would only keep the visible screen in a static capture,
// so expand them to full height before export and return a restore function
function expandScrollers(root: HTMLElement): () => void {
  const saved: Array<[HTMLElement, string | null]> = [];
  root.querySelectorAll<HTMLElement>('*').forEach(node => {
    const oy = getComputedStyle(node).overflowY;
    if (oy === 'auto' || oy === 'scroll') {
      saved.push([node, node.getAttribute('style')]);
      node.style.maxHeight = 'none';
      node.style.height = 'auto';
      node.style.overflowY = 'visible';
    }
  });
  return () => saved.forEach(([node, style]) =>
    style === null ? node.removeAttribute('style') : node.setAttribute('style', style));
}

export async function exportElementPdf(el: HTMLElement, filename: string): Promise<void> {
  const restore = expandScrollers(el);
  let canvas: HTMLCanvasElement;
  try {
    canvas = await renderCanvas(el);
  } finally {
    restore();
  }
  if (!canvas.width || !canvas.height) throw new Error('导出画布为空');
  const pdf = new jsPDF({ orientation: 'p', unit: 'pt', format: 'a4' });
  const pageW = pdf.internal.pageSize.getWidth();
  const pageH = pdf.internal.pageSize.getHeight();
  const imgW = pageW;
  const imgH = (canvas.height / canvas.width) * imgW;
  const imgData = canvas.toDataURL('image/jpeg', 0.92);

  // Long pages are sliced into A4-height pages
  let rendered = 0;
  while (rendered < imgH) {
    if (rendered > 0) pdf.addPage();
    pdf.addImage(imgData, 'JPEG', 0, -rendered, imgW, imgH);
    rendered += pageH;
  }
  pdf.save(filename);
}
