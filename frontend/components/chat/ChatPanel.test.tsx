import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ChatPanel from "./ChatPanel";
import type { ChatAction } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  getChatHistory: vi.fn(),
  sendChatMessage: vi.fn(),
}));

const { getChatHistory, sendChatMessage } = await import("@/lib/api");

const FAILED_TRADE: ChatAction = {
  kind: "trade",
  status: "failed",
  detail: { ticker: "NVDA", side: "buy", quantity: 100 },
  error: "Insufficient cash: need $18,432.00, have $10,000.00",
};

const APPLIED_TRADE: ChatAction = {
  kind: "trade",
  status: "applied",
  detail: { ticker: "AAPL", side: "buy", quantity: 10, fill_price: 201.78, total: 2017.8 },
};

function renderPanel() {
  return render(
    <ChatPanel collapsed={false} onToggle={() => {}} onActionsApplied={() => {}} />,
  );
}

describe("ChatPanel", () => {
  beforeEach(() => {
    vi.mocked(getChatHistory).mockResolvedValue({ messages: [] });
  });

  it("renders a failed action beneath a message whose prose claims success", async () => {
    // The core guarantee of PLAN.md §9: the model writes its message before
    // execution runs, so the prose can confidently announce a trade that was
    // rejected. The action list must contradict it, visibly and with a reason.
    vi.mocked(sendChatMessage).mockResolvedValue({
      message: "Buying 100 NVDA to lift your tech weighting.",
      actions: [FAILED_TRADE],
      created_at: "2026-08-08T14:03:11+00:00",
    });

    renderPanel();
    await userEvent.type(screen.getByTestId("chat-input"), "buy 100 NVDA");
    await userEvent.click(screen.getByTestId("chat-send"));

    const action = await screen.findByTestId("chat-action-1-0");
    expect(action).toHaveAttribute("data-status", "failed");
    // Asserting the status alone would pass against a UI that swallowed the
    // reason — the reason is the feature.
    expect(screen.getByTestId("chat-action-error-1-0")).toHaveTextContent(
      "Insufficient cash: need $18,432.00, have $10,000.00",
    );
    expect(screen.getByTestId("chat-message-1")).toHaveTextContent("Buying 100 NVDA");
  });

  it("shows fill price and total for an applied trade", async () => {
    vi.mocked(sendChatMessage).mockResolvedValue({
      message: "Placing an order to buy 10 AAPL.",
      actions: [APPLIED_TRADE],
      created_at: "2026-08-08T14:03:11+00:00",
    });

    renderPanel();
    await userEvent.type(screen.getByTestId("chat-input"), "buy 10 AAPL");
    await userEvent.click(screen.getByTestId("chat-send"));

    const action = await screen.findByTestId("chat-action-1-0");
    expect(action).toHaveAttribute("data-status", "applied");
    expect(action).toHaveTextContent("201.78");
    expect(action).toHaveTextContent("$2,017.80");
  });

  it("restores stored actions from history on mount", async () => {
    // Stored outcomes outlive a reload, so a refresh must not reduce the
    // transcript to prose that claims a trade succeeded.
    vi.mocked(getChatHistory).mockResolvedValue({
      messages: [
        {
          id: "1",
          role: "assistant",
          content: "Buying 100 NVDA.",
          actions: [FAILED_TRADE],
          created_at: "2026-08-08T14:03:11+00:00",
        },
      ],
    });

    renderPanel();

    expect(await screen.findByTestId("chat-action-0-0")).toHaveAttribute("data-status", "failed");
  });

  it("surfaces a 503 as an inline notice rather than failing silently", async () => {
    vi.mocked(sendChatMessage).mockRejectedValue(
      new Error("Chat is unavailable: OPENROUTER_API_KEY is not configured"),
    );

    renderPanel();
    await userEvent.type(screen.getByTestId("chat-input"), "hello");
    await userEvent.click(screen.getByTestId("chat-send"));

    expect(await screen.findByRole("alert")).toHaveTextContent("OPENROUTER_API_KEY");
  });

  it("shows the loading indicator only while a reply is outstanding", async () => {
    let release: (value: never) => void = () => {};
    vi.mocked(sendChatMessage).mockReturnValue(
      new Promise((resolve) => {
        release = resolve as (value: never) => void;
      }),
    );

    renderPanel();
    await userEvent.type(screen.getByTestId("chat-input"), "hello");
    await userEvent.click(screen.getByTestId("chat-send"));

    expect(screen.getByTestId("chat-loading")).toBeInTheDocument();

    release({ message: "Hi.", actions: [], created_at: "x" } as never);
    await waitFor(() => expect(screen.queryByTestId("chat-loading")).not.toBeInTheDocument());
  });
});
