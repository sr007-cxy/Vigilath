import { useState } from 'react';
import { useTranslation } from 'react-i18next';

type Testimonial = {
  name: string;
  role: string;
  text: string;
};

// 带原始下标,头像按下标取 /image/faces/tNN.webp
type Indexed = Testimonial & { idx: number };

// 名字首字取作头像兜底文字(中文取最后一个字,英文取首字母)
function avatarLabel(name: string): string {
  const trimmed = name.trim();
  if (!trimmed) return '·';
  const cjk = trimmed.match(/[一-龥]/g);
  if (cjk) return cjk[cjk.length - 1];
  return trimmed[0].toUpperCase();
}

const AVATAR_GRADIENTS = [
  'linear-gradient(135deg, #6366f1, #8b5cf6)',
  'linear-gradient(135deg, #0ea5e9, #22d3ee)',
  'linear-gradient(135deg, #f59e0b, #f97316)',
  'linear-gradient(135deg, #10b981, #059669)',
  'linear-gradient(135deg, #ec4899, #f43f5e)',
  'linear-gradient(135deg, #8b5cf6, #d946ef)',
];

function avatarFor(idx: number): string {
  return `/image/faces/t${String(idx + 1).padStart(2, '0')}.webp`;
}

function Avatar({ item }: { item: Indexed }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <span
        className="h-11 w-11 shrink-0 rounded-full flex items-center justify-center text-sm font-bold text-white select-none ring-2 ring-white/70"
        style={{ background: AVATAR_GRADIENTS[item.idx % AVATAR_GRADIENTS.length] }}
        aria-hidden="true"
      >
        {avatarLabel(item.name)}
      </span>
    );
  }
  return (
    <img
      src={avatarFor(item.idx)}
      alt={item.name}
      loading="lazy"
      draggable={false}
      onError={() => setFailed(true)}
      className="h-11 w-11 shrink-0 rounded-full object-cover select-none ring-2 ring-white/70 shadow-sm"
    />
  );
}

function Card({ item }: { item: Indexed }) {
  return (
    <figure className="shrink-0 w-[300px] sm:w-[348px] rounded-2xl bg-surface border border-soft shadow-glow px-6 py-5 flex flex-col gap-3.5 transition-all duration-200 hover:-translate-y-1 hover:shadow-lg">
      <div className="flex items-center gap-3">
        <Avatar item={item} />
        <div className="min-w-0">
          <span className="block text-sm font-semibold text-primary truncate">{item.name}</span>
          <span className="block text-xs text-secondary truncate">{item.role}</span>
        </div>
        {/* 五星 */}
        <div className="flex gap-0.5 ml-auto" aria-hidden="true">
          {Array.from({ length: 5 }).map((_, i) => (
            <svg key={i} className="h-3.5 w-3.5" viewBox="0 0 20 20" fill="#f59e0b">
              <path d="M9.05 2.93c.3-.92 1.6-.92 1.9 0l1.37 4.22a1 1 0 00.95.69h4.44c.97 0 1.37 1.24.59 1.81l-3.6 2.61a1 1 0 00-.36 1.12l1.37 4.22c.3.92-.75 1.69-1.54 1.12l-3.6-2.61a1 1 0 00-1.18 0l-3.6 2.61c-.78.57-1.83-.2-1.53-1.12l1.37-4.22a1 1 0 00-.36-1.12L1.1 9.66c-.78-.57-.38-1.81.59-1.81h4.44a1 1 0 00.95-.69L8.45 2.93z" />
            </svg>
          ))}
        </div>
      </div>
      <blockquote className="text-sm leading-relaxed text-primary">{item.text}</blockquote>
    </figure>
  );
}

function Row({ items, durationSec, reverse }: { items: Indexed[]; durationSec: number; reverse?: boolean }) {
  // 渲染两遍实现无缝循环
  const loop = [...items, ...items];
  return (
    <div
      className={`testimonial-track${reverse ? ' testimonial-track--reverse' : ''} gap-4 sm:gap-5 py-2`}
      style={{ ['--marquee-duration' as string]: `${durationSec}s` }}
    >
      {loop.map((item, i) => (
        <Card key={i} item={item} />
      ))}
    </div>
  );
}

export function TestimonialsCarousel() {
  const { t } = useTranslation();
  const raw = t('home.testimonials.items', { returnObjects: true }) as Testimonial[];

  if (!Array.isArray(raw) || raw.length === 0) return null;

  const items: Indexed[] = raw.map((it, idx) => ({ ...it, idx }));
  // 交错分两排:偶数索引在上,奇数索引在下
  const top = items.filter((_, i) => i % 2 === 0);
  const bottom = items.filter((_, i) => i % 2 === 1);

  return (
    <section className="mt-10 sm:mt-30">
      <div className="text-center mb-8 sm:mb-10">
        <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-surface border border-soft shadow-glow">
          <svg
            xmlns="http://www.w3.org/2000/svg"
            className="h-3.5 w-3.5 text-secondary"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M7 8h10M7 12h6m-6 8l-4-4h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12z"
            />
          </svg>
          <span className="text-xs font-semibold text-secondary">{t('home.testimonials.badge')}</span>
        </div>
      </div>

      {/* 双排反向无缝轮播.两侧渐隐 + 悬停暂停 */}
      <div
        className="testimonial-marquee relative overflow-hidden flex flex-col gap-4 sm:gap-5"
        style={{
          maskImage: 'linear-gradient(to right, transparent, #000 7%, #000 93%, transparent)',
          WebkitMaskImage: 'linear-gradient(to right, transparent, #000 7%, #000 93%, transparent)',
        }}
      >
        <Row items={top} durationSec={52} />
        <Row items={bottom} durationSec={64} reverse />
      </div>
    </section>
  );
}
