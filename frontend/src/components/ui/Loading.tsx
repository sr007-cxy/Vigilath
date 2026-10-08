// Site-wide unified API loading state -- centered spinner + text (+ optional subtext explaining what's being waited on).
// Stop writing bare "..." on pages as a loading hint: a cursor-sized ellipsis can't tell spinning from stuck.
// BrandGrowth's LoadingBlock is re-exported from here, so old call sites don't need changes.

export function LoadingBlock({
  label, hint, className,
}: { label: string; hint?: string; className?: string }) {
  return (
    <div className={`flex flex-col items-center justify-center gap-2 py-10 text-muted ${className ?? ''}`}>
      <svg className="animate-spin w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"
           style={{ color: 'var(--accent-primary)' }}>
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth={4} />
        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
      </svg>
      <span className="text-xs">{label}</span>
      {hint && <span className="text-[10px] opacity-70">{hint}</span>}
    </div>
  );
}
