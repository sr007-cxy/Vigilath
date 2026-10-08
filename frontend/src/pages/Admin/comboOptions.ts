// Writing direction / copy type option values — kept in sync with BrandProfileForm.
// Shared by the plan editor row modal and the "Generate for date" modal (standalone pure module, satisfies react-refresh rules).

export const COMBO_DIRECTIONS: { value: string; label: string }[] = [
  { value: 'industry_insight', label: '行业洞察' },
  { value: 'case_story',       label: '案例分享' },
  { value: 'how_to_guide',     label: '实操指南' },
  { value: 'trend_forecast',   label: '趋势预测' },
  { value: 'product_review',   label: '产品评测' },
  { value: 'customer_story',   label: '客户故事' },
  { value: 'faq',              label: 'FAQ' },
];

export const COMBO_TYPES: { value: string; label: string }[] = [
  { value: 'long_form',          label: '深度长文' },
  { value: 'medium_post',        label: '中等图文' },
  { value: 'short_social',       label: '短社媒' },
  { value: 'video_script_long',  label: '长视频脚本' },
  { value: 'video_script_short', label: '短视频文案' },
  { value: 'faq_list',           label: 'FAQ 列表' },
];
