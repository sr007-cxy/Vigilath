// SMS billing segment estimate. Metric definition matches backend `agents/sales_agent/service/sms_providers/base.py:sms_segments`:
// GSM-7 single 160, concatenated 153; any non-GSM-7 character (Chinese, some punctuation, emoji) switches the whole message to UCS-2,
// single 70, concatenated 67.
//
// Extracted into a shared file instead of per page: both the SMS management page's copy editor and the workbench draft card show segment counts,
// and two different counts is worse than none - users will assume one of them is right.

const GSM7 = new Set(
  "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
  + '¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà'
  + '^{}\\[~]|€',
);

/**
 * Estimate the billing segment count of an SMS text.
 *
 * Only an estimate - the real count is whatever the platform receipt says. We compute it on the frontend because "a Chinese SMS holds only 70 chars"
 * is the easiest trap when writing copy; finding out from the bill that it split into 3 segments is too late.
 */
export function smsSegments(text: string): number {
  const body = String(text || '');
  if (!body) return 0;
  const ucs2 = [...body].some(ch => !GSM7.has(ch));
  if (ucs2) return body.length <= 70 ? 1 : Math.ceil(body.length / 67);
  return body.length <= 160 ? 1 : Math.ceil(body.length / 153);
}

/** Whether the text falls into UCS-2 encoding (almost all Chinese SMS do), which decides 70 vs 160 chars per segment. */
export function smsIsUnicode(text: string): boolean {
  return [...String(text || '')].some(ch => !GSM7.has(ch));
}
