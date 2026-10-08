import { useRef, useState } from 'react';
import { CONTENT_LOCAL_IMPORT_ACCEPT, readImportableDoc } from '../services/contentImport';
import { TopicMediaPicker } from './TopicMediaPicker';
import { MarkdownEditor, type MarkdownEditorHandle } from './ui/MarkdownEditor';
import { uiConfirm, uiPrompt } from './ui/dialogs';

interface ArticleBodyEditorProps {
  topicId?: number;
  token?: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  minHeight?: number;
  showMediaAction?: boolean;
}

function appendImportedText(base: string, incoming: string): string {
  const left = (base || '').trimEnd();
  const right = (incoming || '').trim();
  if (!left) return right;
  if (!right) return left;
  return `${left}\n\n${right}`;
}

export function ArticleBodyEditor({
  topicId,
  token,
  value,
  onChange,
  placeholder,
  minHeight = 320,
  showMediaAction = true,
}: ArticleBodyEditorProps) {
  const editorRef = useRef<MarkdownEditorHandle>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [showMedia, setShowMedia] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const canUseMedia = showMediaAction && topicId != null && !!token;

  const importDoc = async (file: File) => {
    setBusy(true); setErr(null); setMsg(null);
    try {
      const imported = await readImportableDoc(file);
      const incoming = (imported.text || '').trim();
      if (!incoming) {
        setErr('导入结果为空,请换个文件再试');
        return;
      }

      let next = incoming;
      if (value.trim()) {
        const replace = await uiConfirm(
          `正文里已有内容。要用「${imported.filename}」覆盖当前正文吗？`,
          { title: '导入文档到正文', okText: '替换正文', cancelText: '先不替换' },
        );
        if (replace) next = incoming;
        else {
          const append = await uiConfirm(
            `那要把「${imported.filename}」追加到正文末尾吗？`,
            { title: '导入文档到正文', okText: '追加到末尾', cancelText: '取消导入' },
          );
          if (!append) return;
          next = appendImportedText(value, incoming);
        }
      }

      onChange(next);
      setMsg(`已导入 ${imported.filename}`);
      setShowMedia(false);
      setTimeout(() => editorRef.current?.focus(), 0);
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
    editorRef.current?.insertText(`[${cleanLabel}](${cleanUrl})`);
    setMsg('已插入视频链接');
    setErr(null);
  };

  const insertMediaMarkdown = (markdown: string) => {
    const prefix = value && !value.endsWith('\n') ? '\n\n' : (value ? '\n' : '');
    editorRef.current?.insertText(`${prefix}${markdown}\n\n`);
    setMsg('已插入素材到正文');
    setErr(null);
  };

  const actionBtnStyle = {
    background: 'var(--bg-input)', color: 'var(--text-secondary)', border: '1px solid var(--border-color)',
  };

  return (
    <div className="space-y-2">
      <MarkdownEditor
        ref={editorRef}
        value={value}
        onChange={(next) => { onChange(next); if (err) setErr(null); }}
        minHeight={minHeight}
        placeholder={placeholder}
        previewAuthToken={token}
        toolbarActions={(
          <>
            <button type="button" onClick={() => fileInputRef.current?.click()} disabled={busy}
                    className="text-xs px-2.5 py-1 rounded-md" style={actionBtnStyle}>
              {busy ? '导入中…' : '导入文档'}
            </button>
            {showMediaAction && (
              <button type="button" onClick={() => setShowMedia(v => !v)} disabled={busy || !canUseMedia}
                      className="text-xs px-2.5 py-1 rounded-md" style={actionBtnStyle}
                      title={canUseMedia ? '上传图片/视频素材' : '当前场景暂不支持素材面板'}>
                {showMedia ? '收起素材' : '上传素材'}
              </button>
            )}
            <button type="button" onClick={() => { void insertVideoLink(); }} disabled={busy}
                    className="text-xs px-2.5 py-1 rounded-md" style={actionBtnStyle}>
              插入视频链接
            </button>
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
          </>
        )}
      />
      {msg && <div className="text-xs" style={{ color: 'var(--success-fg)' }}>{msg}</div>}
      {err && <div className="text-xs" style={{ color: 'var(--danger-fg)' }}>{err}</div>}
      {showMedia && canUseMedia && (
        <TopicMediaPicker
          topicId={topicId}
          token={token}
          onInsert={insertMediaMarkdown}
        />
      )}
    </div>
  );
}
