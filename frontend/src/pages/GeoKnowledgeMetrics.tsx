import { useTranslation } from 'react-i18next';
import { GeoKnowledgeTabs } from '../components/GeoKnowledgeTabs';

type Category = {
  id: string;
  items: string[];
};

const CATEGORIES: Category[] = [
  {
    id: 'crawlability',
    items: ['https', 'robots', 'sitemap', 'llms', 'aiCrawlerAccess'],
  },
];

function MetricCard({ ns }: { ns: string }) {
  const { t } = useTranslation();
  const howto = t(`${ns}.howto`, { returnObjects: true }) as unknown;
  const steps = Array.isArray(howto) ? (howto as string[]) : [];

  return (
    <details
      className="group rounded-xl overflow-hidden border"
      style={{
        background: 'var(--bg-card)',
        borderColor: 'var(--border-color)',
      }}
    >
      <summary
        className="px-6 py-4 cursor-pointer flex items-center justify-between list-none"
        style={{ color: 'var(--text-primary)' }}
      >
        <span className="font-semibold">{t(`${ns}.name`)}</span>
        <svg
          className="w-5 h-5 transition-transform group-open:rotate-180"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
          style={{ color: 'var(--text-secondary)' }}
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </summary>
      <div
        className="px-6 pb-6 pt-4 space-y-5 border-t"
        style={{ borderColor: 'var(--border-color)' }}
      >
        <div>
          <h4
            className="text-[11px] uppercase tracking-wider font-bold mb-2"
            style={{ color: 'var(--text-muted)' }}
          >
            {t('geoKnowledge.metrics.field.measures')}
          </h4>
          <p
            className="text-sm leading-relaxed"
            style={{ color: 'var(--text-primary)' }}
          >
            {t(`${ns}.measures`)}
          </p>
        </div>
        <div>
          <h4
            className="text-[11px] uppercase tracking-wider font-bold mb-2"
            style={{ color: 'var(--text-muted)' }}
          >
            {t('geoKnowledge.metrics.field.why')}
          </h4>
          <p
            className="text-sm leading-relaxed"
            style={{ color: 'var(--text-primary)' }}
          >
            {t(`${ns}.why`)}
          </p>
        </div>
        <div>
          <h4
            className="text-[11px] uppercase tracking-wider font-bold mb-2"
            style={{ color: 'var(--text-muted)' }}
          >
            {t('geoKnowledge.metrics.field.scoring')}
          </h4>
          <p
            className="text-sm leading-relaxed"
            style={{ color: 'var(--text-primary)' }}
          >
            {t(`${ns}.scoring`)}
          </p>
        </div>
        {steps.length > 0 && (
          <div>
            <h4
              className="text-[11px] uppercase tracking-wider font-bold mb-2"
              style={{ color: 'var(--text-muted)' }}
            >
              {t('geoKnowledge.metrics.field.howto')}
            </h4>
            <ul className="space-y-1.5 text-sm" style={{ color: 'var(--text-primary)' }}>
              {steps.map((step, i) => (
                <li key={i} className="flex items-start gap-2 leading-relaxed">
                  <span style={{ color: 'var(--accent-primary)' }}>▸</span>
                  <span>{step}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </details>
  );
}

export function GeoKnowledgeMetrics() {
  const { t } = useTranslation();

  return (
    <div className="min-h-screen grid-background">
      <div className="bg-glow bg-glow-1"></div>
      <div className="bg-glow bg-glow-2"></div>
      <div className="bg-glow bg-glow-3"></div>

      <main className="flex-1 px-4 py-16 sm:py-24 hero-gradient relative z-10">
        <div className="w-full max-w-5xl mx-auto animate-fade-in">
          <section className="hero mb-10">
            <div className="max-w-3xl mx-auto text-center">
              <h1 className="text-4xl sm:text-5xl lg:text-6xl font-bold mb-6 leading-tight">
                <span className="gradient-text">{t('geoKnowledge.metrics.title')}</span>
              </h1>
              <p
                className="text-lg sm:text-xl max-w-3xl mx-auto leading-relaxed"
                style={{ color: 'var(--text-secondary)' }}
              >
                {t('geoKnowledge.metrics.description')}
              </p>
            </div>
          </section>

          <GeoKnowledgeTabs />

          <section className="space-y-14">
            {CATEGORIES.map((cat) => (
              <div key={cat.id} className="animate-fade-in">
                <h2 className="text-2xl sm:text-3xl font-bold mb-2" style={{ color: 'var(--text-primary)' }}>
                  {t(`geoKnowledge.metrics.categories.${cat.id}.title`)}
                </h2>
                <p
                  className="text-sm sm:text-base mb-6 max-w-3xl"
                  style={{ color: 'var(--text-secondary)' }}
                >
                  {t(`geoKnowledge.metrics.categories.${cat.id}.description`)}
                </p>
                <div className="space-y-3">
                  {cat.items.map((item) => (
                    <MetricCard
                      key={item}
                      ns={`geoKnowledge.metrics.categories.${cat.id}.items.${item}`}
                    />
                  ))}
                </div>
              </div>
            ))}
          </section>
        </div>
      </main>
    </div>
  );
}
