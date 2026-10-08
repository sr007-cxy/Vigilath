// See docs/05-测试验收/url-validation-cases.md for the shared pass/reject test list.
// Must stay in sync with backend/geo/utils/validator.py::validate_url —
// identical rule, identical cases.
//
// Rule: ASCII only; scheme http/https; hostname is LDH with at least one dot
// (each label starts/ends with alnum, hyphens allowed internally). IDN /
// non-ASCII / emoji domains are rejected — add idna conversion both sides
// before relaxing this.

const HOST_RE =
  /^(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]*[a-zA-Z0-9])?)(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9\-]*[a-zA-Z0-9])?)+$/;

const ASCII_ONLY = /^[\x00-\x7F]+$/;

export const normalizeUrl = (input: string): string => {
  let t = input.trim();
  if (!t) return t;
  if (!t.startsWith('http://') && !t.startsWith('https://')) t = `https://${t}`;
  // Always strip trailing slash and hash: `example.com/` and `example.com` used to reach the backend in two forms
  // (inconsistent cache keys/audit behavior, occasional failures with the slash); normalize to the no-slash form here
  try {
    const u = new URL(t);
    const path = u.pathname.replace(/\/+$/, '');
    return `${u.protocol}//${u.host}${path}${u.search}`;
  } catch {
    return t.replace(/\/+$/, '');
  }
};

export const validateUrl = (input: string): boolean => {
  const url = normalizeUrl(input);
  if (!url || !ASCII_ONLY.test(url)) return false;
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return false;
  // URL.hostname lowercases and, for IDN input, already returns punycode —
  // but because we reject non-ASCII above, we only ever see plain LDH here.
  return HOST_RE.test(parsed.hostname);
};
