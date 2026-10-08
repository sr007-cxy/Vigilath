// Letter type catalog, body cleanup rules, report capability vocabulary. Changing this changes the whole workbench at once.
import { type ReportEmailStyleKey } from '../../../services/salesAgentApi';

export const SALES_AGENT_RECOMMENDATION_LIMIT = 100;
export const SALES_AGENT_BATCH_CONCURRENCY = 10;

export const REPORT_EMAIL_STYLES: { key: ReportEmailStyleKey; emoji: string; label: string; desc: string; variants: number }[] = [
  { key: 'leijun_pitch', emoji: '👤', label: '综合负责人', desc: '默认,发给公司相关负责人', variants: 1 },
  { key: 'executive_view', emoji: '🏢', label: '管理层', desc: '偏商业判断和新入口影响', variants: 1 },
  { key: 'marketing_brand_view', emoji: '📣', label: '市场品牌', desc: '偏品牌呈现和可见度', variants: 1 },
  { key: 'sales_growth_view', emoji: '📈', label: '销售增长', desc: '偏新客户发现和线索入口', variants: 1 },
  { key: 'digital_ops_view', emoji: '🧩', label: '数字化官网', desc: '偏官网信号和可优化项', variants: 1 },
];

// "Letter types" for intro letters without report services, a separate key space from the "recipient perspective" set above (backend
// `NO_REPORT_EMAIL_STYLES`). Brands without report capability always take the intro letter path, and when sending they usually don't yet know
// who the recipient is, so these are split by **angle** rather than by role. `standard` is concatenated by the backend, no model involved.
export const NO_REPORT_EMAIL_STYLES: { key: ReportEmailStyleKey; emoji: string; label: string; desc: string }[] = [
  { key: 'standard', emoji: '📄', label: '标准介绍', desc: '按品牌资料拼,措辞固定' },
  { key: 'scenario_fit', emoji: '🎯', label: '场景切入', desc: '先说对方处境,再说能力' },
  { key: 'industry_view', emoji: '📊', label: '行业观察', desc: '从行业变化聊到自己' },
  { key: 'need_check', emoji: '✅', label: '需求确认', desc: '不铺垫,直接问有没有需求' },
];

export const EMAIL_DRAFT_FOOTER_PATTERNS = [
  /\n*\s*[（(]\s*注[：:].*?(?:AI|Ai|ai)\s*生成.*?[）)]\s*$/s,
  /\n+\s*如不(?:希望|方便)接收.*?(?:退订|停止联系).*?$/s,
];

export const REPORT_CAPABILITY_KEYWORDS = [
  '网站检测', '官网检测', '站点检测', '网页检测', '网站诊断', '官网诊断', '站点诊断', '网页诊断',
  '检测报告', '诊断报告', '检测功能', '诊断功能', '报告功能',
  '检测', '诊断', '报告', '审计', '评测', '测评', '体检', '扫描', '监测', '监控',
  'audit', 'report', 'diagnos', 'monitor', 'scan', 'assessment',
];
export const REPORT_CAPABILITY_NEGATION_RE = /(?:不做|不提供|不支持|没有|无|非|无法|不能|不含|不是)[^。；;，,、\n]{0,12}(?:检测|诊断|报告|审计|评测|测评|体检|扫描|监测|监控|audit|report|diagnos|monitor|scan|assessment)/gi;
