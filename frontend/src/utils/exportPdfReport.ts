import jsPDF from 'jspdf';
import html2canvas from 'html2canvas';
import type { TFunction } from 'i18next';
import type { GeoTestResult, CheckResult } from '../types/geo';

type GroupStat = {
  tab: string;
  total: number;
  passed: number;
  failed: number;
  warned: number;
  info: number;
  passRate: number;
  isPaid: boolean;
  isTabLocked: boolean;
};

interface ExportArgs {
  result: GeoTestResult;
  t: TFunction;
  language: string;
  groupStats: GroupStat[];
  categoryGroups: Record<string, string[]>;
  checksByCategory: Record<string, CheckResult[]>;
  otherCategories: string[];
}

const STATUS_COLOR: Record<string, { bg: string; fg: string; label_zh: string; label_en: string }> = {
  PASS: { bg: '#dcfce7', fg: '#166534', label_zh: '通过', label_en: 'Pass' },
  WARN: { bg: '#fef3c7', fg: '#92400e', label_zh: '警告', label_en: 'Warn' },
  FAIL: { bg: '#fee2e2', fg: '#991b1b', label_zh: '失败', label_en: 'Fail' },
  INFO: { bg: '#dbeafe', fg: '#1e40af', label_zh: '信息', label_en: 'Info' },
};

const STATUS_RANK: Record<string, number> = { FAIL: 0, WARN: 1, INFO: 2, PASS: 3 };

const escapeHtml = (s: string): string =>
  String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');

const interpretScore = (score: number, t: TFunction): string => {
  if (score >= 90) return t('result.pdfReport.scoreLevels.excellent');
  if (score >= 75) return t('result.pdfReport.scoreLevels.good');
  if (score >= 60) return t('result.pdfReport.scoreLevels.average');
  if (score >= 40) return t('result.pdfReport.scoreLevels.poor');
  return t('result.pdfReport.scoreLevels.critical');
};

const statusBadge = (status: string, lang: string): string => {
  const color = STATUS_COLOR[status] || STATUS_COLOR.INFO;
  const label = lang.startsWith('zh') ? color.label_zh : color.label_en;
  return `<span style="display:inline-block;padding:2px 10px;border-radius:999px;font-size:11px;font-weight:600;background:${color.bg};color:${color.fg};">${label}</span>`;
};

const tierLabel = (tier: string | null | undefined, lang: string): string => {
  const slug = (tier || 'free').toLowerCase();
  const map_zh: Record<string, string> = {
    free: '免费版',
    pro: '检测会员',
    starter: 'Starter',
    growth: 'Growth',
    scale: 'Scale',
  };
  const map_en: Record<string, string> = {
    free: 'Free',
    pro: 'Detector',
    starter: 'Starter',
    growth: 'Growth',
    scale: 'Scale',
  };
  return (lang.startsWith('zh') ? map_zh : map_en)[slug] || slug.toUpperCase();
};

const buildReportHtml = (args: ExportArgs): string => {
  const { result, t, language, groupStats, categoryGroups, checksByCategory, otherCategories } = args;
  const score = result.score || 0;
  const grade = result.grade || 'F';
  const summary = result.summary || { pass_count: 0, warn_count: 0, fail_count: 0, info_count: 0, total_checks: 0 };
  const lockedSet = new Set(result.locked_categories || []);
  const lockedCount = lockedSet.size;
  const generatedAt = new Date().toLocaleString(language.startsWith('zh') ? 'zh-CN' : 'en-US');

  const tabLabel = (tab: string) => t(`result.categories.${tab}`);
  const catLabel = (cat: string) => t(`result.categoryLabels.${cat}`, { defaultValue: cat });
  const catDesc = (cat: string) => t(`result.categoryDescriptions.${cat}`, { defaultValue: '' });

  // ----- Group breakdown rows
  const groupRowsHtml = groupStats
    .filter((g) => g.total > 0)
    .map((g) => {
      const barFill = `${g.passRate}%`;
      return `
        <tr>
          <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;font-weight:600;color:#0f172a;">${escapeHtml(tabLabel(g.tab))}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;text-align:center;color:#0f172a;">${g.total}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;text-align:center;color:#166534;font-weight:600;">${g.passed}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;text-align:center;color:#92400e;font-weight:600;">${g.warned}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;text-align:center;color:#991b1b;font-weight:600;">${g.failed}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;width:180px;">
            <div style="background:#e5e7eb;border-radius:999px;height:8px;width:100%;overflow:hidden;">
              <div style="background:linear-gradient(90deg,#06b6d4,#8b5cf6);height:100%;width:${barFill};"></div>
            </div>
            <div style="font-size:11px;color:#475569;margin-top:4px;">${g.passRate}%</div>
          </td>
        </tr>`;
    })
    .join('');

  // ----- Detailed sections per group
  const allTabs = [...Object.keys(categoryGroups), ...(otherCategories.length ? ['other'] : [])];
  const detailSectionsHtml = allTabs
    .map((tab) => {
      const cats = tab === 'other' ? otherCategories : categoryGroups[tab] || [];
      const visibleCats = cats.filter((c) => !lockedSet.has(c) && (checksByCategory[c]?.length ?? 0) > 0);
      if (visibleCats.length === 0) return '';

      const catBlocks = visibleCats
        .map((cat) => {
          const checks = checksByCategory[cat] || [];
          const desc = catDesc(cat);
          const itemsHtml = checks
            .map((c) => {
              const fix = c.fix && c.fix.trim() ? c.fix.trim() : t('result.pdfReport.noFix');
              return `
                <div style="border:1px solid #e5e7eb;border-radius:10px;padding:14px 16px;margin-bottom:10px;background:#fff;">
                  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;gap:12px;">
                    <div style="font-size:13px;font-weight:600;color:#0f172a;flex:1;">${escapeHtml(c.message || '')}</div>
                    <div>${statusBadge(c.status, language)}</div>
                  </div>
                  ${
                    c.status !== 'PASS'
                      ? `<div style="font-size:12px;color:#475569;line-height:1.6;">
                          <span style="font-weight:600;color:#0f172a;">${escapeHtml(t('result.pdfReport.fixLabel'))}：</span>${escapeHtml(fix)}
                        </div>`
                      : ''
                  }
                </div>`;
            })
            .join('');

          return `
            <div style="margin-bottom:22px;page-break-inside:avoid;">
              <div style="display:flex;align-items:baseline;gap:10px;margin-bottom:6px;">
                <div style="font-size:15px;font-weight:700;color:#0f172a;">${escapeHtml(catLabel(cat))}</div>
                <div style="font-size:11px;color:#94a3b8;">${escapeHtml(tabLabel(tab))}</div>
              </div>
              ${
                desc
                  ? `<div style="font-size:12px;color:#475569;line-height:1.7;margin-bottom:10px;padding:10px 12px;background:#f1f5f9;border-left:3px solid #06b6d4;border-radius:4px;">${escapeHtml(desc)}</div>`
                  : ''
              }
              ${itemsHtml}
            </div>`;
        })
        .join('');

      return `
        <div style="margin-bottom:18px;">
          <div style="font-size:16px;font-weight:700;color:#0ea5e9;border-bottom:2px solid #0ea5e9;padding-bottom:6px;margin-bottom:14px;">${escapeHtml(tabLabel(tab))}</div>
          ${catBlocks}
        </div>`;
    })
    .join('');

  // ----- Top fixes (sorted by severity)
  const failsAndWarns = (result.checks || [])
    .filter((c) => c.status === 'FAIL' || c.status === 'WARN')
    .sort((a, b) => (STATUS_RANK[a.status] ?? 9) - (STATUS_RANK[b.status] ?? 9))
    .slice(0, 12);

  const topFixesHtml = failsAndWarns.length
    ? failsAndWarns
        .map((c, idx) => {
          const fix = c.fix && c.fix.trim() ? c.fix.trim() : t('result.pdfReport.noFix');
          return `
            <div style="display:flex;gap:12px;margin-bottom:12px;padding:12px 14px;border:1px solid #e5e7eb;border-radius:10px;background:#fff;page-break-inside:avoid;">
              <div style="flex-shrink:0;width:26px;height:26px;border-radius:999px;background:#0f172a;color:#fff;font-size:12px;font-weight:700;display:flex;align-items:center;justify-content:center;">${idx + 1}</div>
              <div style="flex:1;">
                <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
                  <div style="font-size:12px;font-weight:700;color:#0f172a;">${escapeHtml(catLabel(c.category))}</div>
                  ${statusBadge(c.status, language)}
                </div>
                <div style="font-size:12px;color:#334155;line-height:1.6;margin-bottom:4px;">${escapeHtml(c.message || '')}</div>
                <div style="font-size:11px;color:#475569;line-height:1.6;"><span style="font-weight:600;color:#0f172a;">${escapeHtml(t('result.pdfReport.fixLabel'))}：</span>${escapeHtml(fix)}</div>
              </div>
            </div>`;
        })
        .join('')
    : `<div style="font-size:12px;color:#475569;padding:14px;background:#f1f5f9;border-radius:8px;">${escapeHtml(t('result.pdfReport.noFailItems'))}</div>`;

  // ----- Cover & assemble
  return `
    <div style="font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Hiragino Sans GB','Microsoft YaHei','Helvetica Neue',Arial,sans-serif;color:#0f172a;background:#fff;width:794px;padding:48px 56px;box-sizing:border-box;">
      <!-- Cover -->
      <div style="border-bottom:3px solid #0ea5e9;padding-bottom:24px;margin-bottom:28px;">
        <div style="font-size:12px;letter-spacing:0.2em;color:#06b6d4;text-transform:uppercase;font-weight:700;margin-bottom:8px;">GEO Checker</div>
        <div style="font-size:30px;font-weight:800;margin-bottom:6px;background:linear-gradient(90deg,#06b6d4,#8b5cf6,#ec4899);-webkit-background-clip:text;background-clip:text;color:transparent;">${escapeHtml(t('result.pdfReport.title'))}</div>
        <div style="font-size:13px;color:#475569;margin-bottom:18px;">${escapeHtml(t('result.pdfReport.subtitle'))}</div>
        <table style="width:100%;font-size:12px;color:#334155;">
          <tr>
            <td style="padding:4px 0;width:120px;color:#94a3b8;">${escapeHtml(t('result.pdfReport.targetSite'))}</td>
            <td style="padding:4px 0;font-weight:600;color:#0f172a;word-break:break-all;">${escapeHtml(result.url || '')}</td>
          </tr>
          <tr>
            <td style="padding:4px 0;color:#94a3b8;">${escapeHtml(t('result.pdfReport.generatedAt'))}</td>
            <td style="padding:4px 0;color:#0f172a;">${escapeHtml(generatedAt)}</td>
          </tr>
          <tr>
            <td style="padding:4px 0;color:#94a3b8;">${escapeHtml(t('result.pdfReport.tier'))}</td>
            <td style="padding:4px 0;color:#0f172a;">${escapeHtml(tierLabel(result.tier, language))}</td>
          </tr>
        </table>
      </div>

      <!-- Score hero -->
      <div style="display:flex;gap:20px;align-items:stretch;margin-bottom:28px;">
        <div style="flex-shrink:0;width:200px;background:linear-gradient(135deg,#0f172a,#1e293b);color:#fff;border-radius:14px;padding:22px;text-align:center;">
          <div style="font-size:11px;letter-spacing:0.15em;color:#94a3b8;text-transform:uppercase;margin-bottom:8px;">${escapeHtml(t('result.pdfReport.overallScore'))}</div>
          <div style="font-size:54px;font-weight:800;line-height:1;background:linear-gradient(90deg,#06b6d4,#8b5cf6);-webkit-background-clip:text;background-clip:text;color:transparent;">${score}</div>
          <div style="font-size:12px;color:#94a3b8;margin-top:4px;">/ 100</div>
          <div style="margin-top:14px;display:inline-block;padding:6px 18px;border-radius:999px;background:rgba(6,182,212,0.15);border:1px solid rgba(6,182,212,0.4);font-size:18px;font-weight:700;color:#22d3ee;">${escapeHtml(grade)}</div>
        </div>
        <div style="flex:1;background:#f8fafc;border:1px solid #e2e8f0;border-radius:14px;padding:22px;">
          <div style="font-size:12px;font-weight:700;color:#0f172a;margin-bottom:8px;">${escapeHtml(t('result.pdfReport.scoreInterpretation'))}</div>
          <div style="font-size:12.5px;color:#334155;line-height:1.75;">${escapeHtml(interpretScore(score, t))}</div>
          <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:16px;">
            <div style="background:#dcfce7;border-radius:8px;padding:10px;text-align:center;">
              <div style="font-size:18px;font-weight:800;color:#166534;">${summary.pass_count}</div>
              <div style="font-size:10px;color:#166534;font-weight:600;margin-top:2px;">${escapeHtml(t('result.summary.passed'))}</div>
            </div>
            <div style="background:#fef3c7;border-radius:8px;padding:10px;text-align:center;">
              <div style="font-size:18px;font-weight:800;color:#92400e;">${summary.warn_count}</div>
              <div style="font-size:10px;color:#92400e;font-weight:600;margin-top:2px;">${escapeHtml(t('result.summary.warnings'))}</div>
            </div>
            <div style="background:#fee2e2;border-radius:8px;padding:10px;text-align:center;">
              <div style="font-size:18px;font-weight:800;color:#991b1b;">${summary.fail_count}</div>
              <div style="font-size:10px;color:#991b1b;font-weight:600;margin-top:2px;">${escapeHtml(t('result.summary.failed'))}</div>
            </div>
            <div style="background:#dbeafe;border-radius:8px;padding:10px;text-align:center;">
              <div style="font-size:18px;font-weight:800;color:#1e40af;">${summary.info_count}</div>
              <div style="font-size:10px;color:#1e40af;font-weight:600;margin-top:2px;">${escapeHtml(t('result.summary.info'))}</div>
            </div>
          </div>
        </div>
      </div>

      ${
        lockedCount > 0
          ? `<div style="font-size:11px;color:#92400e;background:#fef3c7;border-left:3px solid #f59e0b;padding:10px 12px;border-radius:6px;margin-bottom:24px;">${escapeHtml(t('result.pdfReport.lockedNotice', { count: lockedCount }))}</div>`
          : ''
      }

      <!-- Section 2: Group breakdown -->
      <div style="font-size:18px;font-weight:800;color:#0f172a;margin-bottom:14px;border-left:4px solid #0ea5e9;padding-left:10px;">${escapeHtml(t('result.pdfReport.groupSection'))}</div>
      <table style="width:100%;border-collapse:collapse;background:#fff;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden;font-size:12px;margin-bottom:30px;">
        <thead>
          <tr style="background:#f1f5f9;">
            <th style="padding:10px 12px;text-align:left;font-size:11px;color:#475569;font-weight:700;text-transform:uppercase;letter-spacing:0.05em;">${escapeHtml(t('result.pdfReport.groupLabel'))}</th>
            <th style="padding:10px 12px;text-align:center;font-size:11px;color:#475569;font-weight:700;">${escapeHtml(t('result.summary.totalChecks'))}</th>
            <th style="padding:10px 12px;text-align:center;font-size:11px;color:#166534;font-weight:700;">${escapeHtml(t('result.summary.passed'))}</th>
            <th style="padding:10px 12px;text-align:center;font-size:11px;color:#92400e;font-weight:700;">${escapeHtml(t('result.summary.warnings'))}</th>
            <th style="padding:10px 12px;text-align:center;font-size:11px;color:#991b1b;font-weight:700;">${escapeHtml(t('result.summary.failed'))}</th>
            <th style="padding:10px 12px;text-align:left;font-size:11px;color:#475569;font-weight:700;">${escapeHtml(t('result.groupProgress.title'))}</th>
          </tr>
        </thead>
        <tbody>${groupRowsHtml}</tbody>
      </table>

      <!-- Section 4: Priority fixes -->
      <div style="font-size:18px;font-weight:800;color:#0f172a;margin-bottom:8px;border-left:4px solid #ec4899;padding-left:10px;">${escapeHtml(t('result.pdfReport.recommendationsSection'))}</div>
      <div style="font-size:12px;color:#475569;margin-bottom:14px;">${escapeHtml(t('result.pdfReport.topFixesIntro'))}</div>
      <div style="margin-bottom:30px;">${topFixesHtml}</div>

      <!-- Section 3: Detailed findings -->
      <div style="font-size:18px;font-weight:800;color:#0f172a;margin-bottom:14px;border-left:4px solid #8b5cf6;padding-left:10px;">${escapeHtml(t('result.pdfReport.detailSection'))}</div>
      ${detailSectionsHtml}

      <!-- Appendix -->
      <div style="margin-top:24px;padding:16px 18px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;">
        <div style="font-size:13px;font-weight:700;color:#0f172a;margin-bottom:8px;">${escapeHtml(t('result.pdfReport.appendixSection'))}</div>
        <div style="font-size:11.5px;color:#475569;line-height:1.7;">${escapeHtml(t('result.pdfReport.appendixBody'))}</div>
      </div>

      <div style="margin-top:24px;padding-top:14px;border-top:1px solid #e5e7eb;font-size:10px;color:#94a3b8;text-align:center;">
        ${escapeHtml(t('result.pdfReport.footer', { year: new Date().getFullYear() }))}
      </div>
    </div>
  `;
};

export async function exportPdfReport(args: ExportArgs): Promise<void> {
  const html = buildReportHtml(args);

  // Off-screen container — keeps natural CJK rendering via the browser, then
  // html2canvas rasterizes it. Positioned far off-screen rather than display:none
  // so layout (including font metrics) is computed.
  const container = document.createElement('div');
  container.style.cssText = 'position:fixed;left:-10000px;top:0;z-index:-1;background:#fff;';
  container.innerHTML = html;
  document.body.appendChild(container);

  try {
    const target = container.firstElementChild as HTMLElement;
    // Give the browser a tick to lay out fonts/images before snapshotting.
    await new Promise((r) => setTimeout(r, 50));

    const canvas = await html2canvas(target, {
      scale: 2,
      backgroundColor: '#ffffff',
      useCORS: true,
      logging: false,
      windowWidth: target.scrollWidth,
      windowHeight: target.scrollHeight,
    });

    const pdf = new jsPDF({ unit: 'pt', format: 'a4', orientation: 'portrait' });
    const pageWidth = pdf.internal.pageSize.getWidth();
    const pageHeight = pdf.internal.pageSize.getHeight();

    // Scale the captured canvas to A4 width, then slice it page by page.
    const imgWidth = pageWidth;
    const imgHeight = (canvas.height * imgWidth) / canvas.width;

    let heightLeft = imgHeight;
    let position = 0;
    const dataUrl = canvas.toDataURL('image/jpeg', 0.95);

    pdf.addImage(dataUrl, 'JPEG', 0, position, imgWidth, imgHeight);
    heightLeft -= pageHeight;

    while (heightLeft > 0) {
      position -= pageHeight;
      pdf.addPage();
      pdf.addImage(dataUrl, 'JPEG', 0, position, imgWidth, imgHeight);
      heightLeft -= pageHeight;
    }

    const safeUrl = (args.result.url || 'site').replace(/[^a-zA-Z0-9]/g, '-').slice(0, 60);
    const baseName = args.t('result.pdfReport.fileName');
    pdf.save(`${baseName}-${safeUrl}.pdf`);
  } finally {
    document.body.removeChild(container);
  }
}
