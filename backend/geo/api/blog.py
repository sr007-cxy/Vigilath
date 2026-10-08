"""Public website blog API — read-only, no auth.

GET /api/blog/posts        list (without body, small payload, for the Blog list page)
GET /api/blog/posts/{slug} single post full text (for the BlogPost detail page)

Data lives in the blog_posts table of the shared appdb (see geo/models/blog.py), read by both test and prod.
The frontend falls back to the bundled static blogPosts.ts on fetch failure, so an outage here does not blank the page.
"""
from fastapi import APIRouter
from sqlalchemy import desc

from geo.database import SessionLocal
from geo.models.blog import BlogPostORM
from geo.utils.error_handler import AppException

router = APIRouter()


@router.get("/blog/posts")
async def list_posts():
    db = SessionLocal()
    try:
        rows = (
            db.query(BlogPostORM)
            .filter(BlogPostORM.published.is_(True))
            .order_by(desc(BlogPostORM.date), desc(BlogPostORM.id))
            .all()
        )
        posts = []
        for r in rows:
            d = r.to_locale_dict()
            # The list omits bodies — 38+ bilingual full texts at once would be several hundred KB
            d["zh"].pop("content", None)
            d["en"].pop("content", None)
            posts.append(d)
        return {"posts": posts}
    finally:
        db.close()


@router.get("/blog/posts/{slug}")
async def get_post(slug: str):
    db = SessionLocal()
    try:
        row = (
            db.query(BlogPostORM)
            .filter(BlogPostORM.slug == slug, BlogPostORM.published.is_(True))
            .first()
        )
        if row is None:
            raise AppException(status_code=404, message="post not found")
        return row.to_locale_dict()
    finally:
        db.close()
