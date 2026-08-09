"use client";

import { useEffect, useRef, useState } from "react";
import { Area, AreaChart, ResponsiveContainer, Tooltip, YAxis } from "recharts";
import Panel, { EmptyState } from "@/components/ui/Panel";
import { getPriceHistory } from "@/lib/api";
import { DASH, formatPct, formatPrice, referenceLabel, toneClass } from "@/lib/format";
import type { PriceEvent, PricePoint } from "@/lib/types";

interface DetailChartPanelProps {
  ticker: string | null;
  live: PriceEvent | undefined;
}

/**
 * The main chart: server history first, then live SSE points appended.
 *
 * The backfill is the reason `GET /api/prices/{ticker}/history` exists. Without
 * it this chart would hold only points seen since page load and would be empty
 * after every reload — which is exactly the state PLAN.md §12 asks a test to
 * rule out. An empty `points: []` from a fresh container is still a valid 200,
 * so "no history yet" and "request failed" are rendered differently.
 */
export default function DetailChartPanel({ ticker, live }: DetailChartPanelProps) {
  const [points, setPoints] = useState<PricePoint[]>([]);
  const [loading, setLoading] = useState(false);
  const lastAppended = useRef<number | null>(null);

  useEffect(() => {
    if (!ticker) return;

    let cancelled = false;
    setLoading(true);
    lastAppended.current = null;

    getPriceHistory(ticker)
      .then((history) => {
        if (cancelled) return;
        setPoints(history.points);
        lastAppended.current = history.points.at(-1)?.t ?? null;
      })
      .catch(() => {
        // A 404 means the ticker left the tracked set — correct, not an
        // error worth shouting about. The chart fills in from SSE if it
        // comes back.
        if (!cancelled) setPoints([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [ticker]);

  // Append live ticks onto the backfilled series. Guarded on `updated_at` so
  // a re-render cannot duplicate the last point.
  useEffect(() => {
    if (!live || live.ticker !== ticker) return;
    if (live.status !== "ok" || live.price == null || live.updated_at == null) return;
    if (lastAppended.current != null && live.updated_at <= lastAppended.current) return;

    lastAppended.current = live.updated_at;
    setPoints((current) => [...current, { t: live.updated_at as number, price: live.price as number }]);
  }, [live, ticker]);

  const priced = live?.status === "ok" && live.price != null;

  return (
    <Panel
      title={ticker ? `${ticker} · Price` : "Price"}
      testId="detail-chart"
      scroll={false}
      meta={
        <>
          <span className={`tnum text-data ${priced ? "text-ink" : "text-ink-faint"}`}>
            {priced ? formatPrice(live?.price) : DASH}
          </span>
          <span
            className={`tnum text-tiny ${priced ? toneClass(live?.change_pct) : "text-ink-faint"}`}
            title={referenceLabel(live?.reference_kind)}
          >
            {priced ? formatPct(live?.change_pct) : DASH}
          </span>
        </>
      }
    >
      {/* `data-*` on the frame, not the canvas: a canvas is opaque to the E2E
          suite, so the point count is the only way to assert that the chart
          has history rather than merely a container (API_CONTRACT.md §7.1). */}
      <div
        className="h-full w-full"
        data-ticker={ticker ?? ""}
        data-point-count={points.length}
      >
        {points.length === 0 ? (
          <EmptyState>
            {loading
              ? "Loading history…"
              : ticker
                ? `No history for ${ticker} yet — the chart fills in as prices arrive.`
                : "Select a ticker to chart it."}
          </EmptyState>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={points} margin={{ top: 10, right: 8, bottom: 4, left: 8 }}>
              <defs>
                <linearGradient id="detail-fill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--color-azure)" stopOpacity={0.28} />
                  <stop offset="100%" stopColor="var(--color-azure)" stopOpacity={0} />
                </linearGradient>
              </defs>
              <YAxis
                domain={["dataMin", "dataMax"]}
                hide
                // Padding the domain by the series' own range keeps a quiet
                // stretch from being drawn as dramatic noise filling the panel.
                allowDataOverflow={false}
              />
              <Tooltip
                contentStyle={{
                  background: "var(--color-panel)",
                  border: "1px solid var(--color-rule)",
                  borderRadius: 2,
                  fontSize: 11,
                }}
                // Recharts types these as ReactNode/ValueType, so the
                // conversion happens here rather than in the signature.
                labelFormatter={(label) => new Date(Number(label) * 1000).toLocaleTimeString()}
                formatter={(value) => [formatPrice(Number(value)), "Price"]}
              />
              <Area
                type="monotone"
                dataKey="price"
                stroke="var(--color-azure)"
                strokeWidth={1.5}
                fill="url(#detail-fill)"
                isAnimationActive={false}
                dot={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
    </Panel>
  );
}
