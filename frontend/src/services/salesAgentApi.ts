// Sales Agent — hidden workbench backend API (/api/sales-agent/*).
// chat streams via SSE: POST + ReadableStream (EventSource doesn't support a custom Authorization header).

import { localizedHeaders } from './apiError';
import type { DiagnosisNarrative, DiagnosisProgress, DiagnosisScores, DiagnosisSubResult } from './fullDiagnosisApi';

const API_BASE = (import.meta.env.VITE_API_URL as string) || '/api';
const SALES_AGENT_RECOMMENDATION_LIMIT = 100;

export type SalesAgentA2UIMessage = Record<string, unknown>;

export interface SalesAgentA2UIEnvelope {
  mime?: string;
  version?: string;
  kind?: string;
  source?: string;
  surfaceId?: string;
  catalogId?: string;
  report?: Record<string, unknown>;
  guidance?: Record<string, unknown>;
  recommendations?: Record<string, unknown>;
  sendSchedule?: Record<string, unknown>;
  model?: Record<string, unknown>;
  messages?: SalesAgentA2UIMessage[];
}

export interface SalesAgentUndoTarget {
  checkpoint_id: number;
  step?: string;
  label?: string;
}

export interface SalesAgentCard {
  tool: string;
  data: Record<string, unknown>;
  a2ui?: SalesAgentA2UIEnvelope;
}

export interface SalesAgentQuestionOption {
  id?: number | null;
  kind?: SalesAgentOptionKind | string;
  label: string;
  value: string;
  desc?: string;
  source?: 'system' | 'custom' | 'rule' | 'ai' | string;
  seller_profile_id?: number | null;
  editable?: boolean;
  // Carried by action items like "Custom industry": when selected, an input box opens in place inside the card,
  // and the typed sentence is submitted directly as this step's answer, without leaving for another round of questions
  input_placeholder?: string;
}

export interface SalesAgentQuestionCard {
  id?: string;
  field?: string;
  title: string;
  helper_text?: string;
  type?: 'single_select' | 'text_suggestions' | 'checkbox' | string;
  layout?: 'vertical' | 'horizontal' | string;
  options?: SalesAgentQuestionOption[];
  selected_values?: string[];
  seller_profile_id?: number | null;
  secondary_title?: string;
  secondary_helper_text?: string;
  secondary_field?: string;
  secondary_options?: SalesAgentQuestionOption[];
  secondary_selected_values?: string[];
  secondary_default_value?: string;
  allow_text_fallback?: boolean;
  // Add items in place inside the card: the input becomes a new selected option, without leaving the card or adding a conversation round
  allow_custom_option?: boolean;
  custom_option_placeholder?: string;
  custom_option_max?: number;
  can_go_back?: boolean;
}

export interface SalesAgentRecommendationResult {
  leads: SalesAgentLead[];
  count: number;
  actual_count?: number;
  requested_limit?: number;
  shortfall?: number;
  mode: string;
  final_count?: number;
  initial_count?: number;
  report_matched_count?: number;
  search_available?: boolean;
  missing_config?: string[];
  can_expand?: boolean;
  status?: string;
  discovery?: Record<string, unknown>;
  ranking?: Record<string, unknown>;
  recommendation_table?: Record<string, string>[];
  markdown_table?: string;
  table_columns?: string[];
  seller_profile?: SalesAgentSellerProfile | null;
  account_icp?: SalesAgentAccountICP | null;
  campaign?: SalesAgentRecommendationCampaign | null;
  evidence_summary?: SalesAgentLeadEvidence[];
  question_card?: SalesAgentQuestionCard;
}

export interface SalesAgentChatCallbacks {
  onDelta: (text: string) => void;
  onStage?: (stage: string) => void;
  onCards?: (cards: SalesAgentCard[]) => void;
  onDone?: (sessionId?: string) => void | Promise<void>;
  onError?: (msg: string) => void;
  signal?: AbortSignal;
}

export interface SalesAgentMessage {
  id?: number;
  role: 'user' | 'assistant' | 'tool';
  content: string;
  metadata?: Record<string, unknown>;
}

export interface SalesAgentSellerProfile {
  id?: number | null;
  legacy_profile_id?: number | null;
  name?: string;
  service_scope?: string[];
  service_capabilities?: string[];
  company_size?: string;
  typical_customers?: string[];
  proof_points?: string[];
  default_buyer_roles?: string[];
  default_exclude_rules?: string[];
  summary?: string;
  source_report_table?: string;
  source_report_id?: string;
}

export interface SalesAgentAccountICP {
  id?: number | null;
  seller_profile_id?: number | null;
  legacy_profile_id?: number | null;
  name?: string;
  target_company_types?: string[];
  target_industries?: string[];
  target_regions?: string[];
  target_company_size?: string;
  negative_icp?: string[];
  source?: string;
}

export interface SalesAgentBuyerPersona {
  id?: number | null;
  seller_profile_id?: number | null;
  account_icp_id?: number | null;
  role_name?: string;
  titles?: string[];
  decision_role?: string;
  goals?: string[];
  kpis?: string[];
  pains?: string[];
  objections?: string[];
  messaging_angles?: string[];
  proof_needed?: string[];
  content_preferences?: string[];
  priority?: number;
}

export interface SalesAgentRecommendationCampaign {
  id?: number | null;
  seller_profile_id?: number | null;
  account_icp_id?: number | null;
  legacy_profile_id?: number | null;
  target_industries?: string[];
  target_regions?: string[];
  target_company_size?: string;
  service_scope_override?: string[];
  limit?: number;
  status?: string;
  search_available?: boolean;
  requested_limit?: number;
  actual_count?: number;
  shortfall?: number;
  missing_config?: string[];
  source_report_table?: string;
  source_report_id?: string;
}

export interface SalesAgentLeadEvidence {
  id?: number | null;
  campaign_id?: number | null;
  lead_recommendation_id?: number | null;
  company_id?: number | null;
  evidence_type?: string;
  source_type?: string;
  source_url?: string;
  source_report_table?: string;
  source_report_id?: string;
  evidence_text?: string;
  scoring_category?: string;
  score_delta?: number;
  confidence?: string;
}

export interface SalesAgentProfile {
  id: number;
  service_scope: string[];
  industries: string[];
  regions: string[];
  company_size: string;
  buyer_roles: string[];
  exclude_rules: string[];
  typical_customers: string[];
  source_report_table?: string;
  source_report_id?: string;
  seller_profile?: SalesAgentSellerProfile | null;
  account_icp?: SalesAgentAccountICP | null;
  buyer_personas?: SalesAgentBuyerPersona[];
}

export interface SalesAgentContact {
  id: number;
  company_id: number;
  contact_name?: string;
  position?: string;
  email?: string;
  phone?: string;
  contact_form_url?: string;
  source?: string;
  source_url?: string;
  confidence?: string;
  is_reachable?: boolean;
  consent_status?: string;
  captured_at?: string | null;
}

export interface SalesAgentCompany {
  id: number;
  name: string;
  industry: string;
  region: string;
  website: string;
  employee_size: string;
  source: string;
  status: string;
  email?: string;
  phone?: string;
  contact_form_url?: string;
  contacts?: SalesAgentContact[];
}

export interface SalesAgentLeadScoringBreakdown {
  label: string;
  score: number;
  max: number;
  note?: string;
}

export interface SalesAgentLeadScoring {
  match_score?: number;
  match_score_label?: string;
  opportunity_score?: number | null;
  opportunity_score_label?: string;
  confidence?: string;
  confidence_level?: string;
  recommended_action?: string;
  score_summary?: string;
  opportunity_breakdown?: SalesAgentLeadScoringBreakdown[];
}

export interface SalesAgentReportShare {
  id?: number;
  public_token?: string;
  share_token?: string;
  public_url?: string;
  source_report_table?: string;
  source_report_id?: string;
  report_id?: string;
  report_status?: string;
  status?: string;
  created_at?: string | null;
  updated_at?: string | null;
  expires_at?: string | null;
}

export interface SalesAgentLead {
  id: number;
  company_id: number;
  company?: SalesAgentCompany | null;
  profile_id?: number | null;
  campaign_id?: number | null;
  score: number;
  match_score?: number;
  opportunity_score?: number | null;
  opportunity_score_label?: string;
  confidence?: string;
  confidence_level?: string;
  recommended_action?: string;
  score_summary?: string;
  scoring?: SalesAgentLeadScoring;
  rank: number;
  reason: string;
  status: string;
  source_report_table?: string;
  source_report_id?: string;
  report_snapshot?: Record<string, unknown>;
  report_status?: string;
  report_id?: string;
  report_share?: SalesAgentReportShare;
  public_report_url?: string;
  share_token?: string;
  latest_report_email_draft?: SalesAgentDraft;
  has_report_email_draft?: boolean;
  evidence?: SalesAgentLeadEvidence[];
  next_followup_at?: string | null;
}

// The first group is the "recipient perspective" when there's a report; the second is the "letter type" for intro letters without report services.
// The two key groups don't overlap, and the backend normalizes each by which path the draft takes -- mixing them would hit another type's cached draft.
export type ReportEmailStyleKey = 'leijun_pitch' | 'executive_view' | 'marketing_brand_view' | 'sales_growth_view' | 'digital_ops_view'
  | 'standard' | 'scenario_fit' | 'industry_view' | 'need_check' | string;

export interface ReportEmailStyleOption {
  key: ReportEmailStyleKey;
  label: string;
  description?: string;
  /** Intro letters only: template = concatenated by the backend without the model, llm = the model rewrites the middle section. */
  mode?: 'template' | 'llm' | string;
}

export interface SalesAgentEmailProviderOption {
  provider_type: 'smtp' | 'api' | string;
  api_provider?: string;
  label: string;
  available: boolean;
}

/** Mailbox provider presets: picking 163 should auto-fill smtp.163.com / 465 / SSL, so users only fill in the mailbox and authorization code. */
export interface SalesAgentEmailProviderPreset {
  key: string;
  label: string;
  provider_type: 'smtp' | 'api' | string;
  api_provider?: string;
  /** Used to auto-detect by the sending mailbox domain; enterprise mailbox domains are the client's own, so this is empty and must be picked manually */
  domains: string[];
  smtp_host: string;
  smtp_port: number;
  smtp_security: 'ssl' | 'starttls' | 'none' | string;
  alt_ports?: { smtp_port: number; smtp_security: string }[];
  /** 163 needs an authorization code, Gmail an app-specific password -- the secret input's label follows this */
  secret_label: string;
  hint?: string;
  help_url?: string;
}

export type SalesAgentSenderSelectionPolicy = 'default_only' | 'round_robin' | string;

export interface SalesAgentEmailSetting {
  id?: number | null;
  scope: 'global' | 'user' | string;
  user_id?: number | null;
  sending_enabled: boolean;
  tracking_enabled: boolean;
  default_account_id?: number | null;
  sender_selection_policy?: SalesAgentSenderSelectionPolicy;
  updated_by?: string;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface SalesAgentEmailAccount {
  id: number;
  scope: 'global' | 'user' | string;
  user_id?: number | null;
  display_name: string;
  provider_type: 'smtp' | 'api' | string;
  api_provider?: string;
  /** The matched provider preset; when old data never stored preset_key, the backend looks it up by smtp_host */
  preset_key?: string;
  preset_label?: string;
  enabled: boolean;
  is_default: boolean;
  from_email: string;
  from_name?: string;
  reply_to_email?: string;
  smtp_host?: string;
  smtp_port?: number;
  smtp_security?: 'ssl' | 'starttls' | 'none' | string;
  smtp_username?: string;
  has_smtp_secret?: boolean;
  has_api_key?: boolean;
  api_base_url?: string;
  provider_config?: Record<string, unknown>;
  last_tested_at?: string | null;
  last_test_status?: string;
  last_test_error?: string;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface SalesAgentEmailDelivery {
  id: number;
  user_id: number;
  lead_id?: number | null;
  company_id?: number | null;
  contact_id?: number | null;
  outreach_log_id?: number | null;
  email_account_id?: number | null;
  report_share_id?: number | null;
  message_type: string;
  provider_type: string;
  provider_message_id?: string;
  from_email: string;
  from_name?: string;
  reply_to_email?: string;
  recipient_email: string;
  recipient_name?: string;
  subject: string;
  content_preview?: string;
  content?: string;
  status: string;
  failure_reason?: string;
  send_attempted_at?: string | null;
  sent_at?: string | null;
  failed_at?: string | null;
  first_opened_at?: string | null;
  last_opened_at?: string | null;
  open_count?: number;
  /** How many times this draft was actually sent in total (the backend enforces the resend cap by it) */
  send_count?: number;
  first_report_visit_at?: string | null;
  last_report_visit_at?: string | null;
  report_visit_count?: number;
  read_at?: string | null;
  metadata?: Record<string, unknown>;
  created_at?: string | null;
  updated_at?: string | null;
  company_name?: string;
  company?: SalesAgentCompany | null;
  user_email?: string;
}

export interface SalesAgentEmailEffectiveSettings {
  sending_enabled: boolean;
  tracking_enabled: boolean;
  disabled_reasons?: string[];
  account_source?: string;
  sender_selection_policy?: SalesAgentSenderSelectionPolicy;
  account?: SalesAgentEmailAccount | null;
  global_setting?: SalesAgentEmailSetting;
  user_setting?: SalesAgentEmailSetting;
}

export interface SalesAgentEmailSettingsResponse {
  settings: SalesAgentEmailSetting;
  effective: SalesAgentEmailEffectiveSettings;
  provider_options?: SalesAgentEmailProviderOption[];
  provider_presets?: SalesAgentEmailProviderPreset[];
}

export interface SalesAgentEmailAccountsResponse extends SalesAgentEmailSettingsResponse {
  accounts: SalesAgentEmailAccount[];
}

export interface SalesAgentEmailDeliveriesResponse {
  deliveries: SalesAgentEmailDelivery[];
  total: number;
  limit: number;
  offset: number;
}

export interface SalesAgentEmailStats {
  total: number;
  sent: number;
  failed: number;
  disabled: number;
  no_recipient: number;
  sending: number;
  read: number;
  open_count: number;
  report_visit_count: number;
}

export type SalesAgentEmailAccountInput = Partial<Omit<SalesAgentEmailAccount, 'id' | 'has_smtp_secret' | 'has_api_key' | 'created_at' | 'updated_at' | 'last_tested_at' | 'last_test_status' | 'last_test_error' | 'preset_label'>> & {
  smtp_secret?: string;
  api_key?: string;
};

// --------------------------------------------------------------------------
// SMS channels. Fields correspond one-to-one with the serialization of /sales-agent/sms/*, deliberately not merged with the email set into
// one generic Channel type: CN template channels have concepts like "signature / registered template / variable slots" that email doesn't
// have at all, and a forced merge would stuff both sides with optional fields so the editor could no longer hint at what to fill in.
// --------------------------------------------------------------------------

export type SalesAgentSmsProvider = 'tencent_cloud' | 'aliyun' | 'twilio' | 'aws_sns' | 'custom_http' | string;
/** template = can only send templates registered with the platform (CN); freeform = can send free-form copy (international). */
export type SalesAgentSmsContentMode = 'template' | 'freeform' | string;
/** Routing by phone country code: cn sends only domestic, intl only international, all sends both. */
export type SalesAgentSmsRegionScope = 'cn' | 'intl' | 'all' | string;

export interface SalesAgentSmsProviderOption {
  provider: SalesAgentSmsProvider;
  label: string;
  content_mode: SalesAgentSmsContentMode;
  region_scope: SalesAgentSmsRegionScope;
  available: boolean;
}

/** Platforms name their credentials very differently (SecretId / AccessKeyId / Account SID), so labels follow the preset. */
export interface SalesAgentSmsProviderPreset {
  key: string;
  provider: SalesAgentSmsProvider;
  label: string;
  content_mode: SalesAgentSmsContentMode;
  region_scope: SalesAgentSmsRegionScope;
  needs_sign_name: boolean;
  needs_sdk_app_id: boolean;
  needs_from_number: boolean;
  key_id_label: string;
  secret_label: string;
  sdk_app_id_label?: string;
  hint?: string;
  help_url?: string;
}

export interface SalesAgentSmsSetting {
  id?: number | null;
  scope: 'global' | 'user' | string;
  user_id?: number | null;
  sending_enabled: boolean;
  /** SMS has no tracking pixel; receipts rely on platform push -- corresponds to email's tracking_enabled */
  receipt_enabled: boolean;
  default_account_id?: number | null;
  sender_selection_policy?: SalesAgentSenderSelectionPolicy;
  /** 0 = unlimited. SMS is billed per segment and complaints are costly, so these two gates are used more than for email */
  daily_send_cap: number;
  per_contact_cooldown_days: number;
  updated_by?: string;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface SalesAgentSmsAccount {
  id: number;
  scope: 'global' | 'user' | string;
  user_id?: number | null;
  display_name: string;
  provider: SalesAgentSmsProvider;
  provider_label?: string;
  content_mode: SalesAgentSmsContentMode;
  region_scope: SalesAgentSmsRegionScope;
  enabled: boolean;
  is_default: boolean;
  sign_name?: string;
  sdk_app_id?: string;
  has_api_key_id?: boolean;
  has_api_secret?: boolean;
  api_base_url?: string;
  from_number?: string;
  provider_config?: Record<string, unknown>;
  /** Receipt callback URL to paste into the SMS console; empty string when the platform doesn't support push */
  callback_url?: string;
  supports_callback?: boolean;
  last_tested_at?: string | null;
  last_test_status?: string;
  last_test_error?: string;
  created_at?: string | null;
  updated_at?: string | null;
}

/** Registered template. Email has no counterpart -- CN channels can only send these, the body can't be changed, only variables. */
export interface SalesAgentSmsTemplate {
  id: number;
  account_id: number;
  /** Template ID issued by the platform (Tencent Cloud TemplateId / Alibaba Cloud TemplateCode) */
  template_code: string;
  name: string;
  body: string;
  /** Positional params, index numbered contiguously from 1, corresponding to {1}{2} in the template body; max_len=0 means unlimited */
  param_slots: { index: number; label: string; max_len: number }[];
  purpose: 'outreach' | 'followup' | 'test' | string;
  approved_status: 'pending' | 'approved' | 'rejected' | string;
  enabled: boolean;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface SalesAgentSmsDelivery {
  id: number;
  channel: 'sms';
  user_id: number;
  lead_id?: number | null;
  company_id?: number | null;
  contact_id?: number | null;
  outreach_log_id?: number | null;
  sms_account_id?: number | null;
  report_share_id?: number | null;
  message_type: string;
  provider: SalesAgentSmsProvider;
  provider_message_id?: string;
  sign_name?: string;
  template_code?: string;
  template_params?: string[];
  recipient_phone: string;
  recipient_country?: string;
  recipient_name?: string;
  content_preview?: string;
  content?: string;
  /** Carrier billed segments: 70 characters per segment for Chinese, 160 for pure English */
  segments?: number;
  status: string;
  failure_code?: string;
  failure_reason?: string;
  send_attempted_at?: string | null;
  sent_at?: string | null;
  delivered_at?: string | null;
  failed_at?: string | null;
  first_click_at?: string | null;
  last_click_at?: string | null;
  click_count?: number;
  reply_count?: number;
  opted_out_at?: string | null;
  send_count?: number;
  metadata?: Record<string, unknown>;
  created_at?: string | null;
  updated_at?: string | null;
  company_name?: string;
  company?: SalesAgentCompany | null;
  user_email?: string;
}

export interface SalesAgentSmsEffectiveSettings {
  sending_enabled: boolean;
  receipt_enabled: boolean;
  disabled_reasons?: string[];
  account_source?: string;
  sender_selection_policy?: SalesAgentSenderSelectionPolicy;
  daily_send_cap?: number;
  per_contact_cooldown_days?: number;
  account?: SalesAgentSmsAccount | null;
  global_setting?: SalesAgentSmsSetting;
  user_setting?: SalesAgentSmsSetting;
}

export interface SalesAgentSmsSettingsResponse {
  settings: SalesAgentSmsSetting;
  effective: SalesAgentSmsEffectiveSettings;
  provider_options?: SalesAgentSmsProviderOption[];
  provider_presets?: SalesAgentSmsProviderPreset[];
}

export interface SalesAgentSmsAccountsResponse extends Omit<SalesAgentSmsSettingsResponse, 'settings'> {
  accounts: SalesAgentSmsAccount[];
  /** When an admin fetches everything (scope=all) the backend doesn't return a single settings -- it has no unique owner then */
  settings: SalesAgentSmsSetting | null;
}

export interface SalesAgentSmsTemplatesResponse {
  account: SalesAgentSmsAccount | null;
  templates: SalesAgentSmsTemplate[];
}

export interface SalesAgentSmsDeliveriesResponse {
  deliveries: SalesAgentSmsDelivery[];
  total: number;
  limit: number;
  offset: number;
}

export interface SalesAgentSmsStats {
  total: number;
  sent: number;
  delivered: number;
  failed: number;
  rejected: number;
  disabled: number;
  no_recipient: number;
  sending: number;
  click_count: number;
  reply_count: number;
  opted_out: number;
  segments: number;
}

/** Leave secret fields empty = unchanged (the backend echoes has_* booleans, not ciphertext). */
export type SalesAgentSmsAccountInput = Partial<Omit<SalesAgentSmsAccount, 'id' | 'has_api_key_id' | 'has_api_secret' | 'provider_label' | 'callback_url' | 'supports_callback' | 'created_at' | 'updated_at' | 'last_tested_at' | 'last_test_status' | 'last_test_error'>> & {
  api_key_id?: string;
  api_secret?: string;
};

export type SalesAgentSmsTemplateInput = Partial<Omit<SalesAgentSmsTemplate, 'id' | 'account_id' | 'created_at' | 'updated_at'>>;

/**
 * Delivery records on the draft card. Backend `serialize_draft` picks the table by draft channel: the email row for email drafts,
 * the sms row for SMS drafts. Written as an optional union of both field sets so the draft card can read
 * status/sent_at/failure_reason regardless of channel, while still reading SMS-only billed segments and click counts.
 */
export type SalesAgentDraftDelivery = Partial<SalesAgentEmailDelivery> & Partial<SalesAgentSmsDelivery> & {
  id: number;
  status: string;
};

export interface SalesAgentDraft {
  id: number;
  lead_id: number;
  channel: string;
  subject: string;
  content: string;
  send_status: string;
  requires_approval: boolean;
  has_unsubscribe: boolean;
  metadata?: Record<string, unknown>;
  delivery?: SalesAgentDraftDelivery | null;
  recipient_email?: string;
  /** Target number for SMS drafts; empty string for email drafts */
  recipient_phone?: string;
  from_email?: string;
  failure_reason?: string;
  read_at?: string | null;
  created_at?: string | null;
}

export interface SalesAgentAutoDraftOptions {
  style_key?: ReportEmailStyleKey;
  variant_count?: number;
  force_regenerate?: boolean;
}

export interface SalesAgentAutoDraftResponse {
  blocked: boolean;
  needs_report?: boolean;
  report_status?: string;
  public_report_url?: string;
  draft?: SalesAgentDraft;
  variants?: SalesAgentDraft[];
  lead?: SalesAgentLead;
  company?: SalesAgentCompany;
  contact?: SalesAgentContact;
  report_share?: SalesAgentReportShare;
  reasons?: string[];
  policy?: string;
  hint?: string;
  cached?: boolean;
  style_key?: ReportEmailStyleKey;
  style_options?: ReportEmailStyleOption[];
  report_fingerprint?: string;
}

export interface SalesAgentSessionItem {
  id: string;
  title: string;
  updated_at?: string | null;
}

export interface SalesAgentReportItem {
  source_report_table: string;
  source_report_id: string;
  title: string;
  url: string;
  status: string;
  created_at?: string | null;
  summary: Record<string, unknown>;
}

export interface SalesAgentLeadReportResult {
  blocked: boolean;
  status: string;
  report_status: string;
  report_id?: number | string;
  source_report_table?: string;
  source_report_id?: string;
  share_token?: string;
  public_url?: string;
  report_share?: SalesAgentReportShare;
  lead?: SalesAgentLead;
  company?: SalesAgentCompany;
  reused?: boolean;
  hint?: string;
}

export interface SalesAgentPublicCompany {
  id?: number;
  name?: string;
  industry?: string;
  region?: string;
  website?: string;
  employee_size?: string;
}

export interface SalesAgentPublicReport {
  id: number;
  status: 'generating' | 'ready' | 'failed' | string;
  url: string;
  brand_name: string;
  competitor_domains?: string[];
  industry_terms?: string[];
  brand_terms?: string[];
  progress?: DiagnosisProgress | null;
  scores?: DiagnosisScores | null;
  narrative?: DiagnosisNarrative | null;
  subresults?: Record<string, DiagnosisSubResult> | null;
  subresults_keys?: string[];
  error?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface SalesAgentPublicReportResponse {
  public_token: string;
  public_url: string;
  status: string;
  created_at?: string | null;
  updated_at?: string | null;
  company?: SalesAgentPublicCompany;
  report: SalesAgentPublicReport;
  privacy?: string;
}

export interface SalesAgentBundle {
  session: SalesAgentSessionItem | null;
  sessions: SalesAgentSessionItem[];
  sessions_has_more?: boolean;
  sessions_next_offset?: number | null;
  sessions_limit?: number;
  sessions_offset?: number;
  messages: SalesAgentMessage[];
  undo?: { restored?: boolean; message?: string; step?: string; label?: string; checkpoint_id?: number };
}

export interface SalesAgentSessionsPage {
  sessions: SalesAgentSessionItem[];
  sessions_has_more: boolean;
  sessions_next_offset?: number | null;
  sessions_limit: number;
  sessions_offset: number;
}

function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const token = localStorage.getItem('token');
  return localizedHeaders({
    ...extra,
    Authorization: `Bearer ${token ?? ''}`,
  });
}

type JsonRequestInit = Omit<RequestInit, 'headers'> & { headers?: Record<string, string> };

const INTERNAL_ERROR_RE = /(model|provider|pydantic|openrouter|openai|anthropic|deepseek|qwen|moonshot|glm|siliconflow|api[_ -]?key|token|traceback|exception|source_report|detection_records|full_diagnosis|context=|模型|供应商|密钥|令牌)/i;

function safeSalesAgentError(msg: string, fallback = 'Sales Agent 本次处理失败,请稍后重试或换个问题。'): string {
  const text = (msg || '').trim();
  if (!text) return fallback;
  if (/未登录|过期|401/.test(text)) return '未登录或登录已过期';
  if (/503|暂不可用/i.test(text)) return 'Sales Agent 服务暂不可用';
  if (text.length > 240 || INTERNAL_ERROR_RE.test(text)) return fallback;
  return text;
}

async function jsonRequest<T>(path: string, init: JsonRequestInit = {}): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: authHeaders(init.headers),
  });
  if (resp.status === 401) throw new Error('未登录或登录已过期');
  if (!resp.ok) {
    let msg = `请求失败(${resp.status})`;
    try {
      const body = await resp.json();
      msg = body?.detail || body?.message || msg;
    } catch { /* ignore */ }
    throw new Error(safeSalesAgentError(msg, `请求失败(${resp.status})`));
  }
  return await resp.json() as T;
}

async function publicJsonRequest<T>(path: string, init: JsonRequestInit = {}): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: localizedHeaders(init.headers || {}),
  });
  if (!resp.ok) {
    let msg = `请求失败(${resp.status})`;
    try {
      const body = await resp.json();
      msg = body?.detail || body?.message || msg;
    } catch { /* ignore */ }
    throw new Error(safeSalesAgentError(msg, `请求失败(${resp.status})`));
  }
  return await resp.json() as T;
}

export function sendSalesAgentChatA2UI(message: string, sessionId: string | null, newSession = false, pageContext: Record<string, unknown> = {}): Promise<SalesAgentBundle> {
  return jsonRequest<SalesAgentBundle>('/sales-agent/chat/a2ui', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message,
      session_id: sessionId,
      new_session: newSession,
      page_context: {
        page: '/sales-agent',
        describe: 'Sales Agent 隐藏工作台',
        actions: ['refreshSession'],
        ...pageContext,
      },
    }),
  });
}

export async function streamSalesAgentChat(
  message: string,
  sessionId: string | null,
  cb: SalesAgentChatCallbacks,
  newSession = false,
  pageContext: Record<string, unknown> = {},
): Promise<void> {
  let resp: Response;
  try {
    resp = await fetch(`${API_BASE}/sales-agent/chat`, {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({
        message,
        session_id: sessionId,
        new_session: newSession,
        // page_context has the same definition as /chat/a2ui -- the seed pool and default recommendation count are passed here,
        // and both channels must give the backend identical input, otherwise switching channels changes the recommendation results.
        page_context: {
          page: '/sales-agent',
          describe: 'Sales Agent 隐藏工作台',
          actions: ['refreshSession'],
          ...pageContext,
        },
      }),
      signal: cb.signal,
    });
  } catch (e) {
    if ((e as Error)?.name !== 'AbortError') cb.onError?.(safeSalesAgentError(e instanceof Error ? e.message : '网络错误', '网络错误'));
    return;
  }

  if (resp.status === 401) { cb.onError?.('未登录或登录已过期'); return; }
  if (resp.status === 503) { cb.onError?.('Sales Agent 服务暂不可用'); return; }
  if (!resp.ok || !resp.body) { cb.onError?.(`请求失败(${resp.status})`); return; }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split('\n\n');
      buf = parts.pop() ?? '';
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith('data:')) continue;
        const payload = line.slice(5).trim();
        try {
          const obj = JSON.parse(payload) as { delta?: string; stage?: string; cards?: SalesAgentCard[]; error?: string; done?: boolean; session_id?: string };
          if (obj.delta) cb.onDelta(obj.delta);
          else if (obj.stage) cb.onStage?.(obj.stage);
          else if (obj.cards) cb.onCards?.(obj.cards);
          else if (obj.error) { cb.onError?.(safeSalesAgentError(obj.error)); return; }
          else if (obj.done) { await cb.onDone?.(obj.session_id); return; }
        } catch { /* Skip non-JSON lines */ }
      }
    }
  } catch (e) {
    if ((e as Error)?.name !== 'AbortError') cb.onError?.(safeSalesAgentError((e as Error).message));
    return;
  }
  await cb.onDone?.();
}

export function fetchSalesAgentSession(sessionId?: string): Promise<SalesAgentBundle> {
  return jsonRequest<SalesAgentBundle>(sessionId ? `/sales-agent/sessions/${sessionId}` : '/sales-agent/sessions/current');
}

export function undoSalesAgentSession(sessionId: string | null, checkpointId?: number | null): Promise<SalesAgentBundle> {
  return jsonRequest<SalesAgentBundle>('/sales-agent/sessions/undo', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, confirmed: true, checkpoint_id: checkpointId ?? null }),
  });
}

export function fetchSalesAgentSessions(offset = 0, limit = 15): Promise<SalesAgentSessionsPage> {
  const params = new URLSearchParams({ offset: String(offset), limit: String(limit) });
  return jsonRequest<SalesAgentSessionsPage>(`/sales-agent/sessions?${params.toString()}`);
}

export function createSalesAgentProfile(input: Partial<SalesAgentProfile> & { manual_profile?: Record<string, unknown> }): Promise<SalesAgentProfile> {
  return jsonRequest<SalesAgentProfile>('/sales-agent/customer-profiles', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
}

export function discoverSalesAgentCompanies(profileId?: number | null): Promise<{ companies: SalesAgentCompany[]; count: number; blocked: number }> {
  return jsonRequest('/sales-agent/companies/discover', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ profile_id: profileId ?? null, limit: SALES_AGENT_RECOMMENDATION_LIMIT, seed_companies: [] }),
  });
}

export function rankSalesAgentCompanies(profileId?: number | null): Promise<SalesAgentRecommendationResult> {
  return jsonRequest('/sales-agent/companies/rank', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ profile_id: profileId ?? null, limit: SALES_AGENT_RECOMMENDATION_LIMIT }),
  });
}

export function recommendSalesAgentCompanies(profileId?: number | null, limit = SALES_AGENT_RECOMMENDATION_LIMIT): Promise<SalesAgentRecommendationResult> {
  return jsonRequest('/sales-agent/companies/recommend', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ profile_id: profileId ?? null, limit }),
  });
}

export function createOutreachDraft(leadId: number, channel = 'email', content = '', subject = ''): Promise<{ blocked: boolean; draft?: SalesAgentDraft; lead?: SalesAgentLead; company?: SalesAgentCompany; contact?: SalesAgentContact; delivery?: SalesAgentDraftDelivery | null; reasons?: string[]; policy?: string }> {
  return jsonRequest('/sales-agent/outreach/drafts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ lead_id: leadId, channel, content, subject }),
  });
}

export function autoCreateOutreachDraft(leadId: number, channel = 'email', options: SalesAgentAutoDraftOptions = {}): Promise<SalesAgentAutoDraftResponse> {
  return jsonRequest('/sales-agent/outreach/drafts/auto', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ lead_id: leadId, channel, ...options }),
  });
}

// ── Streaming generation of service intro letters ────────────────────────────────────────────────
// Only the three model-written letter types "scenario angle / industry observation / needs confirmation" take this path. The header and footer are
// fixed by the backend and sent first on connect; the middle is pushed paragraph by paragraph -- the backend waits for a blank line before sending, because every character sent
// must pass the compliance gate first. The "standard intro" is zero-token pure concatenation, and report emails must pass the gate as a whole before being given,
// so both stay on the non-streaming `autoCreateOutreachDraft` path.

export type SalesAgentIntroStreamEvent =
  // Header and footer available on connect. `fallback_subject` is the template title used as a fallback when the model doesn't produce a title,
  // **not** this letter's title -- displaying it directly would flash an old title before the model's replaces it.
  | { type: 'head'; greeting: string; tail: string[]; fallback_subject: string; style_key: string; style_label: string }
  | { type: 'subject'; subject: string }
  // Raw text fragments safe to release while a paragraph is half-written, **preview only**: they don't go into the final letter and are void once the same paragraph's
  // `paragraph` arrives. A frame is often a single character -- appending in order to the current paragraph produces the typewriter effect.
  | { type: 'chunk'; text: string }
  // The stripped full paragraph, which goes into the final letter. On receipt, it replaces this paragraph's character-by-character preview entirely.
  | { type: 'paragraph'; index: number; text: string }
  // The body is finished (still persisting). The frontend shows the footer only after receiving this.
  | { type: 'body_done' }
  // `detail` is the exception type for troubleshooting, only logged to the console, not rendered -- relied on for diagnosis when server logs aren't available.
  | { type: 'reset'; reason: string; detail?: string }
  | { type: 'error'; message: string }
  | { type: 'done'; result: SalesAgentAutoDraftResponse };

/**
 * Returns the complete result from the `done` frame -- the streamed paragraphs are only a preview, **the final result is authoritative**:
 * only the persisted letter carries the draft id and is what gets submitted on confirm send.
 */
export async function streamAutoCreateOutreachDraft(
  leadId: number,
  options: SalesAgentAutoDraftOptions,
  onEvent: (event: SalesAgentIntroStreamEvent) => void,
  signal?: AbortSignal,
): Promise<SalesAgentAutoDraftResponse> {
  const resp = await fetch(`${API_BASE}/sales-agent/outreach/drafts/auto/stream`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ lead_id: leadId, channel: 'email', ...options }),
    signal,
  });
  if (!resp.ok || !resp.body) {
    let detail = '';
    try { detail = String(((await resp.json()) as { detail?: string })?.detail || ''); } catch { /* Non-JSON error body */ }
    throw new Error(detail || `介绍信流式生成失败(${resp.status})`);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let result: SalesAgentAutoDraftResponse | null = null;
  let failure = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split('\n\n');
    buffer = frames.pop() ?? '';
    for (const frame of frames) {
      for (const line of frame.split('\n')) {
        if (!line.startsWith('data:')) continue;
        let event: SalesAgentIntroStreamEvent;
        try { event = JSON.parse(line.slice(5).trim()) as SalesAgentIntroStreamEvent; } catch { continue; }
        if (event.type === 'error') failure = safeSalesAgentError(event.message);
        else if (event.type === 'done') result = event.result;
        else onEvent(event);
      }
    }
  }
  if (failure) throw new Error(failure);
  if (!result) throw new Error('介绍信生成中断,请重试。');
  return result;
}

export function generateLeadReport(leadId: number): Promise<SalesAgentLeadReportResult> {
  return jsonRequest(`/sales-agent/leads/${leadId}/report/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
}

export function fetchPublicSalesAgentReport(token: string): Promise<SalesAgentPublicReportResponse> {
  return publicJsonRequest(`/sales-agent/public/reports/${encodeURIComponent(token)}${window.location.search || ''}`);
}

export function fetchSalesAgentEmailSettings(admin = false): Promise<SalesAgentEmailSettingsResponse> {
  return jsonRequest<SalesAgentEmailSettingsResponse>(admin ? '/sales-agent/admin/email/settings' : '/sales-agent/email/settings');
}

export function updateSalesAgentEmailSettings(input: Partial<SalesAgentEmailSetting>, admin = false): Promise<SalesAgentEmailSettingsResponse> {
  return jsonRequest<SalesAgentEmailSettingsResponse>(admin ? '/sales-agent/admin/email/settings' : '/sales-agent/email/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
}

export function fetchSalesAgentEmailAccounts(admin = false, scope = admin ? 'all' : 'user'): Promise<SalesAgentEmailAccountsResponse> {
  const params = admin ? `?scope=${encodeURIComponent(scope)}` : '';
  return jsonRequest<SalesAgentEmailAccountsResponse>(`${admin ? '/sales-agent/admin/email/accounts' : '/sales-agent/email/accounts'}${params}`);
}

export function createSalesAgentEmailAccount(input: SalesAgentEmailAccountInput, admin = false): Promise<SalesAgentEmailAccount> {
  return jsonRequest<SalesAgentEmailAccount>(admin ? '/sales-agent/admin/email/accounts' : '/sales-agent/email/accounts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
}

export function updateSalesAgentEmailAccount(accountId: number, input: SalesAgentEmailAccountInput, admin = false): Promise<SalesAgentEmailAccount> {
  return jsonRequest<SalesAgentEmailAccount>(`${admin ? '/sales-agent/admin/email/accounts' : '/sales-agent/email/accounts'}/${accountId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
}

export function disableSalesAgentEmailAccount(accountId: number, admin = false): Promise<{ ok: boolean; account?: SalesAgentEmailAccount }> {
  return jsonRequest(`${admin ? '/sales-agent/admin/email/accounts' : '/sales-agent/email/accounts'}/${accountId}`, { method: 'DELETE' });
}

export function testSalesAgentEmailAccount(accountId: number, input: { to_email?: string; subject?: string; content?: string } = {}, admin = false): Promise<{ ok: boolean; delivery?: SalesAgentEmailDelivery; account?: SalesAgentEmailAccount; error?: string }> {
  return jsonRequest(`${admin ? '/sales-agent/admin/email/accounts' : '/sales-agent/email/accounts'}/${accountId}/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
}

export function fetchSalesAgentEmailDeliveries(params: { status?: string; message_type?: string; q?: string; read?: string; limit?: number; offset?: number; user_id?: number; company_id?: number; lead_id?: number } = {}, admin = false): Promise<SalesAgentEmailDeliveriesResponse> {
  const qs = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== null && String(v) !== '') qs.set(k, String(v)); });
  const query = qs.toString();
  return jsonRequest<SalesAgentEmailDeliveriesResponse>(`${admin ? '/sales-agent/admin/email/deliveries' : '/sales-agent/email/deliveries'}${query ? `?${query}` : ''}`);
}

export function fetchSalesAgentEmailStats(params: { user_id?: number } = {}, admin = false): Promise<SalesAgentEmailStats> {
  const qs = new URLSearchParams();
  if (params.user_id) qs.set('user_id', String(params.user_id));
  const query = qs.toString();
  return jsonRequest<SalesAgentEmailStats>(`${admin ? '/sales-agent/admin/email/stats' : '/sales-agent/email/stats'}${query ? `?${query}` : ''}`);
}

// --- SMS ---------------------------------------------------------------------
// Paths correspond one-to-one with the email set above, plus a set of /templates under the channel.

const smsBase = (admin: boolean) => (admin ? '/sales-agent/admin/sms' : '/sales-agent/sms');

export function fetchSalesAgentSmsSettings(admin = false): Promise<SalesAgentSmsSettingsResponse> {
  return jsonRequest<SalesAgentSmsSettingsResponse>(`${smsBase(admin)}/settings`);
}

export function updateSalesAgentSmsSettings(input: Partial<SalesAgentSmsSetting>, admin = false): Promise<SalesAgentSmsSettingsResponse> {
  return jsonRequest<SalesAgentSmsSettingsResponse>(`${smsBase(admin)}/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
}

export function fetchSalesAgentSmsAccounts(admin = false, scope = admin ? 'all' : 'user'): Promise<SalesAgentSmsAccountsResponse> {
  const params = admin ? `?scope=${encodeURIComponent(scope)}` : '';
  return jsonRequest<SalesAgentSmsAccountsResponse>(`${smsBase(admin)}/accounts${params}`);
}

export function createSalesAgentSmsAccount(input: SalesAgentSmsAccountInput, admin = false): Promise<SalesAgentSmsAccount> {
  return jsonRequest<SalesAgentSmsAccount>(`${smsBase(admin)}/accounts`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
}

export function updateSalesAgentSmsAccount(accountId: number, input: SalesAgentSmsAccountInput, admin = false): Promise<SalesAgentSmsAccount> {
  return jsonRequest<SalesAgentSmsAccount>(`${smsBase(admin)}/accounts/${accountId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
}

export function disableSalesAgentSmsAccount(accountId: number, admin = false): Promise<{ ok: boolean; account?: SalesAgentSmsAccount }> {
  return jsonRequest(`${smsBase(admin)}/accounts/${accountId}`, { method: 'DELETE' });
}

export function testSalesAgentSmsAccount(
  accountId: number,
  input: { to_phone?: string; content?: string; template_code?: string; template_params?: string[] } = {},
  admin = false,
): Promise<{ ok: boolean; delivery?: SalesAgentSmsDelivery; account?: SalesAgentSmsAccount; error?: string }> {
  return jsonRequest(`${smsBase(admin)}/accounts/${accountId}/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
}

export function fetchSalesAgentSmsTemplates(accountId: number, admin = false): Promise<SalesAgentSmsTemplatesResponse> {
  return jsonRequest<SalesAgentSmsTemplatesResponse>(`${smsBase(admin)}/accounts/${accountId}/templates`);
}

export function createSalesAgentSmsTemplate(accountId: number, input: SalesAgentSmsTemplateInput, admin = false): Promise<SalesAgentSmsTemplate> {
  return jsonRequest<SalesAgentSmsTemplate>(`${smsBase(admin)}/accounts/${accountId}/templates`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
}

export function updateSalesAgentSmsTemplate(templateId: number, input: SalesAgentSmsTemplateInput, admin = false): Promise<SalesAgentSmsTemplate> {
  return jsonRequest<SalesAgentSmsTemplate>(`${smsBase(admin)}/templates/${templateId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
}

export function disableSalesAgentSmsTemplate(templateId: number, admin = false): Promise<{ ok: boolean; template?: SalesAgentSmsTemplate }> {
  return jsonRequest(`${smsBase(admin)}/templates/${templateId}`, { method: 'DELETE' });
}

export function fetchSalesAgentSmsDeliveries(params: { status?: string; message_type?: string; q?: string; limit?: number; offset?: number; user_id?: number; company_id?: number; lead_id?: number } = {}, admin = false): Promise<SalesAgentSmsDeliveriesResponse> {
  const qs = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== null && String(v) !== '') qs.set(k, String(v)); });
  const query = qs.toString();
  return jsonRequest<SalesAgentSmsDeliveriesResponse>(`${smsBase(admin)}/deliveries${query ? `?${query}` : ''}`);
}

export function fetchSalesAgentSmsStats(params: { user_id?: number } = {}, admin = false): Promise<SalesAgentSmsStats> {
  const qs = new URLSearchParams();
  if (params.user_id) qs.set('user_id', String(params.user_id));
  const query = qs.toString();
  return jsonRequest<SalesAgentSmsStats>(`${smsBase(admin)}/stats${query ? `?${query}` : ''}`);
}

export function confirmOutreachDraft(draftId: number, confirmed = true, note = ''): Promise<{ blocked: boolean; draft: SalesAgentDraft; lead?: SalesAgentLead; delivery?: SalesAgentDraftDelivery | null; reasons?: string[]; policy?: string; hint?: string }> {
  return jsonRequest(`/sales-agent/outreach/drafts/${draftId}/confirm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ confirmed, note }),
  });
}

// ── Scheduled sending (/sales-agent/outreach/schedules, backend plan 4.4) ─────────────────
//
// Entirely different semantics from confirmOutreachDraft above; don't mix them up:
//   confirmOutreachDraft  -- 1 click = **actually sends 1 email right away**;
//   confirmSendSchedule   -- 1 click = authorizes N emails, and after the click **not a single one has been sent**.
// In copy, one says "Sent", the other can only say "Scheduled".

export type SalesAgentScheduleStatus = 'draft' | 'armed' | 'running' | 'done' | 'cancelled' | 'expired';
export type SalesAgentScheduleItemStatus = 'pending_draft' | 'pending' | 'sending' | 'sent' | 'failed' | 'skipped' | 'cancelled';

export interface SalesAgentSendSchedule {
  id: number;
  channel: string;
  status: SalesAgentScheduleStatus | string;
  /** Already human-readable Chinese text; display it directly, don't maintain another status vocabulary on the frontend */
  status_text: string;
  scheduled_at: string;
  /** "2026-09-14 15:00 (Beijing time)" -- echo the full time, the frontend doesn't reformat it */
  scheduled_at_text: string;
  timezone: string;
  throttle_seconds: number;
  daily_cap: number;
  email_account_id: number | null;
  campaign_id: number | null;
  session_id: number | null;
  total: number;
  counts: Record<string, number>;
  sent: number;
  /** pending + pending_draft -- merged into one "Pending send" on the panel; whether the draft is written stays out of the user's view */
  pending: number;
  failed: number;
  skipped: number;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface SalesAgentSendScheduleItem {
  id: number;
  schedule_id: number;
  lead_id: number;
  company_id: number | null;
  company_name: string;
  contact_id: number | null;
  outreach_log_id: number | null;
  status: SalesAgentScheduleItemStatus | string;
  status_text: string;
  skip_reason: string;
  email_delivery_id: number | null;
  /** Fallback trail: the first mailbox bounced and the second succeeded; the panel must make this clear */
  attempted: Array<{ email?: string; result?: string; side?: string; reason?: string }>;
  attempt_count: number;
  finished_at: string | null;
  updated_at: string;
}

export interface SalesAgentSendSchedulePlan {
  schedule: SalesAgentSendSchedule;
  included: Array<{ lead_id: number; company: string; email: string; has_draft: boolean }>;
  /** Each item with a reason. "Some companies can't be sent to" doesn't count */
  skipped: Array<{ lead_id: number; company: string; reason: string }>;
  hint: string;
  time_warning?: string;
}

export interface SalesAgentSendScheduleDetail {
  schedule: SalesAgentSendSchedule;
  items: SalesAgentSendScheduleItem[];
}

export interface SalesAgentSendScheduleList {
  schedules: SalesAgentSendSchedule[];
  total: number;
  limit: number;
  offset: number;
}

export interface SalesAgentSendScheduleAck {
  schedule: SalesAgentSendSchedule;
  hint?: string;
  cancelled_items?: number;
  dropped?: number;
  remaining?: number;
}

export function createSendSchedule(body: {
  lead_ids: number[];
  when_text?: string;
  /** Timezone-less local string (`2026-09-14T15:00`), read by the backend as Beijing time */
  scheduled_at?: string;
  throttle_seconds?: number;
  daily_cap?: number;
  session_id?: string | null;
  campaign_id?: number | null;
  /** Sending by batch: leave `lead_ids` empty and the backend translates the batch into lead_ids. The server has the final say on what "this batch" means */
  campaign_ids?: number[];
  /** true = schedule right after creating (draft -> armed), no second confirmation. The batch modal uses this */
  confirm?: boolean;
}): Promise<SalesAgentSendSchedulePlan & { armed?: SalesAgentSendScheduleAck }> {
  return jsonRequest('/sales-agent/outreach/schedules', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export interface SalesAgentSendSchedulePreview {
  total: number;
  sendable_count: number;
  blocked_count: number;
  when_text: string;
  time_warning: string;
}

/**
 * Preview: with these batches and this time, how many can be sent and how many can't. **Read-only, not persisted to DB.**
 *
 * It uses the same candidate evaluation function as `createSendSchedule`, so the count reported in the modal
 * can't differ from the count actually scheduled -- a mismatch would look like users seeing 8 companies while 10 actually get emails.
 */
export function previewSendSchedule(body: {
  campaign_ids?: number[];
  lead_ids?: number[];
  when_text?: string;
  scheduled_at?: string;
  session_id?: string | null;
}): Promise<SalesAgentSendSchedulePreview> {
  return jsonRequest('/sales-agent/outreach/schedules/preview', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export interface SalesAgentRecommendationBatch {
  campaign_id: number;
  /** Starts at 1, **ascending** by recommendation time -- when users say "the first batch" they mean the earliest one */
  index: number;
  label: string;
  created_at_text: string;
  lead_count: number;
}

export interface SalesAgentRecommendationBatchList {
  batches: SalesAgentRecommendationBatch[];
  /** Default selection = the latest batch. In Chinese 「这批」 ("this batch") by default refers to the one just recommended */
  default_campaign_id: number;
  total_batches: number;
}

export function fetchRecommendationBatches(sessionId?: string | null, limit = 10): Promise<SalesAgentRecommendationBatchList> {
  const qs = new URLSearchParams();
  if (sessionId) qs.set('session_id', sessionId);
  qs.set('limit', String(limit));
  return jsonRequest(`/sales-agent/outreach/recommendation-batches?${qs.toString()}`);
}

export function fetchSendSchedules(params: { status?: string; limit?: number; offset?: number } = {}): Promise<SalesAgentSendScheduleList> {
  const qs = new URLSearchParams();
  if (params.status) qs.set('status', params.status);
  if (params.limit) qs.set('limit', String(params.limit));
  if (params.offset) qs.set('offset', String(params.offset));
  const query = qs.toString();
  return jsonRequest(`/sales-agent/outreach/schedules${query ? `?${query}` : ''}`);
}

/** Details + item breakdown. Both the panel and running polling use it -- the list endpoint can't carry 100 rows of items. */
export function fetchSendScheduleDetail(scheduleId: number): Promise<SalesAgentSendScheduleDetail> {
  return jsonRequest(`/sales-agent/outreach/schedules/${scheduleId}`);
}

/** Authorize (pending confirmation -> scheduled). With `confirmed=false` the backend treats it as cancel, not "nothing happened". */
export function confirmSendSchedule(scheduleId: number, confirmed = true): Promise<SalesAgentSendScheduleAck> {
  return jsonRequest(`/sales-agent/outreach/schedules/${scheduleId}/confirm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ confirmed }),
  });
}

/** Change time / exclude items. Providing neither gets a 400 from the backend; don't use it as a refresh. */
export function patchSendSchedule(scheduleId: number, body: { when_text?: string; scheduled_at?: string; drop_item_ids?: number[] }): Promise<SalesAgentSendScheduleAck> {
  return jsonRequest(`/sales-agent/outreach/schedules/${scheduleId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function cancelSendSchedule(scheduleId: number, reason = ''): Promise<SalesAgentSendScheduleAck> {
  const qs = reason ? `?reason=${encodeURIComponent(reason)}` : '';
  return jsonRequest(`/sales-agent/outreach/schedules/${scheduleId}${qs}`, { method: 'DELETE' });
}

// ── Option catalog ─────────────────────────────────────────────────────────────
// The brand endpoints (GET/POST/PATCH/DELETE /brands) were removed along with /sales-agent/admin
// -- brands are now auto-synced from the brand materials (sync_brand_from_topic), and the frontend no longer creates/updates/deletes them.
// The brand selection card in the conversation flow is produced by the backend itself and never went through here anyway.

export type SalesAgentOptionKind = 'capability' | 'typical_customer' | 'industry' | 'region' | 'company_size';

export interface SalesAgentOptionCatalogEntry {
  id: number;
  kind: SalesAgentOptionKind;
  value: string;
  label: string;
  description: string;
  source: 'system' | 'custom' | string;
  sort_order: number;
  min_employees: number | null;
  max_employees: number | null;
  seller_profile_id: number | null;
  // System preset rows have user_id NULL; the management page shows them read-only, no deletion
  editable: boolean;
  archived_at: string | null;
}

export interface SalesAgentOptionCatalogResponse {
  seller_profile_id: number | null;
  custom_option_limit: number;
  items: Partial<Record<SalesAgentOptionKind, SalesAgentOptionCatalogEntry[]>>;
}

export function fetchSalesAgentOptionCatalog(params: { kind?: SalesAgentOptionKind; seller_profile_id?: number } = {}): Promise<SalesAgentOptionCatalogResponse> {
  const qs = new URLSearchParams();
  if (params.kind) qs.set('kind', params.kind);
  if (params.seller_profile_id) qs.set('seller_profile_id', String(params.seller_profile_id));
  const query = qs.toString();
  return jsonRequest(`/sales-agent/option-catalog${query ? `?${query}` : ''}`);
}

export function createSalesAgentOption(input: { kind: SalesAgentOptionKind; value: string; seller_profile_id: number; label?: string; description?: string }): Promise<SalesAgentOptionCatalogEntry> {
  return jsonRequest('/sales-agent/option-catalog', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
}

/** Change a custom item's display name / description. There's no value -- to change the term, delete and re-add, see update_custom_option. */
export function updateSalesAgentOption(
  optionId: number,
  patch: { label?: string; description?: string },
): Promise<SalesAgentOptionCatalogEntry> {
  return jsonRequest(`/sales-agent/option-catalog/${optionId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
}

export function archiveSalesAgentOption(optionId: number): Promise<SalesAgentOptionCatalogEntry> {
  return jsonRequest(`/sales-agent/option-catalog/${optionId}`, { method: 'DELETE' });
}

export function archiveSalesAgentOptionByValue(input: { kind: SalesAgentOptionKind | string; value: string; seller_profile_id?: number | null }): Promise<{ kind: string; value: string; seller_profile_id: number | null; archived?: SalesAgentOptionCatalogEntry | null; synced?: boolean }> {
  const qs = new URLSearchParams({ kind: input.kind, value: input.value });
  if (input.seller_profile_id) qs.set('seller_profile_id', String(input.seller_profile_id));
  return jsonRequest(`/sales-agent/option-catalog/by-value?${qs.toString()}`, { method: 'DELETE' });
}

export function updateSalesAgentLeadStatus(leadId: number, status: string, note = ''): Promise<{ lead: SalesAgentLead }> {
  return jsonRequest(`/sales-agent/leads/${leadId}/status`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status, reason: 'manual', note }),
  });
}

// ── Long-task progress stream ─────────────────────────────────────────────────────────
// For design and choices see docs/08-独立模块/SalesAgent/03-系统设计/产品体验与工程优化.md §8b.
// Read with fetch + ReadableStream, not EventSource -- the latter can't carry a JWT.

export interface SalesAgentReportProgress {
  type: 'report_progress' | 'ready' | 'idle';
  report_id: number;
  lead_ids: number[];
  status: string;
  done: number;
  total: number;
  current_step: string;
  elapsed_seconds: number;
  url?: string;
  brand_name?: string;
}

export async function streamSalesAgentEvents(
  onEvent: (event: SalesAgentReportProgress) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(`${API_BASE}/sales-agent/events`, { headers: authHeaders(), signal });
  if (!resp.ok || !resp.body) throw new Error(`进度流连接失败(${resp.status})`);

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // SSE framing: separated by blank lines; only lines starting with `data:` are payload (lines starting with `:` are heartbeat comments)
    const frames = buffer.split('\n\n');
    buffer = frames.pop() ?? '';
    for (const frame of frames) {
      for (const line of frame.split('\n')) {
        if (!line.startsWith('data:')) continue;
        try {
          onEvent(JSON.parse(line.slice(5).trim()));
        } catch { /* Partial frame or dirty data: drop this line, don't crash the whole stream */ }
      }
    }
  }
}
