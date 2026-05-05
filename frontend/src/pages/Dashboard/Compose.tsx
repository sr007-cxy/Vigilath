import { useState } from 'react';
import { useTranslation } from 'react-i18next';

const cardStyle: React.CSSProperties = {
  background: 'var(--bg-card)',
  border: '1px solid var(--border-color)',
};

interface Platform {
  id: string;
  name: string;
  handle: string;
  color: string;
  connected: boolean;
  writeOnly?: boolean;
  capabilities: { post: boolean; monitor: boolean; reply: boolean };
}

const PLATFORMS: Platform[] = [
  { id: 'reddit', name: 'Reddit', handle: '@acme_dev', color: '#FF4500', connected: true, capabilities: { post: true, monitor: true, reply: true } },
  { id: 'linkedin', name: 'LinkedIn', handle: 'Acme Co.', color: '#0A66C2', connected: true, capabilities: { post: true, monitor: true, reply: true } },
  { id: 'youtube', name: 'YouTube', handle: 'Acme Channel', color: '#FF0000', connected: true, capabilities: { post: true, monitor: true, reply: true } },
  { id: 'devto', name: 'dev.to', handle: '@acme', color: '#08A6F3', connected: true, capabilities: { post: true, monitor: true, reply: true } },
  { id: 'medium', name: 'Medium', handle: '@acme', color: '#292929', connected: false, writeOnly: true, capabilities: { post: true, monitor: false, reply: false } },
  { id: 'hashnode', name: 'Hashnode', handle: '', color: '#2962FF', connected: false, capabilities: { post: true, monitor: true, reply: true } },
];

export function Compose() {
  const { t } = useTranslation();
  const [brief, setBrief] = useState('');
  const [selectedPlatforms, setSelectedPlatforms] = useState<string[]>(['reddit', 'linkedin', 'youtube', 'devto']);
  const [showPicker, setShowPicker] = useState(false);
  const [scheduleMode, setScheduleMode] = useState<'now' | 'scheduled'>('now');

  const togglePlatform = (id: string) => {
    const p = PLATFORMS.find((pl) => pl.id === id);
    if (!p?.connected) return;
    setSelectedPlatforms((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    );
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-primary">{t('dashboard.compose.title')}</h1>
        <div className="flex gap-2">
          <button
            className="px-4 py-2 text-sm font-medium rounded-lg border transition-colors"
            style={{ color: 'var(--text-secondary)', borderColor: 'var(--border-color)', background: 'var(--bg-surface)' }}
          >
            {t('dashboard.compose.save')}
          </button>
          <button
            className="btn-solid px-4 py-2 text-sm font-semibold rounded-lg transition-all"
          >
            {t('dashboard.compose.post')}
          </button>
        </div>
      </div>

      {/* Brief input */}
      <section className="rounded-xl p-5 space-y-3" style={cardStyle}>
        <label className="text-sm font-medium text-primary block">
          {t('dashboard.compose.briefLabel')}
        </label>
        <textarea
          value={brief}
          onChange={(e) => setBrief(e.target.value)}
          rows={4}
          className="w-full rounded-lg p-3 text-sm resize-none focus:outline-none focus:ring-2"
          style={{
            background: 'var(--bg-surface)',
            color: 'var(--text-primary)',
            border: '1px solid var(--border-color)',
            '--tw-ring-color': 'var(--accent-primary)',
          } as React.CSSProperties}
          placeholder={t('dashboard.compose.briefPlaceholder')}
        />
      </section>

      {/* Platform picker */}
      <section className="rounded-xl p-5 space-y-3" style={cardStyle}>
        <label className="text-sm font-medium text-primary block">
          {t('dashboard.compose.targetPlatforms')}
        </label>

        {/* Selected pills */}
        <div className="flex flex-wrap gap-2 mb-2">
          {selectedPlatforms.map((id) => {
            const p = PLATFORMS.find((pl) => pl.id === id)!;
            return (
              <span
                key={id}
                className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium"
                style={{ color: p.color, background: `${p.color}15`, border: `1px solid ${p.color}30` }}
              >
                <span className="w-2 h-2 rounded-full" style={{ background: p.color }} />
                {p.name}
                <button onClick={() => togglePlatform(id)} className="ml-1 opacity-60 hover:opacity-100">&times;</button>
              </span>
            );
          })}
        </div>

        <button
          onClick={() => setShowPicker(!showPicker)}
          className="w-full text-left px-4 py-2.5 rounded-lg text-sm transition-colors border"
          style={{
            color: 'var(--text-secondary)',
            background: 'var(--bg-surface)',
            borderColor: 'var(--border-color)',
          }}
        >
          {t('dashboard.compose.selectPlatforms')} &#9662;
        </button>

        {showPicker && (
          <div
            className="rounded-lg border overflow-hidden"
            style={{ borderColor: 'var(--border-color)', background: 'var(--bg-surface)' }}
          >
            {PLATFORMS.map((p) => (
              <button
                key={p.id}
                onClick={() => p.connected ? togglePlatform(p.id) : undefined}
                className="w-full flex items-center gap-3 px-4 py-3 text-sm transition-colors border-b last:border-b-0"
                style={{
                  borderColor: 'var(--border-color)',
                  opacity: p.connected ? 1 : 0.5,
                  cursor: p.connected ? 'pointer' : 'default',
                }}
              >
                <span className="w-3 h-3 rounded-full" style={{ background: p.color }} />
                <span className="font-medium text-primary">{p.name}</span>
                <span className="text-xs text-muted flex-1 text-left">{p.handle}</span>
                {p.connected ? (
                  <span className="flex items-center gap-1 text-xs" style={{ color: 'var(--success)' }}>
                    <span className="w-1.5 h-1.5 rounded-full" style={{ background: 'var(--success)' }} />
                    {selectedPlatforms.includes(p.id) ? '✓' : ''}
                  </span>
                ) : p.writeOnly ? (
                  <span className="text-xs px-2 py-0.5 rounded" style={{ color: 'var(--warning)', background: `rgba(245,158,11,0.1)` }}>
                    {t('dashboard.compose.writeOnly')}
                  </span>
                ) : (
                  <span className="text-xs font-medium" style={{ color: 'var(--accent-primary)' }}>
                    + {t('dashboard.compose.connect')}
                  </span>
                )}
              </button>
            ))}
          </div>
        )}
      </section>

      {/* Schedule */}
      <section className="rounded-xl p-5 space-y-3" style={cardStyle}>
        <label className="text-sm font-medium text-primary block">
          {t('dashboard.compose.schedule')}
        </label>
        <div className="flex items-center gap-4">
          <label className="flex items-center gap-2 text-sm text-secondary cursor-pointer">
            <input
              type="radio"
              name="schedule"
              checked={scheduleMode === 'now'}
              onChange={() => setScheduleMode('now')}
              style={{ accentColor: 'var(--accent-primary)' }}
            />
            {t('dashboard.compose.now')}
          </label>
          <label className="flex items-center gap-2 text-sm text-secondary cursor-pointer">
            <input
              type="radio"
              name="schedule"
              checked={scheduleMode === 'scheduled'}
              onChange={() => setScheduleMode('scheduled')}
              style={{ accentColor: 'var(--accent-primary)' }}
            />
            {t('dashboard.compose.scheduled')}
          </label>
          {scheduleMode === 'scheduled' && (
            <input
              type="datetime-local"
              className="text-sm px-3 py-1.5 rounded-lg border"
              style={{
                background: 'var(--bg-surface)',
                color: 'var(--text-primary)',
                borderColor: 'var(--border-color)',
              }}
            />
          )}
        </div>
      </section>

      {/* Action buttons */}
      <div className="flex gap-3">
        <button
          className="btn-solid flex-1 py-3 rounded-lg text-sm font-semibold transition-all flex items-center justify-center gap-2"
        >
          {t('dashboard.compose.draftWithAgent')}
        </button>
        <button
          className="flex-1 py-3 rounded-lg text-sm font-medium border transition-colors"
          style={{
            color: 'var(--text-secondary)',
            borderColor: 'var(--border-color)',
            background: 'var(--bg-surface)',
          }}
        >
          {t('dashboard.compose.writeManually')}
        </button>
      </div>
    </div>
  );
}
