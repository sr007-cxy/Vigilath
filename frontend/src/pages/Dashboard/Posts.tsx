import { useState } from 'react';
import { useTranslation } from 'react-i18next';

const cardStyle: React.CSSProperties = {
  background: 'var(--bg-card)',
  border: '1px solid var(--border-color)',
};

type PostStatus = 'published' | 'scheduled' | 'draft';

interface Post {
  id: string;
  title: string;
  status: PostStatus;
  platforms: { name: string; color: string }[];
  createdAt: string;
  publishedAt?: string;
}

const MOCK_POSTS: Post[] = [
  {
    id: '1',
    title: 'Acme SDK v2: migrate in one line',
    status: 'published',
    platforms: [
      { name: 'Reddit', color: '#FF4500' },
      { name: 'LinkedIn', color: '#0A66C2' },
      { name: 'dev.to', color: '#08A6F3' },
    ],
    createdAt: '2026-04-18',
    publishedAt: '2026-04-19 09:00',
  },
  {
    id: '2',
    title: 'How we reduced cold start by 60%',
    status: 'scheduled',
    platforms: [
      { name: 'Reddit', color: '#FF4500' },
      { name: 'YouTube', color: '#FF0000' },
    ],
    createdAt: '2026-04-19',
    publishedAt: '2026-04-22 09:00',
  },
  {
    id: '3',
    title: 'Q2 roadmap preview — community input welcome',
    status: 'draft',
    platforms: [
      { name: 'LinkedIn', color: '#0A66C2' },
      { name: 'dev.to', color: '#08A6F3' },
    ],
    createdAt: '2026-04-20',
  },
  {
    id: '4',
    title: 'Introducing our new CLI tool',
    status: 'draft',
    platforms: [
      { name: 'Reddit', color: '#FF4500' },
    ],
    createdAt: '2026-04-20',
  },
];

export function Posts() {
  const { t } = useTranslation();
  const [filter, setFilter] = useState<'all' | PostStatus>('all');

  const filtered = filter === 'all' ? MOCK_POSTS : MOCK_POSTS.filter((p) => p.status === filter);

  const statusBadge = (status: PostStatus) => {
    const map = {
      published: { color: 'var(--success)', label: t('dashboard.posts.published') },
      scheduled: { color: 'var(--accent-primary)', label: t('dashboard.posts.scheduled') },
      draft: { color: 'var(--text-muted)', label: t('dashboard.posts.draft') },
    };
    const s = map[status];
    return (
      <span
        className="text-xs px-2 py-0.5 rounded-full font-medium"
        style={{ color: s.color, background: `${s.color}15`, border: `1px solid ${s.color}30` }}
      >
        {s.label}
      </span>
    );
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-primary">{t('dashboard.posts.title')}</h1>
      </div>

      {/* Filters */}
      <div className="flex gap-2 overflow-x-auto scrollbar-hide">
        {(['all', 'published', 'scheduled', 'draft'] as const).map((key) => (
          <button
            key={key}
            onClick={() => setFilter(key)}
            className="px-3 py-1.5 text-sm font-medium rounded-lg whitespace-nowrap transition-colors"
            style={
              filter === key
                ? { background: 'var(--bg-tertiary)', color: 'var(--accent-primary)' }
                : { color: 'var(--text-secondary)' }
            }
          >
            {key === 'all' ? t('dashboard.posts.all') : t(`dashboard.posts.${key}`)}
          </button>
        ))}
      </div>

      {/* Posts list */}
      <div className="space-y-3">
        {filtered.map((post) => (
          <div key={post.id} className="rounded-xl p-4 flex flex-col sm:flex-row sm:items-center gap-3" style={cardStyle}>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-primary truncate">{post.title}</p>
              <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                {statusBadge(post.status)}
                {post.platforms.map((p) => (
                  <span
                    key={p.name}
                    className="text-xs px-1.5 py-0.5 rounded"
                    style={{ color: p.color, background: `${p.color}10` }}
                  >
                    {p.name}
                  </span>
                ))}
              </div>
            </div>
            <div className="text-xs text-muted shrink-0 text-right">
              <p>{post.createdAt}</p>
              {post.publishedAt && (
                <p className="mt-0.5">{post.status === 'scheduled' ? t('dashboard.posts.scheduledFor') : t('dashboard.posts.publishedAt')}: {post.publishedAt}</p>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
