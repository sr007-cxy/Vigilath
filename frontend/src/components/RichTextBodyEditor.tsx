import { useEffect, useId, useMemo, useRef, useState, type ClipboardEvent, type ReactNode } from 'react';
import ReactQuill from 'react-quill-new';
import DOMPurify from 'dompurify';
import { marked } from 'marked';
import TurndownService from 'turndown';
import { gfm } from 'turndown-plugin-gfm';
import 'react-quill-new/dist/quill.snow.css';
import { CONTENT_LOCAL_IMPORT_ACCEPT, readImportableDoc } from '../services/contentImport';
import { topicProfileApi } from '../services/topicProfileApi';
import { mediaMarkdown, TOPIC_IMAGE_ACCEPT, TOPIC_VIDEO_ACCEPT, TopicMediaPicker } from './TopicMediaPicker';
import { MarkdownPreview } from './ui/MarkdownEditor';
import { uiConfirm, uiPrompt } from './ui/dialogs';

interface RichTextBodyEditorProps {
  topicId?: number;
  token?: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  minHeight?: number;
  showMediaAction?: boolean;
}

const markdownBridge = new TurndownService({
  headingStyle: 'atx',
  bulletListMarker: '-',
  codeBlockStyle: 'fenced',
  emDelimiter: '*',
  strongDelimiter: '**',
});
markdownBridge.use(gfm);

const PASTED_IMAGE_EXTENSIONS: Record<string, string> = {
  'image/jpeg': '.jpg',
  'image/png': '.png',
  'image/gif': '.gif',
  'image/webp': '.webp',
  'image/bmp': '.bmp',
};

function pastedImageFile(file: File): File | null {
  const ext = PASTED_IMAGE_EXTENSIONS[file.type.toLowerCase()];
  if (!ext) return null;
  if (Object.values(PASTED_IMAGE_EXTENSIONS).some(suffix => file.name.toLowerCase().endsWith(suffix))) return file;
  return new File([file], `pasted-image-${Date.now()}${ext}`, { type: file.type });
}

function sanitizeHtml(html: string): string {
  return DOMPurify.sanitize(html, {
    USE_PROFILES: { html: true },
    ADD_ATTR: ['target', 'rel'],
  });
}

function markdownToEditorHtml(markdown: string): string {
  const rendered = markdown.trim()
    ? (marked.parse(markdown, { gfm: true, breaks: true }) as string)
    : '';
  const clean = sanitizeHtml(rendered);
  return clean.trim() ? clean : '<p><br></p>';
}

function htmlToMarkdown(html: string): string {
  const clean = sanitizeHtml(html || '');
  return markdownBridge.turndown(clean).replace(/\n{3,}/g, '\n\n').trim();
}

function appendImportedText(base: string, incoming: string): string {
  const left = (base || '').trimEnd();
  const right = (incoming || '').trim();
  if (!left) return right;
  if (!right) return left;
  return `${left}\n\n${right}`;
}

function DocIcon() {
  return (
    <svg viewBox="0 0 24 24" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 3H8a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V9z" />
      <path d="M14 3v6h6" />
      <path d="M9 13h6" />
      <path d="M9 17h6" />
    </svg>
  );
}

function ImageIcon() {
  return (
    <svg viewBox="0 0 24 24" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <circle cx="9" cy="10" r="1.6" />
      <path d="m21 15-4.3-4.3a1.5 1.5 0 0 0-2.1 0L8 17" />
      <path d="m11 17 2.5-2.5a1.5 1.5 0 0 1 2.1 0L18 17" />
    </svg>
  );
}

function VideoUploadIcon() {
  return (
    <svg viewBox="0 0 24 24" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="5" width="13" height="14" rx="2" />
      <path d="m10 10 4 2-4 2z" fill="currentColor" stroke="none" />
      <path d="M17 8h3a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1h-3" />
    </svg>
  );
}

function VideoLinkIcon() {
  return (
    <svg viewBox="0 0 24 24" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
      <path d="M10 13a5 5 0 0 0 7.07 0l2.12-2.12a5 5 0 0 0-7.07-7.07L10.9 5.02" />
      <path d="M14 11a5 5 0 0 0-7.07 0L4.81 13.1a5 5 0 0 0 7.07 7.07l1.41-1.41" />
    </svg>
  );
}

function UndoIcon() {
  return (
    <svg viewBox="0 0 24 24" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 14 4 9l5-5" />
      <path d="M20 20a8 8 0 0 0-8-8H4" />
    </svg>
  );
}

function RedoIcon() {
  return (
    <svg viewBox="0 0 24 24" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
      <path d="m15 14 5-5-5-5" />
      <path d="M4 20a8 8 0 0 1 8-8h8" />
    </svg>
  );
}

function FullscreenEnterIcon() {
  return (
    <svg viewBox="0 0 24 24" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
      <path d="M8 3H3v5" />
      <path d="M16 3h5v5" />
      <path d="M21 16v5h-5" />
      <path d="M8 21H3v-5" />
    </svg>
  );
}

function FullscreenExitIcon() {
  return (
    <svg viewBox="0 0 24 24" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
      <path d="M8 8H3V3" />
      <path d="M16 8h5V3" />
      <path d="M21 16v5h-5" />
      <path d="M8 16H3v5" />
    </svg>
  );
}

function ToolbarActionButton({ label, icon, disabled, variant = 'default', iconOnly = false, onClick }: {
  label: string;
  icon: ReactNode;
  disabled?: boolean;
  variant?: 'default' | 'doc' | 'image' | 'video';
  iconOnly?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      onClick={onClick}
      disabled={disabled}
      className={[
        iconOnly ? 'rich-toolbar-icon-action' : 'rich-toolbar-text-action',
        variant !== 'default' ? `is-${variant}` : '',
      ].filter(Boolean).join(' ')}
    >
      <span className="rich-toolbar-action__icon">{icon}</span>
      {!iconOnly && <span>{label}</span>}
    </button>
  );
}

function ToolbarIconButton({ title, icon, disabled, active = false, variant = 'default', onClick }: {
  title: string;
  icon: ReactNode;
  disabled?: boolean;
  active?: boolean;
  variant?: 'default' | 'doc' | 'image' | 'video';
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      onClick={onClick}
      disabled={disabled}
      className={[
        'rich-toolbar-icon-action',
        variant !== 'default' ? `is-${variant}` : '',
        active ? 'is-active' : '',
      ].filter(Boolean).join(' ')}
    >
      {icon}
    </button>
  );
}

export function RichTextBodyEditor({
  topicId,
  token,
  value,
  onChange,
  placeholder,
  minHeight = 420,
  showMediaAction = true,
}: RichTextBodyEditorProps) {
  const quillRef = useRef<ReactQuill | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);
  const videoInputRef = useRef<HTMLInputElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const lastMarkdownRef = useRef(value);
  const [html, setHtml] = useState(() => markdownToEditorHtml(value));
  const [busy, setBusy] = useState(false);
  const [mediaKind, setMediaKind] = useState<'image' | 'video'>('image');
  const [showMedia, setShowMedia] = useState(false);
  const [previewMode, setPreviewMode] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [mediaRefreshKey, setMediaRefreshKey] = useState(0);
  const toolbarId = useId().replace(/:/g, '');

  const canUseMedia = showMediaAction && topicId != null && !!token;

  useEffect(() => {
    if (value !== lastMarkdownRef.current) {
      setHtml(markdownToEditorHtml(value));
      lastMarkdownRef.current = value;
    }
  }, [value]);

  useEffect(() => {
    const sync = () => {
      const fsEl = document.fullscreenElement;
      setFullscreen(!!fsEl && fsEl === wrapRef.current);
    };
    document.addEventListener('fullscreenchange', sync);
    return () => document.removeEventListener('fullscreenchange', sync);
  }, []);

  const modules = useMemo(() => ({
    history: { delay: 300, maxStack: 100, userOnly: true },
    toolbar: { container: `#${toolbarId}` },
  }), [toolbarId]);

  const formats = useMemo(() => [
    'header', 'bold', 'italic', 'strike', 'blockquote', 'list', 'bullet', 'align', 'link', 'image', 'code-block', 'size',
  ], []);

  const applyMarkdown = (nextMarkdown: string) => {
    lastMarkdownRef.current = nextMarkdown;
    setHtml(markdownToEditorHtml(nextMarkdown));
    onChange(nextMarkdown);
  };

  const handleEditorChange = (nextHtml: string) => {
    const clean = sanitizeHtml(nextHtml);
    setHtml(clean);
    const nextMarkdown = htmlToMarkdown(clean);
    lastMarkdownRef.current = nextMarkdown;
    onChange(nextMarkdown);
    if (err) setErr(null);
  };

  const insertMarkdownSnippet = (markdown: string, successMessage: string) => {
    const editor = quillRef.current?.getEditor();
    const snippetHtml = markdownToEditorHtml(markdown);
    if (!editor) {
      const next = value ? `${value}\n\n${markdown}` : markdown;
      applyMarkdown(next);
    } else {
      const range = editor.getSelection(true);
      const index = range?.index ?? Math.max(0, editor.getLength() - 1);
      editor.clipboard.dangerouslyPasteHTML(index, snippetHtml, 'user');
      setTimeout(() => quillRef.current?.focus(), 0);
    }
    setMsg(successMessage);
    setErr(null);
    setPreviewMode(false);
    setShowMedia(false);
  };

  const runHistory = (direction: 'undo' | 'redo') => {
    const editor = quillRef.current?.getEditor();
    if (!editor) return;
    const history = editor.getModule('history') as { undo?: () => void; redo?: () => void } | undefined;
    if (direction === 'undo') history?.undo?.();
    else history?.redo?.();
  };

  const uploadMediaDirect = async (file: File, kind: 'image' | 'video') => {
    if (!topicId || !token) {
      setErr(kind === 'image' ? '当前场景暂不支持上传图片' : '当前场景暂不支持上传视频');
      return;
    }
    setBusy(true); setErr(null); setMsg(null);
    try {
      const media = await topicProfileApi.uploadMedia(topicId, file, token);
      if (media.public_url) {
        insertMarkdownSnippet(
          mediaMarkdown(media),
          kind === 'image' ? '已上传并插入图片到正文' : '已上传并插入视频到正文',
        );
      }
      setMediaKind(kind);
      setShowMedia(!media.public_url);
      setPreviewMode(false);
      setMediaRefreshKey(prev => prev + 1);
      if (!media.public_url) {
        setMsg(kind === 'image' ? `已上传图片 ${media.filename}` : `已上传视频 ${media.filename}`);
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleMediaPaste = (event: ClipboardEvent<HTMLDivElement>) => {
    const item = Array.from(event.clipboardData.items)
      .find(candidate => candidate.kind === 'file' && candidate.type.startsWith('image/'));
    if (!item) return;
    event.preventDefault();
    const file = item.getAsFile();
    const upload = file && pastedImageFile(file);
    if (!upload) {
      setErr('不支持粘贴该图片格式');
      return;
    }
    void uploadMediaDirect(upload, 'image');
  };

  const toggleFullscreen = async () => {
    const host = wrapRef.current;
    if (!host) return;
    try {
      if (document.fullscreenElement === host) {
        await document.exitFullscreen();
      } else {
        await host.requestFullscreen();
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  const importDoc = async (file: File) => {
    setBusy(true); setErr(null); setMsg(null);
    try {
      const imported = await readImportableDoc(file);
      const incoming = (imported.text || '').trim();
      if (!incoming) {
        setErr('导入结果为空,请换个文件再试');
        return;
      }

      let nextMarkdown = incoming;
      if (value.trim()) {
        const replace = await uiConfirm(
          `正文里已有内容。要用「${imported.filename}」覆盖当前正文吗？`,
          { title: '导入文档到正文', okText: '替换正文', cancelText: '先不替换' },
        );
        if (!replace) {
          const append = await uiConfirm(
            `那要把「${imported.filename}」追加到正文末尾吗？`,
            { title: '导入文档到正文', okText: '追加到末尾', cancelText: '取消导入' },
          );
          if (!append) return;
          nextMarkdown = appendImportedText(value, incoming);
        }
      }

      applyMarkdown(nextMarkdown);
      setMsg(`已导入 ${imported.filename}`);
      setShowMedia(false);
      setPreviewMode(false);
      setTimeout(() => quillRef.current?.focus(), 0);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const insertVideoLink = async () => {
    const url = await uiPrompt('输入视频链接(URL):', {
      title: '插入视频链接',
      placeholder: 'https://www.bilibili.com/...',
    });
    if (url === null) return;
    const cleanUrl = url.trim();
    if (!cleanUrl) {
      setErr('视频链接不能为空');
      return;
    }
    const label = await uiPrompt('链接文字(可留空):', {
      title: '插入视频链接',
      defaultValue: '观看视频',
      placeholder: '例如：观看视频 / B站案例视频',
    });
    if (label === null) return;
    const cleanLabel = label.trim() || '观看视频';
    insertMarkdownSnippet(`[${cleanLabel}](${cleanUrl})`, '已插入视频链接');
  };

  return (
    <div className="space-y-3">
      <section
        ref={wrapRef}
        className={`rounded-[22px] overflow-hidden ${fullscreen ? 'w-screen h-screen rounded-none' : ''}`}
        style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept={CONTENT_LOCAL_IMPORT_ACCEPT}
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = '';
            if (file) void importDoc(file);
          }}
          disabled={busy}
        />
        <input
          ref={imageInputRef}
          type="file"
          accept={TOPIC_IMAGE_ACCEPT}
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = '';
            if (file) void uploadMediaDirect(file, 'image');
          }}
          disabled={busy || !canUseMedia}
        />
        <input
          ref={videoInputRef}
          type="file"
          accept={TOPIC_VIDEO_ACCEPT}
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = '';
            if (file) void uploadMediaDirect(file, 'video');
          }}
          disabled={busy || !canUseMedia}
        />

        <div className="rich-body-editor" onPasteCapture={handleMediaPaste}>
          <div id={toolbarId} className="ql-toolbar ql-snow rich-body-editor-toolbar">
            <span className="ql-formats">
              <select className="ql-header" defaultValue="" aria-label="标题级别">
                <option value="1" />
                <option value="2" />
                <option value="3" />
                <option value="" />
              </select>
              <select className="ql-size" defaultValue="" aria-label="字号">
                <option value="small" />
                <option value="" />
                <option value="large" />
                <option value="huge" />
              </select>
            </span>

            <span className="ql-formats">
              <button type="button" className="ql-bold" aria-label="加粗" />
              <button type="button" className="ql-italic" aria-label="斜体" />
              <button type="button" className="ql-strike" aria-label="删除线" />
              <button type="button" className="ql-blockquote" aria-label="引用" />
            </span>

            <span className="ql-formats">
              <button type="button" className="ql-list" value="ordered" aria-label="有序列表" />
              <button type="button" className="ql-list" value="bullet" aria-label="无序列表" />
              <select className="ql-align" defaultValue="" aria-label="对齐方式">
                <option value="" />
                <option value="center" />
                <option value="right" />
                <option value="justify" />
              </select>
            </span>

            <span className="ql-formats">
              <button type="button" className="ql-link" aria-label="插入链接" />
              <button type="button" className="ql-code-block" aria-label="代码块" />
              <button type="button" className="ql-clean" aria-label="清除格式" />
            </span>

            <span className="rich-toolbar-divider" />

            <span className="ql-formats rich-toolbar-business">
              <ToolbarActionButton
                label={busy ? '导入中…' : '导入文档'}
                icon={<DocIcon />}
                disabled={busy}
                variant="doc"
                iconOnly
                onClick={() => fileInputRef.current?.click()}
              />

              {showMediaAction && (
                <>
                  <ToolbarIconButton
                    title="上传图片"
                    icon={<ImageIcon />}
                    disabled={busy || !canUseMedia}
                    variant="image"
                    active={showMedia && mediaKind === 'image'}
                    onClick={() => imageInputRef.current?.click()}
                  />
                  <ToolbarIconButton
                    title="上传视频"
                    icon={<VideoUploadIcon />}
                    disabled={busy || !canUseMedia}
                    variant="video"
                    active={showMedia && mediaKind === 'video'}
                    onClick={() => videoInputRef.current?.click()}
                  />
                  <ToolbarIconButton
                    title="插入视频链接"
                    icon={<VideoLinkIcon />}
                    disabled={busy}
                    onClick={() => { void insertVideoLink(); }}
                  />
                </>
              )}
            </span>

            <span className="ql-formats">
              <ToolbarIconButton title="撤销" icon={<UndoIcon />} onClick={() => runHistory('undo')} />
              <ToolbarIconButton title="重做" icon={<RedoIcon />} onClick={() => runHistory('redo')} />
            </span>

            <span className="rich-toolbar-spacer" />

            <div className="rich-toolbar-mode-switch">
              <button
                type="button"
                className={!previewMode ? 'is-active' : ''}
                onClick={() => setPreviewMode(false)}
              >
                编辑
              </button>
              <button
                type="button"
                className={previewMode ? 'is-active' : ''}
                onClick={() => setPreviewMode(true)}
              >
                预览
              </button>
            </div>

            <ToolbarIconButton
              title={fullscreen ? '退出全屏' : '全屏编辑'}
              icon={fullscreen ? <FullscreenExitIcon /> : <FullscreenEnterIcon />}
              active={fullscreen}
              onClick={() => { void toggleFullscreen(); }}
            />
          </div>

          {!previewMode ? (
            <ReactQuill
              ref={quillRef}
              theme="snow"
              value={html}
              onChange={handleEditorChange}
              modules={modules}
              formats={formats}
              placeholder={placeholder || '请输入正文内容…'}
              style={{ minHeight: fullscreen ? 'calc(100vh - 110px)' : minHeight }}
            />
          ) : (
            <div className="px-4 py-4" style={{ minHeight: fullscreen ? 'calc(100vh - 110px)' : minHeight, background: 'var(--bg-surface)' }}>
              <MarkdownPreview
                value={value}
                authToken={token}
                emptyText="正文还没有内容"
              />
            </div>
          )}
        </div>

        <div className="px-4 py-2 text-[11px] text-muted flex items-center justify-between"
             style={{ borderTop: '1px solid var(--border-color)', background: 'var(--bg-card)' }}>
          <span>{previewMode ? '正文预览' : '正文编辑'}</span>
          <span>{value.length} 字</span>
        </div>
      </section>

      {msg && <div className="text-xs" style={{ color: 'var(--success-fg)' }}>{msg}</div>}
      {err && <div className="text-xs" style={{ color: 'var(--danger-fg)' }}>{err}</div>}

      {showMedia && canUseMedia && (
        <TopicMediaPicker
          topicId={topicId}
          token={token}
          kind={mediaKind}
          refreshKey={mediaRefreshKey}
          onInsert={(markdown) => insertMarkdownSnippet(markdown, mediaKind === 'image' ? '已插入图片到正文' : '已插入视频到正文')}
        />
      )}
    </div>
  );
}
