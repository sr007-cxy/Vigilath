import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { membershipApi, type Membership } from '../services/membershipApi';
import { useMembership } from '../hooks/useMembership';

type ContactFormState = {
  name: string;
  email: string;
  website: string;
  service: string;
  message: string;
};

const INITIAL_FORM: ContactFormState = {
  name: '',
  email: '',
  website: '',
  service: '',
  message: '',
};

function formatPrice(tier: Membership): string {
  if (tier.tier_type === 'saas') {
    if (tier.price === 0) return '¥0';
    return `¥${tier.price}`;
  }
  // Service tiers: prefer the price_range_usd from features_json if present.
  const range = tier.features_json?.price_range_usd;
  return range ?? `$${Math.round(tier.price).toLocaleString()}+`;
}

export function ProductsServices() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { isLoggedIn } = useMembership();
  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [form, setForm] = useState<ContactFormState>(INITIAL_FORM);
  const [submitting, setSubmitting] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [feedbackKind, setFeedbackKind] = useState<'success' | 'error' | null>(null);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    membershipApi
      .getMemberships()
      .then((data) => {
        if (cancelled) return;
        setMemberships(data);
      })
      .catch((err) => {
        if (cancelled) return;
        setLoadError(err instanceof Error ? err.message : 'Failed to load memberships');
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const serviceTiers = useMemo(
    () => memberships.filter((m) => m.tier_type === 'service'),
    [memberships],
  );

  const handleCTA = (tier: Membership) => {
    if (tier.slug === 'free') {
      navigate('/');
      return;
    }
    if (tier.tier_type === 'saas') {
      if (!isLoggedIn) {
        navigate('/login', {
          state: { from: `/checkout/pending?slug=${encodeURIComponent(tier.slug)}` },
        });
        return;
      }
      navigate(`/checkout/pending?slug=${encodeURIComponent(tier.slug)}`);
      return;
    }
    // service tier: scroll form into view + preselect the tier
    setForm((f) => ({ ...f, service: tier.slug }));
    document.getElementById('contact-form')?.scrollIntoView({ behavior: 'smooth' });
  };

  const handleSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    setFeedback(null);
    setFeedbackKind(null);
    try {
      const resp = await membershipApi.submitContactForm({
        name: form.name,
        email: form.email,
        website: form.website || undefined,
        tier_slug: form.service || undefined,
        message: form.message || undefined,
      });
      setFeedback(resp.message);
      setFeedbackKind('success');
      setForm(INITIAL_FORM);
    } catch (err) {
      setFeedback(err instanceof Error ? err.message : '提交失败，请稍后再试');
      setFeedbackKind('error');
    } finally {
      setSubmitting(false);
    }
  };

  const ctaLabelFor = (tier: Membership) => {
    if (tier.slug === 'free') return '立即试用';
    if (tier.tier_type === 'saas') return `立即订阅`;
    return '联系销售';
  };

  return (
    <div className="min-h-screen grid-background">
      <div className="bg-glow bg-glow-1"></div>
      <div className="bg-glow bg-glow-2"></div>
      <div className="bg-glow bg-glow-3"></div>

      <main className="flex-1 px-4 py-16 sm:py-24 hero-gradient relative z-10">
        <div className="w-full max-w-6xl mx-auto animate-fade-in">
          <section className="hero mb-16">
            <div className="max-w-3xl mx-auto text-center">
              <h1 className="text-4xl sm:text-5xl lg:text-6xl font-bold mb-6 leading-tight animate-slide-up">
                <span className="gradient-text">{t('productsServices.title')}</span>
              </h1>
              <p className="text-lg sm:text-xl text-secondary max-w-3xl mx-auto leading-relaxed animate-slide-up" style={{ animationDelay: '0.1s' }}>
                {t('productsServices.description')}
              </p>
            </div>
          </section>

          <section className="mb-20 animate-fade-in" style={{ animationDelay: '0.2s' }}>
            <h2 className="text-3xl font-bold mb-12 text-center">
              <span className="gradient-text">{t('productsServices.sections.ourServices')}</span>
            </h2>

            {isLoading && (
              <div className="text-center text-secondary">加载套餐中…</div>
            )}
            {loadError && (
              <div className="text-center text-rose-400">{loadError}</div>
            )}

            {!isLoading && !loadError && (
              <>
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-6 mb-12 items-stretch">
                  {memberships.map((tier) => (
                    <div
                      key={tier.id}
                      className={`relative h-full bg-card border rounded-2xl p-6 hover:shadow-lg hover:shadow-accent-primary/10 transition-all duration-300 flex flex-col ${tier.popular
                        ? 'border-accent-primary shadow-lg shadow-accent-primary/20'
                        : 'border-border'
                        }`}
                    >
                      {tier.popular && (
                        <div className="absolute -top-3 left-1/2 -translate-x-1/2 px-3 py-1 rounded-full bg-accent-primary text-white text-[10px] uppercase tracking-[0.2em] font-semibold whitespace-nowrap">
                          热门推荐
                        </div>
                      )}
                      <h3 className="text-lg font-bold mb-2 min-h-[1.75rem]">{tier.name}</h3>
                      <div className="flex items-baseline gap-1 mb-3 min-h-[2.5rem]">
                        <span className="text-3xl font-bold text-accent-primary">
                          {formatPrice(tier)}
                        </span>
                        {tier.tier_type === 'saas' && (
                          <span className="text-sm text-secondary font-medium">{tier.period}</span>
                        )}
                        {tier.tier_type === 'service' && (
                          <span className="text-xs text-secondary font-medium ml-1">起 / 项目</span>
                        )}
                      </div>
                      <p className="text-xs text-secondary leading-relaxed mb-4 line-clamp-3 min-h-[3.4rem]">
                        {tier.description}
                      </p>
                      <ul className="space-y-2 mb-6 flex-1">
                        {tier.features.slice(0, 5).map((feature, idx) => (
                          <li key={idx} className="flex items-start gap-2 text-xs text-primary">
                            <svg
                              className="w-4 h-4 text-accent-primary shrink-0 mt-0.5"
                              fill="none"
                              stroke="currentColor"
                              viewBox="0 0 24 24"
                            >
                              <path
                                strokeLinecap="round"
                                strokeLinejoin="round"
                                strokeWidth="2.5"
                                d="M5 13l4 4L19 7"
                              />
                            </svg>
                            <span className="leading-snug">{feature}</span>
                          </li>
                        ))}
                      </ul>
                      <div className="mt-auto">
                        <button
                          onClick={() => handleCTA(tier)}
                          className="w-full justify-center !py-3 text-sm btn-primary"
                          style={
                            tier.popular
                              ? undefined
                              : {
                                  background: '#ffffff',
                                  border: '1px solid #ffffff',
                                  color: '#0a0a0f',
                                  boxShadow: '0 4px 12px rgba(0, 0, 0, 0.25)',
                                }
                          }
                        >
                          {ctaLabelFor(tier)}
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full border-collapse">
                    <thead>
                      <tr className="bg-card border-b border-border">
                        <th className="px-4 py-3 text-left text-sm font-semibold text-secondary">#</th>
                        <th className="px-4 py-3 text-left text-sm font-semibold text-secondary">权益项</th>
                        <th className="px-4 py-3 text-center text-sm font-semibold text-secondary">免费会员</th>
                        <th className="px-4 py-3 text-center text-sm font-semibold text-secondary">检测会员</th>
                        <th className="px-4 py-3 text-center text-sm font-semibold text-secondary">Starter</th>
                        <th className="px-4 py-3 text-center text-sm font-semibold text-secondary">Growth</th>
                        <th className="px-4 py-3 text-center text-sm font-semibold text-secondary">Scale</th>
                      </tr>
                    </thead>
                    <tbody>
                      {/* 价格行 */}
                      <tr className="border-b border-border">
                        <td className="px-4 py-3 text-sm text-secondary"></td>
                        <td className="px-4 py-3 text-sm font-medium">价格</td>
                        <td className="px-4 py-3 text-center text-sm">¥0/月</td>
                        <td className="px-4 py-3 text-center text-sm">¥19.9/月</td>
                        <td className="px-4 py-3 text-center text-sm">$2K–3K</td>
                        <td className="px-4 py-3 text-center text-sm">$4K–7K</td>
                        <td className="px-4 py-3 text-center text-sm">$8K–12K</td>
                      </tr>
                      {/* 形态行 */}
                      <tr className="border-b border-border">
                        <td className="px-4 py-3 text-sm text-secondary"></td>
                        <td className="px-4 py-3 text-sm font-medium">形态</td>
                        <td className="px-4 py-3 text-center text-sm">自助 SaaS</td>
                        <td className="px-4 py-3 text-center text-sm">自助 SaaS</td>
                        <td className="px-4 py-3 text-center text-sm">人工服务</td>
                        <td className="px-4 py-3 text-center text-sm">人工服务</td>
                        <td className="px-4 py-3 text-center text-sm">人工服务</td>
                      </tr>
                      {/* 权益项1 */}
                      <tr className="border-b border-border">
                        <td className="px-4 py-3 text-sm text-secondary">1</td>
                        <td className="px-4 py-3 text-sm">是否需要注册登录</td>
                        <td className="px-4 py-3 text-center text-sm">❌ 无需</td>
                        <td className="px-4 py-3 text-center text-sm">✅ 需登录</td>
                        <td className="px-4 py-3 text-center text-sm">✅ 需登录</td>
                        <td className="px-4 py-3 text-center text-sm">✅ 需登录</td>
                        <td className="px-4 py-3 text-center text-sm">✅ 需登录</td>
                      </tr>
                      {/* 权益项2 */}
                      <tr className="border-b border-border">
                        <td className="px-4 py-3 text-sm text-secondary">2</td>
                        <td className="px-4 py-3 text-sm">检测大项数量</td>
                        <td className="px-4 py-3 text-center text-sm">5 项</td>
                        <td className="px-4 py-3 text-center text-sm">23 项</td>
                        <td className="px-4 py-3 text-center text-sm">23 项</td>
                        <td className="px-4 py-3 text-center text-sm">23 项</td>
                        <td className="px-4 py-3 text-center text-sm">23 项</td>
                      </tr>
                      {/* 权益项3 */}
                      <tr className="border-b border-border">
                        <td className="px-4 py-3 text-sm text-secondary">3</td>
                        <td className="px-4 py-3 text-sm">子检测点数量</td>
                        <td className="px-4 py-3 text-center text-sm">17 个</td>
                        <td className="px-4 py-3 text-center text-sm">全部子项</td>
                        <td className="px-4 py-3 text-center text-sm">全部子项</td>
                        <td className="px-4 py-3 text-center text-sm">全部子项</td>
                        <td className="px-4 py-3 text-center text-sm">全部子项</td>
                      </tr>
                      {/* 权益项4 */}
                      <tr className="border-b border-border">
                        <td className="px-4 py-3 text-sm text-secondary">4</td>
                        <td className="px-4 py-3 text-sm">每月检测次数</td>
                        <td className="px-4 py-3 text-center text-sm">3 次</td>
                        <td className="px-4 py-3 text-center text-sm">10 次</td>
                        <td className="px-4 py-3 text-center text-sm font-medium">50 次</td>
                        <td className="px-4 py-3 text-center text-sm font-medium">200 次</td>
                        <td className="px-4 py-3 text-center text-sm font-medium">无限</td>
                      </tr>
                      {/* 权益项5 */}
                      <tr className="border-b border-border">
                        <td className="px-4 py-3 text-sm text-secondary">5</td>
                        <td className="px-4 py-3 text-sm">优化建议（修复方案）详细度</td>
                        <td className="px-4 py-3 text-center text-sm">❌</td>
                        <td className="px-4 py-3 text-center text-sm">❌</td>
                        <td className="px-4 py-3 text-center text-sm">✅ 人工交付</td>
                        <td className="px-4 py-3 text-center text-sm">✅ 人工交付</td>
                        <td className="px-4 py-3 text-center text-sm">✅ 人工交付</td>
                      </tr>
                      {/* 权益项6 */}
                      <tr className="border-b border-border">
                        <td className="px-4 py-3 text-sm text-secondary">6</td>
                        <td className="px-4 py-3 text-sm">检测项优先级排序</td>
                        <td className="px-4 py-3 text-center text-sm">❌</td>
                        <td className="px-4 py-3 text-center text-sm">✅</td>
                        <td className="px-4 py-3 text-center text-sm">✅</td>
                        <td className="px-4 py-3 text-center text-sm">✅</td>
                        <td className="px-4 py-3 text-center text-sm">✅</td>
                      </tr>
                      {/* 权益项7 */}
                      <tr className="border-b border-border">
                        <td className="px-4 py-3 text-sm text-secondary">7</td>
                        <td className="px-4 py-3 text-sm">完整报告（23 类全量）</td>
                        <td className="px-4 py-3 text-center text-sm">❌</td>
                        <td className="px-4 py-3 text-center text-sm">✅</td>
                        <td className="px-4 py-3 text-center text-sm">✅</td>
                        <td className="px-4 py-3 text-center text-sm">✅</td>
                        <td className="px-4 py-3 text-center text-sm">✅</td>
                      </tr>
                      {/* 权益项8 */}
                      <tr className="border-b border-border">
                        <td className="px-4 py-3 text-sm text-secondary">8</td>
                        <td className="px-4 py-3 text-sm">检测历史记录</td>
                        <td className="px-4 py-3 text-center text-sm">❌</td>
                        <td className="px-4 py-3 text-center text-sm">✅</td>
                        <td className="px-4 py-3 text-center text-sm">✅</td>
                        <td className="px-4 py-3 text-center text-sm">✅</td>
                        <td className="px-4 py-3 text-center text-sm">✅</td>
                      </tr>
                      {/* 权益项9 */}
                      <tr className="border-b border-border">
                        <td className="px-4 py-3 text-sm text-secondary">9</td>
                        <td className="px-4 py-3 text-sm">技术支持</td>
                        <td className="px-4 py-3 text-center text-sm">❌</td>
                        <td className="px-4 py-3 text-center text-sm">✅ 基础</td>
                        <td className="px-4 py-3 text-center text-sm">基础工单</td>
                        <td className="px-4 py-3 text-center text-sm font-medium">优先响应 &lt; 24h</td>
                        <td className="px-4 py-3 text-center text-sm font-medium">24/7 + 专属顾问</td>
                      </tr>
                      {/* 权益项10 */}
                      <tr className="border-b border-border">
                        <td className="px-4 py-3 text-sm text-secondary">10</td>
                        <td className="px-4 py-3 text-sm">基础 GEO 覆盖</td>
                        <td className="px-4 py-3 text-center text-sm">—</td>
                        <td className="px-4 py-3 text-center text-sm">—</td>
                        <td className="px-4 py-3 text-center text-sm">✅ 基础</td>
                        <td className="px-4 py-3 text-center text-sm">✅ 基础</td>
                        <td className="px-4 py-3 text-center text-sm">✅ 全渠道定制化</td>
                      </tr>
                      {/* 权益项11 */}
                      <tr className="border-b border-border">
                        <td className="px-4 py-3 text-sm text-secondary">11</td>
                        <td className="px-4 py-3 text-sm">海外主流 LLM 收录规范</td>
                        <td className="px-4 py-3 text-center text-sm">—</td>
                        <td className="px-4 py-3 text-center text-sm">—</td>
                        <td className="px-4 py-3 text-center text-sm">✅ 完成</td>
                        <td className="px-4 py-3 text-center text-sm">✅ 适配</td>
                        <td className="px-4 py-3 text-center text-sm">✅ 全渠道</td>
                      </tr>
                      {/* 权益项12 */}
                      <tr className="border-b border-border">
                        <td className="px-4 py-3 text-sm text-secondary">12</td>
                        <td className="px-4 py-3 text-sm">网站文案建设与优化</td>
                        <td className="px-4 py-3 text-center text-sm">—</td>
                        <td className="px-4 py-3 text-center text-sm">—</td>
                        <td className="px-4 py-3 text-center text-sm">✅ 全面</td>
                        <td className="px-4 py-3 text-center text-sm">✅ 含使用场景</td>
                        <td className="px-4 py-3 text-center text-sm">✅ 定制化交易场景</td>
                      </tr>
                      {/* 权益项13 */}
                      <tr className="border-b border-border">
                        <td className="px-4 py-3 text-sm text-secondary">13</td>
                        <td className="px-4 py-3 text-sm">核心产品信息合规配置</td>
                        <td className="px-4 py-3 text-center text-sm">—</td>
                        <td className="px-4 py-3 text-center text-sm">—</td>
                        <td className="px-4 py-3 text-center text-sm">✅</td>
                        <td className="px-4 py-3 text-center text-sm">✅ 使用场景级</td>
                        <td className="px-4 py-3 text-center text-sm">✅ 定制化 + 校验</td>
                      </tr>
                      {/* 权益项14 */}
                      <tr className="border-b border-border">
                        <td className="px-4 py-3 text-sm text-secondary">14</td>
                        <td className="px-4 py-3 text-sm">持续维护</td>
                        <td className="px-4 py-3 text-center text-sm">—</td>
                        <td className="px-4 py-3 text-center text-sm">—</td>
                        <td className="px-4 py-3 text-center text-sm">✅ 项目内</td>
                        <td className="px-4 py-3 text-center text-sm">✅ 项目内</td>
                        <td className="px-4 py-3 text-center text-sm">✅ 长期</td>
                      </tr>
                      {/* 权益项15 */}
                      <tr className="border-b border-border">
                        <td className="px-4 py-3 text-sm text-secondary">15</td>
                        <td className="px-4 py-3 text-sm">付费榜单 SEO 投放</td>
                        <td className="px-4 py-3 text-center text-sm">—</td>
                        <td className="px-4 py-3 text-center text-sm">—</td>
                        <td className="px-4 py-3 text-center text-sm">❌</td>
                        <td className="px-4 py-3 text-center text-sm">✅ 低成本投放位</td>
                        <td className="px-4 py-3 text-center text-sm">✅ 性价比监测</td>
                      </tr>
                      {/* 权益项16 */}
                      <tr className="border-b border-border">
                        <td className="px-4 py-3 text-sm text-secondary">16</td>
                        <td className="px-4 py-3 text-sm">声誉管理</td>
                        <td className="px-4 py-3 text-center text-sm">—</td>
                        <td className="px-4 py-3 text-center text-sm">—</td>
                        <td className="px-4 py-3 text-center text-sm">❌</td>
                        <td className="px-4 py-3 text-center text-sm">❌</td>
                        <td className="px-4 py-3 text-center text-sm">✅ 3–5 篇/月 + 反向链接</td>
                      </tr>
                      {/* 权益项17 */}
                      <tr className="border-b border-border">
                        <td className="px-4 py-3 text-sm text-secondary">17</td>
                        <td className="px-4 py-3 text-sm">公关（PR）支持</td>
                        <td className="px-4 py-3 text-center text-sm">—</td>
                        <td className="px-4 py-3 text-center text-sm">—</td>
                        <td className="px-4 py-3 text-center text-sm">❌</td>
                        <td className="px-4 py-3 text-center text-sm">❌</td>
                        <td className="px-4 py-3 text-center text-sm">✅ 媒体报道</td>
                      </tr>
                      {/* 权益项18 */}
                      <tr className="border-b border-border">
                        <td className="px-4 py-3 text-sm text-secondary">18</td>
                        <td className="px-4 py-3 text-sm">服务周期 / 输出节奏</td>
                        <td className="px-4 py-3 text-center text-sm">—</td>
                        <td className="px-4 py-3 text-center text-sm">—</td>
                        <td className="px-4 py-3 text-center text-sm">一次性项目</td>
                        <td className="px-4 py-3 text-center text-sm">一次性 + 榜单期</td>
                        <td className="px-4 py-3 text-center text-sm">月度持续输出</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </section>

          <section id="contact-form" className="animate-fade-in" style={{ animationDelay: '0.3s' }}>
            <h2 className="text-3xl font-bold mb-12 text-center">
              <span className="gradient-text">{t('productsServices.sections.contactConsultation')}</span>
            </h2>
            <div className="max-w-2xl mx-auto">
              <div className="bg-card border border-border rounded-2xl p-8">
                <h3 className="text-xl font-bold mb-6">{t('productsServices.contact.getCustomPlan')}</h3>
                <form className="space-y-6" onSubmit={handleSubmit}>
                  <div>
                    <label htmlFor="name" className="block text-sm font-semibold mb-2">
                      {t('productsServices.contact.name')}
                    </label>
                    <input
                      type="text"
                      id="name"
                      name="name"
                      required
                      value={form.name}
                      onChange={(e) => setForm({ ...form, name: e.target.value })}
                      className="w-full px-4 py-3 rounded-lg bg-card text-white placeholder-gray-500 focus:outline-none transition-colors duration-200"
                      placeholder={t('productsServices.contact.namePlaceholder')}
                    />
                  </div>
                  <div>
                    <label htmlFor="email" className="block text-sm font-semibold mb-2">
                      {t('productsServices.contact.email')}
                    </label>
                    <input
                      type="email"
                      id="email"
                      name="email"
                      required
                      value={form.email}
                      onChange={(e) => setForm({ ...form, email: e.target.value })}
                      className="w-full px-4 py-3 rounded-lg bg-card text-white placeholder-gray-500 focus:outline-none transition-colors duration-200"
                      placeholder={t('productsServices.contact.emailPlaceholder')}
                    />
                  </div>
                  <div>
                    <label htmlFor="website" className="block text-sm font-semibold mb-2">
                      {t('productsServices.contact.website')}
                    </label>
                    <input
                      type="url"
                      id="website"
                      name="website"
                      value={form.website}
                      onChange={(e) => setForm({ ...form, website: e.target.value })}
                      className="w-full px-4 py-3 rounded-lg bg-card text-white placeholder-gray-500 focus:outline-none transition-colors duration-200"
                      placeholder={t('productsServices.contact.websitePlaceholder')}
                    />
                  </div>
                  <div>
                    <label htmlFor="service" className="block text-sm font-semibold mb-2">
                      {t('productsServices.contact.service')}
                    </label>
                    <select
                      id="service"
                      name="service"
                      value={form.service}
                      onChange={(e) => setForm({ ...form, service: e.target.value })}
                      className="w-full px-4 py-3 rounded-lg bg-card text-white placeholder-gray-500 focus:outline-none transition-colors duration-200"
                    >
                      <option value="">请选择套餐</option>
                      {serviceTiers.map((tier) => (
                        <option key={tier.slug} value={tier.slug}>
                          {tier.name} · {formatPrice(tier)}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label htmlFor="message" className="block text-sm font-semibold mb-2">
                      {t('productsServices.contact.message')}
                    </label>
                    <textarea
                      id="message"
                      name="message"
                      rows={5}
                      value={form.message}
                      onChange={(e) => setForm({ ...form, message: e.target.value })}
                      className="w-full px-4 py-3 rounded-lg bg-card text-white placeholder-gray-500 focus:outline-none transition-colors duration-200 resize-none"
                      placeholder={t('productsServices.contact.messagePlaceholder')}
                    ></textarea>
                  </div>
                  {feedback && (
                    <div
                      className={`text-sm px-4 py-3 rounded-lg ${feedbackKind === 'success'
                        ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30'
                        : 'bg-rose-500/10 text-rose-400 border border-rose-500/30'
                        }`}
                    >
                      {feedback}
                    </div>
                  )}
                  <button
                    type="submit"
                    disabled={submitting}
                    className="w-full justify-center btn-primary !py-3.5 disabled:opacity-60 disabled:cursor-not-allowed"
                  >
                    {submitting ? '提交中…' : t('productsServices.contact.submit')}
                  </button>
                </form>
              </div>
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}
