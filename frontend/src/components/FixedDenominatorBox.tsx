// Answer denominator settings -- the admin config block on the profile page (not rendered on the customer side).
//
// Why this exists: when a project adds terms in batches, each batch raises the denominator, and the new terms haven't been optimized yet, so the overall exposure rate
// inevitably drops -- the ups and downs mix "new-term dilution" and "optimization taking effect", making the trend chart unreadable.
//
// The semantics is **pinning the denominator** (since 2026-08-31): denom = the set value, regardless of how many combinations this period actually covered,
// so columns and periods share the same scale. The cost is that when actual runs exceed the set value the numerator overshoots, **clamped to 100% at output**
// -- the cost is known and clamping was chosen (metric definition in docs/…/报告口径-应答分母托底.md §二.2).
// It used to have max(actual, floor) "lower bound" semantics, which made a whole column fall back to the real denominator as soon as actual runs exceeded it,
// so every column had a different denominator and the table was unreadable. Don't write the copy back to "take the larger of the two".
//
// Affects: overall exposure rate / TOP1 / TOP3 (same scale) / article citation rate.
// Content positive rate (denominator = hit count) and brand share of voice (denominator = total mentions) don't depend on run count and are unchanged.
// Only applies to weekly / monthly reports -- the floor value means "one period"; daily reports are same-day snapshots and always use the real denominator.

import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from './ui/toast';
import { topicAdminApi, type FixedDenominator } from '../services/topicAdminApi';

export function FixedDenominatorBox({ topicId, token, disabled }: {
  topicId: number; token: string; disabled?: boolean;
}) {
  const { t } = useTranslation();
  const [data, setData] = useState<FixedDenominator | null>(null);
  const [value, setValue] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    topicAdminApi.getFixedDenominator(topicId, token)
      .then(d => {
        if (cancelled) return;
        setData(d);
        setValue(d.fixed_response_count ? String(d.fixed_response_count) : '');
      })
      .catch(() => { /* non-admin or old backend: hide the whole block */ });
    return () => { cancelled = true; };
  }, [topicId, token]);

  if (!data) return null;

  const save = async (patch: { fixed_response_count?: number | null; mention_cat_scope?: boolean }) => {
    if (saving) return;
    setSaving(true);
    try {
      const d = await topicAdminApi.setAdminSettings(topicId, token, patch);
      setData(d);
      setValue(d.fixed_response_count ? String(d.fixed_response_count) : '');
      toast.success(t('dashboard.aiTelemetry.form.fixedDenomSaved',
        { defaultValue: '已保存,报告口径立即生效' }));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const parsed = Number(value.trim());
  const canSave = value.trim() === '' || (Number.isFinite(parsed) && parsed >= 0);

  return (
    <div className="rounded-md p-3 space-y-2"
         style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}>
      <div className="text-xs font-semibold text-secondary">
        {t('dashboard.aiTelemetry.form.fixedDenomTitle', { defaultValue: '分母设定值(仅管理员)' })}
      </div>
      <p className="text-xs text-muted">
        {t('dashboard.aiTelemetry.form.fixedDenomHint', {
          defaultValue: '填了就把分母钉死在该值上,与本期实跑覆盖多少个「问题 × 平台」组合无关 —— 期与期、平台与平台同尺可比。实跑长过该值时分子会顶穿,超出部分按 100% 显示。影响整体露出率、TOP1/TOP3、文章引用率;内容中正率、品牌声量度按应答条数算,不受影响。只在周报、月报生效,日报恒用真实分母。留空 = 全程用实际覆盖数。',
        })}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <input type="number" min={0} value={value} disabled={disabled || saving}
          onChange={e => setValue(e.target.value)}
          placeholder={t('dashboard.aiTelemetry.form.fixedDenomPlaceholder',
            { defaultValue: '留空 = 全程用实际覆盖数' })}
          className="px-2 py-1 text-sm rounded w-40"
          style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)',
                   color: 'var(--text-primary)' }} />
        <button type="button" disabled={disabled || saving || !canSave}
          onClick={() => save({ fixed_response_count: value.trim() === '' ? null : Math.round(parsed) })}
          className="px-3 py-1 text-xs rounded disabled:opacity-50"
          style={{ background: 'var(--accent-primary)', color: 'var(--accent-contrast)' }}>
          {saving
            ? t('common.saving', { defaultValue: '保存中…' })
            : t('common.save', { defaultValue: '保存' })}
        </button>
        {data.fixed_response_count ? (
          <button type="button" disabled={disabled || saving} onClick={() => save({ fixed_response_count: null })}
            className="px-3 py-1 text-xs rounded disabled:opacity-50"
            style={{ border: '1px solid var(--border-color)', color: 'var(--text-secondary)' }}>
            {t('dashboard.aiTelemetry.form.fixedDenomClear', { defaultValue: '清除设定' })}
          </button>
        ) : null}
      </div>
      <label className="flex items-center gap-2 text-xs text-secondary">
        <input type="checkbox" checked={data.mention_cat_scope} disabled={disabled || saving}
          onChange={e => save({ mention_cat_scope: e.target.checked })} />
        {t('dashboard.aiTelemetry.form.mentionCatScope', {
          defaultValue: '品牌声量度只算品类词命中(排除品牌词,与露出率同口径)',
        })}
      </label>
      <div className="text-xs text-muted rpt-num space-y-1">
        <div>
          {t('dashboard.aiTelemetry.form.fixedDenomRef', {
            defaultValue: '参考:当前 {{q}} 个提示词 × {{e}} 个模型 = 满覆盖 {{full}} 个「问题 × 平台」组合',
            q: data.query_count, e: data.engine_count, full: data.full_coverage,
          })}
        </div>
        {/* The exposure rate denominator is category-term answers (excluding brand terms/competitor comparison terms), the citation rate denominator is full term list answers --
            the two dimensions share one floor value, so check both full-coverage figures when entering the number */}
        {data.cat_full_coverage ? (
          <div>
            {t('dashboard.aiTelemetry.form.fixedDenomCatRef', {
              defaultValue: '其中品类词 {{q}} 个(露出率的分母口径)= 满覆盖 {{full}} 个组合;文章引用率的分母是全词表组合',
              q: data.cat_query_count, full: data.cat_full_coverage,
            })}
          </div>
        ) : null}
        {data.fixed_response_count && data.fixed_response_count > data.full_coverage ? (
          <div style={{ color: 'var(--warn-fg)' }}>
            {t('dashboard.aiTelemetry.form.fixedDenomAboveFull', {
              defaultValue: '设定值高于满覆盖组合数,当前词表跑满也到不了 100%',
            })}
          </div>
        ) : null}
        {/* Under pinned semantics, a set value below actual runs no longer means "floor not in effect" but "denominator pinned small, numerator overshoots" --
            the old copy saying "the report uses the actual real denominator" was exactly backwards; ops reading it would think the config hadn't taken effect */}
        {data.fixed_response_count && data.recent_cat_valid
          && data.fixed_response_count < data.recent_cat_valid ? (
          <div style={{ color: 'var(--warn-fg)' }}>
            {t('dashboard.aiTelemetry.form.fixedDenomBelowRecent', {
              defaultValue: '近 4 期实跑品类词组合约 {{n}} 个/期,高于设定值 —— 分母被钉小,露出率会被抬高,顶穿的部分按 100% 显示。要与实跑同尺,把设定值改成品类词满覆盖 {{full}}。',
              n: data.recent_cat_valid,
              full: data.cat_full_coverage || data.full_coverage,
            })}
          </div>
        ) : null}
      </div>
    </div>
  );
}
