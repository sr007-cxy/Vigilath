// Lightweight Markdown rich-text editor -- formatting toolbar + write/preview toggle.
// Storage is always markdown source (consistent with the body_markdown contract, no HTML introduced); preview reuses react-markdown + remark-gfm.
// No new dependencies; reusable for any markdown text editing (content review body editing etc.).
import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from 'react';
import ReactMarkdown, { defaultUrlTransform } from 'react-markdown';
import type { Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';

// Preview rendering styles -- same source as AgentChatWidget, all via theme variables (dark/light adaptive).
export const markdownPreviewComponents: Components = {
  table: (p: any) => <div style={{ overflowX: 'auto', margin: '6px 0' }}><table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 13 }} {...p} /></div>,
  th: (p: any) => <th style={{ border: '1px solid var(--border-color)', padding: '4px 8px', textAlign: 'left', background: 'var(--bg-surface)', fontWeight: 600 }} {...p} />,
  td: (p: any) => <td style={{ border: '1px solid var(--border-color)', padding: '4px 8px', textAlign: 'left' }} {...p} />,
  a: (p: any) => <a {...p} target="_blank" rel="noreferrer" style={{ color: 'var(--accent-primary)', textDecoration: 'underline' }} />,
  code: (p: any) => <code style={{ background: 'var(--bg-surface)', padding: '0 4px', borderRadius: 4, fontSize: 12 }} {...p} />,
  pre: (p: any) => <pre style={{ background: 'var(--bg-surface)', padding: 12, borderRadius: 8, overflowX: 'auto', margin: '6px 0' }} {...p} />,
  ul: (p: any) => <ul style={{ margin: '4px 0', paddingLeft: 18 }} {...p} />,
  ol: (p: any) => <ol style={{ margin: '4px 0', paddingLeft: 18 }} {...p} />,
  li: (p: any) => <li style={{ margin: '1px 0' }} {...p} />,
  p: (p: any) => <p style={{ margin: '3px 0' }} {...p} />,
  h1: (p: any) => <div style={{ fontWeight: 700, fontSize: 16, margin: '8px 0 3px' }} {...p} />,
  h2: (p: any) => <div style={{ fontWeight: 700, fontSize: 15, margin: '8px 0 3px' }} {...p} />,
  h3: (p: any) => <div style={{ fontWeight: 700, margin: '6px 0 2px' }} {...p} />,
  blockquote: (p: any) => <blockquote style={{ borderLeft: '3px solid var(--border-color)', paddingLeft: 10, margin: '4px 0', color: 'var(--text-secondary)' }} {...p} />,
  img: (p: any) => <img {...p} style={{ maxWidth: '100%', borderRadius: 8, margin: '8px 0' }} />,
};

function previewUrlTransform(url: string): string {
  if (/^data:image\/(?:png|jpe?g|gif|webp|bmp);base64,/i.test(url)) return url;
  return defaultUrlTransform(url);
}

interface MarkdownPreviewProps {
  value: string;
  minHeight?: number;
  emptyText?: ReactNode;
  className?: string;
  style?: CSSProperties;
  authToken?: string;
}

function AuthMarkdownImage({
  src,
  alt,
  authToken,
  ...rest
}: {
  src?: string;
  alt?: string;
  authToken?: string;
  [key: string]: any;
}) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const isProtectedMedia = !!src && /\/api\/ai-telemetry\/topics\/\d+\/media\/\d+\/blob$/.test(src);

  useEffect(() => {
    if (!src || !authToken || !isProtectedMedia) {
      setBlobUrl(null);
      setFailed(false);
      return;
    }
    let cancelled = false;
    let createdUrl: string | null = null;
    setFailed(false);
    (async () => {
      try {
        const resp = await fetch(src, { headers: { Authorization: `Bearer ${authToken}` } });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const blob = await resp.blob();
        if (cancelled) return;
        createdUrl = URL.createObjectURL(blob);
        setBlobUrl(createdUrl);
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => {
      cancelled = true;
      if (createdUrl) URL.revokeObjectURL(createdUrl);
    };
  }, [authToken, isProtectedMedia, src]);

  if (failed) {
    return <span className="text-muted">图片加载失败</span>;
  }
  return <img src={blobUrl || src} alt={alt} {...rest} />;
}

export function MarkdownPreview({
  value,
  minHeight = 0,
  emptyText = '无内容',
  className,
  style,
  authToken,
}: MarkdownPreviewProps) {
  const components = authToken
    ? { ...markdownPreviewComponents, img: (p: any) => <AuthMarkdownImage {...p} authToken={authToken} style={{ maxWidth: '100%', borderRadius: 8, margin: '8px 0' }} /> }
    : markdownPreviewComponents;
  return (
    <div
      className={className}
      style={{ minHeight, ...style }}
    >
      {value.trim()
        ? <ReactMarkdown remarkPlugins={[remarkGfm]} components={components} urlTransform={previewUrlTransform}>{value}</ReactMarkdown>
        : <span className="text-muted">{emptyText}</span>}
    </div>
  );
}

export interface MarkdownEditorHandle {
  focus: () => void;
  insertText: (text: string, options?: { selectStart?: number; selectEnd?: number }) => void;
}

interface MarkdownEditorProps {
  value: string;
  onChange: (value: string) => void;
  minHeight?: number;
  placeholder?: string;
  toolbarActions?: ReactNode;
  previewAuthToken?: string;
}

export const MarkdownEditor = forwardRef<MarkdownEditorHandle, MarkdownEditorProps>(function MarkdownEditor({
  value,
  onChange,
  minHeight = 280,
  placeholder,
  toolbarActions,
  previewAuthToken,
}: MarkdownEditorProps, ref) {
  const taRef = useRef<HTMLTextAreaElement>(null);
  // Cursor/selection expected after a toolbar rewrite -- for a controlled component it must be set after value lands
  const selRef = useRef<{ start: number; end: number } | null>(null);
  const [tab, setTab] = useState<'write' | 'preview'>('write');

  const commit = (next: string, selection?: { start: number; end: number }) => {
    selRef.current = selection ?? null;
    onChange(next);
  };

  useEffect(() => {
    if (selRef.current && taRef.current) {
      taRef.current.focus();
      taRef.current.setSelectionRange(selRef.current.start, selRef.current.end);
      selRef.current = null;
    }
  }, [value]);

  // Wrapping markers (bold/italic/strikethrough/code...): wrap the selection; with no selection, insert a placeholder and select it
  const wrap = (before: string, after = before, placeholderText = '文字') => {
    const ta = taRef.current;
    if (!ta) return;
    const start = ta.selectionStart, end = ta.selectionEnd;
    const sel = value.slice(start, end) || placeholderText;
    const next = value.slice(0, start) + before + sel + after + value.slice(end);
    commit(next, { start: start + before.length, end: start + before.length + sel.length });
  };

  // Line-start markers (heading/quote/list): add a prefix to every line covered by the selection
  const linePrefix = (makePrefix: (index: number) => string) => {
    const ta = taRef.current;
    if (!ta) return;
    const start = ta.selectionStart, end = ta.selectionEnd;
    const lineStart = value.lastIndexOf('\n', start - 1) + 1;
    const nlAfter = value.indexOf('\n', end);
    const lineEnd = nlAfter === -1 ? value.length : nlAfter;
    const block = value.slice(lineStart, lineEnd);
    const prefixed = block.split('\n').map((l, i) => makePrefix(i) + l).join('\n');
    const next = value.slice(0, lineStart) + prefixed + value.slice(lineEnd);
    commit(next, { start: lineStart, end: lineStart + prefixed.length });
  };

  const insertLink = () => {
    const ta = taRef.current;
    if (!ta) return;
    const start = ta.selectionStart, end = ta.selectionEnd;
    const text = value.slice(start, end) || '链接文字';
    const inserted = `[${text}](url)`;
    const next = value.slice(0, start) + inserted + value.slice(end);
    const urlStart = start + 1 + text.length + 2;   // [ + text + ](
    commit(next, { start: urlStart, end: urlStart + 3 });
  };

  const insertText = (text: string, options?: { selectStart?: number; selectEnd?: number }) => {
    const ta = taRef.current;
    const start = ta ? ta.selectionStart : value.length;
    const end = ta ? ta.selectionEnd : value.length;
    const next = value.slice(0, start) + text + value.slice(end);
    const fallback = start + text.length;
    const selStart = options?.selectStart != null ? start + options.selectStart : fallback;
    const selEnd = options?.selectEnd != null ? start + options.selectEnd : selStart;
    setTab('write');
    commit(next, { start: selStart, end: selEnd });
  };

  useImperativeHandle(ref, () => ({
    focus: () => taRef.current?.focus(),
    insertText,
  }), [value]);

  const tools: { label: string; title: string; run: () => void }[] = [
    { label: 'B', title: '加粗', run: () => wrap('**') },
    { label: 'I', title: '斜体', run: () => wrap('*') },
    { label: 'S', title: '删除线', run: () => wrap('~~') },
    { label: 'H2', title: '二级标题', run: () => linePrefix(() => '## ') },
    { label: 'H3', title: '三级标题', run: () => linePrefix(() => '### ') },
    { label: '❝', title: '引用', run: () => linePrefix(() => '> ') },
    { label: '•', title: '无序列表', run: () => linePrefix(() => '- ') },
    { label: '1.', title: '有序列表', run: () => linePrefix(i => `${i + 1}. `) },
    { label: '🔗', title: '链接', run: insertLink },
    { label: '<>', title: '行内代码', run: () => wrap('`', '`', '代码') },
    { label: '{ }', title: '代码块', run: () => wrap('```\n', '\n```', '代码') },
  ];

  const frame: CSSProperties = {
    border: '1px solid var(--border-color)',
    borderRadius: 14,
    background: 'var(--bg-card)',
    boxShadow: '0 1px 2px rgba(15,23,42,0.06)',
  };

  const toolBtnStyle: CSSProperties = {
    color: 'var(--text-secondary)',
    minWidth: 32,
    fontWeight: 600,
    border: '1px solid var(--border-color)',
    background: 'var(--bg-input)',
  };

  const editorSurfaceHeight = Math.max(minHeight, 320);

  return (
    <div style={frame} className="overflow-hidden">
      {/* Top action area -- closer to common rich-text editors: business actions + preview toggle + formatting tools in layers */}
      <div style={{ borderBottom: '1px solid var(--border-color)', background: 'var(--bg-card)' }}>
        <div className="px-3 pt-3 pb-2 flex items-center gap-2 flex-wrap">
          {toolbarActions && <div className="flex items-center gap-2 flex-wrap">{toolbarActions}</div>}
          <div className="ml-auto inline-flex items-center gap-1 p-1 rounded-xl"
               style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)' }}>
            {(['write', 'preview'] as const).map(k => (
              <button key={k} type="button" onClick={() => setTab(k)}
                      className="text-xs px-3 py-1.5 rounded-lg font-medium"
                      style={{
                        background: tab === k ? 'var(--accent-primary)' : 'transparent',
                        color: tab === k ? '#fff' : 'var(--text-secondary)',
                      }}>
                {k === 'write' ? '编写' : '预览'}
              </button>
            ))}
          </div>
        </div>
        <div className="px-3 pb-3 flex items-center gap-1.5 flex-wrap">
          {tools.map(tl => (
            <button key={tl.title} type="button" title={tl.title} onClick={tl.run}
                    disabled={tab === 'preview'}
                    className="text-xs px-2.5 py-1.5 rounded-lg hover:opacity-80 disabled:opacity-30"
                    style={toolBtnStyle}>
              {tl.label}
            </button>
          ))}
        </div>
      </div>

      {tab === 'write' ? (
        <textarea
          ref={taRef}
          value={value}
          placeholder={placeholder}
          onChange={e => onChange(e.target.value)}
          className="w-full px-4 py-4 text-sm font-mono leading-7 resize-y"
          style={{
            background: 'var(--bg-surface)', color: 'var(--text-primary)',
            minHeight: editorSurfaceHeight, border: 'none', outline: 'none', display: 'block',
          }}
        />
      ) : (
        <MarkdownPreview
          value={value}
          minHeight={editorSurfaceHeight}
          className="px-4 py-4 text-sm text-primary leading-relaxed overflow-auto"
          style={{ background: 'var(--bg-surface)' }}
          authToken={previewAuthToken}
        />
      )}

      <div className="px-4 py-2 text-[11px] text-muted flex items-center justify-between"
           style={{ borderTop: '1px solid var(--border-color)', background: 'var(--bg-card)' }}>
        <span>{tab === 'write' ? 'Markdown 富文本正文' : '正文预览'}</span>
        <span>{value.length} 字</span>
      </div>
    </div>
  );
});

MarkdownEditor.displayName = 'MarkdownEditor';
