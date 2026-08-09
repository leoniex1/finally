import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import Terminal from "./Terminal";

describe("Terminal shell", () => {
  it("renders every panel the E2E suite selects on", () => {
    render(<Terminal />);

    for (const testId of [
      "connection-status",
      "total-value",
      "cash-balance",
      "watchlist",
      "detail-chart",
      "heatmap",
      "pnl-chart",
      "positions-table",
      "trade-ticker",
      "trade-quantity",
      "trade-buy",
      "trade-sell",
      "chat-input",
      "chat-send",
      "chat-messages",
    ]) {
      expect(screen.getByTestId(testId)).toBeInTheDocument();
    }
  });

  it("shows an em dash instead of a zero before any data arrives", () => {
    render(<Terminal />);
    expect(screen.getByTestId("total-value")).toHaveTextContent("—");
    expect(screen.getByTestId("cash-balance")).toHaveTextContent("—");
  });

  it("omits the trade error slot until a trade is rejected", () => {
    render(<Terminal />);
    expect(screen.queryByTestId("trade-error")).not.toBeInTheDocument();
  });

  it("collapses and reopens the copilot", async () => {
    const user = userEvent.setup();
    render(<Terminal />);

    await user.click(screen.getByRole("button", { name: /collapse the ai copilot/i }));
    expect(screen.queryByTestId("chat-input")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /open the ai copilot/i }));
    expect(screen.getByTestId("chat-input")).toBeInTheDocument();
  });

  it("uppercases and caps the ticker typed into the order line", async () => {
    const user = userEvent.setup();
    render(<Terminal />);

    const input = screen.getByTestId("trade-ticker");
    await user.type(input, "googlx");
    expect(input).toHaveValue("GOOGL");
  });
});
