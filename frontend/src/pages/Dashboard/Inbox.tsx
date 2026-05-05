import { useState } from 'react';
import { useTranslation } from 'react-i18next';

const cardStyle: React.CSSProperties = {
  background: 'var(--bg-card)',
  border: '1px solid var(--border-color)',
};

type FilterTab = 'all' | 'approval' | 'sent' | 'ignored';

interface InboxItem {
  id: string;
  author: string;
  platform: string;
  platformColor: string;
  preview: string;
  classification: string;
  timeAgo: string;
  status: 'draft_ready' | 'auto_sent' | 'escalated' | 'auto_fixed';
  confidence?: number;
  threadTitle?: string;
  threadSource?: string;
  draftReply?: string;
  reasoning?: string;
}

const MOCK_ITEMS: InboxItem[] = [
  {
    id: '1',
    author: 'u/kernel_panic',
    platform: 'Reddit',
    platformColor: '#FF4500',
    preview: 'Does this break webhook signing?',
    classification: 'question',
    timeAgo: '12m',
    status: 'draft_ready',
    confidence: 87,
    threadTitle: 'SDK v2 migration',
    threadSource: 'r/javascript',
    draftReply: 'Signing behavior is unchanged in v2 — same HMAC-SHA256, same secret rotation flow. The docs section on signing got rewritten — here\'s the direct link: ...',
    reasoning: 'matched rule: technical Q → answer',
  },
  {
    id: '2',
    author: '@sara_dev',
    platform: 'LinkedIn',
    platformColor: '#0A66C2',
    preview: 'Great timing — just hit this issue yesterday',
    classification: 'praise',
    timeAgo: '24m',
    status: 'auto_sent',
  },
  {
    id: '3',
    author: '@troll42',
    platform: 'YouTube',
    platformColor: '#FF0000',
    preview: 'worst library ever',
    classification: 'complaint',
    timeAgo: '31m',
    status: 'escalated',
  },
  {
    id: '4',
    author: 'nate_m',
    platform: 'dev.to',
    platformColor: '#08A6F3',
    preview: "Typo: 'the the' in section 3",
    classification: 'bug',
    timeAgo: '1h',
    status: 'auto_fixed',
  },
];

export function Inbox() {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<FilterTab>('all');
  const [selectedItem, setSelectedItem] = useState<InboxItem | null>(MOCK_ITEMS[0]);

  const tabs: { key: FilterTab; label: string; count?: number }[] = [
    { key: 'all', label: t('dashboard.inbox.all') },
    { key: 'approval', label: t('dashboard.inbox.needsApproval'), count: 7 },
    { key: 'sent', label: t('dashboard.inbox.sent') },
    { key: 'ignored', label: t('dashboard.inbox.ignored') },
  ];

  const statusLabel = (item: InboxItem) => {
    switch (item.status) {
      case 'draft_ready':
        return { text: t('dashboard.inbox.draftReady', { confidence: item.confidence }), color: 'var(--accent-primary)' };
      case 'auto_sent':
        return { text: t('dashboard.inbox.autoSent'), color: 'var(--success)' };
      case 'escalated':
        return { text: t('dashboard.inbox.escalated'), color: 'var(--warning)' };
      case 'auto_fixed':
        return { text: t('dashboard.inbox.autoFixed'), color: 'var(--success)' };
    }
  };

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-primary">{t('dashboard.inbox.title')}</h1>

      {/* Filter tabs */}
      <div className="flex gap-2 overflow-x-auto scrollbar-hide">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className="px-3 py-1.5 text-sm font-medium rounded-lg whitespace-nowrap transition-colors"
            style={
              activeTab === tab.key
                ? { background: 'var(--bg-tertiary)', color: 'var(--accent-primary)' }
                : { color: 'var(--text-secondary)' }
            }
          >
            {tab.label}
            {tab.count != null && (
              <span
                className="ml-1.5 text-xs px-1.5 py-0.5 rounded-full"
                style={{ background: 'var(--accent-primary)', color: 'var(--solid-btn-text)' }}
              >
                {tab.count}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Split view */}
      <div className="grid md:grid-cols-[340px_1fr] gap-4">
        {/* List */}
        <div className="rounded-xl overflow-hidden" style={cardStyle}>
          {MOCK_ITEMS.map((item) => {
            const status = statusLabel(item);
            const active = selectedItem?.id === item.id;
            return (
              <button
                key={item.id}
                onClick={() => setSelectedItem(item)}
                className="w-full text-left px-4 py-3 border-b last:border-b-0 transition-colors"
                style={{
                  borderColor: 'var(--border-color)',
                  background: active ? 'var(--bg-surface-hover)' : 'transparent',
                }}
              >
                <div className="flex items-center gap-2 mb-1">
                  <span className="w-2 h-2 rounded-full shrink-0" style={{ background: item.platformColor }} />
                  <span className="text-sm font-medium text-primary truncate">{item.author}</span>
                  <span className="text-xs text-muted ml-auto shrink-0">{item.timeAgo}</span>
                </div>
                <p className="text-sm text-secondary truncate">{item.preview}</p>
                <div className="flex items-center gap-2 mt-1">
                  <span
                    className="text-xs px-1.5 py-0.5 rounded"
                    style={{ background: 'var(--bg-tertiary)', color: 'var(--text-muted)' }}
                  >
                    {item.classification}
                  </span>
                  <span className="text-xs" style={{ color: status.color }}>{status.text}</span>
                </div>
              </button>
            );
          })}
        </div>

        {/* Detail pane */}
        {selectedItem ? (
          <div className="rounded-xl p-5 space-y-4" style={cardStyle}>
            <div>
              <h3 className="text-sm font-semibold text-primary">
                {selectedItem.threadTitle ?? selectedItem.preview}
              </h3>
              {selectedItem.threadSource && (
                <p className="text-xs text-muted mt-0.5">
                  {t('dashboard.inbox.on')} {selectedItem.threadSource}
                </p>
              )}
            </div>

            <div className="rounded-lg p-3" style={{ background: 'var(--bg-surface-hover)' }}>
              <p className="text-xs text-muted mb-1">{selectedItem.author}</p>
              <p className="text-sm text-secondary">{selectedItem.preview}</p>
            </div>

            {selectedItem.draftReply && (
              <div className="rounded-lg p-3 border" style={{ borderColor: 'var(--border-color)', background: 'var(--bg-surface)' }}>
                <p className="text-xs text-muted mb-1">{t('dashboard.inbox.draftReplyLabel')}</p>
                <p className="text-sm text-primary">{selectedItem.draftReply}</p>
              </div>
            )}

            {selectedItem.reasoning && (
              <div className="rounded-lg p-3" style={{ background: 'var(--bg-tertiary)' }}>
                <p className="text-xs text-muted mb-1">{t('dashboard.inbox.reasoning')}</p>
                <p className="text-xs text-secondary">{selectedItem.reasoning}</p>
                {selectedItem.confidence && (
                  <p className="text-xs text-muted mt-1">
                    {t('dashboard.inbox.confidenceLabel')}: {selectedItem.confidence}%
                  </p>
                )}
              </div>
            )}

            <div className="flex gap-2 pt-2">
              <button className="btn-solid flex-1 py-2 rounded-lg text-sm font-medium">
                {t('dashboard.inbox.sendAsIs')}
              </button>
              <button
                className="flex-1 py-2 rounded-lg text-sm font-medium border transition-colors"
                style={{ color: 'var(--text-secondary)', borderColor: 'var(--border-color)', background: 'var(--bg-surface)' }}
              >
                {t('dashboard.inbox.edit')}
              </button>
              <button
                className="py-2 px-3 rounded-lg text-sm font-medium border transition-colors"
                style={{ color: 'var(--text-muted)', borderColor: 'var(--border-color)', background: 'var(--bg-surface)' }}
              >
                {t('dashboard.inbox.ignore')}
              </button>
            </div>
          </div>
        ) : (
          <div className="rounded-xl p-10 flex items-center justify-center" style={cardStyle}>
            <p className="text-sm text-muted">{t('dashboard.inbox.selectItem')}</p>
          </div>
        )}
      </div>
    </div>
  );
}
