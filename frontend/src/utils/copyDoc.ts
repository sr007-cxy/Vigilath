// Draft copy - shared by the detail page's "Copy Markdown / Copy article" buttons.
//
// Copy Markdown: hands over the raw md source, for pasting back into an editor / repo.
// Copy article: converts to HTML and writes it as text/html to the clipboard, so pasting into the WeChat Official Account backend, Word or Feishu Docs
//          keeps heading levels and bold; also writes text/plain as a fallback for plain-text editors.
//
// Compatibility order: ClipboardItem (modern browsers) -> contenteditable + execCommand('copy')
// (old Chromium / domestic browser engines) -> plain-text writeText.

import { marked } from 'marked';
import { toast } from '../components/ui/toast';

const htmlToPlain = (html: string): string => {
  const el = document.createElement('div');
  el.innerHTML = html;
  return (el.innerText || el.textContent || '').trim();
};

/** md -> full HTML fragment (with title; marked in sync mode) */
export function docToHtml(title: string, markdown: string): string {
  const body = marked.parse(markdown || '', { async: false }) as string;
  const head = title ? `<h1>${title.replace(/[<>&]/g, c =>
    ({ '<': '&lt;', '>': '&gt;', '&': '&amp;' }[c] || c))}</h1>\n` : '';
  return head + body;
}

/** Rich-text copy fallback: select an off-screen contenteditable, then use execCommand */
function copyHtmlLegacy(html: string): boolean {
  const holder = document.createElement('div');
  holder.contentEditable = 'true';
  holder.innerHTML = html;
  holder.style.cssText = 'position:fixed;left:-99999px;top:0;white-space:pre-wrap;';
  document.body.appendChild(holder);
  try {
    const range = document.createRange();
    range.selectNodeContents(holder);
    const sel = window.getSelection();
    sel?.removeAllRanges();
    sel?.addRange(range);
    const ok = document.execCommand('copy');
    sel?.removeAllRanges();
    return ok;
  } catch {
    return false;
  } finally {
    document.body.removeChild(holder);
  }
}

/** Copy the Markdown source (with the title line) */
export async function copyMarkdown(title: string, markdown: string): Promise<void> {
  const text = `${title ? `# ${title}\n\n` : ''}${markdown || ''}`;
  try {
    await navigator.clipboard.writeText(text);
    toast.success('Markdown 已复制');
  } catch {
    if (copyHtmlLegacy(text.replace(/\n/g, '<br>'))) toast.success('Markdown 已复制');
    else toast.error('复制失败,请手动选中正文复制');
  }
}

/** Copy the article (rich text: heading levels / bold / lists preserved) */
export async function copyArticle(title: string, markdown: string): Promise<void> {
  const html = docToHtml(title, markdown);
  const plain = htmlToPlain(html);
  try {
    if (typeof ClipboardItem !== 'undefined' && navigator.clipboard?.write) {
      await navigator.clipboard.write([
        new ClipboardItem({
          'text/html': new Blob([html], { type: 'text/html' }),
          'text/plain': new Blob([plain], { type: 'text/plain' }),
        }),
      ]);
      toast.success('文章已复制(带排版)');
      return;
    }
  } catch {
    /* fall through to the fallback below */
  }
  if (copyHtmlLegacy(html)) {
    toast.success('文章已复制(带排版)');
    return;
  }
  try {
    await navigator.clipboard.writeText(plain);
    toast.success('文章已复制(纯文本)');
  } catch {
    toast.error('复制失败,请手动选中正文复制');
  }
}
