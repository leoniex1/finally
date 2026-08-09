/**
 * A watchlist row's price shape since page load (PLAN.md §2).
 *
 * Hand-drawn SVG rather than a charting component: there is one of these per
 * row, they redraw on every tick, and a full chart library instance per row
 * would cost far more than the polyline it renders. It also deliberately does
 * *not* fetch history — sparklines fill in progressively from SSE, which is
 * the specified behaviour.
 */

import type { Direction } from "@/lib/types";

const WIDTH = 56;
const HEIGHT = 16;

const STROKE: Record<Direction, string> = {
  up: "var(--color-up)",
  down: "var(--color-down)",
  flat: "var(--color-ink-faint)",
};

export default function Sparkline({
  points,
  direction = "flat",
}: {
  points: number[];
  direction?: Direction;
}) {
  // One point is not a line. Reserve the space anyway so rows do not shift
  // horizontally as prices start arriving.
  if (points.length < 2) {
    return <span className="inline-block" style={{ width: WIDTH, height: HEIGHT }} aria-hidden />;
  }

  const min = Math.min(...points);
  const max = Math.max(...points);
  // A flat series has no range to scale against; drawing it down the middle
  // is honest, whereas dividing by zero would put it at the top or NaN it out.
  const span = max - min || 1;
  const step = WIDTH / (points.length - 1);

  const path = points
    .map((price, index) => {
      const x = index * step;
      const y = HEIGHT - ((price - min) / span) * HEIGHT;
      return `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  return (
    <svg
      width={WIDTH}
      height={HEIGHT}
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      className="overflow-visible"
      aria-hidden
    >
      <path
        d={path}
        fill="none"
        stroke={STROKE[direction]}
        strokeWidth={1}
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}
