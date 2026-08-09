import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import WatchlistPanel from "./WatchlistPanel";
import type { PriceEvent, WatchlistTicker } from "@/lib/types";

function row(overrides: Partial<WatchlistTicker> = {}): WatchlistTicker {
  return {
    ticker: "AAPL",
    added_at: "2026-08-08T14:00:00+00:00",
    price: null,
    prev_price: null,
    reference_price: null,
    reference_kind: null,
    change_pct: null,
    direction: "flat",
    status: "pending",
    updated_at: 0,
    ...overrides,
  };
}

const live: PriceEvent = {
  ticker: "AAPL",
  price: 201.78,
  prev_price: 201.6,
  reference_price: 199.1,
  reference_kind: "session_open",
  change_pct: 0.01346,
  direction: "up",
  status: "ok",
  updated_at: 1786234858,
};

function renderPanel(props: Partial<React.ComponentProps<typeof WatchlistPanel>> = {}) {
  return render(
    <WatchlistPanel
      tickers={[row()]}
      prices={{}}
      sparklines={{}}
      selected={null}
      onSelect={() => {}}
      onAdd={async () => {}}
      onRemove={async () => {}}
      {...props}
    />,
  );
}

describe("WatchlistPanel", () => {
  it("renders an em dash for a pending ticker and omits data-value", () => {
    renderPanel();

    const price = screen.getByTestId("watchlist-price-AAPL");
    expect(price).toHaveTextContent("—");
    expect(price).not.toHaveAttribute("data-value");
  });

  it("formats change % from a fraction, not a raw number", () => {
    // 0.01346 is +1.35%, not +0.01%. Getting this wrong is off by two orders
    // of magnitude and looks plausible either way.
    renderPanel({ prices: { AAPL: live } });

    expect(screen.getByTestId("watchlist-row-AAPL")).toHaveTextContent("+1.35%");
    expect(screen.getByTestId("watchlist-price-AAPL")).toHaveAttribute("data-value", "201.78");
  });

  it("discloses the baseline rather than claiming a daily change", () => {
    renderPanel({ prices: { AAPL: live } });

    // Both the column head and the row's own cell disclose it.
    expect(screen.getAllByTitle(/since session open/i).length).toBeGreaterThan(0);
  });

  it("surfaces the server's rejection text when an add fails", async () => {
    const onAdd = vi.fn().mockRejectedValue(new Error("Invalid ticker: 'TOOLONG'"));
    renderPanel({ onAdd });

    await userEvent.type(screen.getByTestId("watchlist-add-input"), "TOOLO");
    await userEvent.click(screen.getByTestId("watchlist-add-submit"));

    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid ticker");
  });

  it("selects a ticker on row click and removes without selecting", async () => {
    const onSelect = vi.fn();
    const onRemove = vi.fn().mockResolvedValue(undefined);
    renderPanel({ onSelect, onRemove });

    await userEvent.click(screen.getByTestId("watchlist-row-AAPL"));
    expect(onSelect).toHaveBeenCalledWith("AAPL");

    onSelect.mockClear();
    await userEvent.click(screen.getByTestId("watchlist-remove-AAPL"));
    expect(onRemove).toHaveBeenCalledWith("AAPL");
    // The remove button lives inside the row; without stopPropagation it
    // would also select the ticker it just deleted.
    expect(onSelect).not.toHaveBeenCalled();
  });
});
