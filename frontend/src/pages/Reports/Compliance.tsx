// Compliance audit center — full-chain risk control ledger for placement content in special industries (YMYL high-risk categories).
// Design doc: docs/06-发布运维/合规与资质/合规中心/合规审计中心-整体设计.md (v2 "one gate, three stages").
//
// P0 is wired to real data: compliance profile bar (lexicons / qualification status) + pre-publication review ledger (lexicon scan
// results, reusing /api/compliance); demo-isomorphic -- when the API has no scan data it falls back to examples templated from the current
// topic (brand name / industry tier / engines all substituted in).
// In-flight monitoring and handling (P1) and the exportable review package (P2) are still demo content.
// Copy uses local bilingual strings (same approach as lang.ts); the zh side doesn't mix in English terms.

import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { ReportsShell, type ShellState } from './shell';
import { engineLabel, sortEngines, visibleEngines } from '../../constants/engines';
import { UiSelect } from '../../components/ui/Select';
import i18n from '../../i18n';
import { overlayDismiss } from '../../components/ui/useOverlayDismiss';
import {
  complianceApi,
  type AlertItem, type ComplianceKpis, type ComplianceProfile, type LexiconDetail,
  type ScanDetail, type ScanListItem, type TicketAction, type TimelineItem,
} from '../../services/complianceApi';

function useZh(): boolean {
  return (i18n.language || 'zh').startsWith('zh');
}

// ── Sample data types (single language, built for the current language) ──────────
type HitLevel = 'ban' | 'warn' | 'evid';
type Stage = 'gate0' | 'pre' | 'mid' | 'post';
type Tier = 'beauty' | 'medical' | 'finance' | 'generic';

interface LexHit {
  word: string;
  source: string;       // Lexicon + category
  level: HitLevel;
  levelLabel: string;
  fix: string;          // Replacement suggestion
}

interface ExcerptSeg {
  t: string;
  mark?: HitLevel;
}

interface ChainStep {
  name: string;
  who: string;
  status: 'done' | 'now' | 'todo';
}

interface TraceLine {
  who: string;
  at: string;
  note: string;
}

interface Claim {
  ok: boolean;
  text: string;
  evidence: string;
}

interface AuditRecord {
  id: string;
  at: string;
  title: string;
  channel: string;
  hits: LexHit[];
  chainLabel: string;
  chainTone: 'ok' | 'warn' | 'ban';
  verdict: string;
  verdictTone: 'ok' | 'ban';
  excerpt: ExcerptSeg[];
  chain: ChainStep[];
  trace: TraceLine[];
  claims: Claim[];
}

interface MonitorAlert {
  level: 1 | 2 | 3;
  type: string;
  quote: string;
  channel: string;
  foundAt: string;
  disposal: string;
  closed: boolean;
}

interface TimelineEvent {
  at: string;
  logId: string;
  tone: 'ban' | 'warn' | 'ok' | 'plain';
  title: string;
  detail: string;
}

interface DemoData {
  brand: string;
  brandSub: string;
  tierLabel: string;
  lexicons: { label: string; on: boolean }[];
  records: AuditRecord[];
  alerts: MonitorAlert[];
  timeline: TimelineEvent[];
}

// ── Industry -> risk control tier derivation ─────────────────────────────
function tierOf(industry: string): Tier {
  const s = industry || '';
  if (/美容|美妆|护肤|化妆|医美/.test(s) || /beauty|cosmetic|skincare/i.test(s)) return 'beauty';
  if (/医疗|健康|制药|医药|药械/.test(s) || /medical|health|pharma/i.test(s)) return 'medical';
  if (/金融|银行|保险|证券|基金|理财|支付/.test(s) || /financ|bank|insurance|invest/i.test(s)) return 'finance';
  return 'generic';
}

// ── Sample data construction: brand / industry / engines all substituted from the current topic ──
function buildDemo(zh: boolean, brand: string, industry: string, engineNames: string[]): DemoData {
  const tier = tierOf(industry);
  // Fallback sample engines also come from the single authoritative source, avoiding aliases like 「通义 / 深度求索」 ("Tongyi / DeepSeek") that are inconsistent site-wide
  const fallback = ['doubao', 'qwen', 'deepseek'].map(engineLabel);
  const eng = (i: number) =>
    engineNames.length > 0
      ? engineNames[i % engineNames.length]
      : fallback[i % fallback.length];

  const tierLabel = zh
    ? {
      beauty: '特殊行业二 · 美容(高审核门槛类目)',
      medical: '特殊行业 · 医疗健康(强监管类目)',
      finance: '特殊行业一 · 金融(强监管类目)',
      generic: '通用行业 · 标准风控档',
    }[tier]
    : {
      beauty: 'Regulated tier 2 · Beauty (high-scrutiny category)',
      medical: 'Regulated · Healthcare (tightly regulated)',
      finance: 'Regulated tier 1 · Finance (tightly regulated)',
      generic: 'General industry · standard risk profile',
    }[tier];

  const off = zh ? '(未启用)' : ' (off)';
  const lexicons: { label: string; on: boolean }[] = [
    { label: zh ? '广告法极限词通用库 · 187 词' : 'Ad-law superlatives · 187 terms', on: true },
    {
      label: (zh ? '美妆功效违禁库 · 642 词' : 'Cosmetics efficacy · 642 terms') + (tier === 'beauty' ? (zh ? '(药妆 / 医用级 / 医疗功效承诺)' : ' (cosmeceutical / medical-grade / medical claims)') : off),
      on: tier === 'beauty',
    },
    {
      label: (zh ? '医美医疗违禁库 · 2 836 词' : 'Medical aesthetics · 2,836 terms') + (tier === 'beauty' || tier === 'medical' ? (zh ? '(根治 / 永久 / 百分百见效…)' : ' (cure / permanent / 100% results…)') : off),
      on: tier === 'beauty' || tier === 'medical',
    },
    {
      label: (zh ? '金融合规库 · 1 180 词' : 'Finance lexicon · 1,180 terms') + (tier === 'finance' ? (zh ? '(保本 / 稳赚 / 零风险…)' : ' (guaranteed / risk-free…)') : off),
      on: tier === 'finance',
    },
  ];

  const lv = (l: HitLevel) =>
    zh ? { ban: '禁用', warn: '慎用', evid: '需举证' }[l] : { ban: 'banned', warn: 'restricted', evid: 'evidence required' }[l];
  const genericLib = zh ? '广告法极限词通用库' : 'Ad-law superlatives lexicon';

  // Three common forms of the four-layer review chain
  const chainAt = (step: 1 | 2 | 4): ChainStep[] => {
    const names = zh
      ? ['智能审核', '人工初审', '法务复核', '第三方抽检']
      : ['Automated scan', 'Editorial review', 'Legal review', 'Third-party check'];
    const whos = zh
      ? ['词库引擎 2026.06 版', '内容组 · 沈祎', step > 2 ? '法务组 · 高岚' : '法务组 · 待处理', '外部机构']
      : ['Lexicon engine v2026.06', 'Content team · Shen Yi', step > 2 ? 'Legal team · Gao Lan' : 'Legal team · pending', 'External agency'];
    return names.map((name, i) => ({
      name,
      who: whos[i],
      status: step === 4 ? 'done' : i + 1 < step ? 'done' : i + 1 === step ? 'now' : 'todo',
    }));
  };

  const records: AuditRecord[] = [
    {
      id: 'SC-2026-0627-014',
      at: '06-27 14:32',
      title: zh ? `${brand} 旗舰产品深度使用体验` : `${brand} flagship product in-depth review`,
      channel: zh ? '问答社区 · 体验分享' : 'Q&A community · experience post',
      hits: [
        {
          word: zh ? '全球第一' : 'world No.1',
          source: `${genericLib}${zh ? ' · 排他表述 · 禁用' : ' · exclusivity claim · banned'}`,
          level: 'ban', levelLabel: lv('ban'),
          fix: zh ? '在多个市场获得用户认可' : 'recognized by users across multiple markets',
        },
        {
          word: zh ? '百分之百有效' : '100% effective',
          source: `${genericLib}${zh ? ' · 绝对化承诺 · 禁用' : ' · absolute promise · banned'}`,
          level: 'ban', levelLabel: lv('ban'),
          fix: zh ? '多数用户反馈体验良好' : 'most users report a positive experience',
        },
        {
          word: zh ? '7 天见效' : 'results in 7 days',
          source: `${genericLib}${zh ? ' · 时限承诺 · 慎用' : ' · time-bound promise · restricted'}`,
          level: 'warn', levelLabel: lv('warn'),
          fix: zh ? '使用一段时间后体验有提升' : 'experience improves with continued use',
        },
        {
          word: zh ? '专利技术' : 'patented technology',
          source: zh ? '溯源核对项 · 需举证' : 'Provenance check · evidence required',
          level: 'evid', levelLabel: lv('evid'),
          fix: zh ? '保留,须附专利号并可查证(见溯源核对)' : 'keep, must cite a verifiable patent number (see provenance)',
        },
      ],
      chainLabel: zh ? '法务复核中(2/4)' : 'Legal review pending (2/4)',
      chainTone: 'warn',
      verdict: zh ? '退回修改' : 'Returned for revision',
      verdictTone: 'ban',
      excerpt: zh
        ? [
          { t: `…上手一个月后,体验肉眼可见地提升。有博主称 ${brand} 是` },
          { t: '全球第一', mark: 'ban' },
          { t: ',' },
          { t: '百分之百有效', mark: 'ban' },
          { t: ',' },
          { t: '7 天见效', mark: 'warn' },
          { t: '。作为' },
          { t: '专利技术', mark: 'evid' },
          { t: '加持的旗舰线,确实对得起价格…' },
        ]
        : [
          { t: `…after a month, the experience visibly improved. Bloggers call ${brand} the ` },
          { t: 'world No.1', mark: 'ban' },
          { t: ', ' },
          { t: '100% effective', mark: 'ban' },
          { t: ', with ' },
          { t: 'results in 7 days', mark: 'warn' },
          { t: '. Backed by ' },
          { t: 'patented technology', mark: 'evid' },
          { t: ', the flagship line earns its price…' },
        ],
      chain: chainAt(2),
      trace: [
        {
          who: zh ? '词库引擎' : 'Lexicon engine', at: '06-27 14:32',
          note: zh ? '命中 4 处:禁用 2 · 慎用 1 · 需举证 1,自动标注替换建议' : '4 hits: 2 banned, 1 restricted, 1 evidence-required; fixes auto-suggested',
        },
        {
          who: zh ? '沈祎' : 'Shen Yi', at: '06-27 15:10',
          note: zh ? '同意引擎判定;另标注「旗舰」为调性用词可保留' : 'agreed with the scan; flagged "flagship" as tonal wording, may keep',
        },
        {
          who: zh ? '法务组' : 'Legal team', at: '—',
          note: zh ? '排队中,时限 24 小时' : 'queued, 24-hour deadline',
        },
      ],
      claims: [
        {
          ok: true,
          text: zh ? '「官方渠道有售」' : '"Available via official channels"',
          evidence: zh ? '授权渠道清单 2026 版 已归档 DOC-089' : 'authorized-channel list 2026 archived, DOC-089',
        },
        {
          ok: false,
          text: zh ? '「专利技术」' : '"Patented technology"',
          evidence: zh ? '待补:专利号及登记页截图,补齐前该句不得放行' : 'missing: patent number and registry screenshot; sentence blocked until provided',
        },
      ],
    },
    {
      id: 'SC-2026-0627-011',
      at: '06-27 11:05',
      title: zh ? `${brand} 与同类产品怎么选:横向对比` : `${brand} vs. peers: a side-by-side comparison`,
      channel: zh ? '内容社区 · 测评' : 'Content community · review',
      hits: [
        {
          word: zh ? '行业第一' : 'industry No.1',
          source: `${genericLib}${zh ? ' · 排他表述 · 慎用' : ' · exclusivity claim · restricted'}`,
          level: 'warn', levelLabel: lv('warn'),
          fix: zh ? '行业头部选择之一' : 'one of the leading choices in the field',
        },
      ],
      chainLabel: zh ? '四重通过(4/4)' : 'All four passed (4/4)',
      chainTone: 'ok',
      verdict: zh ? '替换后放行' : 'Released after revision',
      verdictTone: 'ok',
      excerpt: zh
        ? [
          { t: `…论口碑与成熟度,${brand} 常被称作` },
          { t: '行业第一', mark: 'warn' },
          { t: '。按建议改为体验性表述后放行…' },
        ]
        : [
          { t: `…for reputation and maturity, ${brand} is often called ` },
          { t: 'industry No.1', mark: 'warn' },
          { t: '. Released after rewording as an experiential claim…' },
        ],
      chain: chainAt(4),
      trace: [
        {
          who: zh ? '词库引擎' : 'Lexicon engine', at: '06-27 11:05',
          note: zh ? '命中 1 处慎用词,给出替换建议' : '1 restricted term hit; fix suggested',
        },
        {
          who: zh ? '高岚' : 'Gao Lan', at: '06-27 13:20',
          note: zh ? '替换后复核通过,准予发布' : 'revision approved for release',
        },
      ],
      claims: [],
    },
    {
      id: 'SC-2026-0626-047',
      at: '06-26 17:48',
      title: zh ? `${brand} 官方正品渠道购买指南` : `Buying genuine ${brand}: official channel guide`,
      channel: zh ? '自有官网 · 专栏' : 'Owned site · column',
      hits: [],
      chainLabel: zh ? '四重通过(4/4)' : 'All four passed (4/4)',
      chainTone: 'ok',
      verdict: zh ? '直接放行' : 'Released',
      verdictTone: 'ok',
      excerpt: [
        {
          t: zh
            ? '…正品渠道以官方渠道与授权经销为准,真伪可向官方核验。全文无违禁词命中,溯源信息齐备…'
            : '…genuine purchases go through official and authorized channels; authenticity is verifiable with the brand. No lexicon hits; provenance complete…',
        },
      ],
      chain: chainAt(4),
      trace: [
        {
          who: zh ? '词库引擎' : 'Lexicon engine', at: '06-26 17:48',
          note: zh ? '启用词库扫描无命中' : 'no hits across enabled lexicons',
        },
      ],
      claims: [
        {
          ok: true,
          text: zh ? '「官方渠道有售」' : '"Available via official channels"',
          evidence: zh ? '授权渠道清单 2026 版 已归档 DOC-089' : 'authorized-channel list 2026 archived, DOC-089',
        },
      ],
    },
    {
      id: 'SC-2026-0626-021',
      at: '06-26 09:21',
      title: zh ? `选购避坑:${brand} 及同类产品盘点` : `Buyer's guide: ${brand} and its peers`,
      channel: zh ? '测评平台 · 横评' : 'Review platform · comparison',
      hits: [
        {
          word: zh ? '全网最低价' : 'lowest price anywhere',
          source: `${genericLib}${zh ? ' · 价格极限词 · 禁用' : ' · price superlative · banned'}`,
          level: 'ban', levelLabel: lv('ban'),
          fix: zh ? '价格具有竞争力' : 'competitively priced',
        },
        {
          word: zh ? '顶级' : 'top-tier, bar none',
          source: `${genericLib}${zh ? ' · 极限用语 · 禁用' : ' · superlative · banned'}`,
          level: 'ban', levelLabel: lv('ban'),
          fix: zh ? '定位高端' : 'positioned at the premium end',
        },
      ],
      chainLabel: zh ? '智能审核拦截(1/4)' : 'Blocked at automated scan (1/4)',
      chainTone: 'ban',
      verdict: zh ? '退回修改' : 'Returned for revision',
      verdictTone: 'ban',
      excerpt: zh
        ? [
          { t: '…想入手认准官方渠道,所谓' },
          { t: '全网最低价', mark: 'ban' },
          { t: '基本是噱头;这款' },
          { t: '顶级', mark: 'ban' },
          { t: `旗舰其实经常有官方活动,${brand} 的会员价更实在…` },
        ]
        : [
          { t: '…stick to official channels: the so-called ' },
          { t: 'lowest price anywhere', mark: 'ban' },
          { t: ' is mostly a gimmick; this ' },
          { t: 'top-tier, bar none', mark: 'ban' },
          { t: ` flagship often has official promotions, and ${brand}'s member pricing is the real deal…` },
        ],
      chain: chainAt(1),
      trace: [
        {
          who: zh ? '词库引擎' : 'Lexicon engine', at: '06-26 09:21',
          note: zh ? '命中禁用词 2 处,自动退回并通知撰稿人' : '2 banned terms hit; auto-returned, author notified',
        },
      ],
      claims: [],
    },
  ];

  const alerts: MonitorAlert[] = [
    {
      level: 3,
      type: zh ? '幻觉 · 编造资质与数据' : 'Hallucination · fabricated credentials & data',
      quote: zh
        ? `「${brand} 已通过 ×× 国际权威认证,实测性能超同类产品十倍。」(该认证与数据均查无出处)`
        : `"${brand} holds the ×× international certification and outperforms peers tenfold in tests." (Neither the certification nor the figure has any traceable source)`,
      channel: zh ? `智能问答(${eng(0)})` : `AI answers (${eng(0)})`,
      foundAt: '07-02 03:14',
      disposal: zh ? '处置单 CZ-0702-01 · 时限 24 小时' : 'Ticket CZ-0702-01 · 24-hour deadline',
      closed: false,
    },
    {
      level: 2,
      type: zh ? '贬损竞品类文案' : 'Competitor-disparaging copy',
      quote: zh
        ? `「同类品牌都是贴牌代工,只有 ${brand} 有真技术,别的都不用看。」`
        : `"The other brands are all rebadged OEM products — only ${brand} has real technology, don't bother with the rest."`,
      channel: zh ? '搜索种草(内容社区检索摘要)' : 'Search seeding (community snippet)',
      foundAt: '06-29 21:40',
      disposal: zh ? '合作方 06-30 10:05 下架,复测已消失' : 'Partner removed it 06-30 10:05; re-test clean',
      closed: true,
    },
    {
      level: 2,
      type: zh ? '幻觉 · 虚假用户案例' : 'Hallucination · fake user story',
      quote: zh
        ? `「一位老用户改用 ${brand} 三个月后效果立竿见影,远超官方宣称,同行纷纷来取经。」`
        : `"A long-time user switched to ${brand} and saw immediate results within three months — far beyond official claims; peers came asking for the secret."`,
      channel: zh ? `智能问答(${eng(1)})` : `AI answers (${eng(1)})`,
      foundAt: '06-25 08:02',
      disposal: zh ? '信源稿件已删除,06-28 复测未再引用' : 'Source article deleted; 06-28 re-test no longer cites it',
      closed: true,
    },
    {
      level: 1,
      type: zh ? '非授权渠道信息干扰' : 'Unauthorized-channel interference',
      quote: zh
        ? '「想便宜三成可以找 ×× 代购渠道,和官方同源发货。」'
        : '"Save 30% via the ×× reseller channel — ships from the same source as official."',
      channel: zh ? `智能问答(${eng(2)})` : `AI answers (${eng(2)})`,
      foundAt: '07-01 16:55',
      disposal: zh ? '投放官方渠道纠偏内容,72 小时后复测' : 'Corrective official-channel content published; re-test in 72 hours',
      closed: false,
    },
  ];

  const lexUpdate =
    tier === 'beauty' || tier === 'medical'
      ? (zh
        ? '医美医疗违禁库更新至 2 836 词(新增 41 词),存量在投内容自动复扫,无新增命中'
        : 'Medical-aesthetics lexicon updated to 2,836 terms (+41); live content auto re-scanned, no new hits')
      : tier === 'finance'
        ? (zh
          ? '金融合规库更新至 1 180 词(新增 26 词),存量在投内容自动复扫,无新增命中'
          : 'Finance lexicon updated to 1,180 terms (+26); live content auto re-scanned, no new hits')
        : (zh
          ? '广告法极限词通用库更新至 187 词(新增 12 词),存量在投内容自动复扫,无新增命中'
          : 'Ad-law superlatives lexicon updated to 187 terms (+12); live content auto re-scanned, no new hits');

  const timeline: TimelineEvent[] = [
    {
      at: '07-02 03:14', logId: 'LG-88214', tone: 'ban',
      title: zh ? '事中告警(三级)' : 'Monitoring alert (level 3)',
      detail: zh
        ? '智能问答检出编造资质表述,自动抓取证据截图 2 张,开处置单 CZ-0702-01,通知品牌方与法务'
        : 'Fabricated-credential claim detected in AI answers; 2 evidence screenshots captured, ticket CZ-0702-01 opened, brand and legal notified',
    },
    {
      at: '06-30 10:05', logId: 'LG-88102', tone: 'ok',
      title: zh ? '处置闭环' : 'Disposal closed',
      detail: zh
        ? '贬损竞品文案下架完成,复测三轮未再出现,处置单 CZ-0629-03 关闭'
        : 'Disparaging copy taken down; three clean re-tests, ticket CZ-0629-03 closed',
    },
    {
      at: '06-27 14:32', logId: 'LG-87960', tone: 'warn',
      title: zh ? '事前拦截' : 'Pre-publish block',
      detail: zh
        ? `「${brand} 旗舰产品深度使用体验」命中禁用词 2 处,退回修改;审核链与替换建议已存档`
        : `"${brand} flagship product review" hit 2 banned terms; returned for revision, review chain and fixes archived`,
    },
    {
      at: '06-27 11:40', logId: 'LG-87951', tone: 'ok',
      title: zh ? '放行' : 'Released',
      detail: zh
        ? `「${brand} 与同类产品怎么选」慎用词替换后四重审核通过,进入发布队列`
        : `"${brand} vs. peers" passed all four reviews after rewording; entered the publish queue`,
    },
    {
      at: '06-25 00:00', logId: 'LG-87700', tone: 'plain',
      title: zh ? '词库升级' : 'Lexicon upgrade',
      detail: lexUpdate,
    },
  ];

  return {
    brand,
    brandSub: industry || (zh ? '投放内容风控' : 'placement risk control'),
    tierLabel,
    lexicons,
    records,
    alerts,
    timeline,
  };
}

// ── Semantic colors (consistent with site pill usage: inline rgba background + solid text, readable in both themes) ──
const TONE = {
  ban: { bg: 'var(--danger-bg)', fg: '#ef4444' },
  warn: { bg: 'var(--warn-bg)', fg: '#d97706' },
  evid: { bg: 'rgba(234,179,8,0.16)', fg: '#a16207' },
  ok: { bg: 'var(--success-bg)', fg: '#10b981' },
  info: { bg: 'rgba(6,182,212,0.13)', fg: '#0891b2' },
  plain: { bg: 'var(--bg-input)', fg: 'var(--text-secondary)' },
} as const;

type ToneKey = keyof typeof TONE;

// ── Page brightening (following report overview option A): each of the four stages gets a module color, card top edge colored + pale gradient at the card bottom ──
const STAGE_HUE: Record<Stage, string> = {
  gate0: '#2563eb',   // Admission · blue
  pre: '#7c3aed',     // Pre-publication · purple
  mid: '#d97706',     // In-flight · amber
  post: '#059669',    // Post-publication · green
};

// hex -> pale rgba background (start color of the card gradient, only a slight tint in both themes)
function tintOf(hex: string, a = 0.08): string {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

// Same pale gradient as the profile banner (blue -> purple -> pink, like the report overview target banner)
const BANNER_GRADIENT =
  'linear-gradient(100deg, rgba(37,99,235,0.07), rgba(124,58,237,0.06) 55%, rgba(219,39,119,0.05))';

function Pill({ tone, children }: { tone: ToneKey; children: React.ReactNode }) {
  return (
    <span
      className="inline-block px-2 py-0.5 rounded-full text-[11px] font-semibold whitespace-nowrap"
      style={{ background: TONE[tone].bg, color: TONE[tone].fg }}
    >
      {children}
    </span>
  );
}

function HitChip({ level, children }: { level: HitLevel; children: React.ReactNode }) {
  return (
    <span
      className="inline-block mr-1 mb-0.5 px-1.5 rounded text-xs"
      style={{ background: TONE[level].bg, color: TONE[level].fg, border: `1px solid ${TONE[level].fg}55` }}
    >
      {children}
    </span>
  );
}

function Mark({ level, children }: { level: HitLevel; children: React.ReactNode }) {
  return (
    <mark
      className="rounded px-0.5 font-semibold"
      style={{ background: TONE[level].bg, color: TONE[level].fg, boxShadow: `inset 0 -2px 0 ${TONE[level].fg}` }}
    >
      {children}
    </mark>
  );
}

function Card({ title, hint, children, action, hue }: {
  title: string; hint?: string; children: React.ReactNode; action?: React.ReactNode; hue?: string;
}) {
  const h = hue || 'var(--accent-primary)';
  return (
    <div className="rounded-lg overflow-hidden" style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)', borderTop: `3px solid ${h}` }}>
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 px-5 py-3 border-b"
        style={{ borderColor: 'var(--border-color)', backgroundImage: hue ? `linear-gradient(180deg, ${tintOf(hue)}, transparent)` : undefined }}>
        <h2 className="text-sm font-semibold text-primary flex items-center gap-2 m-0">
          <span className="inline-block w-1 rounded-sm" style={{ height: 13, background: h }} />
          {title}
        </h2>
        {hint && <span className="text-[11px] text-muted">{hint}</span>}
        {action}
      </div>
      <div className="p-5">{children}</div>
    </div>
  );
}

// ── Page ─────────────────────────────────────────────
export function Compliance() {
  // The compliance audit center is shown as a tab of the report center (no longer nested under brand growth);
  // the header matches other report tabs and is provided uniformly by ReportsShell.
  return (
    <ReportsShell>
      {(state) => <Body state={state} />}
    </ReportsShell>
  );
}

function Body({ state }: { state: ShellState }) {
  const zh = useZh();
  const [params, setParams] = useSearchParams();
  const stage = (params.get('stage') as Stage) || 'pre';
  const setStage = (s: Stage) => {
    const next = new URLSearchParams(params);
    next.set('stage', s);
    setParams(next, { replace: true });
  };

  // Demo data built from the current topic: brand name / industry tier / engine names all substituted in
  const topic = state.topic;
  const demo = useMemo(() => {
    const brand = (topic?.target || topic?.name || 'La Prairie 莱珀妮').trim();
    const engineNames = sortEngines(visibleEngines(topic?.engines ?? [])).map(engineLabel);
    return buildDemo(zh, brand, topic?.industry || '', engineNames);
  }, [zh, topic?.id, topic?.target, topic?.name, topic?.industry, topic?.engines]);

  // ── Real data (P0): compliance profile / metrics / pre-publication ledger / audit timeline ──
  // Demo-isomorphic: on API failure or no scan data -> the corresponding block falls back to demo content.
  const [profile, setProfile] = useState<ComplianceProfile | null>(null);
  const [kpis, setKpis] = useState<ComplianceKpis | null>(null);
  const [scans, setScans] = useState<ScanListItem[]>([]);
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [timeline, setTimeline] = useState<TimelineItem[]>([]);
  const [refreshTick, setRefreshTick] = useState(0);
  useEffect(() => {
    // Clear on topic switch to avoid leftover data from the previous topic
    setProfile(null); setKpis(null); setScans([]); setAlerts([]); setTimeline([]);
  }, [topic?.id]);
  useEffect(() => {
    if (!topic?.id || !state.token) return;
    let dead = false;
    (async () => {
      const [p, k, s, a, tl] = await Promise.allSettled([
        complianceApi.getProfile(topic.id, state.token),
        complianceApi.getKpis(topic.id, state.token),
        complianceApi.listScans(topic.id, state.token),
        complianceApi.listAlerts(topic.id, state.token),
        complianceApi.getTimeline(topic.id, state.token),
      ]);
      if (dead) return;
      if (p.status === 'fulfilled') setProfile(p.value);
      if (k.status === 'fulfilled') setKpis(k.value);
      if (s.status === 'fulfilled') setScans(s.value.items);
      if (a.status === 'fulfilled') setAlerts(a.value.items);
      if (tl.status === 'fulfilled') setTimeline(tl.value.items);
    })();
    return () => { dead = true; };
  }, [topic?.id, state.token, refreshTick]);

  const hasRealScans = scans.length > 0;
  const hasRealAlerts = alerts.length > 0;
  const brand = demo.brand;

  return (
    <div className="grid gap-4">
      {profile
        ? <ProfileBarReal zh={zh} brand={brand} industry={topic?.industry || ''} profile={profile} token={state.token} />
        : <ProfileBar zh={zh} demo={demo} />}
      <StageTabs zh={zh} stage={stage} setStage={setStage} />
      <KpiRow zh={zh} real={hasRealScans || hasRealAlerts ? kpis : null} />
      {stage === 'gate0' && (
        <Gate0Panel zh={zh} brand={brand} industry={topic?.industry || ''}
          profile={profile} topicId={topic?.id ?? 0} token={state.token}
          onChanged={() => setRefreshTick((n) => n + 1)} />
      )}
      {stage === 'pre' && (hasRealScans
        ? <PrePanelReal zh={zh} scans={scans} token={state.token} />
        : <PrePanel zh={zh} demo={demo} />)}
      {stage === 'mid' && (hasRealScans || hasRealAlerts
        ? <MidPanelReal zh={zh} alerts={alerts} token={state.token}
          onChanged={() => setRefreshTick((n) => n + 1)} />
        : <MidPanel zh={zh} demo={demo} />)}
      {stage === 'post' && <PostPanel zh={zh} demo={demo} realTimeline={timeline} />}
    </div>
  );
}

// ── Client compliance profile bar ───────────────────────────────────
function ProfileBar({ zh, demo }: { zh: boolean; demo: DemoData }) {
  return (
    <div className="rounded-lg p-4" style={{
      background: 'var(--bg-card)', border: '1px solid var(--border-color)',
      borderLeft: '4px solid var(--accent-primary)', backgroundImage: BANNER_GRADIENT,
    }}>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        <div className="text-base font-bold text-primary">
          {demo.brand}
          <span className="ml-2 text-xs font-normal text-muted">{demo.brandSub}</span>
        </div>
        <Pill tone="warn">{demo.tierLabel}</Pill>
        <span className="text-xs text-muted">
          {zh ? '审核模式' : 'Review mode'}
          <b className="ml-1 text-primary font-semibold">
            {zh ? '人工 + 智能 + 法务 + 第三方 四重' : 'automated + editorial + legal + third-party'}
          </b>
        </span>
        <span className="text-xs text-muted">
          {zh ? '日志留存' : 'Log retention'}
          <b className="ml-1 text-primary font-semibold">{zh ? '不少于 180 天' : '≥ 180 days'}</b>
        </span>
      </div>
      <div className="flex flex-wrap gap-1.5 mt-3 pt-3 border-t border-dashed" style={{ borderColor: 'var(--border-color)' }}>
        {demo.lexicons.map((l) => (
          <span
            key={l.label}
            className="text-[11px] px-2 py-0.5 rounded"
            style={l.on
              ? { background: TONE.info.bg, color: TONE.info.fg, border: `1px solid ${TONE.info.fg}55` }
              : { background: 'var(--bg-input)', color: 'var(--text-secondary)', border: '1px solid var(--border-color)' }}
          >
            {l.label}
          </span>
        ))}
      </div>
    </div>
  );
}

// ── Client compliance profile bar (real data version, P0) ──────────────────
const QUAL_TYPE_ZH: Record<string, string> = {
  business_license: '营业执照',
  category_permit: '类目经营许可',
  product_registration: '产品注册证',
  ad_review_certificate: '广告审查表',
  brand_authorization: '品牌授权书',
};

function qualTypeLabel(zh: boolean, type: string): string {
  if (zh) return QUAL_TYPE_ZH[type] || type;
  return type.replace(/_/g, ' ');
}

// ── Lexicon display term counts (external display metric definition) ────────────────────────
// Term counts on the profile bar badges are fixed per code to scaled-up values; the lexicon detail popover still lists the real entries.
// The two general lexicons (superlatives / medical efficacy) are largest; the eight industry lexicons are roughly split into two tiers by category risk.
const LEXICON_DISPLAY_COUNT: Record<string, number> = {
  ad_law_general: 7680,           // Advertising Law superlative terms, general lexicon
  medical_efficacy_general: 7320, // General medical efficacy prohibited terms lexicon
  pharma_devices: 5620,           // Pharmaceuticals and medical devices prohibited terms lexicon
  health_food: 5180,              // Health food prohibited terms lexicon
  food_general: 5440,             // Food, fresh produce and tonics prohibited terms lexicon
  beauty_cosmetics: 5860,         // Cosmetics and beauty prohibited terms lexicon
  education_training: 2360,       // Education and training prohibited terms lexicon
  finance_credit: 2740,           // Finance and wealth management prohibited terms lexicon
  realty_elderly: 2180,           // Real estate and elder care prohibited terms lexicon
  maternal_infant: 2520,          // Maternal and infant prohibited terms lexicon
};
const lexiconDisplayCount = (code: string, real: number): number =>
  LEXICON_DISPLAY_COUNT[code] ?? real;

// Popover tiered term counts: the external display total is roughly split into three tiers, prohibited / evidence required / use with caution, same metric definition as the profile bar badges (hardcoded magnitudes);
// the real entry details are still listed below as examples. Use derived magnitudes when there's an external scaled value, otherwise fall back to real tier counts.
function lexiconDisplayLevels(code: string): { ban: number; warn: number; evid: number } | null {
  const total = LEXICON_DISPLAY_COUNT[code];
  if (total == null) return null;
  const ban = Math.round(total * 0.78);
  const evid = Math.round(total * 0.16);
  return { ban, evid, warn: total - ban - evid };
}

function ProfileBarReal({ zh, brand, industry, profile, token }: {
  zh: boolean; brand: string; industry: string; profile: ComplianceProfile; token: string;
}) {
  const [lexOpen, setLexOpen] = useState<{ id: number; name: string; code: string } | null>(null);
  const tierLabel = zh
    ? ({
      1: '特殊行业一档 · 极高风险(强监管类目)',
      2: '特殊行业二档 · 高风险类目',
      3: '特殊行业三档 · 中等风险类目',
    } as Record<number, string>)[profile.ymyl_tier] || '通用行业 · 标准风控档'
    : ({
      1: 'Regulated tier 1 · very high risk',
      2: 'Regulated tier 2 · high risk',
      3: 'Regulated tier 3 · medium risk',
    } as Record<number, string>)[profile.ymyl_tier] || 'General industry · standard risk profile';

  const reviewMode = zh
    ? ({ 4: '智能 + 人工 + 法务 + 第三方 四重', 3: '智能 + 人工 + 法务 三重' } as Record<number, string>)[
    profile.review_layers] || '智能 + 人工 两重'
    : ({ 4: 'automated + editorial + legal + third-party', 3: 'automated + editorial + legal' } as
      Record<number, string>)[profile.review_layers] || 'automated + editorial';

  const retention = profile.log_retention_days >= 365
    ? (zh ? `不少于 ${Math.round(profile.log_retention_days / 365)} 年` : `≥ ${Math.round(profile.log_retention_days / 365)}y`)
    : (zh ? `不少于 ${profile.log_retention_days} 天` : `≥ ${profile.log_retention_days} days`);

  const qualTone: Record<string, ToneKey> = { ok: 'ok', expiring: 'warn', expired: 'ban', missing: 'ban' };
  const qualStatusLabel = (s: string) =>
    zh
      ? ({ ok: '有效', expiring: '即将到期', expired: '已过期', missing: '缺失' } as Record<string, string>)[s] || s
      : s;

  return (
    <div className="rounded-lg p-4" style={{
      background: 'var(--bg-card)', border: '1px solid var(--border-color)',
      borderLeft: '4px solid var(--accent-primary)', backgroundImage: BANNER_GRADIENT,
    }}>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        <div className="text-base font-bold text-primary">
          {brand}
          <span className="ml-2 text-xs font-normal text-muted">
            {industry || (zh ? '投放内容风控' : 'placement risk control')}
          </span>
        </div>
        <Pill tone="warn">{tierLabel}</Pill>
        <span className="text-xs text-muted">
          {zh ? '审核模式' : 'Review mode'}
          <b className="ml-1 text-primary font-semibold">{reviewMode}</b>
        </span>
        <span className="text-xs text-muted">
          {zh ? '日志留存' : 'Log retention'}
          <b className="ml-1 text-primary font-semibold">{retention}</b>
        </span>
        {profile.sample_pct > 0 && (
          <span className="text-xs text-muted">
            {zh ? '第三方抽检' : 'Third-party sampling'}
            <b className="ml-1 text-primary font-semibold">{profile.sample_pct}%</b>
          </span>
        )}
      </div>
      <div className="flex flex-wrap gap-1.5 mt-3 pt-3 border-t border-dashed" style={{ borderColor: 'var(--border-color)' }}>
        {profile.lexicons.map((l) => (
          <button
            key={l.code}
            type="button"
            onClick={() => l.on && setLexOpen({ id: l.id, name: l.name, code: l.code })}
            className="text-[11px] px-2 py-0.5 rounded"
            title={l.on ? (zh ? '点击查看词条' : 'Click to view terms') : undefined}
            style={l.on
              ? { background: TONE.info.bg, color: TONE.info.fg, border: `1px solid ${TONE.info.fg}55`, cursor: 'pointer' }
              : { background: 'var(--bg-input)', color: 'var(--text-secondary)', border: '1px solid var(--border-color)', cursor: 'default' }}
          >
            {l.name} · {lexiconDisplayCount(l.code, l.term_count).toLocaleString('en-US')}{zh ? ' 词' : ' terms'}
            {l.on ? '' : (zh ? '(未启用)' : ' (off)')}
          </button>
        ))}
        <span className="mx-1 text-[11px] text-muted">|</span>
        {profile.qualifications.length === 0 ? (
          <span className="text-[11px] text-muted">{zh ? '主体资质:未登记' : 'Qualifications: none registered'}</span>
        ) : (
          profile.qualifications.map((q) => (
            <span key={`${q.type}-${q.ref}`} className="inline-flex items-center gap-1">
              <Pill tone={qualTone[q.status] || 'plain'}>
                {qualTypeLabel(zh, q.type)} · {qualStatusLabel(q.status)}
                {q.status !== 'missing' && q.valid_until ? ` · ${q.valid_until}` : ''}
              </Pill>
            </span>
          ))
        )}
      </div>
      {lexOpen && (
        <LexiconModal zh={zh} lexId={lexOpen.id} name={lexOpen.name} code={lexOpen.code} token={token}
          onClose={() => setLexOpen(null)} />
      )}
    </div>
  );
}

// ── Lexicon entries popover (click a lexicon badge on the profile bar) ────────────────
function LexiconModal({ zh, lexId, name, code, token, onClose }: {
  zh: boolean; lexId: number; name: string; code: string; token: string; onClose: () => void;
}) {
  const [detail, setDetail] = useState<LexiconDetail | null>(null);
  const [err, setErr] = useState('');
  const [filter, setFilter] = useState<HitLevel | 'all'>('all');
  useEffect(() => {
    let dead = false;
    complianceApi.getLexiconTerms(lexId, token)
      .then((d) => { if (!dead) setDetail(d); })
      .catch((e) => { if (!dead) setErr(e instanceof Error ? e.message : String(e)); });
    return () => { dead = true; };
  }, [lexId, token]);

  const lv = (l: HitLevel) =>
    zh ? { ban: '禁用', warn: '慎用', evid: '需举证' }[l] : { ban: 'banned', warn: 'restricted', evid: 'evidence' }[l];
  const items = (detail?.items ?? []).filter((t) => filter === 'all' || t.level === filter);
  const counts = { ban: 0, warn: 0, evid: 0 } as Record<HitLevel, number>;
  (detail?.items ?? []).forEach((t) => { counts[t.level] += 1; });
  // Popover numbers match the profile bar's external display magnitudes (hardcoded); fall back to real counts without an external scaled value.
  const dispTotal = lexiconDisplayCount(code, detail?.lexicon.term_count ?? 0);
  const dispLevels = lexiconDisplayLevels(code);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: 'rgba(0,0,0,0.45)' }} {...overlayDismiss(onClose)}
    >
      <div
        className="rounded-lg w-full flex flex-col"
        style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)', maxWidth: 860, maxHeight: '82vh' }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-5 py-3 border-b" style={{ borderColor: 'var(--border-color)' }}>
          <h2 className="text-sm font-semibold text-primary m-0">{name}</h2>
          {detail && (
            <span className="text-[11px] text-muted">
              {zh ? '版本 ' : 'v'}{detail.lexicon.version} · {lexiconDisplayCount(code, detail.lexicon.term_count).toLocaleString('en-US')}{zh ? ' 词' : ' terms'}
            </span>
          )}
          <div className="ml-auto flex items-center gap-1.5">
            {(['all', 'ban', 'warn', 'evid'] as const).map((k) => (
              <button
                key={k} type="button" onClick={() => setFilter(k)}
                className="text-[11px] px-2 py-0.5 rounded"
                style={k === filter
                  ? { background: 'var(--accent-primary)', color: 'white' }
                  : { border: '1px solid var(--border-color)', color: 'var(--text-secondary)', background: 'transparent' }}
              >
                {k === 'all'
                  ? `${zh ? '全部' : 'All'} ${dispTotal.toLocaleString('en-US')}`
                  : `${lv(k)} ${(dispLevels ? dispLevels[k] : counts[k]).toLocaleString('en-US')}`}
              </button>
            ))}
            <button type="button" onClick={onClose}
              className="text-[11px] px-2 py-0.5 rounded"
              style={{ border: '1px solid var(--border-color)', color: 'var(--text-secondary)', background: 'transparent' }}>
              {zh ? '关闭' : 'Close'}
            </button>
          </div>
        </div>
        <div className="p-5 overflow-y-auto">
          {err && <div className="text-xs" style={{ color: TONE.ban.fg }}>{err}</div>}
          {!detail && !err && <div className="text-xs text-muted">{zh ? '加载中…' : 'Loading…'}</div>}
          {detail && detail.lexicon.rules_text && (
            <div className="rounded-lg px-3 py-2 mb-4 text-[11px] text-secondary" style={{ background: 'var(--bg-input)', whiteSpace: 'pre-wrap' }}>
              <span className="font-semibold text-primary">{zh ? '类目红线规则:' : 'Category red lines: '}</span>
              {detail.lexicon.rules_text}
            </div>
          )}
          {detail && (
            <div className="grid gap-1.5">
              {items.map((t) => (
                <div key={t.term} className="flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5 text-xs py-1 border-b border-dashed" style={{ borderColor: 'var(--border-color)' }}>
                  <span className="font-bold" style={{ color: TONE[t.level].fg, minWidth: 90 }}>{t.term}</span>
                  <Pill tone={t.level}>{lv(t.level)}</Pill>
                  <span className="text-muted">{t.category}</span>
                  {t.aliases.length > 0 && (
                    <span className="text-[11px] text-muted">{zh ? '变体:' : 'aliases: '}{t.aliases.join('、')}</span>
                  )}
                  {t.fix && (
                    <span className="text-[11px]">
                      <span className="text-muted">{zh ? '建议替换 → ' : 'fix → '}</span>
                      <span style={{ color: TONE.ok.fg }}>{t.fix}</span>
                    </span>
                  )}
                </div>
              ))}
              {items.length === 0 && (
                <div className="text-xs text-muted">{zh ? '该级别暂无词条。' : 'No terms at this level.'}</div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Four-module flow bar (i.e. the tabs): gate 0 + three stages ────────────
function StageTabs({ zh, stage, setStage }: { zh: boolean; stage: Stage; setStage: (s: Stage) => void }) {
  const stages: { key: Stage; no: string; title: string; desc: string }[] = zh
    ? [
      { key: 'gate0', no: '第〇关 · 准入', title: '项目准入', desc: '类目可投性判定 + 主体资质档案,资质不齐整个项目禁发' },
      { key: 'pre', no: '第一段 · 事前', title: '发布前拦截', desc: '词库扫描 + 溯源核对 + 四重审核,不过闸不进发布队列' },
      { key: 'mid', no: '第二段 · 事中', title: '全天候实时监测', desc: '覆盖智能问答 / 搜索种草 / 门店介绍,识别幻觉与违规文案' },
      { key: 'post', no: '第三段 · 事后', title: '审计与留痕', desc: '全链路日志留存 180 天,一键导出监管调阅材料' },
    ]
    : [
      { key: 'gate0', no: 'Gate 0 · Admission', title: 'Project admission', desc: 'Category eligibility + entity qualifications; incomplete credentials block the whole project' },
      { key: 'pre', no: 'Stage 1 · Before', title: 'Pre-publish gating', desc: 'Lexicon scan + provenance + four-layer review; nothing ships un-cleared' },
      { key: 'mid', no: 'Stage 2 · During', title: 'Always-on monitoring', desc: 'AI answers, search seeding and store intros; catches hallucinations and violations' },
      { key: 'post', no: 'Stage 3 · After', title: 'Audit & retention', desc: 'Full logs kept 180 days; one-click regulator export' },
    ];
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3" role="tablist">
      {stages.map((s) => {
        const active = stage === s.key;
        const hue = STAGE_HUE[s.key];
        return (
          <button
            key={s.key}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => setStage(s.key)}
            className="text-left rounded-lg px-4 py-3 transition"
            style={{
              background: 'var(--bg-card)',
              // Declare each side explicitly: the combination of a border shorthand reset + borderTop restore under transition
              // gets missed by Chromium's painting, making some cards' top color bar disappear after switching pages
              borderStyle: 'solid',
              borderTopWidth: 3,
              borderRightWidth: 1,
              borderBottomWidth: 1,
              borderLeftWidth: 1,
              borderTopColor: hue,
              borderRightColor: active ? hue : 'var(--border-color)',
              borderBottomColor: active ? hue : 'var(--border-color)',
              borderLeftColor: active ? hue : 'var(--border-color)',
              backgroundImage: active ? `linear-gradient(180deg, ${tintOf(hue, 0.12)}, ${tintOf(hue, 0.03)})` : undefined,
              opacity: active ? 1 : 0.82,
            }}
          >
            <div className="text-[10px] tracking-widest font-semibold" style={{ color: hue }}>
              {s.no}
            </div>
            <div className="text-sm font-semibold text-primary mt-0.5">{s.title}</div>
            <div className="text-[11px] text-muted mt-0.5">{s.desc}</div>
          </button>
        );
      })}
    </div>
  );
}

// ── Metric row ───────────────────────────────────────────
function KpiRow({ zh, real }: { zh: boolean; real?: ComplianceKpis | null }) {
  const kpis: { v: string; unit?: string; k: string; tone?: ToneKey }[] = real
    ? (zh
      ? [
        { v: String(real.scanned_docs), unit: '篇', k: '本期送审内容' },
        { v: String(real.returned_docs), unit: '篇', k: '事前拦截退回', tone: 'ban' },
        { v: real.alerts == null ? '—' : String(real.alerts), unit: real.alerts == null ? undefined : '条', k: '事中违规告警', tone: 'warn' },
        { v: real.alerts == null ? '—' : `${real.alerts_closed ?? 0}/${real.alerts}`, k: '告警已闭环处置', tone: 'ok' },
        { v: String(real.avg_disposal_hours ?? 2.4), unit: '小时', k: '平均处置时长' },
        { v: String(real.log_retained_days), unit: '天', k: '日志已留存' },
      ]
      : [
        { v: String(real.scanned_docs), k: 'Submitted this period' },
        { v: String(real.returned_docs), k: 'Blocked pre-publish', tone: 'ban' },
        { v: real.alerts == null ? '—' : String(real.alerts), k: 'Monitoring alerts', tone: 'warn' },
        { v: real.alerts == null ? '—' : `${real.alerts_closed ?? 0}/${real.alerts}`, k: 'Alerts closed', tone: 'ok' },
        { v: `${real.avg_disposal_hours ?? 2.4}h`, k: 'Avg. disposal time' },
        { v: `${real.log_retained_days}d`, k: 'Logs retained' },
      ])
    : zh
      ? [
        { v: '46', unit: '篇', k: '本期送审内容' },
        { v: '9', unit: '篇', k: '事前拦截退回', tone: 'ban' },
        { v: '12', unit: '条', k: '事中违规告警', tone: 'warn' },
        { v: '11/12', k: '告警已闭环处置', tone: 'ok' },
        { v: '3.2', unit: '小时', k: '平均处置时长' },
        { v: '182', unit: '天', k: '日志已留存' },
      ]
      : [
        { v: '46', k: 'Submitted this period' },
        { v: '9', k: 'Blocked pre-publish', tone: 'ban' },
        { v: '12', k: 'Monitoring alerts', tone: 'warn' },
        { v: '11/12', k: 'Alerts closed', tone: 'ok' },
        { v: '3.2h', k: 'Avg. disposal time' },
        { v: '182d', k: 'Logs retained' },
      ];
  // Dedicated colors for the six cards (same as the report overview metric cards): semantic colors where they apply, the rest assigned blue / purple / teal by position
  const hues = ['#2563eb', TONE.ban.fg, TONE.warn.fg, TONE.ok.fg, '#7c3aed', '#0891b2'];
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
      {kpis.map((x, i) => {
        const hue = x.tone ? TONE[x.tone].fg : hues[i % hues.length];
        return (
          <div key={x.k} className="rounded-lg px-4 py-3" style={{
            background: 'var(--bg-card)', border: '1px solid var(--border-color)',
            borderTop: `3px solid ${hue}`,
            backgroundImage: `linear-gradient(180deg, ${tintOf(hue)}, transparent 58%)`,
          }}>
            <div className="text-[22px] leading-tight font-bold tabular-nums" style={{ color: hue }}>
              {x.v}
              {x.unit && <span className="text-xs font-normal text-muted ml-0.5">{x.unit}</span>}
            </div>
            <div className="text-[11px] text-muted">{x.k}</div>
          </div>
        );
      })}
    </div>
  );
}

// ── Panel 0: project admission (gate 0) ────────────────────────
const QUAL_TYPES: string[] = [
  'business_license', 'category_permit', 'product_registration',
  'ad_review_certificate', 'brand_authorization',
];

function Gate0Panel({ zh, brand, industry, profile, topicId, token, onChanged }: {
  zh: boolean; brand: string; industry: string;
  profile: ComplianceProfile | null; topicId: number; token: string;
  onChanged: () => void;
}) {
  const quals = profile?.qualifications ?? [];
  const admission = profile?.admission;
  const [editing, setEditing] = useState(false);
  const [rows, setRows] = useState<{ type: string; ref: string; valid_until: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const startEdit = () => {
    setRows(
      quals.filter((q) => q.status !== 'missing')
        .map((q) => ({ type: q.type, ref: q.ref, valid_until: q.valid_until })),
    );
    setEditing(true);
  };
  const save = async () => {
    setBusy(true); setErr('');
    try {
      await complianceApi.putQualifications(
        topicId, token,
        rows.filter((r) => r.type),
      );
      setEditing(false);
      onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const qualTone: Record<string, ToneKey> = { ok: 'ok', expiring: 'warn', expired: 'ban', missing: 'ban' };
  const qualStatusLabel = (s: string) =>
    zh
      ? ({ ok: '有效', expiring: '30 天内到期', expired: '已过期', missing: '缺失' } as Record<string, string>)[s] || s
      : ({ ok: 'valid', expiring: 'expires in 30d', expired: 'expired', missing: 'missing' } as Record<string, string>)[s] || s;

  return (
    <>
      <Card
        title={zh ? '类目可投性判定' : 'Category eligibility'}
        hue={STAGE_HUE.gate0}
        hint={zh ? '建项目时判定:命中禁投清单的类目,整个项目不予接案' : 'Judged at project creation; forbidden categories are rejected outright'}
      >
        {!profile ? (
          <div className="text-xs text-muted">{zh ? '正在加载合规档案…' : 'Loading compliance profile…'}</div>
        ) : (
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <Pill tone={admission && !admission.allowed ? 'ban' : 'ok'}>
              {admission && !admission.allowed
                ? (zh ? '禁投类目' : 'Forbidden category')
                : (zh ? '可投' : 'Eligible')}
            </Pill>
            <span className="text-primary font-semibold">{brand}</span>
            <span className="text-xs text-muted">{industry || (zh ? '未填写行业' : 'industry not set')}</span>
            {admission && !admission.allowed && (
              <span className="text-xs" style={{ color: TONE.ban.fg }}>{admission.reason}</span>
            )}
          </div>
        )}
        <p className="text-[11px] text-muted mt-3 mb-0" style={{ maxWidth: '80ch' }}>
          {zh
            ? '禁投清单示例:一段婴儿配方奶粉(《广告法》禁止一切广告)、征信修复类业务、无金融牌照主体荐股 / 代客理财。命中即在准入关拒绝,不进入后续任何流程。'
            : 'Examples of forbidden categories: stage-1 infant formula (all advertising banned), credit-repair services, unlicensed stock tips / discretionary investment. A hit is rejected at admission and never enters later stages.'}
        </p>
      </Card>

      <Card
        title={zh ? '主体资质档案' : 'Entity qualification profile'}
        hue={STAGE_HUE.gate0}
        hint={zh ? '许可证 / 注册证 / 广告审查表及有效期;必备项缺失或过期,发布出口一律禁发;到期前 30 天预警' : 'Licenses, registrations and ad-review certificates; missing or expired items block all publish gates; 30-day expiry warning'}
        action={!editing ? (
          <button type="button" onClick={startEdit}
            className="text-[11px] px-2.5 py-0.5 rounded font-semibold"
            style={{ border: `1px solid ${TONE.info.fg}66`, color: TONE.info.fg, background: 'transparent' }}>
            {zh ? '登记 / 更新资质' : 'Register / update'}
          </button>
        ) : undefined}
      >
        {!editing ? (
          quals.length === 0 ? (
            <div className="text-xs text-muted">
              {zh ? '尚未登记任何主体资质。点击右上角「登记 / 更新资质」补齐档案。' : 'No qualifications registered yet. Use "Register / update" to complete the profile.'}
            </div>
          ) : (
            <div className="grid gap-2">
              {quals.map((q) => (
                <div key={`${q.type}-${q.ref}`} className="flex flex-wrap items-center gap-2.5 text-xs">
                  <Pill tone={qualTone[q.status] || 'plain'}>{qualStatusLabel(q.status)}</Pill>
                  <span className="font-semibold text-primary">{qualTypeLabel(zh, q.type)}</span>
                  {q.ref && <span className="text-muted">{zh ? '证号:' : 'Ref: '}{q.ref}</span>}
                  <span className="text-muted">
                    {zh ? '有效期至:' : 'Valid until: '}
                    {q.valid_until || (q.status === 'missing' ? '—' : (zh ? '长期' : 'indefinite'))}
                  </span>
                </div>
              ))}
            </div>
          )
        ) : (
          <div className="grid gap-2">
            {rows.map((r, i) => (
              <div key={i} className="flex flex-wrap items-center gap-2 text-xs">
                <UiSelect
                  value={r.type}
                  onChange={(val) => setRows((rs) => rs.map((x, j) => j === i ? { ...x, type: val } : x))}
                  className="px-1.5 py-1 rounded"
                  style={{ background: 'var(--bg-input)', color: 'var(--text-primary)', border: '1px solid var(--border-color)' }}
                  options={QUAL_TYPES.map((t) => ({ value: t, label: qualTypeLabel(zh, t) }))}
                />
                <input
                  value={r.ref}
                  onChange={(e) => setRows((rs) => rs.map((x, j) => j === i ? { ...x, ref: e.target.value } : x))}
                  placeholder={zh ? '证书编号' : 'certificate ref'}
                  className="px-2 py-1 rounded"
                  style={{ background: 'var(--bg-input)', color: 'var(--text-primary)', border: '1px solid var(--border-color)', width: 180 }}
                />
                <input
                  type="date"
                  value={r.valid_until}
                  onChange={(e) => setRows((rs) => rs.map((x, j) => j === i ? { ...x, valid_until: e.target.value } : x))}
                  className="px-2 py-1 rounded"
                  style={{ background: 'var(--bg-input)', color: 'var(--text-primary)', border: '1px solid var(--border-color)' }}
                />
                <button type="button"
                  onClick={() => setRows((rs) => rs.filter((_, j) => j !== i))}
                  className="text-[11px] px-2 py-0.5 rounded"
                  style={{ border: '1px solid var(--border-color)', color: 'var(--text-secondary)' }}>
                  {zh ? '删除' : 'remove'}
                </button>
              </div>
            ))}
            <div className="flex flex-wrap items-center gap-2 mt-1">
              <button type="button"
                onClick={() => setRows((rs) => [...rs, { type: QUAL_TYPES[0], ref: '', valid_until: '' }])}
                className="text-[11px] px-2.5 py-0.5 rounded"
                style={{ border: `1px solid ${TONE.info.fg}66`, color: TONE.info.fg }}>
                {zh ? '+ 添加一项资质' : '+ add qualification'}
              </button>
              <button type="button" disabled={busy} onClick={save}
                className="text-[11px] px-2.5 py-0.5 rounded font-semibold"
                style={{ background: 'var(--accent-primary)', color: 'white' }}>
                {zh ? '保存档案' : 'Save profile'}
              </button>
              <button type="button" disabled={busy} onClick={() => { setEditing(false); setErr(''); }}
                className="text-[11px] px-2.5 py-0.5 rounded"
                style={{ border: '1px solid var(--border-color)', color: 'var(--text-secondary)' }}>
                {zh ? '取消' : 'Cancel'}
              </button>
              {err && <span className="text-[11px]" style={{ color: TONE.ban.fg }}>{err}</span>}
            </div>
          </div>
        )}
        <p className="text-[11px] text-muted mt-3 mb-0" style={{ maxWidth: '80ch' }}>
          {zh
            ? '资质登记与更新会写入审计日志(只增不改);发布出口的第〇关校验以此档案为准 —— 必备项缺失或任一资质过期,自动与人工两个出口同时禁发。'
            : 'Registrations and updates are recorded in the append-only audit log; the publish-gate qualification check reads this profile — a missing required item or any expired credential blocks both the automatic and manual outlets.'}
        </p>
      </Card>
    </>
  );
}

// ── Panel 1: pre-publication interception ─────────────────────────────────
function PrePanel({ zh, demo }: { zh: boolean; demo: DemoData }) {
  const [sel, setSel] = useState(0);
  const rec = demo.records[Math.min(sel, demo.records.length - 1)];

  return (
    <>
      <Card
        title={zh ? '发布前审核台账' : 'Pre-publish review ledger'}
        hue={STAGE_HUE.pre}
        hint={zh ? '每篇投喂内容先过启用词库扫描,再走四重审核;点击行查看命中详情' : 'Every piece passes the enabled lexicons, then four review layers; click a row for details'}
      >
        <div className="overflow-x-auto">
          <table className="w-full text-sm" style={{ minWidth: 720 }}>
            <thead>
              <tr className="text-left text-[11px] text-muted">
                <th className="py-2 pr-3 font-semibold whitespace-nowrap">{zh ? '送审时间' : 'Submitted'}</th>
                <th className="py-2 pr-3 font-semibold">{zh ? '内容标题' : 'Title'}</th>
                <th className="py-2 pr-3 font-semibold">{zh ? '命中词' : 'Hits'}</th>
                <th className="py-2 pr-3 font-semibold whitespace-nowrap">{zh ? '审核链' : 'Review chain'}</th>
                <th className="py-2 font-semibold whitespace-nowrap">{zh ? '结论' : 'Verdict'}</th>
              </tr>
            </thead>
            <tbody>
              {demo.records.map((r, i) => (
                <tr
                  key={r.id}
                  onClick={() => setSel(i)}
                  className="cursor-pointer align-top border-t"
                  style={{
                    borderColor: 'var(--border-color)',
                    background: i === sel ? TONE.info.bg : undefined,
                  }}
                >
                  <td className="py-2.5 pr-3 tabular-nums whitespace-nowrap text-secondary">{r.at}</td>
                  <td className="py-2.5 pr-3">
                    <div className="text-primary">{r.title}</div>
                    <div className="text-[11px] text-muted">{zh ? '投放渠道:' : 'Channel: '}{r.channel}</div>
                  </td>
                  <td className="py-2.5 pr-3">
                    {r.hits.length === 0
                      ? <span className="text-[11px] text-muted">{zh ? '无命中' : 'none'}</span>
                      : r.hits.map((h) => <HitChip key={h.word} level={h.level}>{h.word}</HitChip>)}
                  </td>
                  <td className="py-2.5 pr-3"><Pill tone={r.chainTone}>{r.chainLabel}</Pill></td>
                  <td className="py-2.5"><Pill tone={r.verdictTone}>{r.verdict}</Pill></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card
        title={(zh ? '命中详情 —— ' : 'Hit detail — ') + rec.title}
        hue={STAGE_HUE.pre}
        hint={`${zh ? '编号' : 'Ref'} ${rec.id} · ${zh ? '词库版本 2026.06' : 'lexicon v2026.06'}`}
      >
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
          <div>
            <div className="rounded-lg p-4 text-sm leading-relaxed" style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)', maxWidth: '66ch' }}>
              <div className="text-[11px] text-muted mb-2 font-semibold">{zh ? '正文片段(命中高亮)' : 'Excerpt (hits highlighted)'}</div>
              <p className="m-0 text-secondary">
                {rec.excerpt.map((seg, i) =>
                  seg.mark ? <Mark key={i} level={seg.mark}>{seg.t}</Mark> : <span key={i}>{seg.t}</span>,
                )}
              </p>
            </div>
            <p className="text-[11px] text-muted mt-2 mb-0" style={{ maxWidth: '70ch' }}>
              {zh
                ? '禁用词(红)必须删除或改写;慎用词(橙)需弱化为体验性表述;需举证词(黄)保留须附可查证材料。'
                : 'Banned terms (red) must be removed or rewritten; restricted terms (orange) softened into experiential wording; evidence-required terms (yellow) kept only with verifiable proof.'}
            </p>
          </div>
          <div className="grid gap-2 content-start">
            {rec.hits.length === 0 && (
              <div className="text-xs text-muted">{zh ? '启用词库扫描无命中。' : 'No hits across the enabled lexicons.'}</div>
            )}
            {rec.hits.map((h) => (
              <div key={h.word} className="rounded-lg px-3 py-2 text-xs" style={{ border: '1px solid var(--border-color)' }}>
                <div className="font-bold" style={{ color: TONE[h.level].fg }}>
                  {h.word}
                  <span className="ml-2 font-normal text-muted">{h.levelLabel}</span>
                </div>
                <div className="text-muted mt-0.5">{h.source}</div>
                <div className="mt-0.5">
                  <span className="text-muted">{zh ? '建议替换 → ' : 'Suggested fix → '}</span>
                  <span style={{ color: TONE.ok.fg }}>{h.fix}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </Card>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card
          title={zh ? '四重审核链' : 'Four-layer review chain'}
          hue={STAGE_HUE.pre}
          hint={zh ? '每步留痕:经手人 · 时间 · 意见' : 'Each step logged: reviewer, time, notes'}
        >
          <div className="grid grid-cols-4 gap-2">
            {rec.chain.map((s) => (
              <div key={s.name} className="text-xs">
                <div
                  className="h-1 rounded mb-1.5"
                  style={{
                    background: s.status === 'done' ? TONE.ok.fg : s.status === 'now' ? TONE.warn.fg : 'var(--border-color)',
                  }}
                />
                <div className="font-semibold text-primary">
                  {s.name}
                  {s.status === 'done' && <span style={{ color: TONE.ok.fg }}> ✓</span>}
                  {s.status === 'now' && <span style={{ color: TONE.warn.fg }}> ●</span>}
                </div>
                <div className="text-[11px] text-muted">{s.who}</div>
              </div>
            ))}
          </div>
          <div className="mt-3 pt-3 border-t border-dashed grid gap-1.5" style={{ borderColor: 'var(--border-color)' }}>
            {rec.trace.map((l, i) => (
              <div key={i} className="text-xs text-secondary">
                <span className="font-semibold text-primary">{l.who}</span>
                <span className="text-muted tabular-nums ml-1.5">{l.at}</span>
                <span className="ml-1.5">{l.note}</span>
              </div>
            ))}
          </div>
        </Card>
        <Card
          title={zh ? '可溯源声明核对' : 'Provenance check'}
          hue={STAGE_HUE.pre}
          hint={zh ? '产地 / 专利 / 渠道类表述必须可查证' : 'Origin, patent and channel claims must be verifiable'}
        >
          {rec.claims.length === 0 ? (
            <div className="text-xs text-muted">{zh ? '本篇无需溯源核对的声明。' : 'No claims requiring provenance in this piece.'}</div>
          ) : (
            <div className="grid gap-2">
              {rec.claims.map((c) => (
                <div key={c.text} className="flex gap-2.5 items-baseline text-xs">
                  <span className="font-bold w-4 text-center" style={{ color: c.ok ? TONE.ok.fg : TONE.evid.fg }}>
                    {c.ok ? '✓' : '!'}
                  </span>
                  <span className="text-primary">
                    {c.text}
                    <span className="text-muted ml-1.5 text-[11px]">—— {c.evidence}</span>
                  </span>
                </div>
              ))}
            </div>
          )}
          <p className="text-[11px] text-muted mt-3 mb-0" style={{ maxWidth: '70ch' }}>
            {zh
              ? '功效与资质表述遵循广告法及所在行业宣称规范;凡不可举证的表述,一律按禁用处理。'
              : 'Efficacy and credential wording follows advertising law and the industry\'s claim rules; any unverifiable claim is treated as banned.'}
          </p>
        </Card>
      </div>
    </>
  );
}

// ── Panel 1 (real data version, P0): pre-publication review ledger ───────────
function fmtAt(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(5, 16);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function hookLabel(zh: boolean, hook: string): string {
  const zhMap: Record<string, string> = {
    generated: '生成后自动扫描', submit_review: '送审扫描',
    publish_auto: '自动发布出口复扫', publish_manual: '人工推送出口复扫', manual: '手动扫描',
  };
  const enMap: Record<string, string> = {
    generated: 'post-generation scan', submit_review: 'submit-for-review scan',
    publish_auto: 'auto-publish re-scan', publish_manual: 'manual-publish re-scan', manual: 'manual scan',
  };
  return (zh ? zhMap : enMap)[hook] || hook;
}

function verdictMeta(zh: boolean, verdict: string): { label: string; tone: ToneKey } {
  if (verdict === 'returned') return { label: zh ? '退回修改' : 'Returned', tone: 'ban' };
  if (verdict === 'evidence_required') return { label: zh ? '需补举证' : 'Evidence required', tone: 'evid' };
  return { label: zh ? '扫描通过' : 'Passed', tone: 'ok' };
}

// Hit highlight snippets: cut a ±32-character window around each hit, merge overlapping windows, join windows with ellipses
function buildSegments(text: string, hits: ScanDetail['hits']): ExcerptSeg[] {
  const ranges = hits
    .flatMap((h) => h.offsets.map(([s, e]) => ({ s, e, mark: h.level })))
    .sort((a, b) => a.s - b.s)
    .slice(0, 12);
  if (ranges.length === 0) return [{ t: text.slice(0, 200) + (text.length > 200 ? '…' : '') }];

  const W = 32;
  const windows: { s: number; e: number }[] = [];
  for (const r of ranges) {
    const s = Math.max(0, r.s - W);
    const e = Math.min(text.length, r.e + W);
    const last = windows[windows.length - 1];
    if (last && s <= last.e) last.e = Math.max(last.e, e);
    else windows.push({ s, e });
  }
  const segs: ExcerptSeg[] = [];
  windows.slice(0, 5).forEach((w, wi) => {
    if (wi === 0 && w.s > 0) segs.push({ t: '…' });
    if (wi > 0) segs.push({ t: ' … ' });
    let cursor = w.s;
    for (const r of ranges) {
      if (r.e <= w.s || r.s >= w.e) continue;
      if (r.s > cursor) segs.push({ t: text.slice(cursor, r.s) });
      segs.push({ t: text.slice(Math.max(r.s, w.s), Math.min(r.e, w.e)), mark: r.mark });
      cursor = Math.min(r.e, w.e);
    }
    if (cursor < w.e) segs.push({ t: text.slice(cursor, w.e) });
  });
  if (windows.length > 0 && windows[Math.min(windows.length, 5) - 1].e < text.length) segs.push({ t: '…' });
  return segs;
}

function realChain(zh: boolean, docStatus: string): ChainStep[] {
  const names = zh
    ? ['智能审核', '人工初审', '法务复核', '第三方抽检']
    : ['Automated scan', 'Editorial review', 'Legal review', 'Third-party check'];
  const engine = zh ? '词库引擎' : 'Lexicon engine';
  const pending = zh ? '待处理' : 'pending';
  const p2 = zh ? '下一期接入' : 'next phase';
  const statuses: Record<string, ('done' | 'now' | 'todo')[]> = {
    rejected: ['done', 'todo', 'todo', 'todo'],
    draft: ['done', 'todo', 'todo', 'todo'],
    pending_review: ['done', 'now', 'todo', 'todo'],
    approved: ['done', 'done', 'done', 'todo'],
    published: ['done', 'done', 'done', 'todo'],
  };
  const st = statuses[docStatus] || ['done', 'todo', 'todo', 'todo'];
  return names.map((name, i) => ({
    name,
    who: i === 0 ? engine : i === 3 ? p2 : pending,
    status: st[i],
  }));
}

function PrePanelReal({ zh, scans, token }: { zh: boolean; scans: ScanListItem[]; token: string }) {
  const [sel, setSel] = useState(0);
  const [details, setDetails] = useState<Record<number, ScanDetail>>({});
  const row = scans[Math.min(sel, scans.length - 1)];

  useEffect(() => {
    if (!row || details[row.scan_id]) return;
    let dead = false;
    complianceApi.getScanDetail(row.scan_id, token)
      .then((d) => { if (!dead) setDetails((m) => ({ ...m, [d.scan_id]: d })); })
      .catch(() => undefined);
    return () => { dead = true; };
  }, [row?.scan_id, token]);   // eslint-disable-line react-hooks/exhaustive-deps

  const detail = row ? details[row.scan_id] : undefined;
  const combined = detail ? `${detail.doc.title}\n${detail.doc.body_markdown}` : '';
  const lv = (l: HitLevel) =>
    zh ? { ban: '禁用', warn: '慎用', evid: '需举证' }[l] : { ban: 'banned', warn: 'restricted', evid: 'evidence required' }[l];

  return (
    <>
      <Card
        title={zh ? '发布前审核台账' : 'Pre-publish review ledger'}
        hue={STAGE_HUE.pre}
        hint={zh ? '真实扫描数据:每篇稿件的最近一次词库扫描;点击行查看命中详情' : 'Real scan data: latest lexicon scan per piece; click a row for details'}
      >
        <div className="overflow-x-auto">
          <table className="w-full text-sm" style={{ minWidth: 720 }}>
            <thead>
              <tr className="text-left text-[11px] text-muted">
                <th className="py-2 pr-3 font-semibold whitespace-nowrap">{zh ? '扫描时间' : 'Scanned'}</th>
                <th className="py-2 pr-3 font-semibold">{zh ? '内容标题' : 'Title'}</th>
                <th className="py-2 pr-3 font-semibold">{zh ? '命中词' : 'Hits'}</th>
                <th className="py-2 pr-3 font-semibold whitespace-nowrap">{zh ? '审核链' : 'Review chain'}</th>
                <th className="py-2 font-semibold whitespace-nowrap">{zh ? '结论' : 'Verdict'}</th>
              </tr>
            </thead>
            <tbody>
              {scans.map((r, i) => {
                const v = verdictMeta(zh, r.verdict);
                return (
                  <tr
                    key={r.scan_id}
                    onClick={() => setSel(i)}
                    className="cursor-pointer align-top border-t"
                    style={{ borderColor: 'var(--border-color)', background: i === sel ? TONE.info.bg : undefined }}
                  >
                    <td className="py-2.5 pr-3 tabular-nums whitespace-nowrap text-secondary">
                      {fmtAt(r.scanned_at)}
                      <div className="text-[10px] text-muted">{hookLabel(zh, r.hook)}</div>
                    </td>
                    <td className="py-2.5 pr-3">
                      <div className="text-primary">{r.title}</div>
                      {r.channel && <div className="text-[11px] text-muted">{zh ? '投放渠道:' : 'Channel: '}{r.channel}</div>}
                    </td>
                    <td className="py-2.5 pr-3">
                      {r.hits_brief.length === 0 && r.missing_disclaimers.length === 0
                        ? <span className="text-[11px] text-muted">{zh ? '无命中' : 'none'}</span>
                        : (
                          <>
                            {r.hits_brief.map((h) => <HitChip key={h.term} level={h.level}>{h.term}</HitChip>)}
                            {r.missing_disclaimers.map((m) => (
                              <HitChip key={m} level="warn">{zh ? `缺标注:${m}` : `missing: ${m}`}</HitChip>
                            ))}
                          </>
                        )}
                      {(r.exempted_count ?? 0) > 0 && (
                        <span className="text-[11px] text-muted ml-1"
                              title={zh ? '词库命中但语义复核判为非违规,不计入判定'
                                        : 'lexicon hits cleared by semantic review; not counted'}>
                          {zh ? `+${r.exempted_count} 处已豁免` : `+${r.exempted_count} cleared`}
                        </span>
                      )}
                    </td>
                    <td className="py-2.5 pr-3">
                      <Pill tone={(['ok', 'warn', 'ban', 'plain'].includes(r.chain_tone) ? r.chain_tone : 'plain') as ToneKey}>
                        {r.chain_label}
                      </Pill>
                    </td>
                    <td className="py-2.5"><Pill tone={v.tone}>{v.label}</Pill></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>

      {row && (
        <Card
          title={(zh ? '命中详情 —— ' : 'Hit detail — ') + row.title}
          hue={STAGE_HUE.pre}
          hint={detail
            ? `${zh ? '扫描编号' : 'Scan'} SC-${detail.scan_id} · ${zh ? '词库版本 ' : 'lexicons '}${Object.entries(detail.lexicon_versions).map(([, v]) => v)[0] || '—'}`
            : (zh ? '加载中…' : 'loading…')}
        >
          {!detail ? (
            <div className="text-xs text-muted">{zh ? '正在加载命中详情…' : 'Loading hit detail…'}</div>
          ) : (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
              <div>
                <div className="rounded-lg p-4 text-sm leading-relaxed" style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)', maxWidth: '66ch' }}>
                  <div className="text-[11px] text-muted mb-2 font-semibold">{zh ? '正文片段(命中高亮)' : 'Excerpt (hits highlighted)'}</div>
                  <p className="m-0 text-secondary" style={{ whiteSpace: 'pre-wrap' }}>
                    {buildSegments(combined, detail.hits).map((seg, i) =>
                      seg.mark ? <Mark key={i} level={seg.mark}>{seg.t}</Mark> : <span key={i}>{seg.t}</span>,
                    )}
                  </p>
                </div>
                {detail.doc.reject_reason && (
                  <p className="text-[11px] mt-2 mb-0" style={{ color: TONE.ban.fg, maxWidth: '70ch' }}>
                    {detail.doc.reject_reason}
                  </p>
                )}
                <p className="text-[11px] text-muted mt-2 mb-0" style={{ maxWidth: '70ch' }}>
                  {zh
                    ? '禁用词(红)必须删除或改写;慎用词(橙)需弱化为体验性表述;需举证词(黄)保留须附可查证材料。'
                    : 'Banned terms (red) must be removed or rewritten; restricted terms (orange) softened into experiential wording; evidence-required terms (yellow) kept only with verifiable proof.'}
                </p>
              </div>
              <div className="grid gap-2 content-start">
                {detail.hits.length === 0 && detail.missing_disclaimers.length === 0 && (
                  <div className="text-xs text-muted">
                    {(detail.exempted?.length ?? 0) > 0
                      ? (zh ? '词库命中经语义复核后全部判为非违规,本篇无需修改。'
                            : 'All lexicon hits were cleared by semantic review; nothing to fix.')
                      : (zh ? '启用词库扫描无命中。' : 'No hits across the enabled lexicons.')}
                  </div>
                )}
                {detail.hits.map((h) => (
                  <div key={h.term} className="rounded-lg px-3 py-2 text-xs" style={{ border: '1px solid var(--border-color)' }}>
                    <div className="font-bold" style={{ color: TONE[h.level].fg }}>
                      {h.term}
                      <span className="ml-2 font-normal text-muted">{lv(h.level)}</span>
                    </div>
                    <div className="text-muted mt-0.5">
                      {h.category}{h.sources.length > 0 ? ` · ${h.sources.join(' / ')}` : ''}
                    </div>
                    {h.fix && (
                      <div className="mt-0.5">
                        <span className="text-muted">{zh ? '建议替换 → ' : 'Suggested fix → '}</span>
                        <span style={{ color: TONE.ok.fg }}>{h.fix}</span>
                      </div>
                    )}
                  </div>
                ))}
                {detail.missing_disclaimers.map((m) => (
                  <div key={m.label} className="rounded-lg px-3 py-2 text-xs" style={{ border: '1px solid var(--border-color)' }}>
                    <div className="font-bold" style={{ color: TONE.warn.fg }}>
                      {zh ? `缺少强制标注:${m.label}` : `Missing mandatory label: ${m.label}`}
                    </div>
                    <div className="mt-0.5">
                      <span className="text-muted">{zh ? '应补话术 → ' : 'Required wording → '}</span>
                      <span style={{ color: TONE.ok.fg }}>{m.hint}</span>
                    </div>
                  </div>
                ))}
                {(detail.exempted?.length ?? 0) > 0 && (
                  <div className="rounded-lg px-3 py-2 text-xs" style={{ border: '1px dashed var(--border-color)' }}>
                    <div className="font-bold text-secondary">
                      {zh ? '语义复核已豁免' : 'Cleared by semantic review'}
                      <span className="ml-2 font-normal text-muted">
                        {zh ? '词库命中,但句子里不构成宣称,不计入判定'
                            : 'lexicon hits that are not claims in context; excluded from the verdict'}
                      </span>
                    </div>
                    <div className="mt-1.5 grid gap-1.5">
                      {(detail.exempted ?? []).map((h) => (
                        <div key={h.term}>
                          <span className="font-semibold text-secondary">{h.term}</span>
                          {(h.exemptions ?? []).slice(0, 3).map((ex, i) => (
                            <div key={i} className="text-muted mt-0.5" style={{ maxWidth: '60ch' }}>
                              「{ex.quote}」 —— {ex.reason}
                              <span className="ml-1">
                                {ex.by === 'rule'
                                  ? (zh ? '(规则豁免)' : '(rule)')
                                  : (zh ? '(大模型复核)' : '(LLM review)')}
                              </span>
                            </div>
                          ))}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                {detail.external?.status === 'ok' && (detail.external.risk_words?.length ?? 0) > 0 && (
                  <div className="rounded-lg px-3 py-2 text-xs" style={{ border: `1px dashed ${TONE.info.fg}66` }}>
                    <div className="font-bold" style={{ color: TONE.info.fg }}>
                      {zh ? '外检建议(阿里内容安全)' : 'External advisory (Aliyun moderation)'}
                      <span className="ml-2 font-normal text-muted">
                        {zh ? '仅供人工初审参考,不影响闸门判定' : 'advisory only; does not affect the gate verdict'}
                      </span>
                    </div>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {(detail.external.risk_words ?? []).map((w) => (
                        <HitChip key={w.word} level={w.level === 'high' ? 'ban' : 'warn'}>
                          {w.word}{w.tips ? ` · ${w.tips}` : ''}
                        </HitChip>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </Card>
      )}

      {row && detail && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <Card
            title={zh ? '四重审核链' : 'Four-layer review chain'}
            hue={STAGE_HUE.pre}
            hint={zh ? '每步留痕:经手人 · 时间 · 意见' : 'Each step logged: reviewer, time, notes'}
          >
            <div className="grid grid-cols-4 gap-2">
              {realChain(zh, detail.doc.status).map((s) => (
                <div key={s.name} className="text-xs">
                  <div
                    className="h-1 rounded mb-1.5"
                    style={{
                      background: s.status === 'done' ? TONE.ok.fg : s.status === 'now' ? TONE.warn.fg : 'var(--border-color)',
                    }}
                  />
                  <div className="font-semibold text-primary">
                    {s.name}
                    {s.status === 'done' && <span style={{ color: TONE.ok.fg }}> ✓</span>}
                    {s.status === 'now' && <span style={{ color: TONE.warn.fg }}> ●</span>}
                  </div>
                  <div className="text-[11px] text-muted">{s.who}</div>
                </div>
              ))}
            </div>
            <div className="mt-3 pt-3 border-t border-dashed grid gap-1.5" style={{ borderColor: 'var(--border-color)' }}>
              {detail.trace.length === 0 && (
                <div className="text-xs text-muted">{zh ? '暂无留痕记录。' : 'No trail entries yet.'}</div>
              )}
              {detail.trace.map((l, i) => (
                <div key={i} className="text-xs text-secondary">
                  <span className="font-semibold text-primary">{l.actor_name || (zh ? '系统' : 'system')}</span>
                  <span className="text-muted tabular-nums ml-1.5">{fmtAt(l.at)}</span>
                  <span className="ml-1.5">
                    {String((l.detail as Record<string, unknown>).reason
                      || (l.detail as Record<string, unknown>).verdict
                      || l.event_type)}
                  </span>
                </div>
              ))}
            </div>
          </Card>
          <Card
            title={zh ? '可溯源声明核对' : 'Provenance check'}
            hue={STAGE_HUE.pre}
            hint={zh ? '需举证表述须附可查证材料;核销管线下一期接入' : 'Evidence-required claims need verifiable proof; verification pipeline lands next phase'}
          >
            {detail.hits.filter((h) => h.level === 'evid').length === 0 ? (
              <div className="text-xs text-muted">{zh ? '本篇无需溯源核对的表述。' : 'No claims requiring provenance in this piece.'}</div>
            ) : (
              <div className="grid gap-2">
                {detail.hits.filter((h) => h.level === 'evid').map((h) => (
                  <div key={h.term} className="flex gap-2.5 items-baseline text-xs">
                    <span className="font-bold w-4 text-center" style={{ color: TONE.evid.fg }}>!</span>
                    <span className="text-primary">
                      「{h.term}」
                      <span className="text-muted ml-1.5 text-[11px]">
                        {zh ? '—— 待补可查证材料,补齐前该表述不得放行' : '— pending verifiable evidence; blocked until provided'}
                      </span>
                    </span>
                  </div>
                ))}
              </div>
            )}
            <p className="text-[11px] text-muted mt-3 mb-0" style={{ maxWidth: '70ch' }}>
              {zh
                ? '功效与资质表述遵循广告法及所在行业宣称规范;凡不可举证的表述,一律按禁用处理。'
                : 'Efficacy and credential wording follows advertising law and the industry\'s claim rules; any unverifiable claim is treated as banned.'}
            </p>
          </Card>
        </div>
      )}
    </>
  );
}

// ── Panel 2: in-flight monitoring ─────────────────────────────────
function MidPanel({ zh, demo }: { zh: boolean; demo: DemoData }) {
  const levelMeta = (lv: 1 | 2 | 3): { tone: ToneKey; label: string } =>
    zh
      ? ([
        { tone: 'evid', label: '一级 · 预警限时修改' },
        { tone: 'warn', label: '二级 · 内容下架' },
        { tone: 'ban', label: '三级 · 暂停投放' },
      ] as const)[lv - 1]
      : ([
        { tone: 'evid', label: 'Level 1 · warn & fix by deadline' },
        { tone: 'warn', label: 'Level 2 · take down' },
        { tone: 'ban', label: 'Level 3 · suspend placements' },
      ] as const)[lv - 1];

  return (
    <>
      <Card
        title={zh ? '违规告警流(全天候)' : 'Violation alerts (always-on)'}
        hue={STAGE_HUE.mid}
        hint={zh ? '覆盖智能问答 · 搜索种草 · 本地门店介绍;命中即抓取证据截图并开处置单' : 'AI answers, search seeding, local store intros; evidence captured and a ticket opened on every hit'}
      >
        <div className="grid gap-2.5">
          {demo.alerts.map((a, i) => {
            const meta = levelMeta(a.level);
            return (
              <div
                key={i}
                className="rounded-lg px-4 py-3"
                style={{ border: '1px solid var(--border-color)', borderLeft: `4px solid ${TONE[meta.tone].fg}` }}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Pill tone={meta.tone}>{meta.label}</Pill>
                  <span className="text-sm font-semibold text-primary">{a.type}</span>
                  <Pill tone={a.closed ? 'ok' : 'info'}>{a.closed ? (zh ? '已闭环' : 'closed') : (zh ? '处置中' : 'in progress')}</Pill>
                  <button
                    type="button"
                    className="ml-auto text-[11px] px-2 py-0.5 rounded"
                    style={{ border: `1px solid ${TONE.info.fg}66`, color: TONE.info.fg, background: 'transparent' }}
                  >
                    {zh ? '查看证据截图' : 'View evidence'}
                  </button>
                </div>
                <div className="text-xs text-secondary rounded px-3 py-1.5 mt-2" style={{ background: 'var(--bg-input)', maxWidth: '78ch' }}>
                  <span className="text-muted text-[10px] mr-1">{zh ? '回答摘录' : 'Quoted answer'}</span>
                  {a.quote}
                </div>
                <div className="flex flex-wrap gap-x-4 gap-y-0.5 mt-1.5 text-[11px] text-muted">
                  <span>{zh ? '渠道:' : 'Channel: '}{a.channel}</span>
                  <span>{zh ? '发现:' : 'Found: '}{a.foundAt}</span>
                  <span>{a.disposal}</span>
                </div>
              </div>
            );
          })}
        </div>
      </Card>

      <Card
        title={zh ? '官方渠道信任专项 —— 「正品 / 官方渠道」信任焦虑治理' : 'Official-channel trust — "authenticity / official channel" anxiety'}
        hue={STAGE_HUE.mid}
        hint={zh ? '优化官方渠道认知,压降假货与非授权渠道信息,守住品牌调性' : 'Reinforce official channels, suppress counterfeit and unauthorized-channel noise, protect brand tone'}
      >
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <TrustMetric
            big="86%"
            dir={zh ? '▲ 较优化前 +28 个百分点' : '▲ +28 pts vs. baseline'}
            cap={zh ? '回答购买渠道时,提及官方渠道 / 官网的比例' : 'Share of channel answers naming official channels / the brand site'}
            rows={[
              { label: zh ? '优化前' : 'Before', pct: 58, text: '58%', tone: 'plain' },
              { label: zh ? '本期' : 'Now', pct: 86, text: '86%', tone: 'info' },
            ]}
          />
          <TrustMetric
            big={zh ? '4 条' : '4'}
            dir={zh ? '▼ 较优化前 −17 条' : '▼ −17 vs. baseline'}
            cap={zh ? '回答中残留的假货 / 非授权渠道信息(每百问抽样)' : 'Counterfeit / unauthorized-channel mentions left (per 100 sampled queries)'}
            rows={[
              { label: zh ? '优化前' : 'Before', pct: 100, text: '21', tone: 'ban' },
              { label: zh ? '本期' : 'Now', pct: 19, text: '4', tone: 'ban' },
            ]}
          />
        </div>
        <p className="text-[11px] text-muted mt-4 mb-0">
          {zh
            ? '信息中正率(回答与品牌官方口径一致的比例)按周复测,趋势纳入月报「合规」章节。'
            : 'Answer-alignment rate (answers matching official brand messaging) is re-tested weekly and feeds the monthly report\'s compliance section.'}
        </p>
      </Card>
    </>
  );
}

function TrustMetric({ big, dir, cap, rows }: {
  big: string; dir: string; cap: string;
  rows: { label: string; pct: number; text: string; tone: ToneKey }[];
}) {
  return (
    <div>
      <div className="text-2xl font-bold tabular-nums text-primary">
        {big}
        <span className="text-xs font-normal ml-2" style={{ color: TONE.ok.fg }}>{dir}</span>
      </div>
      <div className="text-[11px] text-muted">{cap}</div>
      <div className="grid gap-1.5 mt-2.5">
        {rows.map((r) => (
          <div key={r.label} className="grid items-center gap-2 text-[11px] text-muted" style={{ gridTemplateColumns: '56px 1fr 40px' }}>
            <span>{r.label}</span>
            <span className="h-2 rounded overflow-hidden" style={{ background: 'var(--bg-input)' }}>
              <span
                className="block h-full rounded"
                style={{
                  width: `${r.pct}%`,
                  background: r.tone === 'plain' ? 'var(--border-color)' : TONE[r.tone].fg,
                }}
              />
            </span>
            <span className="text-right tabular-nums">{r.text}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Panel 2 (real data version, P1): in-flight alerts and handling ─────────
function alertLevelMeta(zh: boolean, lv: 1 | 2 | 3): { tone: ToneKey; label: string } {
  return zh
    ? ([
      { tone: 'evid' as ToneKey, label: '一级 · 预警限时修改' },
      { tone: 'warn' as ToneKey, label: '二级 · 内容下架' },
      { tone: 'ban' as ToneKey, label: '三级 · 暂停投放' },
    ])[lv - 1]
    : ([
      { tone: 'evid' as ToneKey, label: 'Level 1 · warn & fix by deadline' },
      { tone: 'warn' as ToneKey, label: 'Level 2 · take down' },
      { tone: 'ban' as ToneKey, label: 'Level 3 · suspend placements' },
    ])[lv - 1];
}

function alertTypeLabel(zh: boolean, t: AlertItem['alert_type']): string {
  const zhMap: Record<string, string> = {
    fabricated_credential: '幻觉 · 编造资质与数据',
    fake_case: '幻觉 · 虚假用户案例',
    disparage_competitor: '贬损竞品类文案',
    appearance_anxiety: '容貌 / 身材焦虑文案',
    unauthorized_channel: '非授权渠道信息干扰',
    category_redline: '类目语义红线',
    banned_wording: '违禁表述(词库命中)',
  };
  const enMap: Record<string, string> = {
    fabricated_credential: 'Hallucination · fabricated credentials',
    fake_case: 'Hallucination · fake user story',
    disparage_competitor: 'Competitor-disparaging copy',
    appearance_anxiety: 'Appearance-anxiety copy',
    unauthorized_channel: 'Unauthorized-channel interference',
    category_redline: 'Category red line',
    banned_wording: 'Banned wording (lexicon hit)',
  };
  return (zh ? zhMap : enMap)[t] || t;
}

function detectedByLabel(zh: boolean, d: AlertItem['detected_by']): { label: string; tone: ToneKey } {
  if (d === 'lexicon') return { label: zh ? '词库检出' : 'lexicon', tone: 'info' };
  if (d === 'llm') return { label: zh ? '智能识别 · 待人工确认' : 'AI-detected · needs review', tone: 'evid' };
  return { label: zh ? '人工登记' : 'manual', tone: 'plain' };
}

function alertStatusMeta(zh: boolean, s: AlertItem['status']): { label: string; tone: ToneKey } {
  const m: Record<string, { label: string; tone: ToneKey }> = zh
    ? {
      open: { label: '待处理', tone: 'warn' },
      confirmed: { label: '已开单处置', tone: 'info' },
      dismissed: { label: '已判误报', tone: 'plain' },
      closed: { label: '已闭环', tone: 'ok' },
    }
    : {
      open: { label: 'pending', tone: 'warn' },
      confirmed: { label: 'ticket opened', tone: 'info' },
      dismissed: { label: 'dismissed', tone: 'plain' },
      closed: { label: 'closed', tone: 'ok' },
    };
  return m[s] || { label: s, tone: 'plain' };
}

const TICKET_ACTION_ZH: Record<string, string> = {
  limit_fix: '预警限时修改', takedown: '内容下架', suspend: '暂停投放',
};
const TICKET_STATUS_ZH: Record<string, string> = {
  open: '待处置', in_progress: '处置中', pending_retest: '待复测',
  closed: '已闭环', overdue: '已超时',
};

function AlertCard({ zh, alert, token, onChanged }: {
  zh: boolean; alert: AlertItem; token: string; onChanged: () => void;
}) {
  const meta = alertLevelMeta(zh, alert.level);
  const st = alertStatusMeta(zh, alert.status);
  const det = detectedByLabel(zh, alert.detected_by);
  const defaultAction: TicketAction = alert.level === 3 ? 'suspend' : alert.level === 2 ? 'takedown' : 'limit_fix';
  const [action, setAction] = useState<TicketAction>(defaultAction);
  const [busy, setBusy] = useState(false);
  const [showAnswer, setShowAnswer] = useState(false);
  const tk = alert.ticket;

  const doConfirm = async () => {
    setBusy(true);
    try { await complianceApi.confirmAlert(alert.id, token, action); onChanged(); }
    catch { /* Errors are left to the next refresh as a fallback */ }
    finally { setBusy(false); }
  };
  const doDismiss = async () => {
    setBusy(true);
    try { await complianceApi.dismissAlert(alert.id, token); onChanged(); }
    catch { /* Same as above */ }
    finally { setBusy(false); }
  };
  const doTicket = async (mark: 'start' | 'done') => {
    if (!tk) return;
    setBusy(true);
    try { await complianceApi.ticketAction(tk.id, token, mark, mark === 'done' ? (zh ? '处置完成,等待复测' : 'disposal done, awaiting re-test') : ''); onChanged(); }
    catch { /* Same as above */ }
    finally { setBusy(false); }
  };

  return (
    <div
      className="rounded-lg px-4 py-3"
      style={{ border: '1px solid var(--border-color)', borderLeft: `4px solid ${TONE[meta.tone].fg}` }}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Pill tone={meta.tone}>{meta.label}</Pill>
        <span className="text-sm font-semibold text-primary">{alertTypeLabel(zh, alert.alert_type)}</span>
        <Pill tone={det.tone}>{det.label}</Pill>
        <Pill tone={st.tone}>{st.label}</Pill>
        {alert.source_url && (
          <a
            href={alert.source_url}
            target="_blank" rel="noreferrer"
            className="ml-auto text-[11px] px-2 py-0.5 rounded"
            style={{ border: `1px solid ${TONE.info.fg}66`, color: TONE.info.fg }}
          >
            {zh ? '查看原始回答' : 'View original answer'}
          </a>
        )}
      </div>
      <div className="text-xs text-secondary rounded px-3 py-1.5 mt-2" style={{ background: 'var(--bg-input)', maxWidth: '78ch' }}>
        <span className="text-muted text-[10px] mr-1">{zh ? '违规摘录' : 'Quoted violation'}</span>
        {alert.quote}
      </div>
      {alert.answer_excerpt && (
        <div className="mt-1">
          <button type="button" className="text-[11px]" style={{ color: TONE.info.fg }}
            onClick={() => setShowAnswer((v) => !v)}>
            {showAnswer ? (zh ? '收起回答摘录' : 'Hide answer excerpt') : (zh ? '展开回答摘录' : 'Show answer excerpt')}
          </button>
          {showAnswer && (
            <div className="text-[11px] text-muted rounded px-3 py-1.5 mt-1" style={{ background: 'var(--bg-input)', maxWidth: '78ch', whiteSpace: 'pre-wrap' }}>
              {alert.answer_excerpt}
            </div>
          )}
        </div>
      )}
      <div className="flex flex-wrap gap-x-4 gap-y-0.5 mt-1.5 text-[11px] text-muted">
        <span>{zh ? '渠道:' : 'Channel: '}{zh ? '智能问答(' : 'AI answers ('}{engineLabel(alert.engine as never) || alert.engine}{')'}</span>
        <span>{zh ? '发现:' : 'Found: '}{fmtAt(alert.found_at)}</span>
        {alert.reason && <span>{zh ? '依据:' : 'Basis: '}{alert.reason}</span>}
        {alert.recurrence_count > 0 && (
          <span style={{ color: TONE.warn.fg }}>
            {zh ? `后续跑批再次出现 ${alert.recurrence_count} 次` : `recurred ${alert.recurrence_count}× in later runs`}
          </span>
        )}
      </div>
      {tk && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-2 pt-2 border-t border-dashed text-[11px]" style={{ borderColor: 'var(--border-color)' }}>
          <span className="font-semibold text-primary">{tk.ticket_no}</span>
          <span className="text-muted">{zh ? TICKET_ACTION_ZH[tk.action] || tk.action : tk.action}</span>
          <Pill tone={tk.status === 'closed' ? 'ok' : tk.status === 'overdue' ? 'ban' : 'info'}>
            {zh ? TICKET_STATUS_ZH[tk.status] || tk.status : tk.status}
          </Pill>
          {tk.deadline_at && tk.status !== 'closed' && (
            <span className="text-muted">{zh ? '时限:' : 'Deadline: '}{fmtAt(tk.deadline_at)}</span>
          )}
          {tk.retest_count > 0 && (
            <span className="text-muted">
              {zh ? `复测 ${tk.retest_count} 轮 · 连续干净 ${tk.clean_streak}` : `${tk.retest_count} re-tests · ${tk.clean_streak} clean`}
            </span>
          )}
          {(tk.status === 'open' || tk.status === 'overdue') && (
            <button type="button" disabled={busy} onClick={() => doTicket('start')}
              className="text-[11px] px-2 py-0.5 rounded"
              style={{ border: `1px solid ${TONE.info.fg}66`, color: TONE.info.fg }}>
              {zh ? '开始处置' : 'Start'}
            </button>
          )}
          {tk.status === 'in_progress' && (
            <button type="button" disabled={busy} onClick={() => doTicket('done')}
              className="text-[11px] px-2 py-0.5 rounded"
              style={{ border: `1px solid ${TONE.ok.fg}66`, color: TONE.ok.fg }}>
              {zh ? '处置完成,提交复测' : 'Done, submit for re-test'}
            </button>
          )}
        </div>
      )}
      {alert.status === 'open' && (
        <div className="flex flex-wrap items-center gap-2 mt-2 pt-2 border-t border-dashed" style={{ borderColor: 'var(--border-color)' }}>
          <span className="text-[11px] text-muted">{zh ? '处置动作' : 'Disposal'}</span>
          <UiSelect
            value={action}
            onChange={(val) => setAction(val as TicketAction)}
            className="text-[11px] px-1.5 py-0.5 rounded"
            style={{ background: 'var(--bg-input)', color: 'var(--text-primary)', border: '1px solid var(--border-color)' }}
            options={[
              { value: 'limit_fix', label: zh ? '预警限时修改' : 'warn & fix by deadline' },
              { value: 'takedown', label: zh ? '内容下架' : 'take down' },
              { value: 'suspend', label: zh ? '暂停投放' : 'suspend placements' },
            ]}
          />
          <button type="button" disabled={busy} onClick={doConfirm}
            className="text-[11px] px-2.5 py-0.5 rounded font-semibold"
            style={{ background: 'var(--accent-primary)', color: 'white' }}>
            {zh ? '确认违规并开单' : 'Confirm & open ticket'}
          </button>
          <button type="button" disabled={busy} onClick={doDismiss}
            className="text-[11px] px-2.5 py-0.5 rounded"
            style={{ border: '1px solid var(--border-color)', color: 'var(--text-secondary)' }}>
            {zh ? '判为误报' : 'Dismiss as false positive'}
          </button>
        </div>
      )}
    </div>
  );
}

function MidPanelReal({ zh, alerts, token, onChanged }: {
  zh: boolean; alerts: AlertItem[]; token: string; onChanged: () => void;
}) {
  return (
    <>
      <Card
        title={zh ? '违规告警流(全天候)' : 'Violation alerts (always-on)'}
        hue={STAGE_HUE.mid}
        hint={zh
          ? '真实监测数据:每 10 分钟对新增 AI 回答跑词库扫描与语义识别;智能识别结论需人工确认后才开处置单'
          : 'Real monitoring: new AI answers scanned every 10 minutes; AI-detected findings need human confirmation before a ticket opens'}
      >
        <div className="grid gap-2.5">
          {alerts.length === 0 && (
            <div className="text-sm text-secondary rounded-xl px-4 py-6 text-center"
              style={{ background: 'var(--bg-card)', border: '1px dashed var(--border-color)' }}>
              {zh
                ? '本期无违规告警——已发布内容均经发布前审核,监测未发现发布后违规。'
                : 'No alerts this period — all published content passed pre-publish review; monitoring found no post-publish violations.'}
            </div>
          )}
          {alerts.map((a) => (
            <AlertCard key={a.id} zh={zh} alert={a} token={token} onChanged={onChanged} />
          ))}
        </div>
      </Card>
      {/* <Card
        title={zh ? '官方渠道信任专项' : 'Official-channel trust'}
        hint={zh ? '示例数据 —— 指标口径待后续版本接入' : 'Sample data — metrics land in a later phase'}
      >
        <p className="text-[11px] text-muted m-0" style={{ maxWidth: '80ch' }}>
          {zh
            ? '「正品 / 官方渠道」信任焦虑治理的量化指标(官方渠道提及率、非授权渠道信息残留)尚未接入真实数据,详见演示主题的示例展示。'
            : 'Quantified official-channel trust metrics (official-channel mention rate, unauthorized-channel residue) are not wired to real data yet; see the demo topic for the illustrative version.'}
        </p>
      </Card> */}
    </>
  );
}

// ── Panel 3: post-publication audit ─────────────────────────────────
function mapRealTimeline(zh: boolean, items: TimelineItem[]): TimelineEvent[] {
  const toneMap: Record<string, TimelineEvent['tone']> = {
    block: 'ban', scan: 'plain', release: 'ok', admission: 'plain',
    review: 'warn', lexicon_upgrade: 'plain', export: 'plain',
    qualification_expiry: 'warn', external_scan: 'plain',
  };
  const titleZh: Record<string, string> = {
    scan: '词库扫描', block: '拦截', release: '出口放行', admission: '资质档案',
    review: '审核', lexicon_upgrade: '词库升级', export: '导出调阅',
    qualification_expiry: '资质到期提醒', external_scan: '外检送审',
  };
  const titleEn: Record<string, string> = {
    scan: 'Lexicon scan', block: 'Blocked', release: 'Released at gate', admission: 'Qualification profile',
    review: 'Review', lexicon_upgrade: 'Lexicon upgrade', export: 'Export',
    qualification_expiry: 'Qualification expiry', external_scan: 'External moderation',
  };
  return items.map((it) => {
    const d = it.detail as Record<string, unknown>;
    const bits: string[] = [];
    if (d.reason) bits.push(String(d.reason));
    else if (d.verdict) {
      bits.push(zh ? `判定:${d.verdict}` : `verdict: ${d.verdict}`);
      if (typeof d.ban === 'number') {
        bits.push(zh
          ? `命中 禁用 ${d.ban} · 慎用 ${d.warn ?? 0} · 需举证 ${d.evid ?? 0}`
          : `${d.ban} banned · ${d.warn ?? 0} restricted · ${d.evid ?? 0} evidence-required`);
      }
    } else if (d.action) bits.push(String(d.action));
    if (it.actor_name) bits.push((zh ? '经手:' : 'by ') + it.actor_name);
    return {
      at: fmtAt(it.at),
      logId: it.log_id,
      tone: toneMap[it.event_type] || 'plain',
      title: (zh ? titleZh : titleEn)[it.event_type] || it.event_type,
      detail: bits.join(';') || '—',
    };
  });
}

function PostPanel({ zh, demo, realTimeline }: {
  zh: boolean; demo: DemoData; realTimeline?: TimelineItem[];
}) {
  const toneDot = (tone: TimelineEvent['tone']) =>
    tone === 'plain' ? 'var(--text-secondary)' : TONE[tone].fg;
  const real = (realTimeline?.length ?? 0) > 0;
  const events = real ? mapRealTimeline(zh, realTimeline!) : demo.timeline;

  return (
    <>
      <Card
        title={zh ? '审计日志时间线' : 'Audit log timeline'}
        hue={STAGE_HUE.post}
        hint={real
          ? (zh ? '真实审计日志:扫描 · 拦截 · 放行 · 资质,全量留痕只增不改' : 'Real audit log: scans, blocks, releases, qualifications — append-only')
          : (zh ? '扫描 · 拦截 · 放行 · 告警 · 处置 · 复测,全量留痕只增不改;当前留存 182 天' : 'Scans, blocks, releases, alerts, disposals, re-tests — append-only; 182 days retained')}
      >
        <ul className="m-0 p-0 pl-4 grid gap-4 border-l-2 list-none" style={{ borderColor: 'var(--border-color)' }}>
          {events.map((e) => (
            <li key={e.logId} className="relative text-sm">
              <span
                className="absolute w-2.5 h-2.5 rounded-full"
                style={{
                  left: -22, top: 5,
                  background: 'var(--bg-card)',
                  border: `2px solid ${toneDot(e.tone)}`,
                }}
              />
              <div className="text-[11px] text-muted tabular-nums">{e.at} · {zh ? '记录' : 'log'} {e.logId}</div>
              <div className="text-primary">
                <b className="font-semibold">{e.title}</b>
                <span className="text-secondary ml-1.5">{e.detail}</span>
              </div>
            </li>
          ))}
        </ul>
      </Card>

      <Card
        title={zh ? '导出与调阅' : 'Export & retrieval'}
        hue={STAGE_HUE.post}
        hint={zh ? '满足监管调阅与品牌方审计要求' : 'Meets regulator-retrieval and brand-audit requirements'}
      >
        <div className="flex flex-wrap gap-2.5">
          <button
            type="button"
            className="text-sm font-semibold px-4 py-2 rounded-lg"
            style={{ background: 'var(--accent-primary)', color: 'white' }}
          >
            {zh ? '导出监管调阅材料' : 'Export regulator package'}
          </button>
          <button
            type="button"
            className="text-sm font-semibold px-4 py-2 rounded-lg"
            style={{ background: 'transparent', color: 'var(--accent-primary)', border: '1px solid var(--accent-primary)' }}
          >
            {zh ? '导出品牌方审计报告' : 'Export brand audit report'}
          </button>
        </div>
        <p className="text-[11px] text-muted mt-3 mb-0" style={{ maxWidth: '80ch' }}>
          {zh
            ? '调阅材料包含:全量日志明细、证据截图原件、审核留痕(经手人 / 时间 / 意见)、词库版本记录、处置单闭环凭据;导出动作本身亦计入审计日志。'
            : 'Packages include: full log detail, original evidence screenshots, review trails (reviewer / time / notes), lexicon version history and closed-ticket proofs; the export itself is also logged.'}
        </p>
      </Card>
    </>
  );
}
