import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useParams, Navigate } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Components } from 'react-markdown';
import { PageHead } from '../components/PageHead';
import { getPostBySlug, type BlogPost as BlogPostType } from '../data/blogPosts';
import { fetchBlogPost } from '../services/blogApi';

// Markdown rendering styles -- reuse the project's theme color variables, no dependency on the tailwind typography plugin
const mdComponents: Components = {
  h2: ({ children }) => (
    <h2 className="text-2xl font-bold mt-12 mb-4 gradient-text">{children}</h2>
  ),
  h3: ({ children }) => (
    <h3 className="text-xl font-semibold mt-8 mb-3 text-primary">{children}</h3>
  ),
  p: ({ children }) => (
    <p className="text-secondary leading-relaxed mb-5">{children}</p>
  ),
  ul: ({ children }) => (
    <ul className="list-disc pl-6 mb-5 space-y-2 text-secondary marker:text-accent-primary">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="list-decimal pl-6 mb-5 space-y-2 text-secondary marker:text-accent-primary">{children}</ol>
  ),
  li: ({ children }) => <li className="leading-relaxed">{children}</li>,
  strong: ({ children }) => <strong className="font-semibold text-primary">{children}</strong>,
  a: ({ href, children }) => (
    <a href={href} className="text-accent-primary underline underline-offset-2 hover:opacity-80" target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  ),
  blockquote: ({ children }) => (
    <blockquote className="border-l-4 border-accent-primary/60 pl-4 italic text-secondary my-6">{children}</blockquote>
  ),
  table: ({ children }) => (
    <div className="overflow-x-auto my-6">
      <table className="w-full border-collapse text-sm">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border border-border bg-card px-4 py-2 text-left font-semibold text-primary">{children}</th>
  ),
  td: ({ children }) => (
    <td className="border border-border px-4 py-2 text-secondary align-top">{children}</td>
  ),
  code: ({ children }) => (
    <code className="bg-card border border-border rounded px-1.5 py-0.5 text-sm text-accent-primary">{children}</code>
  ),
};

export function BlogPost() {
  const { t } = useTranslation();
  const { slug } = useParams();
  const lang = 'zh' as const;  // ICP filing remediation: the site serves Chinese only

  // DB first: an article may exist only in appdb (adding articles needs no deploy); static data is only a fallback.
  // notFound holds only when "the API explicitly returned 404 and static data doesn't have it either" -- an API outage doesn't count.
  const staticPost = slug ? getPostBySlug(slug) : undefined;
  const [apiPost, setApiPost] = useState<BlogPostType | null>(null);
  const [apiDone, setApiDone] = useState(false);
  useEffect(() => {
    let alive = true;
    setApiPost(null); setApiDone(false);
    if (!slug) { setApiDone(true); return; }
    fetchBlogPost(slug)
      .then((p) => { if (!alive) return; if (p) setApiPost(p); setApiDone(true); })
      .catch(() => { if (alive) setApiDone(true); });
    return () => { alive = false; };
  }, [slug]);

  const post = apiPost ?? staticPost;

  if (!post) {
    // API still in flight and no static fallback -> render an empty shell for a beat to avoid a wrong redirect
    if (!apiDone && slug) return <div className="min-h-screen grid-background" />;
    return <Navigate to="/blog" replace />;
  }

  const c = post[lang];
  const formatDate = (iso: string) =>
    new Date(iso).toLocaleDateString('zh-CN', {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
    });

  return (
    <div className="min-h-screen grid-background">
      <PageHead
        titleKey="pageMeta.blogPost._dynamic"
        titleFallback={`${c.title} · Vigilath`}
        descriptionKey="pageMeta.blogPost._dynamic"
        descriptionFallback={c.excerpt}
      />
      <div className="bg-glow bg-glow-1"></div>
      <div className="bg-glow bg-glow-2"></div>

      <main className="flex-1 px-4 py-16 sm:py-20 hero-gradient relative z-10">
        <article className="w-full max-w-3xl mx-auto animate-fade-in">
          <Link to="/blog" className="inline-flex items-center gap-1 text-sm font-medium text-secondary hover:text-accent-primary transition-colors mb-8">
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
            {t('blog.backToList')}
          </Link>

          <header className="mb-10">
            <div className="flex items-center gap-2 text-sm text-muted mb-4">
              <span>{formatDate(post.date)}</span>
              <span>·</span>
              <span>{t('blog.readingTime', { minutes: post.readingMinutes })}</span>
            </div>
            <h1 className="text-3xl sm:text-4xl font-bold leading-tight gradient-text">{c.title}</h1>
          </header>

          {post.cover && (
            <div className="rounded-2xl overflow-hidden mb-10 shadow-lg shadow-accent-primary/10">
              <img src={post.cover} alt="" className="w-full h-auto object-cover" />
            </div>
          )}

          <div className="text-base">
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
              {c.content}
            </ReactMarkdown>
          </div>

          <div className="mt-16 pt-8 border-t border-border text-center">
            <Link to="/blog" className="btn-secondary inline-flex items-center h-10 px-5 rounded-lg text-sm font-semibold">
              {t('blog.backToList')}
            </Link>
          </div>
        </article>
      </main>
    </div>
  );
}
