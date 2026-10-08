// Data source for the "Solutions" mega-menu and intro/promo pages.
//
// Structure modeled on Miaozhen's /branding-services three-column layout, with content adapted to Vigilath
// locked positioning: end-to-end AI search visibility solution (audit · review · optimization · monitoring), an independent third-party
// AI visibility audit platform, targeting domestic enterprises.
//
// One data set drives two places:
//   1. The full-width three-column mega-menu in Header.tsx (column titles + leaf items)
//   2. The intro pages in Solution.tsx (rendering SOLUTIONS[slug] by :slug)
//
// All copy is bilingual (zh / en); pages pick the language by i18n.language.

export type Locale = 'zh' | 'en';

/** A bilingual text snippet. */
export type LT = Record<Locale, string>;

/** A small "title + description" card on an intro page. */
export interface SolutionPoint {
  title: LT;
  desc: LT;
}

/** A section on an intro page (subheading + a set of cards). */
export interface SolutionSection {
  heading: LT;
  points: SolutionPoint[];
}

/** Full intro page content for a single solution / service item. */
export interface Solution {
  slug: string;
  /** The mega-menu column it belongs to. */
  column: 'audience' | 'scope' | 'solution';
  /** Intro page hero icon key (see ICONS in Solution.tsx). */
  icon: string;
  /** Short label in the mega-menu. */
  navLabel: LT;
  /** Intro page H1. */
  title: LT;
  /** Intro page subtitle (the line below the hero). */
  subtitle: LT;
  /** Hero body paragraph. */
  intro: LT;
  /** Body sections. */
  sections: SolutionSection[];
  /** Footer call to action. */
  cta: { title: LT; desc: LT };
}

/** One column of the mega-menu. */
export interface SolutionColumn {
  id: Solution['column'];
  title: LT;
  /** Slugs of this column's leaf items; order is display order. */
  slugs: string[];
}

export const SOLUTION_COLUMNS: SolutionColumn[] = [
  {
    id: 'audience',
    title: { zh: '服务对象', en: 'Who We Serve' },
    slugs: ['enterprise-cn', 'agencies', 'research'],
  },
  {
    id: 'scope',
    title: { zh: '服务范围', en: 'What We Do' },
    slugs: [
      'ai-visibility-check',
      'geo-audit',
      'content-optimization',
      'engine-monitoring',
      'sentiment-monitoring',
      'competitor-benchmark',
      'source-analysis',
    ],
  },
  {
    id: 'solution',
    title: { zh: '解决方案', en: 'Solutions' },
    slugs: ['architecture', 'full-funnel', 'closed-loop', 'brand-growth', 'sentiment', 'ad-compliance', 'data-compliance'],
  },
];

// ─────────────────────────────────────────────────────────────
// Content of each intro page
// ─────────────────────────────────────────────────────────────

const SOLUTION_LIST: Solution[] = [
  // ===== Audience =====
  {
    slug: 'enterprise-cn',
    column: 'audience',
    icon: 'building',
    navLabel: { zh: '国内企业', en: 'Domestic Enterprises' },
    title: { zh: '国内企业 AI 可见性', en: 'AI Visibility for Domestic Enterprises' },
    subtitle: {
      zh: '覆盖 DeepSeek、豆包、文心、通义、元宝等国产 AI 引擎,看清你在国内 AI 里的位置。',
      en: 'Cover DeepSeek, Doubao, Wenxin, Qwen, Yuanbao and more — know where you stand in domestic AI.',
    },
    intro: {
      zh: '国内用户的提问入口正在快速迁移到国产生成式引擎。这些引擎的信源与抓取逻辑各不相同,通用的 SEO 经验未必适用。威吉力深耕国产引擎,帮国内企业用国产引擎的口径量化与提升 AI 可见性。',
      en: 'Domestic users are migrating to homegrown generative engines, whose sources and crawling logic differ from one another — generic SEO playbooks do not simply carry over. Vigilath specializes in domestic engines to measure and lift AI visibility on their terms.',
    },
    sections: [
      {
        heading: { zh: '为什么需要专门做国产引擎', en: 'Why domestic engines need a dedicated approach' },
        points: [
          {
            title: { zh: '信源结构不同', en: 'Different source mix' },
            desc: { zh: '国产引擎更依赖国内内容生态与平台,信源权重逻辑自成一套。', en: 'Domestic engines lean on local content ecosystems with their own source-weighting logic.' },
          },
          {
            title: { zh: '抓取行为不同', en: 'Different crawling behavior' },
            desc: { zh: '动态加载、结构化数据的识别能力各引擎差异明显,需逐一适配。', en: 'Engines vary in how they handle dynamic loading and schema, requiring per-engine tuning.' },
          },
          {
            title: { zh: '答案波动大', en: 'Volatile answers' },
            desc: { zh: '同题不同引擎、不同时段答案差异大,单点测试不可靠。', en: 'Answers swing across engines and over time, so one-off tests are unreliable.' },
          },
        ],
      },
      {
        heading: { zh: '我们提供', en: 'What we provide' },
        points: [
          {
            title: { zh: '国产引擎全覆盖检测', en: 'Full domestic-engine coverage' },
            desc: { zh: '账号池 + 真实会话,稳定拿到各国产引擎的真实回答与引用面板。', en: 'An account pool with real sessions reliably captures genuine answers and citation panels.' },
          },
          {
            title: { zh: 'GEO/AEO 审计与优化', en: 'GEO/AEO audit and optimization' },
            desc: { zh: '针对国产引擎抓取偏好,出可执行的内容与结构化整改清单。', en: 'Actionable content and schema fixes tuned to domestic crawling preferences.' },
          },
          {
            title: { zh: '可见性趋势监控', en: 'Visibility trend monitoring' },
            desc: { zh: '长期跟踪各引擎口径,沉淀可对比的趋势数据而非一次性快照。', en: 'Track each engine over time to build comparable trends, not one-off snapshots.' },
          },
        ],
      },
    ],
    cta: {
      title: { zh: '测一测你在国产 AI 引擎里的可见性', en: 'Check your visibility in domestic AI engines' },
      desc: { zh: '一次覆盖主流国产引擎,给出可对比的可见性基线。', en: 'One run across major domestic engines for a comparable visibility baseline.' },
    },
  },
  {
    slug: 'agencies',
    column: 'audience',
    icon: 'handshake',
    navLabel: { zh: '渠道 / 代理商', en: 'Channels & Agencies' },
    title: { zh: '渠道 / 代理商合作', en: 'For Channels & Agencies' },
    subtitle: {
      zh: '把 AI 可见性检测与 GEO 优化能力,白标接入你的服务体系。',
      en: 'Bring AI-visibility checks and GEO optimization into your offering, white-labeled.',
    },
    intro: {
      zh: '营销、公关、SEO 与咨询机构正在把 GEO(生成式引擎优化)加入服务清单。威吉力提供独立第三方的检测能力与可批量交付的报告,让你不必自建团队就能为客户做 AI 可见性服务。',
      en: 'Marketing, PR, SEO and consulting firms are adding GEO to their menu. Vigilath provides independent third-party measurement and batch-ready reports, so you can offer AI-visibility services without building a team from scratch.',
    },
    sections: [
      {
        heading: { zh: '合作方式', en: 'Ways to partner' },
        points: [
          {
            title: { zh: '白标报告', en: 'White-label reports' },
            desc: { zh: '以你的品牌交付检测与审计报告,数据由独立第三方背书。', en: 'Deliver audits under your brand, backed by independent third-party data.' },
          },
          {
            title: { zh: '批量检测', en: 'Batch checks' },
            desc: { zh: '为多个客户 / 站点批量跑检测,统一管理与对比。', en: 'Run checks across many clients or sites with unified management and comparison.' },
          },
          {
            title: { zh: 'API 接入', en: 'API access' },
            desc: { zh: '把可见性检测能力接进你自己的工作流与看板。', en: 'Wire visibility checks into your own workflows and dashboards.' },
          },
        ],
      },
      {
        heading: { zh: '你能拿到什么', en: 'What you get' },
        points: [
          {
            title: { zh: '新增收入线', en: 'A new revenue line' },
            desc: { zh: 'GEO 是高需求的新增量服务,易于向存量客户加售。', en: 'GEO is in-demand and easy to upsell to your existing clients.' },
          },
          {
            title: { zh: '客观说服力', en: 'Objective credibility' },
            desc: { zh: '第三方分数与证据,比主观建议更容易让客户买单。', en: 'Third-party scores and evidence sell better than subjective advice.' },
          },
        ],
      },
    ],
    cta: {
      title: { zh: '聊聊渠道合作', en: 'Talk about partnership' },
      desc: { zh: '告诉我们你的客户规模,我们给出合适的合作与白标方案。', en: 'Tell us your client volume and we will propose a fitting partnership and white-label plan.' },
    },
  },
  {
    slug: 'research',
    column: 'audience',
    icon: 'academic',
    navLabel: { zh: '研究机构与高校', en: 'Research & Academia' },
    title: { zh: '研究机构与高校', en: 'Research Institutions & Universities' },
    subtitle: {
      zh: '为生成式引擎、信息检索与传播研究提供可复现的 AI 可见性数据。',
      en: 'Reproducible AI-visibility data for research in generative engines, IR and communication.',
    },
    intro: {
      zh: '生成式引擎如何选择信源、如何形成对实体的判断,是一个全新的研究领域。威吉力的多引擎检测能力可作为研究基础设施,提供结构化、可复现的实测数据。',
      en: 'How generative engines pick sources and form judgments about entities is a brand-new field of study. Vigilath\'s multi-engine measurement can serve as research infrastructure, offering structured, reproducible empirical data.',
    },
    sections: [
      {
        heading: { zh: '研究支持', en: 'Research support' },
        points: [
          {
            title: { zh: '多引擎实测数据', en: 'Multi-engine empirical data' },
            desc: { zh: '同题跨引擎、跨时间的回答与信源数据,适合横纵向对比。', en: 'Cross-engine, cross-time answers and source data, suited to comparative study.' },
          },
          {
            title: { zh: '信源与引用分析', en: 'Source & citation analysis' },
            desc: { zh: '拆解 AI 回答背后的引用来源,量化信源结构与偏好。', en: 'Decompose the citations behind AI answers to quantify source structure and bias.' },
          },
          {
            title: { zh: '可复现方法', en: 'Reproducible method' },
            desc: { zh: '标准化的检测流程,便于在论文中描述与复现。', en: 'A standardized measurement pipeline that is easy to describe and reproduce in papers.' },
          },
        ],
      },
    ],
    cta: {
      title: { zh: '探讨学术合作', en: 'Explore academic collaboration' },
      desc: { zh: '介绍你的研究方向,我们看看能提供哪些数据与接口支持。', en: 'Describe your research and we will see what data and API support we can offer.' },
    },
  },

  // ===== Service scope =====
  {
    slug: 'ai-visibility-check',
    column: 'scope',
    icon: 'search',
    navLabel: { zh: 'AI 可见性检测', en: 'AI Visibility Check' },
    title: { zh: 'AI 可见性检测', en: 'AI Visibility Check' },
    subtitle: {
      zh: '一个问题问遍主流 AI 引擎,看清谁被提到、谁被引用、口径如何。',
      en: 'Ask one question across every major engine — see who gets named, cited and how.',
    },
    intro: {
      zh: '可见性检测是一切优化的起点。威吉力用真实会话向多个 AI 引擎提问,捕捉它们对你品牌 / 产品的真实表述与引用证据,给出 0–100 的 AI 可见性分,而不是凭感觉打分。',
      en: 'Measurement is where everything starts. Vigilath queries multiple AI engines with real sessions, captures how they actually describe your brand or product along with citation evidence, and produces a 0–100 AI visibility score — not a gut feel.',
    },
    sections: [
      {
        heading: { zh: '检测维度', en: 'What we measure' },
        points: [
          {
            title: { zh: '是否被提及', en: 'Mention' },
            desc: { zh: 'AI 回答里是否主动提到你,提及的语境是正面还是负面。', en: 'Whether AI names you, and whether the framing is positive or negative.' },
          },
          {
            title: { zh: '是否被引用', en: 'Citation' },
            desc: { zh: '你的官网或内容是否作为信源出现在引用面板里。', en: 'Whether your site or content appears as a source in the citation panel.' },
          },
          {
            title: { zh: '排序与份额', en: 'Rank & share' },
            desc: { zh: '在同类回答里你与竞品的相对位置和提及份额。', en: 'Your position and share of mention versus competitors in the same answers.' },
          },
        ],
      },
      {
        heading: { zh: '覆盖引擎', en: 'Engines covered' },
        points: [
          {
            title: { zh: '国产引擎', en: 'Domestic engines' },
            desc: { zh: 'DeepSeek、通义千问、豆包、文心一言、元宝、Kimi 等。', en: 'DeepSeek, Qwen, Doubao, Wenxin, Yuanbao, Kimi and more.' },
          },
        ],
      },
    ],
    cta: {
      title: { zh: '现在就测一次', en: 'Run a check now' },
      desc: { zh: '输入域名或品牌词,拿到多引擎可见性基线。', en: 'Enter a domain or brand term for a multi-engine visibility baseline.' },
    },
  },
  {
    slug: 'geo-audit',
    column: 'scope',
    icon: 'clipboard',
    navLabel: { zh: 'GEO / AEO 审计', en: 'GEO / AEO Audit' },
    title: { zh: 'GEO / AEO 审计', en: 'GEO / AEO Audit' },
    subtitle: {
      zh: '25 大类深度体检,定位拖累 AI 可见性的每一个具体环节。',
      en: 'A 25-category deep audit that pinpoints every factor dragging down AI visibility.',
    },
    intro: {
      zh: '知道分数低还不够,要知道为什么低。威吉力对站点做 25 大类的 GEO / AEO 审计,从内容质量、结构化数据、可抓取性到信源权威,逐项给出诊断与可执行的整改建议。',
      en: 'A low score is not enough — you need to know why. Vigilath audits a site across 25 categories, from content quality and schema to crawlability and source authority, with a diagnosis and actionable fixes for each.',
    },
    sections: [
      {
        heading: { zh: '审计范围', en: 'Audit scope' },
        points: [
          {
            title: { zh: '内容与语义', en: 'Content & semantics' },
            desc: { zh: '内容是否清晰、结构化、便于 AI 抽取关键事实。', en: 'Whether content is clear, structured and easy for AI to extract facts from.' },
          },
          {
            title: { zh: '结构化数据', en: 'Structured data' },
            desc: { zh: 'Organization / Product / FAQ / Article 等 schema 是否到位。', en: 'Whether Organization / Product / FAQ / Article schema is in place.' },
          },
          {
            title: { zh: '可抓取性', en: 'Crawlability' },
            desc: { zh: 'AI 爬虫能否完整读取,动态加载是否造成盲区。', en: 'Whether AI crawlers can read fully, and whether dynamic loading creates blind spots.' },
          },
          {
            title: { zh: '站外权威', en: 'Off-page authority' },
            desc: { zh: 'GitHub、npm、PyPI、维基百科等外部信源信号。', en: 'External signals from GitHub, npm, PyPI, Wikipedia and more.' },
          },
        ],
      },
      {
        heading: { zh: '交付物', en: 'Deliverables' },
        points: [
          {
            title: { zh: '诊断报告', en: 'Diagnostic report' },
            desc: { zh: '逐项评分 + 证据,清楚指出问题所在。', en: 'Per-item scores with evidence that clearly locates the problems.' },
          },
          {
            title: { zh: '整改清单', en: 'Fix list' },
            desc: { zh: '按优先级排序的可执行修复项,落地有抓手。', en: 'A prioritized, actionable fix list you can execute.' },
          },
        ],
      },
    ],
    cta: {
      title: { zh: '申请一次完整审计', en: 'Request a full audit' },
      desc: { zh: '告诉我们站点,我们出一份 25 大类的 GEO/AEO 体检报告。', en: 'Share your site and we will produce a 25-category GEO/AEO report.' },
    },
  },
  {
    slug: 'content-optimization',
    column: 'scope',
    icon: 'edit',
    navLabel: { zh: '内容优化与投放', en: 'Content Optimization & Placement' },
    title: { zh: '内容优化与投放', en: 'Content Optimization & Placement' },
    subtitle: {
      zh: '按 AI 偏好生产与投放内容,把「应该被引用」变成「真的被引用」。',
      en: 'Produce and place content the way AI prefers — turn "should be cited" into "is cited".',
    },
    intro: {
      zh: '审计指出问题之后,需要有人把内容真正改对、铺对。威吉力按 AI 抓取与引用偏好生成内容,学习你的自有稿文风,投放到合适的信源平台,并回填命中数据,验证是否真的被 AI 引用。',
      en: 'After an audit names the problems, someone has to actually fix and place the content. Vigilath generates content tuned to AI crawling and citation preferences, learns the voice of your own articles, places it on the right source platforms, and backfills hit data to verify it really gets cited.',
    },
    sections: [
      {
        heading: { zh: '我们怎么做', en: 'How it works' },
        points: [
          {
            title: { zh: '文风对齐', en: 'Voice alignment' },
            desc: { zh: '以你的自有文章为范例,生成内容只学文风不照抄。', en: 'Using your own articles as references, generated content learns the voice without copying.' },
          },
          {
            title: { zh: '结构化生产', en: 'Structured production' },
            desc: { zh: '面向 AI 抽取优化标题、要点、FAQ 与结构化数据。', en: 'Optimize titles, key points, FAQ and schema for AI extraction.' },
          },
          {
            title: { zh: '信源投放', en: 'Source placement' },
            desc: { zh: '投放到 AI 真正会引用的平台,而不是自说自话。', en: 'Place on platforms AI actually cites, not just your own channels.' },
          },
          {
            title: { zh: '命中回填', en: 'Hit backfill' },
            desc: { zh: '回填被引用记录,用数据证明内容有没有奏效。', en: 'Backfill citation records to prove with data whether the content worked.' },
          },
        ],
      },
    ],
    cta: {
      title: { zh: '让内容真正被 AI 引用', en: 'Make content AI actually cites' },
      desc: { zh: '从一篇试点稿开始,看它能不能进 AI 的引用面板。', en: 'Start with one pilot piece and see if it lands in the citation panel.' },
    },
  },
  {
    slug: 'engine-monitoring',
    column: 'scope',
    icon: 'radar',
    navLabel: { zh: 'AI 引擎监控', en: 'AI Engine Monitoring' },
    title: { zh: 'AI 引擎监控', en: 'AI Engine Monitoring' },
    subtitle: {
      zh: '持续跟踪各 AI 引擎对你的口径,掉档即预警。',
      en: 'Continuously track how each AI engine talks about you, and alert on any slip.',
    },
    intro: {
      zh: 'AI 的答案会随模型更新、信源变化而漂移。一次测得好不代表一直好。威吉力按周期复测,把可见性变成可对比的趋势曲线,在排名下滑或竞品反超时第一时间通知你。',
      en: 'AI answers drift as models update and sources change — good once is not good forever. Vigilath re-tests on a cadence, turns visibility into a comparable trend line, and alerts you the moment you slip or a competitor overtakes you.',
    },
    sections: [
      {
        heading: { zh: '监控能力', en: 'Monitoring capabilities' },
        points: [
          {
            title: { zh: '趋势跟踪', en: 'Trend tracking' },
            desc: { zh: '可见性分、提及率、引用数随时间的变化曲线。', en: 'Visibility score, mention rate and citation count over time.' },
          },
          {
            title: { zh: '竞品对比', en: 'Competitor comparison' },
            desc: { zh: '与竞品并排监控,看清此消彼长。', en: 'Monitor side by side with competitors to see the give and take.' },
          },
          {
            title: { zh: '异常告警', en: 'Anomaly alerts' },
            desc: { zh: '掉档、消失、负面表述出现时主动通知。', en: 'Get notified when you drop, disappear or pick up negative framing.' },
          },
        ],
      },
    ],
    cta: {
      title: { zh: '把可见性管起来', en: 'Put visibility under watch' },
      desc: { zh: '设定监控周期与竞品,持续守住你的回答位。', en: 'Set a cadence and competitors to keep defending your answer slots.' },
    },
  },
  {
    slug: 'sentiment-monitoring',
    column: 'scope',
    icon: 'pulse',
    navLabel: { zh: '舆情监控', en: 'Sentiment Monitoring' },
    title: { zh: '舆情监控', en: 'Sentiment Monitoring' },
    subtitle: {
      zh: '跨平台抓取与分析品牌声量,看清立场、意图与情感。',
      en: 'Capture and analyze brand voice across platforms — stance, intent and sentiment.',
    },
    intro: {
      zh: 'AI 对你的判断,往往来自全网对你的讨论。威吉力跨平台抓取相关内容并做结构化情感分析,按新鲜度展示最新声量,帮你及时发现风险与机会。',
      en: 'What AI thinks of you often comes from what the web says about you. Vigilath captures relevant content across platforms, runs structured sentiment analysis, and surfaces the freshest voice so you spot risks and opportunities early.',
    },
    sections: [
      {
        heading: { zh: '分析维度', en: 'Analysis dimensions' },
        points: [
          {
            title: { zh: '情感倾向', en: 'Sentiment' },
            desc: { zh: '量化每条内容的看多 / 看空倾向,聚合成声量指标。', en: 'Quantify the bullish/bearish lean of each item, aggregated into a voice metric.' },
          },
          {
            title: { zh: '立场与意图', en: 'Stance & intent' },
            desc: { zh: '识别内容立场与发布意图,区分噪声与真实信号。', en: 'Identify stance and intent to separate noise from real signal.' },
          },
          {
            title: { zh: '新鲜度展示', en: 'Recency view' },
            desc: { zh: '全量入库、按新鲜度窗口只露最新,避免旧贴干扰。', en: 'Capture everything, but show only the freshest within a recency window.' },
          },
        ],
      },
    ],
    cta: {
      title: { zh: '盯住你的品牌声量', en: 'Keep an eye on your brand voice' },
      desc: { zh: '配置关注主题,持续获得结构化舆情分析。', en: 'Configure topics to follow and get continuous structured sentiment analysis.' },
    },
  },
  {
    slug: 'competitor-benchmark',
    column: 'scope',
    icon: 'scale',
    navLabel: { zh: '竞品对标', en: 'Competitor Benchmark' },
    title: { zh: '竞品对标', en: 'Competitor Benchmark' },
    subtitle: {
      zh: '在同一组问题下,把你和竞品在 AI 里的表现摆在一起看。',
      en: 'Put you and your competitors side by side under the same questions inside AI.',
    },
    intro: {
      zh: '单看自己的分数很难判断好坏,有了参照系才知道差距。威吉力用同一组问题同时检测你与竞品,量化提及份额、引用份额与排序,告诉你该往哪里追。',
      en: 'A score in isolation is hard to judge — you need a reference. Vigilath checks you and your competitors with the same question set, quantifying share of mention, share of citation and rank, so you know where to catch up.',
    },
    sections: [
      {
        heading: { zh: '对标内容', en: 'What we benchmark' },
        points: [
          {
            title: { zh: '提及份额', en: 'Share of mention' },
            desc: { zh: '同类回答里你与竞品被提到的相对频率。', en: 'How often you are named versus competitors in the same answers.' },
          },
          {
            title: { zh: '引用份额', en: 'Share of citation' },
            desc: { zh: '谁的内容更常被 AI 当作信源引用。', en: 'Whose content AI cites as a source more often.' },
          },
          {
            title: { zh: '差距归因', en: 'Gap attribution' },
            desc: { zh: '把差距拆到内容、结构、信源等具体环节。', en: 'Break the gap down to content, structure and source factors.' },
          },
        ],
      },
    ],
    cta: {
      title: { zh: '看看你和对手差多少', en: 'See the gap to your rivals' },
      desc: { zh: '给我们一组竞品,我们出一份并排对标报告。', en: 'Give us a set of competitors and we will deliver a side-by-side benchmark.' },
    },
  },
  {
    slug: 'source-analysis',
    column: 'scope',
    icon: 'link',
    navLabel: { zh: '信源分析', en: 'Source Analysis' },
    title: { zh: '信源分析', en: 'Source Analysis' },
    subtitle: {
      zh: '拆开 AI 回答背后的引用来源,看清它到底信谁。',
      en: 'Open up the citations behind an AI answer to see who it actually trusts.',
    },
    intro: {
      zh: '想被 AI 引用,先得知道 AI 现在引用谁。威吉力深度解析 AI 回答的引用面板与参考来源,识别高权重信源,告诉你应该去哪些平台铺内容、争取被收录。',
      en: 'To get cited, first learn who AI cites today. Vigilath deeply parses the citation panels and reference sources behind AI answers, identifies high-weight sources, and tells you where to place content to win inclusion.',
    },
    sections: [
      {
        heading: { zh: '分析内容', en: 'What we analyze' },
        points: [
          {
            title: { zh: '信源构成', en: 'Source composition' },
            desc: { zh: '一类问题的回答主要引自哪些平台与站点。', en: 'Which platforms and sites a class of answers mainly draws from.' },
          },
          {
            title: { zh: '权重识别', en: 'Weight detection' },
            desc: { zh: '识别被反复引用的高权重信源,定位优先目标。', en: 'Spot high-weight sources cited repeatedly to set priority targets.' },
          },
          {
            title: { zh: '深度抓取', en: 'Deep capture' },
            desc: { zh: '不止内联引用,读参考面板拿到更全的来源清单。', en: 'Beyond inline citations, read reference panels for a fuller source list.' },
          },
        ],
      },
    ],
    cta: {
      title: { zh: '弄清 AI 到底信谁', en: 'Learn who AI trusts' },
      desc: { zh: '选定问题集,我们给出信源构成与投放建议。', en: 'Pick a question set and we will map sources and recommend placements.' },
    },
  },

  // ===== Solutions =====
  {
    slug: 'architecture',
    column: 'solution',
    icon: 'architecture',
    navLabel: { zh: '系统架构', en: 'System Architecture' },
    title: { zh: '系统架构', en: 'System Architecture' },
    subtitle: {
      zh: '三个独立伸缩单元共享一库,支撑检测·审计·优化·监控全链路。',
      en: 'Three independently scalable units on one shared database, powering the full detect · audit · optimize · monitor loop.',
    },
    intro: {
      zh: '威吉力的整体技术架构:核心引擎、智能优化 Agent、监测执行服务层三个互相独立的伸缩单元,共享同一个业务主库,认证走无状态 JWT,报表走缓存降级。',
      en: 'Vigilath\'s overall technical architecture: the Core Engine, the Optimization Agent and the Monitoring & Execution layer as three independently scalable units, sharing one primary database, with stateless JWT auth and a cached, degradation-safe reporting layer.',
    },
    // System architecture uses the standalone Architecture.tsx page (intercepted by the /solutions/architecture static route);
    // sections / cta here only satisfy the Solution interface and are normally never rendered.
    sections: [],
    cta: {
      title: { zh: '了解威吉力系统架构', en: 'Explore the Vigilath architecture' },
      desc: { zh: '看清检测·审计·优化·监控全链路背后的服务与数据流。', en: 'See the services and data flow behind the full loop.' },
    },
  },
  {
    slug: 'full-funnel',
    column: 'solution',
    icon: 'funnel',
    navLabel: { zh: '全链路 AI 可见性评估', en: 'Full-funnel AI Visibility' },
    title: { zh: '全链路 AI 可见性评估', en: 'Full-funnel AI Visibility Assessment' },
    subtitle: {
      zh: '从被检索、被提及到被引用,一次评估全链路每一环。',
      en: 'From retrieval to mention to citation — assess every step of the funnel in one pass.',
    },
    intro: {
      zh: 'AI 可见性不是一个孤立指标,而是一条漏斗:能不能被抓取 → 会不会被提及 → 是否被当作信源引用。威吉力把这条链路拆开逐环评估,告诉你漏在哪一层,该先补哪一环。',
      en: 'AI visibility is not a single metric but a funnel: can you be crawled → are you mentioned → are you cited as a source. Vigilath assesses each step, showing where you leak and which layer to fix first.',
    },
    sections: [
      {
        heading: { zh: '评估链路', en: 'The funnel' },
        points: [
          {
            title: { zh: '可被抓取', en: 'Crawlable' },
            desc: { zh: 'AI 爬虫能否完整读到你的内容。', en: 'Whether AI crawlers can read your content in full.' },
          },
          {
            title: { zh: '可被提及', en: 'Mentionable' },
            desc: { zh: '内容是否足以让 AI 在回答里主动提到你。', en: 'Whether your content is strong enough for AI to name you.' },
          },
          {
            title: { zh: '可被引用', en: 'Citable' },
            desc: { zh: '你是否被当作可信信源进入引用面板。', en: 'Whether you enter the citation panel as a trusted source.' },
          },
        ],
      },
      {
        heading: { zh: '为什么是全链路', en: 'Why full-funnel' },
        points: [
          {
            title: { zh: '定位真瓶颈', en: 'Find the real bottleneck' },
            desc: { zh: '避免在不是瓶颈的环节上空耗预算。', en: 'Stop spending budget on steps that are not the bottleneck.' },
          },
          {
            title: { zh: '统一口径', en: 'One yardstick' },
            desc: { zh: '一套指标贯穿检测、审计、优化、监控。', en: 'One metric set runs through check, audit, optimize and monitor.' },
          },
        ],
      },
    ],
    cta: {
      title: { zh: '做一次全链路评估', en: 'Run a full-funnel assessment' },
      desc: { zh: '一份报告看清你在每一层的漏点。', en: 'One report shows where you leak at every layer.' },
    },
  },
  {
    slug: 'closed-loop',
    column: 'solution',
    icon: 'loop',
    navLabel: { zh: '检测·审计·优化·监控闭环', en: 'Detect · Audit · Optimize · Monitor' },
    title: { zh: '检测·审计·优化·监控闭环', en: 'The Detect · Audit · Optimize · Monitor Loop' },
    subtitle: {
      zh: '威吉力的核心方法论:把 AI 可见性当作一个持续运营的闭环。',
      en: 'Vigilath\'s core method: run AI visibility as a continuous operating loop.',
    },
    intro: {
      zh: 'AI 可见性不是一锤子买卖,而是「检测 → 审计 → 优化 → 监控 → 再检测」的循环。威吉力把这四步打通成一个闭环,每一轮都用数据验证上一轮的优化是否奏效,持续迭代。',
      en: 'AI visibility is not one-and-done — it is a loop of detect → audit → optimize → monitor → detect again. Vigilath connects these four steps into one closed loop, where each round verifies the last with data and keeps iterating.',
    },
    sections: [
      {
        heading: { zh: '四步闭环', en: 'The four-step loop' },
        points: [
          {
            title: { zh: '① 检测', en: '① Detect' },
            desc: { zh: '多引擎测出当前可见性基线与证据。', en: 'Measure the current visibility baseline and evidence across engines.' },
          },
          {
            title: { zh: '② 审计', en: '② Audit' },
            desc: { zh: '25 大类诊断,定位拖后腿的具体环节。', en: 'A 25-category diagnosis to pinpoint the drag factors.' },
          },
          {
            title: { zh: '③ 优化', en: '③ Optimize' },
            desc: { zh: '按 AI 偏好生产与投放内容,落地整改。', en: 'Produce and place content to AI preferences and execute fixes.' },
          },
          {
            title: { zh: '④ 监控', en: '④ Monitor' },
            desc: { zh: '持续复测,验证效果并触发下一轮。', en: 'Re-test continuously to verify impact and trigger the next round.' },
          },
        ],
      },
    ],
    cta: {
      title: { zh: '把可见性运营起来', en: 'Operate visibility as a loop' },
      desc: { zh: '从第一轮检测开始,进入持续迭代的闭环。', en: 'Start with the first detection and enter the iterating loop.' },
    },
  },
  {
    slug: 'brand-growth',
    column: 'solution',
    icon: 'trending',
    navLabel: { zh: '品牌增长方案', en: 'Brand Growth' },
    title: { zh: '品牌增长方案', en: 'Brand Growth Solution' },
    subtitle: {
      zh: '把 AI 可见性沉淀成可持续的品牌增长资产。',
      en: 'Turn AI visibility into a durable brand-growth asset.',
    },
    intro: {
      zh: '面向需要长期经营 AI 渠道的品牌,威吉力提供从策略、内容到监控的一体化增长方案:不仅做单点优化,更帮你把被引用、被推荐沉淀为可复利的品牌资产。',
      en: 'For brands that need to operate the AI channel long-term, Vigilath offers an integrated growth solution from strategy to content to monitoring — not just point fixes, but compounding brand equity from being cited and recommended.',
    },
    sections: [
      {
        heading: { zh: '方案构成', en: 'What is included' },
        points: [
          {
            title: { zh: '可见性策略', en: 'Visibility strategy' },
            desc: { zh: '基于检测与对标,制定优先级与目标引擎清单。', en: 'Set priorities and a target-engine list from checks and benchmarks.' },
          },
          {
            title: { zh: '内容运营', en: 'Content operations' },
            desc: { zh: '持续生产与投放面向 AI 的内容,并回填命中。', en: 'Continuously produce and place AI-oriented content with hit backfill.' },
          },
          {
            title: { zh: '增长看板', en: 'Growth dashboard' },
            desc: { zh: '把可见性、引用、声量沉淀为可追踪的增长看板。', en: 'Consolidate visibility, citations and voice into a trackable dashboard.' },
          },
        ],
      },
    ],
    cta: {
      title: { zh: '规划你的 AI 增长', en: 'Plan your AI growth' },
      desc: { zh: '聊聊目标,我们出一份可落地的增长方案。', en: 'Tell us your goals and we will draft an actionable growth plan.' },
    },
  },
  {
    slug: 'sentiment',
    column: 'solution',
    icon: 'shield',
    navLabel: { zh: '舆情监控方案', en: 'Sentiment Monitoring' },
    title: { zh: '舆情监控方案', en: 'Sentiment Monitoring Solution' },
    subtitle: {
      zh: '跨平台、结构化、按新鲜度展示的品牌舆情一体化方案。',
      en: 'An integrated brand-sentiment solution: cross-platform, structured, recency-first.',
    },
    intro: {
      zh: '面向需要长期守护品牌声誉的团队,威吉力提供从抓取、分析到展示的一体化舆情方案。全量入库保证不漏,结构化分析量化立场与情感,按新鲜度展示只露最新,让团队把注意力放在真正要紧的信号上。',
      en: 'For teams safeguarding brand reputation, Vigilath offers an end-to-end sentiment solution from capture to analysis to display. Full capture means nothing is missed, structured analysis quantifies stance and sentiment, and a recency-first view surfaces only what is fresh — so teams focus on signals that matter.',
    },
    sections: [
      {
        heading: { zh: '方案能力', en: 'Capabilities' },
        points: [
          {
            title: { zh: '跨平台抓取', en: 'Cross-platform capture' },
            desc: { zh: '覆盖主流内容与社媒平台,全量入库不漏关键声量。', en: 'Cover major content and social platforms, capturing everything that matters.' },
          },
          {
            title: { zh: '结构化分析', en: 'Structured analysis' },
            desc: { zh: '量化情感、立场、意图,聚合成可读的声量指标。', en: 'Quantify sentiment, stance and intent into readable voice metrics.' },
          },
          {
            title: { zh: '新鲜度展示', en: 'Recency-first display' },
            desc: { zh: '按新鲜度窗口只露最新内容,屏蔽过期噪声。', en: 'Show only the freshest within a recency window, muting stale noise.' },
          },
        ],
      },
    ],
    cta: {
      title: { zh: '守护你的品牌声誉', en: 'Protect your reputation' },
      desc: { zh: '配置关注主题与平台,获得持续的舆情守护。', en: 'Configure topics and platforms for continuous sentiment protection.' },
    },
  },
  {
    slug: 'ad-compliance',
    column: 'solution',
    icon: 'alert',
    navLabel: { zh: '广告内容风控方案', en: 'Ad Content Risk Control' },
    title: { zh: '广告内容风控方案', en: 'Ad Content Risk Control Solution' },
    subtitle: {
      zh: '面向金融、医美等强监管行业,给 AI 问答与地域投放里的广告内容装上事前拦截、实时监测、事后审计的合规闸门。',
      en: 'For finance, medical aesthetics and other tightly regulated industries: pre-launch gating, real-time monitoring and after-the-fact audit for ad content in AI answers and geo-targeted placements.',
    },
    intro: {
      zh: '生成式引擎正在替品牌"说话",但 AI 说的话从不经过你的审核流程:编造收益、夸大疗效、漏掉风险提示,任何一句都可能踩中监管红线。威吉力把广告内容风控延伸到 AI 渠道,覆盖 AI 问答、搜索摘要、本地门店介绍与地域定向投放,让每一句对外话术都留在合规边界之内。',
      en: 'Generative engines now speak on your brand\'s behalf — yet their words never pass through your review process. A fabricated return figure, an exaggerated treatment claim or a missing risk disclosure can each cross a regulatory red line. Vigilath extends ad-content risk control to the AI channel, covering AI answers, search summaries, local store introductions and geo-targeted placements, so every public claim stays inside the compliance boundary.',
    },
    sections: [
      {
        heading: { zh: '强监管行业的内容风险', en: 'Content risks in regulated industries' },
        points: [
          {
            title: { zh: '违禁承诺踩红线', en: 'Red-line promises' },
            desc: {
              zh: '金融的「保本」「稳赚」「零风险」,医美的「根治」「永久」「百分之百」,一词出现在回答里即构成违规。',
              en: 'Finance terms like "guaranteed principal" or "zero risk" and aesthetics claims like "permanent cure" are violations the moment they surface in an answer.',
            },
          },
          {
            title: { zh: 'AI 幻觉替你背锅', en: 'AI hallucinations on your tab' },
            desc: {
              zh: 'AI 会编造收益数字、疗效案例甚至门店资质,品牌毫不知情,宣传责任却要自己承担。',
              en: 'AI may invent return figures, treatment cases or even store credentials — the brand never knows, yet bears the advertising liability.',
            },
          },
          {
            title: { zh: '地域口径套利', en: 'Cross-region claim arbitrage' },
            desc: {
              zh: '同一物料在不同城市适用不同监管细则,在宽松地区夸大宣传的套利行为最难被人工发现。',
              en: 'The same creative faces different local rules city by city; exaggerating in laxer regions is the arbitrage human review misses most.',
            },
          },
        ],
      },
      {
        heading: { zh: '事前:源头拦截', en: 'Before launch: block at the source' },
        points: [
          {
            title: { zh: '行业违禁词库', en: 'Industry banned-word library' },
            desc: {
              zh: '通用广告红线之上,叠加金融、医美专属增量词库与风险语义库,随监管口径动态更新。',
              en: 'On top of general advertising red lines, finance- and aesthetics-specific banned words and risk semantics, updated as regulators move.',
            },
          },
          {
            title: { zh: '地域合规规则地图', en: 'Regional compliance rule map' },
            desc: {
              zh: '梳理各省市的宣传监管差异,输出分地域的禁用清单与投放准入门槛。',
              en: 'Map advertising rules across provinces into per-region banned lists and placement entry bars.',
            },
          },
          {
            title: { zh: '资质准入校验', en: 'Credential gating' },
            desc: {
              zh: '投放与门店信息上线前核验属地牌照、执业许可与广告审查文号,缺一即拦截发布。',
              en: 'Verify local licenses, practice permits and ad-review filing numbers before anything goes live; block publication if any is missing.',
            },
          },
        ],
      },
      {
        heading: { zh: '事中:全天候实时监测', en: 'In flight: round-the-clock monitoring' },
        points: [
          {
            title: { zh: '全渠道内容监测', en: 'All-channel content watch' },
            desc: {
              zh: '覆盖 AI 问答、搜索摘要、地图门店介绍与短视频文案,命中风险语义即时告警。',
              en: 'Watch AI answers, search summaries, map store introductions and short-video copy, alerting instantly on risky semantics.',
            },
          },
          {
            title: { zh: '幻觉识别与取证', en: 'Hallucination detection with evidence' },
            desc: {
              zh: '识别编造收益、虚构疗效与伪造资质,自动留存回答截图与原文作为取证材料。',
              en: 'Detect fabricated returns, invented outcomes and forged credentials, auto-archiving answer screenshots and raw text as evidence.',
            },
          },
          {
            title: { zh: '标识与提示核查', en: 'Disclosure checks' },
            desc: {
              zh: '核查风险提示、广告标识与批准文号是否完整展示,并逐城比对定向投放口径是否一致。',
              en: 'Verify risk warnings, ad labels and approval numbers are fully shown, and compare geo-targeted claims city by city for consistency.',
            },
          },
        ],
      },
      {
        heading: { zh: '事后:审计与闭环处置', en: 'After the fact: audit and closed loop' },
        points: [
          {
            title: { zh: '全链路日志留存', en: 'End-to-end audit trail' },
            desc: {
              zh: '内容生成、审核、告警与处置记录全程留痕,满足监管调阅与取证的留存要求。',
              en: 'Keep generation, review, alert and handling records end to end, meeting regulator retrieval and evidence-retention requirements.',
            },
          },
          {
            title: { zh: '风险分级处置', en: 'Tiered response' },
            desc: {
              zh: '一般风险限时整改,中风险自动下架并暂停该地域投放,高风险冻结账号并启动合规调查。',
              en: 'Minor issues get a fix-by deadline; medium risks are auto-delisted with regional placement paused; high risks freeze accounts and trigger investigation.',
            },
          },
          {
            title: { zh: '定期自查复盘', en: 'Scheduled self-inspection' },
            desc: {
              zh: '日扫描、周抽查、月度专项到季度跨区域审计,监管政策更新同步回灌规则库。',
              en: 'Daily scans, weekly spot checks, monthly specials and quarterly cross-region audits, feeding regulatory updates back into the rule library.',
            },
          },
        ],
      },
    ],
    cta: {
      title: { zh: '给 AI 渠道加一道合规闸门', en: 'Put a compliance gate on your AI channel' },
      desc: { zh: '聊聊你的行业与投放地域,我们出一份可落地的广告内容风控方案。', en: 'Tell us your industry and target regions, and we will draft an actionable ad-content risk-control plan.' },
    },
  },
  {
    slug: 'data-compliance',
    column: 'solution',
    icon: 'lock',
    navLabel: { zh: '地理数据合规方案', en: 'Geo-Data Compliance' },
    title: { zh: '地理数据合规方案', en: 'Geo-Data Compliance Solution' },
    subtitle: {
      zh: '覆盖采集、传输、存储、共享全生命周期的地理数据合规监控,守住《个保法》《数据安全法》与属地监管底线。',
      en: 'Full-lifecycle compliance monitoring for geographic data — collection, transfer, storage and sharing — meeting PIPL, the Data Security Law and local-jurisdiction rules.',
    },
    intro: {
      zh: '地理位置属于敏感个人信息:门店到访轨迹、用户定位、地域画像一旦采集越界、存储错位或违规出境,面对的不是整改而是处罚。对金融、医美等同时握有资产与生物数据的行业,风险还会层层叠加。威吉力搭建地理数据围栏,把采集、传输、存储、共享四个环节全部纳入实时监控。',
      en: 'Location is sensitive personal information: store-visit trails, user positioning and regional profiles that are over-collected, stored in the wrong place or moved across borders invite penalties, not just rework. For finance and medical aesthetics — industries that also hold asset and biometric data — the risks compound. Vigilath builds a geo-data fence, putting collection, transfer, storage and sharing under real-time monitoring.',
    },
    sections: [
      {
        heading: { zh: '为什么地理数据是高危资产', en: 'Why geographic data is high-risk' },
        points: [
          {
            title: { zh: '天生敏感', en: 'Sensitive by definition' },
            desc: {
              zh: '定位与轨迹在《个保法》下属敏感个人信息,采集须单独明示同意,静默抓取即违规。',
              en: 'Under PIPL, location and trails are sensitive personal information requiring separate, explicit consent — silent capture is itself a violation.',
            },
          },
          {
            title: { zh: '叠加行业数据更危险', en: 'Riskier when combined' },
            desc: {
              zh: '定位一旦与人脸、生物特征或金融资产信息合并采集,风险等级与监管责任成倍上升。',
              en: 'Once location is collected alongside faces, biometrics or financial assets, the risk tier and regulatory exposure multiply.',
            },
          },
          {
            title: { zh: '属地规则各不相同', en: 'Rules differ by jurisdiction' },
            desc: {
              zh: '国内数据本地化、欧盟通用数据保护条例、各国强制本地存储要求并存,一套架构难以通吃。',
              en: 'Domestic localization mandates, the GDPR and per-country storage requirements coexist — one architecture rarely satisfies them all.',
            },
          },
        ],
      },
      {
        heading: { zh: '采集:前置授权管控', en: 'Collection: consent first' },
        points: [
          {
            title: { zh: '明示同意校验', en: 'Explicit-consent checks' },
            desc: {
              zh: '强制校验定位采集是否单独弹窗明示同意,是否提供关闭定位与删除数据的入口。',
              en: 'Verify that location capture shows a dedicated consent prompt and offers switches to disable positioning and delete data.',
            },
          },
          {
            title: { zh: '超范围采集拦截', en: 'Over-collection blocking' },
            desc: {
              zh: '实时拦截越界采集,禁止同时获取定位与人脸、生物特征、资产信息,禁止静默抓取轨迹。',
              en: 'Block out-of-scope capture in real time: no bundling location with faces, biometrics or assets, and no silent trail collection.',
            },
          },
          {
            title: { zh: '采集行为留痕', en: 'Capture logging' },
            desc: {
              zh: '记录每次定位采集的触发场景与用户授权状态,授权链完整可回溯。',
              en: 'Log the trigger scenario and consent status of every location capture, keeping the authorization chain traceable.',
            },
          },
        ],
      },
      {
        heading: { zh: '流转:传输与存储属地监控', en: 'Movement: transfer and residency' },
        points: [
          {
            title: { zh: '出境流转监测', en: 'Cross-border watch' },
            desc: {
              zh: '监测地理数据出境与跨区域流转,关键行业用户数据禁止出境,异常跨境调用即时告警。',
              en: 'Monitor cross-border and cross-region flows; critical-industry user data must stay onshore, with instant alerts on anomalous calls.',
            },
          },
          {
            title: { zh: '存储属地校验', en: 'Storage-residency checks' },
            desc: {
              zh: '自动比对数据物理存储节点与用户属地:金融数据境内存储,病历与皮肤检测数据按门店属地本地存储。',
              en: 'Auto-match physical storage nodes to user locality: financial data onshore, medical records and skin scans stored locally per store region.',
            },
          },
          {
            title: { zh: '错位即高危告警', en: 'Mismatch means high-severity' },
            desc: {
              zh: '存储节点与用户属地不匹配、数据出境风险,均触发高危告警并阻断增量写入。',
              en: 'Node-locality mismatches or cross-border data transfer risks trigger high-severity alerts and block further writes.',
            },
          },
        ],
      },
      {
        heading: { zh: '共享:第三方与审计闭环', en: 'Sharing: third parties and audit' },
        points: [
          {
            title: { zh: '第三方共享监控', en: 'Third-party sharing watch' },
            desc: {
              zh: '监控投放服务商、营销公司与 AI 模型厂商是否违规导出地域数据,共享必须留存协议与脱敏日志。',
              en: 'Watch placement vendors, agencies and AI model providers for illicit geo-data export; every share requires an agreement and desensitization logs.',
            },
          },
          {
            title: { zh: '跨境合规评估', en: 'Cross-border assessments' },
            desc: {
              zh: '跨境业务校验是否完成数据出境安全评估、本地服务器存储等前置义务。',
              en: 'For cross-border operations, verify data-export security assessments and in-region server storage are all in place.',
            },
          },
          {
            title: { zh: '全链路审计留存', en: 'Full audit retention' },
            desc: {
              zh: '采集日志、流转记录、告警处置全程留痕,支撑监管调阅、取证与季度跨区域审计。',
              en: 'Retain capture logs, flow records and alert handling end to end, supporting regulator retrieval, evidence and quarterly cross-region audits.',
            },
          },
        ],
      },
    ],
    cta: {
      title: { zh: '给地理数据画一道围栏', en: 'Fence in your geographic data' },
      desc: { zh: '聊聊你的业务分布与数据架构,我们出一份可落地的地理数据合规方案。', en: 'Tell us your footprint and data architecture, and we will draft an actionable geo-data compliance plan.' },
    },
  },
];

/** slug -> Solution index. */
export const SOLUTIONS: Record<string, Solution> = Object.fromEntries(
  SOLUTION_LIST.map((s) => [s.slug, s]),
);

/** Get leaf items by column (for mega-menu rendering). */
export function solutionsByColumn(columnId: Solution['column']): Solution[] {
  const col = SOLUTION_COLUMNS.find((c) => c.id === columnId);
  if (!col) return [];
  return col.slugs.map((slug) => SOLUTIONS[slug]).filter(Boolean);
}
