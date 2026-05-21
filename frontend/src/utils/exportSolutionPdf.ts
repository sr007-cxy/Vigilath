// PDF 导出 — GEO 品牌增长战略方案.
// 沿用 pdfPrimitives 的 jsPDF + html2canvas 流水线 + CJK 字体栈,
// 不引新依赖。结构跟 AdminSolution 页面保持一致(6 段)。

import type { TFunction } from 'i18next';
import {
  blockWrapClose, blockWrapOpen, composeAndSavePdf, escapeHtml,
} from './pdfPrimitives';
import type {
  TopicStrategicSolution, SolutionDiagnosisCluster,
  SolutionSevenStep, SolutionKeywordTier, SolutionVisionItem,
  SolutionDiagnosisCheck,
} from '../services/adminReviewApi';

const SEVERITY_STYLE = {
  high: { c: '#991b1b', bg: '#fee2e2', label: '重点突破' },
  med:  { c: '#92400e', bg: '#fef3c7', label: '建议补强' },
  low:  { c: '#166534', bg: '#dcfce7', label: '已达标' },
} as const;

const SEVERITY_STYLE_EN = {
  high: { c: '#991b1b', bg: '#fee2e2', label: 'Priority gap' },
  med:  { c: '#92400e', bg: '#fef3c7', label: 'Reinforce' },
  low:  { c: '#166534', bg: '#dcfce7', label: 'On par' },
} as const;

const scoreRingColor = (s: number): string => {
  if (s >= 90) return '#22c55e';
  if (s >= 75) return '#06b6d4';
  if (s >= 60) return '#eab308';
  if (s >= 40) return '#f97316';
  return '#ef4444';
};

const buildCoverBlock = (sol: TopicStrategicSolution, t: TFunction, language: string): string => {
  const generatedAt = sol.updated_at
    ? new Date(sol.updated_at).toLocaleString(language.startsWith('zh') ? 'zh-CN' : 'en-US')
    : '';
  const bp = sol.brand_snapshot || null;
  const brandName = bp?.company_short_name || bp?.company_full_name || bp?.profile_name || '';
  return (
    blockWrapOpen('padding:6px 0 22px 0;border-bottom:3px solid #6366f1;margin-bottom:8px;') +
    `<div style="font-size:11px;letter-spacing:0.22em;color:#6366f1;text-transform:uppercase;font-weight:700;margin-bottom:8px;">GEO STRATEGY</div>` +
    `<div style="font-size:26px;font-weight:800;line-height:1.25;margin-bottom:6px;color:#0f172a;">${escapeHtml(t('admin.solution.title'))}</div>` +
    (brandName
      ? `<div style="font-size:18px;color:#334155;font-weight:600;margin-bottom:10px;">${escapeHtml(brandName)}</div>`
      : '') +
    `<table style="width:100%;font-size:11.5px;color:#334155;border-collapse:collapse;">` +
    `<tr><td style="padding:3px 0;width:110px;color:#94a3b8;">${escapeHtml(t('admin.solution.websiteLabel'))}</td><td style="padding:3px 0;color:#0f172a;word-break:break-all;">${escapeHtml(sol.website_url || '')}</td></tr>` +
    (bp?.industry
      ? `<tr><td style="padding:3px 0;color:#94a3b8;">${escapeHtml(t('admin.solution.brandFields.industry'))}</td><td style="padding:3px 0;color:#0f172a;">${escapeHtml(bp.industry)}</td></tr>`
      : '') +
    (bp?.service_geo
      ? `<tr><td style="padding:3px 0;color:#94a3b8;">${escapeHtml(t('admin.solution.brandFields.service_geo'))}</td><td style="padding:3px 0;color:#0f172a;">${escapeHtml(bp.service_geo)}</td></tr>`
      : '') +
    `<tr><td style="padding:3px 0;color:#94a3b8;">${escapeHtml(t('admin.solution.lastGeneratedAt'))}</td><td style="padding:3px 0;color:#0f172a;">${escapeHtml(generatedAt)}</td></tr>` +
    (sol.llm_model
      ? `<tr><td style="padding:3px 0;color:#94a3b8;">${escapeHtml(t('admin.solution.llmModel'))}</td><td style="padding:3px 0;color:#0f172a;">${escapeHtml(sol.llm_model)}</td></tr>`
      : '') +
    `</table>` +
    blockWrapClose
  );
};

const buildSectionHeading = (text: string): string =>
  blockWrapOpen('padding:14px 0 6px 0;') +
  `<div style="font-size:17px;font-weight:800;color:#0f172a;border-left:4px solid #6366f1;padding-left:10px;">${escapeHtml(text)}</div>` +
  blockWrapClose;

const buildBrandBlock = (sol: TopicStrategicSolution, t: TFunction): string => {
  const bp = sol.brand_snapshot;
  if (!bp) return '';
  const rows: [string, string | string[] | null | undefined][] = [
    [t('admin.solution.brandFields.core_business_lines'), bp.core_business_lines],
    [t('admin.solution.brandFields.target_scenarios'), bp.target_scenarios],
    [t('admin.solution.brandFields.brand_diff_tags'), bp.brand_diff_tags],
    [t('admin.solution.brandFields.brand_slogan'), bp.brand_slogan],
    [t('admin.solution.brandFields.core_message'), bp.core_message],
    [t('admin.solution.brandFields.core_service_overview'), bp.core_service_overview],
  ];
  const visible = rows.filter(([, v]) => Array.isArray(v) ? v.length > 0 : !!(v && String(v).trim()));
  if (visible.length === 0) return '';
  const html =
    visible.map(([k, v]) => {
      const text = Array.isArray(v) ? v.join('、') : String(v || '');
      return (
        `<tr>` +
        `<td style="padding:6px 12px 6px 0;color:#94a3b8;width:130px;vertical-align:top;font-size:11.5px;">${escapeHtml(k)}</td>` +
        `<td style="padding:6px 0;color:#0f172a;line-height:1.7;font-size:12px;">${escapeHtml(text)}</td>` +
        `</tr>`
      );
    }).join('');
  return (
    blockWrapOpen('padding:4px 0 6px 0;') +
    `<table style="width:100%;border-collapse:collapse;">${html}</table>` +
    blockWrapClose
  );
};

const buildScoreBlock = (sol: TopicStrategicSolution, t: TFunction): string => {
  const d = sol.diagnosis;
  if (!d) return '';
  const radius = 50;
  const stroke = 8;
  const circumference = 2 * Math.PI * radius;
  const dashOffset = circumference * (1 - d.score / 100);
  const ringColor = scoreRingColor(d.score);
  const ringHtml =
    `<svg width="120" height="120" viewBox="0 0 120 120" xmlns="http://www.w3.org/2000/svg">` +
    `<circle cx="60" cy="60" r="${radius}" fill="none" stroke="#e2e8f0" stroke-width="${stroke}"/>` +
    `<circle cx="60" cy="60" r="${radius}" fill="none" stroke="${ringColor}" stroke-width="${stroke}" ` +
    `stroke-linecap="round" stroke-dasharray="${circumference}" stroke-dashoffset="${dashOffset}" ` +
    `transform="rotate(-90 60 60)"/></svg>`;
  return (
    blockWrapOpen('padding:6px 0 8px 0;') +
    `<div style="display:flex;gap:14px;align-items:stretch;">` +
    `<div style="flex-shrink:0;width:170px;background:linear-gradient(135deg,#0f172a,#312e81);color:#fff;border-radius:12px;padding:16px 14px;text-align:center;display:flex;flex-direction:column;align-items:center;justify-content:center;">` +
    `<div style="font-size:10px;letter-spacing:0.16em;color:#a5b4fc;text-transform:uppercase;margin-bottom:8px;">${escapeHtml(t('admin.solution.scoreLabel'))}</div>` +
    `<div style="position:relative;width:120px;height:120px;">${ringHtml}` +
    `<div style="position:absolute;top:0;left:0;width:120px;height:120px;display:flex;flex-direction:column;align-items:center;justify-content:center;">` +
    `<div style="font-size:34px;font-weight:800;line-height:1;color:#fff;position:relative;top:-3px;">${d.score}</div>` +
    `<div style="font-size:10px;color:#a5b4fc;">/ 100</div>` +
    `</div></div>` +
    `<div style="margin-top:8px;font-size:14px;font-weight:700;color:#a5b4fc;">${escapeHtml(d.grade || '')}</div>` +
    `</div>` +
    `<div style="flex:1;display:flex;gap:8px;flex-wrap:wrap;">` +
    `<div style="flex:1;min-width:96px;background:#dcfce7;border-radius:8px;padding:10px;text-align:center;"><div style="font-size:20px;font-weight:800;color:#166534;">${d.pass_count}</div><div style="font-size:10px;color:#166534;font-weight:600;margin-top:2px;">${escapeHtml(t('admin.solution.passLabel'))}</div></div>` +
    `<div style="flex:1;min-width:96px;background:#fef3c7;border-radius:8px;padding:10px;text-align:center;"><div style="font-size:20px;font-weight:800;color:#92400e;">${d.warn_count}</div><div style="font-size:10px;color:#92400e;font-weight:600;margin-top:2px;">${escapeHtml(t('admin.solution.warnLabel'))}</div></div>` +
    `<div style="flex:1;min-width:96px;background:#fee2e2;border-radius:8px;padding:10px;text-align:center;"><div style="font-size:20px;font-weight:800;color:#991b1b;">${d.fail_count}</div><div style="font-size:10px;color:#991b1b;font-weight:600;margin-top:2px;">${escapeHtml(t('admin.solution.failLabel'))}</div></div>` +
    `<div style="flex:1;min-width:96px;background:#dbeafe;border-radius:8px;padding:10px;text-align:center;"><div style="font-size:20px;font-weight:800;color:#1e40af;">${d.info_count}</div><div style="font-size:10px;color:#1e40af;font-weight:600;margin-top:2px;">${escapeHtml(t('admin.solution.infoLabel'))}</div></div>` +
    `</div>` +
    `</div>` +
    blockWrapClose
  );
};

const buildClusterBlock = (c: SolutionDiagnosisCluster, language: string): string => {
  const sty = (language.startsWith('zh') ? SEVERITY_STYLE : SEVERITY_STYLE_EN)[c.severity] || SEVERITY_STYLE.low;
  const bullets = (c.bullets || []).slice(0, 6).map(b =>
    `<li style="font-size:11.5px;color:#475569;line-height:1.7;margin-bottom:2px;">${escapeHtml(b)}</li>`,
  ).join('');
  return (
    blockWrapOpen('padding:3px 0;') +
    `<div style="border:1px solid #e5e7eb;border-radius:10px;padding:12px 14px;background:#fff;">` +
    `<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;flex-wrap:wrap;">` +
    `<div style="font-size:13.5px;font-weight:700;color:#0f172a;">${escapeHtml(c.title_zh)}</div>` +
    `<span style="font-size:10.5px;font-weight:700;padding:2px 8px;border-radius:999px;background:${sty.bg};color:${sty.c};">${escapeHtml(sty.label)}</span>` +
    `</div>` +
    (c.summary
      ? `<div style="font-size:12px;color:#334155;line-height:1.75;margin-bottom:6px;">${escapeHtml(c.summary)}</div>`
      : '') +
    (bullets ? `<ul style="margin:4px 0 0 18px;padding:0;list-style:disc;">${bullets}</ul>` : '') +
    `</div>` +
    blockWrapClose
  );
};

const buildExecutionLayersBlock = (sol: TopicStrategicSolution, t: TFunction): string => {
  const layers = sol.diagnosis?.execution_layers || [];
  if (layers.length === 0) return '';
  const cells = layers.map((l) => {
    const clustersHtml = (l.clusters || []).map(c =>
      `<div style="font-size:11px;color:#475569;line-height:1.6;margin-top:3px;">· ${escapeHtml(c)}</div>`,
    ).join('');
    return (
      `<div style="flex:1;min-width:170px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:12px;box-sizing:border-box;">` +
      `<div style="font-size:13px;font-weight:700;color:#312e81;margin-bottom:4px;">${escapeHtml(l.title_zh)}</div>` +
      `<div style="font-size:11px;color:#64748b;line-height:1.7;margin-bottom:6px;">${escapeHtml(l.hint || '')}</div>` +
      clustersHtml +
      `</div>`
    );
  }).join('');
  return (
    blockWrapOpen('padding:2px 0 4px 0;') +
    `<div style="font-size:11.5px;color:#475569;margin-bottom:8px;">${escapeHtml(t('admin.solution.executionLayersHint'))}</div>` +
    `<div style="display:flex;gap:10px;flex-wrap:wrap;">${cells}</div>` +
    blockWrapClose
  );
};

const buildSevenStepsBlock = (steps: SolutionSevenStep[], t: TFunction): string => {
  if (steps.length === 0) return '';
  const rows = steps.map((s) => {
    return (
      `<tr>` +
      `<td style="padding:8px 8px;border-bottom:1px solid #e5e7eb;font-weight:700;color:#312e81;text-align:center;font-size:13px;vertical-align:top;">${s.step}</td>` +
      `<td style="padding:8px 8px;border-bottom:1px solid #e5e7eb;font-weight:600;color:#0f172a;font-size:12px;vertical-align:top;">${escapeHtml(s.name)}</td>` +
      `<td style="padding:8px 8px;border-bottom:1px solid #e5e7eb;color:#475569;font-size:11.5px;line-height:1.7;vertical-align:top;">${escapeHtml(s.core_goal)}</td>` +
      `<td style="padding:8px 8px;border-bottom:1px solid #e5e7eb;color:#0f172a;font-size:11.5px;line-height:1.7;vertical-align:top;">${escapeHtml(s.core_action)}</td>` +
      `<td style="padding:8px 8px;border-bottom:1px solid #e5e7eb;color:#475569;font-size:11.5px;line-height:1.7;vertical-align:top;">${escapeHtml(s.output_value)}</td>` +
      `</tr>`
    );
  }).join('');
  return (
    blockWrapOpen('padding:4px 0;') +
    `<table style="width:100%;border-collapse:collapse;background:#fff;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden;font-size:11.5px;">` +
    `<thead><tr style="background:#eef2ff;">` +
    `<th style="padding:8px 8px;text-align:center;font-size:10.5px;color:#312e81;font-weight:700;width:40px;">${escapeHtml(t('admin.solution.stepCol.step'))}</th>` +
    `<th style="padding:8px 8px;text-align:left;font-size:10.5px;color:#312e81;font-weight:700;width:110px;">${escapeHtml(t('admin.solution.stepCol.name'))}</th>` +
    `<th style="padding:8px 8px;text-align:left;font-size:10.5px;color:#312e81;font-weight:700;width:140px;">${escapeHtml(t('admin.solution.stepCol.core_goal'))}</th>` +
    `<th style="padding:8px 8px;text-align:left;font-size:10.5px;color:#312e81;font-weight:700;">${escapeHtml(t('admin.solution.stepCol.core_action'))}</th>` +
    `<th style="padding:8px 8px;text-align:left;font-size:10.5px;color:#312e81;font-weight:700;width:140px;">${escapeHtml(t('admin.solution.stepCol.output_value'))}</th>` +
    `</tr></thead><tbody>${rows}</tbody></table>` +
    blockWrapClose
  );
};

const buildKeywordTierBlock = (tier: SolutionKeywordTier): string => {
  const chips = tier.keywords.map(k =>
    `<span style="display:inline-block;background:#eef2ff;color:#312e81;border-radius:6px;padding:3px 8px;margin:0 4px 4px 0;font-size:11px;">${escapeHtml(k)}</span>`,
  ).join('');
  return (
    blockWrapOpen('padding:3px 0;') +
    `<div style="border:1px solid #e5e7eb;border-radius:10px;padding:12px 14px;background:#fff;">` +
    `<div style="font-size:13px;font-weight:700;color:#0f172a;margin-bottom:4px;">${escapeHtml(tier.title_zh)}</div>` +
    (tier.description
      ? `<div style="font-size:11.5px;color:#64748b;line-height:1.7;margin-bottom:8px;">${escapeHtml(tier.description)}</div>`
      : '') +
    (chips || `<div style="font-size:11px;color:#94a3b8;">—</div>`) +
    `</div>` +
    blockWrapClose
  );
};

const buildVisionBlock = (items: SolutionVisionItem[]): string => {
  if (items.length === 0) return '';
  const html = items.map((v) => (
    `<div style="border:1px solid #e5e7eb;border-radius:10px;padding:12px 14px;background:#f8fafc;margin-bottom:6px;">` +
    `<div style="font-size:13px;font-weight:700;color:#312e81;margin-bottom:6px;">${escapeHtml(v.title)}</div>` +
    `<div style="font-size:12px;color:#334155;line-height:1.85;">${escapeHtml(v.body)}</div>` +
    `</div>`
  )).join('');
  return (
    blockWrapOpen('padding:4px 0;') + html + blockWrapClose
  );
};

const buildDetailsTableBlock = (checks: SolutionDiagnosisCheck[]): string => {
  if (checks.length === 0) return '';
  const rows = checks.map((c) => (
    `<tr>` +
    `<td style="padding:5px 8px;border-bottom:1px solid #e5e7eb;font-size:11px;color:#334155;vertical-align:top;">${escapeHtml(c.category)}</td>` +
    `<td style="padding:5px 8px;border-bottom:1px solid #e5e7eb;font-size:11px;vertical-align:top;font-weight:700;color:${
      c.status === 'PASS' ? '#166534' :
      c.status === 'WARN' ? '#92400e' :
      c.status === 'FAIL' ? '#991b1b' : '#1e40af'
    };">${escapeHtml(c.status)}</td>` +
    `<td style="padding:5px 8px;border-bottom:1px solid #e5e7eb;font-size:11px;color:#0f172a;line-height:1.6;vertical-align:top;">${escapeHtml(c.message)}</td>` +
    `</tr>`
  )).join('');
  return (
    blockWrapOpen('padding:4px 0;') +
    `<table style="width:100%;border-collapse:collapse;background:#fff;border:1px solid #e5e7eb;border-radius:8px;overflow:hidden;">` +
    `<tbody>${rows}</tbody></table>` +
    blockWrapClose
  );
};

export async function exportSolutionPdf(
  sol: TopicStrategicSolution,
  t: TFunction,
  language: string,
): Promise<void> {
  const blocks: string[] = [];

  blocks.push(buildCoverBlock(sol, t, language));

  if (sol.brand_snapshot) {
    blocks.push(buildSectionHeading(t('admin.solution.section.brand')));
    blocks.push(buildBrandBlock(sol, t));
  }

  if (sol.diagnosis) {
    blocks.push(buildSectionHeading(t('admin.solution.section.diagnosis')));
    blocks.push(buildScoreBlock(sol, t));
    for (const c of sol.diagnosis.clusters) {
      blocks.push(buildClusterBlock(c, language));
    }

    blocks.push(buildSectionHeading(t('admin.solution.section.executionLayers')));
    blocks.push(buildExecutionLayersBlock(sol, t));
  }

  if (sol.seven_steps.length > 0) {
    blocks.push(buildSectionHeading(t('admin.solution.section.sevenSteps')));
    blocks.push(buildSevenStepsBlock(sol.seven_steps, t));
  }

  if (sol.keyword_tiers.length > 0) {
    blocks.push(buildSectionHeading(t('admin.solution.section.keywords')));
    for (const tier of sol.keyword_tiers) {
      blocks.push(buildKeywordTierBlock(tier));
    }
  }

  if (sol.vision.length > 0) {
    blocks.push(buildSectionHeading(t('admin.solution.section.vision')));
    blocks.push(buildVisionBlock(sol.vision));
  }

  if (sol.diagnosis && sol.diagnosis.all_checks.length > 0) {
    blocks.push(buildSectionHeading(t('admin.solution.section.details')));
    blocks.push(buildDetailsTableBlock(sol.diagnosis.all_checks));
  }

  const brand =
    sol.brand_snapshot?.company_short_name ||
    sol.brand_snapshot?.company_full_name ||
    sol.brand_snapshot?.profile_name ||
    'brand';
  const safeName = brand.replace(/[^a-zA-Z0-9一-龥_-]/g, '-').slice(0, 60);
  const baseName = t('admin.solution.title');
  const headerRight = `${baseName} · ${sol.website_url || ''}`;

  await composeAndSavePdf(
    blocks,
    { rightHeaderText: headerRight, t },
    `${safeName}-${baseName}.pdf`,
  );
}
