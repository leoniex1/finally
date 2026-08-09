import type { ConnectionStatus } from "@/lib/types";

const LABEL: Record<ConnectionStatus, string> = {
  connected: "Live",
  reconnecting: "Reconnecting",
  disconnected: "Offline",
};

const DOT: Record<ConnectionStatus, string> = {
  connected: "bg-up",
  reconnecting: "bg-amber status-pulse",
  disconnected: "bg-down",
};

/** Header stream indicator: green live, yellow reconnecting, red offline. */
export default function ConnectionDot({ status }: { status: ConnectionStatus }) {
  return (
    <div
      data-testid="connection-status"
      data-status={status}
      className="flex items-center gap-2"
      title={`Price stream: ${LABEL[status]}`}
    >
      <span className={`inline-block h-1.5 w-1.5 rounded-full ${DOT[status]}`} aria-hidden />
      <span className="eyebrow text-ink-dim">{LABEL[status]}</span>
    </div>
  );
}
