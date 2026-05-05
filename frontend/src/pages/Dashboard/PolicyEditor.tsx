import { useState } from 'react';
import { useTranslation } from 'react-i18next';

const cardStyle: React.CSSProperties = {
  background: 'var(--bg-card)',
  border: '1px solid var(--border-color)',
};

type PolicyTab = 'voice' | 'rules' | 'escalation';

const MOCK_VOICE = `# Voice
- Confident, specific, low-jargon.
- Prefer show-don't-tell; always link to the primary source.
- Never hype. No "game-changing", no "revolutionary".
- Reddit: match the sub's register; dev.to: longer, more code;
  LinkedIn: first sentence is the hook; YT: conversational.

# Hard no-fly
- Don't engage with /r/programmingcirclejerk.
- Never reply to users with < 0 karma.
- Escalate anything involving legal, pricing, or security.`;

const MOCK_RULES = `# Auto-reply rules
- Technical questions: answer with documentation links (confidence threshold: 0.85)
- Praise/positive feedback: acknowledge briefly (auto-send OK, threshold: 0.90)
- Complaints: escalate to human review (never auto-send)
- Spam: ignore silently

# Rate limits
- Max 10 auto-replies per thread per day
- Max 50 auto-replies total per day
- Cool-down: 30s between replies in the same thread`;

const MOCK_ESCALATION = `# Escalation policy
- Legal mentions → immediate escalation to legal@acme.co
- Security/vulnerability → escalate to security@acme.co + Slack #security
- Pricing complaints → escalate to sales@acme.co
- Any reply with confidence < 0.70 → queue for human review
- Any thread with > 5 replies → pause auto-send, notify editor`;

export function PolicyEditor() {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<PolicyTab>('voice');
  const [content, setContent] = useState<Record<PolicyTab, string>>({
    voice: MOCK_VOICE,
    rules: MOCK_RULES,
    escalation: MOCK_ESCALATION,
  });

  const tabs: { key: PolicyTab; label: string }[] = [
    { key: 'voice', label: t('dashboard.policy.voice') },
    { key: 'rules', label: t('dashboard.policy.rules') },
    { key: 'escalation', label: t('dashboard.policy.escalation') },
  ];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-primary">{t('dashboard.policy.title')}</h1>
        <button className="btn-solid px-4 py-2 text-sm font-semibold rounded-lg transition-all">
          {t('dashboard.policy.save')}
        </button>
      </div>

      {/* Tab bar */}
      <div className="flex gap-2">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className="px-4 py-2 text-sm font-medium rounded-lg transition-colors"
            style={
              activeTab === tab.key
                ? { background: 'var(--bg-tertiary)', color: 'var(--accent-primary)' }
                : { color: 'var(--text-secondary)' }
            }
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Editor */}
      <section className="rounded-xl p-5 space-y-3" style={cardStyle}>
        <div className="flex items-center justify-between">
          <p className="text-xs text-muted">
            {activeTab === 'voice' && t('dashboard.policy.voiceDesc')}
            {activeTab === 'rules' && t('dashboard.policy.rulesDesc')}
            {activeTab === 'escalation' && t('dashboard.policy.escalationDesc')}
          </p>
          <span className="text-xs text-muted">v12</span>
        </div>
        <textarea
          value={content[activeTab]}
          onChange={(e) => setContent({ ...content, [activeTab]: e.target.value })}
          rows={18}
          className="w-full rounded-lg p-4 text-sm font-mono resize-none focus:outline-none focus:ring-2"
          style={{
            background: 'var(--bg-surface)',
            color: 'var(--text-primary)',
            border: '1px solid var(--border-color)',
            '--tw-ring-color': 'var(--accent-primary)',
          } as React.CSSProperties}
        />
      </section>
    </div>
  );
}
