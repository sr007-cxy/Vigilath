import { useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import html2canvas from 'html2canvas';
import jsPDF from 'jspdf';

import { mockBriefs } from '../../../../mocks/sentiment';
import { useBriefs, useBriefDetail } from '../../../../hooks/useSentiment';
import type { Brief, BriefSummary, SentimentAccount } from '../../../../types/sentiment';

import { BriefRenderer } from '../components/BriefRenderer';
import { extractAnchors } from '../components/briefUtils';

const cardStyle: React.CSSProperties = {
  background: 'var(--bg-card)',
  border: '1px solid var(--border-color)',
};

interface Props {
  account: SentimentAccount;
  usingMock?: boolean;
}

export function BriefsTab({ account, usingMock }: Props) {
  const { t } = useTranslation();

  const briefsQuery = useBriefs(usingMock ? null : account.id, usingMock ? null : account.ticker);
  const briefs: BriefSummary[] = useMemo(
    () => usingMock ? mockBriefs : (briefsQuery.data?.items ?? []),
    [usingMock, briefsQuery.data],
  );

  const [explicitId, setExplicitId] = useState<number | null>(null);
  // 默认选中第一个,通过派生值实现(避免 useEffect setState)
  const selectedId = explicitId ?? briefs[0]?.id ?? null;

  // 真实模式拉详情;mock 模式直接从静态拿
  const detailQuery = useBriefDetail(
    usingMock ? null : account.id,
    usingMock ? null : selectedId,
  );

  const selected: Brief | null = usingMock
    ? (mockBriefs.find(b => b.id === selectedId) as Brief | undefined) ?? null
    : (detailQuery.data ?? null);

  const anchors = useMemo(
    () => (selected ? extractAnchors(selected.body) : []),
    [selected],
  );

  const briefRef = useRef<HTMLDivElement>(null);
  const [exporting, setExporting] = useState(false);

  const handleExportPdf = async () => {
    if (!selected || !briefRef.current) return;
    setExporting(true);
    try {
      const canvas = await html2canvas(briefRef.current, {
        scale: 2,
        useCORS: true,
        backgroundColor: '#ffffff',
      });
      const imgData = canvas.toDataURL('image/png');
      const pdf = new jsPDF('p', 'mm', 'a4');
      const pageW = pdf.internal.pageSize.getWidth();
      const pageH = pdf.internal.pageSize.getHeight();
      const margin = 10;
      const contentW = pageW - margin * 2;
      const imgH = (canvas.height / canvas.width) * contentW;

      // 如果内容高度超出一页,分页
      let yOffset = 0;
      const availH = pageH - margin * 2;
      while (yOffset < imgH) {
        if (yOffset > 0) pdf.addPage();
        pdf.addImage(imgData, 'PNG', margin, margin - yOffset, contentW, imgH);
        yOffset += availH;
      }

      pdf.save(`brief_${selected.symbol}_${selected.date}.pdf`);
    } finally {
      setExporting(false);
    }
  };

  const handleEmail = () => alert('邮件发送(演示)');

  if (!usingMock && briefsQuery.isLoading) {
    return <div className="rounded-xl py-12 text-center text-secondary text-sm" style={cardStyle}>
      {t('common.loading') || 'Loading...'}
    </div>;
  }
  if (!usingMock && briefsQuery.error) {
    return <div className="rounded-xl py-6 px-4 text-sm" style={{ background: 'rgba(239,68,68,0.1)', color: '#dc2626' }}>
      ⚠ {briefsQuery.error instanceof Error ? briefsQuery.error.message : 'Failed to load briefs'}
    </div>;
  }

  return (
    <div className="grid grid-cols-1 xl:grid-cols-[280px_minmax(0,1fr)] gap-4">
      <aside className="rounded-xl p-3 self-start" style={cardStyle}>
        <header className="flex items-center justify-between px-1 mb-2">
          <h4 className="text-sm font-semibold text-primary">
            {t('dashboard.sentiment.briefs.list')}
          </h4>
          <button type="button" onClick={() => alert('临时简报生成(演示)')}
            className="text-xs" style={{ color: 'var(--accent-primary)' }}>
            {t('dashboard.sentiment.briefs.newAdHoc')}
          </button>
        </header>
        {briefs.length === 0 ? (
          <p className="text-xs text-muted px-2 py-4 text-center">尚无简报。</p>
        ) : (
          <ul className="space-y-1">
            {briefs.map(b => (
              <li key={b.id}>
                <button type="button" onClick={() => setExplicitId(b.id)}
                  className="w-full text-left px-2.5 py-2 rounded text-sm transition-colors"
                  style={{
                    background: selectedId === b.id ? 'var(--bg-tertiary)' : 'transparent',
                    color: selectedId === b.id ? 'var(--accent-primary)' : 'var(--text-primary)',
                    border: '1px solid transparent',
                  }}>
                  <div className="flex items-center justify-between">
                    <span className="font-medium">📄 {b.date}</span>
                  </div>
                  <div className="text-[11px] text-muted mt-0.5">
                    {new Date(b.generated_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}
                  </div>
                </button>
              </li>
            ))}
          </ul>
        )}
      </aside>

      <section className="grid grid-cols-1 lg:grid-cols-[1fr_180px] gap-4">
        <article className="rounded-xl p-6" style={cardStyle}>
          {!usingMock && detailQuery.isLoading && selectedId ? (
            <p className="text-sm text-muted py-12 text-center">{t('common.loading') || 'Loading...'}</p>
          ) : selected ? (
            <>
              <div ref={briefRef}>
                <BriefRenderer body={selected.body} />
              </div>
              <div className="flex items-center gap-2 mt-6 pt-4" style={{ borderTop: '1px solid var(--border-color)' }}>
                <button type="button" onClick={handleExportPdf} disabled={exporting}
                  className="rounded-md px-3 py-1.5 text-sm font-semibold"
                  style={{ background: 'var(--bg-tertiary)', color: 'var(--text-primary)', opacity: exporting ? 0.6 : 1 }}>
                  {exporting ? '导出中…' : t('dashboard.sentiment.briefs.exportPdf')}
                </button>
                <button type="button" onClick={handleEmail}
                  className="btn-solid rounded-md px-3 py-1.5 text-sm font-semibold">
                  {t('dashboard.sentiment.briefs.email')}
                </button>
              </div>
            </>
          ) : (
            <p className="text-sm text-muted py-12 text-center">
              {t('dashboard.sentiment.briefs.selectBrief')}
            </p>
          )}
        </article>

        {anchors.length > 0 && (
          <nav className="hidden lg:block sticky self-start" style={{ top: 80 }}>
            <div className="rounded-xl p-4" style={cardStyle}>
              <p className="text-xs font-semibold text-secondary uppercase tracking-wider mb-2">
                {t('dashboard.sentiment.briefs.anchorTitle')}
              </p>
              <ul className="space-y-1.5">
                {anchors.map(a => (
                  <li key={a.id}>
                    <a href={`#${a.id}`} className="text-xs hover:underline block"
                       style={{ color: 'var(--text-secondary)' }}>
                      {a.text}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          </nav>
        )}
      </section>
    </div>
  );
}
