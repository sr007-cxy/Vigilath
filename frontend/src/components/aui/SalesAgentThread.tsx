/**
 * assistant-ui shell (piece 3). Only changes "how the conversation area runs"; message content is still rendered by the workbench's original
 * `renderMessage` -- recommendation tables, report cards and the draft queue are untouched.
 *
 * Uses ExternalStoreRuntime rather than LocalRuntime: sessions and messages already live in our own DB,
 * and letting assistant-ui take over state would mean two sources of truth.
 *
 * Two pitfalls (measured 2026-08-23, see docs/08-独立模块/SalesAgent/03-系统设计/assistant-ui与A2UI集成.md §0):
 * - `aui.composer` in message scope is the "edit this message" composer; calling it throws
 *   `Composer is not available`; sending a new message must go through `aui.thread.append`
 * - `getExternalStoreMessages` returns our own raw messages, so `tool_cards` needn't be stuffed into
 *   assistant-ui's message model
 */
import { useEffect, useMemo, useRef } from 'react';
import type { ReactNode } from 'react';
import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  ThreadPrimitive,
  getExternalStoreMessages,
  useAuiState,
  useExternalStoreRuntime,
} from '@assistant-ui/react';
import type { SalesAgentMessage } from '../../services/salesAgentApi';
import { cn } from './cn';
import { inkButton } from './surfaces';

/**
 * On by default. This function is kept not for gradual rollout, but so that **it can be turned off without redeploying when things break**:
 * run `localStorage.setItem('sales_agent_aui_shell','0')` in the browser console and refresh to get the old conversation area back.
 * To turn it off globally, rebuild with `VITE_SALES_AGENT_AUI_SHELL=0`.
 */
export function auiShellEnabled(): boolean {
  if (import.meta.env.VITE_SALES_AGENT_AUI_SHELL === '0') return false;
  try {
    // The address bar wins: `?aui=0` off, `?aui=1` on, and remembered -- when things break, nobody should be forced to open the console
    // to type localStorage (Chrome also blocks pasting until you type "allow pasting").
    const fromUrl = new URLSearchParams(window.location.search).get('aui');
    if (fromUrl === '0' || fromUrl === '1') {
      window.localStorage.setItem('sales_agent_aui_shell', fromUrl);
      return fromUrl === '1';
    }
    return window.localStorage.getItem('sales_agent_aui_shell') !== '0';
  } catch {
    return true;  // private mode can't read localStorage; default to on
  }
}

type Props = {
  messages: SalesAgentMessage[];
  busy: boolean;
  onSendText: (text: string) => void;
  renderMessage: (msg: SalesAgentMessage, index: number) => ReactNode;
  composerDisabled?: boolean;
  placeholder?: string;
};

function MessageSlot({ renderMessage, messages }: { renderMessage: Props['renderMessage']; messages: SalesAgentMessage[] }) {
  const [ours] = getExternalStoreMessages<SalesAgentMessage>(useAuiState(s => s.message)) ?? [];
  if (!ours) return null;
  const index = messages.findIndex(m => m === ours);
  return <>{renderMessage(ours, index < 0 ? messages.length - 1 : index)}</>;
}

export function SalesAgentThread({ messages, busy, onSendText, renderMessage, composerDisabled = false, placeholder = '说说你要找什么客户' }: Props) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const lastMessage = messages[messages.length - 1];
  const bottomScrollKey = useMemo(() => {
    const metadata = lastMessage?.metadata || {};
    const cards = Array.isArray(metadata.tool_cards) ? metadata.tool_cards.length : 0;
    const failed = typeof metadata.process_status === 'string' ? metadata.process_status : '';
    return [
      messages.length,
      lastMessage?.id ?? '',
      lastMessage?.role ?? '',
      lastMessage?.content ?? '',
      cards,
      failed,
      busy ? 'busy' : 'idle',
    ].join(':');
  }, [busy, lastMessage, messages.length]);

  // Follow the latest message body/card rendering, but not thinking_steps: the progress stream pushes every 3 seconds
  // and shouldn't drag away the list the user is looking at. Scroll the Viewport directly, so scrollIntoView doesn't find the outer page.
  useEffect(() => {
    const scrollToBottom = (behavior: ScrollBehavior) => {
      const viewport = viewportRef.current;
      if (viewport) {
        viewport.scrollTo({ top: viewport.scrollHeight, behavior });
        return;
      }
      endRef.current?.scrollIntoView({ behavior, block: 'end' });
    };
    const raf = window.requestAnimationFrame(() => {
      scrollToBottom('smooth');
      window.requestAnimationFrame(() => scrollToBottom('auto'));
    });
    // Historical recommendation cards get re-rendered from the persisted bundle after SSE done, so their height isn't stable on the first frame.
    // Only re-scroll a few times in a short window after the latest message changes; no long-term height watching, so the report progress stream doesn't drag away the list the user is viewing.
    const timers = [80, 180, 360, 700].map(delay => window.setTimeout(() => scrollToBottom('auto'), delay));
    return () => {
      window.cancelAnimationFrame(raf);
      timers.forEach(id => window.clearTimeout(id));
    };
  }, [bottomScrollKey]);

  const runtime = useExternalStoreRuntime<SalesAgentMessage>({
    messages,
    isRunning: busy,
    // Persisting and fetching messages stay in the workbench's existing chain; this only forwards the text
    onNew: async (m) => {
      const text = m.content.filter(p => p.type === 'text').map(p => ('text' in p ? p.text : '')).join('').trim();
      if (text) onSendText(text);
    },
    convertMessage: (m) => ({
      role: m.role === 'user' ? 'user' : 'assistant',
      content: [{ type: 'text', text: m.content || '' }],
      id: m.id != null ? String(m.id) : undefined,
    }),
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ThreadPrimitive.Root style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
        {/*
          autoScroll must be off: it re-anchors the viewport on **any** content height change, and the recommendation list
          gets a report progress update every 3 seconds -- showing up as the whole page jumping to the top on a button click and the inner company list refusing to scroll
          (2026-08-23 production). Scrolling is now controlled by endRef below: scroll to the bottom only when the message count changes.
        */}
        <ThreadPrimitive.Viewport ref={viewportRef} autoScroll={false} style={{ flex: 1, overflowY: 'auto', padding: 24 }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 18, width: '100%', maxWidth: 860, margin: '0 auto' }}>
            <ThreadPrimitive.Messages components={{
              UserMessage: () => <MessageSlot renderMessage={renderMessage} messages={messages} />,
              AssistantMessage: () => <MessageSlot renderMessage={renderMessage} messages={messages} />,
            }} />
            <div ref={endRef} />
          </div>
        </ThreadPrimitive.Viewport>
        {!composerDisabled && (
          <div style={{ padding: '14px 24px 22px' }}>
            <ComposerPrimitive.Root
              className="mx-auto flex w-full items-end gap-2 rounded-aui border border-aui-border bg-aui-card p-2"
              style={{ maxWidth: 860 }}
            >
              <ComposerPrimitive.Input
                rows={1}
                placeholder={placeholder}
                className="max-h-40 flex-1 resize-none bg-transparent px-2 py-2 text-[14px] text-aui-foreground outline-none placeholder:text-aui-muted-foreground"
              />
              <ComposerPrimitive.Send className={cn(inkButton, 'rounded-full px-4 py-2 text-[13px] font-bold')}>发送</ComposerPrimitive.Send>
            </ComposerPrimitive.Root>
          </div>
        )}
      </ThreadPrimitive.Root>
    </AssistantRuntimeProvider>
  );
}
