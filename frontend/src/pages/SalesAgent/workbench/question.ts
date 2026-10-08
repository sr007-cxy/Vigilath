// Option normalization, delete/edit checks and answered-value backfill for questionnaire cards.
import { type SalesAgentMessage, type SalesAgentOptionKind, type SalesAgentQuestionCard, type SalesAgentQuestionOption } from '../../../services/salesAgentApi';
import { asList, asRecord, boolOf, jsonArrayTextParts, numberOf, textOf, uniqueTexts } from './utils';

export function answerParts(content: string): string[] {
  const jsonParts = jsonArrayTextParts(content);
  if (jsonParts.length) return jsonParts;
  // Aligned with backend _list_from_text: don't split on `/`, otherwise 「官网建设/优化」 ("website building/optimization") gets broken apart and history echo won't match.
  return (content || '').split(/[、,，;；\n\r]+/).map(x => x.trim()).filter(Boolean);
}

export function normalizeChoiceText(value: unknown): string {
  return String(value || '').replace(/\s+/g, ' ').replace(/\s+desc\s*$/i, '').trim();
}

export function normalizeChoiceValues(values: unknown[]): string[] {
  return uniqueTexts(values.map(normalizeChoiceText).filter(Boolean));
}

export function normalizeQuestionOptions(options: SalesAgentQuestionOption[]): SalesAgentQuestionOption[] {
  const seen = new Set<string>();
  const normalized: SalesAgentQuestionOption[] = [];
  options.forEach(opt => {
    const label = normalizeChoiceText(opt.label || opt.value);
    const value = normalizeChoiceText(opt.value || opt.label);
    if (!label || !value) return;
    const key = value.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    normalized.push({
      ...opt,
      label,
      value,
      desc: normalizeChoiceText(opt.desc),
      input_placeholder: normalizeChoiceText(opt.input_placeholder),
    });
  });
  return normalized;
}

export function isTargetIndustryQuestion(question: SalesAgentQuestionCard): boolean {
  return question.id === 'campaign_target_industries' || question.field === 'target_industries';
}

export function isTargetRegionQuestion(question: SalesAgentQuestionCard): boolean {
  return question.id === 'campaign_target_regions' || question.field === 'target_regions';
}

export function isNoLimitQuestionOption(option: SalesAgentQuestionOption): boolean {
  const value = normalizeChoiceText(option.value || option.label);
  const label = normalizeChoiceText(option.label || option.value);
  return ['不限规模', '不限行业', '不限地区'].includes(value) || ['不限规模', '不限行业', '不限地区'].includes(label);
}

export function isCustomIndustryActionOption(option: SalesAgentQuestionOption): boolean {
  const value = normalizeChoiceText(option.value || option.label);
  const label = normalizeChoiceText(option.label || option.value);
  return ['自定义行业', '其它行业', '其他行业'].includes(value) || ['自定义行业', '其它行业', '其他行业'].includes(label);
}

export function optionKindForQuestion(question: SalesAgentQuestionCard): SalesAgentOptionKind | '' {
  if (question.field === 'seller_capabilities' || question.id === 'seller_profile') return 'capability';
  if (question.field === 'target_industries' || question.id === 'campaign_target_industries') return 'industry';
  if (question.field === 'target_regions' || question.id === 'campaign_target_regions') return 'region';
  if (question.field === 'typical_customers' || question.id === 'seller_typical_customers') return 'typical_customer';
  return '';
}

export function sellerProfileIdForOption(question: SalesAgentQuestionCard, option?: SalesAgentQuestionOption): number | null {
  return option?.seller_profile_id || question.seller_profile_id || null;
}

export function canDeleteQuestionOption(question: SalesAgentQuestionCard, option: SalesAgentQuestionOption): boolean {
  const kind = optionKindForQuestion(question);
  if (!kind || kind === 'company_size') return false;
  if (isNoLimitQuestionOption(option) || option.input_placeholder) return false;
  const sellerProfileId = sellerProfileIdForOption(question, option);
  if (!sellerProfileId) return false;
  // The persona's "service capabilities / suitable clients" come from brand materials or the saved persona; old cards may lack the editable field;
  // as long as the current persona is known, allow deletion and let the backend sync the persona.
  if (kind === 'capability' || kind === 'typical_customer') return true;
  // In target industry/region, some custom values are filled in by the backend echoing selected_values,
  // with no catalog row id / editable, but source will be custom. Allow deleting them via by-value archival,
  // and even if the DB has no matching row they're removed locally from the current card.
  return option.editable === true || option.source === 'custom';
}

export function orderQuestionOptions(question: SalesAgentQuestionCard, options: SalesAgentQuestionOption[]): SalesAgentQuestionOption[] {
  const normalized = normalizeQuestionOptions(options).filter(opt => !(isTargetIndustryQuestion(question) && isCustomIndustryActionOption(opt)));
  const pinnedLabel = isTargetIndustryQuestion(question) ? '不限行业' : isTargetRegionQuestion(question) ? '不限地区' : '';
  if (!pinnedLabel) return normalized;
  const pinned = normalized.filter(opt => opt.value === pinnedLabel || opt.label === pinnedLabel);
  if (!pinned.length) return normalized;
  const rest = normalized.filter(opt => opt.value !== pinnedLabel && opt.label !== pinnedLabel);
  return [...pinned, ...rest];
}

export function isTypicalCustomersQuestion(question: SalesAgentQuestionCard): boolean {
  return question.id === 'seller_typical_customers' || question.field === 'typical_customers';
}

export function submittedValuesForQuestion(question: SalesAgentQuestionCard, submittedText = ''): string[] {
  const selectedParts = normalizeChoiceValues(question.selected_values || []);
  const submittedParts = normalizeChoiceValues(answerParts(submittedText));
  if (!selectedParts.length && !submittedParts.length) return [];
  const options = normalizeQuestionOptions(question.options || []);
  const byText = new Map<string, string>();
  options.forEach(opt => {
    byText.set(opt.value, opt.value);
    byText.set(opt.label, opt.value);
  });
  const normalize = (part: string) => byText.get(normalizeChoiceText(part)) || normalizeChoiceText(part);
  // selected_values are the real selections returned by the backend and must be kept intact; "suitable clients" in particular uses array elements as boundaries,
  // so full phrases must not be split into fragments from the display text again. nextUserAnswer is only a historical fallback.
  if (selectedParts.length) return normalizeChoiceValues(selectedParts.map(normalize));
  const submittedMatched = submittedParts.map(part => byText.get(part)).filter(Boolean) as string[];
  const submittedCustom = submittedParts.filter(part => !byText.get(part));
  return submittedMatched.length ? normalizeChoiceValues(submittedMatched) : normalizeChoiceValues(submittedCustom);
}

export function submittedSecondaryValuesForQuestion(question: SalesAgentQuestionCard, submittedText = ''): string[] {
  const options = question.secondary_options || [];
  if (!options.length) return [];
  const parts = uniqueTexts([...(question.secondary_selected_values || []).map(x => String(x || '').trim()), ...answerParts(submittedText)]);
  const byText = new Map<string, string>();
  options.forEach(opt => {
    byText.set(opt.value.trim(), opt.value);
    byText.set(opt.label.trim(), opt.value);
  });
  for (const part of parts) {
    const direct = byText.get(part);
    if (direct) return [direct];
    const digits = part.match(/\d+/)?.[0];
    if (!digits) continue;
    const matched = options.find(opt => opt.value.includes(digits) || opt.label.includes(digits));
    if (matched) return [matched.value];
  }
  return [];
}

export function nextUserAnswer(messages: SalesAgentMessage[], index: number): string {
  for (let i = index + 1; i < messages.length; i += 1) {
    const msg = messages[i];
    if (msg.role === 'user') return (msg.content || '').trim();
    if (msg.role === 'assistant') break;
  }
  return '';
}

export function questionCardOf(data: Record<string, unknown>): SalesAgentQuestionCard | undefined {
  const card = asRecord(data.question_card);
  const title = textOf(card.title, '');
  if (!title) return undefined;
  const options = normalizeQuestionOptions(asList<Record<string, unknown>>(card.options).map(opt => ({
    id: opt.id == null ? null : numberOf(opt.id, 0) || null,
    kind: textOf(opt.kind, ''),
    label: textOf(opt.label || opt.value, ''),
    value: textOf(opt.value || opt.label, ''),
    desc: textOf(opt.desc, ''),
    source: textOf(opt.source, ''),
    seller_profile_id: opt.seller_profile_id == null ? null : numberOf(opt.seller_profile_id, 0) || null,
    editable: boolOf(opt.editable, false),
    input_placeholder: textOf(opt.input_placeholder, ''),
  })).filter(opt => opt.label && opt.value));
  const secondaryOptions = normalizeQuestionOptions(asList<Record<string, unknown>>(card.secondary_options).map(opt => ({
    label: textOf(opt.label || opt.value, ''),
    value: textOf(opt.value || opt.label, ''),
    desc: textOf(opt.desc, ''),
    source: textOf(opt.source, ''),
  })).filter(opt => opt.label && opt.value));
  return {
    id: textOf(card.id, ''),
    field: textOf(card.field, ''),
    title,
    helper_text: textOf(card.helper_text, ''),
    type: textOf(card.type, 'text_suggestions'),
    layout: textOf(card.layout, ''),
    options,
    selected_values: normalizeChoiceValues(asList(card.selected_values)),
    seller_profile_id: card.seller_profile_id == null ? null : numberOf(card.seller_profile_id, 0) || null,
    secondary_title: textOf(card.secondary_title, ''),
    secondary_helper_text: textOf(card.secondary_helper_text, ''),
    secondary_field: textOf(card.secondary_field, ''),
    secondary_options: secondaryOptions,
    secondary_selected_values: normalizeChoiceValues(asList(card.secondary_selected_values)),
    secondary_default_value: normalizeChoiceText(card.secondary_default_value),
    allow_text_fallback: boolOf(card.allow_text_fallback, true),
    allow_custom_option: boolOf(card.allow_custom_option, false),
    custom_option_placeholder: textOf(card.custom_option_placeholder, ''),
    custom_option_max: numberOf(card.custom_option_max, 20),
    can_go_back: boolOf(card.can_go_back, true),
  };
}
