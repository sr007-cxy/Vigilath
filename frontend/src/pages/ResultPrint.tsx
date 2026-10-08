import { useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useLoadNs } from '../i18n/useLoadNs';
import { accountApi, type DetectionRecordDetail } from '../services/accountApi';
import { buildReportBlocks } from '../utils/exportPdfReport';
import { useDimensionView } from '../hooks/useDimensionView';
import { geoCategoryGroups, FREE_GEO_GROUPS } from '../constants/geoCategories';
import { seoCategoryGroups, FREE_SEO_GROUPS } from '../constants/seoCategories';
import { CONTENT_W_PX, FONT_STACK, PAGE_H_PX, PAGE_W_PX } from '../utils/pdfPrimitives';

/**
 * The **print page** for audit reports. People never visit it directly; it only serves backend Chrome printing:
 * `POST /api/account/detections/{id}/export-pdf` opens this route with Playwright,
 * waits for `<html data-print-ready="1">` to appear, then calls `page.pdf()` to produce a vector PDF.
 *
 * Why not print the result page (/result) directly: the result page is interactive, with 25 major items spread over 7
 * tabs, and GEO / SEO split into two dimensions -- printing would only capture the currently selected screen. This page
 * reuses `buildReportBlocks()`, which lays out the full content as "one document" (cover -> executive summary
 * -> category overview -> priority fixes -> details for both dimensions -> appendix), with no collapsing and no tabs.
 *
 * The same block HTML also feeds the frontend html2canvas fallback chain (used when anonymous users have no history row
 * or backend rendering fails), so the blocks themselves are shared by both; the only difference is the print CSS here.
 */

/**
 * Styles for the print document. `@page` is deliberately omitted: paper size and margins are entirely determined by the backend `page.pdf()`
 * params (body margins + header/footer, cover margin 0); declaring them again in CSS would only conflict.
 *
 * Margins are 13.7mm left and right -- A4 width 210mm minus that is exactly 182.6mm, which at 96dpi
 * is 690px, matching the blocks' CONTENT_W_PX exactly, so no re-layout is needed for printing.
 */
const PRINT_CSS = `
/* 纸是白的。站点主题给 body 铺的底色会被 print_background 一起印出来,
   690px 宽的白色正文压在上面,左右两条边就成了贯穿整页的竖线。 */
html, body { background: #fff !important; }
.print-doc {
  background: #fff;
  color: #0f172a;
  font-family: ${FONT_STACK};
  -webkit-print-color-adjust: exact;
  print-color-adjust: exact;
  width: ${CONTENT_W_PX}px;
  margin: 0 auto;
}
.print-doc [data-pdf-block] {
  /* 一个区块不许被拦腰截断 —— 除非它本来就比一页高(见下面的 breakable) */
  break-inside: avoid;
  page-break-inside: avoid;
  margin-bottom: 5px;
}
.print-doc [data-pdf-block][data-pdf-breakable] {
  /* 检测详情表可能十几行、跨页,允许断,但不许把某一行劈成两半 */
  break-inside: auto;
  page-break-inside: auto;
}
.print-doc [data-pdf-block][data-pdf-breakable] tr {
  break-inside: avoid;
  page-break-inside: avoid;
}
.print-doc [data-pdf-break] {
  break-before: page;
  page-break-before: always;
}
/* 正文第一块自带「另起一页」标记(它原本跟在封面后面)。封面在另一趟印,
   这里不清掉就会先吐一张空白页。 */
.print-doc [data-pdf-block]:first-child {
  break-before: auto !important;
  page-break-before: auto !important;
}
/* 长表格跨页时表头跟着重复 */
.print-doc thead { display: table-header-group; }
.print-doc svg, .print-doc img { break-inside: avoid; }

/* 封面整页出血,和正文分两趟印(后端合并),互相隐藏 */
.print-cover {
  width: ${PAGE_W_PX}px;
  height: ${PAGE_H_PX}px;
  overflow: hidden;
}
html[data-print-pass="cover"] .print-doc { display: none; }
html[data-print-pass="body"] .print-cover { display: none; }
`;

export function ResultPrint() {
  const { recordId } = useParams<{ recordId: string }>();
  const { t, i18n } = useTranslation();
  const nsReady = useLoadNs('result');

  const [record, setRecord] = useState<DetectionRecordDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const id = Number(recordId);
    if (!Number.isFinite(id)) {
      setError('bad record id');
      return;
    }
    const token = localStorage.getItem('token') || '';
    accountApi
      .getDetection(token, id)
      .then(setRecord)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [recordId]);

  const geoResult = record?.result ?? null;
  const seoResult = geoResult?.seo ?? null;

  // Hooks can't be called conditionally -- pass undefined before data arrives, and everything derived is an empty collection
  const geoView = useDimensionView(
    geoResult?.checks, geoCategoryGroups, FREE_GEO_GROUPS,
    geoResult?.locked_categories, t, i18n.language,
  );
  const seoView = useDimensionView(
    seoResult?.checks, seoCategoryGroups, FREE_SEO_GROUPS,
    seoResult?.locked_categories, t, i18n.language,
  );

  const blocks = useMemo(() => {
    if (!geoResult || !nsReady) return null;
    return buildReportBlocks({
      geo: {
        result: geoResult,
        groups: geoCategoryGroups,
        checksByCategory: geoView.checksByCategory,
        otherCategories: geoView.otherCategories,
        groupStats: geoView.groupStats,
      },
      seo: seoResult
        ? {
            result: seoResult,
            groups: seoCategoryGroups,
            checksByCategory: seoView.checksByCategory,
            otherCategories: seoView.otherCategories,
            groupStats: seoView.groupStats,
          }
        : null,
      t,
      language: i18n.language || 'zh',
    });
    // t is a new reference on every render; putting it in deps would make useMemo pointless
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [geoResult, seoResult, geoView, seoView, nsReady, i18n.language]);

  // The cover is the full-bleed part, the body is everything else -- the backend prints in two passes, so split into two trees here too
  const { coverHtml, bodyHtml } = useMemo(() => {
    if (!blocks || blocks.length === 0) return { coverHtml: '', bodyHtml: '' };
    const isCover = blocks[0].includes('data-pdf-fullpage');
    return {
      coverHtml: isCover ? blocks[0] : '',
      bodyHtml: (isCover ? blocks.slice(1) : blocks).join(''),
    };
  }, [blocks]);

  // The backend waits for this marker to start printing. Mark it even on failure, otherwise the render job hangs until timeout.
  useEffect(() => {
    if (bodyHtml || error) {
      document.documentElement.dataset.printReady = error ? 'error' : '1';
    }
    return () => {
      delete document.documentElement.dataset.printReady;
    };
  }, [bodyHtml, error]);

  if (error) {
    return <div data-export-root style={{ padding: 24, fontFamily: FONT_STACK }}>报告加载失败:{error}</div>;
  }
  if (!bodyHtml) return <div style={{ padding: 24 }} />;

  return (
    <>
      <style>{PRINT_CSS}</style>
      <div data-export-root>
        {coverHtml && <div className="print-cover" dangerouslySetInnerHTML={{ __html: coverHtml }} />}
        <div className="print-doc" dangerouslySetInnerHTML={{ __html: bodyHtml }} />
      </div>
    </>
  );
}

export default ResultPrint;
