/**
 * Country/area code list for phone login. Like the Qwen login page: default +86, the rest ordered by how common they are.
 * Only area codes and display names, no number-range validation -- the validity of international numbers is left to the SMS provider.
 */
export interface PhoneCountry {
  /** International dialing code, without + */
  code: string;
  /** Display name (Chinese) */
  name: string;
  /** Flag emoji */
  flag: string;
  /** Local number digit range [min, max] */
  len: [number, number];
}

export const PHONE_COUNTRIES: PhoneCountry[] = [
  { code: '86', name: '中国大陆', flag: '🇨🇳', len: [11, 11] },
  { code: '852', name: '中国香港', flag: '🇭🇰', len: [8, 8] },
  { code: '853', name: '中国澳门', flag: '🇲🇴', len: [8, 8] },
  { code: '886', name: '中国台湾', flag: '🇹🇼', len: [9, 10] },
  { code: '1', name: '美国 / 加拿大', flag: '🇺🇸', len: [10, 10] },
  { code: '65', name: '新加坡', flag: '🇸🇬', len: [8, 8] },
  { code: '60', name: '马来西亚', flag: '🇲🇾', len: [9, 10] },
  { code: '81', name: '日本', flag: '🇯🇵', len: [10, 11] },
  { code: '82', name: '韩国', flag: '🇰🇷', len: [9, 11] },
  { code: '66', name: '泰国', flag: '🇹🇭', len: [9, 9] },
  { code: '84', name: '越南', flag: '🇻🇳', len: [9, 10] },
  { code: '62', name: '印度尼西亚', flag: '🇮🇩', len: [9, 12] },
  { code: '63', name: '菲律宾', flag: '🇵🇭', len: [10, 10] },
  { code: '91', name: '印度', flag: '🇮🇳', len: [10, 10] },
  { code: '971', name: '阿联酋', flag: '🇦🇪', len: [9, 9] },
  { code: '61', name: '澳大利亚', flag: '🇦🇺', len: [9, 9] },
  { code: '64', name: '新西兰', flag: '🇳🇿', len: [8, 10] },
  { code: '44', name: '英国', flag: '🇬🇧', len: [10, 10] },
  { code: '49', name: '德国', flag: '🇩🇪', len: [10, 11] },
  { code: '33', name: '法国', flag: '🇫🇷', len: [9, 9] },
  { code: '7', name: '俄罗斯', flag: '🇷🇺', len: [10, 10] },
];

export const DEFAULT_PHONE_COUNTRY = PHONE_COUNTRIES[0];

/** Whether a local number looks like a mobile number of this country. China keeps the 1[3-9]-prefixed 11-digit rule; others check digit count only. */
export function localNumberValid(country: PhoneCountry, local: string): boolean {
  if (country.code === '86') return /^1[3-9]\d{9}$/.test(local);
  return local.length >= country.len[0] && local.length <= country.len[1];
}

/**
 * Number sent to the backend: China is still bare 11 digits (matching existing users' users.phone, not a single character may change);
 * other countries send E.164 ("+<country code><number>"), which the backend normalize_phone recognizes.
 */
export function toApiPhone(country: PhoneCountry, local: string): string {
  return country.code === '86' ? local : `+${country.code}${local}`;
}
