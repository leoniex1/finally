import ConnectionDot from "@/components/layout/ConnectionDot";
import { DASH, formatMoney, formatPct, formatSignedMoney, toneClass } from "@/lib/format";
import type { ConnectionStatus } from "@/lib/types";

interface HeaderProps {
  totalValue: number | null;
  cashBalance: number | null;
  unrealizedPnl?: number | null;
  unrealizedPnlPct?: number | null;
  connection: ConnectionStatus;
}

function Stat({
  label,
  value,
  testId,
  className = "text-ink",
  size = "base",
}: {
  label: string;
  value: string;
  testId?: string;
  className?: string;
  size?: "base" | "lead";
}) {
  return (
    <div className="flex flex-col justify-center gap-0.5">
      <span className="eyebrow">{label}</span>
      <span
        data-testid={testId}
        className={`tnum leading-none ${size === "lead" ? "text-[17px] font-medium" : "text-data"} ${className}`}
      >
        {value}
      </span>
    </div>
  );
}

export default function Header({
  totalValue,
  cashBalance,
  unrealizedPnl = null,
  unrealizedPnlPct = null,
  connection,
}: HeaderProps) {
  return (
    <header className="flex h-14 shrink-0 items-stretch justify-between gap-6 border-b border-rule bg-panel px-4">
      <div className="flex items-center gap-2.5">
        <span className="h-4 w-1 bg-amber" aria-hidden />
        <span className="font-display text-[20px] font-bold leading-none tracking-[0.06em] text-ink">
          FIN<span className="text-amber">ALLY</span>
        </span>
        <span className="eyebrow hidden border-l border-rule pl-2.5 sm:inline">
          AI Trading Workstation
        </span>
      </div>

      <div className="flex items-stretch gap-5">
        <Stat
          label="Portfolio value"
          value={formatMoney(totalValue)}
          testId="total-value"
          size="lead"
        />
        <div className="w-px self-stretch bg-rule" aria-hidden />
        <Stat label="Cash" value={formatMoney(cashBalance)} testId="cash-balance" />
        <div className="w-px self-stretch bg-rule" aria-hidden />
        <Stat
          label="Unrealized P&L"
          value={
            unrealizedPnl == null
              ? DASH
              : `${formatSignedMoney(unrealizedPnl)}  ${formatPct(unrealizedPnlPct)}`
          }
          className={toneClass(unrealizedPnl)}
        />
        <div className="w-px self-stretch bg-rule" aria-hidden />
        <div className="flex items-center">
          <ConnectionDot status={connection} />
        </div>
      </div>
    </header>
  );
}
