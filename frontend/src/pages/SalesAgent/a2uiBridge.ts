import { basicCatalog } from '@a2ui/react/v0_9';
import type { A2uiMessage } from '@a2ui/web_core/v0_9';
import type { SalesAgentA2UIEnvelope, SalesAgentCard } from '../../services/salesAgentApi';

type A2UIText = string | { path: string };
type A2UISource = 'backend' | 'frontend';
type A2UIPayloadKind = 'report' | 'guidance' | 'recommendations' | 'send_schedule';

const SALES_AGENT_A2UI_RECOMMENDATION_LIMIT = 100;

type ReportDimension = {
  name: string;
  score: string;
  note: string;
};

type ReportStat = {
  label: string;
  value: string;
};

export type A2UIReportModel = {
  title: string;
  url: string;
  status: string;
  scoreText: string;
  progressText: string;
  grade: string;
  executiveSummary: string;
  reportPage: string;
  stats: ReportStat[];
  dimensions: ReportDimension[];
  keyFindings: string[];
  quickWins: string[];
};

type A2UIGuidanceOption = { label: string; value: string; desc?: string };

export type A2UIGuidanceModel = {
  kind: 'guidance';
  title: string;
  message: string;
  question: string;
  missingItems: string[];
  status: string;
  hint: string;
  options: A2UIGuidanceOption[];
  selectedValues?: string[];
  secondaryTitle?: string;
  secondaryHelperText?: string;
  secondaryField?: string;
  secondaryOptions?: A2UIGuidanceOption[];
  secondarySelectedValues?: string[];
  secondaryDefaultValue?: string;
};

export type A2UIRecommendationItem = {
  title: string;
  rank: string;
  score: string;
  match_score?: string;
  opportunity_score?: number | null;
  opportunity_score_label?: string;
  confidence?: string;
  confidence_level?: string;
  recommended_action?: string;
  score_summary?: string;
  lead_id?: string;
  report_status?: string;
  report_id?: string;
  public_report_url?: string;
  share_token?: string;
  report_share?: Record<string, unknown>;
  meta: string;
  reason: string;
  website: string;
  contact: string;
};

export type A2UIRecommendationsModel = {
  kind: 'company_recommendations';
  title: string;
  subtitle: string;
  status: string;
  items: A2UIRecommendationItem[];
};

export type A2UISendScheduleItem = { company: string; email: string; statusText: string };
export type A2UISendScheduleSkip = { company: string; reason: string };

/** Scheduling summary card (plan 4.5). Field names correspond one-to-one with `_normalize_send_schedule` in
 *  `backend/agents/sales_agent/a2ui.py` -- cards drawn by the backend are fed straight in; this frontend copy only falls back
 *  when the backend envelope is missing, and both sides must say exactly the same thing. */
export type A2UISendScheduleModel = {
  kind: 'send_schedule';
  scheduleId: number;
  title: string;
  status: string;
  statusText: string;
  whenText: string;
  countText: string;
  hint: string;
  warning: string;
  includedTitle: string;
  included: A2UISendScheduleItem[];
  moreIncluded: string;
  skippedTitle: string;
  skipped: A2UISendScheduleSkip[];
  moreSkipped: string;
  footer: string;
  confirmLabel: string;
};

export type A2UISendSchedulePickerBatch = {
  campaignId: number;
  label: string;
  createdAtText: string;
  leadCount: number;
};

/** Data for the "Send to which batch?" modal. **Deliberately without company names** -- the modal only reports counts; the list is in the post-scheduling card and right panel. */
export type A2UISendSchedulePickerModel = {
  kind: 'send_schedule_picker';
  whenText: string;
  scheduledAt: string;
  /** Bare Beijing-time string, fed directly into `<input min=...>` -- the only remaining time rule is "not earlier than now". */
  minScheduledAt: string;
  sessionId: string;
  batches: A2UISendSchedulePickerBatch[];
  defaultCampaignId: number;
  totalLeads: number;
  timeWarning: string;
};

export type A2UIReportPayload = {
  kind: A2UIPayloadKind;
  surfaceId: string;
  messages: A2uiMessage[];
  report: A2UIReportModel;
  guidance?: A2UIGuidanceModel;
  recommendations?: A2UIRecommendationsModel;
  sendSchedule?: A2UISendScheduleModel;
  title: string;
  raw: SalesAgentCard;
  source: A2UISource;
  catalogId?: string;
};

type A2UIComponent = {
  id: string;
  component: string;
  weight?: number;
  [key: string]: unknown;
};

function emptyReport(): A2UIReportModel {
  return {
    title: '',
    url: '',
    status: '',
    scoreText: '',
    progressText: '',
    grade: '',
    executiveSummary: '',
    reportPage: '',
    stats: [],
    dimensions: [],
    keyFindings: [],
    quickWins: [],
  };
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function asList<T = unknown>(value: unknown): T[] {
  return Array.isArray(value) ? value as T[] : [];
}

function numberOf(v: unknown, fallback = 0): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

function textOf(v: unknown, fallback = ''): string {
  if (v === null || v === undefined || v === '') return fallback;
  if (Array.isArray(v)) return v.map(x => textOf(x, '')).filter(Boolean).join('、') || fallback;
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

function shortText(value: unknown, max = 180): string {
  const text = textOf(value, '');
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

function singleQuestion(value: unknown): string {
  const candidates = Array.isArray(value) ? value : [value];
  for (const candidate of candidates) {
    const text = textOf(candidate, '').trim();
    if (!text) continue;
    const lines = text.replace(/\r/g, '\n').split('\n').map(line => line.trim()).filter(Boolean);
    const line = lines.find(item => /[?？]/.test(item)) || lines[0];
    if (!line) continue;
    return line.replace(/^(?:1[.、)]|①|[-•])\s*/, '').trim();
  }
  return '';
}

function reportTextList(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(x => shortText(x, 180)).filter(Boolean);
  const text = textOf(value, '').trim();
  return text ? [text] : [];
}

function pickReportValue(data: Record<string, unknown>, summary: Record<string, unknown>, keys: string[]): unknown {
  for (const key of keys) {
    const direct = data[key];
    if (direct !== null && direct !== undefined && direct !== '') return direct;
    const nested = summary[key];
    if (nested !== null && nested !== undefined && nested !== '') return nested;
  }
  return undefined;
}

function reportStatusLabel(status: string): string {
  const map: Record<string, string> = { generating: '检测中', ready: '已完成', failed: '失败', none: '尚未检测' };
  return map[status] || status || '—';
}

function stableHash(value: string): string {
  let hash = 5381;
  for (let i = 0; i < value.length; i += 1) hash = ((hash << 5) + hash) ^ value.charCodeAt(i);
  return Math.abs(hash >>> 0).toString(36);
}

function text(id: string, textValue: A2UIText, variant: 'h1' | 'h2' | 'h3' | 'h4' | 'h5' | 'caption' | 'body' = 'body', weight?: number): A2UIComponent {
  return { id, component: 'Text', text: textValue, variant, ...(weight ? { weight } : {}) };
}

function column(id: string, children: string[], weight?: number): A2UIComponent {
  return { id, component: 'Column', children, align: 'stretch', ...(weight ? { weight } : {}) };
}

function row(id: string, children: string[]): A2UIComponent {
  return { id, component: 'Row', children, align: 'stretch' };
}

function card(id: string, child: string, weight?: number): A2UIComponent {
  return { id, component: 'Card', child, ...(weight ? { weight } : {}) };
}

function normalizeReport(card: SalesAgentCard): A2UIReportModel {
  const data = asRecord(card.data);
  const summary = asRecord(data.summary);
  const brandName = textOf(pickReportValue(data, summary, ['brand_name', 'brand', 'title']), '');
  const url = textOf(pickReportValue(data, summary, ['url', 'website']), '');
  const status = textOf(pickReportValue(data, summary, ['status']), '');
  const totalScore = textOf(pickReportValue(data, summary, ['total_score', 'score']), '');
  const grade = textOf(pickReportValue(data, summary, ['grade']), '');
  const executiveSummary = textOf(pickReportValue(data, summary, ['executive_summary', 'summary_text', 'hint', 'message']), '');
  const reportPage = textOf(pickReportValue(data, summary, ['report_page', 'report_url']), '');
  const progress = asRecord(pickReportValue(data, summary, ['progress']));
  const progressText = progress.total != null ? `${textOf(progress.done, '0')} / ${textOf(progress.total, '0')}` : '';
  const scoreText = totalScore ? `${totalScore}${grade ? ` / ${grade}` : ''}` : '';
  const dimensions = asList<Record<string, unknown>>(pickReportValue(data, summary, ['dimensions'])).slice(0, SALES_AGENT_A2UI_RECOMMENDATION_LIMIT).map((dim, i) => ({
    name: textOf(dim['维度'] || dim.name || dim.key || dim.dimension, `维度 ${i + 1}`),
    score: textOf(dim['得分'] || dim.score || dim.value, '—'),
    note: textOf(dim['口径'] || dim.source || dim.reason || dim.note, ''),
  }));
  const stats = [
    { label: '状态', value: reportStatusLabel(status) },
    { label: '综合评分', value: scoreText },
    { label: '进度', value: progressText },
    { label: '等级', value: grade },
  ].filter(stat => stat.value);

  return {
    title: brandName || url || (/generate_detection_report/i.test(card.tool || '') ? '诊断报告入口' : '历史报告摘要'),
    url,
    status: reportStatusLabel(status),
    scoreText,
    progressText,
    grade,
    executiveSummary,
    reportPage,
    stats,
    dimensions,
    keyFindings: reportTextList(pickReportValue(data, summary, ['key_findings', 'findings'])),
    quickWins: reportTextList(pickReportValue(data, summary, ['quick_wins', 'actions', 'suggestions'])),
  };
}

function normalizeEnvelopeReport(value: unknown, fallback: A2UIReportModel): A2UIReportModel {
  const report = asRecord(value);
  if (!Object.keys(report).length) return fallback;
  const dimensions = asList<Record<string, unknown>>(report.dimensions).slice(0, SALES_AGENT_A2UI_RECOMMENDATION_LIMIT).map((dim, i) => ({
    name: textOf(dim.name, fallback.dimensions[i]?.name || `维度 ${i + 1}`),
    score: textOf(dim.score, fallback.dimensions[i]?.score || '—'),
    note: textOf(dim.note, fallback.dimensions[i]?.note || ''),
  }));
  const stats = asList<Record<string, unknown>>(report.stats).map(stat => ({
    label: textOf(stat.label, ''),
    value: textOf(stat.value, ''),
  })).filter(stat => stat.label && stat.value);

  return {
    title: textOf(report.title, fallback.title),
    url: textOf(report.url, fallback.url),
    status: textOf(report.status, fallback.status),
    scoreText: textOf(report.scoreText, fallback.scoreText),
    progressText: textOf(report.progressText, fallback.progressText),
    grade: textOf(report.grade, fallback.grade),
    executiveSummary: textOf(report.executiveSummary, fallback.executiveSummary),
    reportPage: textOf(report.reportPage, fallback.reportPage),
    stats: stats.length ? stats : fallback.stats,
    dimensions: dimensions.length ? dimensions : fallback.dimensions,
    keyFindings: reportTextList(report.keyFindings).length ? reportTextList(report.keyFindings) : fallback.keyFindings,
    quickWins: reportTextList(report.quickWins).length ? reportTextList(report.quickWins) : fallback.quickWins,
  };
}

function guidanceSourceData(card: SalesAgentCard): Record<string, unknown> {
  const data = asRecord(card.data);
  const ranking = asRecord(data.ranking);
  if (ranking.status || ranking.needs_user_input || ranking.next_question || ranking.questions) return ranking;
  return data;
}

function guidanceOptions(value: unknown): A2UIGuidanceOption[] {
  return asList<Record<string, unknown>>(value).map(opt => ({
    label: textOf(opt.label || opt.value, ''),
    value: textOf(opt.value || opt.label, ''),
    desc: textOf(opt.desc || opt.description, ''),
  })).filter(opt => opt.label && opt.value);
}

function normalizeGuidance(card: SalesAgentCard): A2UIGuidanceModel {
  const data = guidanceSourceData(card);
  const question = singleQuestion(data.next_question) || singleQuestion(data.questions) || singleQuestion(data.message);
  const qCard = asRecord(data.question_card);
  const cardId = textOf(qCard.id, '');
  const title = /^(sales_agent_entry_action)/.test(cardId) ? '找客户' : textOf(qCard.title, '请先补充信息');
  return {
    kind: 'guidance',
    title,
    message: textOf(data.message, '这次新对话里我还不了解你的业务情况。为了推荐得更准,请先补充:'),
    question: question || textOf(qCard.title, '') || '这次你主要想推广什么服务或产品?',
    missingItems: asList(data.missing_items || data.missing_config).map(item => textOf(item, '')).filter(Boolean),
    status: 'need_more_info',
    hint: textOf(qCard.helper_text, '请直接在下方聊天输入框回答这个问题。'),
    options: guidanceOptions(qCard.options),
    selectedValues: asList(qCard.selected_values).map(item => textOf(item, '')).filter(Boolean),
    secondaryTitle: textOf(qCard.secondary_title, ''),
    secondaryHelperText: textOf(qCard.secondary_helper_text, ''),
    secondaryField: textOf(qCard.secondary_field, ''),
    secondaryOptions: guidanceOptions(qCard.secondary_options),
    secondarySelectedValues: asList(qCard.secondary_selected_values).map(item => textOf(item, '')).filter(Boolean),
    secondaryDefaultValue: textOf(qCard.secondary_default_value, ''),
  };
}

function normalizeEnvelopeGuidance(value: unknown, fallback: A2UIGuidanceModel): A2UIGuidanceModel {
  const guidance = asRecord(value);
  if (!Object.keys(guidance).length) return fallback;
  const options = guidanceOptions(guidance.options);
  const secondaryOptions = guidanceOptions(guidance.secondaryOptions || guidance.secondary_options);
  return {
    kind: 'guidance',
    title: textOf(guidance.title, fallback.title),
    message: textOf(guidance.message, fallback.message),
    question: singleQuestion(guidance.question) || fallback.question,
    missingItems: asList(guidance.missingItems || guidance.missing_items).map(item => textOf(item, '')).filter(Boolean),
    status: textOf(guidance.status, fallback.status),
    hint: textOf(guidance.hint, fallback.hint),
    options: options.length ? options : fallback.options,
    selectedValues: asList(guidance.selectedValues || guidance.selected_values).map(item => textOf(item, '')).filter(Boolean),
    secondaryTitle: textOf(guidance.secondaryTitle || guidance.secondary_title, fallback.secondaryTitle || ''),
    secondaryHelperText: textOf(guidance.secondaryHelperText || guidance.secondary_helper_text, fallback.secondaryHelperText || ''),
    secondaryField: textOf(guidance.secondaryField || guidance.secondary_field, fallback.secondaryField || ''),
    secondaryOptions: secondaryOptions.length ? secondaryOptions : fallback.secondaryOptions,
    secondarySelectedValues: asList(guidance.secondarySelectedValues || guidance.secondary_selected_values).map(item => textOf(item, '')).filter(Boolean),
    secondaryDefaultValue: textOf(guidance.secondaryDefaultValue || guidance.secondary_default_value, fallback.secondaryDefaultValue || ''),
  };
}

function normalizeRecommendations(card: SalesAgentCard): A2UIRecommendationsModel {
  const data = asRecord(card.data);
  const cardModel = asRecord(data.result_card);
  const directItems = asList<Record<string, unknown>>(cardModel.items);
  const leads = asList<Record<string, unknown>>(data.leads);
  const table = asList<Record<string, unknown>>(data.recommendation_table);
  const items = (directItems.length ? directItems : table).slice(0, SALES_AGENT_A2UI_RECOMMENDATION_LIMIT).map((item, i) => {
    const lead = leads[i] || {};
    const scoring = asRecord(item.scoring || lead.scoring);
    const company = asRecord(lead.company);
    const industry = textOf(company.industry, '');
    const region = textOf(company.region, '');
    const size = textOf(company.employee_size, '');
    const meta = [industry ? `目标行业:${industry}` : '', region ? `目标地区:${region}` : '', size ? `企业规模:${size}` : ''].filter(Boolean).join(' · ');
    const reportShare = asRecord(item.report_share || lead.report_share);
    const matchScore = textOf(item.match_score || lead.match_score || scoring.match_score || item.score || lead.score, '');
    return {
      title: textOf(item.title || item['企业'] || item.company || item.name || company.name, `企业 ${i + 1}`),
      rank: textOf(item.rank || lead.rank, String(i + 1)),
      score: matchScore,
      match_score: matchScore,
      opportunity_score: numberOf(item.opportunity_score ?? lead.opportunity_score ?? scoring.opportunity_score, 0) || null,
      opportunity_score_label: textOf(item.opportunity_score_label || lead.opportunity_score_label || scoring.opportunity_score_label, '待评估'),
      confidence: textOf(item.confidence || lead.confidence || scoring.confidence, 'low'),
      confidence_level: textOf(item.confidence_level || lead.confidence_level || scoring.confidence_level, '低'),
      recommended_action: textOf(item.recommended_action || lead.recommended_action || scoring.recommended_action, ''),
      score_summary: textOf(item.score_summary || lead.score_summary || scoring.score_summary, ''),
      lead_id: textOf(item.lead_id || lead.id, ''),
      report_status: textOf(item.report_status || lead.report_status || reportShare.report_status, ''),
      report_id: textOf(item.report_id || lead.report_id || lead.source_report_id || reportShare.report_id, ''),
      public_report_url: textOf(item.public_report_url || lead.public_report_url || reportShare.public_url, ''),
      share_token: textOf(item.share_token || lead.share_token || reportShare.share_token || reportShare.public_token, ''),
      report_share: reportShare,
      meta: textOf(item.meta, meta),
      reason: shortText(item.reason || lead.reason, 160),
      website: textOf(item.website || item['官网'] || company.website, '-').replace(/^https?:\/\/(www\.)?/i, '').replace(/\/$/, ''),
      contact: textOf(item.contact || item['公开联系方式'] || item.public_contact, '未发现公开公司级联系方式'),
    };
  });
  const requested = textOf(data.requested_limit, '');
  const actual = textOf(data.actual_count || data.count, String(items.length));
  return {
    kind: 'company_recommendations',
    title: textOf(cardModel.title, /rank_companies/i.test(card.tool || '') ? '企业排序预览' : '企业推荐预览'),
    subtitle: textOf(cardModel.subtitle, `已推荐 ${actual}${requested ? ` / 目标 ${requested}` : ''}`),
    status: textOf(cardModel.status || data.status, 'ok'),
    items,
  };
}

function normalizeEnvelopeRecommendations(value: unknown, fallback: A2UIRecommendationsModel): A2UIRecommendationsModel {
  const recommendations = asRecord(value);
  if (!Object.keys(recommendations).length) return fallback;
  const items = asList<Record<string, unknown>>(recommendations.items).slice(0, SALES_AGENT_A2UI_RECOMMENDATION_LIMIT).map((item, i) => {
    const fallbackItem = fallback.items[i];
    const reportShare = asRecord(item.report_share || fallbackItem?.report_share);
    return {
      title: textOf(item.title, fallbackItem?.title || `企业 ${i + 1}`),
      rank: textOf(item.rank, fallbackItem?.rank || String(i + 1)),
      score: textOf(item.score, fallbackItem?.score || ''),
      match_score: textOf(item.match_score, fallbackItem?.match_score || fallbackItem?.score || ''),
      opportunity_score: numberOf(item.opportunity_score, 0) || fallbackItem?.opportunity_score || null,
      opportunity_score_label: textOf(item.opportunity_score_label, fallbackItem?.opportunity_score_label || '待评估'),
      confidence: textOf(item.confidence, fallbackItem?.confidence || 'low'),
      confidence_level: textOf(item.confidence_level, fallbackItem?.confidence_level || '低'),
      recommended_action: textOf(item.recommended_action, fallbackItem?.recommended_action || ''),
      score_summary: textOf(item.score_summary, fallbackItem?.score_summary || ''),
      lead_id: textOf(item.lead_id, fallbackItem?.lead_id || ''),
      report_status: textOf(item.report_status, fallbackItem?.report_status || textOf(reportShare.report_status, '')),
      report_id: textOf(item.report_id, fallbackItem?.report_id || textOf(reportShare.report_id, '')),
      public_report_url: textOf(item.public_report_url, fallbackItem?.public_report_url || textOf(reportShare.public_url, '')),
      share_token: textOf(item.share_token, fallbackItem?.share_token || textOf(reportShare.share_token || reportShare.public_token, '')),
      report_share: reportShare,
      meta: textOf(item.meta, fallbackItem?.meta || ''),
      reason: textOf(item.reason, fallbackItem?.reason || ''),
      website: textOf(item.website, fallbackItem?.website || '-'),
      contact: textOf(item.contact, fallbackItem?.contact || '未发现公开公司级联系方式'),
    };
  });
  return {
    kind: 'company_recommendations',
    title: textOf(recommendations.title, fallback.title),
    subtitle: textOf(recommendations.subtitle, fallback.subtitle),
    status: textOf(recommendations.status, fallback.status),
    items: items.length ? items : fallback.items,
  };
}

const SALES_AGENT_A2UI_SCHEDULE_LIMIT = 12;

function normalizeSendSchedule(card: SalesAgentCard): A2UISendScheduleModel {
  const data = asRecord(card.data);
  const schedule = asRecord(data.schedule);
  const includedAll = asList<Record<string, unknown>>(data.included);
  const skippedAll = asList<Record<string, unknown>>(data.skipped);
  const status = textOf(schedule.status, 'draft');
  // After armed the title says "Scheduled"; for draft it says "Pending confirmation". To users these are two different things:
  // one is "I've authorized it", the other "still waiting for my click"; never mix them.
  const armed = status === 'armed' || status === 'running' || status === 'done';
  const included = includedAll.slice(0, SALES_AGENT_A2UI_SCHEDULE_LIMIT).map(item => ({
    company: textOf(item.company, '(未命名企业)'),
    email: textOf(item.email, ''),
    // has_draft isn't exposed: whether the draft is written is our scheduling detail, and users can't act on it anyway.
    statusText: '待发送',
  }));
  const skipped = skippedAll.slice(0, SALES_AGENT_A2UI_SCHEDULE_LIMIT).map(item => ({
    company: textOf(item.company, '(未命名企业)'),
    reason: shortText(item.reason, 60),
  }));
  const total = numberOf(schedule.total, includedAll.length);
  return {
    kind: 'send_schedule',
    scheduleId: numberOf(schedule.id, 0),
    title: armed ? '已排期' : '定时发送 · 待确认',
    status,
    statusText: textOf(schedule.status_text, ''),
    whenText: textOf(schedule.scheduled_at_text, ''),
    countText: total ? `${total} 家` : '',
    hint: textOf(data.hint, ''),
    warning: textOf(data.time_warning, ''),
    includedTitle: includedAll.length ? `会发这 ${includedAll.length} 家` : '',
    included,
    moreIncluded: includedAll.length > included.length ? `另外 ${includedAll.length - included.length} 家没展开,在计划详情里看` : '',
    // "Give the reason per company for those that can't send" is a hard rule; here we only truncate the count, never merge reasons.
    skippedTitle: skippedAll.length ? `发不了这 ${skippedAll.length} 家` : '',
    skipped,
    moreSkipped: skippedAll.length > skipped.length ? `另外 ${skippedAll.length - skipped.length} 家发不了,在计划详情里看` : '',
    footer: armed
      // The per-company exclusion checkboxes in the panel have been removed, so we can't promise "exclude some companies" here anymore --
      // sending users to look for a nonexistent checkbox in the panel is worse than saying nothing.
      ? '到点会逐封自动发,中途可以在计划面板里改时间,或者整个取消。'
      : '现在**一封都还没发出去**。确认之后才会到点逐封发 —— 确认的是「给这些企业发信」这个范围。',
    confirmLabel: armed ? '' : '确认排期',
  };
}

function normalizeEnvelopeSendSchedule(value: unknown, fallback: A2UISendScheduleModel): A2UISendScheduleModel {
  const model = asRecord(value);
  if (!Object.keys(model).length) return fallback;
  const included = asList<Record<string, unknown>>(model.included).map((item, i) => ({
    company: textOf(item.company, fallback.included[i]?.company || ''),
    email: textOf(item.email, fallback.included[i]?.email || ''),
    statusText: textOf(item.statusText, fallback.included[i]?.statusText || '待发送'),
  }));
  const skipped = asList<Record<string, unknown>>(model.skipped).map((item, i) => ({
    company: textOf(item.company, fallback.skipped[i]?.company || ''),
    reason: textOf(item.reason, fallback.skipped[i]?.reason || ''),
  }));
  return {
    kind: 'send_schedule',
    scheduleId: numberOf(model.scheduleId, fallback.scheduleId),
    title: textOf(model.title, fallback.title),
    status: textOf(model.status, fallback.status),
    statusText: textOf(model.statusText, fallback.statusText),
    whenText: textOf(model.whenText, fallback.whenText),
    countText: textOf(model.countText, fallback.countText),
    hint: textOf(model.hint, fallback.hint),
    warning: textOf(model.warning, fallback.warning),
    includedTitle: textOf(model.includedTitle, fallback.includedTitle),
    included: included.length ? included : fallback.included,
    moreIncluded: textOf(model.moreIncluded, fallback.moreIncluded),
    skippedTitle: textOf(model.skippedTitle, fallback.skippedTitle),
    skipped: skipped.length ? skipped : fallback.skipped,
    moreSkipped: textOf(model.moreSkipped, fallback.moreSkipped),
    footer: textOf(model.footer, fallback.footer),
    confirmLabel: textOf(model.confirmLabel, fallback.confirmLabel),
  };
}

function sendScheduleMessages(surfaceId: string, schedule: A2UISendScheduleModel): A2uiMessage[] {
  const bodyChildren = ['schedule-title', 'schedule-when'];
  if (schedule.hint) bodyChildren.push('schedule-hint');
  if (schedule.warning) bodyChildren.push('schedule-warning');
  if (schedule.included.length) {
    bodyChildren.push('included-title', ...schedule.included.map((_, i) => `included-row-${i}`));
    if (schedule.moreIncluded) bodyChildren.push('included-more');
  }
  if (schedule.skipped.length) {
    bodyChildren.push('skipped-title', ...schedule.skipped.map((_, i) => `skipped-row-${i}`));
    if (schedule.moreSkipped) bodyChildren.push('skipped-more');
  }
  bodyChildren.push('schedule-footer');
  const components: A2UIComponent[] = [
    text('renderer-note', 'A2UI 兼容排期卡片 · Google Agent-to-User Interface', 'caption'),
    text('schedule-title', { path: '/title' }, 'h3'),
    text('schedule-when', { path: '/whenText' }, 'h4', 1),
    ...(schedule.hint ? [text('schedule-hint', { path: '/hint' }, 'body')] : []),
    ...(schedule.warning ? [text('schedule-warning', { path: '/warning' }, 'body', 1)] : []),
    ...(schedule.included.length ? [text('included-title', { path: '/includedTitle' }, 'h4', 1)] : []),
    ...schedule.included.flatMap((_, i) => [
      text(`included-${i}`, { path: `/included/${i}/company` }, 'body', 1),
      text(`included-${i}-email`, { path: `/included/${i}/email` }, 'caption'),
      text(`included-${i}-state`, { path: `/included/${i}/statusText` }, 'caption'),
      row(`included-row-${i}`, [`included-${i}`, `included-${i}-email`, `included-${i}-state`]),
    ]),
    ...(schedule.moreIncluded ? [text('included-more', { path: '/moreIncluded' }, 'caption')] : []),
    ...(schedule.skipped.length ? [text('skipped-title', { path: '/skippedTitle' }, 'h4', 1)] : []),
    ...schedule.skipped.flatMap((_, i) => [
      text(`skipped-${i}`, { path: `/skipped/${i}/company` }, 'body', 1),
      text(`skipped-${i}-reason`, { path: `/skipped/${i}/reason` }, 'caption'),
      row(`skipped-row-${i}`, [`skipped-${i}`, `skipped-${i}-reason`]),
    ]),
    ...(schedule.moreSkipped ? [text('skipped-more', { path: '/moreSkipped' }, 'caption')] : []),
    text('schedule-footer', { path: '/footer' }, 'caption'),
    column('schedule-body', bodyChildren),
    card('schedule-card', 'schedule-body'),
    column('root', ['renderer-note', 'schedule-card']),
  ];
  return [
    { version: 'v0.9', createSurface: { surfaceId, catalogId: basicCatalog.id } },
    { version: 'v0.9', updateComponents: { surfaceId, components } },
    { version: 'v0.9', updateDataModel: { surfaceId, path: '/', value: schedule } },
  ];
}

function appendSection(components: A2UIComponent[], rootChildren: string[], sectionId: string, title: string, bodyIds: string[]) {
  if (!bodyIds.length) return;
  const titleId = `${sectionId}-title`;
  const columnId = `${sectionId}-column`;
  components.push(text(titleId, title, 'h4'));
  components.push(column(columnId, [titleId, ...bodyIds]));
  components.push(card(sectionId, columnId));
  rootChildren.push(sectionId);
}

function reportMessages(surfaceId: string, report: A2UIReportModel): A2uiMessage[] {
  const components: A2UIComponent[] = [];
  const rootChildren: string[] = ['renderer-note'];

  components.push(text('renderer-note', 'A2UI 兼容渲染 · Google Agent-to-User Interface', 'caption'));

  const statCards = report.stats.map((stat, i) => {
    const labelId = `stat-${i}-label`;
    const valueId = `stat-${i}-value`;
    const bodyId = `stat-${i}-body`;
    const cardId = `stat-${i}-card`;
    components.push(text(labelId, stat.label, 'caption'));
    components.push(text(valueId, stat.value, 'h3'));
    components.push(column(bodyId, [labelId, valueId]));
    components.push(card(cardId, bodyId, 1));
    return cardId;
  });
  for (let i = 0; i < statCards.length; i += 2) {
    const rowId = `stats-row-${i / 2}`;
    components.push(row(rowId, statCards.slice(i, i + 2)));
    rootChildren.push(rowId);
  }

  if (report.executiveSummary) {
    const bodyId = 'summary-body';
    components.push(text(bodyId, { path: '/executiveSummary' }, 'body'));
    appendSection(components, rootChildren, 'summary-section', '报告摘要', [bodyId]);
  }

  if (report.dimensions.length) {
    const dimensionRows = report.dimensions.map((_, i) => {
      const nameId = `dimension-${i}-name`;
      const scoreId = `dimension-${i}-score`;
      const noteId = `dimension-${i}-note`;
      const rowId = `dimension-${i}-row`;
      const bodyId = `dimension-${i}-body`;
      const children = [rowId];
      components.push(text(nameId, { path: `/dimensions/${i}/name` }, 'body', 1));
      components.push(text(scoreId, { path: `/dimensions/${i}/score` }, 'body'));
      components.push(row(rowId, [nameId, scoreId]));
      if (report.dimensions[i].note) {
        components.push(text(noteId, { path: `/dimensions/${i}/note` }, 'caption'));
        children.push(noteId);
      }
      components.push(column(bodyId, children));
      return bodyId;
    });
    appendSection(components, rootChildren, 'dimensions-section', '维度得分', dimensionRows);
  }

  const findingIds = report.keyFindings.slice(0, SALES_AGENT_A2UI_RECOMMENDATION_LIMIT).map((_, i) => {
    const id = `finding-${i}`;
    components.push(text(id, `${i + 1}. ${report.keyFindings[i]}`, 'body'));
    return id;
  });
  appendSection(components, rootChildren, 'findings-section', '关键发现', findingIds);

  const winIds = report.quickWins.slice(0, SALES_AGENT_A2UI_RECOMMENDATION_LIMIT).map((_, i) => {
    const id = `quick-win-${i}`;
    components.push(text(id, `${i + 1}. ${report.quickWins[i]}`, 'body'));
    return id;
  });
  appendSection(components, rootChildren, 'quick-wins-section', '快速优化建议', winIds);

  if (report.reportPage) {
    components.push(text('open-report-label', '打开完整报告', 'body'));
    components.push({
      id: 'open-report-button',
      component: 'Button',
      child: 'open-report-label',
      variant: 'primary',
      action: { event: { name: 'open_report', context: { href: report.reportPage } } },
    });
    rootChildren.push('open-report-button');
  }

  components.push(column('root', rootChildren));

  return [
    { version: 'v0.9', createSurface: { surfaceId, catalogId: basicCatalog.id } },
    { version: 'v0.9', updateComponents: { surfaceId, components } },
    { version: 'v0.9', updateDataModel: { surfaceId, path: '/', value: report } },
  ];
}

function recommendationMessages(surfaceId: string, recommendations: A2UIRecommendationsModel): A2uiMessage[] {
  const components: A2UIComponent[] = [
    text('renderer-note', 'A2UI 兼容推荐企业卡片 · Google Agent-to-User Interface', 'caption'),
    text('recommendation-title', { path: '/title' }, 'h3'),
    text('recommendation-subtitle', { path: '/subtitle' }, 'caption'),
    ...recommendations.items.slice(0, SALES_AGENT_A2UI_RECOMMENDATION_LIMIT).flatMap((item, i) => {
      const badge = `#${item.rank || i + 1}${item.score ? ` · ${item.score}分` : ''}`;
      return [
        text(`company-${i}-title`, { path: `/items/${i}/title` }, 'body', 1),
        text(`company-${i}-badge`, badge, 'caption'),
        row(`company-${i}-header`, [`company-${i}-title`, `company-${i}-badge`]),
        ...(item.meta ? [text(`company-${i}-meta`, { path: `/items/${i}/meta` }, 'caption')] : []),
        ...(item.reason ? [text(`company-${i}-reason`, { path: `/items/${i}/reason` }, 'body')] : []),
        text(`company-${i}-website`, { path: `/items/${i}/website` }, 'caption'),
        text(`company-${i}-contact`, { path: `/items/${i}/contact` }, 'caption'),
        column(`company-${i}-body`, [
          `company-${i}-header`,
          ...(item.meta ? [`company-${i}-meta`] : []),
          ...(item.reason ? [`company-${i}-reason`] : []),
          `company-${i}-website`,
          `company-${i}-contact`,
        ]),
        card(`company-${i}-card`, `company-${i}-body`),
      ];
    }),
    column('recommendation-body', ['recommendation-title', 'recommendation-subtitle', ...recommendations.items.slice(0, SALES_AGENT_A2UI_RECOMMENDATION_LIMIT).map((_, i) => `company-${i}-card`)]),
    card('recommendation-card', 'recommendation-body'),
    column('root', ['renderer-note', 'recommendation-card']),
  ];
  return [
    { version: 'v0.9', createSurface: { surfaceId, catalogId: basicCatalog.id } },
    { version: 'v0.9', updateComponents: { surfaceId, components } },
    { version: 'v0.9', updateDataModel: { surfaceId, path: '/', value: recommendations } },
  ];
}

function guidanceMessages(surfaceId: string, guidance: A2UIGuidanceModel): A2uiMessage[] {
  const primaryOptionIds = guidance.options.slice(0, SALES_AGENT_A2UI_RECOMMENDATION_LIMIT).map((_, i) => `option-card-${i}`);
  const secondaryOptions = (guidance.secondaryOptions || []).slice(0, SALES_AGENT_A2UI_RECOMMENDATION_LIMIT);
  const secondaryOptionIds = secondaryOptions.map((_, i) => `secondary-option-card-${i}`);
  const secondaryChildren = secondaryOptions.length ? ['secondary-divider', 'secondary-title', 'secondary-hint', ...secondaryOptionIds] : [];
  const components: A2UIComponent[] = [
    text('renderer-note', 'A2UI 兼容引导卡片 · Google Agent-to-User Interface', 'caption'),
    text('question-title', { path: '/title' }, 'h3'),
    text('question-text', { path: '/question' }, 'body', 1),
    text('question-hint', { path: '/hint' }, 'caption'),
    ...guidance.options.slice(0, SALES_AGENT_A2UI_RECOMMENDATION_LIMIT).flatMap((opt, i) => [
      text(`option-${i}`, opt.label, 'body'),
      card(`option-card-${i}`, `option-${i}`),
    ]),
    ...(secondaryOptions.length ? [
      text('secondary-divider', '────────', 'caption'),
      text('secondary-title', { path: '/secondaryTitle' }, 'h4', 1),
      text('secondary-hint', { path: '/secondaryHelperText' }, 'caption'),
      ...secondaryOptions.flatMap((opt, i) => [
        text(`secondary-option-${i}`, opt.label, 'body'),
        card(`secondary-option-card-${i}`, `secondary-option-${i}`),
      ]),
    ] : []),
    column('question-body', ['question-title', 'question-text', 'question-hint', ...primaryOptionIds, ...secondaryChildren]),
    card('question-card', 'question-body'),
    column('root', ['renderer-note', 'question-card']),
  ];
  return [
    { version: 'v0.9', createSurface: { surfaceId, catalogId: basicCatalog.id } },
    { version: 'v0.9', updateComponents: { surfaceId, components } },
    { version: 'v0.9', updateDataModel: { surfaceId, path: '/', value: guidance } },
  ];
}

function validBackendA2UIEnvelope(a2ui?: SalesAgentA2UIEnvelope): a2ui is SalesAgentA2UIEnvelope & { surfaceId: string; messages: A2uiMessage[] } {
  return !!a2ui
    && typeof a2ui.surfaceId === 'string'
    && a2ui.surfaceId.trim().length > 0
    && Array.isArray(a2ui.messages)
    && a2ui.messages.length > 0
    && (!a2ui.mime || a2ui.mime === 'application/a2ui+json')
    && (!a2ui.version || /^v0\.9/.test(a2ui.version));
}

function backendCardToA2UI(card: SalesAgentCard): A2UIReportPayload | null {
  if (!validBackendA2UIEnvelope(card.a2ui)) return null;
  const envelopeKind = card.a2ui.kind === 'recommendations' || card.a2ui.recommendations || card.a2ui.model?.kind === 'company_recommendations'
    ? 'recommendations'
    : card.a2ui.kind === 'guidance' || card.a2ui.guidance || card.a2ui.model?.kind === 'guidance'
      ? 'guidance'
      : card.a2ui.kind === 'send_schedule' || card.a2ui.sendSchedule || card.a2ui.model?.kind === 'send_schedule'
        ? 'send_schedule'
        : 'report';
  if (envelopeKind === 'send_schedule') {
    const fallback = normalizeSendSchedule(card);
    const sendSchedule = normalizeEnvelopeSendSchedule(card.a2ui.sendSchedule || card.a2ui.model, fallback);
    return {
      kind: 'send_schedule',
      surfaceId: card.a2ui.surfaceId,
      messages: card.a2ui.messages,
      report: emptyReport(),
      sendSchedule,
      title: sendSchedule.title,
      raw: card,
      source: 'backend',
      catalogId: card.a2ui.catalogId,
    };
  }
  if (envelopeKind === 'recommendations') {
    const fallback = normalizeRecommendations(card);
    const recommendations = normalizeEnvelopeRecommendations(card.a2ui.recommendations || card.a2ui.model, fallback);
    return {
      kind: 'recommendations',
      surfaceId: card.a2ui.surfaceId,
      messages: card.a2ui.messages,
      report: emptyReport(),
      recommendations,
      title: recommendations.title,
      raw: card,
      source: 'backend',
      catalogId: card.a2ui.catalogId,
    };
  }
  if (envelopeKind === 'guidance') {
    const fallback = normalizeGuidance(card);
    const guidance = normalizeEnvelopeGuidance(card.a2ui.guidance || card.a2ui.model, fallback);
    return {
      kind: 'guidance',
      surfaceId: card.a2ui.surfaceId,
      messages: card.a2ui.messages,
      report: emptyReport(),
      guidance,
      title: guidance.title,
      raw: card,
      source: 'backend',
      catalogId: card.a2ui.catalogId,
    };
  }
  const fallback = normalizeReport(card);
  const report = normalizeEnvelopeReport(card.a2ui.report, fallback);
  return {
    kind: 'report',
    surfaceId: card.a2ui.surfaceId,
    messages: card.a2ui.messages,
    report,
    title: report.title,
    raw: card,
    source: 'backend',
    catalogId: card.a2ui.catalogId,
  };
}

export function isA2UIReportCard(card?: SalesAgentCard | null): card is SalesAgentCard {
  return !!card && /summarize_report|generate_detection_report/i.test(card.tool || '');
}

export function isA2UIGuidanceCard(card?: SalesAgentCard | null): card is SalesAgentCard {
  if (!card || !/rank_companies|recommend_companies/i.test(card.tool || '')) return false;
  const data = guidanceSourceData(card);
  return Boolean(data.needs_user_input || data.status === 'need_more_info' || singleQuestion(data.next_question) || singleQuestion(data.questions));
}

export function isA2UIRecommendationsCard(card?: SalesAgentCard | null): card is SalesAgentCard {
  if (!card || !/rank_companies|recommend_companies/i.test(card.tool || '')) return false;
  const data = asRecord(card.data);
  if (data.needs_user_input || data.status === 'need_more_info') return false;
  return Boolean(asList(asRecord(data.result_card).items).length || asList(data.recommendation_table).length || asList(data.leads).length);
}

export function isA2UISendScheduleCard(card?: SalesAgentCard | null): card is SalesAgentCard {
  if (!card || !/schedule_outreach_send|confirm_scheduled_send|reschedule_send/i.test(card.tool || '')) return false;
  const data = asRecord(card.data);
  // A need_time response has no plan, only a follow-up question -- drawing a card would make users think it's already scheduled.
  if (data.need_time) return false;
  return numberOf(asRecord(data.schedule).id, 0) > 0;
}

/**
 * "Send to which batch?" selection card. Not the same as the one above:
 * that one is **already scheduled**, this one is **not scheduled yet, pick a batch first** -- the card has no schedule_id at all.
 */
export function isA2UISendSchedulePickerCard(card?: SalesAgentCard | null): card is SalesAgentCard {
  if (!card || !/^send_schedule_batch_picker$/i.test((card.tool || '').trim())) return false;
  // With no batches at all it's not a selectable card -- the backend won't draw it either, so guard here too.
  return asList(asRecord(card.data).batches).length > 0;
}

export function normalizeSendSchedulePicker(card: SalesAgentCard): A2UISendSchedulePickerModel {
  const data = asRecord(card.data);
  const batches = asList<Record<string, unknown>>(data.batches).map((item, i) => ({
    campaignId: numberOf(item.campaign_id, 0),
    label: textOf(item.label, `第 ${i + 1} 批`),
    createdAtText: textOf(item.created_at_text, ''),
    leadCount: numberOf(item.lead_count, 0),
  }));
  return {
    kind: 'send_schedule_picker',
    // This time is the only thing the user must verify: "before 3 pm" gets parsed as 15:00.
    whenText: textOf(data.when_text, ''),
    scheduledAt: textOf(data.scheduled_at, ''),
    minScheduledAt: textOf(data.min_scheduled_at, ''),
    sessionId: textOf(data.session_id, ''),
    batches,
    defaultCampaignId: numberOf(data.default_campaign_id, batches.length ? batches[batches.length - 1].campaignId : 0),
    totalLeads: numberOf(data.total_leads, batches.reduce((sum, b) => sum + b.leadCount, 0)),
    timeWarning: textOf(data.time_warning, ''),
  };
}

export function salesAgentCardToA2UI(card: SalesAgentCard): A2UIReportPayload | null {
  const tool = card.tool || '';
  const backendPayload = backendCardToA2UI(card);
  if (backendPayload) return backendPayload;
  if (isA2UIReportCard(card)) {
    const report = normalizeReport(card);
    const surfaceId = `sales-report-${stableHash(JSON.stringify([tool, report.title, report.url, report.reportPage]))}`;
    return {
      kind: 'report',
      surfaceId,
      messages: reportMessages(surfaceId, report),
      report,
      title: report.title,
      raw: card,
      source: 'frontend',
      catalogId: basicCatalog.id,
    };
  }
  if (isA2UIGuidanceCard(card)) {
    const guidance = normalizeGuidance(card);
    const surfaceId = `sales-guidance-${stableHash(JSON.stringify([tool, guidance.question, guidance.missingItems]))}`;
    return {
      kind: 'guidance',
      surfaceId,
      messages: guidanceMessages(surfaceId, guidance),
      report: emptyReport(),
      guidance,
      title: guidance.title,
      raw: card,
      source: 'frontend',
      catalogId: basicCatalog.id,
    };
  }
  if (isA2UIRecommendationsCard(card)) {
    const recommendations = normalizeRecommendations(card);
    const surfaceId = `sales-recommendations-${stableHash(JSON.stringify([tool, recommendations.title, recommendations.subtitle, recommendations.items]))}`;
    return {
      kind: 'recommendations',
      surfaceId,
      messages: recommendationMessages(surfaceId, recommendations),
      report: emptyReport(),
      recommendations,
      title: recommendations.title,
      raw: card,
      source: 'frontend',
      catalogId: basicCatalog.id,
    };
  }
  if (isA2UISendScheduleCard(card)) {
    const sendSchedule = normalizeSendSchedule(card);
    const surfaceId = `sales-send-schedule-${stableHash(JSON.stringify([sendSchedule.scheduleId, sendSchedule.status, sendSchedule.whenText, sendSchedule.included]))}`;
    return {
      kind: 'send_schedule',
      surfaceId,
      messages: sendScheduleMessages(surfaceId, sendSchedule),
      report: emptyReport(),
      sendSchedule,
      title: sendSchedule.title,
      raw: card,
      source: 'frontend',
      catalogId: basicCatalog.id,
    };
  }
  return null;
}

export function canRenderWithA2UI(card?: SalesAgentCard | null): boolean {
  return !!(card && salesAgentCardToA2UI(card));
}
