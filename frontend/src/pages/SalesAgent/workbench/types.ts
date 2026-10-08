// Workbench internal types: the frontend display shape of backend cards, not the API contract (the contract is in services/salesAgentApi).
import { type ReportEmailStyleKey, type SalesAgentCard, type SalesAgentDraft, type SalesAgentLead, type SalesAgentQuestionCard, type SalesAgentUndoTarget } from '../../../services/salesAgentApi';

export type SalesAgentPreviewKind = 'companies' | 'draft' | 'profile' | 'question' | 'schedule' | 'generic';

export type SalesAgentProfileSection = { label: string; values: string[]; fullValues?: string[]; empty?: boolean };

export type SalesAgentPreviewCard = {
  kind: SalesAgentPreviewKind;
  title: string;
  undoTarget?: SalesAgentUndoTarget | null;
  meta?: string;
  desc?: string;
  score?: string;
  status?: string;
  items?: { label: string; value: string }[];
  profileSections?: SalesAgentProfileSection[];
  actions?: { label: string; text: string }[];
  tableRows?: { company: string; website: string; contact: string }[];
  companyCards?: { company: string; website: string; contact: string; meta?: string; reason?: string; rank?: string; score?: string; matchScore?: string; opportunityScore?: string; confidence?: string; recommendedAction?: string; scoreSummary?: string; leadId?: string; companyId?: string; reportStatus?: string; reportId?: string; publicReportUrl?: string; shareToken?: string; latestReportEmailDraft?: SalesAgentDraft | null; emailStatus?: string; emailStatusTone?: 'neutral' | 'success' | 'warning' | 'danger'; hasEmail?: boolean; recommendedAt?: string }[];
  readonlyHistory?: boolean;
  questionCard?: SalesAgentQuestionCard;
  /** Whether the current brand's materials include audit/diagnosis services. When false the whole card has no report entry (button, status, filter). */
  reportCapable?: boolean;
  raw?: SalesAgentCard;
};

export type ConfirmDraftItem = {
  draft: SalesAgentDraft;
  variants: SalesAgentDraft[];
  lead: SalesAgentLead | null;
  raw: SalesAgentCard;
  cached?: boolean;
};

export type LeadReportState = {
  status: string;
  publicUrl?: string;
  shareToken?: string;
  reportId?: string;
  // From the progress stream (02-设计-产品体验与工程优化 §8b); empty without a stream, falling back to static text
  done?: number;
  total?: number;
  currentStep?: string;
  elapsedSeconds?: number;
};

export type DraftLoadingState = {
  leadId: number;
  styleKey: ReportEmailStyleKey;
  // The following exist only for streaming intro letters: the header and footer are fixed by the backend and present once connected; the middle accumulates character by character and is finalized per paragraph.
  // This is only a **preview**; confirming send submits the one persisted in the `done` frame.
  greeting?: string;
  tail?: string[];
  // The model-written title. Empty while the model hasn't reached it / didn't produce one -- show a placeholder bar when empty,
  // not `fallbackSubject`, otherwise an old title flashes first and then gets replaced.
  subject?: string;
  // Template title used as a fallback when the model doesn't produce a title. Shown only after the body is finished or degraded.
  fallbackSubject?: string;
  // Paragraphs already received (the stripped version).
  paragraphs?: string[];
  // The paragraph being written, appended character by character from `chunk` frames. Replaced entirely when the same paragraph's `paragraph` frame arrives.
  pending?: string;
  // The body is finished (may still be persisting). The footer waits for this -- showing it right away looks like the letter is already done.
  bodyDone?: boolean;
  resetReason?: string;
};

export type CompanyRecommendationCardItem = NonNullable<SalesAgentPreviewCard['companyCards']>[number];
export type EmailRecordTarget = { companyName: string; leadId?: number; companyId?: number };
export type UndoRequestHandler = (target?: SalesAgentUndoTarget | null) => void;
