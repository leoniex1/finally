"use client";

import Panel, { EmptyState } from "@/components/ui/Panel";
import {
  DASH,
  formatMoney,
  formatPct,
  formatPrice,
  formatQuantity,
  toneClass,
} from "@/lib/format";
import type { Portfolio, PriceEvent } from "@/lib/types";

interface PositionsPanelProps {
  portfolio: Portfolio | null;
  prices: Record<string, PriceEvent>;
  onSelect: (ticker: string) => void;
}

const HEAD = "eyebrow text-right";

/**
 * The holdings book.
 *
 * Prices come from the stream rather than from the portfolio payload, so the
 * table stays live between refreshes — but the *decision* about whether a
 * position is priceable stays with the server's `priced` flag. A position the
 * server could not price shows `—` for price and P&L rather than a fabricated
 * number, and omits `data-value` entirely so "unpriced" is assertable and can
 * never be read as a real zero (API_CONTRACT.md §7.1).
 */
export default function PositionsPanel({ portfolio, prices, onSelect }: PositionsPanelProps) {
  const positions = portfolio?.positions ?? [];

  return (
    <Panel
      title="Positions"
      testId="positions-table"
      meta={<span className="tnum text-micro text-ink-faint">{positions.length}</span>}
    >
      {positions.length === 0 ? (
        <EmptyState>No open positions. Use the order line below to buy.</EmptyState>
      ) : (
        <table className="w-full border-collapse">
          <thead className="sticky top-0 z-10 bg-panel">
            <tr className="border-b border-rule-soft">
              <th className="eyebrow px-3 py-1.5 text-left">Symbol</th>
              <th className={`${HEAD} px-3 py-1.5`}>Qty</th>
              <th className={`${HEAD} px-3 py-1.5`}>Avg cost</th>
              <th className={`${HEAD} px-3 py-1.5`}>Last</th>
              <th className={`${HEAD} px-3 py-1.5`}>Market value</th>
              <th className={`${HEAD} px-3 py-1.5`}>Unrealized</th>
              <th className={`${HEAD} px-3 py-1.5`}>%</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((position) => {
              const live = prices[position.ticker];
              // The server owns the priced/unpriced decision; the stream only
              // supplies a fresher number for a position already priceable.
              const livePrice =
                live?.status === "ok" && live.price != null ? live.price : position.current_price;
              const priced = position.priced && livePrice != null;

              const marketValue = priced ? position.quantity * livePrice : position.market_value;
              const pnl = priced ? marketValue - position.cost_basis : null;
              const pnlPct = priced && position.cost_basis ? pnl! / position.cost_basis : null;

              return (
                <tr
                  key={position.ticker}
                  data-testid={`position-row-${position.ticker}`}
                  onClick={() => onSelect(position.ticker)}
                  className="cursor-pointer border-b border-rule-soft transition-colors hover:bg-panel-hi"
                >
                  <td className="px-3 py-1.5 font-mono text-data font-medium text-ink">
                    {position.ticker}
                  </td>
                  <td
                    data-testid={`position-qty-${position.ticker}`}
                    data-value={String(position.quantity)}
                    className="tnum px-3 py-1.5 text-right text-tiny text-ink"
                  >
                    {formatQuantity(position.quantity)}
                  </td>
                  <td
                    data-testid={`position-avg-cost-${position.ticker}`}
                    data-value={String(position.avg_cost)}
                    className="tnum px-3 py-1.5 text-right text-tiny text-ink-dim"
                  >
                    {formatPrice(position.avg_cost)}
                  </td>
                  <td
                    data-testid={`position-price-${position.ticker}`}
                    {...(priced ? { "data-value": String(livePrice) } : {})}
                    className="tnum px-3 py-1.5 text-right text-tiny text-ink"
                  >
                    {priced ? formatPrice(livePrice) : DASH}
                  </td>
                  <td className="tnum px-3 py-1.5 text-right text-tiny text-ink">
                    {formatMoney(marketValue)}
                  </td>
                  <td
                    data-testid={`position-pnl-${position.ticker}`}
                    {...(priced ? { "data-value": String(pnl) } : {})}
                    className={`tnum px-3 py-1.5 text-right text-tiny ${priced ? toneClass(pnl) : "text-ink-faint"}`}
                  >
                    {priced ? formatMoney(pnl) : DASH}
                  </td>
                  <td
                    className={`tnum px-3 py-1.5 text-right text-tiny ${priced ? toneClass(pnlPct) : "text-ink-faint"}`}
                  >
                    {priced ? formatPct(pnlPct) : DASH}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </Panel>
  );
}
