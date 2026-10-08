import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { ChevronDown, ChevronRight, ExternalLink, FileText, Maximize2, Minimize2, MoreHorizontal, X } from 'lucide-react';
import { AuthModal } from '../../components/AuthModal';
import { ThemeToggle } from '../../components/ThemeToggle';
import { Pagination } from '../../components/Pagination';
import { toast } from '../../components/ui/toast';
import { useAuth } from '../../contexts/AuthContext';
import { displayName } from '../../utils/userDisplay';
import { smsIsUnicode, smsSegments } from '../../utils/smsSegments';
import { A2UIReportRenderer } from './A2UIReportRenderer';
import { canRenderWithA2UI, isA2UISendScheduleCard, isA2UISendSchedulePickerCard, normalizeSendSchedulePicker, salesAgentCardToA2UI, type A2UISendSchedulePickerModel } from './a2uiBridge';
import { DeliveryContentDialog, DeliveryTable } from './SalesAgentEmailManager';
import {
  archiveSalesAgentOption,
  archiveSalesAgentOptionByValue,
  autoCreateOutreachDraft,
  confirmOutreachDraft,
  confirmSendSchedule,
  createSalesAgentOption,
  fetchSalesAgentEmailDeliveries,
  fetchSalesAgentSession,
  fetchSendSchedules,
  generateLeadReport,
  undoSalesAgentSession,
  fetchSalesAgentSessions,
  streamAutoCreateOutreachDraft,
  streamSalesAgentChat,
  type SalesAgentAutoDraftResponse,
  type SalesAgentBundle,
  type SalesAgentCard,
  type SalesAgentDraft,
  type SalesAgentEmailDelivery,
  type SalesAgentLead,
  type ReportEmailStyleKey,
  type SalesAgentMessage,
  type SalesAgentQuestionCard,
  type SalesAgentQuestionOption,
  type SalesAgentSessionItem,
  type SalesAgentUndoTarget,
} from '../../services/salesAgentApi';
import { AgentStatus } from '../../components/aui/AgentStatus';
import { cn } from '../../components/aui/cn';
import { ToolCall } from '../../components/aui/ToolCall';
import { SalesAgentThread, auiShellEnabled } from '../../components/aui/SalesAgentThread';
import { streamSalesAgentEvents } from '../../services/salesAgentApi';
import { field, inkButton, paper } from '../../components/aui/surfaces';
import { UiSelect } from '../../components/ui/Select';
import { type CompanyRecommendationCardItem, type ConfirmDraftItem, type DraftLoadingState, type EmailRecordTarget, type LeadReportState, type SalesAgentPreviewCard, type SalesAgentProfileSection, type UndoRequestHandler } from './workbench/types';
import { NO_REPORT_EMAIL_STYLES, REPORT_EMAIL_STYLES, SALES_AGENT_BATCH_CONCURRENCY, SALES_AGENT_RECOMMENDATION_LIMIT } from './workbench/constants';
import { asList, asRecord, boolOf, copyTextToClipboard, fmtTime, friendlyError, isJwtExpired, linkifyPlainText, looksLikeEmail, numberOf, runWithConcurrency, shortText, textOf } from './workbench/utils';
import { canDeleteQuestionOption, isNoLimitQuestionOption, isTargetIndustryQuestion, isTargetRegionQuestion, isTypicalCustomersQuestion, nextUserAnswer, normalizeChoiceValues, optionKindForQuestion, orderQuestionOptions, sellerProfileIdForOption, submittedSecondaryValuesForQuestion, submittedValuesForQuestion } from './workbench/question';
import { confirmDraftItemsFromCards, draftHtmlContent, emailStatusInfo, emailStatusToneStyle, isNoReportDefaultDraft, isNoReportEmailStyleKey, isStreamableIntroStyleKey, normalizeEmailStyleKey, normalizeNoReportEmailStyleKey, normalizeReportEmailStyleKey, reportEmailVariantCount } from './workbench/draft';
import { isReportCard, mergeSessionLists, normalizeChatMessages, pickReportValue, previewCardsOf, reportPageTarget, reportProgress, reportStatusLabel, reportTextList, thinkingStepsOf, userFacingActionText } from './workbench/cards';
import { MarkdownView } from './workbench/markdown';
import { SendSchedulePanel, type SendScheduleView } from './workbench/SendSchedulePanel';
import { SendScheduleBatchDrawer } from './workbench/SendScheduleBatchDrawer';

const page: CSSProperties = { minHeight: '100vh', height: '100vh', background: 'var(--bg-primary)', color: 'var(--text-primary)', fontFamily: 'ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif', display: 'flex', overflow: 'hidden', position: 'relative' };
const sidebar: CSSProperties = { width: 88, background: 'var(--aui-background)', borderRight: '1px solid var(--aui-border)', display: 'flex', flexDirection: 'column', flexShrink: 0, backdropFilter: 'blur(18px)', WebkitBackdropFilter: 'blur(18px)', position: 'relative', zIndex: 1, overflow: 'hidden' };
const main: CSSProperties = { flex: 1, minWidth: 0, display: 'flex', overflow: 'hidden', position: 'relative', zIndex: 1 };
// These two are spread-referenced in dozens of places across the page; changing them here unifies all buttons at once.
// Use --aui-* rather than --solid-btn-*: the latter is a linear-gradient in light/dark,
// and mixed with the flat palette of Elements it looks like two different products.
const primaryBtn: CSSProperties = { border: 'none', background: 'var(--aui-primary)', color: 'var(--aui-primary-foreground)', borderRadius: 999, padding: '10px 16px', cursor: 'pointer', fontWeight: 700 };
const ghostBtn: CSSProperties = { border: '1px solid var(--aui-border)', background: 'var(--aui-card)', color: 'var(--aui-muted-foreground)', borderRadius: 12, padding: '8px 10px', cursor: 'pointer' };

function SidebarToggleIcon({ direction }: { direction: 'left' | 'right' }) {
  return (
    <svg width="19" height="19" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <rect x="3.5" y="4" width="17" height="16" rx="4" stroke="currentColor" strokeWidth="1.8" />
      <path d="M9 4.8v14.4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" opacity=".45" />
      {direction === 'right' ? (
        <path d="M13.2 9.2 16 12l-2.8 2.8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      ) : (
        <path d="M16 9.2 13.2 12 16 14.8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      )}
    </svg>
  );
}

function NewChatIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M6.8 5.5h7.4c2.8 0 4.5 1.6 4.5 4.4v3.2c0 2.8-1.7 4.4-4.5 4.4h-2.7l-3.2 2.4c-.5.4-1.2 0-1.2-.6v-1.8h-.3c-2.8 0-4.5-1.6-4.5-4.4V9.9c0-2.8 1.7-4.4 4.5-4.4Z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
      <path d="M10.5 8.8v5.4M7.8 11.5h5.4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <path d="M16.2 6.2l.5-1.2.5 1.2 1.2.5-1.2.5-.5 1.2-.5-1.2-1.2-.5 1.2-.5Z" fill="currentColor" />
    </svg>
  );
}

function SendIcon() {
  return (
    <svg width="21" height="21" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M4.6 11.2 18.7 4.7c.7-.3 1.4.4 1.1 1.1l-6.5 14.1c-.3.7-1.3.6-1.5-.1l-1.7-5.7-5.7-1.7c-.8-.2-.9-1.2-.2-1.5Z" stroke="currentColor" strokeWidth="1.9" strokeLinejoin="round" />
      <path d="m10.3 13.8 3.8-3.8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function LeftSidebar({
  sessions,
  activeSessionId,
  onSelectSession,
  onNewChat,
  expanded,
  onToggle,
  valid,
  userLabel,
  onLogin,
  onLogout,
  sessionsHasMore,
  sessionsLoadingMore,
  onLoadMoreSessions,
}: {
  sessions: SalesAgentSessionItem[];
  activeSessionId: string | null;
  onSelectSession: (id: string) => void;
  onNewChat: () => void;
  expanded: boolean;
  onToggle: () => void;
  valid: boolean;
  userLabel: string;
  onLogin: () => void;
  onLogout: () => void;
  sessionsHasMore: boolean;
  sessionsLoadingMore: boolean;
  onLoadMoreSessions: () => void;
}) {
  return (
    <aside style={{ ...sidebar, width: expanded ? 268 : 88, transition: 'width .2s ease' }}>
      <div style={{ padding: expanded ? '22px 18px 14px' : '22px 0 14px', display: 'flex', alignItems: 'center', justifyContent: expanded ? 'space-between' : 'center' }}>
        <a href="/" aria-label="返回首页" style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0, textDecoration: 'none' }}>
          <img className="brand-logo" src="/image/logo.svg" alt="Vigilath" style={{ height: 32, width: 'auto', display: 'block', flexShrink: 0 }} />
          {expanded && <span style={{ color: 'var(--text-primary)', fontSize: 18, fontWeight: 900 }}>Vigilath</span>}
        </a>
        {expanded && <button title="收起侧边栏" aria-label="收起侧边栏" style={{ ...ghostBtn, width: 34, height: 34, borderRadius: 12, padding: 0, display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }} onClick={onToggle}><SidebarToggleIcon direction="left" /></button>}
      </div>
      {!expanded && <button title="展开侧边栏" aria-label="展开侧边栏" style={{ width: 48, height: 34, borderRadius: 14, margin: '0 auto 12px', border: '1px solid var(--border-color)', background: 'var(--bg-surface)', color: 'var(--text-secondary)', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: 'var(--shadow-card)' }} onClick={onToggle}><SidebarToggleIcon direction="right" /></button>}
      <div style={{ padding: expanded ? '0 18px 12px' : '0 0 14px', display: 'flex', justifyContent: 'center' }}>
        <button title="开启新对话" aria-label="开启新对话" style={{ width: expanded ? '100%' : 42, height: expanded ? 36 : 42, borderRadius: expanded ? 12 : 14, border: '1px solid var(--border-color)', background: 'var(--bg-surface)', color: 'var(--text-primary)', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, cursor: 'pointer', boxShadow: 'var(--shadow-card)', fontSize: 13 }} onClick={onNewChat}>
          <NewChatIcon />
          {expanded && '开启新对话'}
        </button>
      </div>
      <div style={{ width: expanded ? 'calc(100% - 36px)' : 34, height: 1, background: 'var(--border-color)', margin: '0 auto 12px' }} />
      <div style={{ color: 'var(--text-muted)', fontSize: 12, textAlign: expanded ? 'left' : 'center', padding: expanded ? '0 18px' : 0, marginBottom: 8 }}>对话历史</div>
      <div style={{ flex: 1, overflowY: 'auto', padding: expanded ? '0 12px 12px' : '0 0 12px', display: 'grid', justifyItems: expanded ? 'stretch' : 'center', alignContent: 'start', gap: 8 }}>
        {sessions.map((s, i) => (
          <button key={s.id} title={`${s.title || `Sales Agent #${s.id}`} ${fmtTime(s.updated_at)}`} onClick={() => onSelectSession(s.id)} style={{ width: expanded ? '100%' : 42, height: expanded ? 'auto' : 42, textAlign: expanded ? 'left' : 'center', border: activeSessionId === s.id ? '1px solid var(--border-strong)' : '1px solid var(--border-color)', background: activeSessionId === s.id ? 'var(--bg-surface)' : 'transparent', borderRadius: 14, cursor: 'pointer', color: activeSessionId === s.id ? 'var(--text-primary)' : 'var(--text-secondary)', boxShadow: activeSessionId === s.id ? 'var(--shadow-card)' : 'none', fontSize: 12, fontWeight: 800, padding: expanded ? '9px 10px' : 0 }}>
            {expanded ? <><div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.title || `Sales Agent #${s.id}`}</div><div style={{ color: 'var(--text-muted)', fontSize: 12, marginTop: 3 }}>{fmtTime(s.updated_at)}</div></> : String(i + 1).padStart(2, '0')}
          </button>
        ))}
        {!sessions.length && <div title="暂无历史对话" style={{ color: 'var(--text-muted)', fontSize: expanded ? 13 : 16, lineHeight: expanded ? '1.7' : '42px', textAlign: 'center', padding: expanded ? 10 : 0 }}>{expanded ? '暂无历史对话' : '—'}</div>}
        {sessionsHasMore && (
          <button type="button" title="加载更多历史对话" disabled={sessionsLoadingMore} onClick={onLoadMoreSessions} style={{ width: expanded ? '100%' : 42, height: expanded ? 34 : 42, borderRadius: 14, border: '1px solid var(--border-color)', background: 'var(--bg-surface)', color: sessionsLoadingMore ? 'var(--text-muted)' : 'var(--text-secondary)', cursor: sessionsLoadingMore ? 'not-allowed' : 'pointer', fontSize: 12, fontWeight: 800, boxShadow: 'var(--shadow-card)' }}>
            {expanded ? (sessionsLoadingMore ? '加载中…' : '加载更多') : '···'}
          </button>
        )}
      </div>
      <div style={{ padding: expanded ? '12px 14px 18px' : '0 0 18px' }}>
        {expanded ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, background: 'var(--bg-card)', border: '1px solid var(--border-color)', borderRadius: 16, padding: '10px 12px', boxShadow: 'var(--shadow-card)' }}>
            <div style={{ width: 34, height: 34, borderRadius: '50%', background: valid ? 'var(--accent-primary)' : 'var(--bg-tertiary)', color: valid ? 'var(--accent-contrast)' : 'var(--text-muted)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 900, flexShrink: 0 }}>
              {valid ? userLabel.slice(0, 1).toUpperCase() : '登'}
            </div>
            <div style={{ flex: 1, minWidth: 0, color: 'var(--text-primary)', fontWeight: 800, fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {valid ? userLabel : '登录 / 注册'}
            </div>
            <button style={{ ...ghostBtn, borderRadius: 999, padding: '6px 10px', fontSize: 12 }} onClick={valid ? onLogout : onLogin}>{valid ? '退出' : '登录'}</button>
          </div>
        ) : (
          <button title={valid ? `${userLabel} · 展开侧边栏` : '展开侧边栏后登录 / 注册'} aria-label="展开侧边栏" onClick={onToggle} style={{ width: 42, height: 42, borderRadius: 14, margin: '0 auto', border: '1px solid var(--border-color)', background: valid ? 'var(--accent-primary)' : 'var(--bg-surface)', color: valid ? 'var(--accent-contrast)' : 'var(--text-primary)', display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', boxShadow: 'var(--shadow-card)', fontWeight: 900, fontSize: 13 }}>
            {valid ? userLabel.slice(0, 1).toUpperCase() : '登'}
          </button>
        )}
      </div>
    </aside>
  );
}

function CenterChat({ messages, input, setInput, busy, onSend, onSendText, onUndo, valid, onNeedLogin, hideInput = false, compactRecommendations = false, onOpenDraftCard, onOpenDraftItem, onGenerateDraft, onGenerateReport, onBatchGenerateReports, onBatchGenerateDrafts, onOpenEmailRecords, onScheduleSend, onOpenSchedules, onOpenBatchPicker, pendingScheduleCount = 0, draftGeneratingLeadId = null, reportGeneratingLeadId = null, leadReports = {} }: { messages: SalesAgentMessage[]; input: string; setInput: (v: string) => void; busy: boolean; onSend: () => void; onSendText: (text: string) => void; onUndo: UndoRequestHandler; valid: boolean; onNeedLogin: () => void; hideInput?: boolean; compactRecommendations?: boolean; onOpenDraftCard?: (card: SalesAgentCard) => void; onOpenDraftItem?: (item: ConfirmDraftItem) => void; onGenerateDraft?: (leadId: string) => void; onGenerateReport?: (leadId: string) => void; onBatchGenerateReports?: (leadIds: string[]) => void; onBatchGenerateDrafts?: (leadIds: string[]) => void; onOpenEmailRecords?: (target: EmailRecordTarget) => void; onScheduleSend?: (leadIds: string[]) => void; onOpenSchedules?: (scheduleId?: number) => void; onOpenBatchPicker?: (card: SalesAgentCard) => void; pendingScheduleCount?: number; draftGeneratingLeadId?: number | null; reportGeneratingLeadId?: number | null; leadReports?: Record<number, LeadReportState> }) {
  const endRef = useRef<HTMLDivElement>(null);
  const empty = messages.length <= 1 && !messages.some(m => m.role === 'user');
  const latestAssistantIndex = messages.map(m => m.role).lastIndexOf('assistant');
  const readOnlyActions = hideInput;
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages]);

  // Message content rendering is unchanged -- the new shell only changes "how the conversation area runs", not what's drawn in the bubbles
  const renderBubble = (m: SalesAgentMessage, i: number) => {
    const isLatestAssistant = m.role === 'assistant' && i === latestAssistantIndex;
    const submittedText = m.role === 'assistant' ? nextUserAnswer(messages, i) : '';
    return <MessageBubble key={m.id ?? i} msg={m} busy={busy && i === messages.length - 1} actionsDisabled={readOnlyActions || busy || !isLatestAssistant} undoDisabled={readOnlyActions || busy} companyActionsDisabled={busy} submittedText={submittedText} onSendText={onSendText} onUndo={onUndo} onOpenDraftCard={onOpenDraftCard} onOpenDraftItem={onOpenDraftItem} onGenerateDraft={onGenerateDraft} onGenerateReport={onGenerateReport} onBatchGenerateReports={onBatchGenerateReports} onBatchGenerateDrafts={onBatchGenerateDrafts} onOpenEmailRecords={onOpenEmailRecords} onScheduleSend={onScheduleSend} onOpenSchedules={onOpenSchedules} onOpenBatchPicker={onOpenBatchPicker} pendingScheduleCount={pendingScheduleCount} draftGeneratingLeadId={draftGeneratingLeadId} reportGeneratingLeadId={reportGeneratingLeadId} leadReports={leadReports} compactRecommendations={compactRecommendations} />;
  };

  // Gradual rollout: when on, use the assistant-ui shell; when off, the old path as before. Both paths share the same renderBubble.
  if (!empty && auiShellEnabled()) {
    return (
      <main style={{ minWidth: 0, display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden', background: 'transparent', position: 'relative' }}>
        <div style={{ height: 64, display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 12, padding: '0 26px', color: 'var(--text-secondary)' }}>
          <ThemeToggle />
          <span>Sales Agent</span>
        </div>
        <SalesAgentThread messages={messages} busy={busy} onSendText={onSendText} renderMessage={renderBubble} composerDisabled={hideInput} />
      </main>
    );
  }

  return (
    <main style={{ minWidth: 0, display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden', background: 'transparent', position: 'relative' }}>
      <div style={{ height: 64, display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 12, padding: '0 26px', color: 'var(--text-secondary)' }}>
        <ThemeToggle />
        <span>Sales Agent</span>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: empty ? '10vh 24px 24px' : '24px', display: 'flex', flexDirection: 'column' }}>
        {empty ? (
          <div style={{ textAlign: 'center', margin: '56px auto 0', width: '100%', maxWidth: 860 }}>
            <div className="gradient-text" style={{ fontSize: 36, fontWeight: 900, marginBottom: 28, letterSpacing: '-0.04em' }}>今天要跟进哪些销售线索?</div>
            {!hideInput && <InputBox input={input} setInput={setInput} busy={busy} onSend={onSend} valid={valid} onNeedLogin={onNeedLogin} large />}
          </div>
        ) : (
          <>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 18, width: '100%', maxWidth: 860, margin: '0 auto' }}>
              {messages.map((m, i) => renderBubble(m, i))}
              <div ref={endRef} />
            </div>
          </>
        )}
      </div>
      {!empty && !hideInput && <div style={{ padding: '14px 24px 22px', background: 'transparent' }}><div style={{ maxWidth: 860, margin: '0 auto' }}><InputBox input={input} setInput={setInput} busy={busy} onSend={onSend} valid={valid} onNeedLogin={onNeedLogin} /></div></div>}
    </main>
  );
}

function ThinkingArrowIcon({ open }: { open: boolean }) {
  return (
    <svg width="18" height="18" viewBox="0 0 20 20" fill="none" aria-hidden="true" style={{ transform: open ? 'rotate(180deg)' : 'rotate(0deg)', transition: 'transform .18s ease' }}>
      <path d="M5.2 7.4 10 12.2l4.8-4.8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

type ToolCallRow = { label: string; activeLabel: string; query?: string; result?: string };

// Tool name -> user-facing wording. Users never see function names (prompts explicitly forbid exposing them too).
const TOOL_LABELS: Record<string, [done: string, running: string]> = {
  recommend_companies: ['推荐目标企业', '正在筛选目标企业'],
  discover_companies: ['公网搜索企业', '正在公网搜索企业'],
  rank_companies: ['排序目标企业', '正在排序目标企业'],
  generate_detection_report: ['发起诊断报告', '正在发起诊断报告'],
  summarize_report: ['读取报告结论', '正在读取报告结论'],
  build_customer_profile: ['保存服务画像', '正在保存服务画像'],
  get_current_customer_profile: ['读取服务画像', '正在读取服务画像'],
  get_outreach_copy_context: ['准备触达上下文', '正在准备触达上下文'],
  generate_outreach_copy: ['生成邮件草稿', '正在生成邮件草稿'],
  update_lead_status: ['更新线索状态', '正在更新线索状态'],
  get_outreach_delivery_status: ['查发送情况', '正在查发送情况'],
  record_compliance_event: ['记录合规事件', '正在记录合规事件'],
  // Pseudo tool names used only for cards (not Pydantic AI tools; cards inserted by deterministic flows themselves)
  sales_agent_seller_profile_summary: ['读取画像', '正在读取画像'],
  sales_agent_guidance: ['整理下一步', '正在整理下一步'],
  sales_agent_entry_action: ['整理下一步', '正在整理下一步'],
  sales_agent_detection_url: ['确认检测网址', '正在确认检测网址'],
  sales_agent_detection_keywords: ['确认行业关键词', '正在确认行业关键词'],
};

// Unrecognized tool names all go here -- **never display the key as-is**.
// On 2026-08-23 production once leaked `sales_agent_seller_profile_summary`:
// prompts.py explicitly forbids exposing tool/function/table names, and showing the raw text as a fallback bypasses that constraint.
const TOOL_LABEL_FALLBACK: [done: string, running: string] = ['处理中间步骤', '正在处理'];

function toolCallQuery(tool: string, data: Record<string, unknown>): string {
  if (/recommend|discover|rank/.test(tool)) {
    const parts = [
      numberOf(data.requested_limit) ? `${numberOf(data.requested_limit)} 家` : '',
      asList<string>(data.industries).join('、'),
      asList<string>(data.regions).join('、'),
    ].filter(Boolean);
    return parts.join(' · ');
  }
  if (/report/.test(tool)) return textOf(data.url ?? data.brand_name, '');
  if (/delivery/.test(tool)) return textOf(asRecord(data.filters).company_name, '');
  return '';
}

function toolCallResult(tool: string, data: Record<string, unknown>): string {
  if (/recommend|discover|rank/.test(tool)) {
    const count = numberOf(data.actual_count ?? data.count);
    const shortfall = numberOf(data.shortfall);
    if (!count && !shortfall) return '';
    return shortfall ? `${count} 家,缺口 ${shortfall} 家` : `${count} 家`;
  }
  if (/report/.test(tool)) {
    const status = textOf(data.status, '');
    const score = data.total_score;
    return score != null ? `${status || '已完成'} · 总分 ${textOf(score, '-')}` : (status ? reportStatusLabel(status) : '');
  }
  if (/delivery/.test(tool)) {
    const sum = asRecord(data.summary);
    return `发出 ${numberOf(sum.sent)} · 被打开 ${numberOf(sum.opened)}`;
  }
  return '';
}

function guidanceToolLabel(data: Record<string, unknown>): [done: string, running: string] {
  const q = asRecord(data.question_card);
  const id = textOf(q.id, '').toLowerCase();
  const field = textOf(q.field, '').toLowerCase();
  const title = textOf(q.title || data.title, '');
  const key = `${id} ${field} ${title}`;

  if (id === 'sales_agent_entry_action') return ['选择操作', '正在准备操作'];
  if (key.includes('seller_capabilities') || key.includes('seller_profile') || title.includes('专业能力')) return ['确认专业能力', '正在确认专业能力'];
  if (key.includes('typical_customers') || title.includes('客户类型') || title.includes('适合客户')) return ['确认适合客户', '正在确认适合客户'];
  if (key.includes('target_company_size') || title.includes('企业规模')) return ['确认目标规模', '正在确认目标规模'];
  if (key.includes('target_industries') || title.includes('目标行业')) return ['确认目标行业', '正在确认目标行业'];
  if (key.includes('target_regions') || title.includes('目标地区')) return ['确认目标地区', '正在确认目标地区'];
  return ['确认补充信息', '正在确认补充信息'];
}

function toolCallRowsOf(msg: SalesAgentMessage): ToolCallRow[] {
  const cards = msg.metadata?.tool_cards;
  if (!Array.isArray(cards)) return [];
  const rows: ToolCallRow[] = [];
  for (const raw of cards) {
    const card = raw as SalesAgentCard | null;
    const tool = String(card?.tool || '').trim();
    if (!tool) continue;
    const data = asRecord(card?.data);
    // Guidance cards are uniformly stamped tool="recommend_companies" by the backend (guidance.py:363),
    // and following it would display "Recommend target companies", while the card actually asks "Which step do you want first?" -- a misnomer.
    // Judging by card content takes precedence over the tool name.
    const isGuidance = boolOf(data.needs_user_input, false) || textOf(data.status, '') === 'need_more_info';
    const [label, activeLabel] = isGuidance
      ? guidanceToolLabel(data)
      : (TOOL_LABELS[tool] || TOOL_LABEL_FALLBACK);
    rows.push({
      label,
      activeLabel,
      // A guidance card just asks for the next step, recommendations haven't started; don't render the default requested_limit as "preparing the next 100 companies".
      query: isGuidance ? undefined : (toolCallQuery(tool, data) || undefined),
      result: isGuidance ? undefined : (toolCallResult(tool, data) || undefined),
    });
  }
  return rows;
}

function ProfileSectionsPanel({ sections, intro }: { sections: SalesAgentProfileSection[]; intro?: string }) {
  const [open, setOpen] = useState(false);
  const visibleSections = sections.filter(section => section.values.length);
  const valuesOf = (section: SalesAgentProfileSection) => section.empty ? [] : (section.fullValues || section.values.filter(value => !value.startsWith('+')));
  const summaryValuesOf = (section: SalesAgentProfileSection) => section.empty ? section.values : valuesOf(section);
  const total = visibleSections.reduce((sum, section) => sum + valuesOf(section).length, 0);
  const primary = visibleSections.find(section => section.label.includes('专业能力') && !section.empty) || visibleSections.find(section => !section.empty) || visibleSections[0];
  const profileSignal = primary ? summaryValuesOf(primary).slice(0, 3) : [];
  if (!visibleSections.length) return null;

  const sectionTone = (section: SalesAgentProfileSection) => section.empty ? '#f59e0b' : section.label.includes('适合') ? '#22c55e' : section.label.includes('规模') ? '#6366f1' : 'var(--accent-primary)';

  return (
    <div style={{ marginTop: 12, border: '1px solid var(--aui-border)', background: 'linear-gradient(180deg, var(--aui-card) 0%, var(--bg-surface) 100%)', borderRadius: 18, overflow: 'hidden', boxShadow: '0 10px 26px rgba(15,23,42,.06)' }}>
      <button type="button" onClick={() => setOpen(v => !v)} style={{ width: '100%', border: 'none', background: 'transparent', color: 'var(--text-primary)', padding: '13px 14px 11px', display: 'grid', gridTemplateColumns: '1fr auto', gap: 12, cursor: 'pointer', textAlign: 'left', alignItems: 'center' }}>
        <span style={{ minWidth: 0, display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ width: 34, height: 34, borderRadius: 12, background: 'var(--aui-accent)', color: 'var(--accent-primary)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 16, fontWeight: 900, flexShrink: 0 }}>方</span>
          <span style={{ minWidth: 0, display: 'grid', gap: 3 }}>
            <span style={{ fontSize: 14, fontWeight: 950, letterSpacing: '-0.01em' }}>我方画像</span>
            <span style={{ color: 'var(--text-muted)', fontSize: 12, fontWeight: 700 }}>{visibleSections.length} 个维度 · {total} 条信息，用来匹配下一批目标客户</span>
          </span>
        </span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: 'var(--text-muted)', fontSize: 12, fontWeight: 900, flexShrink: 0 }}>
          {open ? '收起' : '展开'} <ThinkingArrowIcon open={open} />
        </span>
      </button>

      <div style={{ borderTop: '1px solid var(--border-color)', padding: '12px 14px 14px', display: 'grid', gap: 11 }}>
        {!open && (
          <>
            {intro && <div style={{ color: 'var(--text-secondary)', fontSize: 14, lineHeight: 1.65 }}>{intro}</div>}
            {primary && (
              <div style={{ border: '1px solid var(--aui-border)', background: 'var(--aui-muted)', borderRadius: 15, padding: '10px 11px', display: 'grid', gap: 8 }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
                  <span style={{ color: 'var(--text-primary)', fontSize: 12, fontWeight: 950 }}>{primary.label}</span>
                  <span style={{ color: 'var(--text-muted)', fontSize: 11, fontWeight: 800 }}>核心卖点</span>
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  {profileSignal.map((value, i) => <span key={`${primary.label}-signal-${value}-${i}`} style={{ border: '1px solid var(--aui-border)', background: 'var(--aui-card)', color: 'var(--text-primary)', borderRadius: 999, padding: '4px 8px', fontSize: 12, fontWeight: 850, lineHeight: 1.25 }}>{shortText(value, 22)}</span>)}
                  {valuesOf(primary).length > profileSignal.length && <span style={{ color: 'var(--text-muted)', fontSize: 12, fontWeight: 800, alignSelf: 'center' }}>+{valuesOf(primary).length - profileSignal.length} 项</span>}
                </div>
              </div>
            )}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 8 }}>
              {visibleSections.map((section, i) => {
                const values = summaryValuesOf(section);
                const count = valuesOf(section).length;
                return (
                  <div key={`${section.label}-${i}`} style={{ border: '1px solid var(--aui-border)', background: 'var(--bg-surface)', borderRadius: 14, padding: '9px 10px', minWidth: 0, minHeight: 64, display: 'grid', gap: 5, alignContent: 'space-between' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
                      <span style={{ width: 7, height: 7, borderRadius: '50%', background: sectionTone(section), flexShrink: 0 }} />
                      <span style={{ color: 'var(--text-primary)', fontSize: 12, fontWeight: 900, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{section.label}</span>
                    </div>
                    <div style={{ color: 'var(--text-muted)', fontSize: 11, fontWeight: 750, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{section.empty ? '待补充' : `${count} 项 · ${shortText(values[0], 20)}`}</div>
                  </div>
                );
              })}
            </div>
          </>
        )}

        {open && (
          <div style={{ maxHeight: 'min(58vh, 620px)', overflowY: 'auto', paddingRight: 4 }}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 10 }}>
              {visibleSections.map((section, i) => {
                const values = summaryValuesOf(section);
                const count = valuesOf(section).length;
                return (
                  <div key={`${section.label}-${i}`} style={{ border: '1px solid var(--aui-border)', background: 'var(--aui-muted)', borderRadius: 15, padding: 11, minHeight: 116, display: 'grid', gap: 9, alignContent: 'start' }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7, color: 'var(--text-primary)', fontSize: 12, fontWeight: 950 }}><span style={{ width: 8, height: 8, borderRadius: '50%', background: sectionTone(section) }} />{section.label}</span>
                      <span style={{ color: 'var(--text-muted)', fontSize: 11, fontWeight: 850 }}>{section.empty ? '待补充' : `${count} 项`}</span>
                    </div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, maxHeight: count > 10 ? 260 : undefined, overflowY: count > 10 ? 'auto' : undefined, paddingRight: count > 10 ? 3 : undefined }}>
                      {values.map((value, j) => {
                        const longText = value.length > 20;
                        return (
                          <span
                            key={`${section.label}-${value}-${j}`}
                            style={section.empty
                              ? { border: '1px dashed var(--aui-border)', background: 'transparent', color: 'var(--text-muted)', borderRadius: 999, padding: '4px 8px', fontSize: 12, fontWeight: 850, lineHeight: 1.25 }
                              : longText
                                ? { width: '100%', border: '1px solid var(--aui-border)', background: 'var(--aui-card)', color: 'var(--text-secondary)', borderRadius: 12, padding: '7px 9px', fontSize: 12, fontWeight: 700, lineHeight: 1.45 }
                                : { border: '1px solid var(--aui-border)', background: 'var(--aui-card)', color: 'var(--text-secondary)', borderRadius: 999, padding: '4px 8px', fontSize: 12, fontWeight: 850, lineHeight: 1.25 }}
                          >
                            {value}
                          </span>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function UndoConfirmDialog({ open, busy, target, onCancel, onConfirm }: { open: boolean; busy: boolean; target?: SalesAgentUndoTarget | null; onCancel: () => void; onConfirm: () => void }) {
  if (!open) return null;
  const label = (target?.label || '').trim();
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 80, display: 'grid', placeItems: 'center', padding: 20 }}>
      <button type="button" aria-label="取消撤销" onClick={busy ? undefined : onCancel} style={{ position: 'absolute', inset: 0, border: 'none', background: 'rgba(15,23,42,.42)', cursor: busy ? 'default' : 'pointer' }} />
      <div role="dialog" aria-modal="true" aria-labelledby="sales-agent-undo-title" style={{ position: 'relative', width: 'min(460px, 100%)', border: '1px solid var(--border-color)', background: 'var(--bg-card)', borderRadius: 18, padding: 18, boxShadow: '0 24px 70px rgba(15,23,42,.25)', color: 'var(--text-primary)' }}>
        <div id="sales-agent-undo-title" style={{ fontSize: 18, fontWeight: 950, letterSpacing: '-0.02em' }}>确认撤销到这一步?</div>
        <div style={{ color: 'var(--text-secondary)', fontSize: 13, lineHeight: 1.7, marginTop: 10 }}>
          确认后会回到{label ? `「${label}」` : '这张'}可编辑卡片继续填写,并撤销这张卡之后生成的对话卡片和未确认推荐结果。已外发邮件、已人工推进的线索状态不会被反向撤回。
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 18 }}>
          <button type="button" disabled={busy} onClick={onCancel} style={{ ...ghostBtn, padding: '8px 13px', fontSize: 13, opacity: busy ? .55 : 1, cursor: busy ? 'not-allowed' : 'pointer' }}>取消</button>
          <button type="button" disabled={busy} onClick={onConfirm} style={{ ...primaryBtn, padding: '8px 14px', fontSize: 13, opacity: busy ? .65 : 1, cursor: busy ? 'not-allowed' : 'pointer' }}>{busy ? '撤销中…' : '确认撤销'}</button>
        </div>
      </div>
    </div>
  );
}

function ThinkingSteps({ steps, status }: { steps: string[]; status: 'running' | 'done' | 'failed' }) {
  const [open, setOpen] = useState(false);
  // Hooks must come before early returns: when steps goes from empty to non-empty the hook count jumps from 0 to 1,
  // and React throws "Rendered more hooks than during the previous render".
  if (!steps.length) return null;
  const failed = status === 'failed';
  const running = status === 'running';

  const displaySteps = Array.from(new Set(['开始', ...steps.filter(step => step !== '理解销售目标' && step !== '思考销售线索方向')]));
  const title = failed ? '处理未完成' : running ? '正在思考...' : '处理过程';
  const tone = running || failed ? 'var(--accent-primary)' : 'var(--text-muted)';
  return (
    <div style={{ width: 'min(380px, 100%)', marginBottom: 12 }}>
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        style={{
          width: '100%',
          border: '1px solid var(--aui-border)',
          borderBottomColor: 'var(--aui-border)',
          borderRadius: open ? '16px 16px 0 0' : 16,
          background: 'var(--aui-card)',
          color: 'var(--text-primary)',

          padding: '12px 14px',
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          cursor: 'pointer',
          textAlign: 'left',
        }}
      >
        <span className={running ? 'animate-spin' : undefined} style={{ width: 18, height: 18, borderRadius: '50%', border: `3px solid var(--aui-border)`, borderTopColor: tone, flexShrink: 0 }} />
        <span style={{ flex: 1, minWidth: 0, fontSize: 17, fontWeight: 900, letterSpacing: '-0.02em' }}>{title}</span>
        <span style={{ color: 'var(--text-muted)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>
          <ThinkingArrowIcon open={open} />
        </span>
      </button>
      {open && (
        <div
          style={{
            border: '1px solid var(--aui-border)',
            borderTop: 'none',
            borderRadius: '0 0 16px 16px',
            background: 'var(--aui-card)',

            padding: '12px 16px 14px',
          }}
        >
          <div style={{ height: 1, background: 'var(--aui-border)', marginBottom: 12 }} />
          <div style={{ display: 'grid', gap: 12 }}>
            {displaySteps.map((step, i) => {
              const activeStep = running && i === displaySteps.length - 1;
              const dotColor = activeStep ? 'var(--aui-primary)' : 'var(--aui-muted-foreground)';
              return (
                <div key={`${step}-${i}`} style={{ display: 'flex', alignItems: 'center', gap: 12, color: 'var(--text-secondary)', fontSize: 16, lineHeight: 1.4 }}>
                  <span style={{ width: 9, height: 9, borderRadius: '50%', background: dotColor, boxShadow: 'none', flexShrink: 0 }} />
                  <span>{step}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function MessageBubble({ msg, busy, actionsDisabled, undoDisabled = actionsDisabled, companyActionsDisabled = actionsDisabled, submittedText = '', onSendText, onUndo, onOpenDraftCard, onOpenDraftItem, onGenerateDraft, onGenerateReport, onBatchGenerateReports, onBatchGenerateDrafts, onOpenEmailRecords, onScheduleSend, onOpenSchedules, onOpenBatchPicker, pendingScheduleCount = 0, draftGeneratingLeadId = null, reportGeneratingLeadId = null, leadReports = {}, compactRecommendations = false }: { msg: SalesAgentMessage; busy: boolean; actionsDisabled: boolean; undoDisabled?: boolean; companyActionsDisabled?: boolean; submittedText?: string; onSendText: (text: string) => void; onUndo: UndoRequestHandler; onOpenDraftCard?: (card: SalesAgentCard) => void; onOpenDraftItem?: (item: ConfirmDraftItem) => void; onGenerateDraft?: (leadId: string) => void; onGenerateReport?: (leadId: string) => void; onBatchGenerateReports?: (leadIds: string[]) => void; onBatchGenerateDrafts?: (leadIds: string[]) => void; onOpenEmailRecords?: (target: EmailRecordTarget) => void; onScheduleSend?: (leadIds: string[]) => void; onOpenSchedules?: (scheduleId?: number) => void; onOpenBatchPicker?: (card: SalesAgentCard) => void; pendingScheduleCount?: number; draftGeneratingLeadId?: number | null; reportGeneratingLeadId?: number | null; leadReports?: Record<number, LeadReportState>; compactRecommendations?: boolean }) {
  const user = msg.role === 'user';
  const steps = user ? [] : thinkingStepsOf(msg);
  const toolCalls = user ? [] : toolCallRowsOf(msg);
  const previews = user ? [] : previewCardsOf(msg);
  const failed = !user && msg.metadata?.process_status === 'failed';
  const errorText = failed ? textOf(msg.metadata?.error_message, '') : '';
  const status: 'running' | 'done' | 'failed' = failed ? 'failed' : busy ? 'running' : 'done';
  return (
    <div style={{ alignSelf: user ? 'flex-end' : 'flex-start', maxWidth: user ? '78%' : '100%', width: user ? undefined : '100%' }}>
      <div
        className={cn(
          'px-[15px] py-3 leading-[1.75]',
          user ? cn(inkButton, 'rounded-[18px_18px_4px_18px] active:scale-100') : cn(paper, 'rounded-[18px_18px_18px_4px] text-aui-foreground'),
        )}
        style={{ width: user ? undefined : '100%', boxSizing: 'border-box' }}
      >
        {!user && (toolCalls.length
          // With tool cards, list each one: what was called, with what conditions, and what came out.
          // Only without them (plain chat, failure fallback) fall back to the old coarse step bar.
          ? <div style={{ display: 'grid', gap: 2, marginBottom: 12 }}>
              {toolCalls.map((row, i) => <ToolCall key={`${row.label}-${i}`} {...row} running={busy && i === toolCalls.length - 1} failed={failed} />)}
            </div>
          : <ThinkingSteps steps={steps} status={status} />)}
        {user ? msg.content : (msg.content ? <MarkdownView text={msg.content} /> : (busy ? '正在处理销售线索…' : ''))}
        {errorText && <div className="rounded-aui px-3 py-2.5 text-[13px] leading-relaxed" style={{ marginTop: msg.content ? 12 : 0, border: '1px solid var(--danger-border)', background: 'var(--danger-bg)', color: 'var(--danger-fg)' }}>{errorText}</div>}
        {!user && <PreviewCards cards={previews} actionsDisabled={actionsDisabled} undoDisabled={undoDisabled} companyActionsDisabled={companyActionsDisabled} submittedText={submittedText} onSendText={onSendText} onUndo={onUndo} onOpenDraftCard={onOpenDraftCard} onOpenDraftItem={onOpenDraftItem} onGenerateDraft={onGenerateDraft} onGenerateReport={onGenerateReport} onBatchGenerateReports={onBatchGenerateReports} onBatchGenerateDrafts={onBatchGenerateDrafts} onOpenEmailRecords={onOpenEmailRecords} onScheduleSend={onScheduleSend} onOpenSchedules={onOpenSchedules} onOpenBatchPicker={onOpenBatchPicker} pendingScheduleCount={pendingScheduleCount} draftGeneratingLeadId={draftGeneratingLeadId} reportGeneratingLeadId={reportGeneratingLeadId} leadReports={leadReports} compactRecommendations={compactRecommendations} />}
      </div>
    </div>
  );
}

function PreviewCards({ cards, actionsDisabled, undoDisabled = actionsDisabled, companyActionsDisabled = actionsDisabled, submittedText = '', onSendText, onUndo, onOpenDraftCard, onOpenDraftItem, onGenerateDraft, onGenerateReport, onBatchGenerateReports, onBatchGenerateDrafts, onOpenEmailRecords, onScheduleSend, onOpenSchedules, onOpenBatchPicker, pendingScheduleCount = 0, draftGeneratingLeadId = null, reportGeneratingLeadId = null, leadReports = {}, compactRecommendations = false }: { cards: SalesAgentPreviewCard[]; actionsDisabled: boolean; undoDisabled?: boolean; companyActionsDisabled?: boolean; submittedText?: string; onSendText: (text: string) => void; onUndo: UndoRequestHandler; onOpenDraftCard?: (card: SalesAgentCard) => void; onOpenDraftItem?: (item: ConfirmDraftItem) => void; onGenerateDraft?: (leadId: string) => void; onGenerateReport?: (leadId: string) => void; onBatchGenerateReports?: (leadIds: string[]) => void; onBatchGenerateDrafts?: (leadIds: string[]) => void; onOpenEmailRecords?: (target: EmailRecordTarget) => void; onScheduleSend?: (leadIds: string[]) => void; onOpenSchedules?: (scheduleId?: number) => void; onOpenBatchPicker?: (card: SalesAgentCard) => void; pendingScheduleCount?: number; draftGeneratingLeadId?: number | null; reportGeneratingLeadId?: number | null; leadReports?: Record<number, LeadReportState>; compactRecommendations?: boolean }) {
  if (!cards.length) return null;
  return (
    <div style={{ display: 'grid', gap: 10, marginTop: 12, width: '100%' }}>
      {cards.map((card, i) => <PreviewCard key={`${card.kind}-${card.title}-${i}`} card={card} actionsDisabled={actionsDisabled} undoDisabled={undoDisabled} companyActionsDisabled={companyActionsDisabled} submittedText={submittedText} onSendText={onSendText} onUndo={onUndo} onOpenDraftCard={onOpenDraftCard} onOpenDraftItem={onOpenDraftItem} onGenerateDraft={onGenerateDraft} onGenerateReport={onGenerateReport} onBatchGenerateReports={onBatchGenerateReports} onBatchGenerateDrafts={onBatchGenerateDrafts} onOpenEmailRecords={onOpenEmailRecords} onScheduleSend={onScheduleSend} onOpenSchedules={onOpenSchedules} onOpenBatchPicker={onOpenBatchPicker} pendingScheduleCount={pendingScheduleCount} draftGeneratingLeadId={draftGeneratingLeadId} reportGeneratingLeadId={reportGeneratingLeadId} leadReports={leadReports} compactRecommendations={compactRecommendations} />)}
    </div>
  );
}

/** Action bar of the scheduling summary card in chat (4.5).
 *
 * A2UI only has Text / Column / Row / Card / Button components -- the full list doesn't fit in a card, so
 * "See all" can only pull up the right panel. But **confirmation must not be pushed into the panel too**: confirming is a compliance authorization,
 * and the moment the user has just read this card is when they best understand what they're authorizing; making them click one more layer to confirm
 * would only have them blindly click "Confirm" through a drawer.
 */
function ScheduleCardActions({ card, disabled, onOpenSchedules }: { card: SalesAgentCard; disabled: boolean; onOpenSchedules?: (scheduleId?: number) => void }) {
  const [busy, setBusy] = useState(false);
  const schedule = asRecord(asRecord(card.data).schedule);
  const scheduleId = numberOf(schedule.id, 0);
  const confirmable = textOf(schedule.status, '') === 'draft';
  if (!scheduleId) return null;
  const confirm = async () => {
    setBusy(true);
    try {
      const res = await confirmSendSchedule(scheduleId);
      // The backend's returned hint is authoritative -- re-writing the "it will send automatically on time" sentence in the frontend will drift from the backend sooner or later.
      toast.success(res.hint || '已确认排期,到点会自动逐封发。');
      onOpenSchedules?.(scheduleId);
    } catch (e) {
      toast.error(friendlyError(e instanceof Error ? e.message : '确认排期失败'));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, marginTop: 11 }} onClick={e => e.stopPropagation()}>
      <button type="button" style={ghostBtn} onClick={() => onOpenSchedules?.(scheduleId)}>打开右侧清单</button>
      {confirmable && (
        <button
          type="button"
          disabled={disabled || busy}
          onClick={() => void confirm()}
          style={{ ...primaryBtn, padding: '8px 14px', opacity: disabled || busy ? 0.6 : 1, cursor: disabled || busy ? 'not-allowed' : 'pointer' }}
        >
          {busy ? '确认中…' : '确认排期'}
        </button>
      )}
      {confirmable && <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>确认之前一封都不会发出去。</span>}
    </div>
  );
}

function PreviewCard({ card, actionsDisabled, undoDisabled = actionsDisabled, companyActionsDisabled = actionsDisabled, submittedText = '', onSendText, onUndo, onOpenDraftCard, onOpenDraftItem, onGenerateDraft, onGenerateReport, onBatchGenerateReports, onBatchGenerateDrafts, onOpenEmailRecords, onScheduleSend, onOpenSchedules, onOpenBatchPicker, pendingScheduleCount = 0, draftGeneratingLeadId = null, reportGeneratingLeadId = null, leadReports = {}, compactRecommendations = false }: { card: SalesAgentPreviewCard; actionsDisabled: boolean; undoDisabled?: boolean; companyActionsDisabled?: boolean; submittedText?: string; onSendText: (text: string) => void; onUndo: UndoRequestHandler; onOpenDraftCard?: (card: SalesAgentCard) => void; onOpenDraftItem?: (item: ConfirmDraftItem) => void; onGenerateDraft?: (leadId: string) => void; onGenerateReport?: (leadId: string) => void; onBatchGenerateReports?: (leadIds: string[]) => void; onBatchGenerateDrafts?: (leadIds: string[]) => void; onOpenEmailRecords?: (target: EmailRecordTarget) => void; onScheduleSend?: (leadIds: string[]) => void; onOpenSchedules?: (scheduleId?: number) => void; onOpenBatchPicker?: (card: SalesAgentCard) => void; pendingScheduleCount?: number; draftGeneratingLeadId?: number | null; reportGeneratingLeadId?: number | null; leadReports?: Record<number, LeadReportState>; compactRecommendations?: boolean }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const historical = actionsDisabled;
  const tone = historical ? 'var(--text-muted)' : card.kind === 'companies' ? '#22c55e' : card.kind === 'question' ? 'var(--accent-primary)' : card.kind === 'draft' ? '#f59e0b' : card.kind === 'schedule' ? '#0ea5e9' : isReportCard(card.raw) ? 'var(--accent-primary)' : 'var(--text-secondary)';
  const canOpenDraft = !historical && card.kind === 'draft' && card.raw && !asRecord(card.raw.data).blocked;
  const canUndo = !undoDisabled && !!card.undoTarget;
  useEffect(() => {
    if (!menuOpen) return undefined;
    const close = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) setMenuOpen(false);
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [menuOpen]);
  return (
    <div
      role={canOpenDraft ? 'button' : undefined}
      tabIndex={canOpenDraft ? 0 : undefined}
      onClick={canOpenDraft ? () => { if (card.raw) onOpenDraftCard?.(card.raw); } : undefined}
      onKeyDown={canOpenDraft ? e => { if ((e.key === 'Enter' || e.key === ' ') && card.raw) { e.preventDefault(); onOpenDraftCard?.(card.raw); } } : undefined}
      style={{ width: '100%', boxSizing: 'border-box', border: '1px solid var(--border-color)', background: 'var(--bg-surface)', borderRadius: 16, padding: 14, boxShadow: 'var(--shadow-card)', cursor: canOpenDraft ? 'pointer' : 'default' }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8, color: 'var(--text-primary)', fontWeight: 900, lineHeight: 1.35 }}>
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: tone, boxShadow: 'none', flexShrink: 0 }} />
            <span style={{ wordBreak: 'break-word' }}>{card.title}</span>
          </div>
          {card.meta && <div style={{ color: 'var(--text-muted)', fontSize: 12, marginTop: 4, lineHeight: 1.45, wordBreak: 'break-word' }}>{card.meta}</div>}
        </div>
        <div style={{ flexShrink: 0, textAlign: 'right', display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 8 }}>
          {(card.score || card.status) && <div style={{ textAlign: 'right' }}>
            {card.score && <div style={{ color: 'var(--text-primary)', fontSize: 18, fontWeight: 900, lineHeight: 1 }}>{card.score}</div>}
            {card.status && <div style={{ color: 'var(--text-muted)', fontSize: 12, marginTop: 4 }}>{card.status}</div>}
          </div>}
          {canUndo && (
            <div ref={menuRef} style={{ position: 'relative' }}>
              <button
                type="button"
                aria-label="打开卡片操作"
                aria-haspopup="menu"
                aria-expanded={menuOpen}
                onClick={e => { e.stopPropagation(); setMenuOpen(v => !v); }}
                style={{ ...ghostBtn, width: 30, height: 30, borderRadius: 999, padding: 0, display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}
              >
                <MoreHorizontal size={17} />
              </button>
              {menuOpen && (
                <div role="menu" style={{ position: 'absolute', top: 34, right: 0, zIndex: 20, minWidth: 132, border: '1px solid var(--aui-border)', background: 'var(--bg-card)', borderRadius: 12, padding: 5, boxShadow: '0 14px 34px rgba(15,23,42,.18)' }}>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={e => { e.stopPropagation(); setMenuOpen(false); onUndo(card.undoTarget); }}
                    style={{ width: '100%', border: 'none', background: 'transparent', color: 'var(--text-primary)', borderRadius: 9, padding: '8px 10px', cursor: 'pointer', textAlign: 'left', fontSize: 13, fontWeight: 850 }}
                  >
                    撤销到这一步
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
      {card.desc && card.kind !== 'profile' && <div style={{ color: 'var(--text-secondary)', fontSize: 13, lineHeight: 1.65, marginTop: 10 }}>{card.desc}</div>}
      {!!card.profileSections?.length && <ProfileSectionsPanel sections={card.profileSections} intro={card.desc} />}
      {isA2UISendScheduleCard(card.raw) && card.raw && <ScheduleCardActions card={card.raw} disabled={actionsDisabled} onOpenSchedules={onOpenSchedules} />}
      {/* The real interaction for the batch selection card is in the modal. This button is for people who closed the modal by accident --
          without it, users would have to restate the whole sentence "send to this batch at 3 pm". */}
      {isA2UISendSchedulePickerCard(card.raw) && card.raw && !actionsDisabled && (
        <div style={{ marginTop: 11 }} onClick={e => e.stopPropagation()}>
          <button type="button" style={ghostBtn} onClick={() => card.raw && onOpenBatchPicker?.(card.raw)}>选择批次并排期</button>
        </div>
      )}
      {card.questionCard && <QuestionOptionsCard question={card.questionCard} disabled={actionsDisabled} submittedText={submittedText} onSelect={onSendText} />}
      {!!card.companyCards?.length && <CompanyRecommendationCards companies={card.companyCards} compact={compactRecommendations} actionsDisabled={companyActionsDisabled} readonlyHistory={!!card.readonlyHistory} reportCapable={card.reportCapable !== false} onGenerateDraft={onGenerateDraft} onOpenDraftItem={onOpenDraftItem} onGenerateReport={onGenerateReport} onBatchGenerateReports={onBatchGenerateReports} onBatchGenerateDrafts={onBatchGenerateDrafts} onOpenEmailRecords={onOpenEmailRecords} onScheduleSend={onScheduleSend} onOpenSchedules={onOpenSchedules} pendingScheduleCount={pendingScheduleCount} draftGeneratingLeadId={draftGeneratingLeadId} reportGeneratingLeadId={reportGeneratingLeadId} leadReports={leadReports} />}
      {!!card.tableRows?.length && <div style={{ overflowX: 'auto', marginTop: 11 }}>
        <table style={{ borderCollapse: 'collapse', width: '100%', minWidth: 360, fontSize: 12.5 }}>
          <thead><tr>
            <th style={{ border: '1px solid var(--border-color)', padding: '6px 8px', textAlign: 'left', color: 'var(--text-primary)', background: 'var(--bg-tertiary)' }}>企业</th>
            <th style={{ border: '1px solid var(--border-color)', padding: '6px 8px', textAlign: 'left', color: 'var(--text-primary)', background: 'var(--bg-tertiary)' }}>官网</th>
            <th style={{ border: '1px solid var(--border-color)', padding: '6px 8px', textAlign: 'left', color: 'var(--text-primary)', background: 'var(--bg-tertiary)' }}>公开联系方式</th>
          </tr></thead>
          <tbody>
            {card.tableRows.map((row, i) => <tr key={`${row.company}-${i}`}>
              <td style={{ border: '1px solid var(--border-color)', padding: '6px 8px', color: 'var(--text-secondary)' }}>{row.company}</td>
              <td style={{ border: '1px solid var(--border-color)', padding: '6px 8px', color: 'var(--text-secondary)' }}>{row.website}</td>
              <td style={{ border: '1px solid var(--border-color)', padding: '6px 8px', color: 'var(--text-secondary)' }}>{row.contact}</td>
            </tr>)}
          </tbody>
        </table>
      </div>}
      {!!card.items?.length && <div style={{ display: 'grid', gap: 7, marginTop: 11 }}>
        {card.items.map((item, i) => <div key={`${item.label}-${i}`} style={{ display: 'grid', gridTemplateColumns: '82px 1fr', gap: 8, alignItems: 'start', color: 'var(--text-secondary)', fontSize: 12, lineHeight: 1.55 }}>
          <span style={{ color: 'var(--text-muted)' }}>{item.label}</span>
          <span>{item.value}</span>
        </div>)}
      </div>}
      {!!card.actions?.length && <div style={{ display: 'flex', justifyContent: 'flex-end', flexWrap: 'wrap', gap: 8, marginTop: card.kind === 'profile' ? 10 : 12 }}>
        {card.actions.map((action, i) => {
          const primary = action.text === '继续';
          const isUndo = action.text === '撤销' || action.label === '撤销';
          if (isUndo) return null;
          return <button key={`${action.label}-${i}`} type="button" disabled={actionsDisabled} onClick={e => { e.stopPropagation(); if (!actionsDisabled) onSendText(action.text); }} style={{ ...(primary ? primaryBtn : ghostBtn), padding: card.kind === 'profile' ? (primary ? '7px 14px' : '7px 11px') : '8px 13px', fontSize: card.kind === 'profile' ? 12 : 13, opacity: actionsDisabled ? .58 : 1, cursor: actionsDisabled ? 'not-allowed' : 'pointer' }}>{action.label}</button>;
        })}
      </div>}
      {canOpenDraft && <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 12 }}>
        <button type="button" style={{ ...primaryBtn, padding: '8px 13px', fontSize: 13 }} onClick={e => { e.stopPropagation(); if (card.raw) onOpenDraftCard?.(card.raw); }}>打开右侧草稿</button>
      </div>}
    </div>
  );
}

function websiteHref(website: string): string {
  const raw = (website || '').trim();
  if (!raw || raw === '-') return '';
  return /^https?:\/\//i.test(raw) ? raw : `https://${raw}`;
}

function currentOriginReportUrl(url: string): string {
  const raw = (url || '').trim();
  if (!raw) return '';
  try {
    const parsed = new URL(raw, window.location.origin);
    if (parsed.pathname.startsWith('/sales-agent/report/')) {
      return parsed.pathname + parsed.search + parsed.hash;
    }
  } catch { /* keep raw below */ }
  return raw;
}

function reportUrlFromCard(item: CompanyRecommendationCardItem, state?: LeadReportState): string {
  const token = state?.shareToken || item.shareToken || '';
  return currentOriginReportUrl(state?.publicUrl || item.publicReportUrl || (token ? '/sales-agent/report/' + token : ''));
}

function companyRecommendationSelectionKey(item: CompanyRecommendationCardItem, index: number): string {
  return item.leadId ? 'lead:' + item.leadId : item.company + '-' + index;
}

function leadReportStateFromPayload(raw: Record<string, unknown>): LeadReportState {
  const share = asRecord(raw.report_share);
  return {
    status: textOf(raw.report_status || raw.status || share.report_status, 'none'),
    publicUrl: textOf(raw.public_url || raw.public_report_url || share.public_url, ''),
    shareToken: textOf(raw.share_token || share.share_token || share.public_token, ''),
    reportId: textOf(raw.report_id || raw.source_report_id || share.report_id || share.source_report_id, ''),
  };
}

function CompanyRecommendationCards({
  companies,
  compact = false,
  actionsDisabled = false,
  reportCapable = true,
  readonlyHistory = false,
  onGenerateDraft,
  onOpenDraftItem,
  onGenerateReport,
  onBatchGenerateReports,
  onBatchGenerateDrafts,
  onOpenEmailRecords,
  onScheduleSend,
  onOpenSchedules,
  pendingScheduleCount = 0,
  draftGeneratingLeadId = null,
  reportGeneratingLeadId = null,
  leadReports = {},
}: {
  companies: CompanyRecommendationCardItem[];
  compact?: boolean;
  actionsDisabled?: boolean;
  /** Read-only display of historical recommendation details: keep company cards, but no selection, bulk actions, or report/email generation entries. */
  readonlyHistory?: boolean;
  /** Whether the brand materials include audit/diagnosis services. When false, the report button, report status and report filter are not rendered at all. */
  reportCapable?: boolean;
  onGenerateDraft?: (leadId: string) => void;
  onOpenDraftItem?: (item: ConfirmDraftItem) => void;
  onGenerateReport?: (leadId: string) => void;
  onBatchGenerateReports?: (leadIds: string[]) => void;
  onBatchGenerateDrafts?: (leadIds: string[]) => void;
  onOpenEmailRecords?: (target: EmailRecordTarget) => void;
  /** Scheduled sending: put the selected recommended companies into a plan that sends one email at a time when the time comes (4.5) */
  onScheduleSend?: (leadIds: string[]) => void;
  /** Persistent entry to the plan list -- if the page is closed and reopened tomorrow, this is the only place to find pending plans; with an id it goes straight to details */
  onOpenSchedules?: (scheduleId?: number) => void;
  /** Count of plans not yet sent, shown on the persistent entry as a visual reminder */
  pendingScheduleCount?: number;
  draftGeneratingLeadId?: number | null;
  reportGeneratingLeadId?: number | null;
  leadReports?: Record<number, LeadReportState>;
}) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [searchText, setSearchText] = useState('');
  const [reportFilter, setReportFilter] = useState<'all' | 'ready' | 'pending' | 'generating' | 'draft'>('all');
  const [pageIndex, setPageIndex] = useState(0);
  const itemStates = companies.map((item, i) => {
    const leadIdNumber = numberOf(item.leadId, 0);
    const reportState = leadIdNumber ? leadReports[leadIdNumber] : undefined;
    const reportStatus = reportState?.status || item.reportStatus || '';
    const reportUrl = reportUrlFromCard(item, reportState);
    const reportReady = reportStatus === 'ready' && !!reportUrl;
    const rowKey = companyRecommendationSelectionKey(item, i);
    return { item, i, leadIdNumber, reportState, reportStatus, reportUrl, reportReady, rowKey };
  });
  const normalizedSearch = searchText.trim().toLowerCase();
  const filteredItemStates = itemStates.filter(state => {
    const { item, reportStatus, reportReady } = state;
    if (reportCapable && reportFilter === 'ready' && !reportReady) return false;
    if (reportCapable && reportFilter === 'generating' && reportStatus !== 'generating') return false;
    if (reportCapable && reportFilter === 'pending' && (reportReady || reportStatus === 'generating')) return false;
    if (reportFilter === 'draft' && !item.latestReportEmailDraft) return false;
    if (!normalizedSearch) return true;
    const haystack = [
      item.company,
      item.website,
      item.contact,
      item.meta,
      item.reason,
      item.recommendedAction,
      item.scoreSummary,
      reportStatusLabel(reportStatus),
    ].filter(Boolean).join(' ').toLowerCase();
    return haystack.includes(normalizedSearch);
  });
  const pageSize = 10;
  const pageCount = Math.max(1, Math.ceil(filteredItemStates.length / pageSize));
  const safePageIndex = Math.min(pageIndex, pageCount - 1);
  const pageStart = safePageIndex * pageSize;
  const pagedItemStates = filteredItemStates.slice(pageStart, pageStart + pageSize);
  const selectableItems = readonlyHistory ? [] : itemStates.filter(state => !!state.leadIdNumber);
  const selectableFilteredItems = readonlyHistory ? [] : filteredItemStates.filter(state => !!state.leadIdNumber);
  const selectableKeys = selectableItems.map(state => state.rowKey).join('\u0001');

  useEffect(() => {
    setSelected(prev => {
      const allowed = new Set(selectableItems.map(state => state.rowKey));
      const next: Record<string, boolean> = {};
      Object.keys(prev).forEach(key => { if (allowed.has(key)) next[key] = true; });
      return next;
    });
  }, [selectableKeys]);

  const selectedItems = selectableItems.filter(state => selected[state.rowKey]);
  const selectedFilteredItems = selectableFilteredItems.filter(state => selected[state.rowKey]);
  const selectedCount = selectedItems.length;
  const selectedFilteredCount = selectedFilteredItems.length;
  const allSelected = !!selectableFilteredItems.length && selectedFilteredCount === selectableFilteredItems.length;
  const batchBusy = actionsDisabled || reportGeneratingLeadId !== null || draftGeneratingLeadId !== null;
  const selectedReportLeadIds = selectedItems
    .filter(state => state.reportStatus !== 'generating' && !state.reportReady)
    .map(state => String(state.leadIdNumber));
  const selectedDraftLeadIds = selectedItems
    .filter(state => state.item.hasEmail !== false || looksLikeEmail(state.item.contact))
    .map(state => String(state.leadIdNumber));
  const canBatchReport = reportCapable && !!onBatchGenerateReports && !!selectedReportLeadIds.length && !batchBusy;
  const canBatchDraft = !!onBatchGenerateDrafts && !!selectedDraftLeadIds.length && !batchBusy;
  const listMaxHeight = compact ? 'min(260px, calc(100vh - 360px))' : 'min(320px, calc(100vh - 360px))';

  useEffect(() => {
    if (safePageIndex !== pageIndex) setPageIndex(safePageIndex);
  }, [safePageIndex, pageIndex]);
  useEffect(() => setPageIndex(0), [normalizedSearch, reportFilter, companies.length]);

  function toggleAll(checked: boolean) {
    setSelected(prev => {
      const next = checked ? { ...prev } : { ...prev };
      selectableFilteredItems.forEach(state => {
        if (checked) next[state.rowKey] = true;
        else delete next[state.rowKey];
      });
      return next;
    });
  }

  function toggleOne(rowKey: string, checked: boolean) {
    setSelected(prev => {
      const next = { ...prev };
      if (checked) next[rowKey] = true;
      else delete next[rowKey];
      return next;
    });
  }

  return (
    <>
      <div style={{ marginTop: 8, border: '1px solid var(--border-color)', background: 'var(--bg-card)', borderRadius: 12, padding: '7px 9px', display: 'grid', gridTemplateColumns: compact ? '1fr' : 'minmax(160px, 1fr) 160px auto', gap: 7, alignItems: 'center' }}>
        <input value={searchText} onChange={e => setSearchText(e.currentTarget.value)} placeholder="搜索企业/官网/联系方式/推荐理由" style={{ border: '1px solid var(--border-color)', background: 'var(--bg-surface)', color: 'var(--text-primary)', borderRadius: 10, padding: '7px 9px', fontSize: 12, outline: 'none' }} />
        <UiSelect value={reportFilter} onChange={val => setReportFilter(val as typeof reportFilter)}
                  style={{ border: '1px solid var(--border-color)', background: 'var(--bg-surface)', color: 'var(--text-primary)', borderRadius: 10, padding: '7px 9px', fontSize: 12, outline: 'none' }}
                  options={[
                    { value: 'all', label: '全部企业' },
                    // The three report-related items follow the report capability: brands that don't sell audits seeing "no report generated"
                    // would only think the feature is broken. "Has email draft" is unrelated to reports and always stays.
                    ...(reportCapable ? [
                      { value: 'ready', label: '报告已完成' },
                      { value: 'pending', label: '未生成报告' },
                      { value: 'generating', label: '报告生成中' },
                    ] : []),
                    { value: 'draft', label: '已有邮件草稿' },
                  ]} />
        <div style={{ color: 'var(--text-muted)', fontSize: 12, textAlign: compact ? 'left' : 'right' }}>筛选 {filteredItemStates.length} / {itemStates.length} 家 · 第 {safePageIndex + 1} / {pageCount} 页</div>
      </div>
      {/* Persistent plan list entry (4.5): not attached to the card shown right after "a plan was just created" -- a user creates a plan today,
          closes the page, and tomorrow must be able to find "which pending plans do I have". Pending ones are marked in the primary color: that's the only
          place they can change their mind before the emails actually go out. */}
      {!readonlyHistory && !!onOpenSchedules && (
        <div style={{ marginTop: 8, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <button type="button" onClick={() => onOpenSchedules()}
                  style={{ ...ghostBtn, padding: '5px 9px', fontSize: 12, ...(pendingScheduleCount ? { borderColor: 'var(--aui-primary)', color: 'var(--aui-primary)', fontWeight: 800 } : {}) }}>
            发送计划{pendingScheduleCount ? `(${pendingScheduleCount} 个待发)` : ''}
          </button>
          {!!pendingScheduleCount && <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>还没发出去,现在改时间或取消都来得及。</span>}
        </div>
      )}
      {!!selectableItems.length && (
        <div style={{ marginTop: 8, border: '1px solid var(--border-color)', background: 'var(--bg-card)', borderRadius: 12, padding: '7px 9px', display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 7 }}>
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: 'var(--text-secondary)', fontSize: 12, fontWeight: 800, cursor: actionsDisabled ? 'not-allowed' : 'pointer' }}>
            <input type="checkbox" checked={allSelected} disabled={actionsDisabled || !selectableFilteredItems.length} onChange={e => toggleAll(e.currentTarget.checked)} />
            全选当前筛选
          </label>
          <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>已选 {selectedCount} 家；当前筛选可选 {selectableFilteredItems.length} 家</span>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginLeft: 'auto' }}>
            {reportCapable && <button type="button" disabled={!canBatchReport} onClick={() => onBatchGenerateReports?.(selectedReportLeadIds)} title={selectedCount && !selectedReportLeadIds.length ? '选中企业都已有报告或正在生成' : '为选中企业批量生成诊断报告'} style={{ ...ghostBtn, padding: '5px 9px', fontSize: 12, opacity: canBatchReport ? 1 : .5, cursor: canBatchReport ? 'pointer' : 'not-allowed' }}>批量生成报告{selectedReportLeadIds.length ? '(' + selectedReportLeadIds.length + ')' : ''}</button>}
            {!readonlyHistory && <button type="button" disabled={!canBatchDraft} onClick={() => onBatchGenerateDrafts?.(selectedDraftLeadIds)} title={selectedCount && !selectedDraftLeadIds.length ? '选中企业都没有公开邮箱' : '生成邮件草稿；没有报告时不带报告链接，右侧逐封人工确认后才发送'} style={{ ...primaryBtn, padding: '5px 9px', fontSize: 12, opacity: canBatchDraft ? 1 : .5, cursor: canBatchDraft ? 'pointer' : 'not-allowed' }}>批量写邮件{selectedDraftLeadIds.length ? '(' + selectedDraftLeadIds.length + ')' : ''}</button>}
            {/* Scheduled sending: side by side with "Bulk write emails", but with entirely different semantics -- this neither writes nor sends,
                it only puts the selected batch into a plan that sends one at a time when the time comes, and needs one more confirmation to count as authorized. */}
            {!readonlyHistory && !!onScheduleSend && <button type="button" disabled={!canBatchDraft} onClick={() => onScheduleSend(selectedDraftLeadIds)} title={selectedCount && !selectedDraftLeadIds.length ? '选中企业都没有公开邮箱' : '把选中企业排进定时发送计划;确认之后才会到点自动发'} style={{ ...ghostBtn, padding: '5px 9px', fontSize: 12, opacity: canBatchDraft ? 1 : .5, cursor: canBatchDraft ? 'pointer' : 'not-allowed' }}>定时发送{selectedDraftLeadIds.length ? '(' + selectedDraftLeadIds.length + ')' : ''}</button>}
            {!!selectedCount && <button type="button" disabled={batchBusy} onClick={() => setSelected({})} style={{ ...ghostBtn, padding: '5px 9px', fontSize: 12, opacity: batchBusy ? .5 : 1 }}>清空</button>}
          </div>
        </div>
      )}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: 7, marginTop: 8, maxHeight: listMaxHeight, overflowY: 'auto', overscrollBehavior: 'contain', paddingRight: 4, minHeight: 0 }}>
        {!pagedItemStates.length && <div style={{ border: '1px dashed var(--border-color)', borderRadius: 14, padding: '18px 12px', color: 'var(--text-muted)', fontSize: 13, textAlign: 'center' }}>没有匹配的企业，换个关键词或筛选条件试试。</div>}
        {pagedItemStates.map(({ item, i, leadIdNumber, reportState, reportStatus, reportReady, rowKey }) => {
          const href = websiteHref(item.website);
          const matchScore = item.matchScore && item.matchScore !== '-' ? item.matchScore : (item.score && item.score !== '-' ? item.score : '');
          const opportunityScore = item.opportunityScore && item.opportunityScore !== '-' ? item.opportunityScore : '待评估';
          const confidence = item.confidence && item.confidence !== '-' ? item.confidence : '低';
          const reportBusy = !!leadIdNumber && reportGeneratingLeadId === leadIdNumber;
          const canGenerateReport = reportCapable && !!leadIdNumber && !!onGenerateReport && !reportBusy && !actionsDisabled;
          const existingDraft = item.latestReportEmailDraft || null;
          const companyIdNumber = numberOf(item.companyId || existingDraft?.delivery?.company_id, 0);
          const hasEmail = item.hasEmail !== false || looksLikeEmail(item.contact);
          const canOpenEmailRecords = hasEmail && !!onOpenEmailRecords && !actionsDisabled && (!!leadIdNumber || !!companyIdNumber);
          const existingDraftItem: ConfirmDraftItem | null = existingDraft ? {
            draft: existingDraft,
            variants: [existingDraft],
            lead: {
              id: existingDraft.lead_id,
              company_id: companyIdNumber || leadIdNumber,
              company: { id: companyIdNumber || leadIdNumber, name: item.company, industry: '', region: '', website: item.website, employee_size: '', source: '', status: '', email: '', phone: '', contact_form_url: '', contacts: [] },
              score: numberOf(item.score || item.matchScore, 0),
              match_score: numberOf(item.matchScore || item.score, 0),
              rank: numberOf(item.rank, i + 1),
              reason: item.reason || '已生成报告邮件草稿',
              status: 'Draft Ready',
              report_status: reportStatus,
              report_id: item.reportId,
              public_report_url: item.publicReportUrl,
              share_token: item.shareToken,
              report_share: { public_url: item.publicReportUrl, share_token: item.shareToken, report_id: item.reportId, report_status: reportStatus },
            },
            raw: { tool: 'generate_outreach_copy', data: { draft: existingDraft, variants: [existingDraft], lead: { id: existingDraft.lead_id, report_status: reportStatus, public_report_url: item.publicReportUrl, share_token: item.shareToken, report_id: item.reportId }, company: { id: companyIdNumber || leadIdNumber, name: item.company, website: item.website }, cached: true } },
            cached: true,
          } : null;
          const anyDraftBusy = draftGeneratingLeadId !== null;
          const draftBusy = !!leadIdNumber && draftGeneratingLeadId === leadIdNumber;
          const canOpenExistingDraft = !!existingDraftItem && !!onOpenDraftItem && !anyDraftBusy && !actionsDisabled;
          const canGenerateDraft = hasEmail && !!leadIdNumber && !!onGenerateDraft && !anyDraftBusy && !actionsDisabled;
          const canUseDraftButton = canOpenExistingDraft || canGenerateDraft;
          const emailStatus = existingDraft ? emailStatusInfo(existingDraft) : (item.emailStatus ? { label: item.emailStatus, tone: item.emailStatusTone || 'neutral' } : null);
          const isExpanded = !!expanded[rowKey];
          const selectedRow = !!selected[rowKey];
          return (
            <div key={item.company + '-' + i} style={{ border: '1px solid var(--border-color)', background: 'var(--bg-card)', borderRadius: 12, padding: compact ? '6px 8px' : '7px 9px', boxShadow: '0 4px 12px rgba(15,23,42,.04)' }}>
              <div style={{ display: 'grid', gridTemplateColumns: readonlyHistory
                // The last column (buttons) must be max-content in all four layouts: `auto` falls back to min-content when space is tight,
                // which for a row of buttons is the width of the narrowest button (on 2026-08-23 production rendered 「生/成/报/告」 ("Generate report") one character per line). The two compact
                // branches originally used auto -- only nowrap on the buttons below kept it from recurring; do not revert.
                ? (compact ? '26px minmax(120px, 1fr) minmax(62px, auto) max-content' : '26px minmax(150px, 1fr) minmax(190px, auto) max-content')
                : (compact ? '22px 26px minmax(120px, 1fr) minmax(62px, auto) max-content' : '22px 26px minmax(150px, 1fr) minmax(190px, auto) max-content'), gap: compact ? 5 : 8, alignItems: 'center' }}>
                {!readonlyHistory && <input type="checkbox" aria-label={'选择 ' + item.company} checked={selectedRow} disabled={!leadIdNumber || actionsDisabled} onClick={e => e.stopPropagation()} onChange={e => toggleOne(rowKey, e.currentTarget.checked)} style={{ width: 16, height: 16, cursor: !leadIdNumber || actionsDisabled ? 'not-allowed' : 'pointer' }} />}
                <button type="button" aria-label={isExpanded ? '收起企业详情' : '展开企业详情'} onClick={() => setExpanded(prev => ({ ...prev, [rowKey]: !isExpanded }))} style={{ width: 24, height: 24, border: '1px solid var(--border-color)', borderRadius: 8, background: 'var(--bg-surface)', color: 'var(--text-muted)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', padding: 0 }}>
                  {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                </button>
                <div onClick={() => setExpanded(prev => ({ ...prev, [rowKey]: !isExpanded }))} style={{ minWidth: 0, cursor: 'pointer' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 7, minWidth: 0 }}>
                    <span style={{ flexShrink: 0, color: 'var(--text-muted)', fontSize: 11, fontWeight: 800 }}>#{item.rank || i + 1}</span>
                    <span style={{ color: 'var(--text-primary)', fontSize: 14, fontWeight: 900, lineHeight: 1.25, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.company}</span>
                  </div>
                  <div style={{ color: 'var(--text-muted)', fontSize: 11, marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {[item.meta, item.recommendedAt ? `推荐时间：${item.recommendedAt}` : ''].filter(Boolean).join(' · ')}
                  </div>
                </div>
                {compact && <div style={{ color: 'var(--text-secondary)', fontSize: 12, whiteSpace: 'nowrap' }}><span style={{ color: 'var(--text-muted)' }}>匹配</span> <b style={{ color: 'var(--text-primary)' }}>{matchScore ? matchScore + '分' : '待评分'}</b></div>}
                {/* Metrics cell: two lines inside, same height as the company name cell -- the row height stays, but the status gets the full column width,
                    so "Diagnosing · 4/8 · off-site authority signals · running for 6 min" needs neither truncation nor hover. */}
                {!compact && (
                  <div style={{ minWidth: 0, display: 'grid', gap: 3, fontSize: 12, color: 'var(--text-secondary)' }}>
                    <div style={{ display: 'flex', gap: 12, whiteSpace: 'nowrap' }}>
                      <span><span style={{ color: 'var(--text-muted)' }}>匹配</span> <b style={{ color: 'var(--text-primary)' }}>{matchScore ? matchScore + '分' : '待评分'}</b></span>
                      <span><span style={{ color: 'var(--text-muted)' }}>机会</span> <b style={{ color: 'var(--text-primary)' }}>{opportunityScore}</b></span>
                      <span><span style={{ color: 'var(--text-muted)' }}>置信</span> <b style={{ color: 'var(--text-primary)' }}>{confidence}</b></span>
                    </div>
                    {/* For brands that don't sell audits, the whole row omits reports -- leaving just a gray "no report generated",
                        users would think the hidden button is a malfunction rather than the brand simply not offering this service. */}
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                      {reportCapable && (reportStatus === 'generating'
                        ? <AgentStatus state="working" label={reportProgress(reportState).label} title={reportProgress(reportState).title}
                                       truncateLabel={false} className="w-fit whitespace-nowrap px-2 py-0.5" />
                        : <span style={{ color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>{reportStatus ? reportStatusLabel(reportStatus) : '未生成报告'}</span>)}
                      {emailStatus && <span style={{ border: '1px solid', borderRadius: 999, padding: '1px 7px', fontSize: 11, fontWeight: 800, whiteSpace: 'nowrap', ...emailStatusToneStyle(emailStatus.tone) }}>{emailStatus.label}</span>}
                    </div>
                  </div>
                )}

                {/* nowrap + no shrink: even if the column width gets changed again, buttons won't be squeezed vertical (happened in production on 2026-08-23) */}
                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: compact ? 4 : 6, flexShrink: 0, whiteSpace: 'nowrap' }}>
                  {!compact && !readonlyHistory && reportCapable && <button type="button" disabled={!canGenerateReport} onClick={() => { if (item.leadId) onGenerateReport?.(item.leadId); }} style={{ ...ghostBtn, padding: '5px 8px', fontSize: 12, opacity: canGenerateReport ? 1 : .55, cursor: canGenerateReport ? 'pointer' : 'not-allowed' }}>{reportBusy ? '生成中…' : reportReady || reportStatus === 'generating' ? '刷新' : '生成报告'}</button>}
                  {hasEmail && <button type="button" disabled={!canOpenEmailRecords} onClick={() => onOpenEmailRecords?.({ companyName: item.company, leadId: leadIdNumber || undefined, companyId: companyIdNumber || undefined })} title="查看这家企业/线索的历史邮件发送记录" style={{ ...ghostBtn, padding: compact ? '4px 7px' : '5px 8px', fontSize: compact ? 11 : 12, opacity: canOpenEmailRecords ? 1 : .55, cursor: canOpenEmailRecords ? 'pointer' : 'not-allowed' }}>发送记录</button>}
                  {!readonlyHistory && hasEmail && <button type="button" disabled={!canUseDraftButton} onClick={() => { if (existingDraftItem) onOpenDraftItem?.(existingDraftItem); else if (item.leadId) onGenerateDraft?.(item.leadId); }} title={existingDraftItem ? '查看已生成的报告邮件草稿' : reportReady ? '基于目标企业诊断报告画像生成邮件' : reportCapable ? '先生成不带报告链接的邮件草稿；有报告后邮件会带报告链接' : '按你上传的品牌资料生成服务介绍邮件'} style={{ ...primaryBtn, padding: compact ? '4px 7px' : '5px 8px', fontSize: compact ? 11 : 12, whiteSpace: 'nowrap', opacity: canUseDraftButton ? 1 : .55, cursor: canUseDraftButton ? 'pointer' : 'not-allowed' }}>{draftBusy ? '生成中…' : existingDraftItem ? '查看邮件' : '生成邮件'}</button>}
                </div>
              </div>

              {isExpanded && <div style={{ display: 'grid', gap: 7, marginTop: 9, paddingTop: 9, borderTop: '1px solid var(--border-color)' }}>
                {(item.recommendedAction || item.reason || item.scoreSummary) && <div style={{ display: 'grid', gap: 6 }}>
                  {item.recommendedAction && <div style={{ display: 'grid', gridTemplateColumns: '48px 1fr', gap: 6, color: 'var(--text-secondary)', fontSize: 12, lineHeight: 1.45 }}><span style={{ color: 'var(--text-muted)', fontWeight: 800 }}>动作</span><span>{item.recommendedAction}</span></div>}
                  {item.reason && <div style={{ display: 'grid', gridTemplateColumns: '48px 1fr', gap: 6, color: 'var(--text-secondary)', fontSize: 12, lineHeight: 1.45 }}><span style={{ color: 'var(--text-muted)', fontWeight: 800 }}>依据</span><span>{item.reason}</span></div>}
                  {item.scoreSummary && <div style={{ display: 'grid', gridTemplateColumns: '48px 1fr', gap: 6, color: 'var(--text-muted)', fontSize: 11.5, lineHeight: 1.45 }}><span style={{ fontWeight: 800 }}>分数</span><span>{item.scoreSummary}</span></div>}
                </div>}
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 8, color: 'var(--text-secondary)', fontSize: 12 }}>
                  <div><span style={{ color: 'var(--text-muted)' }}>官网：</span>{href ? <a href={href} target="_blank" rel="noreferrer" style={{ color: 'var(--accent-primary)', textDecoration: 'none' }}>{item.website}</a> : item.website}</div>
                  <div><span style={{ color: 'var(--text-muted)' }}>联系方式：</span>{item.contact}</div>
                  {item.recommendedAt && <div><span style={{ color: 'var(--text-muted)' }}>推荐时间：</span>{item.recommendedAt}</div>}
                  {emailStatus && <div><span style={{ color: 'var(--text-muted)' }}>邮件发送：</span>{emailStatus.label}</div>}
                </div>
              </div>}
            </div>
          );
        })}
      </div>
      {pageCount > 1 && (
        <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap', color: 'var(--text-muted)', fontSize: 12 }}>
          <span>每页 10 家，当前显示 {pageStart + 1}-{Math.min(pageStart + pagedItemStates.length, filteredItemStates.length)} / {filteredItemStates.length} 家</span>
          <div style={{ display: 'flex', gap: 8 }}>
            <button type="button" disabled={safePageIndex <= 0} onClick={() => setPageIndex(prev => Math.max(0, prev - 1))} style={{ ...ghostBtn, padding: '6px 10px', fontSize: 12, opacity: safePageIndex <= 0 ? .5 : 1 }}>上一页</button>
            <button type="button" disabled={safePageIndex >= pageCount - 1} onClick={() => setPageIndex(prev => Math.min(pageCount - 1, prev + 1))} style={{ ...ghostBtn, padding: '6px 10px', fontSize: 12, opacity: safePageIndex >= pageCount - 1 ? .5 : 1 }}>下一页</button>
          </div>
        </div>
      )}
    </>
  );
}

function QuestionOptionsCard({ question, disabled, submittedText = '', onSelect }: { question: SalesAgentQuestionCard; disabled: boolean; submittedText?: string; onSelect: (text: string) => void }) {
  const baseOptions = orderQuestionOptions(question, question.options || []);
  const isTypicalCustomers = isTypicalCustomersQuestion(question);
  const isTargetIndustry = isTargetIndustryQuestion(question);
  const isTargetRegion = isTargetRegionQuestion(question);
  // Items added inside the card live only on this card and are submitted together with preset items on confirm, persisted by the backend;
  // next time the card opens they're already in options (the backend merges selected_values back into options), so no duplicates
  const [customOptions, setCustomOptions] = useState<SalesAgentQuestionOption[]>([]);
  const [customDraft, setCustomDraft] = useState('');
  // The sentence typed in place after selecting an action item like "Custom industry"
  const [actionDraft, setActionDraft] = useState('');
  const [busyOption, setBusyOption] = useState('');
  const [removedValues, setRemovedValues] = useState<string[]>([]);
  const removedValueSet = new Set(removedValues.map(value => value.toLowerCase()));
  const options = orderQuestionOptions(question, customOptions.length ? [...baseOptions, ...customOptions] : baseOptions)
    .filter(opt => !removedValueSet.has(opt.value.toLowerCase()));
  const submittedValues = submittedValuesForQuestion(question, submittedText);
  const secondaryOptions = question.secondary_options || [];
  const secondarySubmittedValues = submittedSecondaryValuesForQuestion(question, submittedText);
  const secondaryDefault = question.secondary_default_value || secondaryOptions[0]?.value || '';
  const selectedValuesKey = submittedValues.join('');
  const secondaryValuesKey = secondarySubmittedValues.join('');
  const [selected, setSelected] = useState<string[]>(submittedValues);
  const [secondarySelected, setSecondarySelected] = useState<string>(secondarySubmittedValues[0] || secondaryDefault);
  useEffect(() => setSelected(submittedValues), [selectedValuesKey, question.id, question.field]);
  useEffect(() => { setCustomOptions([]); setCustomDraft(''); setActionDraft(''); setBusyOption(''); setRemovedValues([]); }, [question.id, question.field]);
  useEffect(() => setSecondarySelected(secondarySubmittedValues[0] || secondaryDefault), [secondaryValuesKey, secondaryDefault, question.id, question.secondary_field]);
  const isSingle = question.type === 'single_select';
  const isMulti = question.type === 'checkbox';
  const hasSecondary = secondaryOptions.length > 0;
  const needsConfirm = ['campaign_target_company_size', 'campaign_target_industries', 'campaign_target_regions'].includes(question.id || '');
  // Multi-select cards used to use single-column big buttons for everything, so target industry/size filled the whole card like form blocks.
  // Default is now the small pills used by "find companies"; only brand selection/persona items with descriptions keep the vertical detail rows.
  const hasOptionDescriptions = options.some(opt => !!opt.desc);
  const compactOptions = isMulti || ['campaign_target_regions', 'sales_agent_entry_action'].includes(question.id || '');
  const vertical = !compactOptions && (question.layout === 'vertical' || (isSingle && hasOptionDescriptions));
  const optionValueSet = new Set(options.map(opt => opt.value));
  const displayedSelected = disabled ? submittedValues : selected;
  const visibleSelected = displayedSelected.filter(value => optionValueSet.has(value));
  const selectedSet = new Set(visibleSelected);
  const currentSelected = selected.filter(value => optionValueSet.has(value));
  const submittedLabels = submittedValues.map(value => options.find(opt => opt.value === value)?.label || value);
  const secondarySubmittedLabels = secondarySubmittedValues.map(value => secondaryOptions.find(opt => opt.value === value)?.label || value);
  const submittedSummaryLabels = isTypicalCustomers && (question.selected_values || []).length ? normalizeChoiceValues(question.selected_values || []) : submittedLabels;
  // The selected action item ("Custom industry") -- when present, open an input box inside the card and submit the sentence in it.
  // Only applies to single-select: adding items in place on multi-select cards goes through allow_custom_option; don't mix them.
  const activeAction = isMulti ? undefined : options.find(opt => opt.input_placeholder && selected.includes(opt.value));
  const isTargetCompanySize = question.id === 'campaign_target_company_size' || question.field === 'target_company_size';
  const isSelectableAllQuestion = isTargetCompanySize || isTargetIndustry || isTargetRegion;
  const customNoun = question.field === 'target_regions' ? '地区' : question.field === 'target_industries' ? '行业' : isTypicalCustomers ? '客户类型' : '能力';
  const noLimitValues = options.filter(isNoLimitQuestionOption).map(opt => opt.value);
  const noLimitValueSet = new Set(noLimitValues);
  const regularOptionValues = options.filter(opt => !isNoLimitQuestionOption(opt)).map(opt => opt.value);
  const allRegularSelected = regularOptionValues.length > 0 && regularOptionValues.every(value => currentSelected.includes(value));
  function selectAllRegularOptions() {
    if (disabled) return;
    setSelected(allRegularSelected ? [] : regularOptionValues);
  }
  function toggle(value: string) {
    if (disabled) return;
    if (!isMulti) {
      // Action items with an in-place input can't submit on a single click -- what should be submitted is the sentence in the box
      const opt = options.find(item => item.value === value);
      if (hasSecondary || needsConfirm || opt?.input_placeholder) setSelected([value]);
      else onSelect(value);
      setActionDraft('');
      return;
    }
    setSelected(prev => {
      if (!isSelectableAllQuestion) return prev.includes(value) ? prev.filter(item => item !== value) : [...prev, value];
      if (noLimitValueSet.has(value)) return prev.includes(value) ? [] : [value];
      const withoutNoLimit = prev.filter(item => !noLimitValueSet.has(item));
      return withoutNoLimit.includes(value) ? withoutNoLimit.filter(item => item !== value) : [...withoutNoLimit, value];
    });
  }
  const customMax = question.custom_option_max || 20;
  // The limit is on the number "newly added in the card this time"; previously saved custom items must not count,
  // otherwise with many historical items the input gets locked and users can't add more.
  const customFull = customOptions.length >= customMax;
  function addCustomOption() {
    if (disabled || busyOption) return;
    // Each click of + is one custom item. Commas/enumeration commas/semicolons are often part of long sentences
    // and must not be stripped here; multiple items in bulk are left to the backend to split by newline.
    const text = customDraft.replace(/\r\n?/g, '\n').trim();
    if (!text) return;
    // If it has the same name as an existing item (including presets/historical custom items), just check it instead of adding another;
    // this step must not be blocked by customFull, otherwise "type an existing item and select it" would also break.
    const existing = options.find(opt => opt.value.toLowerCase() === text.toLowerCase() || opt.label.toLowerCase() === text.toLowerCase());
    if (existing) {
      setSelected(prev => {
        if (!isSelectableAllQuestion) return prev.includes(existing.value) ? prev : [...prev, existing.value];
        if (isNoLimitQuestionOption(existing)) return [existing.value];
        const withoutNoLimit = prev.filter(item => !noLimitValueSet.has(item));
        return withoutNoLimit.includes(existing.value) ? withoutNoLimit : [...withoutNoLimit, existing.value];
      });
      setCustomDraft('');
      return;
    }
    if (customFull) return;
    const kind = optionKindForQuestion(question);
    const sellerProfileId = sellerProfileIdForOption(question);
    const localOption: SalesAgentQuestionOption = { label: text, value: text, source: 'custom', kind: kind || undefined, seller_profile_id: sellerProfileId, editable: !!kind && !!sellerProfileId };
    setRemovedValues(prev => prev.filter(value => value.toLowerCase() !== text.toLowerCase()));
    setCustomOptions(prev => [...prev, localOption]);
    setSelected(prev => isSelectableAllQuestion ? [...prev.filter(item => !noLimitValueSet.has(item)), text] : [...prev, text]);
    setCustomDraft('');
    if (kind && sellerProfileId) {
      setBusyOption(`add:${text}`);
      createSalesAgentOption({ kind, value: text, label: text, seller_profile_id: sellerProfileId })
        .then(entry => {
          setCustomOptions(prev => prev.map(opt => opt.value.toLowerCase() === text.toLowerCase() ? { ...opt, id: entry.id, kind: entry.kind, seller_profile_id: entry.seller_profile_id, editable: entry.editable } : opt));
        })
        .catch(e => {
          setCustomOptions(prev => prev.filter(opt => opt.value.toLowerCase() !== text.toLowerCase()));
          setSelected(prev => prev.filter(value => value.toLowerCase() !== text.toLowerCase()));
          toast.error(friendlyError(e instanceof Error ? e.message : `添加${customNoun}失败`));
        })
        .finally(() => setBusyOption(''));
    }
  }
  function deleteQuestionOption(opt: SalesAgentQuestionOption, event?: { stopPropagation: () => void }) {
    event?.stopPropagation();
    if (disabled || busyOption || !canDeleteQuestionOption(question, opt)) return;
    const kind = optionKindForQuestion(question);
    const sellerProfileId = sellerProfileIdForOption(question, opt);
    if (!kind || !sellerProfileId) return;
    const key = opt.value.toLowerCase();
    setBusyOption(`delete:${opt.value}`);
    const removeLocal = () => {
      setRemovedValues(prev => prev.some(value => value.toLowerCase() === key) ? prev : [...prev, opt.value]);
      setCustomOptions(prev => prev.filter(item => item.value.toLowerCase() !== key));
      setSelected(prev => prev.filter(value => value.toLowerCase() !== key));
    };
    const request = opt.id ? archiveSalesAgentOption(opt.id) : archiveSalesAgentOptionByValue({ kind, value: opt.value, seller_profile_id: sellerProfileId });
    request
      .then(removeLocal)
      .then(() => toast.success(`已删除${customNoun}:${opt.label}`))
      .catch(e => toast.error(friendlyError(e instanceof Error ? e.message : `删除${customNoun}失败`)))
      .finally(() => setBusyOption(''));
  }
  // 「、,，;；」 (ASCII and full-width comma, enumeration comma, semicolons) are answer separators in the backend; including them in a typed sentence splits it into two items
  const actionText = actionDraft.replace(/[、,，;；\n]+/g, ' ').replace(/\s+/g, ' ').trim();
  function confirmCurrent() {
    if (disabled || !currentSelected.length) return;
    // Clicked an action item like "Custom industry": submit the sentence typed in the box, not the action label itself --
    // submitting the label would only make the backend ask again, while the input box is right there
    if (activeAction && !actionText) return;
    const values = activeAction ? [actionText] : currentSelected;
    // The backend supports JSON arrays; don't join with 「、」 (enumeration comma) anymore, otherwise option text containing enumeration commas/commas gets split apart.
    onSelect(JSON.stringify([...values, hasSecondary ? secondarySelected : ''].filter(Boolean)));
  }
  return (
    <div style={{ marginTop: 12 }}>
      <div style={vertical ? { display: 'grid', gridTemplateColumns: '1fr', gap: 8, justifyItems: 'stretch' } : { display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
        {options.map(opt => {
          const active = selectedSet.has(opt.value);
          const showInlineState = isMulti && !compactOptions;
          const deletable = !disabled && canDeleteQuestionOption(question, opt);
          const deleting = busyOption === `delete:${opt.value}`;
          const optionButton = (
            <button
              type="button"
              disabled={disabled || deleting}
              onClick={() => toggle(opt.value)}
              title={opt.desc || opt.label}
              style={{
                border: `1px solid ${active && !disabled ? 'var(--aui-primary)' : 'var(--aui-border)'}`,
                background: disabled ? (vertical ? 'var(--aui-card)' : 'var(--aui-muted)') : active ? 'var(--aui-accent)' : (vertical ? 'var(--aui-card)' : 'var(--aui-muted)'),
                color: disabled ? 'var(--text-muted)' : 'var(--text-primary)',
                borderRadius: vertical ? 12 : 999,
                padding: compactOptions ? '5px 9px' : (vertical ? '10px 13px' : '7px 11px'),
                cursor: disabled || deleting ? 'not-allowed' : 'pointer',
                opacity: disabled ? .58 : 1,
                fontSize: compactOptions ? 12 : 13,
                fontWeight: 800,
                textAlign: 'left',
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: vertical ? 'space-between' : 'center',
                gap: compactOptions ? 5 : 10,
                lineHeight: 1.25,
                maxWidth: '100%',
                flex: deletable ? '1 1 auto' : undefined,
              }}
            >
              <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: vertical ? 'normal' : 'nowrap' }}>
                {active && compactOptions ? '✓ ' : ''}{opt.label}
                {vertical && opt.desc && <span style={{ display: 'block', color: 'var(--text-muted)', fontSize: 11, fontWeight: 500, marginTop: 2 }}>{opt.desc}</span>}
              </span>
              {showInlineState && <span style={{ color: disabled ? 'var(--text-muted)' : active ? 'var(--accent-primary)' : 'var(--text-muted)', fontSize: 12 }}>{active ? '已选' : '可选'}</span>}
            </button>
          );
          if (!deletable) return <span key={`${question.id || question.field || 'question'}-${opt.value}`}>{optionButton}</span>;
          return (
            <span key={`${question.id || question.field || 'question'}-${opt.value}`} style={{ display: 'inline-flex', alignItems: 'center', gap: 4, maxWidth: '100%' }}>
              {optionButton}
              <button
                type="button"
                disabled={disabled || deleting}
                onClick={event => deleteQuestionOption(opt, event)}
                title={`删除${opt.label}`}
                style={{ width: 24, height: 24, borderRadius: 999, border: '1px solid var(--danger-border)', background: 'var(--danger-bg)', color: 'var(--danger-fg)', cursor: disabled || deleting ? 'not-allowed' : 'pointer', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 14, fontWeight: 950, flexShrink: 0 }}
              >
                {deleting ? '…' : '×'}
              </button>
            </span>
          );
        })}
      </div>
      {activeAction && !disabled && (
        <input
          autoFocus
          value={actionDraft}
          onChange={e => setActionDraft(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); confirmCurrent(); } }}
          placeholder={activeAction.input_placeholder}
          style={{
            width: '100%', boxSizing: 'border-box', marginTop: 8,
            border: '1px solid var(--aui-primary)', borderRadius: 12,
            padding: '10px 13px', fontSize: 13, background: 'var(--aui-card)',
            color: 'var(--text-primary)', outline: 'none',
          }}
        />
      )}
      {question.allow_custom_option && !disabled && (
        <div style={{ display: 'flex', gap: 6, marginTop: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <input
            value={customDraft}
            onChange={e => setCustomDraft(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addCustomOption(); } }}
            placeholder={question.custom_option_placeholder || '输入并添加一项'}
            style={{
              flex: '0 1 280px', minWidth: 160, maxWidth: '100%', border: '1px solid var(--aui-border)', borderRadius: 999,
              padding: '7px 11px', fontSize: 12, background: 'var(--aui-card)',
              color: 'var(--text-primary)', outline: 'none', lineHeight: 1.25,
            }}
          />
          <button
            type="button"
            onClick={addCustomOption}
            disabled={!customDraft.trim()}
            aria-label={`添加${customNoun}`}
            title={`添加${customNoun}`}
            style={{ ...ghostBtn, width: 32, height: 32, borderRadius: 999, padding: 0, opacity: customDraft.trim() ? 1 : .5, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M12 5v14M5 12h14" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
            </svg>
          </button>
        </div>
      )}
      {question.allow_custom_option && !disabled && customFull && (
        <div style={{ color: 'var(--text-muted)', fontSize: 12, marginTop: 6 }}>本次最多新增 {customMax} 项；已有项仍可输入后直接选中。</div>
      )}
      {hasSecondary && (
        <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--border-color)' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 8 }}>
            <div style={{ color: 'var(--text-primary)', fontSize: 13, fontWeight: 900 }}>{question.secondary_title || '推荐几家企业'}</div>
            {question.secondary_helper_text && <div style={{ color: 'var(--text-muted)', fontSize: 12 }}>{question.secondary_helper_text}</div>}
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {secondaryOptions.map(opt => {
              const active = secondarySelected === opt.value;
              return (
                <button key={`${question.id || question.field || 'question'}-${question.secondary_field || 'secondary'}-${opt.value}`} type="button" disabled={disabled} onClick={() => setSecondarySelected(opt.value)} style={{ border: `1px solid ${active && !disabled ? 'var(--aui-primary)' : 'var(--aui-border)'}`, background: disabled ? 'var(--aui-muted)' : active ? 'var(--aui-accent)' : 'var(--aui-muted)', color: disabled ? 'var(--text-muted)' : 'var(--text-primary)', borderRadius: 999, padding: '5px 9px', cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? .58 : 1, fontSize: 12, fontWeight: 800, lineHeight: 1.25 }}>{opt.label}</button>
              );
            })}
          </div>
        </div>
      )}
      {(isMulti || hasSecondary || needsConfirm || !!activeAction) && !disabled && (() => {
        // "Continue" stays gray until the action item has text -- submitting the literal label "Custom industry" is meaningless
        const ready = currentSelected.length > 0 && (!activeAction || !!actionText);
        return (
          <div style={{ display: 'flex', justifyContent: isSelectableAllQuestion ? 'space-between' : 'flex-end', alignItems: 'center', gap: 10, marginTop: 12 }}>
            {isSelectableAllQuestion ? (
              <button type="button" onClick={selectAllRegularOptions} style={{ ...ghostBtn, padding: '8px 12px', fontSize: 12, fontWeight: 850 }}>{allRegularSelected ? '取消全选' : '全选'}</button>
            ) : <span />}
            <button type="button" disabled={!ready} onClick={confirmCurrent} style={{ ...primaryBtn, padding: (hasSecondary || needsConfirm) ? '10px 22px' : '9px 17px', fontSize: (hasSecondary || needsConfirm) ? 14 : 13, minWidth: (hasSecondary || needsConfirm) ? 112 : undefined, opacity: ready ? 1 : .5 }}>{hasSecondary || needsConfirm ? '继续' : `确认选择${currentSelected.length ? `(${currentSelected.length})` : ''}`}</button>
          </div>
        );
      })()}
      {disabled ? (
        <div style={{ color: 'var(--text-muted)', fontSize: 12, marginTop: 8 }}>{submittedSummaryLabels.length || secondarySubmittedLabels.length ? `已提交：${[...submittedSummaryLabels, ...secondarySubmittedLabels].join('、')}` : '这一步已提交,请继续操作最新问题卡。'}</div>
      ) : question.allow_text_fallback !== false && <div style={{ color: 'var(--text-muted)', fontSize: 12, marginTop: 8 }}>{isMulti ? `可多选后确认,也可以在输入框添加其它${customNoun}。` : '也可以直接在输入框输入其它答案。'}</div>}
    </div>
  );
}

function InputBox({ input, setInput, busy, onSend, valid, onNeedLogin, large = false }: { input: string; setInput: (v: string) => void; busy: boolean; onSend: () => void; valid: boolean; onNeedLogin: () => void; large?: boolean }) {
  function handleInput(v: string) {
    if (!valid) {
      onNeedLogin();
      return;
    }
    setInput(v);
  }

  return (
    <div className={cn(paper, 'rounded-[22px]', large ? 'p-4' : 'p-2.5')}>
      <textarea
        value={input}
        onFocus={() => { if (!valid) onNeedLogin(); }}
        onChange={e => handleInput(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); onSend(); } }}
        placeholder="输入官网、行业或客户方向,例如: 帮我推荐北京律所财务外包目标客户"
        className="box-border w-full resize-none border-none bg-transparent text-[16px] text-aui-foreground outline-none placeholder:text-aui-muted-foreground"
        style={{ minHeight: large ? 72 : 40, maxHeight: 130 }}
        disabled={busy}
      />
      <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center' }}>
        <button onClick={onSend} disabled={busy} aria-label="发送"
                className={cn(inkButton, 'inline-flex size-11 items-center justify-center rounded-full p-0', busy && 'opacity-55')}>
          <SendIcon />
        </button>
      </div>
    </div>
  );
}

function outreachChannelLabel(channel: string) {
  if (channel === 'sms') return '短信';
  if (channel === 'phone_script') return '电话话术';
  if (channel === 'contact_form') return '官网表单';
  if (channel === 'linkedin_script') return 'LinkedIn 私信';
  return '邮件';
}

function outreachTargetLabel(channel: string) {
  if (channel === 'sms') return '短信号码';
  if (channel === 'phone_script') return '联系电话';
  if (channel === 'contact_form') return '官网表单';
  if (channel === 'linkedin_script') return 'LinkedIn/私信对象';
  return '收件邮箱';
}

/**
 * Billing/mode hint bar for SMS drafts.
 *
 * For email drafts the length doesn't matter, but SMS does: a Chinese SMS is one segment per 70 characters, so a 200-character copy is actually billed as 3,
 * and CN channels can only send pre-registered templates -- both must be visible before clicking "Confirm send".
 */
function SmsDraftMeta({ draft }: { draft: SalesAgentDraft }) {
  const meta = asRecord(draft.metadata);
  const templateMode = textOf(meta.sms_content_mode, '') === 'template';
  const templateCode = textOf(meta.sms_template_code, '');
  // Already-sent ones show the real segment count from the platform receipt; unsent ones use a local estimate
  const sentSegments = numberOf(draft.delivery?.segments, 0);
  const segments = sentSegments || smsSegments(draft.content);
  return (
    <div style={{ marginTop: 8, color: 'var(--text-muted)', fontSize: 12, lineHeight: 1.7 }}>
      <div>
        短信长度 {draft.content.length} 字 · 约 {segments} 条计费{sentSegments ? '(平台回执)' : ''}
        {smsIsUnicode(draft.content) ? '(含中文/符号,按 70 字一条)' : '(纯英数,按 160 字一条)'}
        {!templateMode && '；超过 320 字后端会阻断保存。'}
      </div>
      <div>
        {templateMode
          ? (templateCode
            ? `发送方式：已报备模板 ${templateCode}；正文以平台报备内容为准，这里只是变量填充后的预览。`
            // A draft without a template doesn't mean it can't send: the backend falls back to an approved template under the channel (usable_sms_template),
            // and only blocks when the channel has no usable template at all -- this must be stated precisely, otherwise users will edit the draft instead of configuring templates
            : '发送方式：已报备模板(草稿未指定)；发送时会自动选用该通道下已审核通过的模板，若通道没有可用模板会被拦下，请先到短信配置页添加。')
          : '发送方式：自由文案(国际通道)；国内号码发不了自由文案，会按号码国家码路由到国际通道。'}
      </div>
      {draft.recipient_phone && <div>目标号码：{draft.recipient_phone}</div>}
    </div>
  );
}

function OutreachTemplatePreview({ draft, compact = false, scrollable = false }: { draft: SalesAgentDraft; compact?: boolean; scrollable?: boolean }) {
  const channelLabel = `${outreachChannelLabel(draft.channel)}模板`;
  const isSms = draft.channel === 'sms';
  const noReportDefault = isNoReportDefaultDraft(draft);
  const htmlContent = draftHtmlContent(draft);
  return (
    <div style={{ overflow: scrollable ? 'visible' : 'hidden', borderRadius: 16, border: '1px solid var(--aui-border)', background: 'var(--bg-card)', margin: compact ? '0 0 10px' : '12px 0' }}>
      <div style={{ padding: compact ? 13 : 15 }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 10, marginBottom: 12 }}>
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 9, minWidth: 0 }}>
            <span style={{ width: 28, height: 28, borderRadius: 10, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg-surface)', color: 'var(--text-primary)', border: '1px solid var(--border-color)', flexShrink: 0 }}>
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M5 7.5c0-1.4 1.1-2.5 2.5-2.5h9C17.9 5 19 6.1 19 7.5v9c0 1.4-1.1 2.5-2.5 2.5h-9A2.5 2.5 0 0 1 5 16.5v-9Z" stroke="currentColor" strokeWidth="1.8" />
                <path d="M8 9h8M8 12h5M8 15h6.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
              </svg>
            </span>
            <div style={{ minWidth: 0 }}>
              <div style={{ color: 'var(--text-primary)', fontWeight: 800, lineHeight: 1.42, wordBreak: 'break-word', fontSize: compact ? 16 : 17 }}>{draft.subject || channelLabel}</div>
              <div style={{ color: 'var(--text-muted)', fontSize: 12, marginTop: 3 }}>{channelLabel} · {draft.send_status}{noReportDefault ? ' · 无报告服务介绍信' : ''}</div>
            </div>
          </div>
          <div style={{ display: 'inline-flex', alignItems: 'center', gap: 7, flexShrink: 0 }}>
            {draft.subject && <button type="button" onClick={() => copyTextToClipboard(draft.subject, '标题')} style={{ ...ghostBtn, borderRadius: 999, padding: '4px 9px', fontSize: 12, fontWeight: 800 }}>复制标题</button>}
            <span style={{ color: 'var(--text-secondary)', background: 'var(--bg-surface)', border: '1px solid var(--border-color)', borderRadius: 999, padding: '4px 9px', fontSize: 12, fontWeight: 700, flexShrink: 0 }}>草稿</span>
          </div>
        </div>
        <div style={{ margin: 0, whiteSpace: htmlContent ? 'normal' : 'pre-wrap', lineHeight: 1.86, color: 'var(--text-primary)', background: 'var(--bg-primary)', border: '1px solid var(--aui-border)', borderRadius: 14, padding: compact ? 15 : 17, fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', Arial, sans-serif", fontSize: 14.5, letterSpacing: '.01em' }}>
          {htmlContent ? <div dangerouslySetInnerHTML={{ __html: htmlContent }} /> : <MarkdownView text={linkifyPlainText(draft.content)} />}
        </div>
        {isSms && <SmsDraftMeta draft={draft} />}
      </div>
    </div>
  );
}

function draftStyleKey(draft: SalesAgentDraft): ReportEmailStyleKey {
  const raw = textOf(asRecord(draft.metadata).style_key, '');
  // Using the report normalization for intro letter keys would collapse them all into leijun_pitch -- four letter types sharing one
  // cache slot, so switching types hits the previous type's draft directly.
  return isNoReportDefaultDraft(draft) ? normalizeNoReportEmailStyleKey(raw) : normalizeReportEmailStyleKey(raw);
}

function draftIntroMode(draft: SalesAgentDraft): string {
  return textOf(asRecord(draft.metadata).intro_mode, '');
}

function draftStyleLabel(draft: SalesAgentDraft): string {
  const styles = isNoReportDefaultDraft(draft) ? NO_REPORT_EMAIL_STYLES : REPORT_EMAIL_STYLES;
  const style = styles.find(s => s.key === draftStyleKey(draft));
  return style?.label || textOf(asRecord(draft.metadata).style_label, isNoReportDefaultDraft(draft) ? '服务介绍信' : '报告邮件');
}

function ReportEmailVariantControls({ item, regenerating = false, onSwitchStyle, onRegenerate }: { item: ConfirmDraftItem; regenerating?: boolean; onSwitchStyle?: (styleKey: ReportEmailStyleKey) => void; onRegenerate?: (styleKey: ReportEmailStyleKey) => void }) {
  const currentStyle = draftStyleKey(item.draft);
  const noReportDefault = isNoReportDefaultDraft(item.draft);
  const [selectedStyle, setSelectedStyle] = useState<ReportEmailStyleKey>(currentStyle);
  useEffect(() => setSelectedStyle(currentStyle), [currentStyle, item.draft.id]);
  const cached = item.cached || boolOf(asRecord(item.draft.metadata).cached, false);
  const styles = noReportDefault ? NO_REPORT_EMAIL_STYLES : REPORT_EMAIL_STYLES;
  // The "standard intro" is concatenated by the backend; the same materials always produce the identical result, so regenerating is pointless.
  // Old drafts generated before this change have no intro_mode, but back then concatenation was the only path -- recognize them by key too.
  const canRegenerate = !noReportDefault || (currentStyle !== 'standard' && draftIntroMode(item.draft) !== 'template');
  return (
    <section className={cn(paper, 'grid gap-3 rounded-aui p-3.5')}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center' }}>
        <div>
          <div style={{ color: 'var(--text-primary)', fontWeight: 900 }}>{noReportDefault ? '写信类型' : '收件人视角'}</div>
          <div style={{ color: 'var(--text-muted)', fontSize: 12, marginTop: 2 }}>{cached ? (noReportDefault ? '已使用缓存版本' : '已使用报告缓存版本') : '新生成草稿'} · {draftStyleLabel(item.draft)}</div>
        </div>
        {canRegenerate && (
          <button type="button" disabled={regenerating} onClick={() => onRegenerate?.(selectedStyle)}
                  className={cn(inkButton, 'rounded-full px-3.5 py-2 text-xs font-bold', regenerating && 'opacity-60')}>{regenerating ? '生成中…' : '重新生成'}</button>
        )}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: 8 }}>
        {styles.map(style => {
          const active = selectedStyle === style.key;
          return <button key={style.key} type="button" disabled={regenerating} onClick={() => { setSelectedStyle(style.key); if (style.key !== currentStyle) onSwitchStyle?.(style.key); }}
            className={cn(
              'rounded-aui border p-2.5 text-left text-aui-foreground transition-colors',
              active ? 'border-aui-primary bg-aui-accent ring-1 ring-aui-ring' : 'border-aui-border bg-aui-card hover:bg-aui-accent',
              regenerating ? 'cursor-wait opacity-60' : 'cursor-pointer',
            )}>
            <div style={{ fontWeight: 900, fontSize: 13 }}>{style.emoji} {style.label}</div>
            <div style={{ color: 'var(--text-muted)', fontSize: 11, lineHeight: 1.45, marginTop: 3 }}>{style.desc}</div>
          </button>;
        })}
      </div>
    </section>
  );
}

function KV({ label, value }: { label: string; value: string }) {
  return <div style={{ display: 'grid', gridTemplateColumns: '72px 1fr', gap: 8, fontSize: 13, lineHeight: 1.7, margin: '3px 0' }}><span style={{ color: 'var(--text-muted)' }}>{label}</span><span style={{ color: 'var(--text-secondary)' }}>{value || '-'}</span></div>;
}

function OutreachConfirmPanelContent({ draft, lead, sending = false, onConfirm }: { draft: SalesAgentDraft; lead: SalesAgentLead | null; sending?: boolean; onConfirm: (confirmed: boolean) => void }) {
  const [metaOpen, setMetaOpen] = useState(false);
  const fallbackLead: SalesAgentLead = { id: draft.lead_id, company_id: draft.lead_id, company: null, score: 0, rank: 0, reason: '', status: '' };
  const displayLead = lead ?? fallbackLead;
  const contact = displayLead.company?.contacts?.[0];
  const channelLabel = outreachChannelLabel(draft.channel);
  const targetLabel = outreachTargetLabel(draft.channel);
  const isSmsDraft = draft.channel === 'sms';
  const missingTargetText = draft.channel === 'email'
    ? '待补充收件邮箱'
    : isSmsDraft
      ? '待补充手机号'
      : draft.channel === 'contact_form'
        ? '待补充官网表单地址'
        : '待补充联系电话';
  const target = draft.channel === 'email'
    ? (contact?.email || displayLead.company?.email || missingTargetText)
    : draft.channel === 'contact_form'
      ? (contact?.contact_form_url || displayLead.company?.contact_form_url || missingTargetText)
      : (contact?.phone || displayLead.company?.phone || missingTargetText);
  const reportShare = asRecord(displayLead.report_share);
  const reportUrl = displayLead.public_report_url || textOf(reportShare.public_url, '');
  const reportStatus = displayLead.report_status || textOf(reportShare.report_status, '');
  const reportLabel = reportUrl ? `${reportStatusLabel(reportStatus)} · 可公开访问` : (reportStatus ? reportStatusLabel(reportStatus) : '未关联');
  const missingTarget = target === missingTargetText;
  const blockedConsent = ['unsubscribed', 'blacklisted', 'do_not_contact'].includes(contact?.consent_status || '');
  const alreadySent = draft.send_status === 'sent';
  const delivery = draft.delivery || null;
  const deliveryTarget = isSmsDraft
    ? (draft.recipient_phone || delivery?.recipient_phone || target)
    : (draft.recipient_email || delivery?.recipient_email || target);
  const deliveryFrom = draft.from_email || delivery?.from_email || '';
  const deliveryStatus = delivery?.status || draft.send_status;
  const noReportDefault = isNoReportDefaultDraft(draft);
  // How many times sent / when last sent. The backend has a resend cap and cooldown for sent drafts (outreach.py::_guard_resend);
  // without echoing it in the frontend, users only see a "Send again" button and learn only when rejected.
  const sentCount = numberOf(delivery?.send_count ?? (alreadySent ? 1 : 0));
  const lastSentAt = delivery?.sent_at || '';
  const resendExhausted = alreadySent && sentCount >= 3;
  const cannotConfirm = blockedConsent || resendExhausted;
  const missingTargetWarning = draft.channel === 'email'
    ? '当前缺少企业公开收件邮箱,后端会写入 no_recipient 记录且不会真实外发。'
    : draft.channel === 'sms'
      ? '当前缺少可发短信的手机号(座机/400 号码发不了短信),后端会写入 no_recipient 记录且不会真实外发。'
      : draft.channel === 'contact_form'
        ? '当前缺少官网表单地址,确认后会进入待审批并标记为待补充表单地址;补齐目标前不会触达。'
        : '当前缺少联系电话,确认后会进入待审批并标记为待补充号码;补齐号码前不会触达。';
  const channelWarning = draft.channel === 'sms'
    // This used to say "the system won't call the SMS gateway" -- once the SMS channel is wired that's wrong; confirming really sends.
    ? `这是短信触达草稿,确认后会按短信配置里的通道真实下发到企业手机号,按号码国家码路由(国内走已报备模板,国际走自由文案),约 ${smsSegments(draft.content)} 条计费。请先核对号码来源与退订说明。`
    : draft.channel === 'email'
      ? (noReportDefault
        ? '这是无报告默认介绍邮件;不会带报告链接,确认后会按数据库里的用户/全局邮箱配置发送到企业公开邮箱。'
        : '这是基于诊断报告生成的邮件内容;确认后会按数据库里的用户/全局邮箱配置发送到企业公开邮箱,不会再使用固定测试收件箱。')
      : `这是${channelLabel}触达草稿,需要人工确认目标和内容后,再进入待审批;系统不会自动触达。`;
  const noticeText = alreadySent
    ? (resendExhausted
      ? `这封已经发出 ${sentCount} 次,不能再发了。要继续跟进请改内容后重新生成草稿。`
      : isSmsDraft
        // SMS has no "open"; the only interactions are short-link clicks and inbound replies; reusing email's "recipient hasn't opened yet" would be false information
        ? `短信已发出${sentCount > 1 ? ` ${sentCount} 次` : ''}${lastSentAt ? `,最近一次 ${fmtTime(lastSentAt)}` : ''}${deliveryStatus === 'delivered' ? ',运营商已回执送达' : ',等待运营商回执'}${numberOf(delivery?.click_count, 0) ? `,报告链接被点击 ${delivery?.click_count} 次` : ''}。重发有 5 分钟冷却,最多 3 次。`
        : `邮件已发出${sentCount > 1 ? ` ${sentCount} 次` : ''}${lastSentAt ? `,最近一次 ${fmtTime(lastSentAt)}` : ''}${delivery?.read_at ? ',对方已阅读/访问报告' : ',对方尚未打开'}。重发有 5 分钟冷却,最多 3 次。`)
    : blockedConsent
      ? '当前联系人命中退订、黑名单或不联系标记,不能确认触达。'
      : deliveryStatus === 'disabled'
        ? `${channelLabel}发送已关闭:${draft.failure_reason || delivery?.failure_reason || '不会真实外发。'}`
        : deliveryStatus === 'no_recipient'
          ? (isSmsDraft
            ? '目标企业只有座机/400 号码或没有号码,不会真实外发;请先补充可接收短信的手机号。'
            : '目标企业缺少可用公开邮箱,不会真实外发;请先补充联系人邮箱。')
          // rejected means blocked by local rules (rate limit, missing registered template, no SMS consent); the reason must be stated truthfully
          : deliveryStatus === 'rejected'
            ? `本次未发送:${draft.failure_reason || delivery?.failure_reason || '命中发送频控或缺少已报备模板,请到短信配置页检查。'}`
            : deliveryStatus === 'failed'
              ? `上次发送失败:${draft.failure_reason || delivery?.failure_reason || (isSmsDraft ? '请检查短信通道配置后重试。' : '请检查邮箱配置后重试。')}`
              : missingTarget
                ? missingTargetWarning
                : `${channelWarning} 若后端发现来源不明、退订、黑名单等风险,会直接阻断。`;
  return (
    <>
      <OutreachTemplatePreview draft={draft} compact scrollable />
      <section style={{ border: '1px solid var(--border-color)', background: 'var(--bg-surface)', borderRadius: 14, overflow: 'hidden' }}>
        <button type="button" onClick={() => setMetaOpen(v => !v)} style={{ width: '100%', border: 'none', background: 'transparent', color: 'var(--text-secondary)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '9px 11px', cursor: 'pointer', fontSize: 12 }}>
          <span>{displayLead.company?.name || `线索 #${draft.lead_id}`} · {channelLabel} · {reportLabel}</span>
          {metaOpen ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
        </button>
        {metaOpen && <div style={{ display: 'grid', gap: 6, padding: '0 11px 10px' }}>
          <KV label="企业" value={displayLead.company?.name || `线索 #${draft.lead_id}`} />
          <KV label="渠道" value={channelLabel} />
          <KV label={targetLabel} value={deliveryTarget} />
          {draft.channel === 'email' && <KV label="发件邮箱" value={deliveryFrom || '按配置解析'} />}
          {draft.channel === 'email' && <KV label="发送状态" value={deliveryStatus || draft.send_status} />}
          {draft.channel === 'email' && <KV label="阅读" value={delivery?.read_at ? '已阅读/访问报告' : `打开 ${delivery?.open_count || 0} 次 · 报告访问 ${delivery?.report_visit_count || 0} 次`} />}
          {draft.channel === 'email' && (draft.failure_reason || delivery?.failure_reason) && <KV label="失败原因" value={draft.failure_reason || delivery?.failure_reason || ''} />}
          {/* These SMS rows correspond to email's sending mailbox/opens: after sending, you need to see which signature template was used, how many segments were billed, and whether anyone clicked or unsubscribed */}
          {isSmsDraft && <KV label="签名/模板" value={[delivery?.sign_name, delivery?.template_code || textOf(asRecord(draft.metadata).sms_template_code, '')].filter(Boolean).join(' · ') || '按通道配置解析'} />}
          {isSmsDraft && <KV label="发送状态" value={deliveryStatus || draft.send_status} />}
          {isSmsDraft && <KV label="计费条数" value={`${numberOf(delivery?.segments, 0) || smsSegments(draft.content)} 条${numberOf(delivery?.segments, 0) ? '(平台回执)' : '(本地估算)'}`} />}
          {isSmsDraft && <KV label="互动" value={`点击 ${numberOf(delivery?.click_count, 0)} 次 · 回复 ${numberOf(delivery?.reply_count, 0)} 条${delivery?.opted_out_at ? ' · 已退订' : ''}`} />}
          {isSmsDraft && (draft.failure_reason || delivery?.failure_reason) && <KV label="失败原因" value={`${draft.failure_reason || delivery?.failure_reason || ''}${delivery?.failure_code ? `(${delivery.failure_code})` : ''}`} />}
          <KV label="来源" value={contact?.source || displayLead.company?.source || '未确认来源'} />
          <KV label="同意" value={contact?.consent_status || 'unknown'} />
          <KV label="状态" value={draft.send_status} />
          <KV label="审批" value={draft.requires_approval ? '需要人工审批' : '未标记'} />
          {draft.channel === 'email' && noReportDefault && <KV label="模板" value="无报告服务介绍信" />}
          <KV label="诊断报告" value={reportLabel} />
          {draft.channel !== 'email' && <KV label="退订" value={draft.has_unsubscribe ? '已包含退订说明' : '未包含退订说明'} />}
        </div>}
      </section>
      <div className={cn(field, 'rounded-aui px-3 py-3 text-[13px] leading-relaxed')}
           style={{ color: (blockedConsent || missingTarget) ? 'var(--warn-fg)' : 'var(--text-secondary)' }}>
        {noticeText}
      </div>
      <div className="flex justify-end">
        <button
          className={cn(inkButton, 'rounded-full px-4 py-2 text-[13px] font-bold', (cannotConfirm || sending) && 'cursor-not-allowed opacity-50')}
          onClick={() => onConfirm(true)}
          disabled={cannotConfirm || sending}
        >{sending ? (alreadySent ? '再次发送中…' : '发送中…') : (alreadySent ? '再次发送' : '确认发送')}</button>
      </div>
    </>
  );
}

function ReportStat({ label, value, accent = false }: { label: string; value: string; accent?: boolean }) {
  if (!value) return null;
  return (
    <div style={{ border: '1px solid var(--border-color)', background: 'var(--bg-surface)', borderRadius: 16, padding: '12px 14px', minWidth: 0 }}>
      <div style={{ color: 'var(--text-muted)', fontSize: 12, marginBottom: 4 }}>{label}</div>
      <div style={{ color: accent ? 'var(--accent-primary)' : 'var(--text-primary)', fontSize: 20, fontWeight: 900, lineHeight: 1.2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{value}</div>
    </div>
  );
}

function ReportListSection({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <section style={{ border: '1px solid var(--border-color)', background: 'var(--bg-surface)', borderRadius: 18, padding: 15 }}>
      <div style={{ color: 'var(--text-primary)', fontWeight: 900, marginBottom: 9 }}>{title}</div>
      <ol style={{ margin: 0, paddingLeft: 19, color: 'var(--text-secondary)', lineHeight: 1.75, fontSize: 13.5 }}>
        {items.slice(0, 8).map((item, i) => <li key={`${title}-${i}`}>{item}</li>)}
      </ol>
    </section>
  );
}

/** Watch a streaming intro letter as it's written. The header and footer are fixed by the backend and present once connected; the middle grows paragraph by paragraph.
 *
 * What's shown here is a **preview**, not the final draft -- confirming send submits the one persisted in the `done` frame.
 * Normally the two are identical word for word (the streamed paragraphs and the persisted copy are the same), but when the model falls back to JSON or
 * hits the compliance gate midway, the backend redoes it, and the preview is cleared and replayed by a `reset` frame.
 */
function IntroStreamPreview({ loading }: { loading: DraftLoadingState }) {
  const paragraphs = loading.paragraphs || [];
  const pending = (loading.pending || '').trim();
  const bodyDone = !!loading.bodyDone;
  // The model's title takes precedence. Only when it doesn't produce one do we fall back to the template title, and only after the body is finished or degraded
  // -- showing it midway, users would see the title start old and jump to a new one a few seconds later.
  const settled = bodyDone || !!loading.resetReason;
  const subject = loading.subject || (settled ? loading.fallbackSubject || '' : '');
  return (
    <div style={{ border: '1px solid var(--border-color)', borderRadius: 14, padding: 14, background: 'var(--bg-page)', display: 'grid', gap: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13.5, paddingBottom: 8, borderBottom: '1px dashed var(--border-color)' }}>
        <span style={{ color: 'var(--text-secondary)', fontWeight: 600 }}>标题：</span>
        {subject ? (
          <span style={{ color: 'var(--text-primary)', fontWeight: 800 }}>{subject}</span>
        ) : (
          /* Title not decided yet. Leave a placeholder bar rather than blank, so the title row doesn't push the whole block down when it appears. */
          <span style={{ height: 13, flex: '0 1 200px', borderRadius: 999, background: 'var(--aui-accent)' }} />
        )}
      </div>
      {loading.resetReason && (
        <div style={{ color: 'var(--text-secondary)', fontSize: 12.5, lineHeight: 1.6 }}>
          {loading.resetReason}
        </div>
      )}
      <div style={{ color: 'var(--text-primary)', fontSize: 13.5, lineHeight: 1.85, display: 'grid', gap: 10 }}>
        {/* Show the salutation only once the first body character arrives, for the same reason as the footer below: the model may emit no body for tens of seconds while thinking
            (measured up to 53 seconds in production), and a lone 「XX相关负责人您好:」 ("Dear XX contact,") with nothing below looks like
            the letter broke. The degradation path re-sends concatenated paragraphs, so "never a salutation" can't happen here. */}
        {(paragraphs.length > 0 || pending) && <div>{loading.greeting}</div>}
        {paragraphs.map((text, i) => <div key={`intro-para-${i}`}>{text}</div>)}
        {/* The paragraph being written. Replaced by the stripped final text once this paragraph's `paragraph` frame arrives. */}
        {pending && <div>{pending}</div>}
        {/* A placeholder bar while the middle isn't finished, otherwise users can't tell "finished" from "stuck". */}
        {!bodyDone && (
          <div style={{ height: 13, width: paragraphs.length || pending ? '46%' : '78%', borderRadius: 999, background: 'var(--aui-accent)' }} />
        )}
        {/* The footer appears only after the body is done: showing "contact info + best regards" right away looks like the letter is already finished. */}
        {bodyDone && (loading.tail || []).map((text, i) => (
          <div key={`intro-tail-${i}`} style={{ color: 'var(--text-secondary)', whiteSpace: 'pre-wrap' }}>{text}</div>
        ))}
      </div>
    </div>
  );
}

function SalesReportSidePanel({ card, confirmDraft, draftLoading = null, draftQueueCount = 0, width = 420, isFullscreen = false, regeneratingDraft = false, confirmSending = false, onWidthChange, onToggleFullscreen, onClose, onConfirmDraft, onSwitchDraftStyle, onRegenerateDraftStyle, preferA2UI = false }: { card: SalesAgentCard | null; confirmDraft?: ConfirmDraftItem | null; draftLoading?: DraftLoadingState | null; draftQueueCount?: number; width?: number; isFullscreen?: boolean; regeneratingDraft?: boolean; confirmSending?: boolean; onWidthChange?: (width: number) => void; onToggleFullscreen?: () => void; onClose: () => void; onConfirmDraft?: (confirmed: boolean) => void; onSwitchDraftStyle?: (styleKey: ReportEmailStyleKey) => void; onRegenerateDraftStyle?: (styleKey: ReportEmailStyleKey) => void; preferA2UI?: boolean }) {
  const [a2uiFailed, setA2uiFailed] = useState(false);

  useEffect(() => {
    setA2uiFailed(false);
  }, [card, confirmDraft]);

  if (!card && !confirmDraft && !draftLoading) return null;
  const draft = confirmDraft?.draft ?? null;
  const panelCard = confirmDraft?.raw ?? card ?? null;
  const a2uiPayload = panelCard && !draft && preferA2UI && !a2uiFailed && canRenderWithA2UI(panelCard) ? salesAgentCardToA2UI(panelCard) : null;
  const data = asRecord(panelCard?.data);
  const summary = asRecord(data.summary);
  const isGuidancePanel = !!(a2uiPayload && a2uiPayload.kind === 'guidance');
  const isRecommendationsPanel = !!(a2uiPayload && a2uiPayload.kind === 'recommendations');
  const brandName = textOf(pickReportValue(data, summary, ['brand_name', 'brand', 'title']), '');
  const url = textOf(pickReportValue(data, summary, ['url', 'website']), '');
  const status = textOf(pickReportValue(data, summary, ['status']), '');
  const totalScore = textOf(pickReportValue(data, summary, ['total_score', 'score']), '');
  const grade = textOf(pickReportValue(data, summary, ['grade']), '');
  const executiveSummary = textOf(pickReportValue(data, summary, ['executive_summary', 'summary_text', 'hint', 'message']), '');
  const reportPage = textOf(pickReportValue(data, summary, ['report_page', 'report_url']), '');
  const progress = asRecord(pickReportValue(data, summary, ['progress']));
  const dimensions = asList<Record<string, unknown>>(pickReportValue(data, summary, ['dimensions']));
  const keyFindings = reportTextList(pickReportValue(data, summary, ['key_findings', 'findings']));
  const quickWins = reportTextList(pickReportValue(data, summary, ['quick_wins', 'actions', 'suggestions']));
  const panelTitle = draft
    ? `${outreachChannelLabel(draft.channel)}草稿详情${draftQueueCount > 1 ? ` · 待处理 ${draftQueueCount} 条` : ''}`
    : draftLoading
      ? '正在生成报告邮件'
      : a2uiPayload?.title || brandName || url || (/generate_detection_report/i.test(panelCard?.tool || '') ? '诊断报告入口' : '历史报告摘要');
  const progressText = progress.total != null ? `${textOf(progress.done, '0')} / ${textOf(progress.total, '0')}` : '';
  const reportLinkAttrs = reportPageTarget(reportPage);
  const panelStyle: CSSProperties = isFullscreen
    ? { position: 'fixed', inset: 0, width: '100vw', maxWidth: 'none', height: '100vh', zIndex: 80 }
    : { width, maxWidth: 'min(860px, 92vw)', height: '100vh', flexShrink: 0, position: 'relative', zIndex: 2 };
  function startResize(e: { clientX: number }) {
    if (isFullscreen || !onWidthChange) return;
    const startX = e.clientX;
    const startWidth = width;
    const maxWidth = Math.min(860, Math.round(window.innerWidth * 0.72));
    const move = (ev: PointerEvent) => onWidthChange(Math.max(360, Math.min(maxWidth, startWidth + startX - ev.clientX)));
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }

  return (
    <aside style={{ ...panelStyle, borderLeft: '1px solid var(--aui-border)', background: 'var(--aui-card)', display: 'flex', flexDirection: 'column' }}>
      {!isFullscreen && <div onPointerDown={startResize} title="拖动调整宽度" style={{ position: 'absolute', left: -5, top: 0, bottom: 0, width: 10, cursor: 'col-resize', zIndex: 4 }} />}
      <div style={{ padding: '20px 20px 16px', borderBottom: '1px solid var(--border-color)', background: 'linear-gradient(180deg, var(--aui-card)) 0%, var(--bg-card) 100%)' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
          <span style={{ width: 42, height: 42, borderRadius: 16, background: 'var(--solid-btn-bg)', color: 'var(--solid-btn-text)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', boxShadow: 'var(--glow-primary)', flexShrink: 0 }}>
            <FileText size={21} />
          </span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ color: 'var(--text-muted)', fontSize: 12, fontWeight: 800 }}>Sales Agent · {draft ? '触达草稿详情' : isRecommendationsPanel ? '推荐企业' : isGuidancePanel ? '找客户工作区' : '右侧报告'}{a2uiPayload ? ` · ${a2uiPayload.source === 'backend' ? (isRecommendationsPanel ? '后端 A2UI 推荐卡片' : isGuidancePanel ? '后端 A2UI 找客户' : '后端 A2UI 渲染') : (isRecommendationsPanel ? 'A2UI 兼容推荐卡片' : isGuidancePanel ? 'A2UI 兼容找客户' : 'A2UI 兼容渲染')}` : ''}</div>
            <div style={{ color: 'var(--text-primary)', fontSize: 20, lineHeight: 1.25, fontWeight: 900, marginTop: 4, wordBreak: 'break-word' }}>{panelTitle}</div>
            {url && <div style={{ color: 'var(--text-muted)', fontSize: 12, marginTop: 6, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{url}</div>}
          </div>
          <button type="button" aria-label={isFullscreen ? '退出全屏' : '全屏查看'} onClick={onToggleFullscreen} style={{ ...ghostBtn, width: 34, height: 34, borderRadius: 12, padding: 0, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
            {isFullscreen ? <Minimize2 size={17} /> : <Maximize2 size={17} />}
          </button>
          <button type="button" aria-label="关闭报告面板" onClick={onClose} style={{ ...ghostBtn, width: 34, height: 34, borderRadius: 12, padding: 0, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
            <X size={17} />
          </button>
        </div>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: 18, display: 'grid', alignContent: 'start', gap: 14 }}>
        {!draftLoading && !draft && !isGuidancePanel && !isRecommendationsPanel && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 10 }}>
            <ReportStat label="状态" value={reportStatusLabel(status)} />
            <ReportStat label="综合评分" value={totalScore ? `${totalScore}${grade ? ` / ${grade}` : ''}` : ''} accent />
            <ReportStat label="进度" value={progressText} />
            <ReportStat label="等级" value={grade} />
          </div>
        )}

        {draftLoading ? (
          <section style={{ border: '1px solid var(--border-color)', background: 'var(--bg-surface)', borderRadius: 18, padding: 18, display: 'grid', gap: 12 }}>
            <div style={{ width: 44, height: 44, borderRadius: 16, background: 'var(--solid-btn-bg)', color: 'var(--solid-btn-text)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', boxShadow: 'var(--glow-primary)' }}>✍️</div>
            <div style={{ color: 'var(--text-primary)', fontSize: 18, fontWeight: 900 }}>{isNoReportEmailStyleKey(draftLoading.styleKey) ? '正在生成服务介绍信…' : '正在生成完整报告邮件…'}</div>
            <div style={{ color: 'var(--text-secondary)', lineHeight: 1.7, fontSize: 13.5 }}>
              {isNoReportEmailStyleKey(draftLoading.styleKey)
                ? `已打开邮件抽屉。这封信没有报告支撑,只会按你自己的品牌资料、以「${NO_REPORT_EMAIL_STYLES.find(s => s.key === normalizeNoReportEmailStyleKey(draftLoading.styleKey))?.label || '标准介绍'}」的角度写,不会出现任何检测结论。`
                : `已打开邮件抽屉。系统会基于真实诊断报告、公开报告链接和「${REPORT_EMAIL_STYLES.find(s => s.key === normalizeReportEmailStyleKey(draftLoading.styleKey))?.label || '综合负责人'}」视角生成克制的诊断冷邮件。`}
            </div>
            {draftLoading.greeting ? (
              <IntroStreamPreview loading={draftLoading} />
            ) : (
              <div style={{ display: 'grid', gap: 8 }}>
                <div style={{ height: 14, width: '86%', borderRadius: 999, background: 'var(--aui-accent)' }} />
                <div style={{ height: 14, width: '72%', borderRadius: 999, background: 'var(--aui-accent)' }} />
                <div style={{ height: 120, borderRadius: 16, background: 'linear-gradient(90deg, var(--aui-muted), var(--aui-accent)), var(--aui-muted))' }} />
              </div>
            )}
          </section>
        ) : draft ? (
          <>
            {confirmDraft && <ReportEmailVariantControls item={confirmDraft} regenerating={regeneratingDraft || confirmSending} onSwitchStyle={onSwitchDraftStyle} onRegenerate={onRegenerateDraftStyle} />}
            <OutreachConfirmPanelContent draft={draft} lead={confirmDraft?.lead ?? null} sending={confirmSending} onConfirm={confirmed => onConfirmDraft?.(confirmed)} />
          </>
        ) : a2uiPayload ? (
          <A2UIReportRenderer payload={a2uiPayload} onRenderError={() => setA2uiFailed(true)} />
        ) : (
          <>
            {executiveSummary && (
              <section style={{ border: '1px solid var(--border-color)', background: 'var(--bg-surface)', borderRadius: 18, padding: 15 }}>
                <div style={{ color: 'var(--text-primary)', fontWeight: 900, marginBottom: 8 }}>报告摘要</div>
                <div style={{ color: 'var(--text-secondary)', lineHeight: 1.75, fontSize: 13.5, whiteSpace: 'pre-wrap' }}>{executiveSummary}</div>
              </section>
            )}

            {!!dimensions.length && (
              <section style={{ border: '1px solid var(--border-color)', background: 'var(--bg-surface)', borderRadius: 18, padding: 15 }}>
                <div style={{ color: 'var(--text-primary)', fontWeight: 900, marginBottom: 9 }}>维度得分</div>
                <div style={{ display: 'grid', gap: 8 }}>
                  {dimensions.slice(0, 8).map((dim, i) => {
                    const name = textOf(dim['维度'] || dim.name || dim.key || dim.dimension, `维度 ${i + 1}`);
                    const score = textOf(dim['得分'] || dim.score || dim.value, '—');
                    const note = textOf(dim['口径'] || dim.source || dim.reason || dim.note, '');
                    return (
                      <div key={`${name}-${i}`} style={{ borderTop: i ? '1px solid var(--border-color)' : 'none', paddingTop: i ? 8 : 0 }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, fontSize: 13.5 }}>
                          <span style={{ color: 'var(--text-secondary)' }}>{name}</span>
                          <span style={{ color: 'var(--text-primary)', fontWeight: 900 }}>{score}</span>
                        </div>
                        {note && <div style={{ color: 'var(--text-muted)', fontSize: 12, marginTop: 3 }}>{note}</div>}
                      </div>
                    );
                  })}
                </div>
              </section>
            )}

            <ReportListSection title="关键发现" items={keyFindings} />
            <ReportListSection title="快速优化建议" items={quickWins} />

            {reportPage && (
              <a href={reportPage} {...reportLinkAttrs} style={{ ...primaryBtn, textDecoration: 'none', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 8, borderRadius: 14 }}>
                打开完整报告 <ExternalLink size={16} />
              </a>
            )}
          </>
        )}
      </div>
    </aside>
  );
}

export default function SalesAgentWorkbench({ embedded = false }: { embedded?: boolean } = {}) {
  const { isLoggedIn, token, user, clearAuth } = useAuth();
  const valid = isLoggedIn && !!token && !isJwtExpired(token);
  const userLabel = displayName(user) || 'Sales Agent 用户';
  const emptyBundle: SalesAgentBundle = { session: null, sessions: [], messages: [] };
  const [bundle, setBundle] = useState<SalesAgentBundle>(emptyBundle);
  const [messages, setMessages] = useState<SalesAgentMessage[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [draftGeneratingLeadId, setDraftGeneratingLeadId] = useState<number | null>(null);
  const [reportGeneratingLeadId, setReportGeneratingLeadId] = useState<number | null>(null);
  const [leadReports, setLeadReports] = useState<Record<number, LeadReportState>>({});
  const [err, setErr] = useState<string | null>(null);
  const [authOpen, setAuthOpen] = useState(false);
  const [confirmDraft, setConfirmDraft] = useState<ConfirmDraftItem | null>(null);
  const [confirmDraftQueue, setConfirmDraftQueue] = useState<ConfirmDraftItem[]>([]);
  const [reportPanelCard, setReportPanelCard] = useState<SalesAgentCard | null>(null);
  const [draftLoading, setDraftLoading] = useState<DraftLoadingState | null>(null);
  // Scheduled sending (4.5): the fourth form of the right panel. Mutually exclusive with reportPanelCard / confirmDraft --
  // it occupies the same area, no new Drawer.
  const [scheduleView, setScheduleView] = useState<SendScheduleView | null>(null);
  const [pendingScheduleCount, setPendingScheduleCount] = useState(0);
  const [scheduleRefreshTick, setScheduleRefreshTick] = useState(0);
  // The "Send to which batch?" modal. One click on OK really schedules it, so it **auto-opens only once for this round's new message** --
  // history replay, session switching and re-renders must not trigger it. Surfaces already shown are recorded in a ref (see the effect below).
  const [batchPicker, setBatchPicker] = useState<A2UISendSchedulePickerModel | null>(null);
  // Only "the user just finished saying something" gets a chance to auto-open. Judging "does the last message carry a card" from messages
  // isn't enough: switching sessions, browsing history and component re-renders also make that message the last one, so a modal that really sends emails
  // would pop up by itself when the user is merely looking back at history.
  const batchPickerArmedRef = useRef(false);
  const [sidePanelWidth, setSidePanelWidth] = useState(620);
  const [sidePanelFullscreen, setSidePanelFullscreen] = useState(false);
  const [draftRegenerating, setDraftRegenerating] = useState(false);
  const [confirmSendingDraftId, setConfirmSendingDraftId] = useState<number | null>(null);
  const confirmSendingCountRef = useRef(0);
  const [sidebarExpanded, setSidebarExpanded] = useState(true);
  const [newSessionPending, setNewSessionPending] = useState(false);
  const [historyReadOnly, setHistoryReadOnly] = useState(false);
  const [sessionsLoadingMore, setSessionsLoadingMore] = useState(false);
  const [emailRecordTarget, setEmailRecordTarget] = useState<EmailRecordTarget | null>(null);
  const [companyDeliveries, setCompanyDeliveries] = useState<SalesAgentEmailDelivery[]>([]);
  const [companyDeliveryTotal, setCompanyDeliveryTotal] = useState(0);
  const [companyDeliveryPage, setCompanyDeliveryPage] = useState(1);
  const [companyDeliveryPageSize, setCompanyDeliveryPageSize] = useState(20);
  const [companyDeliveryLoading, setCompanyDeliveryLoading] = useState(false);
  const [viewingCompanyDelivery, setViewingCompanyDelivery] = useState<SalesAgentEmailDelivery | null>(null);
  const [undoConfirmOpen, setUndoConfirmOpen] = useState(false);
  const [undoTarget, setUndoTarget] = useState<SalesAgentUndoTarget | null>(null);
  const activeStreamRef = useRef<AbortController | null>(null);
  const busyRef = useRef(false);

  const sessionId = bundle.session?.id ?? null;
  const confirmDraftRef = useRef<ConfirmDraftItem | null>(null);
  const confirmDraftQueueRef = useRef<ConfirmDraftItem[]>([]);
  const seenConfirmDraftIdsRef = useRef<Set<number>>(new Set());
  const draftCacheRef = useRef<Map<string, ConfirmDraftItem>>(new Map());

  useEffect(() => { if (token && isJwtExpired(token)) clearAuth(); }, [token, clearAuth]);
  useEffect(() => () => { activeStreamRef.current?.abort(); }, []);
  useEffect(() => { if (valid) void loadBundle(undefined, true); }, [valid]);

  function setActiveConfirmDraft(item: ConfirmDraftItem | null) {
    confirmDraftRef.current = item;
    setConfirmDraft(item);
  }

  function setConfirmDraftQueueItems(items: ConfirmDraftItem[]) {
    confirmDraftQueueRef.current = items;
    setConfirmDraftQueue(items);
  }

  function draftCacheKey(leadId: number, styleKey: ReportEmailStyleKey) {
    return `${leadId}:${normalizeEmailStyleKey(styleKey)}`;
  }

  function rememberDraftItem(item: ConfirmDraftItem) {
    draftCacheRef.current.set(draftCacheKey(item.draft.lead_id, draftStyleKey(item.draft)), item);
  }

  function showConfirmDraftItem(item: ConfirmDraftItem) {
    setDraftLoading(null);
    rememberDraftItem(item);
    setActiveConfirmDraft(item);
    setSidePanelWidth(width => Math.max(width, 620));
    setSidePanelFullscreen(false);
  }

  function resetDraftConfirmations(clearSeen = false) {
    setActiveConfirmDraft(null);
    setConfirmDraftQueueItems([]);
    if (clearSeen) seenConfirmDraftIdsRef.current.clear();
  }

  function enqueueConfirmDraftItems(items: ConfirmDraftItem[]) {
    const fresh = items.filter(item => {
      if (seenConfirmDraftIdsRef.current.has(item.draft.id)) return false;
      seenConfirmDraftIdsRef.current.add(item.draft.id);
      return true;
    });
    if (!fresh.length) return;
    const currentQueue = confirmDraftQueueRef.current;
    if (confirmDraftRef.current) {
      setConfirmDraftQueueItems([...currentQueue, ...fresh]);
      return;
    }
    const [first, ...rest] = fresh;
    if (rest.length) setConfirmDraftQueueItems([...currentQueue, ...rest]);
    showConfirmDraftItem(first);
  }

  function openDraftItem(item: ConfirmDraftItem) {
    if (!valid) {
      requireLogin();
      return;
    }
    seenConfirmDraftIdsRef.current.add(item.draft.id);
    showConfirmDraftItem(item);
  }

  function openDraftCard(card: SalesAgentCard) {
    if (!valid) {
      requireLogin();
      return;
    }
    const [item] = confirmDraftItemsFromCards([card]);
    if (!item) return;
    openDraftItem(item);
  }

  function openEmailRecords(target: EmailRecordTarget) {
    if (!valid) {
      requireLogin();
      return;
    }
    setEmailRecordTarget(target);
    setCompanyDeliveryPage(1);
    setCompanyDeliveries([]);
    setCompanyDeliveryTotal(0);
  }

  function openScheduleCompose(leadIds: string[]) {
    if (!valid) {
      requireLogin();
      return;
    }
    const ids = leadIds.map(id => numberOf(id, 0)).filter(Boolean);
    if (!ids.length) {
      toast.error('选中的企业都没有可用的公开收件邮箱,没法排进发送计划。');
      return;
    }
    closeDraftConfirmation();
    setReportPanelCard(null);
    setScheduleView({ mode: 'compose', leadIds: ids });
  }

  // Always open the plan list; an id only **pre-expands that row** (the chat scheduling card's "Open list on the right" uses this).
  // The list is an accordion with details inside the row, so entering doesn't hide other plans -- to see a second one just click the second row.
  function openScheduleList(scheduleId?: number) {
    if (!valid) {
      requireLogin();
      return;
    }
    closeDraftConfirmation();
    setReportPanelCard(null);
    setScheduleView(scheduleId ? { mode: 'detail', scheduleId } : { mode: 'list' });
    // Right after confirming in chat, the "N pending" on the badge should drop accordingly.
    if (scheduleId) setScheduleRefreshTick(t => t + 1);
  }

  // If this round's reply carries a "Send to which batch?" card, pull up the modal automatically -- after the user says "send to this batch at 3 pm"
  // they should see the options and counts directly, without clicking again.
  //
  // Judge only once at the moment `busy` drops back to false, and consume armed right there (whether or not there's a card):
  // the remaining paths -- switching sessions, browsing history, any re-render -- can't get in.
  useEffect(() => {
    if (busy || !batchPickerArmedRef.current) return;
    const last = messages[messages.length - 1];
    if (!last || last.role !== 'assistant') return;
    batchPickerArmedRef.current = false;
    if (historyReadOnly) return;
    const card = asList<SalesAgentCard>(last.metadata?.tool_cards).find(c => isA2UISendSchedulePickerCard(c));
    if (card) setBatchPicker(normalizeSendSchedulePicker(card));
  }, [messages, busy, historyReadOnly]);

  // "N pending" on the persistent entry. Polling lives in the panel (4.6: every 10 seconds only while running);
  // here it's fetched only on opening the workbench and after each plan status change -- the badge needn't be real-time.
  useEffect(() => {
    if (!valid) { setPendingScheduleCount(0); return; }
    let alive = true;
    fetchSendSchedules({ limit: 50 })
      .then(res => {
        if (!alive) return;
        setPendingScheduleCount((res.schedules || []).filter(row => row.status === 'draft' || row.status === 'armed').length);
      })
      .catch(() => { /* If the badge can't be fetched, don't show it; don't bother the user */ });
    return () => { alive = false; };
  }, [valid, scheduleRefreshTick]);

  useEffect(() => {
    if (!emailRecordTarget || !valid) return;
    setCompanyDeliveryLoading(true);
    fetchSalesAgentEmailDeliveries({
      company_id: emailRecordTarget.companyId,
      lead_id: emailRecordTarget.leadId,
      limit: companyDeliveryPageSize,
      offset: (companyDeliveryPage - 1) * companyDeliveryPageSize,
    })
      .then(result => {
        setCompanyDeliveries(result.deliveries || []);
        setCompanyDeliveryTotal(result.total || 0);
      })
      .catch(e => toast.error(friendlyError(e instanceof Error ? e.message : '发送记录加载失败')))
      .finally(() => setCompanyDeliveryLoading(false));
  }, [emailRecordTarget, valid, companyDeliveryPage, companyDeliveryPageSize]);


  // Long-task progress stream. Connect only while a report is running, disconnect once all are done -- idle connections burden the server too.
  // Don't retry forever on disconnect: after a failure fall back to the original manual "Refresh", rather than freezing the UI on "connecting".
  //
  // The dependency must be only the boolean "is anything running", not leadReports itself: setLeadReports in the callback
  // swaps the leadReports reference, and every new server connection starts with an empty last_sent and always pushes a frame on connect
  // (stream() in api.py), so "push a frame -> swap reference -> dependency changes -> abort and reconnect -> push another frame"
  // becomes an infinite loop with no delay in between, firing thousands of requests within tens of seconds.
  const hasRunningReport = useMemo(
    () => Object.values(leadReports).some(r => r?.status === 'generating'),
    [leadReports],
  );

  useEffect(() => {
    if (!hasRunningReport || !valid) return;
    const ctrl = new AbortController();
    void streamSalesAgentEvents(event => {
      if (event.type !== 'report_progress') return;
      setLeadReports(prev => {
        let changed = false;
        const next = { ...prev };
        for (const leadId of event.lead_ids || []) {
          const cur = next[leadId];
          if (!cur) continue;
          const status = event.status || cur.status;
          // If the frame content is identical to the current state, leave it alone -- unconditionally returning a new object just costs an extra re-render
          if (
            cur.status === status
            && cur.done === event.done
            && cur.total === event.total
            && cur.currentStep === event.current_step
            && cur.elapsedSeconds === event.elapsed_seconds
          ) continue;
          next[leadId] = {
            ...cur,
            status,
            done: event.done,
            total: event.total,
            currentStep: event.current_step,
            elapsedSeconds: event.elapsed_seconds,
          };
          changed = true;
        }
        return changed ? next : prev;
      });
    }, ctrl.signal).catch(() => { /* On disconnect fall back to manual refresh; don't bother the user */ });
    return () => ctrl.abort();
  }, [hasRunningReport, valid]);

  async function generateReportForLead(leadIdText: string) {
    if (!valid) {
      requireLogin();
      return;
    }
    const leadId = numberOf(leadIdText, 0);
    if (!leadId || reportGeneratingLeadId) return;
    setErr(null);
    setReportGeneratingLeadId(leadId);
    try {
      const result = await generateLeadReport(leadId);
      const reportState = leadReportStateFromPayload(result as unknown as Record<string, unknown>);
      setLeadReports(prev => ({ ...prev, [leadId]: { ...prev[leadId], ...reportState } }));
      const reportStatus = result.report_status || result.status;
      if (reportStatus === 'failed') {
        toast.error(result.hint || '诊断报告发起失败,请稍后重试。');
      } else if (reportStatus === 'ready') {
        toast.success(result.hint || '报告已完成,可以查看报告并生成邮件。');
      } else {
        toast.info(result.hint || '诊断已开始,完成后再基于真实报告生成邮件。');
      }
    } catch (e) {
      toast.error(friendlyError(e instanceof Error ? e.message : '诊断报告生成失败'));
    } finally {
      setReportGeneratingLeadId(null);
    }
  }

  async function generateDraftForLead(leadIdText: string, styleKey: ReportEmailStyleKey = 'leijun_pitch', forceRegenerate = false) {
    if (!valid) {
      requireLogin();
      return;
    }
    const leadId = numberOf(leadIdText, 0);
    if (!leadId || draftGeneratingLeadId || draftRegenerating) return;
    const normalizedStyle = normalizeEmailStyleKey(styleKey);
    if (!forceRegenerate) {
      const cachedDraft = draftCacheRef.current.get(draftCacheKey(leadId, normalizedStyle));
      if (cachedDraft) {
        setErr(null);
        setReportPanelCard(null);
        showConfirmDraftItem(cachedDraft);
        return;
      }
    }
    setErr(null);
    setReportPanelCard(null);
    setActiveConfirmDraft(null);
    setDraftLoading({ leadId, styleKey: normalizedStyle });
    setSidePanelWidth(width => Math.max(width, 660));
    setSidePanelFullscreen(false);
    if (forceRegenerate) setDraftRegenerating(true);
    else setDraftGeneratingLeadId(leadId);
    try {
      const draftOptions = { style_key: normalizedStyle, variant_count: reportEmailVariantCount(normalizedStyle), force_regenerate: forceRegenerate };
      let result: SalesAgentAutoDraftResponse;
      if (isStreamableIntroStyleKey(normalizedStyle)) {
        // Only update the loading state still on this lead -- if the user switched away midway, don't write the paragraphs back.
        const patchLoading = (patch: Partial<DraftLoadingState>) =>
          setDraftLoading(state => (state && state.leadId === leadId ? { ...state, ...patch } : state));
        let streamStarted = false;
        try {
          result = await streamAutoCreateOutreachDraft(leadId, draftOptions, event => {
            if (event.type === 'head') {
              streamStarted = true;
              patchLoading({ greeting: event.greeting, tail: event.tail, fallbackSubject: event.fallback_subject, subject: '', paragraphs: [], pending: '', bodyDone: false, resetReason: undefined });
            } else if (event.type === 'subject') {
              patchLoading({ subject: event.subject });
            } else if (event.type === 'chunk') {
              setDraftLoading(state => (state && state.leadId === leadId ? { ...state, pending: (state.pending || '') + event.text } : state));
            } else if (event.type === 'paragraph') {
              // This paragraph is finalized -- the stripped version is authoritative; discard the accumulated character-by-character preview.
              setDraftLoading(state => (state && state.leadId === leadId ? { ...state, paragraphs: [...(state.paragraphs || []), event.text], pending: '' } : state));
            } else if (event.type === 'body_done') {
              patchLoading({ pending: '', bodyDone: true });
            } else if (event.type === 'reset') {
              // The model's paragraph failed the gate or was interrupted, and the backend fell back to the standard intro -- the preview paragraphs are void.
              // `detail` is the exception type, not shown to users, but it must stay in the console: without server logs
              // it's the only clue as to "why streaming didn't work".
              if (event.detail) console.warn('[intro letter stream] interrupted, degraded:', event.detail);
              patchLoading({ subject: '', paragraphs: [], pending: '', bodyDone: false, resetReason: event.reason });
            }
          });
        } catch (e) {
          if (streamStarted) throw e;
          // Failed before streaming started (most commonly this lead actually has report capability, backend 400) -- take the original path,
          // so users don't miss out on the letter because of a switch that only affects "how it's displayed".
          // But this fallback must leave a trace: a silent fallback would disguise "backend not deployed / route 404" as "streaming didn't take effect",
          // making them indistinguishable when troubleshooting.
          console.warn('[intro letter stream] could not be established, fell back to the non-streaming endpoint:', e);
          result = await autoCreateOutreachDraft(leadId, 'email', draftOptions);
        }
      } else {
        result = await autoCreateOutreachDraft(leadId, 'email', draftOptions);
      }
      const reportState = leadReportStateFromPayload(result as unknown as Record<string, unknown>);
      if (reportState.status !== 'none' || reportState.publicUrl || reportState.shareToken) {
        setLeadReports(prev => ({ ...prev, [leadId]: { ...prev[leadId], ...reportState } }));
      }
      if (result.needs_report) {
        setDraftLoading(null);
        toast.info(result.reasons?.join('；') || result.hint || '暂未生成邮件草稿，请稍后刷新后重试。');
        return;
      }
      if (result.blocked || !result.draft) {
        setDraftLoading(null);
        toast.error(result.reasons?.join('；') || result.hint || '触达草稿被阻断,未生成。');
        return;
      }
      const raw: SalesAgentCard = { tool: 'generate_outreach_copy', data: result as unknown as Record<string, unknown> };
      const [item] = confirmDraftItemsFromCards([raw]);
      if (!item) {
        setDraftLoading(null);
        setErr('触达草稿已生成,但前端无法打开详情。');
        return;
      }
      if (item.lead) {
        const share = asRecord(item.lead.report_share);
        const readyState: LeadReportState = {
          status: item.lead.report_status || textOf(share.report_status, reportState.status),
          publicUrl: item.lead.public_report_url || textOf(share.public_url, reportState.publicUrl || ''),
          shareToken: item.lead.share_token || textOf(share.share_token || share.public_token, reportState.shareToken || ''),
          reportId: item.lead.report_id || item.lead.source_report_id || textOf(share.report_id || share.source_report_id, reportState.reportId || ''),
        };
        setLeadReports(prev => ({ ...prev, [leadId]: { ...prev[leadId], ...readyState } }));
      }
      seenConfirmDraftIdsRef.current.add(item.draft.id);
      showConfirmDraftItem(item);
      // When the request used the default "recipient perspective" key but an intro letter came back (this lead has no report capability),
      // the two keys don't match -- store a copy under the requested one too, so clicking the same button next time doesn't hit the backend for nothing.
      if (normalizedStyle !== draftStyleKey(item.draft)) draftCacheRef.current.set(draftCacheKey(leadId, normalizedStyle), item);
      await loadBundle(sessionId ?? undefined, newSessionPending);
    } catch (e) {
      setDraftLoading(null);
      setErr(friendlyError(e instanceof Error ? e.message : '触达草稿生成失败'));
    } finally {
      setDraftGeneratingLeadId(null);
      setDraftRegenerating(false);
    }
  }

  async function generateReportsForLeads(leadIdTexts: string[]) {
    if (!valid) {
      requireLogin();
      return;
    }
    const leadIds = Array.from(new Set(leadIdTexts.map(id => numberOf(id, 0)).filter(Boolean)));
    if (!leadIds.length || reportGeneratingLeadId || draftGeneratingLeadId) return;
    setErr(null);
    let started = 0;
    let ready = 0;
    let failed = 0;
    try {
      for (const leadId of leadIds) {
        setReportGeneratingLeadId(leadId);
        try {
          const result = await generateLeadReport(leadId);
          const reportState = leadReportStateFromPayload(result as unknown as Record<string, unknown>);
          setLeadReports(prev => ({ ...prev, [leadId]: { ...prev[leadId], ...reportState } }));
          const reportStatus = result.report_status || result.status;
          if (reportStatus === 'failed') failed += 1;
          else {
            started += 1;
            if (reportStatus === 'ready') ready += 1;
          }
        } catch {
          failed += 1;
        }
      }
      if (started) {
        toast.info(ready === started ? '已完成 ' + ready + ' 家报告。' : '已为 ' + started + ' 家发起报告生成，完成后可批量写邮件。');
      }
      if (failed) toast.error(failed + ' 家报告生成失败，请稍后重试。');
    } finally {
      setReportGeneratingLeadId(null);
    }
  }

  async function generateDraftsForLeads(leadIdTexts: string[], styleKey: ReportEmailStyleKey = 'leijun_pitch') {
    if (!valid) {
      requireLogin();
      return;
    }
    const leadIds = Array.from(new Set(leadIdTexts.map(id => numberOf(id, 0)).filter(Boolean)));
    if (!leadIds.length || draftGeneratingLeadId || draftRegenerating || reportGeneratingLeadId) return;
    const normalizedStyle = normalizeEmailStyleKey(styleKey);
    setErr(null);
    setReportPanelCard(null);
    setDraftLoading(null);
    setSidePanelWidth(width => Math.max(width, 660));
    setSidePanelFullscreen(false);
    const draftItems: ConfirmDraftItem[] = [];
    let cached = 0;
    let needsReport = 0;
    let blocked = 0;
    let failed = 0;
    try {
      await runWithConcurrency(leadIds, SALES_AGENT_BATCH_CONCURRENCY, async leadId => {
        const cachedDraft = draftCacheRef.current.get(draftCacheKey(leadId, normalizedStyle));
        if (cachedDraft) {
          cached += 1;
          draftItems.push(cachedDraft);
          return;
        }
        setDraftGeneratingLeadId(leadId);
        try {
          const result = await autoCreateOutreachDraft(leadId, 'email', { style_key: normalizedStyle, variant_count: reportEmailVariantCount(normalizedStyle), force_regenerate: false });
          const reportState = leadReportStateFromPayload(result as unknown as Record<string, unknown>);
          if (reportState.status !== 'none' || reportState.publicUrl || reportState.shareToken) {
            setLeadReports(prev => ({ ...prev, [leadId]: { ...prev[leadId], ...reportState } }));
          }
          if (result.needs_report) {
            needsReport += 1;
            return;
          }
          if (result.blocked || !result.draft) {
            blocked += 1;
            return;
          }
          const raw: SalesAgentCard = { tool: 'generate_outreach_copy', data: result as unknown as Record<string, unknown> };
          const [item] = confirmDraftItemsFromCards([raw]);
          if (item) {
            draftItems.push(item);
            // Same as above: the requested key and the returned key aren't necessarily in the same space, so record both.
            if (normalizedStyle !== draftStyleKey(item.draft)) draftCacheRef.current.set(draftCacheKey(leadId, normalizedStyle), item);
          } else failed += 1;
        } catch {
          failed += 1;
        }
      });
      draftItems.forEach(rememberDraftItem);
      enqueueConfirmDraftItems(draftItems);
      if (draftItems.length) {
        await loadBundle(sessionId ?? undefined, newSessionPending);
        toast.success('已生成 ' + draftItems.length + ' 封邮件草稿，右侧逐封确认后才发送。');
      }
      if (needsReport) toast.info(needsReport + ' 家暂未返回邮件草稿，已跳过。');
      if (blocked) toast.error(blocked + ' 家邮件草稿被策略阻断。');
      if (failed) toast.error(failed + ' 家邮件草稿生成失败，请稍后重试。');
      if (!draftItems.length && !needsReport && !blocked && !failed && cached) toast.info('选中企业的邮件草稿已在右侧队列中。');
    } finally {
      setDraftGeneratingLeadId(null);
    }
  }

  function applyBundle(b: SalesAgentBundle, readOnly = false) {
    setBundle(prev => {
      const loadedSessions = mergeSessionLists(b.sessions, prev.sessions);
      const sessions = b.session ? mergeSessionLists(loadedSessions, [b.session]) : loadedSessions;
      // When switching historical sessions or refreshing the current one, keep the history pages the user already "loaded more" of; don't drop back to just the first 15.
      const keepLoadedPages = prev.sessions.length > b.sessions.length && sessions.length > b.sessions.length;
      return {
        ...b,
        sessions,
        sessions_has_more: keepLoadedPages ? (prev.sessions_has_more ?? b.sessions_has_more) : b.sessions_has_more,
        sessions_next_offset: keepLoadedPages ? (prev.sessions_next_offset ?? b.sessions_next_offset ?? sessions.length) : b.sessions_next_offset,
        sessions_limit: b.sessions_limit ?? prev.sessions_limit ?? 15,
        sessions_offset: b.sessions_offset ?? 0,
      };
    });
    setMessages(normalizeChatMessages(b.messages));
    setNewSessionPending(false);
    setHistoryReadOnly(readOnly);
  }

  async function loadBundle(id?: string, blankChat = false, readOnly = false) {
    try {
      const b = await fetchSalesAgentSession(id);
      if (blankChat && !id) {
        setBundle({ ...b, session: null, messages: [] });
        setMessages([]);
        setNewSessionPending(true);
        setHistoryReadOnly(false);
      } else {
        applyBundle(b, readOnly);
      }
    } catch {
      setErr(null);
      setBundle(emptyBundle);
      setMessages([]);
      setNewSessionPending(blankChat);
      setHistoryReadOnly(false);
    }
  }

  function requestUndo(target?: SalesAgentUndoTarget | null) {
    if (!valid) {
      requireLogin();
      return;
    }
    if (busyRef.current) return;
    setUndoTarget(target || null);
    setUndoConfirmOpen(true);
  }

  async function confirmUndo() {
    if (!valid || busyRef.current) {
      if (!valid) requireLogin();
      return;
    }
    setUndoConfirmOpen(false);
    setErr(null);
    busyRef.current = true;
    setBusy(true);
    activeStreamRef.current?.abort();
    activeStreamRef.current = null;
    try {
      const b = await undoSalesAgentSession(sessionId, undoTarget?.checkpoint_id ?? null);
      applyBundle(b, false);
      resetDraftConfirmations(false);
      if (b.undo?.restored) {
        toast.success('已撤销到这一步，可继续编辑。');
      } else {
        toast.info(b.undo?.message || '当前没有可撤销步骤。');
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '撤销失败');
    } finally {
      setUndoTarget(null);
      busyRef.current = false;
      setBusy(false);
    }
  }

  async function loadMoreSessions() {
    if (!valid || sessionsLoadingMore || !bundle.sessions_has_more) {
      if (!valid) requireLogin();
      return;
    }
    setSessionsLoadingMore(true);
    try {
      const page = await fetchSalesAgentSessions(bundle.sessions_next_offset ?? bundle.sessions.length, 15);
      setBundle(prev => ({
        ...prev,
        sessions: mergeSessionLists(prev.sessions, page.sessions),
        sessions_has_more: page.sessions_has_more,
        sessions_next_offset: page.sessions_next_offset,
        sessions_limit: page.sessions_limit,
        sessions_offset: page.sessions_offset,
      }));
    } catch {
      setErr('历史会话加载失败,请稍后再试。');
    } finally {
      setSessionsLoadingMore(false);
    }
  }

  function newChat() {
    activeStreamRef.current?.abort();
    activeStreamRef.current = null;
    busyRef.current = false;
    setBusy(false);
    setMessages([]);
    setInput('');
    setHistoryReadOnly(false);
    setReportPanelCard(null);
    setLeadReports({});
    resetDraftConfirmations(true);
    setBundle(prev => ({ ...prev, session: null, messages: [] }));
    setNewSessionPending(true);
  }

  function requireLogin() {
    setAuthOpen(true);
  }

  async function send(q?: string) {
    if (!valid) {
      requireLogin();
      return;
    }
    const text = (q ?? input).trim();
    if (!text || busyRef.current) return;
    busyRef.current = true;
    setSidebarExpanded(false);
    setErr(null);
    setInput('');
    setHistoryReadOnly(false);
    setBusy(true);
    batchPickerArmedRef.current = true;
    activeStreamRef.current?.abort();
    activeStreamRef.current = null;
    const targetSessionId = newSessionPending ? null : sessionId;
    const controller = new AbortController();
    activeStreamRef.current = controller;
    setMessages(m => [...m, { role: 'user', content: userFacingActionText(text) }, { role: 'assistant', content: '', metadata: { thinking_steps: ['开始'] } }]);

    // The three things streamed back each accumulate in a closure, then are written back as a whole to the last assistant message --
    // pushing directly inside the setState callback drops frames because it gets a stale snapshot.
    let answer = '';
    const steps: string[] = ['开始'];
    const streamedCards: SalesAgentCard[] = [];
    const patchAssistant = (patch: (msg: SalesAgentMessage) => SalesAgentMessage) => {
      setMessages(m => {
        const copy = [...m];
        const idx = copy.length - 1;
        if (idx >= 0 && copy[idx].role === 'assistant') copy[idx] = patch(copy[idx]);
        return copy;
      });
    };

    try {
      await streamSalesAgentChat(text, targetSessionId, {
        signal: controller.signal,
        // Stage lines are really pushed by the backend (request / organizing sales info / generating reply / saving session record),
        // no longer a fake sequence guessed by frontend regexes.
        onStage: stage => {
          if (!stage || steps[steps.length - 1] === stage) return;
          steps.push(stage);
          patchAssistant(msg => ({ ...msg, metadata: { ...(msg.metadata || {}), thinking_steps: [...steps] } }));
        },
        onDelta: d => {
          answer += d;
          patchAssistant(msg => ({ ...msg, content: answer }));
        },
        // Cards arrive as soon as a tool returns, without waiting for the whole round to finish
        onCards: cards => {
          streamedCards.push(...cards);
          patchAssistant(msg => ({ ...msg, metadata: { ...(msg.metadata || {}), tool_cards: [...streamedCards] } }));
        },
        onError: msg => {
          patchAssistant(prev => ({
            ...prev,
            content: '',
            metadata: { ...(prev.metadata || {}), process_status: 'failed', error_message: friendlyError(msg), thinking_steps: [...steps, '处理失败'] },
          }));
        },
        // At the end the DB copy is authoritative: the streamed bubble was assembled locally, only the persisted one has the id / full cards
        onDone: async streamedSessionId => {
          await loadBundle(streamedSessionId ?? targetSessionId ?? undefined, false);
          enqueueConfirmDraftItems(confirmDraftItemsFromCards(streamedCards));
        },
      }, newSessionPending, {
        default_recommendation_limit: SALES_AGENT_RECOMMENDATION_LIMIT,
      });
    } catch (e) {
      if ((e as Error)?.name === 'AbortError') return;
      const msg = friendlyError(e instanceof Error ? e.message : '请求失败');
      patchAssistant(prev => ({
        ...prev,
        content: '',
        metadata: { ...(prev.metadata || {}), process_status: 'failed', error_message: msg, thinking_steps: [...steps, '处理失败'] },
      }));
    } finally {
      if (activeStreamRef.current === controller) activeStreamRef.current = null;
      busyRef.current = false;
      setBusy(false);
    }
  }


  function switchCurrentDraftStyle(styleKey: ReportEmailStyleKey) {
    const current = confirmDraftRef.current;
    if (!current) return;
    void generateDraftForLead(String(current.draft.lead_id), styleKey, false);
  }

  function regenerateCurrentDraft(styleKey: ReportEmailStyleKey) {
    const current = confirmDraftRef.current;
    if (!current) return;
    void generateDraftForLead(String(current.draft.lead_id), styleKey, true);
  }

  async function submitDraftConfirmationFor(draft: SalesAgentDraft, confirmed: boolean) {
    if (confirmSendingDraftId !== null) return;
    if (confirmed && confirmSendingCountRef.current >= SALES_AGENT_BATCH_CONCURRENCY) {
      toast.info('发送队列忙碌中，请等前面的邮件完成后再继续。');
      return;
    }
    setErr(null);
    if (confirmed) confirmSendingCountRef.current += 1;
    setConfirmSendingDraftId(draft.id);
    try {
      const wasSent = draft.send_status === 'sent';
      const r = await confirmOutreachDraft(draft.id, confirmed, confirmed ? '右侧抽屉人工确认' : '右侧抽屉人工拒绝发送');
      setActiveConfirmDraft(null);
      const [next, ...rest] = confirmDraftQueueRef.current;
      setConfirmDraftQueueItems(rest);
      if (next) showConfirmDraftItem(next);
      await loadBundle(sessionId ?? undefined, newSessionPending);
      if (confirmed && r.blocked && r.reasons?.length) toast.error(r.reasons.join('；'));
      else if (confirmed && !r.blocked) {
        const status = r.delivery?.status || r.draft?.send_status || '';
        // With SMS wired, these hints can't hardcode "email" -- sending SMS but showing "email sent" makes users think the wrong channel was used
        const ch = outreachChannelLabel(r.draft?.channel || draft.channel);
        const isSms = (r.draft?.channel || draft.channel) === 'sms';
        if (status === 'sent') toast.success(wasSent ? `${ch}已再次发送` : `${ch}已发送`);
        else if (status === 'delivered') toast.success(`${ch}已送达`);
        else if (status === 'disabled') toast.info(r.hint || `${ch}发送已关闭,已记录但未真实外发。`);
        else if (status === 'no_recipient') toast.error(r.hint || (isSms ? '目标企业缺少可接收短信的手机号,未真实外发。' : '目标企业缺少收件邮箱,未真实外发。'));
        else if (status === 'rejected') toast.error(r.hint || r.delivery?.failure_reason || `${ch}被本地规则拦下,未真实外发;请到${ch}配置页检查频控与模板。`);
        else if (status === 'failed') toast.error(r.hint || r.delivery?.failure_reason || `${ch}发送失败`);
        else toast.success(r.hint || '已确认');
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '确认失败');
    } finally {
      if (confirmed) confirmSendingCountRef.current = Math.max(0, confirmSendingCountRef.current - 1);
      setConfirmSendingDraftId(null);
    }
  }

  async function submitDraftConfirmation(confirmed: boolean) {
    if (!confirmDraft || confirmSendingDraftId !== null) return;
    await submitDraftConfirmationFor(confirmDraft.draft, confirmed);
  }

  function closeDraftConfirmation() {
    setDraftLoading(null);
    setActiveConfirmDraft(null);
    const [next, ...rest] = confirmDraftQueueRef.current;
    setConfirmDraftQueueItems(rest);
    if (next) showConfirmDraftItem(next);
  }

  const pageStyle = embedded ? { ...page, minHeight: 'calc(100vh - 8rem)', height: 'calc(100vh - 8rem)', border: '1px solid var(--border-color)', borderRadius: 20, boxShadow: 'var(--shadow-card)' } : page;

  return (
    <div className="grid-background" style={pageStyle}>
      {!embedded && <div className="bg-glow bg-glow-1"></div>}
      {!embedded && <div className="bg-glow bg-glow-2"></div>}
      {!embedded && <div className="bg-glow bg-glow-3"></div>}
      {!embedded && <LeftSidebar
        sessions={bundle.sessions}
        activeSessionId={sessionId}
        onSelectSession={id => void loadBundle(id, false, false)}
        onNewChat={newChat}
        expanded={sidebarExpanded}
        onToggle={() => setSidebarExpanded(v => !v)}
        valid={valid}
        userLabel={userLabel}
        onLogin={requireLogin}
        onLogout={clearAuth}
        sessionsHasMore={!!bundle.sessions_has_more}
        sessionsLoadingMore={sessionsLoadingMore}
        onLoadMoreSessions={() => void loadMoreSessions()}
      />}
      <div style={main}>
        <div style={{ position: 'relative', minWidth: 0, flex: 1 }}>
          <CenterChat messages={messages} input={input} setInput={setInput} busy={busy} onSend={() => void send()} onSendText={text => void send(text)} onUndo={requestUndo} valid={valid} onNeedLogin={requireLogin} hideInput={historyReadOnly} compactRecommendations={!!(confirmDraft || draftLoading || reportPanelCard || scheduleView)} onOpenDraftCard={openDraftCard} onOpenDraftItem={openDraftItem} onGenerateDraft={leadId => void generateDraftForLead(leadId)} onGenerateReport={leadId => void generateReportForLead(leadId)} onBatchGenerateReports={leadIds => void generateReportsForLeads(leadIds)} onBatchGenerateDrafts={leadIds => void generateDraftsForLeads(leadIds)} onOpenEmailRecords={openEmailRecords} onScheduleSend={openScheduleCompose} onOpenSchedules={openScheduleList} onOpenBatchPicker={card => setBatchPicker(normalizeSendSchedulePicker(card))} pendingScheduleCount={pendingScheduleCount} draftGeneratingLeadId={draftGeneratingLeadId} reportGeneratingLeadId={reportGeneratingLeadId} leadReports={leadReports} />
          {err && (
            <div style={{ position: 'absolute', left: '50%', bottom: 92, transform: 'translateX(-50%)', maxWidth: 860, width: 'calc(100% - 48px)', border: '1px solid var(--danger-border)', background: 'var(--bg-card)', color: 'var(--text-secondary)', borderRadius: 14, padding: '10px 12px', fontSize: 13, lineHeight: 1.6, boxShadow: 'var(--shadow-card)' }}>
              {err}
            </div>
          )}
          <AuthModal isOpen={authOpen} onClose={() => setAuthOpen(false)} defaultTab="login" onSuccess={() => setAuthOpen(false)} />
          <UndoConfirmDialog open={undoConfirmOpen} busy={busy} target={undoTarget} onCancel={() => { setUndoConfirmOpen(false); setUndoTarget(null); }} onConfirm={() => void confirmUndo()} />
        </div>
        {/* Batch selection drawer: same layer and form as the "Company send records" below. It would also display inside the chat column's
            `position: relative` div (it's fixed), but that layer may get a transform/filter at any time --
            once it does, it creates a new containing block for fixed, and the drawer shrinks into that column. */}
        {batchPicker && (
          <SendScheduleBatchDrawer
            model={batchPicker}
            onClose={() => setBatchPicker(null)}
            // A successful schedule no longer auto-opens plan details: the confirmation feedback is a toast, and a popping panel would push away
            // the recommendation list the user is looking at. To see the list, go in via the "Send plans" entry.
            onScheduled={() => setScheduleRefreshTick(t => t + 1)}
          />
        )}
        {emailRecordTarget && (
          <div style={{ position: 'fixed', inset: 0, zIndex: 65, display: 'flex', justifyContent: 'flex-end' }}>
            <button type="button" aria-label="关闭发送记录" onClick={() => setEmailRecordTarget(null)} style={{ position: 'absolute', inset: 0, border: 'none', background: 'rgba(15,23,42,.38)', cursor: 'pointer' }} />
            <aside style={{ position: 'relative', width: 'min(900px, 100vw)', height: '100vh', background: 'var(--bg-card)', borderLeft: '1px solid var(--border-color)', boxShadow: '-24px 0 48px rgba(15,23,42,.18)', display: 'flex', flexDirection: 'column' }}>
              <div style={{ padding: '18px 20px 14px', borderBottom: '1px solid var(--border-color)', display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
                <div style={{ minWidth: 0 }}>
                  <div style={{ color: 'var(--text-muted)', fontSize: 12, fontWeight: 800 }}>Sales Agent · 企业发送记录</div>
                  <div style={{ color: 'var(--text-primary)', fontSize: 20, fontWeight: 900, marginTop: 4, wordBreak: 'break-word' }}>{emailRecordTarget.companyName} · 邮件发送记录</div>
                  <div style={{ color: 'var(--text-muted)', fontSize: 12, marginTop: 6 }}>只展示当前账号可见的该企业/线索邮件记录；发送仍需先生成草稿并逐封人工确认。</div>
                </div>
                <button type="button" aria-label="关闭发送记录" onClick={() => setEmailRecordTarget(null)} style={{ ...ghostBtn, width: 34, height: 34, borderRadius: 12, padding: 0, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                  <X size={17} />
                </button>
              </div>
              <div style={{ flex: 1, overflowY: 'auto', padding: 18, display: 'grid', alignContent: 'start', gap: 12 }}>
                {companyDeliveryLoading && <div style={{ border: '1px solid var(--border-color)', background: 'var(--bg-surface)', color: 'var(--text-secondary)', borderRadius: 14, padding: '10px 12px', fontSize: 13 }}>发送记录加载中…</div>}
                <DeliveryTable deliveries={companyDeliveries} onViewContent={setViewingCompanyDelivery} />
                <Pagination
                  className="mt-1"
                  page={companyDeliveryPage}
                  pageSize={companyDeliveryPageSize}
                  total={companyDeliveryTotal}
                  onPageChange={setCompanyDeliveryPage}
                  onPageSizeChange={size => { setCompanyDeliveryPageSize(size); setCompanyDeliveryPage(1); }}
                />
              </div>
            </aside>
          </div>
        )}
        <DeliveryContentDialog delivery={viewingCompanyDelivery} onClose={() => setViewingCompanyDelivery(null)} />
        {/* Reuse the persistent right area, no new Drawer (4.5). Width and fullscreen follow the report panel,
            but the interaction is different: a button here authorizes N emails at once, and after clicking not a single one has been sent yet. */}
        {!!scheduleView && (
          <aside style={sidePanelFullscreen
            ? { position: 'fixed', inset: 0, width: '100vw', height: '100vh', zIndex: 80, background: 'var(--bg-primary)', borderLeft: '1px solid var(--border-color)' }
            : { width: sidePanelWidth, maxWidth: 'min(860px, 92vw)', height: '100vh', flexShrink: 0, position: 'relative', zIndex: 2, background: 'var(--bg-primary)', borderLeft: '1px solid var(--border-color)' }}>
            <SendSchedulePanel
              view={scheduleView}
              onViewChange={setScheduleView}
              onClose={() => setScheduleView(null)}
              onScheduleChanged={() => setScheduleRefreshTick(v => v + 1)}
            />
          </aside>
        )}
        <SalesReportSidePanel
          card={reportPanelCard}
          confirmDraft={confirmDraft}
          draftLoading={draftLoading}
          draftQueueCount={confirmDraft ? confirmDraftQueue.length + 1 : 0}
          width={sidePanelWidth}
          isFullscreen={sidePanelFullscreen}
          regeneratingDraft={draftRegenerating}
          confirmSending={confirmSendingDraftId === confirmDraft?.draft.id}
          onWidthChange={setSidePanelWidth}
          onToggleFullscreen={() => setSidePanelFullscreen(v => !v)}
          onClose={confirmDraft || draftLoading ? closeDraftConfirmation : () => setReportPanelCard(null)}
          onConfirmDraft={confirmed => void submitDraftConfirmation(confirmed)}
          onSwitchDraftStyle={switchCurrentDraftStyle}
          onRegenerateDraftStyle={regenerateCurrentDraft}
          preferA2UI
        />
      </div>
    </div>
  );
}
