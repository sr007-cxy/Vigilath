// Brand profile form -- shared by TopicEditor and /dashboard/topics/:id/profile.
//
// Field definitions in BrandProfile in services/aiTelemetryApi.ts.
// Required-field validation is controlled by the caller passing requiredKeys (admin editor vs user submission-for-review differ).
//
// 2026-05-18:
//   - The 5 sections changed from vertical stacking to top tabs, shortening the page
//   - Aliases (target_aliases) moved from a separate outer section into the "Basic identity" tab;
//     callers may optionally pass `aliases` + `onAliasesChange` (only TopicEditor does)

import { useState } from 'react';
import type { BrandProduct, BrandProfile } from '../services/aiTelemetryApi';
import { EMPTY_BRAND_PRODUCT } from '../services/aiTelemetryApi';

// Core fields required when the user submits for review (aligned with backend PROFILE_REQUIRED_FIELDS)
// Section 2, content creation directions (creation_directions / copywriting_types / target_platforms /
// content_tones / content_redlines), was removed entirely -- these are decided at the content review / placement strategy stage,
// not forced in the profile form. BrandProfile schema fields are kept (old data stays intact),
// and content_generator's prompt assembly skips them if empty.
// From 2026-05-17 -- only the 6 fields in "Section 1, Basic identity" are required.
// 2026-05-27: core_business_lines is no longer required -- in practice many cases describe the business line in one sentence
// in industry, so leaving it empty is allowed to proceed. The other 5 remain required.
// Other modules are "asset library" in nature: filling more is encouraged but not enforced; the LLM uses whatever it gets.
export const PROFILE_REQUIRED_FIELDS: (keyof BrandProfile)[] = [
  'profile_name', 'company_full_name', 'company_short_name',
  'industry', 'service_geo',
];

export function isProfileFieldFilled(profile: BrandProfile, key: keyof BrandProfile): boolean {
  const v = profile[key] as string | string[];
  if (Array.isArray(v)) return v.length > 0;
  return !!(v && v.trim());
}

export function profileMissingFields(profile: BrandProfile, required = PROFILE_REQUIRED_FIELDS): (keyof BrandProfile)[] {
  return required.filter(k => !isProfileFieldFilled(profile, k));
}

// Entity type -- single choice, drives query expansion style (same keys as backend query_expander.ENTITY_TYPE_HINTS).
// Auto-detected by AI during profile extraction, user-editable; empty = undetermined, expansion uses the generic template.
const ENTITY_TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: 'service_tool',  label: '服务业' },
  { value: 'manufacturer',  label: '制造业' },
  { value: 'brand_owner',   label: '品牌方' },
  { value: 'other',         label: '其他' },
];

const COMPANY_SIZE_OPTIONS: { value: string; label: string }[] = [
  { value: '1-10 名员工', label: '1-10 名员工' },
  { value: '11-100 名员工', label: '11-100 名员工' },
  { value: '101-500 名员工', label: '101-500 名员工' },
  { value: '501-1000 名员工', label: '501-1000 名员工' },
  { value: '1001+ 名员工', label: '1001+ 名员工' },
];

type SectionKey = 'basic' | 'subject' | 'service' | 'story' | 'extra' | 'creation';

const SECTIONS: { key: SectionKey; label: string }[] = [
  { key: 'basic',    label: '一、基础标识' },
  { key: 'subject',  label: '二、品牌主体信息' },
  { key: 'service',  label: '三、产品 / 服务核心信息' },
  { key: 'story',    label: '四、品牌故事与情感素材' },
  { key: 'extra',    label: '五、补充素材与创作边界' },
  { key: 'creation', label: '六、内容创作偏好' },
];

// 2026-05-28 -- candidate values for creation direction / copywriting type (same keys as backend content_generator.DIRECTION_HINTS /
// TYPE_HINTS). Multi-select: select several -> the same query produces that many drafts in different styles.
const CREATION_DIRECTION_OPTIONS: { value: string; label: string; desc: string }[] = [
  { value: 'industry_insight',  label: '行业洞察',  desc: '趋势 / 市场数据 / 头部玩家' },
  { value: 'case_story',        label: '案例分享',  desc: '真实项目复盘' },
  { value: 'how_to_guide',      label: '实操指南',  desc: '步骤化 / 怎么做' },
  { value: 'trend_forecast',    label: '趋势预测',  desc: '行业前瞻 + 应对' },
  { value: 'product_review',    label: '产品评测',  desc: '横向对比 / 优劣矩阵' },
  { value: 'customer_story',    label: '客户故事',  desc: '第一人称 + 量化成果' },
  { value: 'faq',               label: 'FAQ 答疑', desc: '常见问题列表' },
];

const COPYWRITING_TYPE_OPTIONS: { value: string; label: string; desc: string }[] = [
  { value: 'long_form',          label: '深度长文',     desc: '1500-2500 字 · 公众号 / 知乎' },
  { value: 'medium_post',        label: '中等图文',     desc: '500-1500 字 · 公众号中等' },
  { value: 'short_social',       label: '短社媒文案',   desc: '<500 字 · 小红书 / 微博' },
  { value: 'video_script_long',  label: '长视频脚本',   desc: '5-8 分钟口播稿' },
  { value: 'video_script_short', label: '短视频文案',   desc: '30-60 秒口播稿' },
  { value: 'faq_list',           label: 'FAQ 列表',     desc: '8-12 条 Q&A' },
];

interface ProfileFormProps {
  profile: BrandProfile;
  onChange: (p: BrandProfile) => void;
  readOnly?: boolean;
  requiredKeys?: (keyof BrandProfile)[];
  // Aliases (optional) -- only used by TopicEditor; if not passed, the alias input isn't rendered in "Basic identity".
  // Text (comma / newline separated) is used because that's how the outer layer persists it, reusing the semantics directly;
  // the parent component handles split -> string[] itself.
  aliasesText?: string;
  onAliasesTextChange?: (v: string) => void;
  aliasesCount?: number;
  aliasesLabel?: string;
  aliasesPlaceholder?: string;
  aliasesHint?: string;
}

export function BrandProfileForm({
  profile, onChange, readOnly = false, requiredKeys = PROFILE_REQUIRED_FIELDS,
  aliasesText, onAliasesTextChange, aliasesCount,
  aliasesLabel, aliasesPlaceholder, aliasesHint,
}: ProfileFormProps) {
  const req = new Set(requiredKeys);
  const setField = <K extends keyof BrandProfile>(key: K, value: BrandProfile[K]) => {
    onChange({ ...profile, [key]: value });
  };
  const [tab, setTab] = useState<SectionKey>('basic');
  const showAliases = typeof aliasesText === 'string' && typeof onAliasesTextChange === 'function';

  return (
    <div className="space-y-3">
      <div className="flex gap-1 border-b overflow-x-auto"
           style={{ borderColor: 'var(--border-color)' }}>
        {SECTIONS.map(s => (
          <button key={s.key} type="button" onClick={() => setTab(s.key)}
                  className="px-3 py-2 text-sm whitespace-nowrap -mb-px"
                  style={{
                    borderBottom: tab === s.key ? '2px solid var(--accent-primary)' : '2px solid transparent',
                    color: tab === s.key ? 'var(--accent-primary)' : 'var(--text-secondary)',
                  }}>
            {s.label}
          </button>
        ))}
      </div>

      <section className="rounded-md p-4"
               style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
        <div className="space-y-3">
          {tab === 'basic' && (
            <>
              <Pair>
                <TextRow label="名称" required={req.has('profile_name')} readOnly={readOnly}
                         value={profile.profile_name} onChange={v => setField('profile_name', v)} />
                <TextRow label="公司 / 品牌全称" required={req.has('company_full_name')} readOnly={readOnly}
                         value={profile.company_full_name} onChange={v => setField('company_full_name', v)} />
              </Pair>
              <Pair>
                <TextRow label="公司 / 品牌简称" required={req.has('company_short_name')} readOnly={readOnly}
                         value={profile.company_short_name} onChange={v => setField('company_short_name', v)} />
                <TextRow label="所属行业 / 赛道" required={req.has('industry')} readOnly={readOnly}
                         value={profile.industry} onChange={v => setField('industry', v)} />
              </Pair>
              <SegmentedRow
                label="主体类型"
                aiBadge
                options={ENTITY_TYPE_OPTIONS}
                value={profile.entity_type}
                onChange={v => setField('entity_type', v)}
                readOnly={readOnly}
              />
              <Pair>
                <TextRow label="服务地域" required={req.has('service_geo')} readOnly={readOnly}
                         value={profile.service_geo} onChange={v => setField('service_geo', v)} />
                <TextRow label="品牌官网(选填)" required={false} readOnly={readOnly}
                         value={profile.website || ''} onChange={v => setField('website', v)} />
              </Pair>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                <TextRow label="地点" required={false} readOnly={readOnly}
                         value={profile.ctrip_location?.name || ''}
                         placeholder="例如：西湖风景名胜区"
                         onChange={v => setField('ctrip_location', {
                           ...profile.ctrip_location,
                           name: v,
                         })} />
                <TextRow label="城市" required={false} readOnly={readOnly}
                         value={profile.ctrip_location?.city || ''}
                         placeholder="例如：杭州"
                         onChange={v => setField('ctrip_location', {
                           name: profile.ctrip_location?.name || '',
                           ...profile.ctrip_location,
                           city: v,
                         })} />
                <TextRow label="详细地址" required={false} readOnly={readOnly}
                         value={profile.ctrip_location?.address || ''}
                         placeholder="例如：西湖区龙井路 1 号"
                         onChange={v => setField('ctrip_location', {
                           name: profile.ctrip_location?.name || '',
                           ...profile.ctrip_location,
                           address: v,
                         })} />
              </div>
              {/* Contact info: only allowed into articles if filled in. When all are empty, generation forces the entire text to contain no
                  phone / URL / WeChat / email -- previously there were no fields here, so the model had to make one up. */}
              <Pair>
                <TextRow label="联系电话(选填)" required={false} readOnly={readOnly}
                         value={profile.contact_phone || ''}
                         onChange={v => setField('contact_phone', v)} />
                <TextRow label="微信 / 公众号(选填)" required={false} readOnly={readOnly}
                         value={profile.contact_wechat || ''}
                         onChange={v => setField('contact_wechat', v)} />
              </Pair>
              <Pair>
                <TextRow label="联系邮箱(选填)" required={false} readOnly={readOnly}
                         value={profile.contact_email || ''}
                         onChange={v => setField('contact_email', v)} />
                <div className="text-[11px] text-muted self-end pb-2 leading-relaxed">
                  填了的联系方式才允许出现在生成的文章里;全部留空时,文章中不会
                  出现任何电话、网址、微信或邮箱。
                </div>
              </Pair>
              <ParagraphRow label="核心业务线" required={req.has('core_business_lines')} readOnly={readOnly}
                            value={profile.core_business_lines} onChange={v => setField('core_business_lines', v)} />
              {showAliases && (
                <div>
                  <Label text={aliasesLabel || '别名 / 简称(逗号分隔,最多 10 个)'} required={false} />
                  <input type="text" disabled={readOnly}
                         value={aliasesText}
                         onChange={e => onAliasesTextChange!(e.target.value)}
                         placeholder={aliasesPlaceholder}
                         className="w-full text-sm px-3 py-2 rounded-md"
                         style={{
                           background: 'var(--bg-input)', color: 'var(--text-primary)',
                           border: '1px solid var(--border-color)',
                           opacity: readOnly ? 0.6 : 1,
                         }} />
                  {(aliasesHint || typeof aliasesCount === 'number') && (
                    <span className="block mt-1 text-xs text-muted">
                      {aliasesHint || `已识别 ${aliasesCount} 个别名`}
                    </span>
                  )}
                </div>
              )}
            </>
          )}

          {tab === 'subject' && (
            <>
              <CompanySizeRow
                value={profile.team_size}
                onChange={v => setField('team_size', v)}
                readOnly={readOnly}
              />
              <Pair>
                <AgencyCheckboxRow
                  checked={Boolean(profile.is_agency)}
                  onChange={v => setField('is_agency', v)}
                  readOnly={readOnly}
                />
                <TextRow label="成立时间 / 从业年限" required={req.has('founded_year')} readOnly={readOnly}
                         value={profile.founded_year} onChange={v => setField('founded_year', v)} />
              </Pair>
              <ParagraphRow label="核心荣誉 / 背书资质" required={req.has('core_credentials')} readOnly={readOnly}
                            value={profile.core_credentials} onChange={v => setField('core_credentials', v)} />
              <TagsRow label="品牌差异化标签(3-5 个)" required={req.has('brand_diff_tags')} readOnly={readOnly}
                       value={profile.brand_diff_tags} onChange={v => setField('brand_diff_tags', v)} />
            </>
          )}

          {tab === 'service' && (
            <>
              <TextAreaRow label="核心服务概述" required={req.has('core_service_overview')} readOnly={readOnly}
                           value={profile.core_service_overview} onChange={v => setField('core_service_overview', v)} />
              <ParagraphRow label="服务核心特点" required={req.has('service_features')} readOnly={readOnly}
                            value={profile.service_features} onChange={v => setField('service_features', v)} />
              <ParagraphRow label="服务关键流程 / 环节" required={req.has('service_process')} readOnly={readOnly}
                            value={profile.service_process} onChange={v => setField('service_process', v)} />
              <ParagraphRow label="服务覆盖场景 / 客户类型" required={req.has('target_scenarios')} readOnly={readOnly}
                            value={profile.target_scenarios} onChange={v => setField('target_scenarios', v)} />
              <TagsRow label="服务交付保障(选填)" required={false} readOnly={readOnly}
                       value={profile.service_guarantees} onChange={v => setField('service_guarantees', v)} />
              <ProductsRow readOnly={readOnly}
                           value={profile.products || []} onChange={v => setField('products', v)} />
            </>
          )}

          {tab === 'story' && (
            <>
              <TextAreaRow label="品牌故事 / 成立初衷" required={req.has('brand_story')} readOnly={readOnly}
                           value={profile.brand_story} onChange={v => setField('brand_story', v)} />
              <TextAreaRow label="核心人物故事(选填)" required={false} readOnly={readOnly}
                           value={profile.key_person_story} onChange={v => setField('key_person_story', v)} />
              <ParagraphRow label="典型案例 / 客户故事(脱敏)(选填)" required={false} readOnly={readOnly}
                            value={profile.case_stories} onChange={v => setField('case_stories', v)} />
              <TextAreaRow label="品牌价值观 / 服务理念" required={req.has('brand_values')} readOnly={readOnly}
                           value={profile.brand_values} onChange={v => setField('brand_values', v)} />
            </>
          )}

          {tab === 'extra' && (
            <>
              <Pair>
                <TextRow label="品牌 Slogan / 宣传语(选填)" required={false} readOnly={readOnly}
                         value={profile.brand_slogan} onChange={v => setField('brand_slogan', v)} />
                <span />
              </Pair>
              <TagsRow label="可提供的素材类型(选填)" required={false} readOnly={readOnly}
                       value={profile.available_materials} onChange={v => setField('available_materials', v)} />
              <TextAreaRow label="本次内容想传递的核心信息" required={req.has('core_message')} readOnly={readOnly}
                           value={profile.core_message} onChange={v => setField('core_message', v)} />
              <TextAreaRow label="其他补充说明(选填)" required={false} readOnly={readOnly}
                           value={profile.extra_notes} onChange={v => setField('extra_notes', v)} />
            </>
          )}
          {tab === 'creation' && (
            <>
              <div className="text-xs text-muted -mt-1 mb-2 leading-relaxed"
                   style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)',
                            padding: '8px 12px', borderRadius: 6 }}>
                选了几个「创作方向」× 几个「文案类型」,系统就为每条提示词
                <strong style={{ color: 'var(--accent-primary)' }}> 出几篇风格不同的稿件 </strong>。
                例如:勾「案例分享 + 实操指南」× 「深度长文 + 短社媒」= 同一问题 4 篇稿。
                上限 9 篇/问题(超过自动截断);两边都不选 → 沿用旧行为(每问题 1 篇)。
              </div>
              <OptionsMultiRow
                label="创作方向(多选)"
                options={CREATION_DIRECTION_OPTIONS}
                value={profile.creation_directions}
                onChange={v => setField('creation_directions', v)}
                readOnly={readOnly}
              />
              <OptionsMultiRow
                label="文案类型(多选)"
                options={COPYWRITING_TYPE_OPTIONS}
                value={profile.copywriting_types}
                onChange={v => setField('copywriting_types', v)}
                readOnly={readOnly}
              />
            </>
          )}
        </div>
      </section>
    </div>
  );
}

function Pair({ children }: { children: React.ReactNode }) {
  // Two-column grid at md+, single column at sm. Fill empty slots with a <span /> placeholder
  return <div className="grid grid-cols-1 md:grid-cols-2 gap-3">{children}</div>;
}

function Label({ text, required }: { text: string; required: boolean }) {
  return (
    <label className="block text-xs text-secondary mb-1">
      {text}{required && <span style={{ color: 'var(--danger-fg)' }}> *</span>}
    </label>
  );
}

interface TextProps {
  label: string; required: boolean; readOnly: boolean;
  value: string; onChange: (v: string) => void; placeholder?: string;
}

function TextRow({ label, required, readOnly, value, onChange, placeholder }: TextProps) {
  return (
    <div>
      <Label text={label} required={required} />
      <input type="text" disabled={readOnly}
             className="w-full text-sm px-3 py-2 rounded-md"
             style={{
               background: 'var(--bg-input)', color: 'var(--text-primary)',
               border: '1px solid var(--border-color)',
               opacity: readOnly ? 0.6 : 1,
             }}
             value={value} placeholder={placeholder}
             onChange={e => onChange(e.target.value)} />
    </div>
  );
}

function CompanySizeRow({ value, onChange, readOnly }: { value: string; onChange: (v: string) => void; readOnly: boolean }) {
  const isPreset = COMPANY_SIZE_OPTIONS.some(opt => opt.value === value);
  return (
    <div>
      <Label text="公司 / 团队规模(选填)" required={false} />
      <div className="flex flex-wrap gap-2">
        {COMPANY_SIZE_OPTIONS.map(opt => {
          const active = value === opt.value;
          return (
            <button
              key={opt.value}
              type="button"
              disabled={readOnly}
              onClick={() => onChange(active ? '' : opt.value)}
              className="px-3 py-2 rounded-md text-sm border"
              style={{
                background: active ? 'var(--accent-primary)' : 'var(--bg-card)',
                borderColor: active ? 'var(--accent-primary)' : 'var(--border-color)',
                color: active ? '#fff' : 'var(--text-primary)',
                fontWeight: active ? 600 : 400,
                cursor: readOnly ? 'not-allowed' : 'pointer',
                opacity: readOnly ? 0.6 : 1,
              }}
            >
              {active && <span className="mr-1">●</span>}{opt.label}
            </button>
          );
        })}
      </div>
      <input
        type="text"
        disabled={readOnly}
        value={isPreset ? '' : value}
        onChange={e => onChange(e.target.value)}
        placeholder="其他规模 / 专业能力说明(可选)"
        className="w-full text-sm px-3 py-2 rounded-md mt-2"
        style={{
          background: 'var(--bg-input)', color: 'var(--text-primary)',
          border: '1px solid var(--border-color)',
          opacity: readOnly ? 0.6 : 1,
        }}
      />
      <span className="block mt-1 text-xs text-muted">点击固定规模更快;已有自由文本会保留在下方自定义输入中。</span>
    </div>
  );
}

function AgencyCheckboxRow({ checked, onChange, readOnly }: { checked: boolean; onChange: (v: boolean) => void; readOnly: boolean }) {
  return (
    <div>
      <Label text="您是代理机构吗?" required={false} />
      <button
        type="button"
        disabled={readOnly}
        onClick={() => onChange(!checked)}
        className="w-full text-left px-3 py-2 rounded-md border"
        style={{
          background: checked ? 'var(--bg-tertiary)' : 'var(--bg-card)',
          borderColor: checked ? 'var(--accent-primary)' : 'var(--border-color)',
          color: 'var(--text-primary)',
          cursor: readOnly ? 'not-allowed' : 'pointer',
          opacity: readOnly ? 0.6 : 1,
        }}
      >
        <div className="text-sm font-medium" style={{ color: checked ? 'var(--accent-primary)' : 'var(--text-primary)' }}>
          {checked ? '☑' : '☐'} 是的,我是代理机构
        </div>
        <div className="text-[11px] text-muted mt-0.5">用于后续内容口径和客户场景判断,不会打断当前问答流程。</div>
      </button>
    </div>
  );
}

function TextAreaRow({ label, required, readOnly, value, onChange }: TextProps) {
  return (
    <div>
      <Label text={label} required={required} />
      <textarea disabled={readOnly}
                rows={3}
                className="w-full text-sm px-3 py-2 rounded-md"
                style={{
                  background: 'var(--bg-input)', color: 'var(--text-primary)',
                  border: '1px solid var(--border-color)',
                  opacity: readOnly ? 0.6 : 1,
                }}
                value={value}
                onChange={e => onChange(e.target.value)} />
    </div>
  );
}

// Whole-text editing, still stored as string[] underneath (keeps schema compatibility).
// On load: multi-item arrays are joined with newlines for display;
// On save: the whole text is trimmed and written back as the sole element of string[], empty stores [].
// This way downstream LLM prompt assembly (joining the array line by line) is unchanged; only the input experience changes
// from "users manually maintain multiple items" to "one block of text".
interface ParagraphProps {
  label: string; required: boolean; readOnly: boolean;
  value: string[]; onChange: (v: string[]) => void; rows?: number;
}

function ParagraphRow({ label, required, readOnly, value, onChange, rows = 4 }: ParagraphProps) {
  const text = value.join('\n');
  return (
    <div>
      <Label text={label} required={required} />
      <textarea disabled={readOnly}
                rows={rows}
                className="w-full text-sm px-3 py-2 rounded-md"
                style={{
                  background: 'var(--bg-input)', color: 'var(--text-primary)',
                  border: '1px solid var(--border-color)',
                  opacity: readOnly ? 0.6 : 1,
                }}
                value={text}
                onChange={e => {
                  const v = e.target.value;
                  onChange(v.trim() ? [v] : []);
                }} />
    </div>
  );
}

interface TagsProps {
  label: string; required: boolean; readOnly: boolean;
  value: string[]; onChange: (v: string[]) => void; placeholder?: string;
}

// 2026-05-28 -- multi-select chip row with a fixed enum of options (creation direction / copywriting type); unlike TagsRow's
// free input, here you "click a chip to toggle". Each chip has a short description below.
function OptionsMultiRow({
  label, options, value, onChange, readOnly,
}: {
  label: string;
  options: { value: string; label: string; desc: string }[];
  value: string[];
  onChange: (v: string[]) => void;
  readOnly: boolean;
}) {
  const set = new Set(value || []);
  const toggle = (v: string) => {
    const next = new Set(set);
    if (next.has(v)) next.delete(v); else next.add(v);
    onChange(Array.from(next));
  };
  return (
    <div>
      <Label text={label} required={false} />
      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-2">
        {options.map(opt => {
          const checked = set.has(opt.value);
          return (
            <button
              key={opt.value}
              type="button"
              disabled={readOnly}
              onClick={() => toggle(opt.value)}
              className="text-left px-3 py-2 rounded-md border"
              style={{
                background: checked ? 'var(--bg-tertiary)' : 'var(--bg-card)',
                borderColor: checked ? 'var(--accent-primary)' : 'var(--border-color)',
                color: 'var(--text-primary)',
                cursor: readOnly ? 'not-allowed' : 'pointer',
                opacity: readOnly ? 0.6 : 1,
              }}
            >
              <div className="text-sm font-medium" style={{ color: checked ? 'var(--accent-primary)' : 'var(--text-primary)' }}>
                {checked && <span className="mr-1">✓</span>}{opt.label}
              </div>
              <div className="text-[11px] text-muted mt-0.5">{opt.desc}</div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

// Single-choice segmented buttons -- fixed enum options, only one selectable (clicking the same one again clears it).
// With a ✨AI-judged badge + dynamic hint: when selected, explains the current judgment can be changed; when not, explains it affects query expansion direction.
function SegmentedRow({
  label, options, value, onChange, readOnly, aiBadge = false,
}: {
  label: string;
  options: { value: string; label: string }[];
  value: string;
  onChange: (v: string) => void;
  readOnly: boolean;
  aiBadge?: boolean;
}) {
  const current = options.find(o => o.value === value);
  const hint = current
    ? `已按资料自动判为「${current.label}」,不对可直接改`
    : '按资料自动判断;影响扩展词方向(服务业→功能/价格/集成 · 制造业→厂家/参数/OEM)';
  return (
    <div>
      <div className="flex items-center gap-2 mb-1">
        <label className="block text-xs text-secondary">{label}</label>
        {aiBadge && (
          <span className="text-[10px] px-1.5 py-0.5 rounded-full"
                style={{ background: 'rgba(139,92,246,0.15)', color: '#8b5cf6' }}>
            ✨AI 判断
          </span>
        )}
      </div>
      <div className="flex flex-wrap gap-2">
        {options.map(opt => {
          const active = value === opt.value;
          return (
            <button
              key={opt.value}
              type="button"
              disabled={readOnly}
              onClick={() => onChange(active ? '' : opt.value)}
              className="px-3 py-1.5 rounded-md text-sm border"
              style={{
                background: active ? 'var(--accent-primary)' : 'var(--bg-card)',
                borderColor: active ? 'var(--accent-primary)' : 'var(--border-color)',
                color: active ? '#fff' : 'var(--text-primary)',
                fontWeight: active ? 600 : 400,
                cursor: readOnly ? 'not-allowed' : 'pointer',
                opacity: readOnly ? 0.6 : 1,
              }}
            >
              {active && <span className="mr-1">●</span>}{opt.label}
            </button>
          );
        })}
      </div>
      <span className="block mt-1 text-xs text-muted">{hint}</span>
    </div>
  );
}

// Product / model list (added 2026-09-22). One product per row: name / aliases / category / selling points / audience / featured.
// Featured is single-choice -- turning one on turns the others off (draft generation's "prioritize embedding" only honors one; multiple featured makes no sense).
// Product names / aliases are treated by the backend as brand terms (hit detection + metric term exclusion + query expansion classification), so prompt users to enter model names,
// not generic category words as product names.
function ProductsRow({ readOnly, value, onChange }: {
  readOnly: boolean; value: BrandProduct[]; onChange: (v: BrandProduct[]) => void;
}) {
  const update = (i: number, patch: Partial<BrandProduct>) =>
    onChange(value.map((p, idx) => (idx === i ? { ...p, ...patch } : p)));
  const setFeatured = (i: number, on: boolean) =>
    onChange(value.map((p, idx) => ({ ...p, is_featured: on ? idx === i : (idx === i ? false : p.is_featured) })));
  const remove = (i: number) => onChange(value.filter((_, idx) => idx !== i));
  const add = () => onChange([...value, { ...EMPTY_BRAND_PRODUCT }]);
  const inputStyle = {
    background: 'var(--bg-input)', color: 'var(--text-primary)',
    border: '1px solid var(--border-color)', opacity: readOnly ? 0.6 : 1,
  } as const;
  return (
    <div>
      <Label text="产品 / 型号清单(选填)" required={false} />
      <p className="text-xs mb-2" style={{ color: 'var(--text-muted)' }}>
        消费品牌按具体产品填:产品名填型号(如「{'<brand-e>'}手柄」),品类填通用名(如「游戏手柄」)。
        产品名和别名会被当作品牌词识别命中;种子提示词是产品名时,扩词按其品类造词、不再把产品当成别家品牌。
        标「主推」的产品生稿时优先植入,并在建主题时推荐作种子。
      </p>
      <div className="space-y-2">
        {value.map((p, i) => (
          <div key={i} className="rounded-md p-3 space-y-2"
               style={{ border: '1px solid var(--border-color)', background: 'var(--bg-tertiary)' }}>
            <div className="flex items-center gap-2">
              <input type="text" value={p.name} disabled={readOnly} placeholder="产品 / 型号名 *"
                     onChange={e => update(i, { name: e.target.value })}
                     className="flex-1 text-sm px-3 py-1.5 rounded-md" style={inputStyle} />
              <input type="text" value={p.category} disabled={readOnly} placeholder="品类通用名(如 游戏手柄)"
                     onChange={e => update(i, { category: e.target.value })}
                     className="flex-1 text-sm px-3 py-1.5 rounded-md" style={inputStyle} />
              <label className="flex items-center gap-1 text-xs whitespace-nowrap cursor-pointer"
                     style={{ color: p.is_featured ? 'var(--accent-primary)' : 'var(--text-secondary)' }}>
                <input type="checkbox" checked={Boolean(p.is_featured)} disabled={readOnly}
                       onChange={e => setFeatured(i, e.target.checked)} />
                主推
              </label>
              {!readOnly && (
                <button type="button" onClick={() => remove(i)}
                        className="text-xs px-2 py-1 rounded-md"
                        style={{ color: 'var(--text-muted)', border: '1px solid var(--border-color)' }}>删除</button>
              )}
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
              <TagsRow label="别名 / 型号变体" required={false} readOnly={readOnly}
                       value={p.aliases || []} onChange={v => update(i, { aliases: v })} placeholder="如 <brand-e>、Nova" />
              <TagsRow label="核心卖点" required={false} readOnly={readOnly}
                       value={p.selling_points || []} onChange={v => update(i, { selling_points: v })} placeholder="如 霍尔扳机" />
              <TagsRow label="目标人群 / 场景" required={false} readOnly={readOnly}
                       value={p.target_users || []} onChange={v => update(i, { target_users: v })} placeholder="如 主机玩家" />
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
              <input type="text" value={p.launch_date} disabled={readOnly} placeholder="上市时间(选填)"
                     onChange={e => update(i, { launch_date: e.target.value })}
                     className="text-sm px-3 py-1.5 rounded-md" style={inputStyle} />
              <input type="text" value={p.price_range} disabled={readOnly} placeholder="价格带(选填)"
                     onChange={e => update(i, { price_range: e.target.value })}
                     className="text-sm px-3 py-1.5 rounded-md" style={inputStyle} />
            </div>
          </div>
        ))}
      </div>
      {!readOnly && value.length < 30 && (
        <button type="button" onClick={add}
                className="mt-2 text-xs px-3 py-1.5 rounded-md"
                style={{ background: 'var(--accent-primary)', color: '#fff' }}>+ 添加产品</button>
      )}
    </div>
  );
}


function TagsRow({ label, required, readOnly, value, onChange, placeholder }: TagsProps) {
  const [draft, setDraft] = useState('');
  const add = () => {
    const v = draft.trim();
    if (!v) return;
    if (value.includes(v)) { setDraft(''); return; }
    onChange([...value, v]);
    setDraft('');
  };
  const remove = (i: number) => onChange(value.filter((_, idx) => idx !== i));
  return (
    <div>
      <Label text={label} required={required} />
      <div className="flex flex-wrap gap-1.5 mb-2">
        {value.map((tag, i) => (
          <span key={`${tag}-${i}`}
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs"
                style={{ background: 'var(--bg-tertiary)', color: 'var(--accent-primary)' }}>
            {tag}
            {!readOnly && (
              <button type="button" onClick={() => remove(i)} className="text-muted hover:text-primary">×</button>
            )}
          </span>
        ))}
      </div>
      {!readOnly && (
        <div className="flex gap-2">
          <input type="text" value={draft}
                 onChange={e => setDraft(e.target.value)}
                 onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); add(); } }}
                 placeholder={placeholder || '输入后回车添加'}
                 className="flex-1 text-sm px-3 py-1.5 rounded-md"
                 style={{ background: 'var(--bg-input)', color: 'var(--text-primary)',
                          border: '1px solid var(--border-color)' }} />
          <button type="button" onClick={add}
                  className="text-xs px-3 py-1.5 rounded-md"
                  style={{ background: 'var(--accent-primary)', color: '#fff' }}>添加</button>
        </div>
      )}
    </div>
  );
}
