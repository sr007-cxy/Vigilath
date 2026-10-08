// Expanded-prompt CSV import / export (pure frontend, no backend).
// Fixed column order: prompt, seed term, scenario, monitored. Export includes a UTF-8 BOM so Excel opens it on double-click;
// import also accepts .txt (one per line, prompt column only). No papaparse - parsing 4-column RFC-4180 by hand is enough.

import { ALL_SCENES, type SceneType } from '../services/aiTelemetryApi';

export interface QueryCsvRow {
  text: string;
  seed: string;          // empty string = not assigned to a seed (falls into "Ungrouped" in the picker)
  scene: SceneType;
  picked: boolean;       // whether monitored; defaults to true
}

export const QUERY_CSV_ACCEPT = '.csv,.txt';

const HEADER_CELLS = ['提示词', '种子词', '场景', '是否监测'];
// First cell is one of these -> skip as header (supports hand-made files with English headers)
const HEADER_FIRST_CELL = new Set(['提示词', 'text', 'query', 'prompt', '问题']);

// Scenario column: accepts a key or the Chinese label (kept in sync with i18n topic.profile.monitor.scene.*.label);
// the reverse lookup table is hard-coded here so parsing does not depend on an i18n instance.
const SCENE_ALIASES: Record<string, SceneType> = {
  search: 'search', '搜索词': 'search', '搜索': 'search',
  qa: 'qa', '问答词': 'qa', '问答': 'qa',
  intent: 'intent', '意图词': 'intent', '意图': 'intent',
  brand: 'brand', '品牌词': 'brand', '品牌': 'brand',
};
const PICKED_FALSE = new Set(['否', 'no', 'false', '0', 'n', '未监测', '不监测']);

function csvCell(v: string): string {
  return /[",\n\r]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
}

export function serializeQueryCsv(
  rows: QueryCsvRow[],
  sceneLabel: (s: SceneType) => string,
): string {
  const lines = [HEADER_CELLS.map(csvCell).join(',')];
  for (const r of rows) {
    lines.push([
      r.text, r.seed, sceneLabel(r.scene), r.picked ? '是' : '否',
    ].map(csvCell).join(','));
  }
  return '\ufeff' + lines.join('\r\n') + '\r\n';
}

/** Minimal RFC-4180: handles commas / newlines / `""` escapes inside quotes; one string[] per row. */
function parseCsvRecords(input: string): string[][] {
  const records: string[][] = [];
  let row: string[] = [];
  let cell = '';
  let quoted = false;
  for (let i = 0; i < input.length; i++) {
    const ch = input[i];
    if (quoted) {
      if (ch === '"') {
        if (input[i + 1] === '"') { cell += '"'; i++; }
        else quoted = false;
      } else {
        cell += ch;
      }
      continue;
    }
    if (ch === '"') { quoted = true; continue; }
    if (ch === ',') { row.push(cell); cell = ''; continue; }
    if (ch === '\n') { row.push(cell); records.push(row); row = []; cell = ''; continue; }
    cell += ch;
  }
  if (cell !== '' || row.length > 0) { row.push(cell); records.push(row); }
  return records;
}

export function parseSceneCell(raw: string | undefined): SceneType {
  const key = (raw || '').trim().toLowerCase();
  const hit = SCENE_ALIASES[key] ?? SCENE_ALIASES[(raw || '').trim()];
  return hit && ALL_SCENES.includes(hit) ? hit : 'search';
}

/** Parse CSV / TXT text; dedupes by prompt within the file (first occurrence wins). */
export function parseQueryCsv(raw: string): QueryCsvRow[] {
  const normalized = raw.replace(/^\ufeff/, '').replace(/\r\n?/g, '\n');
  const records = parseCsvRecords(normalized);
  const out: QueryCsvRow[] = [];
  const seen = new Set<string>();
  records.forEach((cells, idx) => {
    const text = (cells[0] || '').trim();
    if (!text) return;
    if (idx === 0 && HEADER_FIRST_CELL.has(text.toLowerCase())) return;
    if (seen.has(text)) return;
    seen.add(text);
    const pickedCell = (cells[3] || '').trim().toLowerCase();
    out.push({
      text,
      seed: (cells[1] || '').trim(),
      scene: parseSceneCell(cells[2]),
      picked: !PICKED_FALSE.has(pickedCell),
    });
  });
  return out;
}

export function queryCsvFilename(topicLabel: string): string {
  const d = new Date();
  const ymd = `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}`;
  const label = (topicLabel || 'topic').trim().replace(/[\\/:*?"<>|]/g, '_').slice(0, 40) || 'topic';
  return `${label}-扩展提示词-${ymd}.csv`;
}

/** Trigger a browser download (same hidden <a download> technique as geoApi.downloadFixPackage). */
export function downloadTextFile(content: string, filename: string, mime = 'text/csv;charset=utf-8'): void {
  const blob = new Blob([content], { type: mime });
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = objectUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(objectUrl);
}
