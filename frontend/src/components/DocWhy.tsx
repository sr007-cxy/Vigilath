// Article detail "Strategy basis" -- what was injected into the prompt at generation time.
//
// Relation to the plan's three layers: the plan answers "why this article was scheduled" (platform/template/question/time),
// this answers "once scheduled, how it was actually written". Together they close the loop.
//
// ⚠️ Metric definition: everything is recomputed from the **current config**, not an archive from generation time -- platform red lines keep being appended by
// "learning from rejections", citation requirements change as the source strategy regenerates, competitor lists change with batch runs. So the backend's
// caveat must be shown as-is, so nobody thinks this is a snapshot from that time.
//
// Visibility: customers can open their own articles too, so "deliverable core" items like the raw platform red lines and each citation requirement
// go only to ops (staff_only_text); customers see a summarized count.

import { useState } from 'react';
import {
  adminContentReviewApi, type DocRationale,
} from '../services/adminContentReviewApi';
import { useAuth } from '../contexts/AuthContext';
import { COMBO_DIRECTIONS, COMBO_TYPES } from '../pages/Admin/comboOptions';

const labelOf = (opts: { value: string; label: string }[], v?: string | null) =>
  (v ? opts.find(o => o.value === v)?.label || v : '');

const fmt = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric',
                                                hour: '2-digit', minute: '2-digit' }) : '';

export function DocWhy({ docId }: { docId: number }) {
  const token = localStorage.getItem('token') || '';
  const { isStaff } = useAuth();
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<DocRationale | null>(null);
  const [loading, setLoading] = useState(false);

  // Fetch on demand: the API queries platform rules + aggregates competitors from recent batch runs, which shouldn't run for nothing in a list.
  // Put in the click handler rather than an effect -- the act of expanding is itself the trigger to fetch.
  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next && !data && !loading) {
      setLoading(true);
      adminContentReviewApi.getRationale(docId, token)
        .then(setData)
        .catch(() => setData(null))
        .finally(() => setLoading(false));
    }
  };

  return (
    <div className="mt-2 rounded-md"
         style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}>
      <button type="button" onClick={toggle}
              className="w-full text-left px-2.5 py-1.5 text-xs flex items-center gap-2"
              style={{ color: 'var(--text-secondary)' }}>
        <span>{open ? '▾' : '▸'}</span>
        <span>策略依据</span>
        <span className="text-[10px] text-muted ml-auto">{open ? '收起' : '展开'}</span>
      </button>
      {open && (
        <div className="px-2.5 pb-2.5">
          {loading && <div className="text-xs text-muted py-2">正在复算生成依据…</div>}
          {!loading && !data && (
            <div className="text-xs text-muted py-2">未能获取本篇的生成依据。</div>
          )}
          {!loading && data && <Body data={data} isStaff={isStaff} />}
        </div>
      )}
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="py-1.5 text-xs" style={{ borderTop: '1px solid var(--border-color)' }}>
      <span className="text-muted">{label}:</span>{' '}
      <span style={{ color: 'var(--text-secondary)' }}>{children}</span>
    </div>
  );
}

function Body({ data, isStaff }: { data: DocRationale; isStaff: boolean }) {
  const blocks = data.blocks || [];
  const get = (k: string) => blocks.find(b => b.key === k);
  const kw = get('keyword_seo');
  const cover = get('cover_queries');
  const rules = get('platform_rules');
  const guide = get('citation_playbook');
  const gaps = get('query_gap_plan');
  const comp = get('competitors');
  const refs = get('style_refs');
  const contacts = get('contacts');
  const materials = get('materials');
  const dir = labelOf(COMBO_DIRECTIONS, data.combo?.creation_direction);
  const typ = labelOf(COMBO_TYPES, data.combo?.copywriting_type);

  if (blocks.length === 0 && !data.template) {
    return <div className="text-xs text-muted py-2">本篇暂无可还原的生成依据。</div>;
  }

  return (
    <div>
      {data.template && (
        <Row label="采用模板">
          {data.template.name}
          {data.template.kind ? `(${data.template.kind})` : ''}
          {data.plan_row?.seq != null && ` —— 由发文计划第 ${data.plan_row.seq + 1} 行指定`}
        </Row>
      )}
      {(dir || typ) && (
        <Row label="写作方向">
          {[dir, typ].filter(Boolean).join(' · ')}
          {data.combo?.length_hint && ` · 篇幅 ${data.combo.length_hint}`}
          {data.combo?.source === 'plan_row' ? '(发文计划行指定)' : '(品牌画像默认值)'}
        </Row>
      )}
      {cover && (cover.items?.length ?? 0) > 0 && (
        <Row label="覆盖的提示词">
          {(cover.items as string[]).join('、')}
          <span className="text-muted">
            {' '}—— 要求融入行文逐一解答,不得罗列为问题清单
          </span>
        </Row>
      )}
      {kw && (
        <Row label="关键词投放">
          核心词「{kw.primary}」标题、开头、结尾各 1 次,全文 3-5 次
          {(kw.secondary?.length ?? 0) > 0 && `;另有 ${kw.secondary!.length} 个次要词各 1-2 次`}
        </Row>
      )}
      {rules && (
        <Row label={`${data.platform} 审核红线`}>
          共 {rules.count} 条,已全部注入
          {rules.updated_at && <span className="text-muted">(更新于 {fmt(rules.updated_at)})</span>}
          {isStaff && rules.text && (
            <details className="mt-1">
              <summary className="cursor-pointer text-[10px] text-muted">查看原文</summary>
              <pre className="text-[10px] whitespace-pre-wrap mt-1 max-h-40 overflow-y-auto"
                   style={{ color: 'var(--text-muted)' }}>{rules.text}</pre>
            </details>
          )}
        </Row>
      )}
      {guide && (
        <Row label="引用友好度要求">
          共 {guide.count} 条,来自信源策略分析(依据各引擎实际偏好引用的内容特征拆解)
          {isStaff && (guide.items?.length ?? 0) > 0 && (
            <details className="mt-1">
              <summary className="cursor-pointer text-[10px] text-muted">查看明细</summary>
              <ul className="mt-1 space-y-0.5">
                {(guide.items as string[]).map((g, i) => (
                  <li key={i} className="text-[10px] text-muted">· {g}</li>
                ))}
              </ul>
            </details>
          )}
        </Row>
      )}
      {gaps && (gaps.items?.length ?? 0) > 0 && (
        <Row label="问题级引用缺口方案">
          其中 {(gaps.items as unknown[]).length} 条针对本篇需覆盖的问题制定,优先注入
          <ul className="mt-1 space-y-0.5">
            {(gaps.items as { query: string; gap_plan: string }[]).map((g, i) => (
              <li key={i} className="text-[10px] text-muted">· 「{g.query}」:{g.gap_plan}</li>
            ))}
          </ul>
        </Row>
      )}
      {contacts && (
        <Row label="允许出现的联系方式">
          {(contacts.count ?? 0) > 0
            ? (contacts.items as string[]).join(' · ')
            : '无 —— 全文不会出现电话 / 网址 / 微信 / 邮箱'}
          <span className="text-muted"> —— {contacts.text}</span>
        </Row>
      )}
      {materials && (materials.count ?? 0) > 0 && (
        <Row label="引用的自有资料">
          命中 {materials.count} 段({materials.text})
          <ul className="mt-1 space-y-0.5">
            {(materials.items as { title: string; source: string }[]).map((m, i) => (
              <li key={i} className="text-[10px] text-muted">
                · {m.title}{m.source ? ` — ${m.source}` : ''}
              </li>
            ))}
          </ul>
        </Row>
      )}
      {refs && (
        <Row label="文风参考">
          {refs.count} 篇自有旧稿
          {(refs.cited_count ?? 0) > 0
            ? `,其中 ${refs.cited_count} 篇已被 AI 实际引用,可作为客观正例`
            : ''}
        </Row>
      )}
      {comp && (
        <Row label="提及的同行品牌">
          {comp.text} <span className="text-muted">—— 取自跑批中 AI 实际提及的品牌,非模型杜撰</span>
        </Row>
      )}
      {data.quality?.score != null && (
        <Row label="成稿自评">{data.quality.score} 分</Row>
      )}
      {data.llm_model && isStaff && <Row label="生成模型">{data.llm_model}</Row>}

      {data.caveat && (
        <p className="text-[10px] text-muted mt-2 pt-2 leading-relaxed"
           style={{ borderTop: '1px solid var(--border-color)' }}>
          ⚠️ {data.caveat}
          {data.generated_at && ` 本篇生成于 ${fmt(data.generated_at)}。`}
        </p>
      )}
    </div>
  );
}
