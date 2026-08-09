"use client";

import { useCallback, useMemo, useState } from "react";
import ChatPanel from "@/components/chat/ChatPanel";
import DetailChartPanel from "@/components/chart/DetailChartPanel";
import Header from "@/components/layout/Header";
import HeatmapPanel from "@/components/portfolio/HeatmapPanel";
import PnlChartPanel from "@/components/portfolio/PnlChartPanel";
import PositionsPanel from "@/components/portfolio/PositionsPanel";
import TradeBar from "@/components/trade/TradeBar";
import WatchlistPanel from "@/components/watchlist/WatchlistPanel";
import { addWatchlistTicker, removeWatchlistTicker } from "@/lib/api";
import { usePortfolioState, useSelectedTicker } from "@/lib/usePortfolio";
import { usePriceStream } from "@/lib/usePriceStream";

/**
 * The workstation frame: fixed header, a three-column body, and the order line
 * across the foot. The page never scrolls — each panel scrolls on its own, so
 * the whole book stays on screen at once.
 *
 * This component owns everything the panels share: the single price stream,
 * the REST-backed portfolio state, and the selected ticker. Prices flow down
 * as one map rather than each panel opening its own EventSource.
 */
export default function Terminal() {
  const { prices, sparklines, connection } = usePriceStream();
  const { portfolio, watchlist, history, refresh, setWatchlist } = usePortfolioState();
  const [chatCollapsed, setChatCollapsed] = useState(false);

  const tickers = useMemo(() => watchlist.map((row) => row.ticker), [watchlist]);
  const [selectedTicker, selectTicker] = useSelectedTicker(tickers);

  const handleAdd = useCallback(
    async (ticker: string) => {
      const added = await addWatchlistTicker(ticker);
      // Merge rather than re-fetch: the POST already returned the row, and a
      // full refresh here would make the new symbol appear a beat late.
      setWatchlist((current) =>
        current.some((row) => row.ticker === added.ticker) ? current : [...current, added],
      );
    },
    [setWatchlist],
  );

  const handleRemove = useCallback(
    async (ticker: string) => {
      await removeWatchlistTicker(ticker);
      setWatchlist((current) => current.filter((row) => row.ticker !== ticker));
      // Deliberately does not touch positions or the selection: a held ticker
      // keeps streaming and stays chartable after leaving the watchlist
      // (PLAN.md §6).
    },
    [setWatchlist],
  );

  // Total value is recomputed from live prices rather than waiting on the next
  // portfolio fetch, so the header ticks with the market like the rest of the
  // terminal. Unpriced positions fall back to the server's figure.
  const liveTotal = useMemo(() => {
    if (!portfolio) return null;
    const positionsValue = portfolio.positions.reduce((sum, position) => {
      const live = prices[position.ticker];
      const usable = position.priced && live?.status === "ok" && live.price != null;
      return sum + (usable ? position.quantity * live.price! : position.market_value);
    }, 0);
    return portfolio.cash_balance + positionsValue;
  }, [portfolio, prices]);

  const livePnl = useMemo(() => {
    if (!portfolio || liveTotal == null) return { value: null, pct: null };
    const costBasis = portfolio.positions
      .filter((position) => position.priced)
      .reduce((sum, position) => sum + position.cost_basis, 0);
    if (!costBasis) return { value: null, pct: null };
    const value = liveTotal - portfolio.cash_balance - costBasis;
    return { value, pct: value / costBasis };
  }, [portfolio, liveTotal]);

  return (
    <div className="grid h-dvh grid-rows-[auto_minmax(0,1fr)_auto] bg-void">
      <Header
        totalValue={liveTotal}
        cashBalance={portfolio?.cash_balance ?? null}
        unrealizedPnl={livePnl.value}
        unrealizedPnlPct={livePnl.pct}
        connection={connection}
      />

      <div className="flex min-h-0 overflow-hidden">
        <div className="grid min-h-0 flex-1 grid-cols-1 gap-px overflow-y-auto lg:grid-cols-[300px_minmax(0,1fr)] lg:overflow-hidden">
          <WatchlistPanel
            tickers={watchlist}
            prices={prices}
            sparklines={sparklines}
            selected={selectedTicker}
            onSelect={selectTicker}
            onAdd={handleAdd}
            onRemove={handleRemove}
            className="min-h-[220px] lg:min-h-0"
          />

          <div className="grid min-h-0 grid-rows-[minmax(240px,1.35fr)_minmax(200px,1fr)_minmax(170px,0.95fr)] gap-px">
            <DetailChartPanel
              ticker={selectedTicker}
              live={selectedTicker ? prices[selectedTicker] : undefined}
            />

            <div className="grid min-h-0 grid-cols-1 gap-px md:grid-cols-2">
              <HeatmapPanel portfolio={portfolio} prices={prices} onSelect={selectTicker} />
              <PnlChartPanel points={history} />
            </div>

            <PositionsPanel portfolio={portfolio} prices={prices} onSelect={selectTicker} />
          </div>
        </div>

        <ChatPanel
          collapsed={chatCollapsed}
          onToggle={() => setChatCollapsed((value) => !value)}
          onActionsApplied={refresh}
        />
      </div>

      <TradeBar ticker={selectedTicker} prices={prices} onFilled={refresh} />
    </div>
  );
}
