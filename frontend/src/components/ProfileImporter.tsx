// AI 智能填充 — 拖入文件 / 粘贴文本 → 后端 LLM 抽取 BrandProfile → 合并回资料表单.
//
// 后端路由:POST /api/ai-telemetry/profile/extract (geo/api/ai_telemetry.py)
// 复用同一个 DeepSeek 通道(直连 + OpenRouter fallback)。后端没配 key 时返回 503,
// 这里弹错误提示给用户而不是静默吞。
//
// 合并策略:
//   - 「填充空白」:只填当前资料里为空的字段(数组空、字符串为空白),不动用户已经手填的
//   - 「全部覆盖」:LLM 抽到的非空值都覆盖现有值
// 默认是「填充空白」,避免一不小心把用户精心写好的内容冲掉。
//
// 交互:
//   - 默认展开,免得用户看不到拖放区
//   - 拖入文件后自动用「填充空白」调用一次后端;粘贴文本走 textarea + 手动按钮

import { useRef, useState } from 'react';
import type { BrandProfile } from '../services/aiTelemetryApi';
import { topicProfileApi } from '../services/topicProfileApi';

// 收窄到三种格式 — txt/md 客户端 file.text() 直读,docx 二进制走后端解析。
// .pdf / .doc / .csv / .json 等都不接受;PDF 扫描件常抽不到字反而误导用户。
const TEXT_EXTS = ['.txt', '.md'];
const BINARY_EXTS = ['.docx'];
const ACCEPT_EXTS = [...TEXT_EXTS, ...BINARY_EXTS];
const MAX_TEXT_LEN = 60000;
const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

interface ProfileImporterProps {
  profile: BrandProfile;
  onApply: (next: BrandProfile) => void;
  token: string;
  disabled?: boolean;
  // 可选:把 LLM 顺手给的种子提示词候选合并到种子词步骤。
  // 调用方负责去重 — Importer 拿到的就是 LLM 清洗后的清单。
  // TopicEditor 里有种子 state,会传;TopicProfile 的种子在另一个 tab + 后端表里,不传。
  onApplySeeds?: (suggestions: string[]) => void;
}

export function ProfileImporter({ profile, onApply, token, disabled, onApplySeeds }: ProfileImporterProps) {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [okMsg, setOkMsg] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);
  const [open, setOpen] = useState(true);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // 单一可信源:当前 profile 通过 ref 暴露给 callExtract,避免 merge 用旧 props。
  // 拖入后立刻 setText + 异步 callExtract,closure 抓的 profile 是回调时的 props,
  // 父组件 re-render 后 profile 新值靠 ref 取。
  const profileRef = useRef(profile);
  profileRef.current = profile;

  const callExtract = async (rawText: string, mode: 'fill-blank' | 'overwrite') => {
    const t = rawText.trim();
    if (!t || t.length < 10) {
      setErr('请先拖入文件或在文本框里粘贴内容(至少 10 个字符)');
      return;
    }
    setBusy(true); setErr(null); setOkMsg(null);
    try {
      const resp = await topicProfileApi.extractProfile(t, token);
      const merged = mergeProfile(profileRef.current, resp.profile, mode);
      const changed = countChanged(profileRef.current, merged);
      onApply(merged);
      const seeds = resp.seed_suggestions || [];
      if (seeds.length > 0 && onApplySeeds) onApplySeeds(seeds);
      const seedHint = seeds.length > 0 && onApplySeeds ? `,种子提示词候选 ${seeds.length} 条已带到下一步` : '';
      if (changed === 0) {
        setOkMsg(`没解出可用字段(原文可能太短或没有品牌信息)${seedHint},可手动调整后再试`);
      } else {
        setOkMsg(`已${mode === 'overwrite' ? '覆盖' : '填充'} ${changed} 个字段${seedHint}`);
      }
    } catch (e) {
      setErr(humanizeFetchError(e));
    } finally {
      setBusy(false);
    }
  };

  const callExtractFile = async (file: File, mode: 'fill-blank' | 'overwrite') => {
    setBusy(true); setErr(null);
    try {
      const resp = await topicProfileApi.extractProfileFile(file, token);
      const merged = mergeProfile(profileRef.current, resp.profile, mode);
      const changed = countChanged(profileRef.current, merged);
      onApply(merged);
      const seeds = resp.seed_suggestions || [];
      if (seeds.length > 0 && onApplySeeds) onApplySeeds(seeds);
      const seedHint = seeds.length > 0 && onApplySeeds ? `,种子提示词候选 ${seeds.length} 条已带到下一步` : '';
      if (changed === 0) {
        setOkMsg(`没解出可用字段(文件可能太短或没有品牌信息)${seedHint},可手动调整后再试`);
      } else {
        setOkMsg(`已${mode === 'overwrite' ? '覆盖' : '填充'} ${changed} 个字段${seedHint}`);
      }
    } catch (e) {
      setErr(humanizeFetchError(e));
    } finally {
      setBusy(false);
    }
  };

  const readFile = async (file: File) => {
    const ext = (file.name.match(/\.[a-z0-9]+$/i)?.[0] || '').toLowerCase();
    const isText = TEXT_EXTS.includes(ext);
    const isBinary = BINARY_EXTS.includes(ext);
    if (!isText && !isBinary) {
      if (ext === '.doc') {
        setErr('暂不支持 .doc 旧格式,请用 Word「另存为」.docx 后重试');
      } else if (ext === '.pdf') {
        setErr('暂不支持 PDF,请复制内容粘贴到下方文本框,或另存为 .docx / .txt / .md');
      } else {
        setErr(`不支持的文件类型(${ext || '未知'});只接受 ${ACCEPT_EXTS.join(' / ')}`);
      }
      return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      setErr(`文件过大(${Math.floor(file.size / 1024)} KB > ${MAX_UPLOAD_BYTES / 1024 / 1024} MB 上限),请精简后再试`);
      return;
    }

    if (isBinary) {
      // PDF / Word — 二进制,前端 file.text() 拿不到字,直接 multipart 给后端
      setText('');
      setErr(null);
      setOkMsg(`已上传 ${file.name}(${Math.floor(file.size / 1024)} KB),后端正在解析…`);
      await callExtractFile(file, 'fill-blank');
      return;
    }

    // 文本类 — 客户端读出后塞到 textarea,顺便走旧的 /profile/extract JSON 路径
    try {
      const content = await file.text();
      const trimmed = content.length > MAX_TEXT_LEN ? content.slice(0, MAX_TEXT_LEN) : content;
      setText(trimmed);
      setErr(null);
      setOkMsg(`已读取 ${file.name}(${trimmed.length.toLocaleString()} 字),正在用 AI 解析…`);
      await callExtract(trimmed, 'fill-blank');
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDrag(false);
    if (disabled || busy) return;
    const f = e.dataTransfer.files?.[0];
    if (f) readFile(f);
  };

  const onPickFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) readFile(f);
    e.target.value = '';
  };

  return (
    <section className="rounded-md p-4"
             style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
      <header className="flex items-center justify-between gap-3 mb-2">
        <div>
          <h3 className="text-sm font-semibold text-primary">AI 智能填充</h3>
          <p className="text-xs text-muted mt-0.5">
            拖入 .txt / .md / .docx,或粘贴公司简介 / 官网文案 / PRD,AI 帮你自动填好下面 6 大模块。
          </p>
        </div>
        <button type="button" onClick={() => setOpen(o => !o)}
                disabled={disabled}
                className="text-xs px-2.5 py-1 rounded-md"
                style={{ background: 'var(--bg-tertiary)', color: 'var(--text-primary)',
                         opacity: disabled ? 0.5 : 1 }}>
          {open ? '收起' : '展开'}
        </button>
      </header>

      {open && (
        <div className="space-y-3">
          <div onDragOver={e => { e.preventDefault(); if (!disabled && !busy) setDrag(true); }}
               onDragLeave={() => setDrag(false)}
               onDrop={onDrop}
               onClick={() => fileInputRef.current?.click()}
               className="rounded-md p-4 text-center text-xs cursor-pointer transition-colors"
               style={{
                 border: `1px dashed ${drag ? 'var(--accent-primary)' : 'var(--border-color)'}`,
                 background: drag ? 'rgba(99,102,241,0.06)' : 'var(--bg-input)',
                 color: 'var(--text-secondary)',
                 opacity: disabled ? 0.5 : 1,
               }}>
            {drag ? '松开以读取' : (
              <>
                拖入文件或<span style={{ color: 'var(--accent-primary)' }}>点击选择</span>
                <span className="block text-muted mt-1">
                  支持 .txt / .md / .docx;PDF / Word(.doc)请复制内容粘贴到下方
                </span>
              </>
            )}
            <input ref={fileInputRef} type="file" accept={ACCEPT_EXTS.join(',')}
                   className="hidden" onChange={onPickFile} disabled={disabled || busy} />
          </div>

          <textarea value={text} onChange={e => setText(e.target.value.slice(0, MAX_TEXT_LEN))}
                    rows={6}
                    placeholder="或直接粘贴公司简介 / 官网文案 / PRD,AI 会从中抽取 6 大模块字段..."
                    disabled={disabled || busy}
                    className="w-full text-sm px-3 py-2 rounded-md"
                    style={{ background: 'var(--bg-input)', color: 'var(--text-primary)',
                             border: '1px solid var(--border-color)' }} />
          <div className="flex items-center justify-between gap-2 flex-wrap text-xs">
            <span className="text-muted">{text.length.toLocaleString()} / {MAX_TEXT_LEN.toLocaleString()} 字</span>
            <div className="flex items-center gap-2">
              <button type="button" disabled={disabled || busy || text.trim().length < 10}
                      onClick={() => callExtract(text, 'fill-blank')}
                      className="text-xs px-3 py-1.5 rounded-md text-white"
                      style={{ background: 'var(--accent-primary)',
                               opacity: (disabled || busy || text.trim().length < 10) ? 0.5 : 1 }}>
                {busy ? '解析中…' : 'AI 解析'}
              </button>
              <button type="button" disabled={disabled || busy || text.trim().length < 10}
                      onClick={() => callExtract(text, 'overwrite')}
                      className="text-xs px-3 py-1.5 rounded-md"
                      style={{ background: 'var(--bg-tertiary)', color: 'var(--text-primary)',
                               opacity: (disabled || busy || text.trim().length < 10) ? 0.5 : 1 }}>
                全部覆盖
              </button>
            </div>
          </div>
          {err && <div className="text-xs" style={{ color: '#ef4444' }}>{err}</div>}
          {okMsg && <div className="text-xs" style={{ color: '#10b981' }}>{okMsg}</div>}
        </div>
      )}
    </section>
  );
}


// ─────────────── merge 策略 ────────────────────────────────

function isBlank(v: unknown): boolean {
  if (v == null) return true;
  if (typeof v === 'string') return v.trim().length === 0;
  if (Array.isArray(v)) return v.length === 0;
  return false;
}

// 把 fetch 抛的 TypeError("Failed to fetch") 翻译成对用户更友好的提示.
// 常见诱因:中间代理(BMS02 EIP 映射)在 60s 把长连接砍掉,导致连接重置.
function humanizeFetchError(e: unknown): string {
  const raw = e instanceof Error ? e.message : String(e);
  if (/failed to fetch|networkerror|load failed/i.test(raw)) {
    return 'AI 解析连接被中断 — 多半是中间代理在 60s 把长请求砍掉了。请把原文缩短到 1000 字以内,或分两段粘贴再重试。';
  }
  return raw;
}

function mergeProfile(
  base: BrandProfile, extracted: BrandProfile, mode: 'fill-blank' | 'overwrite',
): BrandProfile {
  const out: Record<string, unknown> = { ...base };
  for (const key of Object.keys(extracted) as (keyof BrandProfile)[]) {
    const newVal = extracted[key];
    if (isBlank(newVal)) continue;
    if (mode === 'overwrite' || isBlank(base[key])) {
      out[key] = newVal;
    }
  }
  return out as unknown as BrandProfile;
}

function countChanged(a: BrandProfile, b: BrandProfile): number {
  let n = 0;
  for (const key of Object.keys(a) as (keyof BrandProfile)[]) {
    const av = a[key], bv = b[key];
    if (Array.isArray(av) && Array.isArray(bv)) {
      if (av.length !== bv.length || av.some((x, i) => x !== bv[i])) n++;
    } else if (av !== bv) {
      n++;
    }
  }
  return n;
}
