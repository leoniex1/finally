"use client";

import { useState } from "react";
import { executeTrade } from "@/lib/api";
import { formatMoney, formatPrice } from "@/lib/format";
import type { PriceEvent, TradeSide } from "@/lib/types";

interface TradeBarProps {
  ticker: string | null;
  prices: Record<string, PriceEvent>;
  onFilled: () => Promise<void> | void;
}

/**
 * The order line: market orders, instant fill, no confirmation dialog.
 *
 * The ticker field is free text on purpose, which means the `409 no price
 * available` rejection is easy to hit — a symbol the data source does not
 * recognise, or one added seconds ago that has not ticked yet. Every rejection
 * renders the server's `detail` verbatim (API_CONTRACT.md §2); this component
 * never composes its own wording for a server-side refusal, so the message the
 * user reads is the same one the chat panel shows for the same mistake.
 */
export default function TradeBar({ ticker, prices, onFilled }: TradeBarProps) {
  const [symbol, setSymbol] = useState("");
  const [quantity, setQuantity] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // The selected ticker is a default, not a lock: typing overrides it.
  const effective = (symbol || ticker || "").toUpperCase();
  const live = prices[effective];
  const priced = live?.status === "ok" && live.price != null;
  const estimate = priced && Number(quantity) > 0 ? live.price! * Number(quantity) : null;

  async function submit(side: TradeSide) {
    if (busy) return;
    setBusy(true);
    setError(null);
    setConfirmation(null);

    try {
      const result = await executeTrade({
        ticker: effective,
        // Sent raw: the server owns the §3.3 ladder, and pre-validating here
        // would mean two implementations of the same rules disagreeing.
        quantity: Number(quantity),
        side,
      });
      setConfirmation(
        `${side === "buy" ? "Bought" : "Sold"} ${result.trade.quantity} ${result.trade.ticker} @ ${formatPrice(result.trade.price)}`,
      );
      setQuantity("");
      await onFilled();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Trade failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-12 shrink-0 items-center gap-3 border-t border-rule bg-panel px-4">
      <span className="eyebrow hidden sm:inline">Order</span>

      <input
        data-testid="trade-ticker"
        value={symbol}
        onChange={(event) => setSymbol(event.target.value.toUpperCase())}
        placeholder={ticker ?? "SYMBOL"}
        maxLength={5}
        className="w-24 border border-rule bg-void px-2 py-1 font-mono text-data text-ink placeholder:text-ink-faint focus:border-azure focus:outline-none"
      />

      <input
        data-testid="trade-quantity"
        value={quantity}
        onChange={(event) => setQuantity(event.target.value)}
        placeholder="QTY"
        inputMode="decimal"
        className="w-24 border border-rule bg-void px-2 py-1 font-mono text-data text-ink placeholder:text-ink-faint focus:border-azure focus:outline-none"
      />

      <div className="flex gap-1.5">
        <button
          type="button"
          data-testid="trade-buy"
          disabled={busy}
          onClick={() => submit("buy")}
          className="border border-up/40 bg-up/15 px-4 py-1 text-micro font-semibold uppercase tracking-wide text-up transition-colors hover:bg-up/25 disabled:opacity-50"
        >
          Buy
        </button>
        <button
          type="button"
          data-testid="trade-sell"
          disabled={busy}
          onClick={() => submit("sell")}
          className="border border-down/40 bg-down/15 px-4 py-1 text-micro font-semibold uppercase tracking-wide text-down transition-colors hover:bg-down/25 disabled:opacity-50"
        >
          Sell
        </button>
      </div>

      <span className="tnum hidden text-tiny text-ink-dim md:inline">
        {estimate != null ? `≈ ${formatMoney(estimate)}` : priced ? formatPrice(live!.price) : ""}
      </span>

      {/* Absent from the DOM when there is no error — the E2E suite asserts on
          presence rather than on empty text (API_CONTRACT.md §7.1). */}
      {error ? (
        <span data-testid="trade-error" role="alert" className="truncate text-tiny text-down">
          {error}
        </span>
      ) : confirmation ? (
        <span className="truncate text-tiny text-up">{confirmation}</span>
      ) : null}
    </div>
  );
}
