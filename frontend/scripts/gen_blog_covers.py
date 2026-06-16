#!/usr/bin/env python3
"""生成博客封面 SVG —— 自包含、不依赖外部图床,8 篇文章各一张同语言、异色调的封面。

设计语言:深空蓝渐变底 + 主题色辉光 + 点阵网格 + 单个圆角线性图标 + 品牌放大镜小标。
跨三套主题(极简/浅色/深色)都在卡片图区独立成图,不受站点背景影响。
输出到 frontend/public/image/blog/<slug>.svg
"""
import os

OUT = os.path.join(os.path.dirname(__file__), "..", "public", "image", "blog")
W, H = 1200, 675

# slug, 主色, 副色, 角标文字(中), 图标 id
COVERS = [
    ("what-is-geo",              "#22d3ee", "#7b61ff", "GEO 基础",   "bubble"),
    ("how-ai-picks-sources",     "#a78bfa", "#22d3ee", "信源机制",   "network"),
    ("geo-for-global-brands",    "#2dd4bf", "#38bdf8", "多引擎",     "globe"),
    ("geo-vs-aeo",               "#fbbf24", "#fb7185", "概念辨析",   "venn"),
    ("monitor-brand-in-ai-answers","#38bdf8","#7b61ff","持续监控",   "chart"),
    ("wikipedia-and-ai-visibility","#818cf8","#22d3ee","权威信源",   "book"),
    ("make-content-ai-crawlable","#34d399", "#22d3ee", "收录侧",     "crawl"),
    ("ai-hallucination-brand-risk","#fb7185","#f59e0b","舆情风险",   "warning"),
]


def icon(kind, cx, cy, c, c2):
    """返回居中(cx,cy)的线性图标 SVG 片段。统一描边风格。"""
    s = f'stroke="{c}" stroke-width="11" fill="none" stroke-linecap="round" stroke-linejoin="round"'
    s2 = f'stroke="{c2}" stroke-width="11" fill="none" stroke-linecap="round" stroke-linejoin="round"'
    if kind == "bubble":
        return f'''
        <rect x="{cx-150}" y="{cy-120}" width="300" height="200" rx="36" {s}/>
        <path d="M {cx-60} {cy+80} L {cx-30} {cy+140} L {cx+10} {cy+80}" {s}/>
        <path d="M {cx} {cy-70} L {cx+14} {cy-22} L {cx+62} {cy-8} L {cx+14} {cy+6} L {cx} {cy+54} L {cx-14} {cy+6} L {cx-62} {cy-8} L {cx-14} {cy-22} Z" fill="{c2}" stroke="none"/>
        <line x1="{cx-100}" y1="{cy+18}" x2="{cx-30}" y2="{cy+18}" {s2}/>
        '''
    if kind == "network":
        sat = [(-160, -90), (170, -70), (140, 110), (-150, 100)]
        lines = "".join(f'<line x1="{cx}" y1="{cy}" x2="{cx+dx}" y2="{cy+dy}" {s2}/>' for dx, dy in sat)
        dots = ""
        stroke_only = f'stroke="{c}" stroke-width="11"'
        for i, (dx, dy) in enumerate(sat):
            fill = c if i == 1 else "none"
            dots += f'<circle cx="{cx+dx}" cy="{cy+dy}" r="34" fill="{fill}" {stroke_only}/>'
        return f'{lines}<circle cx="{cx}" cy="{cy}" r="50" fill="{c2}" stroke="none" opacity="0.18"/><circle cx="{cx}" cy="{cy}" r="50" {s}/>{dots}'
    if kind == "globe":
        return f'''
        <circle cx="{cx}" cy="{cy}" r="150" {s}/>
        <line x1="{cx-150}" y1="{cy}" x2="{cx+150}" y2="{cy}" {s}/>
        <ellipse cx="{cx}" cy="{cy}" rx="64" ry="150" {s}/>
        <path d="M {cx-140} {cy-58} Q {cx} {cy-30} {cx+140} {cy-58}" {s2}/>
        <path d="M {cx-140} {cy+58} Q {cx} {cy+30} {cx+140} {cy+58}" {s2}/>
        <circle cx="{cx+150}" cy="{cy-150}" r="20" fill="{c2}" stroke="none"/>
        <circle cx="{cx-170}" cy="{cy-120}" r="14" fill="{c}" stroke="none"/>
        '''
    if kind == "venn":
        return f'''
        <circle cx="{cx-60}" cy="{cy}" r="125" {s}/>
        <circle cx="{cx+60}" cy="{cy}" r="125" {s2}/>
        <path d="M {cx} {cy-95} A 125 125 0 0 1 {cx} {cy+95} A 125 125 0 0 1 {cx} {cy-95} Z" fill="{c}" opacity="0.16" stroke="none"/>
        '''
    if kind == "chart":
        pts = [(-180, 70), (-100, 20), (-30, 50), (50, -40), (130, -10), (190, -90)]
        poly = " ".join(f"{cx+dx},{cy+dy}" for dx, dy in pts)
        dots = "".join(f'<circle cx="{cx+dx}" cy="{cy+dy}" r="12" fill="{c2}" stroke="none"/>' for dx, dy in pts)
        return f'''
        <line x1="{cx-190}" y1="{cy-110}" x2="{cx-190}" y2="{cy+110}" {s} opacity="0.5"/>
        <line x1="{cx-190}" y1="{cy+110}" x2="{cx+200}" y2="{cy+110}" {s} opacity="0.5"/>
        <polyline points="{poly}" {s}/>{dots}
        '''
    if kind == "book":
        return f'''
        <path d="M {cx} {cy-110} C {cx-60} {cy-140} {cx-150} {cy-130} {cx-180} {cy-110} L {cx-180} {cy+110} C {cx-150} {cy+90} {cx-60} {cy+80} {cx} {cy+110}" {s}/>
        <path d="M {cx} {cy-110} C {cx+60} {cy-140} {cx+150} {cy-130} {cx+180} {cy-110} L {cx+180} {cy+110} C {cx+150} {cy+90} {cx+60} {cy+80} {cx} {cy+110}" {s}/>
        <line x1="{cx}" y1="{cy-110}" x2="{cx}" y2="{cy+110}" {s}/>
        <line x1="{cx-145}" y1="{cy-78}" x2="{cx-45}" y2="{cy-90}" {s2}/>
        <line x1="{cx-145}" y1="{cy-30}" x2="{cx-45}" y2="{cy-42}" {s2}/>
        <line x1="{cx+45}" y1="{cy-90}" x2="{cx+145}" y2="{cy-78}" {s2}/>
        <line x1="{cx+45}" y1="{cy-42}" x2="{cx+145}" y2="{cy-30}" {s2}/>
        '''
    if kind == "crawl":
        return f'''
        <rect x="{cx-150}" y="{cy-130}" width="240" height="260" rx="24" {s}/>
        <line x1="{cx-110}" y1="{cy-80}" x2="{cx+40}" y2="{cy-80}" {s2}/>
        <line x1="{cx-110}" y1="{cy-30}" x2="{cx+50}" y2="{cy-30}" {s2}/>
        <line x1="{cx-110}" y1="{cy+20}" x2="{cx+10}" y2="{cy+20}" {s2}/>
        <circle cx="{cx+120}" cy="{cy+70}" r="56" {s}/>
        <line x1="{cx+160}" y1="{cy+110}" x2="{cx+205}" y2="{cy+155}" {s}/>
        <path d="M {cx+96} {cy+70} L {cx+114} {cy+88} L {cx+146} {cy+50}" {s2}/>
        '''
    if kind == "warning":
        return f'''
        <path d="M {cx} {cy-130} L {cx+160} {cy+120} L {cx-160} {cy+120} Z" {s}/>
        <line x1="{cx}" y1="{cy-40}" x2="{cx}" y2="{cy+40}" {s2}/>
        <circle cx="{cx}" cy="{cy+82}" r="8" fill="{c2}" stroke="none"/>
        '''
    return ""


def cover(slug, c, c2, kicker, kind):
    cx, cy = 760, 330
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-label="Vigilath blog cover">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#0a0f1f"/>
      <stop offset="1" stop-color="#141a35"/>
    </linearGradient>
    <radialGradient id="glow" cx="0.62" cy="0.3" r="0.7">
      <stop offset="0" stop-color="{c}" stop-opacity="0.34"/>
      <stop offset="1" stop-color="{c}" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="glow2" cx="0.2" cy="0.85" r="0.6">
      <stop offset="0" stop-color="{c2}" stop-opacity="0.22"/>
      <stop offset="1" stop-color="{c2}" stop-opacity="0"/>
    </radialGradient>
    <pattern id="dots" width="36" height="36" patternUnits="userSpaceOnUse">
      <circle cx="2" cy="2" r="1.5" fill="#ffffff" opacity="0.06"/>
    </pattern>
  </defs>
  <rect width="{W}" height="{H}" fill="url(#bg)"/>
  <rect width="{W}" height="{H}" fill="url(#dots)"/>
  <rect width="{W}" height="{H}" fill="url(#glow)"/>
  <rect width="{W}" height="{H}" fill="url(#glow2)"/>
  {icon(kind, cx, cy, c, c2)}
  <!-- 品牌放大镜小标 -->
  <g transform="translate(64,592)">
    <circle cx="16" cy="14" r="15" fill="none" stroke="{c}" stroke-width="5"/>
    <line x1="27" y1="25" x2="40" y2="38" stroke="{c}" stroke-width="5" stroke-linecap="round"/>
    <text x="58" y="22" font-family="-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif" font-size="26" font-weight="800" fill="#f8fafc" letter-spacing="3">VIGILATH</text>
  </g>
  <!-- 角标 -->
  <g transform="translate(64,72)">
    <rect x="0" y="0" width="{28 + len(kicker)*30}" height="46" rx="23" fill="{c}" fill-opacity="0.14" stroke="{c}" stroke-opacity="0.5" stroke-width="2"/>
    <text x="20" y="31" font-family="-apple-system,PingFang SC,Microsoft YaHei,sans-serif" font-size="24" font-weight="700" fill="{c}">{kicker}</text>
  </g>
</svg>
'''
    return svg


def main():
    os.makedirs(OUT, exist_ok=True)
    for slug, c, c2, kicker, kind in COVERS:
        path = os.path.join(OUT, f"{slug}.svg")
        with open(path, "w", encoding="utf-8") as f:
            f.write(cover(slug, c, c2, kicker, kind))
        print("wrote", os.path.relpath(path))


if __name__ == "__main__":
    main()
