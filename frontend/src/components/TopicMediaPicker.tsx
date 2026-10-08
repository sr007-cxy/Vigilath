// Article editor asset panel -- reuses the topic media upload/list/delete chain,
// inserting image markdown or video link markdown into the body.
import { useEffect, useMemo, useRef, useState } from 'react';
import { topicProfileApi, type TopicMedia } from '../services/topicProfileApi';
import { MediaDescriptionField } from './MediaDescriptionField';
import { uiConfirm } from './ui/dialogs';
import { overlayDismiss } from './ui/useOverlayDismiss';

const IMAGE_EXTS = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'];
const VIDEO_EXTS = ['.mp4', '.mov', '.webm', '.m4v', '.mkv'];
const ACCEPT_MEDIA_EXTS = [...IMAGE_EXTS, ...VIDEO_EXTS];

export const TOPIC_IMAGE_ACCEPT = IMAGE_EXTS.join(',');
export const TOPIC_VIDEO_ACCEPT = VIDEO_EXTS.join(',');

export function mediaMarkdown(media: TopicMedia): string {
  if (!media.public_url) return '';
  const label = (media.description || '').trim() || media.filename;
  return media.kind === 'image'
    ? `![${label}](${media.public_url})`
    : `[观看视频：${label}](${media.public_url})`;
}

interface TopicMediaPickerProps {
  topicId: number;
  token: string;
  kind?: 'image' | 'video';
  refreshKey?: number;
  onInsert: (markdown: string) => void;
}

export function TopicMediaPicker({ topicId, token, kind, refreshKey, onInsert }: TopicMediaPickerProps) {
  const [list, setList] = useState<TopicMedia[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [previewMedia, setPreviewMedia] = useState<TopicMedia | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const refresh = async () => {
    setLoading(true); setErr(null);
    try {
      const items = await topicProfileApi.listMedia(topicId, token);
      setList(items);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void refresh(); }, [refreshKey, topicId, token]);

  const uploadOne = async (file: File) => {
    setBusy(true); setErr(null);
    try {
      const media = await topicProfileApi.uploadMedia(topicId, file, token);
      setList(prev => [media, ...prev]);
      const markdown = mediaMarkdown(media);
      if (markdown) onInsert(markdown);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleFiles = async (files: File[]) => {
    for (const file of files) {
      await uploadOne(file);
    }
  };

  const remove = async (media: TopicMedia) => {
    if (!(await uiConfirm(`删除素材「${media.filename}」？`, { danger: true }))) return;
    setBusy(true); setErr(null);
    try {
      await topicProfileApi.deleteMedia(topicId, media.id, token);
      setList(prev => prev.filter(x => x.id !== media.id));
      setPreviewMedia(prev => (prev?.id === media.id ? null : prev));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const previewHint = useMemo(() => {
    if (!previewMedia) return kind === 'video' ? '点击卡片可全屏预览视频' : '点击卡片可全屏预览图片';
    return previewMedia.kind === 'image' ? '当前全屏查看图片素材' : '当前全屏查看视频素材';
  }, [kind, previewMedia]);

  const visibleList = useMemo(() => (
    kind ? list.filter(media => media.kind === kind) : list
  ), [kind, list]);

  const titleText = kind === 'video' ? '视频素材' : kind === 'image' ? '图片素材' : '图片 / 视频素材';
  const descText = kind === 'video'
    ? '点击任意视频可全屏预览；若后端已提供公开地址，可直接插入正文。'
    : kind === 'image'
      ? '点击任意图片可全屏预览；若后端已提供公开地址，可直接插入正文。'
      : '点击任意素材可全屏预览；若后端已提供公开地址，可直接插入正文。';

  return (
    <>
      <section
        className="rounded-2xl p-4 space-y-4"
        style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}
      >
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div>
            <h4 className="text-sm font-semibold text-primary">{titleText}</h4>
            <p className="text-xs text-muted mt-1 leading-5">
              {descText}
            </p>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={busy}
              className="text-xs px-3 py-1.5 rounded-xl text-white"
              style={{ background: 'var(--accent-primary)', opacity: busy ? 0.5 : 1 }}
            >
              {kind === 'video' ? '上传视频' : kind === 'image' ? '上传图片' : '上传素材'}
            </button>
            <button
              type="button"
              onClick={() => void refresh()}
              disabled={busy || loading}
              className="text-xs px-3 py-1.5 rounded-xl"
              style={{ background: 'var(--bg-input)', color: 'var(--text-secondary)', border: '1px solid var(--border-color)' }}
            >
              刷新
            </button>
          </div>
        </div>

        <div className="flex items-center gap-2 flex-wrap text-[11px] text-muted">
          <span className="px-2.5 py-1 rounded-full" style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)' }}>
            {kind === 'video' ? '支持视频上传' : kind === 'image' ? '支持图片上传' : '支持图片 / 视频上传'}
          </span>
          <span className="px-2.5 py-1 rounded-full" style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)' }}>
            {previewHint}
          </span>
        </div>

        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept={kind === 'video' ? TOPIC_VIDEO_ACCEPT : kind === 'image' ? TOPIC_IMAGE_ACCEPT : ACCEPT_MEDIA_EXTS.join(',')}
          className="hidden"
          onChange={(e) => {
            const files = Array.from(e.target.files || []);
            e.target.value = '';
            void handleFiles(files);
          }}
          disabled={busy}
        />

        {err && <div className="text-xs" style={{ color: 'var(--danger-fg)' }}>{err}</div>}
        {busy && <div className="text-xs text-muted">处理中…</div>}
        {loading && <div className="text-xs text-muted">加载中…</div>}
        {!loading && visibleList.length === 0 && (
          <div className="text-xs text-muted py-6 text-center rounded-xl"
               style={{ background: 'var(--bg-surface)' }}>
            {kind === 'video' ? '还没有视频素材，先上传一份试试。' : kind === 'image' ? '还没有图片素材，先上传一份试试。' : '还没有素材，先上传一份试试。'}
          </div>
        )}

        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
          {visibleList.map(media => (
            <MediaCard
              key={media.id}
              media={media}
              token={token}
              disabled={busy}
              onSaved={saved => setList(prev => prev.map(x => x.id === saved.id ? saved : x))}
              onInsert={onInsert}
              onOpenPreview={() => setPreviewMedia(media)}
              onRemove={() => { void remove(media); }}
            />
          ))}
        </div>
      </section>

      <MediaPreviewOverlay
        media={previewMedia}
        token={token}
        onClose={() => setPreviewMedia(null)}
      />
    </>
  );
}

function MediaCard({ media, token, disabled, onSaved, onInsert, onOpenPreview, onRemove }: {
  onSaved?: (m: TopicMedia) => void;
  media: TopicMedia;
  token: string;
  disabled?: boolean;
  onInsert: (markdown: string) => void;
  onOpenPreview: () => void;
  onRemove: () => void;
}) {
  const insertMarkdown = mediaMarkdown(media);

  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [previewErr, setPreviewErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let createdUrl: string | null = null;
    setPreviewErr(null);
    setBlobUrl(null);

    (async () => {
      try {
        const resp = await fetch(media.url, { headers: { Authorization: `Bearer ${token}` } });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const blob = await resp.blob();
        if (cancelled) return;
        createdUrl = URL.createObjectURL(blob);
        setBlobUrl(createdUrl);
      } catch (e) {
        if (!cancelled) setPreviewErr(e instanceof Error ? e.message : String(e));
      }
    })();

    return () => {
      cancelled = true;
      if (createdUrl) URL.revokeObjectURL(createdUrl);
    };
  }, [media.url, token]);

  return (
    <article
      className="rounded-[22px] overflow-hidden"
      style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)', boxShadow: '0 10px 28px rgba(15,23,42,0.06)' }}
    >
      <button
        type="button"
        onClick={onOpenPreview}
        className="w-full text-left"
        disabled={!blobUrl && !!previewErr}
        title="点击全屏预览"
      >
        <div className="aspect-[4/3] flex items-center justify-center overflow-hidden text-xs text-muted relative"
             style={{ background: 'var(--bg-surface)' }}>
          {previewErr && <span style={{ color: 'var(--danger-fg)' }}>{previewErr}</span>}
          {!previewErr && !blobUrl && <span>加载中…</span>}
          {!previewErr && blobUrl && media.kind === 'image' && (
            <img src={blobUrl} alt={media.filename} className="w-full h-full object-cover" />
          )}
          {!previewErr && blobUrl && media.kind === 'video' && (
            <video src={blobUrl} muted className="w-full h-full object-cover" />
          )}
          {!previewErr && blobUrl && (
            <div className="absolute inset-x-0 bottom-0 px-3 py-2 text-[11px] text-white flex items-center justify-between"
                 style={{ background: 'linear-gradient(180deg, rgba(15,23,42,0) 0%, rgba(15,23,42,0.78) 100%)' }}>
              <span>{media.kind === 'image' ? '查看大图' : '全屏播放'}</span>
              <span>点击预览</span>
            </div>
          )}
        </div>
      </button>
      <div className="p-3 space-y-3">
        <div>
          <div className="text-sm font-medium text-primary truncate" title={media.filename}>{media.filename}</div>
          <div className="text-[11px] text-muted mt-1">
            {media.kind === 'image' ? '图片素材' : '视频素材'}
          </div>
          <div className="mt-2">
            <MediaDescriptionField media={media} token={token} disabled={disabled} onSaved={onSaved} />
          </div>
        </div>
        {!media.public_url && (
          <div className="text-[11px] leading-5 rounded-xl px-3 py-2" style={{ color: '#b45309', background: 'var(--warn-bg)' }}>
            当前后端还没提供公开地址，暂时只能预览 / 管理，不能插入正文。
          </div>
        )}
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => onInsert(insertMarkdown)}
            disabled={disabled || !media.public_url}
            className="flex-1 text-[11px] px-3 py-2 rounded-xl text-white"
            style={{ background: 'var(--accent-primary)', opacity: (disabled || !media.public_url) ? 0.5 : 1 }}
          >
            {media.kind === 'image' ? '插入图片' : '插入视频'}
          </button>
          <button
            type="button"
            onClick={onRemove}
            disabled={disabled}
            className="text-[11px] px-3 py-2 rounded-xl"
            style={{ background: 'transparent', color: 'var(--danger-fg)', border: '1px solid rgba(239,68,68,0.35)', opacity: disabled ? 0.5 : 1 }}
          >
            删除
          </button>
        </div>
      </div>
    </article>
  );
}

function MediaPreviewOverlay({ media, token, onClose }: {
  media: TopicMedia | null;
  token: string;
  onClose: () => void;
}) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!media) {
      setBlobUrl(null);
      setErr(null);
      return;
    }
    let cancelled = false;
    let createdUrl: string | null = null;
    setBlobUrl(null);
    setErr(null);

    (async () => {
      try {
        const resp = await fetch(media.url, { headers: { Authorization: `Bearer ${token}` } });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const blob = await resp.blob();
        if (cancelled) return;
        createdUrl = URL.createObjectURL(blob);
        setBlobUrl(createdUrl);
      } catch (e) {
        if (!cancelled) setErr(e instanceof Error ? e.message : String(e));
      }
    })();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);

    return () => {
      cancelled = true;
      if (createdUrl) URL.revokeObjectURL(createdUrl);
      window.removeEventListener('keydown', onKey);
    };
  }, [media, onClose, token]);

  if (!media) return null;

  return (
    <div
      className="fixed inset-0 z-[140] bg-black/95" {...overlayDismiss(onClose)}
    >
      <button
        type="button"
        onClick={onClose}
        className="absolute top-5 right-5 px-4 py-2 rounded-full text-sm text-white"
        style={{ background: 'rgba(255,255,255,0.14)', border: '1px solid rgba(255,255,255,0.18)' }}
      >
        关闭
      </button>
      <div className="absolute left-5 top-5 right-24 text-white min-w-0">
        <div className="text-base font-semibold truncate">{media.filename}</div>
        <div className="text-sm text-white/70 mt-1">全屏预览</div>
      </div>
      <div
        className="w-full h-full flex items-center justify-center p-6"
        onClick={e => e.stopPropagation()}
      >
        {err && <div className="text-sm text-red-300">{err}</div>}
        {!err && !blobUrl && <div className="text-sm text-white/70">加载中…</div>}
        {!err && blobUrl && media.kind === 'image' && (
          <img src={blobUrl} alt={media.filename} className="max-w-full max-h-full object-contain" />
        )}
        {!err && blobUrl && media.kind === 'video' && (
          <video src={blobUrl} controls autoPlay className="max-w-full max-h-full object-contain" />
        )}
      </div>
    </div>
  );
}
