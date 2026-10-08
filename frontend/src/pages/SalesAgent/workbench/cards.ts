// Flatten backend tool_cards into the workbench preview cards: recommendations, reports, personas and historical messages.
import { type SalesAgentCard, type SalesAgentMessage, type SalesAgentSessionItem, type SalesAgentUndoTarget } from '../../../services/salesAgentApi';
import { type LeadReportState, type SalesAgentPreviewCard, type SalesAgentProfileSection } from './types';
import { REPORT_CAPABILITY_KEYWORDS, REPORT_CAPABILITY_NEGATION_RE, SALES_AGENT_RECOMMENDATION_LIMIT } from './constants';
import { asList, asRecord, boolOf, jsonArrayTextParts, looksLikeEmail, numberOf, shortText, textOf, uniqueTexts } from './utils';
import { questionCardOf } from './question';
import { draftFromRecord, emailStatusInfo } from './draft';

export function mergeSessionLists(primary: SalesAgentSessionItem[], extra: SalesAgentSessionItem[] = []): SalesAgentSessionItem[] {
  const seen = new Set<string>();
  const merged: SalesAgentSessionItem[] = [];
  for (const item of [...primary, ...extra]) {
    if (!item?.id || seen.has(item.id)) continue;
    seen.add(item.id);
    merged.push(item);
  }
  return merged;
}

export function thinkingStepsOf(msg: SalesAgentMessage): string[] {
  const raw = msg.metadata?.thinking_steps;
  if (Array.isArray(raw)) return raw.map(x => textOf(x, '')).filter(Boolean);
  const toolCards = msg.metadata?.tool_cards;
  if (Array.isArray(toolCards)) return toolThinkingSteps(toolCards.filter(Boolean) as SalesAgentCard[]);
  return [];
}

export function toolThinkingSteps(cards: SalesAgentCard[]): string[] {
  const names = cards.map(c => c.tool || '').join(' ');
  const steps: string[] = [];
  if (/generate_detection_report|summarize_report|report/i.test(names)) steps.push('读取真实检测报告');
  if (/build_customer_profile|profile|画像/i.test(names)) steps.push('整理筛选条件');
  if (/recommend_companies|discover_companies|rank_companies|company|lead/i.test(names)) steps.push('筛选并排序目标企业');
  if (/generate_outreach_copy|outreach|draft/i.test(names)) steps.push('生成合规洽谈草稿');
  if (/update_lead_status|status|follow/i.test(names)) steps.push('记录线索跟进状态');
  if (/compliance|risk/i.test(names)) steps.push('检查合规风险');
  return steps.length ? steps : ['整理处理结果'];
}

export function simplifyRecommendedAction(value: unknown): string {
  const text = textOf(value, '').trim();
  if (!text) return '';
  if (/先打开官网确认公司级邮箱、电话或联系表单/.test(text)) return '先确认公开联系方式。';
  if (/优先生成 AI-GEO 审计简报邮件/.test(text)) return '生成报告邮件，可引用真实报告。';
  if (/先补充官网或公开渠道/.test(text)) return '先补官网，再诊断触达。';
  if (/先检测官网或补充公开证据/.test(text)) return '先补证据或检测官网。';
  if (/正文使用待审计确认的审慎措辞/.test(text)) return '生成报告邮件，用审慎措辞。';
  return shortText(text, 34);
}

export function simplifyScoreSummary(value: unknown): string {
  const text = textOf(value, '').trim();
  if (!text) return '';
  if (/匹配度用于推荐排序|机会分仅按公开官网/.test(text)) return '匹配度排优先级；机会分为保守估算。';
  return shortText(text, 42);
}

export function simplifyRecommendationReason(value: unknown): string {
  const text = textOf(value, '').trim();
  if (!text) return '';
  const isFinal = /最终推荐/.test(text);
  const isInitial = /初步推荐/.test(text) || !isFinal;
  const parts: string[] = [];
  if (/目标行业|行业与 ICP/.test(text)) parts.push('符合目标行业');
  if (/目标地区|地区与 ICP/.test(text)) parts.push('符合目标地区');
  if (/有官网/.test(text)) parts.push('有官网可诊断');
  const service = text.match(/(?:用户服务范围)?[“"]([^”"]+)[”"]/);
  if (service?.[1]) parts.push(`可结合${service[1]}切入`);
  if (/真实报告匹配|可做最终推荐/.test(text) && !/不匹配|未关联|无真实报告/.test(text)) parts.push('可引用真实报告');
  if (/未关联|无真实报告|未使用该企业检测结论|不得声称|不引用检测结论|报告不匹配/.test(text)) parts.push('不引用检测结论');
  const unique = [...new Set(parts)];
  if (unique.length) return `${isFinal ? '最终推荐' : isInitial ? '初步推荐' : '推荐依据'}：${unique.join('，')}。`;
  return shortText(text, 64);
}

export function userFacingActionText(content: string): string {
  const text = (content || '').trim();
  const jsonParts = jsonArrayTextParts(text);
  if (jsonParts.length) return jsonParts.join('、');
  if (/^给线索\s*\d+\s*生成邮件$/.test(text)) return '生成邮件';
  if (/^给.{1,80}生成邮件$/.test(text)) return '生成邮件';
  return content;
}

export function isReportCard(card?: SalesAgentCard | null): card is SalesAgentCard {
  return !!card && /summarize_report|generate_detection_report/i.test(card.tool || '');
}

export function pickReportValue(data: Record<string, unknown>, summary: Record<string, unknown>, keys: string[]): unknown {
  for (const key of keys) {
    const direct = data[key];
    if (direct !== null && direct !== undefined && direct !== '') return direct;
    const nested = summary[key];
    if (nested !== null && nested !== undefined && nested !== '') return nested;
  }
  return undefined;
}

export function reportTextList(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(x => shortText(x, 180)).filter(Boolean);
  const text = textOf(value, '').trim();
  return text ? [text] : [];
}

/** That column is only minmax(92px, .55fr) and can't fit the full description -- the short one goes in the cell, the full one in title. */
export function reportProgress(state?: LeadReportState): { label: string; title: string } {
  const done = numberOf(state?.done, 0);
  const total = numberOf(state?.total, 0);
  const seconds = numberOf(state?.elapsedSeconds, 0);
  const minutes = Math.floor(seconds / 60);

  // After splitting into two lines there's a full row width here, so the full description fits (no longer just a number)
  const parts = ['诊断中'];
  if (total) parts.push(`${done}/${total}`);
  if (state?.currentStep) parts.push(state.currentStep);
  if (seconds >= 60) parts.push(`已跑 ${minutes} 分钟`);
  else if (!total) parts.push('约 1–2 分钟');
  const label = parts.join(' · ');

  const full = ['诊断进行中'];
  if (total) full.push(`已完成 ${done}/${total} 项`);
  if (state?.currentStep) full.push(`当前:${state.currentStep}`);
  full.push(seconds >= 60 ? `已跑 ${minutes} 分钟` : '刚开始');
  if (!total) full.push('轻量检测约 1–2 分钟');
  return { label, title: full.join(' · ') };
}


export function reportStatusLabel(status: string): string {
  const map: Record<string, string> = { generating: '检测中', ready: '已完成', failed: '失败', none: '尚未检测' };
  return map[status] || status || '—';
}

export function reportPageTarget(url: string): { target?: string; rel?: string } {
  return /^https?:\/\//i.test(url) ? { target: '_blank', rel: 'noreferrer' } : {};
}

export function recommendationData(data: Record<string, unknown>): Record<string, unknown> {
  return asRecord(data.ranking).leads ? asRecord(data.ranking) : data;
}

export function undoTargetOf(data: Record<string, unknown>): SalesAgentUndoTarget | null {
  const raw = asRecord(data.undo_checkpoint);
  const checkpointId = numberOf(raw.checkpoint_id, 0);
  if (!checkpointId) return null;
  return {
    checkpoint_id: checkpointId,
    step: textOf(raw.step, ''),
    label: textOf(raw.label, ''),
  };
}

export function recommendationMeta(data: Record<string, unknown>, leadsLength: number): string {
  const requested = numberOf(data.requested_limit, numberOf(data.count, leadsLength));
  const actual = numberOf(data.actual_count, numberOf(data.count, leadsLength));
  const shortfall = numberOf(data.shortfall, Math.max(0, requested - actual));
  return `请求 ${requested} 家 / 实际 ${actual} 家 / 缺口 ${shortfall} 家`;
}

// Entry action cards (the two shortcuts offered when greeting) aren't required questions and can't take on the "more info needed" shell.
export function isEntryActionCard(data: Record<string, unknown>): boolean {
  const q = asRecord(data.question_card);
  return textOf(q.id, '') === 'sales_agent_entry_action';
}

export function recommendationStatus(data: Record<string, unknown>): string {
  if (isEntryActionCard(data)) return '可选';
  if (data.needs_user_input || data.status === 'need_more_info') return '需要补充信息';
  const mode = textOf(data.mode, 'initial');
  const finalCount = numberOf(data.final_count, 0);
  const initialCount = numberOf(data.initial_count, numberOf(data.count, 0));
  if (mode === 'final') return '最终推荐';
  if (mode === 'mixed') return `初步 ${initialCount} / 最终 ${finalCount}`;
  return '初步推荐';
}

export function guidanceQuestionText(data: Record<string, unknown>): string {
  const direct = textOf(data.next_question, '').trim();
  if (direct) return direct;
  const [firstQuestion] = asList(data.questions).map(x => textOf(x, '')).filter(Boolean);
  if (firstQuestion) return firstQuestion;
  return textOf(data.message, '这次新对话里我还不了解你的业务情况。为了推荐得更准,先确认一个信息：这次你主要想推广什么服务或产品?');
}

export function recommendationDesc(data: Record<string, unknown>): string {
  // When info is missing the backend already gives only one next_question; the fallback text here must also keep "one question per round".
  if (data.needs_user_input || data.status === 'need_more_info') return guidanceQuestionText(data);
  const missing = asList(data.missing_config).map(x => textOf(x, '')).filter(Boolean);
  if (data.search_available === false) return `公网搜索未启用,无法自动扩展候选企业${missing.length ? `:${missing.join('、')}` : ''}。`;
  if (numberOf(data.shortfall, 0) > 0) return '已按真实来源返回当前可用企业,未补足部分不会编造。';
  return '';
}

export function publicContactText(company: Record<string, unknown>): string {
  const contacts = asList<Record<string, unknown>>(company.contacts);
  const values: string[] = [];
  let forms = 0;
  const add = (v: unknown) => {
    const text = textOf(v, '').trim();
    if (text && !values.includes(text)) values.push(text);
  };
  contacts.forEach(c => {
    if (['unsubscribed', 'blacklisted', 'do_not_contact'].includes(textOf(c.consent_status, ''))) return;
    add(c.email);
    add(c.phone);
    if (textOf(c.contact_form_url, '').trim()) forms += 1;
  });
  if (!values.length && !forms) {
    add(company.email);
    add(company.phone);
    if (textOf(company.contact_form_url, '').trim()) forms += 1;
  }
  if (forms) values.push(forms === 1 ? '官网联系表单' : `官网联系表单 ×${forms}`);
  return values.join('、') || '未发现公开公司级联系方式';
}

export function companyHasPublicEmail(company: Record<string, unknown>): boolean {
  if (looksLikeEmail(textOf(company.email, ''))) return true;
  return asList<Record<string, unknown>>(company.contacts).some(c => {
    if (['unsubscribed', 'blacklisted', 'do_not_contact'].includes(textOf(c.consent_status, ''))) return false;
    return looksLikeEmail(textOf(c.email, ''));
  });
}

export function recommendationTableRows(data: Record<string, unknown>, leads: Record<string, unknown>[]): { company: string; website: string; contact: string }[] {
  const direct = asList<Record<string, unknown>>(data.recommendation_table);
  if (direct.length) {
    return direct.slice(0, SALES_AGENT_RECOMMENDATION_LIMIT).map(row => ({
      company: textOf(row['企业'] || row.company || row.name, '企业'),
      website: textOf(row['官网'] || row.website, '-'),
      contact: textOf(row['公开联系方式'] || row.contact || row.public_contact, '未发现公开公司级联系方式'),
    }));
  }
  return leads.slice(0, SALES_AGENT_RECOMMENDATION_LIMIT).map(lead => {
    const company = asRecord(lead.company);
    return {
      company: textOf(company.name || lead.company_id, '企业'),
      website: textOf(company.website, '-').replace(/^https?:\/\/(www\.)?/i, '').replace(/\/$/, ''),
      contact: publicContactText(company),
    };
  });
}

export function profileTextsForReportCapability(profile: Record<string, unknown>): string[] {
  const values: string[] = [];
  const add = (value: unknown) => {
    if (Array.isArray(value)) value.forEach(add);
    else if (value && typeof value === 'object') Object.values(value as Record<string, unknown>).forEach(add);
    else {
      const text = textOf(value, '').trim();
      if (text) values.push(text);
    }
  };
  [
    'service_capabilities', 'service_scope', 'capabilities', 'services', 'features', 'service_features',
    'core_service_overview', 'core_business_lines', 'target_scenarios', 'description', 'summary',
  ].forEach(key => add(profile[key]));
  return values;
}

export function profileTextIsReportCapable(texts: string[]): boolean {
  const blob = texts.join(' ').toLowerCase();
  if (!blob.trim()) return false;
  const positiveBlob = blob.replace(REPORT_CAPABILITY_NEGATION_RE, '');
  return REPORT_CAPABILITY_KEYWORDS.some(word => positiveBlob.includes(word.toLowerCase()));
}

// Gating for the report entry: brands selling website building, restaurant franchising, or only GEO-TAG effect measurement/ROI reconciliation shouldn't see "Generate diagnosis report".
// The source of truth is the service capabilities in the brand materials (`ai_telemetry_topics.profile_json`), derived by the backend and sent over:
// the recommendation path carries it on `seller_profile.report_capable` (per brand), history queries at the top level (relaxed per account).
// Old message cards may have stored true computed from the old vocabulary, so the service text is checked again here: only explicit website audit/diagnosis report action words pass.
export function recommendationReportCapable(data: Record<string, unknown>): boolean {
  const seller = asRecord(data.seller_profile);
  const sellerCapabilityTexts = profileTextsForReportCapability(seller);
  if (typeof seller.report_capable === 'boolean') {
    return seller.report_capable && (!sellerCapabilityTexts.length || profileTextIsReportCapable(sellerCapabilityTexts));
  }
  if (typeof data.report_capable === 'boolean') {
    return data.report_capable && (!sellerCapabilityTexts.length || profileTextIsReportCapable(sellerCapabilityTexts));
  }
  return true;
}

export function profileSectionFromValue(label: string, value: unknown, limit = 4): SalesAgentProfileSection {
  const values = uniqueTexts(asList<unknown>(value).map(item => textOf(item, '')).filter(Boolean));
  if (typeof value === 'string' && !values.length) values.push(value.trim());
  const usable = values.filter(item => item && !item.includes('待补充'));
  if (!usable.length) return { label, values: ['待补充'], fullValues: [], empty: true };
  const clipped = usable.slice(0, limit);
  const overflow = usable.length - clipped.length;
  return { label, values: overflow > 0 ? [...clipped, `+${overflow} 项`] : clipped, fullValues: usable };
}

export function sellerProfileSections(data: Record<string, unknown>): SalesAgentProfileSection[] {
  // Our own persona confirmation always shows four dimensions: empty values show "To be completed" too, so users don't think the item is missing.
  return [
    profileSectionFromValue('专业能力', data.service_capabilities, 4),
    profileSectionFromValue('服务方向', data.service_scope, 4),
    profileSectionFromValue('企业规模', textOf(data.company_size, ''), 1),
    profileSectionFromValue('适合客户', data.typical_customers, 4),
  ];
}

export function sellerProfileDesc(data: Record<string, unknown>, sections: SalesAgentProfileSection[]): string {
  const missing = asList<unknown>(data.missing).map(item => textOf(item, '')).filter(Boolean);
  const readonly = boolOf(data.readonly, false);
  // The read-only persona card text is assembled by the backend per "what's missing / what to do next"; use it directly, don't assemble another --
  // a separate frontend copy would swallow the backend's guidance (completing the persona makes matching more accurate) entirely.
  const summary = textOf(data.summary_text || data.message, '');
  if (readonly && summary) return summary;
  if (missing.length) return readonly ? `画像信息待补充：${missing.join('、')}。可点击更新画像补齐。` : `画像信息待补充：${missing.join('、')}。`;
  if (sections.some(section => !section.empty)) return readonly ? '这是当前保存的我方画像。' : '已读取我方画像，请确认后继续。';
  return textOf(data.summary_text || data.message, '已根据已有信息组装我方画像。');
}

export function recommendationCompanyCards(data: Record<string, unknown>, leads: Record<string, unknown>[]): NonNullable<SalesAgentPreviewCard['companyCards']> {
  const rows = recommendationTableRows(data, leads);
  const directItems = asList<Record<string, unknown>>(asRecord(data.result_card).items);
  return rows.map((row, i) => {
    const lead = leads[i] || {};
    const direct = directItems[i] || {};
    const scoring = asRecord(direct.scoring || lead.scoring);
    const company = asRecord(lead.company);
    const reportShare = asRecord(direct.report_share || lead.report_share);
    const latestDraft = draftFromRecord(asRecord(direct.latest_report_email_draft || lead.latest_report_email_draft));
    const emailStatus = emailStatusInfo(latestDraft);
    const industry = textOf(company.industry, '');
    const region = textOf(company.region, '');
    const size = textOf(company.employee_size, '');
    const meta = [industry ? `目标行业：${industry}` : '', region ? `目标地区：${region}` : '', size ? `企业规模：${size}` : ''].filter(Boolean).join(' · ');
    return {
      ...row,
      meta: textOf(direct.meta, meta),
      reason: simplifyRecommendationReason(direct.reason || lead.reason),
      rank: textOf(direct.rank || lead.rank, String(i + 1)),
      score: textOf(direct.score || lead.score, ''),
      matchScore: textOf(direct.match_score ?? lead.match_score ?? scoring.match_score ?? direct.score ?? lead.score, ''),
      opportunityScore: textOf(direct.opportunity_score_label ?? lead.opportunity_score_label ?? scoring.opportunity_score_label ?? direct.opportunity_score ?? lead.opportunity_score ?? scoring.opportunity_score, '待评估'),
      confidence: textOf(direct.confidence_level ?? lead.confidence_level ?? scoring.confidence_level, '低'),
      recommendedAction: simplifyRecommendedAction(direct.recommended_action ?? lead.recommended_action ?? scoring.recommended_action),
      scoreSummary: simplifyScoreSummary(direct.score_summary ?? lead.score_summary ?? scoring.score_summary),
      leadId: textOf(direct.lead_id ?? lead.id, ''),
      companyId: textOf(direct.company_id ?? lead.company_id ?? company.id, ''),
      reportStatus: textOf(direct.report_status ?? lead.report_status ?? reportShare.report_status, ''),
      reportId: textOf(direct.report_id ?? lead.report_id ?? lead.source_report_id ?? reportShare.report_id, ''),
      publicReportUrl: textOf(direct.public_report_url ?? lead.public_report_url ?? reportShare.public_url, ''),
      shareToken: textOf(direct.share_token ?? lead.share_token ?? reportShare.share_token ?? reportShare.public_token, ''),
      latestReportEmailDraft: latestDraft,
      emailStatus: emailStatus?.label || '',
      emailStatusTone: emailStatus?.tone,
      hasEmail: boolOf(direct.has_email ?? lead.has_email, companyHasPublicEmail(company)) || looksLikeEmail(textOf(row.contact, '')),
      recommendedAt: textOf(direct.recommended_at ?? lead.recommended_at, ''),
    };
  });
}

export function previewCardsFromToolCards(cards: SalesAgentCard[]): SalesAgentPreviewCard[] {
  return cards.flatMap(card => {
    const data = asRecord(card.data);
    const tool = card.tool || '';
    if (/summarize_report|generate_detection_report/i.test(tool)) {
      const summary = asRecord(data.summary);
      const reportPage = textOf(data.report_page, '');
      const status = textOf(data.status || summary.status, '');
      const title = /generate_detection_report/i.test(tool) ? '诊断报告入口' : '历史报告摘要';
      return {
        kind: 'generic',
        title,
        meta: [reportPage ? '可打开完整页面' : '报告摘要', status].filter(Boolean).join(' · '),
        status,
        desc: shortText(data.hint || data.executive_summary || summary.executive_summary || data.summary_text || summary.summary_text || data.url || summary.url || reportPage, 150),
        items: reportPage ? [{ label: '入口', value: reportPage }] : undefined,
        raw: card,
      };
    }
    if (/rank_companies|recommend_companies|get_recommended_companies/i.test(tool)) {
      const ranking = recommendationData(data);
      const leads = asList<Record<string, unknown>>(ranking.leads);
      const needsInput = Boolean(ranking.needs_user_input || ranking.status === 'need_more_info');
      const qCard = needsInput ? questionCardOf(ranking) : undefined;
      const undoTarget = undoTargetOf(data);
      const title = isEntryActionCard(ranking) ? '你可以从这里开始'
        : needsInput ? '请先补充信息'
        // Looking up history and running a new batch use the same card (pagination/search/details all included); titles differ so it's clear whether this batch is newly recommended
        : /get_recommended_companies/i.test(tool) ? '历史推荐企业'
        : /recommend_companies/i.test(tool) ? '企业推荐预览' : '企业排序预览';
      return {
        kind: qCard ? 'question' : 'companies',
        title: qCard?.title || title,
        undoTarget,
        meta: needsInput ? '' : recommendationMeta(ranking, leads.length),
        status: recommendationStatus(ranking),
        desc: qCard?.helper_text || recommendationDesc(ranking),
        tableRows: [],
        companyCards: needsInput ? [] : recommendationCompanyCards(ranking, leads),
        items: [],
        questionCard: qCard,
        reportCapable: recommendationReportCapable(ranking),
        readonlyHistory: boolOf(ranking.readonly || ranking.disable_email_actions || ranking.disable_outreach_actions, false),
        raw: card,
      };
    }
    if (/discover_companies/i.test(tool)) {
      const companies = asList<Record<string, unknown>>(data.companies);
      return {
        kind: 'companies',
        title: '候选企业发现预览',
        meta: recommendationMeta(data, companies.length),
        status: textOf(data.discovery_mode, 'public_search'),
        desc: recommendationDesc(data) || textOf(data.source_policy, 'manual/public/authorized only'),
        items: companies.slice(0, SALES_AGENT_RECOMMENDATION_LIMIT).map((company, i) => ({
          label: `企业 ${i + 1}`,
          value: `${textOf(company.name, '企业')}｜${textOf(company.industry, '-')}｜${textOf(company.region, '-')}`,
        })),
        raw: card,
      };
    }
    if (/^send_schedule_batch_picker$/i.test(tool.trim())) {
      // The real interaction is in the modal. This card is just a placeholder + a reopen entry -- after accidentally closing the modal
      // users can click back without restating the sentence.
      const batches = asList<Record<string, unknown>>(data.batches);
      const totalLeads = numberOf(data.total_leads, batches.reduce((sum, b) => sum + numberOf(b.lead_count, 0), 0));
      return {
        kind: 'schedule',
        title: '定时发送 · 选择要发的批次',
        meta: [textOf(data.when_text, ''), batches.length > 1 ? `${batches.length} 批可选` : '', totalLeads ? `共 ${totalLeads} 家` : ''].filter(Boolean).join(' · '),
        desc: shortText(textOf(data.time_warning, '') || '选好批次点确定就排上,确定之前一封都不会发出去。', 160),
        raw: card,
      };
    }
    if (/schedule_outreach_send|confirm_scheduled_send|reschedule_send/i.test(tool)) {
      // A need_time response has no plan, only a "what time to send?". Drawing a scheduling card then would make users think it's already scheduled.
      if (data.need_time) {
        return { kind: 'generic', title: '定时发送 · 还差一个时间', desc: shortText(data.hint || data.message, 160), raw: card };
      }
      const schedule = asRecord(data.schedule);
      const status = textOf(schedule.status, 'draft');
      const armed = status === 'armed' || status === 'running' || status === 'done';
      const included = asList(data.included);
      const skipped = asList(data.skipped);
      const total = numberOf(schedule.total, included.length);
      return {
        kind: 'schedule',
        // "Scheduled" and "Pending confirmation" must be distinguishable at a glance -- between the two words lies "will the emails actually go out".
        title: armed ? '已排期' : '定时发送 · 待确认',
        meta: [textOf(schedule.scheduled_at_text, ''), total ? `${total} 家可发` : '', skipped.length ? `${skipped.length} 家跳过` : ''].filter(Boolean).join(' · '),
        status: textOf(schedule.status_text, ''),
        desc: shortText([textOf(data.time_warning, ''), textOf(data.hint, '')].filter(Boolean).join(' '), 200),
        raw: card,
      };
    }
    if (/generate_outreach_copy/i.test(tool)) {
      const draft = asRecord(data.draft);
      return {
        kind: 'draft',
        title: data.blocked ? '洽谈草稿已阻断' : '洽谈草稿预览',
        meta: textOf(draft.channel || data.decision, ''),
        status: data.blocked ? 'blocked' : 'requires approval',
        desc: shortText(draft.subject || draft.content || asList(data.reasons).join('；') || data.hint),
        raw: card,
      };
    }
    if (/sales_agent_seller_profile_(summary|snapshot)/i.test(tool)) {
      const undoTarget = undoTargetOf(data);
      const actions = asList<Record<string, unknown>>(data.actions).map(action => ({
        label: textOf(action.label, ''),
        text: textOf(action.text || action.value || action.label, ''),
      })).filter(action => action.label && action.text);
      const items = asList<Record<string, unknown>>(data.items).map(item => ({
        label: textOf(item.label, ''),
        value: textOf(item.value, ''),
      })).filter(item => item.label && item.value);
      const hideItems = data.hide_items === true;
      const profileSections = sellerProfileSections(data);
      return {
        kind: 'profile',
        title: textOf(data.title, '画像'),
        undoTarget,
        meta: textOf(data.source, '已组装画像'),
        desc: sellerProfileDesc(data, profileSections),
        profileSections,
        items: profileSections.length || hideItems ? [] : (items.length ? items : [
          { label: '专业能力', value: textOf(data.service_capabilities, '-') },
          { label: '服务范围', value: textOf(data.service_scope, '-') },
          { label: '企业规模', value: textOf(data.company_size, '-') },
        ]),
        actions,
        raw: card,
      };
    }
    if (/build_customer_profile/i.test(tool)) {
      return {
        kind: 'profile',
        title: '筛选条件预览',
        meta: textOf(data.company_size, ''),
        desc: `行业:${textOf(data.industries, '-')}；地区:${textOf(data.regions, '-')}`,
        items: [
          { label: '服务', value: textOf(data.service_scope, '-') },
          { label: '角色', value: textOf(data.buyer_roles, '-') },
        ],
        raw: card,
      };
    }
    return { kind: 'generic', title: '处理结果预览', desc: shortText(data, 160), raw: card };
  });
}

export function previewCardsOf(msg: SalesAgentMessage): SalesAgentPreviewCard[] {
  const toolCards = msg.metadata?.tool_cards;
  if (Array.isArray(toolCards)) return previewCardsFromToolCards(toolCards.filter(Boolean) as SalesAgentCard[]);
  const raw = msg.metadata?.preview_cards;
  if (Array.isArray(raw)) return raw.filter(Boolean) as SalesAgentPreviewCard[];
  return [];
}

export function normalizeChatMessages(rows: SalesAgentMessage[]): SalesAgentMessage[] {
  return rows
    .filter(m => m.role === 'user' || m.role === 'assistant')
    .filter(m => !(m.role === 'assistant' && /你好,我是\s*Vigilath\s*Sales Agent/i.test(m.content || '')))
    .map(m => {
      if (m.role === 'user') return { ...m, content: userFacingActionText(m.content) };
      const metadata = asRecord(m.metadata);
      const toolCards = asList<SalesAgentCard>(metadata.tool_cards);
      if (!toolCards.length) return m;
      return { ...m, metadata: { ...metadata, preview_cards: previewCardsFromToolCards(toolCards) } };
    });
}
