// Markdown rendering: unified styles for tables/links/headings.
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

// Only for this file's MarkdownView. Exporting it would trigger react-refresh/only-export-components.
function markdownComponents() {
  return {
    table: (p: any) => <div style={{ overflowX: 'auto', margin: '6px 0' }}><table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 13 }} {...p} /></div>,
    th: (p: any) => <th style={{ border: '1px solid var(--border-color)', padding: '5px 8px', textAlign: 'left', background: 'var(--bg-tertiary)', color: 'var(--text-primary)' }} {...p} />,
    td: (p: any) => <td style={{ border: '1px solid var(--border-color)', padding: '5px 8px', textAlign: 'left' }} {...p} />,
    a: ({ href = '', children, ...p }: any) => (
      <a
        {...p}
        href={href}
        target="_blank"
        rel="noreferrer"
        style={{ color: 'var(--accent-primary)', textDecoration: 'underline' }}
      >
        {children}
      </a>
    ),
    code: (p: any) => <code style={{ background: 'var(--bg-tertiary)', padding: '0 4px', borderRadius: 4, fontSize: 12 }} {...p} />,
    h1: (p: any) => <h1 style={{ margin: '10px 0 6px', fontSize: 22, lineHeight: 1.35, fontWeight: 700 }} {...p} />,
    h2: (p: any) => <h2 style={{ margin: '10px 0 6px', fontSize: 19, lineHeight: 1.4, fontWeight: 700 }} {...p} />,
    h3: (p: any) => <h3 style={{ margin: '8px 0 4px', fontSize: 16, lineHeight: 1.45, fontWeight: 700 }} {...p} />,
    p: (p: any) => <p style={{ margin: '4px 0' }} {...p} />,
    ul: (p: any) => <ul style={{ margin: '4px 0', paddingLeft: 18 }} {...p} />,
    ol: (p: any) => <ol style={{ margin: '4px 0', paddingLeft: 18 }} {...p} />,
    li: (p: any) => <li style={{ margin: '2px 0' }} {...p} />,
  };
}

export function MarkdownView({ text }: { text: string }) {
  return <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents()}>{text}</ReactMarkdown>;
}
