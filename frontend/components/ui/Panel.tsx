import type { ReactNode } from "react";

interface PanelProps {
  title: string;
  /** Right-aligned slot in the panel head: counts, controls, live figures. */
  meta?: ReactNode;
  children: ReactNode;
  /** Applied to the panel frame. */
  className?: string;
  /** Applied to the scrolling content area. */
  bodyClassName?: string;
  testId?: string;
  /** Content area scrolls internally — the page itself never scrolls. */
  scroll?: boolean;
}

/**
 * The one panel frame the whole terminal is built from: hairline border,
 * a micro-label head, and a content area that scrolls on its own.
 */
export default function Panel({
  title,
  meta,
  children,
  className = "",
  bodyClassName = "",
  testId,
  scroll = true,
}: PanelProps) {
  return (
    <section
      data-testid={testId}
      className={`flex min-h-0 min-w-0 flex-col border border-rule bg-panel ${className}`}
    >
      <header className="flex h-7 shrink-0 items-center justify-between gap-3 border-b border-rule-soft px-3">
        <h2 className="eyebrow truncate">{title}</h2>
        {meta ? <div className="flex shrink-0 items-center gap-3">{meta}</div> : null}
      </header>
      <div className={`min-h-0 flex-1 ${scroll ? "overflow-auto" : "overflow-hidden"} ${bodyClassName}`}>
        {children}
      </div>
    </section>
  );
}

/** Quiet placeholder copy for a panel with nothing in it yet. */
export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-full items-center justify-center px-6 text-center text-tiny text-ink-faint">
      {children}
    </div>
  );
}
