// Asset description -- "what this image is". Draft-generation recall and LLM image selection both rely on it; filenames no longer factor into guessing
// (`IMG_2031.jpg` can never be guessed). Saves on blur; on save failure the old value is restored.
import { useEffect, useState } from 'react';
import { topicProfileApi, type TopicMedia } from '../services/topicProfileApi';

export function MediaDescriptionField({ media, token, disabled, onSaved, compact }: {
  media: TopicMedia; token: string; disabled?: boolean;
  onSaved?: (m: TopicMedia) => void; compact?: boolean;
}) {
  const [val, setVal] = useState(media.description || '');
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { setVal(media.description || ''); }, [media.id, media.description]);

  const commit = async () => {
    const next = val.trim();
    if (next === (media.description || '').trim()) return;
    setSaving(true); setErr(null);
    try {
      const m = await topicProfileApi.updateMedia(media.topic_id, media.id, next, token);
      onSaved?.(m);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setVal(media.description || '');
    } finally { setSaving(false); }
  };

  return (
    <div>
      <input
        value={val}
        onChange={e => setVal(e.target.value)}
        onBlur={() => void commit()}
        onKeyDown={e => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }}
        onClick={e => e.stopPropagation()}
        disabled={disabled || saving}
        maxLength={500}
        placeholder="这张图是什么?(如:产品主图 / 车间实拍 / 创始人照片)"
        title="写清楚这张图是什么,生成文章时才会用对地方;不写就只能靠文件名猜"
        className={`w-full rounded-md px-2 ${compact ? 'py-0.5 text-[10px]' : 'py-1 text-[11px]'}`}
        style={{
          background: 'var(--bg-input)', border: '1px solid var(--border-color)',
          color: 'var(--text-primary)', opacity: (disabled || saving) ? 0.6 : 1,
        }}
      />
      {err && <div className="text-[10px] mt-0.5" style={{ color: 'var(--danger-fg)' }}>{err}</div>}
    </div>
  );
}
