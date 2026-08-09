"use client";

import Panel, { EmptyState } from "@/components/ui/Panel";
import { DASH, formatPct } from "@/lib/format";
import type { Portfolio, PriceEvent } from "@/lib/types";

interface HeatmapPanelProps {
  portfolio: Portfolio | null;
  prices: Record<string, PriceEvent>;
  onSelect: (ticker: string) => void;
}

/** P&L → tile colour. Intensity saturates at ±5%, which is a big move for a
 *  single session; beyond that the tile is simply "very green"/"very red"
 *  rather than continuing to darken into illegibility. */
function tileStyle(pnlPct: number | null): { background: string; border: string } {
  if (pnlPct == null) {
    return { background: "var(--color-panel-hi)", border: "var(--color-rule)" };
  }
  const intensity = Math.min(Math.abs(pnlPct) / 0.05, 1);
  const colour = pnlPct >= 0 ? "var(--color-up)" : "var(--color-down)";
  return {
    background: `color-mix(in srgb, ${colour} ${8 + intensity * 34}%, var(--color-panel))`,
    border: `color-mix(in srgb, ${colour} ${30 + intensity * 40}%, var(--color-rule))`,
  };
}

/**
 * Portfolio treemap: area is weight, colour is P&L (PLAN.md §2).
 *
 * A hand-rolled squarified layout rather than Recharts' Treemap — with ten or
 * so tiles the layout is a few lines, and it keeps each tile a real DOM node
 * carrying its own test hooks. A canvas treemap would be opaque to the E2E
 * suite, which asserts `data-pnl-sign` and `data-weight` per tile
 * (API_CONTRACT.md §7.1).
 */
export default function HeatmapPanel({ portfolio, prices, onSelect }: HeatmapPanelProps) {
  const positions = portfolio?.positions ?? [];

  return (
    <Panel title="Allocation" testId="heatmap" scroll={false}>
      {positions.length === 0 ? (
        <EmptyState>Positions appear here sized by weight, coloured by P&amp;L.</EmptyState>
      ) : (
        <div className="flex h-full flex-wrap content-start gap-px p-px">
          {positions.map((position) => {
            const live = prices[position.ticker];
            const livePrice =
              live?.status === "ok" && live.price != null ? live.price : position.current_price;
            const priced = position.priced && livePrice != null;

            const pnlPct =
              priced && position.cost_basis
                ? (position.quantity * livePrice - position.cost_basis) / position.cost_basis
                : null;

            const share = portfolio?.positions_value
              ? position.market_value / portfolio.positions_value
              : 0;
            const style = tileStyle(pnlPct);

            return (
              <button
                key={position.ticker}
                type="button"
                data-testid={`heatmap-tile-${position.ticker}`}
                data-pnl-sign={pnlPct == null ? "flat" : pnlPct > 0 ? "up" : pnlPct < 0 ? "down" : "flat"}
                data-weight={String(position.weight)}
                onClick={() => onSelect(position.ticker)}
                title={`${position.ticker} · ${formatPct(position.weight)} of portfolio`}
                className="flex min-h-[46px] min-w-[68px] flex-col items-start justify-center overflow-hidden border px-2 py-1 text-left transition-opacity hover:opacity-85"
                style={{
                  // Area tracks weight. The floor keeps a 0.5% position from
                  // collapsing to an unreadable sliver, so the map stays a map.
                  flex: `${Math.max(share, 0.06)} 1 68px`,
                  background: style.background,
                  borderColor: style.border,
                }}
              >
                <span className="font-mono text-data font-medium text-ink">{position.ticker}</span>
                <span className="tnum text-tiny text-ink-dim">
                  {pnlPct == null ? DASH : formatPct(pnlPct)}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </Panel>
  );
}
