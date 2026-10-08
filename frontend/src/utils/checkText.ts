import type { CheckResult } from '../types/geo';

/** Minimal shape of i18next's `t` that both the page and the PDF exporter use. */
export type Translate = (key: string, options?: Record<string, unknown>) => string;

/**
 * Render a check's user-facing message. Prefers i18n key + params (emitted
 * via geo_checker.emit_check), falls back to the raw English `message`
 * for legacy un-migrated checks.
 *
 * `defaultValue` matters: i18n is configured with `fallbackLng: false`, so a
 * key that is missing from the dictionary would otherwise render as the literal
 * key path ("result.checks.seo.title.length_ok") in the UI. Passing the
 * backend's English text keeps a missing translation merely untranslated
 * instead of visibly broken.
 *
 * Lives here rather than in Result.tsx because the PDF exporter needs the exact
 * same rendering — reading `check.message` directly there produced a Chinese
 * page and an English PDF.
 */
export function renderCheckMessage(check: CheckResult, t: Translate): string {
  if (check.message_key) {
    return t(check.message_key, {
      ...((check.message_params || {}) as Record<string, unknown>),
      defaultValue: check.message,
    });
  }
  return check.message;
}

/**
 * Render a check's fix recommendation. Prefers `fix_key` + `fix_params`
 * (emitted via emit_fix), falls back to the raw English `fix` text.
 * Returns an empty string when no fix is available so callers can boolean-
 * check the result. Same missing-key guard as renderCheckMessage.
 */
export function renderCheckFix(check: CheckResult, t: Translate): string {
  if (check.fix_key) {
    return t(check.fix_key, {
      ...((check.fix_params || {}) as Record<string, unknown>),
      defaultValue: (check.fix || '').trim(),
    });
  }
  return (check.fix || '').trim();
}
