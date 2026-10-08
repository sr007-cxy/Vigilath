// System architecture page -- top tagline + project intro, directly showing the overall architecture diagram (no title),
// followed by the "Core system" intro: three-layer runtime model + business loop + knowledge-enhanced data foundation + external connections.
// Diagram: docs/03-系统设计/系统架构/a-v1.0.png -> public/arch/architecture.png;
// Content source: docs/03-系统设计/系统架构/系统到模块级介绍.md (framing: autonomous optimization engine, not a "platform").
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { PageHead } from '../components/PageHead';
import { useContactModal } from '../components/ContactModalContext';
import { SolutionIcon } from '../components/SolutionIcon';
import type { Locale } from '../data/solutions';

type LT = Record<Locale, string>;

type TextBlock = {
  title: LT;
  desc: LT;
};

type LayerBlock = TextBlock & {
  meta: LT;
};

// Three-layer runtime model (doc §6–8)
const STACK: LayerBlock[] = [
  {
    title: { zh: '核心引擎', en: 'Core Engine' },
    meta: { zh: '业务编排层', en: 'Business orchestration' },
    desc: {
      zh: '业务编排中心,统一调度检测、会员、支付、内容生成、遥测、舆情与后台审核;含业务路由层、检测评分引擎、海外引擎直连通道。',
      en: 'The orchestration hub for checks, membership, payments, content, telemetry, sentiment and admin review — with the business router, the scoring engine and the overseas direct channel.',
    },
  },
  {
    title: { zh: '智能优化 Agent', en: 'Optimization Agent' },
    meta: { zh: '对话执行层', en: 'Conversational execution' },
    desc: {
      zh: '对话式执行层,把自然语言意图转成结构化工作流:建主题、诊断、扩词、生成、发布、看效果;独立伸缩以隔离依赖。',
      en: 'Turns natural-language intent into structured workflows — topic, diagnosis, expansion, generation, publishing and review — scaling independently to isolate dependencies.',
    },
  },
  {
    title: { zh: '监测执行服务层', en: 'Monitoring Execution Layer' },
    meta: { zh: '采样与采集层', en: 'Sampling & collection' },
    desc: {
      zh: '把「要监测什么」变成实际采样与聚合:遥测调度、舆情哨兵、浏览器引擎(账号池)、热榜聚合。',
      en: 'Turns "what to monitor" into real sampling and aggregation — telemetry dispatch, sentiment sentinel, browser engine (account pool) and hot-topic aggregation.',
    },
  },
];

// Core business loop modules (doc §9, assess -> monitor -> optimize -> operate -> collaborate, 8 in total)
const BUSINESS_LOOP: TextBlock[] = [
  {
    title: { zh: '网站审计', en: 'Site audit' },
    desc: { zh: '从网站与内容资产入手,输出 GEO/AEO 评分、分项诊断与修复清单。', en: 'Starts from site and content assets, outputting GEO/AEO scores, diagnosis and fixes.' },
  },
  {
    title: { zh: '问答采样', en: 'Q&A sampling' },
    desc: { zh: '在多个国内外 AI 引擎采集真实回答、引用链接与排名证据。', en: 'Collects real answers, citations and ranking evidence across domestic and global engines.' },
  },
  {
    title: { zh: '品牌露出', en: 'Brand exposure' },
    desc: { zh: '计算露出率、Top1 / Top3、竞品占位与 SAIV。', en: 'Computes exposure rate, Top1 / Top3, competitor occupation and SAIV.' },
  },
  {
    title: { zh: '引用分析', en: 'Citation analysis' },
    desc: { zh: '识别引用来源、自有信源占比、信源偏好与引用缺口。', en: 'Identifies cited sources, owned-source share, source preference and citation gaps.' },
  },
  {
    title: { zh: '优化闭环', en: 'Optimization loop' },
    desc: { zh: '按诊断与采样结果生成内容与发布策略,并在复测中更新。', en: 'Generates content and publishing strategy from findings, updated by retesting.' },
  },
  {
    title: { zh: '运营报表', en: 'Operations reports' },
    desc: { zh: '把持续监测结果转成日报、周报、月报、趋势与预警。', en: 'Turns continuous monitoring into daily / weekly / monthly reports, trends and alerts.' },
  },
  {
    title: { zh: '风险治理', en: 'Risk governance' },
    desc: { zh: '监控舆情、合规、幻觉与错误回答风险,输出告警与处置单。', en: 'Monitors sentiment, compliance, hallucination and wrong-answer risk with alerts and tickets.' },
  },
  {
    title: { zh: '智能协同入口', en: 'Collaboration entry' },
    desc: { zh: '让用户通过企业 IM 或对话式 Agent 调用引擎能力。', en: 'Lets users invoke engine capabilities via enterprise IM or a conversational agent.' },
  },
];

// Knowledge-enhanced data foundation (doc §10)
const DATA_FOUNDATION: TextBlock[] = [
  {
    title: { zh: '业务主库', en: 'Primary database' },
    desc: { zh: '系统事实源:账号、主题、任务、报告、舆情、合规、审计、支付与会话历史。', en: 'The fact source: accounts, topics, tasks, reports, sentiment, compliance, audit, payments and dialogue.' },
  },
  {
    title: { zh: '报表缓存', en: 'Report cache' },
    desc: { zh: '高频数据与任务状态缓存:报告加载、跨 worker 状态、限流与降级安全。', en: 'Caches hot data and task state: report loading, cross-worker state, rate-limit and graceful degradation.' },
  },
  {
    title: { zh: '图数据库', en: 'Graph database' },
    desc: { zh: '知识图谱底座:企业画像、实体、竞品关系与引用关系,支撑实体审计与引用归因。', en: 'The knowledge-graph base: enterprise profiles, entities, competitor and citation relations for entity audit and citation attribution.' },
  },
  {
    title: { zh: '向量数据库', en: 'Vector database' },
    desc: { zh: 'RAG 与语义检索底座:资料向量、相似 Query 索引,增强内容生成与 Agent 知识问答。', en: 'The RAG and semantic-retrieval base: material vectors and similar-query indexing that enrich content generation and agent Q&A.' },
  },
];

// External connections (doc §11, three groups)
const EXTERNAL: TextBlock[] = [
  {
    title: { zh: '国内 AI 引擎', en: 'Domestic AI engines' },
    desc: { zh: 'DeepSeek、通义千问、豆包、文心一言、腾讯元宝、Kimi —— 主要经浏览器引擎服务或专用 API 通道采样。', en: 'DeepSeek, Qwen, Doubao, Ernie, Tencent Yuanbao, Kimi — sampled mainly via the browser engine service or dedicated API channels.' },
  },
  {
    title: { zh: '发布与通知通道', en: 'Publish & notify' },
    desc: { zh: '内容发布渠道、邮件通知、企业 IM 通知与 Webhook,接收优化闭环输出并回传触达状态。', en: 'Content publishing, email, enterprise-IM notifications and webhooks — receiving loop output and returning reach status.' },
  },
];

export function Architecture() {
  const { t, i18n } = useTranslation();
  const { openContact } = useContactModal();
  const locale: Locale = i18n.language === 'en' ? 'en' : 'zh';
  const isZh = locale === 'zh';

  return (
    <div className="min-h-screen grid-background">
      <PageHead
        titleKey={isZh ? '系统架构 · Vigilath' : 'System Architecture · Vigilath'}
        titleFallback={isZh ? '系统架构 · Vigilath' : 'System Architecture · Vigilath'}
        descriptionKey={isZh ? '面向全球企业的 AI 可见性自主优化引擎架构:核心引擎、智能优化 Agent、监测执行服务层与知识增强数据底座。' : 'Architecture of the Autonomous AI Visibility Engine: Core Engine, Optimization Agent, Monitoring Execution Layer and knowledge-enhanced data foundations.'}
        descriptionFallback={isZh ? '面向全球企业的 AI 可见性自主优化引擎架构:核心引擎、智能优化 Agent、监测执行服务层与知识增强数据底座。' : 'Architecture of the Autonomous AI Visibility Engine: Core Engine, Optimization Agent, Monitoring Execution Layer and knowledge-enhanced data foundations.'}
      />
      <div className="bg-glow bg-glow-1"></div>
      <div className="bg-glow bg-glow-2"></div>
      <div className="bg-glow bg-glow-3"></div>

      <main className="relative z-10 flex-1">
        {/* ── Main architecture diagram (visible on entry, shown directly, no title) ──────── */}
        <section className="px-4 pt-10 pb-6 sm:pt-14">
          <div className="mx-auto w-full max-w-7xl animate-fade-in">
            <nav className="mb-6 flex flex-wrap items-center gap-2 text-sm" style={{ color: 'var(--text-muted)' }}>
              <Link to="/" className="transition-colors hover:text-primary">{t('nav.home')}</Link>
              <span>/</span>
              <span style={{ color: 'var(--text-secondary)' }}>{t('nav.solutions')}</span>
              <span>/</span>
              <span style={{ color: 'var(--text-secondary)' }}>{isZh ? '系统架构' : 'System Architecture'}</span>
            </nav>

            <figure className="arch-figure overflow-x-auto rounded-3xl border border-border bg-white p-3 shadow-sm sm:p-6" style={{ boxShadow: 'var(--glow-primary)' }}>
              <img
                src="/arch/architecture.png"
                alt={isZh ? 'Vigilath 逻辑架构图:用户入口、负载均衡、核心引擎、智能优化 Agent、监测执行服务层、核心业务闭环、业务主库、报表缓存、图数据库、向量数据库与外部 AI 引擎' : 'Vigilath logical architecture: user entry, load balancing, Core Engine, Optimization Agent, Monitoring Execution Layer, business loop, primary database, cache, graph database, vector database and external AI engines'}
                className="mx-auto block h-auto"
                style={{ minWidth: '900px' }}
                loading="eager"
              />
            </figure>
          </div>
        </section>

        {/* ── Core system (per the documentation) ─────────────────── */}
        <section className="px-4 py-8 sm:py-10">
          <div className="mx-auto w-full max-w-7xl">
            <p className="mt-2 max-w-3xl text-sm leading-relaxed text-secondary">
              {isZh
                ? '系统由用户入口、负载均衡、核心引擎、智能优化 Agent、监测执行服务层、核心业务闭环模块、数据层与外部依赖共同组成,三个核心单元互相独立、隔离伸缩。'
                : 'The system spans user entry, load balancing, the Core Engine, the Optimization Agent, the Monitoring Execution Layer, the core business loop, the data layer and external dependencies — with three independent, separately scalable core units.'}
            </p>

            {/* Three-layer runtime model */}
            <div className="mt-8">
              <h3 className="text-lg font-bold text-primary">{isZh ? '三层运行模型' : 'Three-layer operating model'}</h3>
              <div className="mt-4 grid gap-3 md:grid-cols-3">
                {STACK.map((item) => (
                  <div key={item.title[locale]} className="rounded-2xl border border-border bg-card p-5 shadow-sm">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <h4 className="text-base font-bold text-primary">{item.title[locale]}</h4>
                      <span className="rounded-full bg-accent-primary/10 px-2.5 py-1 text-[11px] font-semibold" style={{ color: 'var(--accent-primary)' }}>
                        {item.meta[locale]}
                      </span>
                    </div>
                    <p className="mt-2 text-sm leading-relaxed text-secondary">{item.desc[locale]}</p>
                  </div>
                ))}
              </div>
            </div>

            {/* 8 business loop modules */}
            <div className="mt-10">
              <h3 className="text-lg font-bold text-primary">{isZh ? '核心业务闭环' : 'Core business loop'}</h3>
              <p className="mt-1.5 text-sm leading-relaxed text-secondary">
                {isZh ? '评估 → 监测 → 优化 → 运营 → 协同,八个模块连成一条持续迭代的运营链路。' : 'Assess → monitor → optimize → operate → collaborate: eight modules form one continuous loop.'}
              </p>
              <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                {BUSINESS_LOOP.map((item, index) => (
                  <div key={item.title[locale]} className="group rounded-2xl border border-border bg-card p-5 shadow-sm transition-all duration-200 hover:-translate-y-0.5 hover:border-accent-primary/40">
                    <div className="mb-4 flex items-center justify-between gap-3">
                      <span className="text-xs font-semibold text-muted">{String(index + 1).padStart(2, '0')}</span>
                      <span className="h-px flex-1 bg-border" />
                    </div>
                    <h4 className="text-base font-bold text-primary">{item.title[locale]}</h4>
                    <p className="mt-2 text-sm leading-relaxed text-secondary">{item.desc[locale]}</p>
                  </div>
                ))}
              </div>
            </div>

            {/* Knowledge-enhanced data foundation */}
            <div className="mt-10 rounded-3xl border border-border bg-card p-5 shadow-sm sm:p-7">
              <div className="grid gap-6 lg:grid-cols-[0.85fr_1.15fr]">
                <div>
                  <h3 className="text-lg font-bold text-primary">{isZh ? '知识增强数据底座' : 'Knowledge-enhanced data foundations'}</h3>
                  <p className="mt-3 text-sm leading-relaxed text-secondary">
                    {isZh
                      ? '业务主库是事实源,缓存保障体验;图数据库与向量数据库负责知识图谱与 RAG,用来增强企业画像与内容生成,不替代业务主库。'
                      : 'The primary database owns facts and cache protects experience; graph and vector databases power the knowledge graph and RAG to enrich enterprise profiles and content generation, without replacing the primary database.'}
                  </p>
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  {DATA_FOUNDATION.map((item) => (
                    <div key={item.title[locale]} className="rounded-2xl border border-border bg-surface p-4">
                      <h4 className="text-sm font-bold text-primary">{item.title[locale]}</h4>
                      <p className="mt-2 text-xs leading-relaxed text-secondary">{item.desc[locale]}</p>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            {/* External connections */}
            <div className="mt-10">
              <h3 className="text-lg font-bold text-primary">{isZh ? 'AI 引擎' : 'External connections'}</h3>
              <div className="mt-4 grid gap-3 md:grid-cols-3">
                {EXTERNAL.map((item) => (
                  <div key={item.title[locale]} className="rounded-2xl border border-border bg-card p-5 shadow-sm">
                    <h4 className="text-base font-bold text-primary">{item.title[locale]}</h4>
                    <p className="mt-2 text-sm leading-relaxed text-secondary">{item.desc[locale]}</p>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>

        {/* ── CTA ──────────────────────────────────────── */}
        <section className="px-4 pb-20 pt-8">
          <div className="mx-auto w-full max-w-7xl">
            <div className="relative overflow-hidden rounded-3xl border border-accent-primary/20 bg-card p-8 shadow-sm sm:p-10 lg:p-12">
              <div className="grid items-center gap-8 lg:grid-cols-[1fr_auto]">
                <div>
                  <span className="mb-5 inline-flex h-12 w-12 items-center justify-center rounded-2xl bg-accent-primary/10">
                    <SolutionIcon name="architecture" className="h-6 w-6 text-accent-primary" />
                  </span>
                  <h2 className="text-2xl font-bold text-primary sm:text-3xl">{isZh ? '把 AI 可见性变成可运营资产' : 'Turn AI Visibility into an Operable Asset'}</h2>
                  <p className="mt-3 max-w-2xl text-sm leading-relaxed text-secondary sm:text-base">
                    {isZh
                      ? '从网站审计到内容生成、从真实采样到复测归因,我们可以按你的品牌场景拆解架构落地路径。'
                      : 'From website audit to content generation, from real sampling to retest attribution, we can map this architecture to your brand workflow.'}
                  </p>
                </div>
                <div className="flex flex-col gap-3 sm:flex-row lg:flex-col">
                  <button
                    onClick={() => openContact()}
                    className="btn-solid inline-flex h-11 items-center justify-center rounded-lg px-8 text-sm font-semibold transition-all duration-200"
                  >
                    {isZh ? '联系我们' : 'Contact us'}
                  </button>
                  <Link
                    to="/"
                    className="btn-secondary inline-flex h-11 items-center justify-center rounded-lg px-8 text-sm font-semibold transition-all duration-200"
                  >
                    {isZh ? '免费检测一次' : 'Run a free check'}
                  </Link>
                </div>
              </div>
            </div>
          </div>
        </section>
      </main>
    </div>
  );
}
