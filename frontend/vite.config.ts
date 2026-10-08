import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import { visualizer } from 'rollup-plugin-visualizer'
import { readFileSync, writeFileSync, mkdirSync } from 'fs'
import { join } from 'path'
// Pure data module (no runtime dependencies), used to generate the sitemap at build time - after adding solutions/blog posts
// the sitemap syncs automatically, no manual edits needed.
import { SOLUTIONS } from './src/data/solutions'
import { blogPosts } from './src/data/blogPosts'
import { caseStudies } from './src/data/caseStudies'

/**
 * Generate sitemap.xml at build time - covers static public pages + Solutions(:slug) + Blog(:slug),
 * using the current site origin as the domain (CN site www.vigilath.cn / global site www.vigilath.com).
 * Private/app pages (dashboard/login/checkout/brand-growth etc.) are deliberately excluded.
 */
function buildSitemap(origin: string): string {
  const today = new Date().toISOString().slice(0, 10)
  type Entry = { path: string; changefreq: string; priority: string; lastmod?: string }
  const staticEntries: Entry[] = [
    { path: '/', changefreq: 'weekly', priority: '1.0' },
    { path: '/checker', changefreq: 'weekly', priority: '0.9' },
    { path: '/geo-knowledge', changefreq: 'weekly', priority: '0.8' },
    { path: '/geo-knowledge/metrics', changefreq: 'monthly', priority: '0.6' },
    { path: '/products-services', changefreq: 'monthly', priority: '0.8' },
    { path: '/pricing', changefreq: 'monthly', priority: '0.8' },
    { path: '/membership', changefreq: 'monthly', priority: '0.8' },
    { path: '/cases', changefreq: 'weekly', priority: '0.7' },
    { path: '/blog', changefreq: 'weekly', priority: '0.7' },
    { path: '/faq', changefreq: 'monthly', priority: '0.7' },
    { path: '/about', changefreq: 'monthly', priority: '0.7' },
    { path: '/process', changefreq: 'monthly', priority: '0.6' },
    { path: '/data', changefreq: 'monthly', priority: '0.6' },
    { path: '/contact', changefreq: 'monthly', priority: '0.5' },
    { path: '/privacy', changefreq: 'yearly', priority: '0.3' },
    { path: '/terms', changefreq: 'yearly', priority: '0.3' },
    { path: '/cookie-policy', changefreq: 'yearly', priority: '0.3' },
  ]
  const solutionEntries: Entry[] = Object.keys(SOLUTIONS).map((slug) => ({
    path: `/solutions/${slug}`, changefreq: 'monthly', priority: '0.8',
  }))
  const blogEntries: Entry[] = blogPosts.map((p) => ({
    path: `/blog/${p.slug}`, changefreq: 'monthly', priority: '0.6', lastmod: p.date,
  }))
  const caseEntries: Entry[] = caseStudies.map((c) => ({
    path: `/cases/${c.slug}`, changefreq: 'monthly', priority: '0.7',
  }))
  const urls = [...staticEntries, ...solutionEntries, ...blogEntries, ...caseEntries]
    .map((e) =>
      `  <url>\n    <loc>${origin}${e.path}</loc>\n    <lastmod>${e.lastmod || today}</lastmod>\n    <changefreq>${e.changefreq}</changefreq>\n    <priority>${e.priority}</priority>\n  </url>`,
    )
    .join('\n')
  return `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls}\n</urlset>\n`
}

/**
 * Post-build plugin: generates per-route HTML files with unique <title>,
 * <meta description>, <canonical>, and route-specific SEO skeleton content.
 *
 * This solves the SPA "duplicate content" problem for GEO checkers and AI
 * crawlers that don't execute JS: each route gets its own index.html with
 * unique metadata. nginx's `try_files $uri $uri/ /index.html` will serve
 * the route-specific file when it exists.
 */

/** Static SEO metadata + skeleton body for one route. */
type RouteMeta = { title: string; description: string; h1: string; content: string }

/**
 * Static route page copy - after the ICP filing remediation this repo only builds CN site artifacts:
 * all Chinese, no naming of foreign AI vendors or engines, no foreign-currency prices, contact details belong to the domestic entity.
 * New routes only need to be added here. Global site copy is frozen on the `release/vigilath-com` branch.
 * See docs/06-发布运维/合规与资质/备案整改/01-CN站去境外信息-开发文档.md
 */
const routes: Record<string, RouteMeta> = {
  '/geo-knowledge': {
    title: 'GEO 知识中心 —— AI 可见性指南｜威吉力 Vigilath',
    description: '了解生成式引擎优化的运作方式：面向 AI 爬虫的 robots.txt 配置、llms.txt、结构化数据、语义化 HTML，以及决定 AI 可见性评分的 25 个类别。',
    h1: 'GEO 知识中心 —— AI 可见性指南',
    content: `
      <section>
        <h2>理解生成式引擎优化</h2>
        <p><dfn>生成式引擎优化</dfn>（<abbr title="Generative Engine Optimization">GEO</abbr>）是让网页内容更容易被生成式 AI 引擎发现和引用的一套方法。本知识中心覆盖 GEO 的每一环 —— 从技术抓取通道（robots.txt、llms.txt、sitemap.xml）到大模型更倾向引用的内容结构。</p>
        <h2>GEO 知识分类</h2>
        <ul>
          <li><strong>抓取访问</strong> —— 如何为 AI 爬虫配置 robots.txt、llms.txt 与 .well-known 清单</li>
          <li><strong>内容可提取性</strong> —— 服务端渲染、语义化 HTML、标题层级、正文占比</li>
          <li><strong>元信息与结构化数据</strong> —— JSON-LD 标记、Open Graph、canonical</li>
          <li><strong>权威与可信</strong> —— 安全响应头、专业度信号、品牌实体知识图谱、跨平台存在感</li>
          <li><strong>答案格式适配</strong> —— 定义句、对比表格、步骤说明、FAQ 区块、可引用数据</li>
        </ul>
        <h2>GEO 指标解读</h2>
        <p>AI 可见性评分（0–100）把 25 个类别、100+ 项离散检测汇成一个可执行的数字。每个类别对应 AI 就绪度的一个具体侧面 —— 从 robots.txt 是否放行 AI 爬虫，到内容里是否有可被引用的定义与对比表格。</p>
      </section>`,
  },
  '/products-services': {
    title: '产品与服务 —— GEO 检测方案｜威吉力 Vigilath',
    description: '了解威吉力的会员方案与代运营服务：免费 GEO 检测、完整 25 类别审计、AI 搜索可见性监测、内容优化与品牌增长。',
    h1: '威吉力产品与服务',
    content: `
      <section>
        <h2>会员方案</h2>
        <p>威吉力提供多档会员方案，覆盖从个人创业者到管理数百个域名的企业团队。全部方案均包含核心的 25 类别 GEO 审计，档位越高解锁的检测次数、代运营能力与企业级功能越多。具体价格以站内<a href="/pricing">定价页</a>为准。</p>
        <table>
          <thead><tr><th>方案</th><th>每月检测次数</th><th>核心权益</th></tr></thead>
          <tbody>
            <tr><td>免费版</td><td>3 次</td><td>基础 25 类别审计、AI 可见性评分</td></tr>
            <tr><td>专业版</td><td>20 次</td><td>完整审计、检测历史、优先修复清单、报告导出</td></tr>
            <tr><td>入门版</td><td>不限</td><td>GEO 代运营、AI 收录规范、文案合规校验</td></tr>
            <tr><td>增长版</td><td>不限</td><td>入门版全部权益 + 媒介投放、优先技术支持</td></tr>
            <tr><td>企业版</td><td>不限</td><td>定制化 GEO 覆盖、品牌声誉管理、专属顾问</td></tr>
          </tbody>
        </table>
        <h2>GEO 代运营服务</h2>
        <p>入门版及以上档位提供落地服务：我们直接实施审计发现的问题 —— 配置面向 AI 爬虫的 robots.txt、搭建 JSON-LD 结构化数据、生成 llms.txt，并把内容整理成 AI 更容易引用的结构。</p>
      </section>`,
  },
  '/about': {
    title: '关于威吉力 —— 我们的定位与团队｜Vigilath',
    description: '威吉力是独立第三方 AI 可见性检测平台，帮助网站提升在 AI 搜索中的可见性。了解我们的团队、定位与 AI 可见性评分背后的技术。',
    h1: '关于威吉力',
    content: `
      <section>
        <h2>我们做什么</h2>
        <p>威吉力成立的初衷是解决数字营销里的一个新问题：让网站在 AI 回答里被看见。随着生成式 AI 重塑人们获取信息的方式，我们提供工具与专业能力，确保品牌被 AI 引擎引用、转述和推荐。</p>
        <h2>平台能力</h2>
        <p>我们构建了一体化的 <abbr title="Generative Engine Optimization">GEO</abbr>+<abbr title="Answer Engine Optimization">AEO</abbr> 审计平台。威吉力从 25 个类别、100+ 项信号扫描任意网站，产出 0–100 的 AI 可见性评分、类别拆解与优先修复清单，并通过代运营服务帮团队把这些修复规模化落地。</p>
      </section>`,
  },
  '/privacy': {
    title: '隐私政策｜威吉力 Vigilath',
    description: '威吉力隐私政策。了解我们在您使用威吉力 GEO 检测平台时如何收集、使用和保护您的个人信息。',
    h1: '隐私政策',
    content: `
      <section>
        <h2>1. 概述</h2>
        <p>威吉力（Vigilath）重视您的隐私。本隐私政策说明我们在您使用威吉力 GEO 检测服务（以下称"本服务"）时如何收集、使用和保护您的个人信息。</p>
        <h2>2. 我们收集的信息</h2>
        <p>我们收集您主动提供的信息（账号注册、联系表单）以及自动采集的信息（使用数据、设备信息、Cookie）。我们不会向第三方出售您的个人数据。</p>
        <h2>3. 信息的使用</h2>
        <p>我们使用您的信息来提供和改进本服务、处理交易、发送通知并保障安全。完整条款以本页面渲染的正式版本为准。</p>
        <h2>4. 联系我们</h2>
        <p>隐私相关问题请联系 &lt;support-email&gt;，或访问<a href="/contact">联系我们</a>页面。</p>
      </section>`,
  },
  '/terms': {
    title: '服务条款｜威吉力 Vigilath',
    description: '威吉力服务条款。阅读您使用威吉力 GEO+AEO 平台所适用的条款，包括会员方案、可接受使用规范与知识产权。',
    h1: '服务条款',
    content: `
      <section>
        <h2>1. 条款的接受</h2>
        <p>访问或使用威吉力（以下称"本服务"）即表示您同意受本服务条款约束。若您不同意，请勿使用本服务。</p>
        <h2>2. 服务说明</h2>
        <p>威吉力提供生成式引擎优化（GEO）就绪度检测服务，从 25 个类别审计网站以评估其 AI 可见性。本服务包含免费与付费会员档位。</p>
        <h2>3. 会员与计费</h2>
        <p>付费方案按周期计费。您可随时取消，取消于当前计费周期结束时生效。</p>
        <h2>4. 联系我们</h2>
        <p>关于本条款的问题请联系 &lt;support-email&gt;，或访问<a href="/contact">联系我们</a>页面。</p>
      </section>`,
  },
  '/membership': {
    title: '会员方案 —— 免费版到企业版｜威吉力 Vigilath',
    description: '对比威吉力各档会员方案。免费版每月含 3 次检测，付费档位提供完整审计、检测历史、GEO 代运营与企业级能力。',
    h1: '威吉力会员方案',
    content: `
      <section>
        <h2>选择适合你的 AI 可见性方案</h2>
        <p>每档方案都包含核心的 25 类别审计。档位越高，解锁的检测次数、代运营能力与企业级功能越多。具体价格以站内<a href="/pricing">定价页</a>为准。</p>
        <table>
          <thead><tr><th>方案</th><th>检测次数</th><th>修复清单</th><th>检测历史</th><th>GEO 代运营</th><th>专属顾问</th></tr></thead>
          <tbody>
            <tr><td>免费版</td><td>3 次/月</td><td>基础</td><td>否</td><td>否</td><td>否</td></tr>
            <tr><td>专业版</td><td>20 次/月</td><td>完整</td><td>是</td><td>否</td><td>否</td></tr>
            <tr><td>入门版</td><td>不限</td><td>完整</td><td>是</td><td>是</td><td>否</td></tr>
            <tr><td>增长版</td><td>不限</td><td>完整</td><td>是</td><td>是</td><td>否</td></tr>
            <tr><td>企业版</td><td>不限</td><td>完整</td><td>是</td><td>是</td><td>是</td></tr>
          </tbody>
        </table>
        <h2>常见问题</h2>
        <ul>
          <li><strong>可以随时升级吗？</strong> —— 可以，升级按比例折算并立即生效。</li>
          <li><strong>有免费试用吗？</strong> —— 免费版长期免费使用。</li>
          <li><strong>支持哪些支付方式？</strong> —— 微信支付。</li>
        </ul>
      </section>`,
  },
  '/contact': {
    title: '联系我们｜威吉力 Vigilath',
    description: '联系威吉力团队咨询商务合作、技术支持或渠道合作。我们会在 24 小时内响应。',
    h1: '联系威吉力',
    content: `
      <section>
        <h2>与我们取得联系</h2>
        <p>对 GEO 有疑问、需要 AI 可见性审计的协助，或想了解代运营服务？我们的团队随时提供支持。</p>
        <ul>
          <li><strong>邮箱：</strong> &lt;support-email&gt;</li>
          <li><strong>客服热线：</strong> &lt;support-phone&gt;</li>
          <li><strong>响应时间：</strong> 工作日 24 小时内</li>
        </ul>
        <h2>我们能帮你解决什么</h2>
        <ul>
          <li>威吉力检测与报告的技术支持</li>
          <li>入门版、增长版、企业版的商务咨询</li>
          <li>渠道合作与系统对接</li>
          <li>媒体与采访事宜</li>
        </ul>
      </section>`,
  },
  '/cookie-policy': {
    title: 'Cookie 政策｜威吉力 Vigilath',
    description: '威吉力如何使用 Cookie 与浏览器存储。包含必要的 localStorage（登录凭据、用户信息、语言、主题）及其管理方式。',
    h1: 'Cookie 政策',
    content: `
      <section>
        <h2>1. 什么是 Cookie</h2>
        <p>Cookie 是网站存放在您设备上的小型文本文件，用于记住偏好并改善使用体验。威吉力同时使用 <code>localStorage</code> 等浏览器存储技术。</p>
        <h2>2. 我们使用的 Cookie 与存储</h2>
        <p><strong>必要存储</strong>（localStorage）：<code>token</code>（登录凭据）、<code>user</code>（当前用户基本信息）、<code>i18nextLng</code>（语言偏好）、<code>theme</code>（浅色/深色偏好）。</p>
        <p><strong>第三方 Cookie：</strong>支付页面在完成微信支付流程时可能写入支付平台的必要 Cookie。</p>
        <h2>3. 如何管理 Cookie</h2>
        <p>您可以通过浏览器设置管理或删除 Cookie 与 localStorage 数据。禁用必要存储可能导致需要重新登录或部分功能无法正常工作。</p>
        <h2>4. 联系我们</h2>
        <p>关于本 Cookie 政策的问题，请发送邮件至 <a href="mailto:<support-email>">&lt;support-email&gt;</a>。</p>
      </section>`,
  },
  '/faq': {
    title: '常见问题 —— GEO 与 AI 可见性答疑｜威吉力 Vigilath',
    description: '客户最常问的问题：GEO 与 SEO 有何不同、覆盖哪些 AI 引擎、多久见效、如何定价、8 步增长流程与舆情监测。',
    h1: '常见问题',
    content: `
      <section>
        <h2>常见问题</h2>
        <h3>GEO 和 SEO 有什么不同？</h3>
        <p>GEO（生成式引擎优化）关注的是让品牌被 AI 助手主动推荐，SEO 关注的是在搜索结果页里的排名。GEO 面向 AI 搜索时代，是对 SEO 的补充而非替代。</p>
        <h3>多久能看到效果？</h3>
        <p>视行业与竞争程度，客户通常在 1–3 个月内看到 AI 提及率与引用率上升，3–6 个月达到较充分的优化状态。驾驶舱每日产出可见性曲线，便于量化跟踪。</p>
        <h3>覆盖哪些 AI 引擎？</h3>
        <p>覆盖 DeepSeek、通义千问、豆包、文心一言、腾讯元宝、Kimi 等国内主流引擎。新引擎只要开放接口或可抓取，通常两周内接入。</p>
        <h3>8 步流程和传统 SEO 代理有什么区别？</h3>
        <p>传统 SEO 是人工串行：关键词 → 内容 → 外链 → 等排名。8 步流程把链路自动闭环 —— 从种子扩展、聚类、成稿、分发，到 AI 收录验证、引用监测和反馈迭代。</p>
        <h3>舆情监测是实时的吗？</h3>
        <p>目前为每日批量采集加即时告警。命中负面报道或关键词时，15 分钟内通过邮件与站内通知推送。</p>
        <h3>可以自助使用，还是必须联系销售？</h3>
        <p>免费档位可自助运行 25 类别 GEO 审计与爬虫可访问性测试，会员解锁全部进阶检测。8 步流程加内容分发属于增长服务，需要商务对接。</p>
        <h3>费用是多少？</h3>
        <p>GEO 自助会员按月订阅，具体以定价页为准。8 步增长服务按项目计价，取决于行业、目标引擎与内容产出量，可联系商务获取报价。</p>
        <h3>提供持续维护吗？</h3>
        <p>提供，而且是必需的 —— AI 引擎的算法、抓取偏好和排序规则持续变化。月度维护包含模型升级跟踪、提示词刷新、内容策略调整与引用位次防守。</p>
      </section>
      <script type="application/ld+json">
      {"@context":"https://schema.org","@type":"FAQPage","mainEntity":[
        {"@type":"Question","name":"GEO 和 SEO 有什么不同？","acceptedAnswer":{"@type":"Answer","text":"GEO（生成式引擎优化）关注让品牌被 AI 助手主动推荐，SEO 关注搜索结果页排名。GEO 是对 SEO 的补充而非替代。"}},
        {"@type":"Question","name":"多久能看到效果？","acceptedAnswer":{"@type":"Answer","text":"客户通常在 1–3 个月内看到 AI 提及率与引用率上升，3–6 个月达到较充分的优化状态，可通过每日可见性曲线跟踪。"}},
        {"@type":"Question","name":"覆盖哪些 AI 引擎？","acceptedAnswer":{"@type":"Answer","text":"覆盖 DeepSeek、通义千问、豆包、文心一言、腾讯元宝、Kimi 等国内主流引擎。"}},
        {"@type":"Question","name":"可以自助使用，还是必须联系销售？","acceptedAnswer":{"@type":"Answer","text":"免费档位可自助运行 25 类别 GEO 审计，会员解锁进阶检测。8 步增长流程属于代运营服务，需要商务对接。"}},
        {"@type":"Question","name":"提供持续维护吗？","acceptedAnswer":{"@type":"Answer","text":"提供。月度维护包含模型升级跟踪、提示词刷新、内容策略调整与引用位次防守。"}}
      ]}
      </script>`,
  },
  '/pricing': {
    title: '定价 —— GEO 会员与增长方案｜威吉力 Vigilath',
    description: '对比威吉力各档方案：免费版（每月 3 次检测）、专业版（20 次完整审计）、入门版与增长版（GEO 代运营）、企业版（定制）。',
    h1: '威吉力定价',
    content: `
      <section>
        <h2>会员方案</h2>
        <p>每档方案都包含核心的 25 类别审计。档位越高，解锁的检测次数、代运营能力与企业级功能越多。付费方案支持微信支付，按人民币计价。</p>
        <table>
          <thead><tr><th>方案</th><th>每月检测次数</th><th>核心权益</th></tr></thead>
          <tbody>
            <tr><td>免费版</td><td>3 次</td><td>基础 25 类别审计、AI 可见性评分</td></tr>
            <tr><td>专业版</td><td>20 次</td><td>完整审计、检测历史、优先修复清单、报告导出</td></tr>
            <tr><td>入门版</td><td>不限</td><td>GEO 代运营、AI 收录规范、文案合规校验</td></tr>
            <tr><td>增长版</td><td>不限</td><td>入门版全部权益 + 媒介投放、优先技术支持</td></tr>
            <tr><td>企业版</td><td>不限</td><td>定制化 GEO 覆盖、品牌声誉管理、专属顾问</td></tr>
          </tbody>
        </table>
      </section>`,
  },
  '/process': {
    title: '威吉力如何工作 —— GEO 审计流程｜Vigilath',
    description: '威吉力的流程：输入网址，跑一遍 25 类别、100+ 信号的审计，覆盖抓取访问、内容可提取性、结构化数据、权威信号与答案格式适配，得到 0–100 的 AI 可见性评分与优先修复清单。',
    h1: '威吉力如何工作',
    content: `
      <section>
        <h2>从一个网址到 AI 可见性评分</h2>
        <ol>
          <li><strong>输入网址</strong> —— 指向任意网站或落地页。</li>
          <li><strong>审计 25 个类别 / 100+ 项信号</strong> —— 覆盖抓取访问、内容可提取性、元信息与结构化数据、权威与可信、答案格式适配。</li>
          <li><strong>评分</strong> —— 得到 0–100 的 AI 可见性评分与等级。</li>
          <li><strong>类别拆解</strong> —— 看清每项信号是通过、警告还是失败。</li>
          <li><strong>优先修复清单</strong> —— 按影响力排序的可执行建议。</li>
          <li><strong>落地优化</strong> —— 自助实施，或交给威吉力的 GEO / AEO 代运营服务。</li>
        </ol>
      </section>`,
  },
  '/data': {
    title: 'AI 可见性数据与指标 —— 评分如何计算｜威吉力 Vigilath',
    description: '理解威吉力的指标体系：0–100 的 AI 可见性评分、25 类别拆解、100+ 项信号、AI 引用频次与回答收录率。',
    h1: 'AI 可见性数据与指标',
    content: `
      <section>
        <h2>这些数字意味着什么</h2>
        <p><strong>AI 可见性评分</strong>（0–100）把 25 个类别、100+ 项离散检测汇成一个可执行的数字，并附带等级。</p>
        <ul>
          <li><strong>25 类别拆解</strong> —— 每个类别对应 AI 就绪度的一个具体侧面，从 robots.txt 抓取权限到可被引用的内容形态。</li>
          <li><strong>引用频次</strong> —— AI 引擎引用或提及你的域名的频率。</li>
          <li><strong>回答收录率</strong> —— 品牌在各 AI 引擎生成的回答中出现的比例。</li>
          <li><strong>每日可见性曲线</strong> —— 随着修复落地与引擎重新抓取，跟踪指标的变化。</li>
        </ul>
      </section>`,
  },
}

function seoPages(origin: string): Plugin {

  return {
    name: 'seo-pages',
    apply: 'build',
    closeBundle() {
      const distDir = join(process.cwd(), 'dist')
      const host = new URL(origin).host // e.g. www.vigilath.cn / www.vigilath.com

      // sitemap.xml is generated from data at build time (overwriting the static file), with the correct domain + all pages.
      try {
        writeFileSync(join(distDir, 'sitemap.xml'), buildSitemap(origin))
      } catch {
        // skip if dist is not ready yet (rare)
      }

      // CN site (REGION=cn): replace the default domain in all static SEO files with this site's domain.
      // These are static files in public/ (robots/sitemap/feed/llms/ai/.well-known)
      // that cannot read import.meta.env, so we can only do string replacement on the dist/ build output.
      // Must run before the per-route page generation below so derived pages inherit the correct domain.
      if (host !== 'www.vigilath.com') {
        const seoFiles = [
          'robots.txt', 'feed.xml', 'humans.txt',
          'llms.txt', 'llms-full.txt', 'ai.txt', 'index.html',
          '.well-known/ai-plugin.json', '.well-known/security.txt',
          '.well-known/ai.txt', '.well-known/openai.yaml', '.well-known/llms.txt',
        ]
        for (const f of seoFiles) {
          const p = join(distDir, f)
          try {
            writeFileSync(p, readFileSync(p, 'utf-8').split('www.vigilath.com').join(host))
          } catch {
            // skip if the file does not exist (some sites do not have it)
          }
        }
      }

      let html: string
      try {
        html = readFileSync(join(distDir, 'index.html'), 'utf-8')
      } catch {
        return // dist/index.html not yet built
      }

      for (const [route, meta] of Object.entries(routes)) {
        const dir = join(distDir, route.slice(1)) // e.g. dist/geo-knowledge
        mkdirSync(dir, { recursive: true })

        let page = html
        // Replace <title>
        page = page.replace(
          /<title>[^<]*<\/title>/,
          `<title>${meta.title}</title>`,
        )
        // Replace meta description
        page = page.replace(
          /<meta name="description" content="[^"]*"/,
          `<meta name="description" content="${meta.description}"`,
        )
        // Replace canonical
        page = page.replace(
          /<link rel="canonical" href="[^"]*"/,
          `<link rel="canonical" href="${origin}${route}"`,
        )
        // Replace og:title
        page = page.replace(
          /<meta property="og:title" content="[^"]*"/,
          `<meta property="og:title" content="${meta.title}"`,
        )
        // Replace og:description
        page = page.replace(
          /<meta property="og:description" content="[^"]*"/,
          `<meta property="og:description" content="${meta.description}"`,
        )
        // Replace og:url
        page = page.replace(
          /<meta property="og:url" content="[^"]*"/,
          `<meta property="og:url" content="${origin}${route}"`,
        )
        // Replace twitter:title
        page = page.replace(
          /<meta name="twitter:title" content="[^"]*"/,
          `<meta name="twitter:title" content="${meta.title}"`,
        )
        // Replace twitter:description
        page = page.replace(
          /<meta name="twitter:description" content="[^"]*"/,
          `<meta name="twitter:description" content="${meta.description}"`,
        )
        // Replace SEO skeleton content (between <article> tags)
        page = page.replace(
          /<article>[\s\S]*?<\/article>/,
          `<article><h1>${meta.h1}</h1>${meta.content}</article>`,
        )
        // Update breadcrumb (the template's aria-label / home label are in Chinese)
        page = page.replace(
          /<nav aria-label="面包屑">[\s\S]*?<\/nav>/,
          `<nav aria-label="面包屑"><ol><li><a href="/">首页</a></li><li><a href="${route}">${meta.h1.replace(/&amp;/g, '&')}</a></li></ol></nav>`,
        )

        writeFileSync(join(dir, 'index.html'), page)
      }
    },
  }
}

// https://vitejs.dev/config/
export default defineConfig(() => {
  // After the ICP filing remediation this repo only builds CN site artifacts; the SEO domain is fixed to www.vigilath.cn
  // (www.vigilath.com.cn shares the same artifacts; the footer filing number is switched by domain at runtime).
  // The global site build is frozen on the release/vigilath-com branch.
  const SITE_ORIGIN = 'https://www.vigilath.cn'

  return {
  plugins: [react(), seoPages(SITE_ORIGIN)],
  cacheDir: '.vite/build-cache',
  build: {
    outDir: 'dist',
    sourcemap: false,
    minify: 'terser',
    // WeCom/WeChat in-app browsers are old-engine WebViews (X5, engine often < Chrome 87).
    // Set the CSS target to an old baseline so the minifier does not emit new shorthands like `inset` and keeps longhands,
    // fixing absolutely positioned overlays drifting in WeCom, such as "donut chart center text not showing".
    cssTarget: ['chrome61', 'safari11', 'ios11'],
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules/react') || id.includes('node_modules/react-dom')) {
            return 'vendor';
          }
          if (id.includes('node_modules/react-router-dom')) {
            return 'router';
          }
          if (id.includes('node_modules/i18next') || id.includes('node_modules/react-i18next')) {
            return 'i18n';
          }
          if (id.includes('node_modules/@radix-ui/react-tooltip')) {
            return 'ui';
          }
        }
      },
      plugins: [
        // Analysis artifact lives outside dist/ so it never ships to webroot.
        // open:false — on headless/EC2 build env, opening a browser hangs.
        // Run `npx vite build && xdg-open stats.html` locally to view.
        visualizer({
          open: false,
          filename: 'stats.html'
        })
      ]
    }
  },
  server: {
    // Aitoearn Extension 3.3.0 injects its Web API on localhost:8080,
    // but not on localhost:3000. Keep local development on a supported origin.
    port: 8080,
    open: true,
    proxy: {
      // Local dev proxies straight to the production API (no need to start a local backend:8070).
      // changeOrigin rewrites Host to www.vigilath.com (production routes by Host + needs TLS SNI);
      // secure:true verifies the production certificate. To switch back to a local backend, change target back to http://localhost:8070.
      '/api': {
        target: 'http://localhost:8070',
        changeOrigin: true,
      },
      '/pay': {
        target: 'http://localhost:3010',
        changeOrigin: true,
      },
    },
  }
  }
})
