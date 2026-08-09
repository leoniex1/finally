"use client";

import { useState } from "react";
import Panel from "@/components/ui/Panel";
import Sparkline from "@/components/watchlist/Sparkline";
import { usePriceFlash } from "@/lib/usePriceStream";
import { DASH, formatPct, formatPrice, isPriced, referenceLabel, toneClass } from "@/lib/format";
import type { PriceEvent, WatchlistTicker } from "@/lib/types";

interface WatchlistPanelProps {
  tickers: WatchlistTicker[];
  prices: Record<string, PriceEvent>;
  sparklines: Record<string, number[]>;
  selected: string | null;
  onSelect: (ticker: string) => void;
  onAdd: (ticker: string) => Promise<void>;
  onRemove: (ticker: string) => Promise<void>;
  className?: string;
}

function Row({
  row,
  live,
  spark,
  selected,
  onSelect,
  onRemove,
}: {
  row: WatchlistTicker;
  live: PriceEvent | undefined;
  spark: number[];
  selected: boolean;
  onSelect: (ticker: string) => void;
  onRemove: (ticker: string) => void;
}) {
  // The live event wins where present; the REST row is the fallback for a
  // ticker that has not ticked since page load.
  const entry: WatchlistTicker | PriceEvent = live ?? row;
  const flash = usePriceFlash(live);
  const priced = isPriced(entry);

  return (
    <div
      data-testid={`watchlist-row-${row.ticker}`}
      onClick={() => onSelect(row.ticker)}
      className={`group grid cursor-pointer grid-cols-[1fr_auto_auto] items-center gap-x-3 border-b border-rule-soft px-3 py-1.5 transition-colors ${
        selected ? "bg-panel-hi" : "hover:bg-panel-hi"
      }`}
    >
      <div className="flex min-w-0 items-center gap-2">
        <span
          className={`h-3 w-0.5 shrink-0 ${selected ? "bg-amber" : "bg-transparent"}`}
          aria-hidden
        />
        <span className="truncate font-mono text-data font-medium text-ink">{row.ticker}</span>
        <button
          type="button"
          data-testid={`watchlist-remove-${row.ticker}`}
          aria-label={`Remove ${row.ticker}`}
          onClick={(clickEvent) => {
            clickEvent.stopPropagation();
            onRemove(row.ticker);
          }}
          className="ml-auto hidden text-ink-faint transition-colors hover:text-down group-hover:block"
        >
          <span className="text-micro">✕</span>
        </button>
      </div>

      <div className="flex items-center gap-2">
        <Sparkline points={spark} direction={entry.direction} />
        <span
          data-testid={`watchlist-price-${row.ticker}`}
          {...(priced ? { "data-value": String(entry.price) } : {})}
          className={`tnum w-16 rounded-control px-1 text-right text-data text-ink ${flash}`}
        >
          {priced ? formatPrice(entry.price) : DASH}
        </span>
      </div>

      <span
        className={`tnum w-14 text-right text-tiny ${priced ? toneClass(entry.change_pct) : "text-ink-faint"}`}
        title={referenceLabel(entry.reference_kind)}
      >
        {priced ? formatPct(entry.change_pct) : DASH}
      </span>
    </div>
  );
}

export default function WatchlistPanel({
  tickers,
  prices,
  sparklines,
  selected,
  onSelect,
  onAdd,
  onRemove,
  className = "",
}: WatchlistPanelProps) {
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Whichever baseline the stream is actually reporting, so the column
  // subtitle discloses it rather than asserting "daily".
  const baseline = referenceLabel(
    tickers.map((t) => prices[t.ticker]?.reference_kind ?? t.reference_kind).find(Boolean) ?? null,
  );

  async function submit(submitEvent: React.FormEvent) {
    submitEvent.preventDefault();
    const ticker = draft.trim();
    if (!ticker || busy) return;

    setBusy(true);
    setError(null);
    try {
      await onAdd(ticker);
      setDraft("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not add ticker");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel
      title="Watchlist"
      testId="watchlist"
      className={className}
      meta={<span className="tnum text-micro text-ink-faint">{tickers.length}</span>}
      bodyClassName="flex flex-col"
    >
      <div className="sticky top-0 z-10 grid grid-cols-[1fr_auto_auto] items-center gap-x-3 border-b border-rule-soft bg-panel px-3 py-1.5">
        <span className="eyebrow">Symbol</span>
        <span className="eyebrow text-right">Last</span>
        <span className="eyebrow w-14 cursor-help text-right" title={`Change % ${baseline}`}>
          Chg %
        </span>
      </div>

      <div className="min-h-0 flex-1">
        {tickers.map((row) => (
          <Row
            key={row.ticker}
            row={row}
            live={prices[row.ticker]}
            spark={sparklines[row.ticker] ?? []}
            selected={selected === row.ticker}
            onSelect={onSelect}
            onRemove={onRemove}
          />
        ))}
      </div>

      <form
        onSubmit={submit}
        className="sticky bottom-0 border-t border-rule bg-panel px-3 py-2"
      >
        <div className="flex gap-1.5">
          <input
            data-testid="watchlist-add-input"
            value={draft}
            onChange={(changeEvent) => setDraft(changeEvent.target.value.toUpperCase())}
            placeholder="ADD SYMBOL"
            maxLength={5}
            className="min-w-0 flex-1 border border-rule bg-void px-2 py-1 font-mono text-tiny text-ink placeholder:text-ink-faint focus:border-azure focus:outline-none"
          />
          <button
            type="submit"
            data-testid="watchlist-add-submit"
            disabled={busy}
            className="border border-rule bg-violet px-2.5 py-1 text-micro font-semibold uppercase tracking-wide text-ink transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            Add
          </button>
        </div>
        {error ? (
          <p className="mt-1.5 text-tiny text-down" role="alert">
            {error}
          </p>
        ) : null}
      </form>
    </Panel>
  );
}
