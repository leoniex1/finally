import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import PositionsPanel from "./PositionsPanel";
import type { Portfolio, Position, PriceEvent } from "@/lib/types";

function position(overrides: Partial<Position> = {}): Position {
  return {
    ticker: "AAPL",
    quantity: 10,
    avg_cost: 190,
    current_price: 200,
    market_value: 2000,
    cost_basis: 1900,
    unrealized_pnl: 100,
    unrealized_pnl_pct: 0.0526,
    weight: 0.19,
    priced: true,
    updated_at: "2026-08-08T14:00:00+00:00",
    ...overrides,
  };
}

function portfolio(positions: Position[]): Portfolio {
  return {
    cash_balance: 8000,
    total_value: 10000,
    positions_value: 2000,
    total_unrealized_pnl: 100,
    total_unrealized_pnl_pct: 0.05,
    positions,
  };
}

const priced: Record<string, PriceEvent> = {
  AAPL: {
    ticker: "AAPL",
    price: 210,
    prev_price: 209,
    reference_price: 200,
    reference_kind: "session_open",
    change_pct: 0.05,
    direction: "up",
    status: "ok",
    updated_at: 1786234858,
  },
};

describe("PositionsPanel", () => {
  it("prices rows from the live stream, not the stale REST payload", () => {
    render(<PositionsPanel portfolio={portfolio([position()])} prices={priced} onSelect={() => {}} />);

    // 10 shares at the streamed 210, not the payload's 200.
    expect(screen.getByTestId("position-price-AAPL")).toHaveAttribute("data-value", "210");
    expect(screen.getByTestId("position-pnl-AAPL")).toHaveAttribute("data-value", "200");
  });

  it("renders an em dash and omits data-value for an unpriced position", () => {
    // API_CONTRACT §7.1: an absent attribute is how "no price" is asserted,
    // and it can never be confused with a real 0.
    render(
      <PositionsPanel
        portfolio={portfolio([position({ priced: false, current_price: null })])}
        prices={{}}
        onSelect={() => {}}
      />,
    );

    const price = screen.getByTestId("position-price-AAPL");
    expect(price).toHaveTextContent("—");
    expect(price).not.toHaveAttribute("data-value");
    expect(screen.getByTestId("position-pnl-AAPL")).not.toHaveAttribute("data-value");
  });

  it("does not invent a price when the server says unpriced but a stream event exists", () => {
    // The server owns the priced/unpriced decision. A stale cache entry must
    // not resurrect a position the server refused to value.
    render(
      <PositionsPanel
        portfolio={portfolio([position({ priced: false, current_price: null })])}
        prices={priced}
        onSelect={() => {}}
      />,
    );

    expect(screen.getByTestId("position-price-AAPL")).toHaveTextContent("—");
  });

  it("exposes raw quantities so a full-liquidation test can read them", () => {
    render(
      <PositionsPanel
        portfolio={portfolio([position({ quantity: 3.33333 })])}
        prices={priced}
        onSelect={() => {}}
      />,
    );

    expect(screen.getByTestId("position-qty-AAPL")).toHaveAttribute("data-value", "3.33333");
  });

  it("shows an empty state rather than an empty table", () => {
    render(<PositionsPanel portfolio={portfolio([])} prices={{}} onSelect={() => {}} />);

    expect(screen.getByText(/No open positions/i)).toBeInTheDocument();
  });
});
