// Query type labels for seed prompts.
//
// Names match sceneLabels in frontend/src/pages/Reports/lang.ts verbatim --
// the report center and the diagnosis report must use the same names; change one, sync the other.
// (The two sides use different i18n mechanisms: the report center uses useReportsLang(), this uses react-i18next,
//  so it's a copy rather than shared; unifying them into a single constant is a separate follow-up.)

/** Display order: matches the order of the report center rank distribution panel */
export const SCENE_ORDER = ['compare', 'brand', 'search', 'qa', 'intent', 'scenario', 'unknown'];

/** Exclusion set for the exposure rate/TOP1/TOP3 metric definitions (backend EXPOSURE_ONLY_SCENES) */
export const EXPOSURE_ONLY_SCENES = new Set(['brand', 'compare']);

export function sceneLabel(scene: string, t: (k: string, o?: any) => string): string {
  return t(`diagnosis.report.sceneLabels.${scene}`, { defaultValue: scene });
}
