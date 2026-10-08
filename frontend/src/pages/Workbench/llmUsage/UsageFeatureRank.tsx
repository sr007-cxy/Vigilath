// Model usage -- ranking by feature scenario. "Which feature burns the most" is the core question this page answers.
// Horizontal bars are drawn with div width percentages, no chart library (the bar count varies, and recharts' BarChart height doesn't auto-fit well).
import { Sparkline } from '../../Reports/components';
import { UiSelect } from '../../../components/ui/Select';
import { fmtCompact, fmtInt } from '../../../services/adminLlmUsageApi';
import type { UsageFeatureRank as RankData } from '../../../services/adminLlmUsageApi';

// Scenario name -> localized label. Unregistered scenarios show the key as-is (that usually means a new call site just got wired,
// or the context wasn't instrumented and fell into unknown -- both need to be seen, not quietly prettified).
export const FEATURE_LABEL: Record<string, string> = {
  unknown: '未归因(有出口没埋上下文)',
  'content_gen': '文章生成',
  'content_gen.article': '文章生成 · 正文',
  'content_gen.outline': '文章生成 · 提纲',
  'content_gen.review': '文章生成 · 评审',
  'content_gen.revise': '文章生成 · 修订',
  'content_gen.quality': '文章生成 · 精写',
  'full_diagnosis': '一键全面诊断',
  'solution': '优化方案生成',
  'source_strategy': '信源策略',
  'query_expand': '问句扩写',
  'profile_extract': '品牌资料智能填充',
  'media_select': '媒体自动选型',
  'keyword_heat': '搜索热度采集',
  'compliance.scan': '合规事前审核',
  'compliance.monitor': '合规事中监测',
  'platform_rules': '平台规则解析',
  'embedding': '向量化',
  'image_gen': '文章配图',
};

export function featureLabel(key: string): string {
  if (FEATURE_LABEL[key]) return FEATURE_LABEL[key];
  const base = key.split('.')[0];
  return FEATURE_LABEL[base] ? `${FEATURE_LABEL[base]} · ${key.slice(base.length + 1)}` : key;
}

export function UsageFeatureRank({
  data, orderBy, onOrderByChange, onPick,
}: {
  data: RankData | null;
  orderBy: string;
  onOrderByChange: (v: string) => void;
  onPick: (feature: string) => void;
}) {
  const items = data?.items || [];
  const maxTok = Math.max(1, ...items.map((i) => i.total_tokens));

  return (
    <div>
      <div className="flex items-center gap-2 mb-3 text-xs">
        <span className="text-muted">排序</span>
        <UiSelect
          value={orderBy}
          onChange={onOrderByChange}
          className="px-2 py-1 rounded-md"
          style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)',
                   color: 'var(--text-primary)', minWidth: 120 }}
          options={[
            { value: 'total_tokens', label: '按 token 用量' },
            { value: 'calls', label: '按调用次数' },
            { value: 'error_rate', label: '按失败率' },
          ]}
        />
        <span className="ml-auto text-muted">点场景名可筛下方明细</span>
      </div>

      {!items.length && <div className="text-xs text-muted py-8 text-center">暂无数据</div>}

      <div className="space-y-2">
        {items.map((it) => (
          <div key={it.feature} className="rounded-md px-3 py-2"
               style={{ background: 'var(--bg-tertiary)' }}>
            <div className="flex items-baseline gap-2 flex-wrap">
              <button type="button" onClick={() => onPick(it.feature)}
                      className="text-xs font-medium text-primary hover:underline text-left">
                {featureLabel(it.feature)}
              </button>
              {it.feature === 'unknown' && (
                <span className="text-[10px] px-1 rounded"
                      style={{ background: 'var(--warn-bg, var(--bg-card))', color: 'var(--warn-fg)' }}>
                  待补上下文
                </span>
              )}
              <span className="ml-auto text-xs tabular-nums text-primary">
                {fmtCompact(it.total_tokens)} token
              </span>
              <span className="text-[11px] tabular-nums text-muted w-12 text-right">
                {it.share_pct}%
              </span>
            </div>

            <div className="mt-1.5 h-1.5 rounded-full overflow-hidden"
                 style={{ background: 'var(--bg-card)' }}>
              <div className="h-full rounded-full"
                   style={{ width: `${Math.max(2, (it.total_tokens / maxTok) * 100)}%`,
                            background: 'var(--data-accent)' }} />
            </div>

            <div className="mt-1.5 flex items-center gap-3 text-[11px] text-muted flex-wrap">
              <span className="tabular-nums">调用 {fmtInt(it.calls)} 次</span>
              <span className="tabular-nums">
                失败率 <span style={{ color: it.error_rate_pct > 5 ? 'var(--warn-fg)' : undefined }}>
                  {it.error_rate_pct}%
                </span>
              </span>
              <span className="tabular-nums">均耗时 {(it.avg_latency_ms / 1000).toFixed(1)}s</span>
              {it.top_model && <span className="truncate max-w-[180px]">{it.top_model}</span>}
              <span className="ml-auto" style={{ width: 90 }}>
                <Sparkline values={it.sparkline} width={90} height={20} />
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
