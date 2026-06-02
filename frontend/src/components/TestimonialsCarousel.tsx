import { useTranslation } from 'react-i18next';

type Testimonial = {
  name: string;
  role: string;
  text: string;
};

// 名字首字取作头像文字(中文取最后一个字,英文取首字母)
function avatarLabel(name: string): string {
  const trimmed = name.trim();
  if (!trimmed) return '·';
  const hasCJK = /[一-龥]/.test(trimmed);
  if (hasCJK) {
    const cjk = trimmed.match(/[一-龥]/g);
    return cjk ? cjk[cjk.length - 1] : trimmed.slice(-1);
  }
  return trimmed[0].toUpperCase();
}

// 由名字派生稳定的柔和渐变色(同名同色,刷新不跳)
const AVATAR_GRADIENTS = [
  'linear-gradient(135deg, #6366f1, #8b5cf6)',
  'linear-gradient(135deg, #0ea5e9, #22d3ee)',
  'linear-gradient(135deg, #f59e0b, #f97316)',
  'linear-gradient(135deg, #10b981, #059669)',
  'linear-gradient(135deg, #ec4899, #f43f5e)',
  'linear-gradient(135deg, #8b5cf6, #d946ef)',
];

function avatarGradient(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i += 1) {
    hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  }
  return AVATAR_GRADIENTS[hash % AVATAR_GRADIENTS.length];
}

function Card({ item }: { item: Testimonial }) {
  return (
    <figure
      className="shrink-0 w-[300px] sm:w-[360px] rounded-2xl bg-surface border border-soft shadow-glow px-6 py-5 flex flex-col gap-4 transition-transform duration-200 hover:-translate-y-0.5"
    >
      {/* 五星 */}
      <div className="flex gap-0.5" aria-hidden="true">
        {Array.from({ length: 5 }).map((_, i) => (
          <svg key={i} className="h-4 w-4" viewBox="0 0 20 20" fill="#f59e0b">
            <path d="M9.05 2.93c.3-.92 1.6-.92 1.9 0l1.37 4.22a1 1 0 00.95.69h4.44c.97 0 1.37 1.24.59 1.81l-3.6 2.61a1 1 0 00-.36 1.12l1.37 4.22c.3.92-.75 1.69-1.54 1.12l-3.6-2.61a1 1 0 00-1.18 0l-3.6 2.61c-.78.57-1.83-.2-1.53-1.12l1.37-4.22a1 1 0 00-.36-1.12L1.1 9.66c-.78-.57-.38-1.81.59-1.81h4.44a1 1 0 00.95-.69L8.45 2.93z" />
          </svg>
        ))}
      </div>

      {/* 评价正文 */}
      <blockquote className="text-sm leading-relaxed text-primary flex-1">
        {item.text}
      </blockquote>

      {/* 头像 + 署名 */}
      <figcaption className="flex items-center gap-3 mt-1">
        <span
          className="h-10 w-10 shrink-0 rounded-full flex items-center justify-center text-sm font-bold text-white select-none"
          style={{ background: avatarGradient(item.name) }}
          aria-hidden="true"
        >
          {avatarLabel(item.name)}
        </span>
        <span className="min-w-0">
          <span className="block text-sm font-semibold text-primary truncate">
            {item.name}
          </span>
          <span className="block text-xs text-secondary truncate">
            {item.role}
          </span>
        </span>
      </figcaption>
    </figure>
  );
}

export function TestimonialsCarousel() {
  const { t } = useTranslation();
  const items = t('home.testimonials.items', { returnObjects: true }) as Testimonial[];

  if (!Array.isArray(items) || items.length === 0) return null;

  // 渲染两遍以实现无缝循环;时长随条数线性增长,速度恒定
  const loop = [...items, ...items];
  const durationSec = Math.max(40, items.length * 6);

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
          <span className="text-xs font-semibold text-secondary">
            {t('home.testimonials.badge')}
          </span>
        </div>
      </div>

      {/* 无缝横向轮播.两侧渐隐遮罩 + 悬停暂停 */}
      <div
        className="testimonial-marquee relative overflow-hidden"
        style={{
          ['--marquee-duration' as string]: `${durationSec}s`,
          maskImage:
            'linear-gradient(to right, transparent, #000 6%, #000 94%, transparent)',
          WebkitMaskImage:
            'linear-gradient(to right, transparent, #000 6%, #000 94%, transparent)',
        }}
      >
        <div className="testimonial-track gap-4 sm:gap-5 py-2">
          {loop.map((item, idx) => (
            <Card key={idx} item={item} />
          ))}
        </div>
      </div>
    </section>
  );
}
