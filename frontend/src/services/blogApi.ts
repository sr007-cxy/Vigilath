// Blog article API -- the authoritative data is in the blog_posts table of the shared appdb (read by both test/prod),
// articles are added via backend/scripts/sync_blog_posts.py, no frontend redeploy needed.
// On fetch failure (backend down/local frontend-only development), callers fall back to the bundled static blogPosts.ts.
import type { BlogPost } from '../data/blogPosts';

const API_BASE = (import.meta.env.VITE_API_URL as string) || '/api';

// List item: same shape as BlogPost, but zh has no content (the list endpoint doesn't return bodies).
// The backend may still send en fields (DB side pending 6.2); the frontend never reads them.
export type BlogPostListItem = Omit<BlogPost, 'zh'> & {
  zh: { title: string; excerpt: string };
};

export async function fetchBlogPosts(): Promise<BlogPostListItem[]> {
  const resp = await fetch(`${API_BASE}/blog/posts`);
  if (!resp.ok) throw new Error(`blog list ${resp.status}`);
  const data = await resp.json();
  if (!Array.isArray(data?.posts)) throw new Error('blog list bad payload');
  return data.posts as BlogPostListItem[];
}

export async function fetchBlogPost(slug: string): Promise<BlogPost | null> {
  const resp = await fetch(`${API_BASE}/blog/posts/${encodeURIComponent(slug)}`);
  if (resp.status === 404) return null;
  if (!resp.ok) throw new Error(`blog post ${resp.status}`);
  return (await resp.json()) as BlogPost;
}
