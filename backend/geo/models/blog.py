"""Website blog posts — after moving into the DB, test/prod read the same table in appdb.

Historically posts were hard-coded in frontend/src/data/blogPosts.ts, and every new post required test/prod
to build+deploy separately. After moving into the blog_posts table: adding a post = running
backend/scripts/sync_blog_posts.py once (upsert by slug), visible on both sites immediately, zero deploys.
The frontend still keeps blogPosts.ts as a static fallback when the API is unavailable.
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from geo.database import Base


class BlogPostORM(Base):
    __tablename__ = "blog_posts"

    id = Column(Integer, primary_key=True, index=True)
    # /blog/:slug route and unique key, same semantics as the frontend static data
    slug = Column(String, nullable=False, unique=True, index=True)
    # ISO date string, e.g. 2026-07-14 — the frontend sorts/formats it as a string; keep that convention
    date = Column(String, nullable=False, index=True)
    # Cover URL (nullable). A relative path /image/blog/<slug>.jpg points to each site's own static assets;
    # new covers not shipped with the frontend can be stored as absolute URLs (e.g. under the prod domain), hotlinked by both sites.
    cover = Column(String, nullable=True)
    reading_minutes = Column(Integer, nullable=False, default=5)

    zh_title = Column(String, nullable=False)
    zh_excerpt = Column(Text, nullable=False)
    zh_content = Column(Text, nullable=False)  # Markdown
    en_title = Column(String, nullable=False)
    en_excerpt = Column(Text, nullable=False)
    en_content = Column(Text, nullable=False)  # Markdown

    # Unpublishing does not delete the row: published=False removes it from list/detail, content kept for restoration
    published = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_locale_dict(self) -> dict:
        """Serialize into the frontend BlogPost shape (zh/en locale blocks)."""
        return {
            "slug": self.slug,
            "date": self.date,
            "cover": self.cover,
            "readingMinutes": self.reading_minutes,
            "zh": {"title": self.zh_title, "excerpt": self.zh_excerpt, "content": self.zh_content},
            "en": {"title": self.en_title, "excerpt": self.en_excerpt, "content": self.en_content},
        }
