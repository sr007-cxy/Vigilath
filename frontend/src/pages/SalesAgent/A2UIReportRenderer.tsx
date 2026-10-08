import { Component, useCallback, useEffect, useMemo, type ReactNode } from 'react';
import { A2uiSurface, basicCatalog } from '@a2ui/react/v0_9';
import { MessageProcessor, type A2uiClientAction } from '@a2ui/web_core/v0_9';
import type { A2UIReportPayload } from './a2uiBridge';

type Props = {
  payload: A2UIReportPayload;
  onRenderError: (error: Error) => void;
};

type ErrorBoundaryProps = {
  children: ReactNode;
  onError: (error: Error) => void;
};

type ErrorBoundaryState = {
  hasError: boolean;
};

class A2UIRenderBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: Error) {
    this.props.onError(error);
  }

  render() {
    if (this.state.hasError) return null;
    return this.props.children;
  }
}

function reportPageTarget(url: string): string {
  return /^https?:\/\//i.test(url) ? '_blank' : '_self';
}

export function A2UIReportRenderer({ payload, onRenderError }: Props) {
  const handleAction = useCallback((action: A2uiClientAction) => {
    if (action.name !== 'open_report') return;
    const href = typeof action.context?.href === 'string' ? action.context.href : payload.report.reportPage;
    if (!href) return;
    if (reportPageTarget(href) === '_blank') {
      window.open(href, '_blank', 'noreferrer');
      return;
    }
    window.location.assign(href);
  }, [payload.report.reportPage]);

  const processed = useMemo(() => {
    try {
      const processor = new MessageProcessor([basicCatalog], action => void handleAction(action));
      processor.processMessages(payload.messages);
      const surface = processor.model.getSurface(payload.surfaceId) ?? Array.from(processor.model.surfacesMap.values())[0] ?? null;
      if (!surface) return { processor, surface: null, error: new Error('A2UI surface not found') };
      return { processor, surface, error: null };
    } catch (error) {
      return { processor: null, surface: null, error: error instanceof Error ? error : new Error(String(error)) };
    }
  }, [handleAction, payload.messages, payload.surfaceId]);

  useEffect(() => {
    if (processed.error) onRenderError(processed.error);
  }, [onRenderError, processed.error]);

  useEffect(() => () => {
    processed.processor?.model.dispose();
  }, [processed.processor]);

  if (!processed.surface) return null;

  return (
    <A2UIRenderBoundary onError={onRenderError}>
      <style>{`
        .sales-agent-a2ui-surface {
          --a2ui-color-primary: var(--accent-primary);
          --a2ui-color-primary-hover: color-mix(in srgb, var(--accent-primary) 86%, #ffffff);
          --a2ui-color-on-primary: var(--solid-btn-text);
          --a2ui-color-surface: var(--bg-surface);
          --a2ui-color-on-surface: var(--text-primary);
          --a2ui-color-background: var(--bg-card);
          --a2ui-color-on-background: var(--text-primary);
          --a2ui-color-border: var(--border-color);
          --a2ui-border: 1px solid var(--border-color);
          --a2ui-border-radius: 16px;
          --a2ui-card-background: var(--bg-surface);
          --a2ui-card-border-radius: 18px;
          --a2ui-card-box-shadow: none;
          --a2ui-card-margin: 0;
          --a2ui-card-padding: 15px;
          --a2ui-spacing-xs: 4px;
          --a2ui-spacing-s: 8px;
          --a2ui-spacing-m: 10px;
          --a2ui-spacing-l: 14px;
          --a2ui-font-size-xs: 12px;
          --a2ui-font-size-s: 13px;
          --a2ui-font-size-m: 13.5px;
          --a2ui-font-size-l: 16px;
          --a2ui-font-size-xl: 20px;
          --a2ui-line-height-body: 1.75;
          --a2ui-row-gap: 10px;
          --a2ui-column-gap: 14px;
          --a2ui-text-caption-color: var(--text-muted);
          color: var(--text-primary);
        }
        .sales-agent-a2ui-surface button {
          border: 0;
          border-radius: 14px;
          background: var(--solid-btn-bg);
          color: var(--solid-btn-text);
          box-shadow: var(--glow-primary);
          cursor: pointer;
          font: inherit;
          font-weight: 800;
          padding: 11px 14px;
          width: 100%;
        }
        .sales-agent-a2ui-surface button:hover { filter: brightness(1.03); }
      `}</style>
      <div className="sales-agent-a2ui-surface">
        <A2uiSurface surface={processed.surface} />
      </div>
    </A2UIRenderBoundary>
  );
}
