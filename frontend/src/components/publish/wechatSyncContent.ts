const ABSOLUTE_URL_RE = /^[a-z][a-z\d+.-]*:/i;

function absolutizeImageSrc(src: string | null, baseUrl: string): string | null {
  const value = src?.trim();
  if (!value || ABSOLUTE_URL_RE.test(value)) return src;
  try {
    return new URL(value, baseUrl).href;
  } catch {
    return src;
  }
}

function absolutizeMarkdownImageTarget(rawTarget: string, baseUrl: string): string {
  const leading = rawTarget.match(/^\s*/)?.[0] || '';
  const trailing = rawTarget.match(/\s*$/)?.[0] || '';
  const target = rawTarget.trim();
  if (!target) return rawTarget;

  if (target.startsWith('<')) {
    const end = target.indexOf('>');
    if (end === -1) return rawTarget;
    const src = target.slice(1, end);
    const nextSrc = absolutizeImageSrc(src, baseUrl);
    if (!nextSrc || nextSrc === src) return rawTarget;
    return `${leading}<${nextSrc}>${target.slice(end + 1)}${trailing}`;
  }

  const firstSpace = target.search(/\s/);
  const src = firstSpace === -1 ? target : target.slice(0, firstSpace);
  const rest = firstSpace === -1 ? '' : target.slice(firstSpace);
  const nextSrc = absolutizeImageSrc(src, baseUrl);
  if (!nextSrc || nextSrc === src) return rawTarget;
  return `${leading}${nextSrc}${rest}${trailing}`;
}

export function absolutizeMarkdownImageUrls(markdown: string, baseUrl: string): string {
  return markdown.replace(/!\[([^\]\n]*)\]\(([^)\n]+)\)/g, (_match, alt: string, rawTarget: string) => (
    `![${alt}](${absolutizeMarkdownImageTarget(rawTarget, baseUrl)})`
  ));
}

export function absolutizeHtmlImageUrls(html: string, baseUrl: string): string {
  if (!html.trim()) return html;
  const parsed = new DOMParser().parseFromString(html, 'text/html');
  parsed.querySelectorAll('img[src]').forEach(img => {
    const src = img.getAttribute('src');
    const nextSrc = absolutizeImageSrc(src, baseUrl);
    if (nextSrc && nextSrc !== src) {
      img.setAttribute('src', nextSrc);
    }
  });
  return parsed.body.innerHTML;
}
