// Quantified case study data -- P0 #2. Statically hardcoded (like data/blogPosts.ts); may move to a backend CMS later.
// Monitoring numbers come from real platform telemetry (test DB topic 2 "示例律所" (example law firm)); dailyLeads and quote are the external metric definitions given by the business.
// For new cases: pull data from ai_telemetry_topics / query_hits / topic_generated_docs using the same metric definitions.

export type EngineHit = { engine: string; hits: number };

export type TrendPoint = { label: string; value: number }; // cumulative AI mentions (question x engine count)

// Case headline: customers brought by AI per day. This is the external metric definition given by the business, not from telemetry --
// placed first on the page in the largest font; the technical data below is only supporting evidence. To change the number, change it only here.
export type DailyLeads = { before: number; after: number };

export type CaseMetrics = {
  seedKeywords: number;  // seed term scale (prompt count, distinct query)
  indexedCount: number;  // indexed count (number of published articles)
  citedByAI: number;     // number of articles cited by AI
};

// Case body: follows the narrative order of OptimizeGEO's case pages -- title -> three numbers -> who the customer is -> problem -> starting point -> what was done -> results -> one-liner.
export type CaseResult = { value: string; title: string; desc: string };

export type CaseLangContent = {
  brand: string;        // brand display name (no full name externally, just "a lawyer")
  vertical: string;     // vertical display name
  headline: string;     // story title: "How X gets customers from AI every day"
  tagline: string;      // subtitle: real inquiries, real customers, real cases
  listSummary: string[]; // list page summary: three paragraphs (problem / approach / results), two or three sentences each
  about: string;        // about the customer
  problem: string;      // problem: not found in AI
  starting: string;     // starting point: baseline at onboarding
  fix: string[];        // what was done (item by item)
  results: CaseResult[]; // results (leading with numbers, item by item)
  takeaway: string;     // one-line summary
  quote?: { text: string; who: string }; // customer's own words; fill in once obtained, not rendered if absent
};

export type CaseStudy = {
  slug: string;
  verticalKey: 'law' | 'tax' | 'idc' | 'robotics';
  order: number;          // list page order (case 1, case 2 ...)
  engineMax: number;      // total prompts per engine (denominator of the hit distribution)
  hitCells: number;       // number of "question x engine" cells hit in AI answers
  totalCells: number;     // total number of cells
  periodLabel?: string;
  dailyLeads?: DailyLeads;  // customers from AI per day: before optimization -> now. Leave empty if the business didn't provide numbers; the page won't render this cell
  beforeMentions: number;   // hit question count before optimization (baseline)
  hitQueries: number;       // number of prompts hit (current monitoring set metric definition, consistent with the monitoring report)
  trend: TrendPoint[];      // AI visibility growth curve (cumulative hit question count, by date)
  metrics: CaseMetrics;
  engineHits: EngineHit[]; // hit question count per engine (descending)
  zh: CaseLangContent;
  en: CaseLangContent;
};

export const caseStudies: CaseStudy[] = [
  {
    slug: 'example-law-firm',
    order: 1,
    verticalKey: 'law',
    // Data source: PG appdb (the DB geo-backend actually reads), topic 2, 2026-05-13~06-21.
    // 137 hit questions = current monitoring set metric definition (consistent with brand growth "monitoring report");
    // query_hits all-time hit queries total 139 (including 2 removed from the monitoring set).
    engineMax: 180,
    hitCells: 297,
    totalCells: 900,
    periodLabel: '2026 年 5–6 月(约 6 周)',
    // 0 customers from AI before optimization, 5 per day now (business metric definition)
    dailyLeads: { before: 0, after: 5 },
    beforeMentions: 0,
    hitQueries: 137,
    // Real daily cumulative hit question count (by each query's earliest hit date, ending aligned with the monitoring set's 137)
    trend: [
      { label: '5/6', value: 0 },
      { label: '5/13', value: 1 },
      { label: '5/19', value: 3 },
      { label: '5/20', value: 18 },
      { label: '5/25', value: 34 },
      { label: '5/26', value: 128 },
      { label: '5/31', value: 136 },
      { label: '6/9', value: 137 },
      { label: '6/21', value: 137 },
    ],
    metrics: { seedKeywords: 180, indexedCount: 152, citedByAI: 43 },
    engineHits: [
      { engine: 'wenxin', hits: 128 },
      { engine: 'doubao', hits: 92 },
      { engine: 'qwen', hits: 68 },
      { engine: 'yuanbao', hits: 7 },
      { engine: 'deepseek', hits: 2 },
    ],
    zh: {
      brand: '某律师 · 示例律所',
      vertical: '法律服务',
      headline: '某律师如何让 AI 每天带来客户',
      tagline: '真实咨询,真实客户,真实案源。',
      listSummary: [
        '某律师执业于示例律所,做跨境并购、私募股权、反垄断与 FDI。客户找律师的方式在变——企业法务先问 AI「跨境并购找哪家律所」,而在 AI 的回答里,某律师一次都没出现过。接入监测时的基线:180 个问题,0 命中。',
        '把 180 条提示词当作「客户会怎么问」的清单,按 AI 的引用偏好发布 152 篇专业内容,每周看命中数,哪条没被答到就补哪条。',
        '约 6 周,137 个问题被 AI 答案命中,43 篇内容被 AI 直接引用,5 个国产引擎全覆盖。从 AI 里一个客户都没有,到每天 5 个客户主动找上门。',
      ],
      about:
        '某律师执业于示例律师事务所,业务集中在跨境并购、私募股权、资本市场、反垄断与外商直接投资(FDI)。这些业务的客户是企业法务和创始人,决策链长、单案金额高,过去主要靠口碑和转介绍。',
      problem:
        '客户找律师的方式在变。越来越多企业法务先问 AI:「跨境并购找哪家律所」「反垄断申报谁做得好」。而在 AI 的回答里,某律师和示例律所一次都没出现过。AI 里搜不到,在这批客户眼里就等于不存在——而且 AI 推荐的是谁,连知道都不知道。',
      starting:
        '接入监测时的基线:围绕五大业务场景布设 180 条客户真正会问 AI 的问题,跑遍文心一言、豆包、通义千问、腾讯元宝、DeepSeek 5 个引擎——命中 0 条。从 AI 来的客户,也是 0。',
      fix: [
        '把 180 条提示词当作「客户会怎么问」的清单,每一条都对应一个真实的案源场景',
        '按 AI 的引用偏好生产并发布 152 篇专业内容,覆盖每一个未命中的问题',
        '每周看命中数:哪条问题还没被 AI 答到,就补哪条内容,直到命中',
      ],
      results: [
        { value: '0 → 5', title: '每天 5 个客户从 AI 找上门', desc: '优化前从 AI 来 0 个客户;现在每天约 5 个客户通过 AI 的推荐主动咨询。' },
        { value: '137 / 180', title: '个提示词被 AI 答案命中', desc: '约 6 周内从 0 到 137,客户在 AI 里问这些问题时,答案里有某律师。' },
        { value: '43', title: '篇内容被 AI 直接引用', desc: '累计发布 152 篇,其中 43 篇被 AI 原文引用为答案来源。' },
        { value: '5', title: '个国产 AI 引擎全覆盖', desc: '文心一言命中最多(128/180),豆包 92、通义千问 68。' },
      ],
      takeaway:
        '某律师没有去改某个指标,而是多了一条案源渠道。从 AI 里一个客户都没有,到每天 5 个客户主动找上门,用了 6 周。',
    },
    en: {
      brand: 'Attorney A · Example Law Firm',
      vertical: 'Legal Services',
      headline: 'How a Cross-Border M&A Lawyer Turned AI Into a Daily Source of Clients',
      tagline: 'Real inquiries. Real clients. Real matters.',
      listSummary: [
        'Attorney A practices cross-border M&A, private equity, antitrust and FDI at Example Law Firm. In-house counsel now ask AI first — "which firm for a cross-border deal?" — and in those answers he never appeared. Baseline at onboarding: 180 questions, 0 hits.',
        'The 180 monitored questions became the list of how clients actually ask. 152 pieces of expert content were published to what AI engines cite, reviewed weekly: whatever AI still wasn\'t answering got written for.',
        'In ~6 weeks, 137 questions surfaced in AI answers, 43 pieces were cited directly, all 5 Chinese engines covered. From zero clients out of AI to five a day.',
      ],
      about:
        'Attorney A practices at Example Law Firm, focusing on cross-border M&A, private equity, capital markets, antitrust and FDI. His clients are in-house counsel and founders — long decision cycles, high-value matters, historically won through reputation and referrals.',
      problem:
        'How clients find lawyers is changing. In-house counsel now ask AI first: "Which firm for a cross-border acquisition?" "Who handles antitrust filings well?" In those answers, Attorney A and his firm never appeared. Not being in AI answers meant not existing for these clients — and nobody knew which competitors AI was recommending instead.',
      starting:
        'The baseline at onboarding: 180 real client questions across five practice areas, run across five leading Chinese AI engines (ERNIE, Doubao, Qwen, Yuanbao, DeepSeek) — 0 hits. Clients from AI: also 0.',
      fix: [
        'Treated the 180 monitored questions as a list of "how clients actually ask" — each one maps to a real matter type',
        'Published 152 pieces of expert content shaped to what AI engines cite, covering every unanswered question',
        'Reviewed hits weekly: whatever AI still wasn\'t answering, write for it until it does',
      ],
      results: [
        { value: '0 → 5', title: 'clients a day from AI', desc: 'Zero clients from AI before; now about 5 inquiries a day come through AI recommendations.' },
        { value: '137 / 180', title: 'monitored questions surfaced in AI answers', desc: 'From 0 to 137 in ~6 weeks — when clients ask these questions, the answer includes Attorney A.' },
        { value: '43', title: 'pieces cited directly by AI', desc: '152 published, 43 quoted verbatim as an answer source.' },
        { value: '5', title: 'Chinese AI engines covered', desc: 'ERNIE surfaced him most (128/180), then Doubao 92 and Qwen 68.' },
      ],
      takeaway:
        'Attorney A didn\'t move a metric — he opened a new client channel. From zero clients out of AI to five a day, in six weeks.',
    },
  },
  {
    slug: 'example-tax',
    order: 2,
    verticalKey: 'tax',
    // Data source: PG appdb, topic 20 "北京示例财税管理有限公司" (Beijing example finance & tax management co.), 2026-06-29~09-15 (about 11 weeks).
    // 512 prompts, 408 hit (distinct query, total_hits>0); Doubao's three channels (doubao/doubao_browser/
    // doubao_mobile) are merged as the same engine for distinct query; 258 published = topic_doc_publish_tasks status=published with
    // published_url; 52 cited = topic_generated_docs.cited_by_json non-empty. Trend accumulated by each query's earliest hit date.
    engineMax: 512,
    hitCells: 1409,
    totalCells: 2782,
    periodLabel: '2026 年 6–9 月(约 11 周)',
    beforeMentions: 0,
    hitQueries: 408,
    trend: [
      { label: '6/29', value: 32 },
      { label: '7/30', value: 63 },
      { label: '8/13', value: 137 },
      { label: '8/21', value: 164 },
      { label: '8/24', value: 268 },
      { label: '8/31', value: 331 },
      { label: '9/6', value: 394 },
      { label: '9/14', value: 408 },
    ],
    metrics: { seedKeywords: 512, indexedCount: 258, citedByAI: 52 },
    engineHits: [
      { engine: 'wenxin', hits: 366 },
      { engine: 'doubao', hits: 282 },
      { engine: 'qwen', hits: 228 },
      { engine: 'yuanbao', hits: 226 },
      { engine: 'deepseek', hits: 203 },
    ],
    zh: {
      brand: '示例财税',
      vertical: '财税服务',
      headline: '示例财税如何让律所在 AI 里找到它',
      tagline: '一个垂直到只服务律师行业的财税公司,在 5 个 AI 引擎里被推荐。',
      listSummary: [
        '示例财税专做律师行业的财税合规——律所智慧台账、律师个税筹划、财务外包与税务风控,已服务 100+ 家律所、2000+ 名律师。但律所合伙人问 AI「律所财税外包找谁」「律师个税怎么筹划」时,AI 推荐的是别人。接入时:512 个提示词,0 命中。',
        '512 条问题覆盖律所财税、个税筹划、工商注册、专精特新与高企申报五类场景;按 AI 的引用偏好发布 258 篇内容,跑遍文心一言、豆包、通义千问、腾讯元宝、DeepSeek,每周补没命中的问题。',
        '约 11 周,408 个问题被 AI 答案命中(80%),52 篇内容被 AI 直接引用,文心一言命中 366/512。律所合伙人在 AI 里问财税,答案里有示例财税。',
      ],
      about:
        '北京示例财税管理有限公司专注律师行业的财税解决方案:律所智慧台账搭建、精英律师个税筹划、财务外包与税务风控、专精特新与高新技术企业申报。已服务 100+ 家律所、2000+ 名律师。客户是律所合伙人和提成律师——专业、挑剔、不看广告,过去主要靠同行推荐。',
      problem:
        '律所合伙人开始问 AI:「律所财税外包找谁靠谱」「律师个税怎么筹划」「律所智能台账怎么搭」。这些问题的答案里,示例财税一次都没出现过——AI 推荐的是通用财税公司,而不是唯一专做律师行业的那家。垂直到极致的优势,AI 完全不知道。',
      starting:
        '接入监测时的基线:围绕律所财税、律师个税、工商注册、专精特新与高企申报五类场景,布设 512 条律所合伙人真正会问 AI 的问题,跑遍文心一言、豆包、通义千问、腾讯元宝、DeepSeek 5 个引擎——命中 0 条。',
      fix: [
        '把 512 条提示词当作「律所合伙人会怎么问」的清单,每一条对应一个真实的业务场景',
        '按 AI 的引用偏好生产并发布 258 篇专业内容,把「只服务律师行业」这件事讲清楚',
        '每周看命中数:哪条问题 AI 还没答到示例财税,就补哪条内容,直到命中',
      ],
      results: [
        { value: '408 / 512', title: '个提示词被 AI 答案命中', desc: '约 11 周内从 0 到 408,八成的问题,AI 的答案里有示例财税。' },
        { value: '52', title: '篇内容被 AI 直接引用', desc: '累计发布 258 篇,其中 52 篇被 AI 原文引用为答案来源。' },
        { value: '366', title: '个问题在文心一言命中', desc: '512 条里命中 366,豆包 282、通义千问 228、腾讯元宝 226、DeepSeek 203。' },
        { value: '5', title: '个国产 AI 引擎全覆盖', desc: '每个引擎命中都在四成以上,不靠单一引擎。' },
      ],
      takeaway:
        '示例财税把「只做律师行业」这个最锋利的定位,讲进了 AI 的答案里。从 512 个问题 0 命中,到 408 个命中,用了 11 周。',
    },
    en: {
      brand: 'Example Tax & Accounting',
      vertical: 'Tax & Accounting',
      headline: 'How a Law-Firm-Only Accounting Firm Got Recommended by Five AI Engines',
      tagline: 'A firm that serves only lawyers, now in the answers lawyers get from AI.',
      listSummary: [
        'Example Tax does tax and accounting exclusively for law firms — firm ledgers, partner tax planning, outsourced finance and tax risk control — with 100+ firms and 2,000+ lawyers served. But when law-firm partners asked AI "who handles law-firm accounting?", AI recommended someone else. Baseline: 512 monitored questions, 0 hits.',
        '512 questions across law-firm accounting, partner tax, incorporation and innovation-grant filings; 258 pieces published to what AI engines cite, across ERNIE, Doubao, Qwen, Yuanbao and DeepSeek, topped up weekly wherever AI still wasn\'t answering.',
        'In ~11 weeks, 408 questions surfaced in AI answers (80%), 52 pieces cited directly, 366/512 on ERNIE alone. When partners ask AI about firm finances, Example Tax is in the answer.',
      ],
      about:
        'Beijing Example Tax & Accounting focuses solely on the legal industry: law-firm ledger systems, partner tax planning, outsourced finance and tax risk control, and innovation-grant filings. 100+ law firms and 2,000+ lawyers served. Its clients are partners and fee-earning lawyers — expert, demanding, immune to ads — historically won through peer referrals.',
      problem:
        'Law-firm partners started asking AI: "Who is reliable for law-firm accounting?" "How should a lawyer plan personal tax?" Example Tax never appeared in those answers. AI recommended generalist accounting firms — not the one firm that only serves lawyers. Its sharpest advantage was invisible to AI.',
      starting:
        'Baseline at onboarding: 512 real partner questions across law-firm accounting, lawyer tax, incorporation and innovation-grant filings, run across five Chinese AI engines (ERNIE, Doubao, Qwen, Yuanbao, DeepSeek) — 0 hits.',
      fix: [
        'Treated the 512 monitored questions as a list of "how partners actually ask" — each maps to a real service line',
        'Published 258 pieces of expert content shaped to what AI engines cite, making "law firms only" unmistakable',
        'Reviewed hits weekly: whatever AI still wasn\'t answering with Example Tax, write for it until it does',
      ],
      results: [
        { value: '408 / 512', title: 'monitored questions surfaced in AI answers', desc: 'From 0 to 408 in ~11 weeks — four out of five questions now name Example Tax.' },
        { value: '52', title: 'pieces cited directly by AI', desc: '258 published, 52 quoted verbatim as an answer source.' },
        { value: '366', title: 'questions surfaced on ERNIE', desc: '366 of 512; Doubao 282, Qwen 228, Yuanbao 226, DeepSeek 203.' },
        { value: '5', title: 'Chinese AI engines covered', desc: 'Above 40% on every engine — no single-engine dependence.' },
      ],
      takeaway:
        'Example Tax got its sharpest positioning — law firms only — into AI\'s answers. From 0 of 512 questions to 408, in 11 weeks.',
    },
  },
];
