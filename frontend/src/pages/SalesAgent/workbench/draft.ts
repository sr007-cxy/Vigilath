// Email/SMS drafts: parsing of letter types, body cleanup, delivery records and confirmation items.
import { type CSSProperties } from 'react';
import { type ReportEmailStyleKey, type SalesAgentCard, type SalesAgentCompany, type SalesAgentContact, type SalesAgentDraft, type SalesAgentDraftDelivery, type SalesAgentLead } from '../../../services/salesAgentApi';
import { type ConfirmDraftItem } from './types';
import { EMAIL_DRAFT_FOOTER_PATTERNS, NO_REPORT_EMAIL_STYLES, REPORT_EMAIL_STYLES } from './constants';
import { asList, asRecord, boolOf, fmtTime, isTodayTime, numberOf, sanitizeEmailHtml, textOf } from './utils';

export function normalizeReportEmailStyleKey(styleKey: unknown): ReportEmailStyleKey {
  const key = String(styleKey || '').trim().toLowerCase().replace(/-/g, '_');
  return REPORT_EMAIL_STYLES.some(s => s.key === key) ? key : 'leijun_pitch';
}

export function normalizeNoReportEmailStyleKey(styleKey: unknown): ReportEmailStyleKey {
  const key = String(styleKey || '').trim().toLowerCase().replace(/-/g, '_');
  return NO_REPORT_EMAIL_STYLES.some(s => s.key === key) ? key : 'standard';
}

export function isNoReportEmailStyleKey(styleKey: unknown): boolean {
  return NO_REPORT_EMAIL_STYLES.some(s => s.key === String(styleKey || '').trim().toLowerCase().replace(/-/g, '_'));
}

/** The literals of the two key spaces don't overlap, so normalize by **membership** without first knowing which path this letter takes. */
export function normalizeEmailStyleKey(styleKey: unknown): ReportEmailStyleKey {
  return isNoReportEmailStyleKey(styleKey) ? normalizeNoReportEmailStyleKey(styleKey) : normalizeReportEmailStyleKey(styleKey);
}

/** For this letter type the middle section is model-written and can stream as it's written. `standard` is pure concatenation with nothing to stream. */
export function isStreamableIntroStyleKey(styleKey: unknown): boolean {
  return isNoReportEmailStyleKey(styleKey) && normalizeNoReportEmailStyleKey(styleKey) !== 'standard';
}

export function reportEmailVariantCount(styleKey: ReportEmailStyleKey): number {
  // Intro letters generate only the selected type at a time -- variation comes from the type, not from multiple candidates of the same type.
  if (isNoReportEmailStyleKey(styleKey)) return 1;
  return REPORT_EMAIL_STYLES.find(s => s.key === normalizeReportEmailStyleKey(styleKey))?.variants ?? 1;
}

export function cleanEmailDraftContent(content: string, channel = 'email'): string {
  let text = (content || '').trim();
  if (channel !== 'email') return text;
  let previous = '';
  while (text && text !== previous) {
    previous = text;
    EMAIL_DRAFT_FOOTER_PATTERNS.forEach(pattern => { text = text.replace(pattern, '').trim(); });
  }
  return text;
}

export function draftHtmlContent(draft: SalesAgentDraft): string {
  return sanitizeEmailHtml(asRecord(draft.metadata).html_content);
}

export function isNoReportDefaultDraft(draft: SalesAgentDraft): boolean {
  const metadata = asRecord(draft.metadata);
  return metadata.kind === 'no_report_default_email' || metadata.template_mode === 'no_report_default_pitch';
}

export function contactFromRecord(raw: Record<string, unknown>, companyId = 0): SalesAgentContact | null {
  const email = textOf(raw.email, '');
  const phone = textOf(raw.phone, '');
  const form = textOf(raw.contact_form_url, '');
  if (!numberOf(raw.id, 0) && !email && !phone && !form) return null;
  return {
    id: numberOf(raw.id, 0),
    company_id: numberOf(raw.company_id, companyId),
    contact_name: textOf(raw.contact_name, ''),
    position: textOf(raw.position, ''),
    email,
    phone,
    contact_form_url: form,
    source: textOf(raw.source, ''),
    source_url: textOf(raw.source_url, ''),
    confidence: textOf(raw.confidence, ''),
    is_reachable: boolOf(raw.is_reachable, true),
    consent_status: textOf(raw.consent_status, 'unknown'),
    captured_at: textOf(raw.captured_at, ''),
  };
}

export function companyFromDraftPayload(data: Record<string, unknown>, contact: SalesAgentContact | null): SalesAgentCompany | null {
  const raw = asRecord(data.company);
  const companyId = numberOf(raw.id, contact?.company_id ?? 0);
  const name = textOf(raw.name, '');
  if (!companyId && !name) return null;
  const rawContacts = asList<Record<string, unknown>>(raw.contacts)
    .map(c => contactFromRecord(c, companyId))
    .filter(Boolean) as SalesAgentContact[];
  const contacts = contact ? [contact] : rawContacts;
  return {
    id: companyId,
    name: name || `企业 #${companyId}`,
    industry: textOf(raw.industry, ''),
    region: textOf(raw.region, ''),
    website: textOf(raw.website, ''),
    employee_size: textOf(raw.employee_size, ''),
    source: textOf(raw.source, ''),
    status: textOf(raw.status, ''),
    email: contact?.email || textOf(raw.email, ''),
    phone: contact?.phone || textOf(raw.phone, ''),
    contact_form_url: contact?.contact_form_url || textOf(raw.contact_form_url, ''),
    contacts,
  };
}

export function emailStatusInfo(draft?: SalesAgentDraft | null): { label: string; tone: 'neutral' | 'success' | 'warning' | 'danger' } | null {
  if (!draft) return null;
  const delivery = draft.delivery || null;
  const status = (delivery?.status || draft.send_status || '').trim();
  const sentAt = delivery?.sent_at || '';
  const when = sentAt ? fmtTime(sentAt) : (delivery?.failed_at ? fmtTime(delivery.failed_at) : '');
  const isSms = draft.channel === 'sms';
  if (status === 'sent') return { label: `${isTodayTime(sentAt) ? '今日已发送' : '已发送'}${when ? ' · ' + when : ''}`, tone: 'success' };
  // delivered exists only for SMS: sent and carrier-receipt-confirmed delivered are two different things
  if (status === 'delivered') return { label: `已送达${delivery?.delivered_at ? ' · ' + fmtTime(delivery.delivered_at) : ''}`, tone: 'success' };
  if (status === 'failed') return { label: `发送失败${when ? ' · ' + when : ''}`, tone: 'danger' };
  // rejected means blocked locally (rate limit/missing template/no consent), the gateway was never actually called; shown separately from failed
  if (status === 'rejected') return { label: '已拦截未发送', tone: 'danger' };
  if (status === 'disabled') return { label: '发送关闭', tone: 'warning' };
  if (status === 'no_recipient') return { label: isSms ? '无可发号码' : '无收件人', tone: 'warning' };
  if (status === 'sending') return { label: '发送中', tone: 'warning' };
  if (status === 'pending_approval') return { label: '待确认发送', tone: 'warning' };
  if (status === 'draft') return { label: '草稿未发送', tone: 'neutral' };
  return status ? { label: status, tone: 'neutral' } : null;
}

export function emailStatusToneStyle(tone: 'neutral' | 'success' | 'warning' | 'danger'): CSSProperties {
  if (tone === 'success') return { color: '#15803d', background: 'rgba(34,197,94,.10)', borderColor: 'rgba(34,197,94,.28)' };
  if (tone === 'danger') return { color: 'var(--danger-fg)', background: 'var(--danger-bg)', borderColor: 'var(--danger-border)' };
  if (tone === 'warning') return { color: 'var(--warn-fg)', background: 'var(--warn-bg)', borderColor: 'var(--warn-border)' };
  return { color: 'var(--text-muted)', background: 'var(--bg-surface)', borderColor: 'var(--border-color)' };
}

/**
 * Delivery records attached to a draft. The backend gives different tables per draft channel: email rows for email drafts, sms rows for SMS drafts,
 * and only half the fields overlap. Here both sets of fields are read at once -- the missing half is empty,
 * and the draft card picks what to show by channel, one less layer of branching than writing a parser at each call site.
 */
export function deliveryFromRecord(raw: Record<string, unknown>): SalesAgentDraftDelivery | null {
  const id = numberOf(raw.id, 0);
  if (!id) return null;
  return {
    id,
    user_id: numberOf(raw.user_id, 0),
    lead_id: numberOf(raw.lead_id, 0) || null,
    company_id: numberOf(raw.company_id, 0) || null,
    contact_id: numberOf(raw.contact_id, 0) || null,
    outreach_log_id: numberOf(raw.outreach_log_id, 0) || null,
    email_account_id: numberOf(raw.email_account_id, 0) || null,
    report_share_id: numberOf(raw.report_share_id, 0) || null,
    message_type: textOf(raw.message_type, ''),
    provider_type: textOf(raw.provider_type, ''),
    provider_message_id: textOf(raw.provider_message_id, ''),
    from_email: textOf(raw.from_email, ''),
    from_name: textOf(raw.from_name, ''),
    reply_to_email: textOf(raw.reply_to_email, ''),
    recipient_email: textOf(raw.recipient_email, ''),
    recipient_name: textOf(raw.recipient_name, ''),
    subject: textOf(raw.subject, ''),
    content_preview: textOf(raw.content_preview, ''),
    status: textOf(raw.status, ''),
    failure_reason: textOf(raw.failure_reason, ''),
    send_attempted_at: textOf(raw.send_attempted_at, ''),
    sent_at: textOf(raw.sent_at, ''),
    failed_at: textOf(raw.failed_at, ''),
    open_count: numberOf(raw.open_count, 0),
    report_visit_count: numberOf(raw.report_visit_count, 0),
    read_at: textOf(raw.read_at, ''),
    company_name: textOf(raw.company_name, ''),
    // The following are unique to SMS rows: channel, target number, billed segments, clicks/replies/unsubscribes
    channel: textOf(raw.channel, '') === 'sms' ? 'sms' : undefined,
    provider: textOf(raw.provider, ''),
    sign_name: textOf(raw.sign_name, ''),
    template_code: textOf(raw.template_code, ''),
    recipient_phone: textOf(raw.recipient_phone, ''),
    recipient_country: textOf(raw.recipient_country, ''),
    segments: numberOf(raw.segments, 0),
    delivered_at: textOf(raw.delivered_at, ''),
    failure_code: textOf(raw.failure_code, ''),
    click_count: numberOf(raw.click_count, 0),
    reply_count: numberOf(raw.reply_count, 0),
    opted_out_at: textOf(raw.opted_out_at, ''),
  };
}

export function draftFromRecord(raw: Record<string, unknown>): SalesAgentDraft | null {
  const id = numberOf(raw.id, 0);
  const leadId = numberOf(raw.lead_id, 0);
  const channel = textOf(raw.channel, 'email');
  const content = cleanEmailDraftContent(textOf(raw.content, ''), channel);
  if (!id || !leadId || !content) return null;
  const delivery = deliveryFromRecord(asRecord(raw.delivery));
  return {
    id,
    lead_id: leadId,
    channel,
    subject: textOf(raw.subject, ''),
    content,
    send_status: textOf(raw.send_status, 'draft'),
    requires_approval: boolOf(raw.requires_approval, true),
    has_unsubscribe: boolOf(raw.has_unsubscribe, false),
    metadata: asRecord(raw.metadata),
    delivery,
    recipient_email: textOf(raw.recipient_email, delivery?.recipient_email || ''),
    recipient_phone: textOf(raw.recipient_phone, delivery?.recipient_phone || ''),
    from_email: textOf(raw.from_email, delivery?.from_email || ''),
    failure_reason: textOf(raw.failure_reason, delivery?.failure_reason || ''),
    read_at: textOf(raw.read_at, delivery?.read_at || ''),
    created_at: textOf(raw.created_at, ''),
  };
}

export function confirmDraftItemsFromCards(cards: SalesAgentCard[]): ConfirmDraftItem[] {
  return cards.flatMap(card => {
    if (!/generate_outreach_copy/i.test(card.tool || '')) return [];
    const data = asRecord(card.data);
    if (data.blocked) return [];
    const draft = draftFromRecord(asRecord(data.draft));
    if (!draft) return [];
    const variants = asList<Record<string, unknown>>(data.variants).map(v => draftFromRecord(v)).filter(Boolean) as SalesAgentDraft[];
    const allVariants = variants.length ? variants : [draft];
    const contact = contactFromRecord(asRecord(data.contact), numberOf(asRecord(data.company).id, 0));
    const company = companyFromDraftPayload(data, contact);
    const rawLead = asRecord(data.lead);
    const reportShare = asRecord(rawLead.report_share || data.report_share);
    const lead: SalesAgentLead = {
      id: draft.lead_id,
      company_id: numberOf(rawLead.company_id, company?.id ?? 0),
      company,
      profile_id: numberOf(rawLead.profile_id, 0) || null,
      score: numberOf(rawLead.score, 0),
      match_score: numberOf(rawLead.match_score, numberOf(rawLead.score, 0)),
      opportunity_score: rawLead.opportunity_score === null || rawLead.opportunity_score === undefined ? null : numberOf(rawLead.opportunity_score, 0),
      opportunity_score_label: textOf(rawLead.opportunity_score_label, ''),
      confidence: textOf(rawLead.confidence, ''),
      confidence_level: textOf(rawLead.confidence_level, ''),
      recommended_action: textOf(rawLead.recommended_action, ''),
      score_summary: textOf(rawLead.score_summary, ''),
      scoring: asRecord(rawLead.scoring),
      rank: numberOf(rawLead.rank, 0),
      reason: textOf(rawLead.reason, '已生成触达草稿'),
      status: textOf(rawLead.status, 'Draft Ready'),
      source_report_table: textOf(rawLead.source_report_table || reportShare.source_report_table, ''),
      source_report_id: textOf(rawLead.source_report_id || reportShare.source_report_id, ''),
      report_snapshot: asRecord(rawLead.report_snapshot),
      report_status: textOf(rawLead.report_status || reportShare.report_status, ''),
      report_id: textOf(rawLead.report_id || rawLead.source_report_id || reportShare.report_id || reportShare.source_report_id, ''),
      report_share: reportShare,
      public_report_url: textOf(rawLead.public_report_url || reportShare.public_url || data.public_report_url, ''),
      share_token: textOf(rawLead.share_token || reportShare.share_token || reportShare.public_token || data.share_token, ''),
      next_followup_at: textOf(rawLead.next_followup_at, ''),
    };
    return [{ draft, variants: allVariants, lead, raw: card, cached: boolOf(data.cached, false) }];
  });
}
