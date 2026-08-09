"use client";

import { useEffect, useRef, useState } from "react";
import { getChatHistory, sendChatMessage } from "@/lib/api";
import { formatMoney, formatPrice, formatQuantity } from "@/lib/format";
import type { ChatAction, ChatMessage } from "@/lib/types";

interface ChatPanelProps {
  collapsed: boolean;
  onToggle: () => void;
  /** Actions can move cash and positions, so the terminal re-reads state. */
  onActionsApplied: () => Promise<void> | void;
}

function describe(action: ChatAction): string {
  if (action.kind === "trade") {
    const { side, quantity, ticker, fill_price, total } = action.detail;
    const verb = side === "buy" ? "Bought" : "Sold";
    if (action.status === "applied" && fill_price != null) {
      return `${verb} ${formatQuantity(quantity)} ${ticker} @ ${formatPrice(fill_price)} · ${formatMoney(total ?? null)}`;
    }
    return `${side === "buy" ? "Buy" : "Sell"} ${formatQuantity(quantity)} ${ticker}`;
  }
  const { action: change, ticker } = action.detail;
  return `${change === "add" ? "Added" : "Removed"} ${ticker}${change === "add" ? " to" : " from"} watchlist`;
}

/**
 * One executed action, rendered beneath the message that requested it.
 *
 * This component is the point of the whole feature. The model writes its prose
 * before execution runs and there is no second LLM call, so the message above
 * can state with total confidence that a trade happened when it did not
 * (PLAN.md §9). The action list is the authoritative record and is shown even
 * when it contradicts the text — which is why a failure is visually loud and
 * always carries its reason.
 */
function ActionRow({
  action,
  messageIndex,
  actionIndex,
}: {
  action: ChatAction;
  messageIndex: number;
  actionIndex: number;
}) {
  const failed = action.status === "failed";

  return (
    <div
      data-testid={`chat-action-${messageIndex}-${actionIndex}`}
      data-status={action.status}
      className={`mt-1 border-l-2 py-1 pl-2 text-tiny ${
        failed ? "border-down bg-down/10" : "border-up bg-up/10"
      }`}
    >
      <div className="flex items-start gap-1.5">
        <span className={failed ? "text-down" : "text-up"} aria-hidden>
          {failed ? "✕" : "✓"}
        </span>
        <div className="min-w-0">
          <div className={failed ? "text-ink-dim line-through" : "text-ink"}>{describe(action)}</div>
          {failed && action.error ? (
            <div
              data-testid={`chat-action-error-${messageIndex}-${actionIndex}`}
              className="mt-0.5 text-down"
            >
              {action.error}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export default function ChatPanel({ collapsed, onToggle, onActionsApplied }: ChatPanelProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Stored actions outlive a reload (PLAN.md §9), so the transcript is
    // restored complete with its outcomes rather than just its prose.
    getChatHistory()
      .then((history) => setMessages(history.messages))
      .catch(() => {
        /* an empty panel is a fine cold start; nothing to report */
      });
  }, []);

  useEffect(() => {
    // Assigning `scrollTop` rather than calling `scrollTo`: it is supported
    // everywhere including jsdom, where `scrollTo` is not implemented at all.
    const element = scroller.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [messages, pending]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || pending) return;

    setDraft("");
    setNotice(null);
    setPending(true);
    // Optimistic user row: the assistant's reply can take a second, and an
    // input that empties with nothing to show for it reads as a dropped
    // message.
    setMessages((current) => [
      ...current,
      {
        id: `local-${Date.now()}`,
        role: "user",
        content: text,
        actions: null,
        created_at: new Date().toISOString(),
      },
    ]);

    try {
      const response = await sendChatMessage(text);
      setMessages((current) => [
        ...current,
        {
          id: `assistant-${Date.now()}`,
          role: "assistant",
          content: response.message,
          actions: response.actions,
          created_at: response.created_at,
        },
      ]);
      if (response.actions.some((action) => action.status === "applied")) {
        await onActionsApplied();
      }
    } catch (caught) {
      setNotice(caught instanceof Error ? caught.message : "The assistant is unavailable.");
    } finally {
      setPending(false);
    }
  }

  if (collapsed) {
    return (
      <button
        type="button"
        onClick={onToggle}
        aria-label="Open the AI copilot"
        className="flex w-9 shrink-0 flex-col items-center gap-2 border-l border-rule bg-panel py-3 text-ink-dim transition-colors hover:text-ink"
      >
        <span className="text-amber">✦</span>
        <span className="eyebrow [writing-mode:vertical-rl]">Copilot</span>
      </button>
    );
  }

  return (
    <aside className="flex w-[340px] shrink-0 flex-col border-l border-rule bg-panel">
      <header className="flex h-7 shrink-0 items-center justify-between border-b border-rule-soft px-3">
        <h2 className="eyebrow">
          <span className="text-amber">✦</span> AI Copilot
        </h2>
        <button
          type="button"
          onClick={onToggle}
          aria-label="Collapse the AI copilot"
          className="text-micro text-ink-faint hover:text-ink"
        >
          ✕
        </button>
      </header>

      <div ref={scroller} data-testid="chat-messages" className="min-h-0 flex-1 overflow-auto p-3">
        {messages.length === 0 ? (
          <p className="text-tiny text-ink-faint">
            Ask about your portfolio, request analysis, or tell me to place a trade.
          </p>
        ) : null}

        {messages.map((message, messageIndex) => (
          <div
            key={message.id}
            data-testid={`chat-message-${messageIndex}`}
            data-role={message.role}
            className="mb-3"
          >
            <div className="eyebrow mb-1">{message.role === "user" ? "You" : "FinAlly"}</div>
            <div
              className={`whitespace-pre-wrap text-tiny leading-relaxed ${
                message.role === "user" ? "text-ink-dim" : "text-ink"
              }`}
            >
              {message.content}
            </div>
            {message.actions?.map((action, actionIndex) => (
              <ActionRow
                key={actionIndex}
                action={action}
                messageIndex={messageIndex}
                actionIndex={actionIndex}
              />
            ))}
          </div>
        ))}

        {pending ? (
          <div data-testid="chat-loading" className="flex items-center gap-1.5 text-tiny text-ink-faint">
            <span className="status-pulse inline-block h-1.5 w-1.5 rounded-full bg-amber" />
            Thinking…
          </div>
        ) : null}

        {notice ? (
          <p role="alert" className="mt-2 border-l-2 border-down bg-down/10 py-1 pl-2 text-tiny text-down">
            {notice}
          </p>
        ) : null}
      </div>

      <form onSubmit={submit} className="shrink-0 border-t border-rule p-2.5">
        <div className="flex gap-1.5">
          <input
            data-testid="chat-input"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="Ask or instruct…"
            className="min-w-0 flex-1 border border-rule bg-void px-2 py-1.5 text-tiny text-ink placeholder:text-ink-faint focus:border-azure focus:outline-none"
          />
          <button
            type="submit"
            data-testid="chat-send"
            disabled={pending}
            className="border border-rule bg-violet px-3 py-1.5 text-micro font-semibold uppercase tracking-wide text-ink transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            Send
          </button>
        </div>
      </form>
    </aside>
  );
}
