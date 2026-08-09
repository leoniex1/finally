"use client";

import { Line, LineChart, ResponsiveContainer, Tooltip, YAxis } from "recharts";
import Panel, { EmptyState } from "@/components/ui/Panel";
import { formatMoney, formatSignedMoney, toneClass } from "@/lib/format";
import type { PortfolioHistoryPoint } from "@/lib/types";

/**
 * Total portfolio value over time, from `portfolio_snapshots`.
 *
 * The server windows and downsamples this (API_CONTRACT.md §3.4), so whatever
 * arrives is already chart-sized and is drawn as-is — no client-side thinning,
 * which would drop the endpoints the server deliberately preserved.
 */
export default function PnlChartPanel({ points }: { points: PortfolioHistoryPoint[] }) {
  const first = points[0]?.total_value ?? null;
  const last = points.at(-1)?.total_value ?? null;
  const change = first != null && last != null ? last - first : null;

  return (
    <Panel
      title="Portfolio value"
      testId="pnl-chart"
      scroll={false}
      meta={
        change == null ? null : (
          <span className={`tnum text-tiny ${toneClass(change)}`}>{formatSignedMoney(change)}</span>
        )
      }
    >
      {/* Point count on the frame — see DetailChartPanel: a canvas cannot be
          asserted against, so this is how "the chart has data" is proven. */}
      <div className="h-full w-full" data-point-count={points.length}>
        {points.length < 2 ? (
          <EmptyState>
            Portfolio value is recorded every 30 seconds — the curve appears shortly.
          </EmptyState>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={points} margin={{ top: 12, right: 10, bottom: 6, left: 10 }}>
              <YAxis domain={["dataMin", "dataMax"]} hide />
              <Tooltip
                contentStyle={{
                  background: "var(--color-panel)",
                  border: "1px solid var(--color-rule)",
                  borderRadius: 2,
                  fontSize: 11,
                }}
                labelFormatter={(_, payload) => {
                  const at = payload?.[0]?.payload?.recorded_at;
                  return at ? new Date(at).toLocaleTimeString() : "";
                }}
                formatter={(value) => [formatMoney(Number(value)), "Value"]}
              />
              <Line
                type="monotone"
                dataKey="total_value"
                stroke="var(--color-amber)"
                strokeWidth={1.5}
                dot={false}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </Panel>
  );
}
