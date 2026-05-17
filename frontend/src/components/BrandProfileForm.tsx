// 品牌资料表单(6 大模块)— TopicEditor 和 /dashboard/topics/:id/profile 共用.
//
// 字段定义见 services/aiTelemetryApi.ts 的 BrandProfile.
// 必填项校验在调用方传 requiredKeys 控制(因为 admin 编辑器 vs 用户提交审核要求不同).

import { useState } from 'react';
import type { BrandProfile } from '../services/aiTelemetryApi';

// 用户提交审核时必填的核心字段(跟后端 PROFILE_REQUIRED_FIELDS 对齐)
// 二、内容创作方向(creation_directions / copywriting_types / target_platforms /
// content_tones / content_redlines)整节移除 — 这些由内容审核 / 投放策略阶段
// 决定,不在资料表单里强制填。BrandProfile schema 字段保留(老数据不破),
// content_generator 的 prompt 拼装如果读到空就跳过。
export const PROFILE_REQUIRED_FIELDS: (keyof BrandProfile)[] = [
  'profile_name', 'company_full_name', 'company_short_name',
  'industry', 'core_business_lines', 'service_geo',
  'founded_year', 'core_credentials', 'brand_diff_tags',
  'core_service_overview', 'service_features', 'service_process', 'target_scenarios',
  'target_audience', 'user_pain_points', 'user_faqs', 'decision_factors',
  'brand_story', 'brand_values',
  'core_message',
];

export function isProfileFieldFilled(profile: BrandProfile, key: keyof BrandProfile): boolean {
  const v = profile[key] as string | string[];
  if (Array.isArray(v)) return v.length > 0;
  return !!(v && v.trim());
}

export function profileMissingFields(profile: BrandProfile, required = PROFILE_REQUIRED_FIELDS): (keyof BrandProfile)[] {
  return required.filter(k => !isProfileFieldFilled(profile, k));
}

interface ProfileFormProps {
  profile: BrandProfile;
  onChange: (p: BrandProfile) => void;
  readOnly?: boolean;
  requiredKeys?: (keyof BrandProfile)[];
}

export function BrandProfileForm({
  profile, onChange, readOnly = false, requiredKeys = PROFILE_REQUIRED_FIELDS,
}: ProfileFormProps) {
  const req = new Set(requiredKeys);
  const setField = <K extends keyof BrandProfile>(key: K, value: BrandProfile[K]) => {
    onChange({ ...profile, [key]: value });
  };
  return (
    <div className="space-y-6">
      <Section title="一、基础标识">
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
        <Pair>
          <TextRow label="服务地域" required={req.has('service_geo')} readOnly={readOnly}
                   value={profile.service_geo} onChange={v => setField('service_geo', v)} />
          <span />
        </Pair>
        <TagsRow label="核心业务线" required={req.has('core_business_lines')} readOnly={readOnly}
                 value={profile.core_business_lines} onChange={v => setField('core_business_lines', v)} />
      </Section>

      <Section title="二、品牌主体信息">
        <Pair>
          <TextRow label="公司 / 团队规模(选填)" required={false} readOnly={readOnly}
                   value={profile.team_size} onChange={v => setField('team_size', v)} />
          <TextRow label="成立时间 / 从业年限" required={req.has('founded_year')} readOnly={readOnly}
                   value={profile.founded_year} onChange={v => setField('founded_year', v)} />
        </Pair>
        <TagsRow label="核心荣誉 / 背书资质" required={req.has('core_credentials')} readOnly={readOnly}
                 value={profile.core_credentials} onChange={v => setField('core_credentials', v)} />
        <TagsRow label="品牌差异化标签(3-5 个)" required={req.has('brand_diff_tags')} readOnly={readOnly}
                 value={profile.brand_diff_tags} onChange={v => setField('brand_diff_tags', v)} />
      </Section>

      <Section title="三、产品 / 服务核心信息">
        <TextAreaRow label="核心服务概述" required={req.has('core_service_overview')} readOnly={readOnly}
                     value={profile.core_service_overview} onChange={v => setField('core_service_overview', v)} />
        <TagsRow label="服务核心特点(分点)" required={req.has('service_features')} readOnly={readOnly}
                 value={profile.service_features} onChange={v => setField('service_features', v)} />
        <TagsRow label="服务关键流程 / 环节" required={req.has('service_process')} readOnly={readOnly}
                 value={profile.service_process} onChange={v => setField('service_process', v)} />
        <TagsRow label="服务覆盖场景 / 客户类型" required={req.has('target_scenarios')} readOnly={readOnly}
                 value={profile.target_scenarios} onChange={v => setField('target_scenarios', v)} />
        <TagsRow label="服务交付保障(选填)" required={false} readOnly={readOnly}
                 value={profile.service_guarantees} onChange={v => setField('service_guarantees', v)} />
      </Section>

      <Section title="四、目标用户与痛点">
        <TagsRow label="核心目标用户" required={req.has('target_audience')} readOnly={readOnly}
                 value={profile.target_audience} onChange={v => setField('target_audience', v)} />
        <TagsRow label="用户核心痛点(分点)" required={req.has('user_pain_points')} readOnly={readOnly}
                 value={profile.user_pain_points} onChange={v => setField('user_pain_points', v)} />
        <TagsRow label="用户高频疑问 / 常见误区" required={req.has('user_faqs')} readOnly={readOnly}
                 value={profile.user_faqs} onChange={v => setField('user_faqs', v)} />
        <TagsRow label="用户决策关键因素" required={req.has('decision_factors')} readOnly={readOnly}
                 value={profile.decision_factors} onChange={v => setField('decision_factors', v)} />
      </Section>

      <Section title="五、品牌故事与情感素材">
        <TextAreaRow label="品牌故事 / 成立初衷" required={req.has('brand_story')} readOnly={readOnly}
                     value={profile.brand_story} onChange={v => setField('brand_story', v)} />
        <TextAreaRow label="核心人物故事(选填)" required={false} readOnly={readOnly}
                     value={profile.key_person_story} onChange={v => setField('key_person_story', v)} />
        <TagsRow label="典型案例 / 客户故事(脱敏)(选填)" required={false} readOnly={readOnly}
                 value={profile.case_stories} onChange={v => setField('case_stories', v)} />
        <TextAreaRow label="品牌价值观 / 服务理念" required={req.has('brand_values')} readOnly={readOnly}
                     value={profile.brand_values} onChange={v => setField('brand_values', v)} />
      </Section>

      <Section title="六、补充素材与创作边界">
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
      </Section>
    </div>
  );
}

function Pair({ children }: { children: React.ReactNode }) {
  // md+ 两列网格,sm 单列。空槽位塞 <span /> 占位即可
  return <div className="grid grid-cols-1 md:grid-cols-2 gap-3">{children}</div>;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-md p-4"
             style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>
      <h3 className="text-sm font-semibold text-primary mb-3">{title}</h3>
      <div className="space-y-3">{children}</div>
    </section>
  );
}

function Label({ text, required }: { text: string; required: boolean }) {
  return (
    <label className="block text-xs text-secondary mb-1">
      {text}{required && <span style={{ color: '#ef4444' }}> *</span>}
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

interface TagsProps {
  label: string; required: boolean; readOnly: boolean;
  value: string[]; onChange: (v: string[]) => void; placeholder?: string;
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
