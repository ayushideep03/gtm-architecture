import { createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api } from "./api";

// ── data hook: fetch + refresh on demand / on interval / on live events ──────
export function useData<T>(path: string | null, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const live = useContext(LiveContext);
  const load = useCallback(async () => {
    if (!path) return;
    try {
      setData(await api.get<T>(path));
      setError(null);
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, ...deps]);
  useEffect(() => {
    load();
  }, [load]);
  // refetch (debounced) when the live stream reports new activity
  const timer = useRef<number>();
  useEffect(() => {
    if (!live.tick) return;
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(load, 400);
  }, [live.tick, load]);
  return { data, error, loading, reload: load };
}

// ── live stream (SSE) shared by the whole app ────────────────────────────────
export interface LiveState {
  connected: boolean;
  tick: number;
  events: any[];
}
export const LiveContext = createContext<LiveState>({ connected: false, tick: 0, events: [] });

export function LiveProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<LiveState>({ connected: false, tick: 0, events: [] });
  useEffect(() => {
    const es = new EventSource("/api/v1/stream");
    es.onopen = () => setState((s) => ({ ...s, connected: true }));
    es.onerror = () => setState((s) => ({ ...s, connected: false }));
    es.addEventListener("oxygen", (msg) => {
      const ev = JSON.parse((msg as MessageEvent).data);
      setState((s) => ({ connected: true, tick: s.tick + 1, events: [ev, ...s.events].slice(0, 200) }));
    });
    return () => es.close();
  }, []);
  return <LiveContext.Provider value={state}>{children}</LiveContext.Provider>;
}

// ── toasts ───────────────────────────────────────────────────────────────────
const ToastContext = createContext<(msg: string, err?: boolean) => void>(() => {});
export const useToast = () => useContext(ToastContext);
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<{ msg: string; err: boolean } | null>(null);
  const show = useCallback((msg: string, err = false) => {
    setToast({ msg, err });
    window.setTimeout(() => setToast(null), err ? 7000 : 3500);
  }, []);
  return (
    <ToastContext.Provider value={show}>
      {children}
      {toast && (
        <div className={`toast ${toast.err ? "err" : ""}`} role="status">
          {toast.msg}
        </div>
      )}
    </ToastContext.Provider>
  );
}

// ── formatting ───────────────────────────────────────────────────────────────
export const human = (s?: string | null) => (s ? s.replace(/_/g, " ") : "—");

export function ago(iso?: string | null): string {
  if (!iso) return "—";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  const fut = diff < 0;
  const a = Math.abs(diff);
  const v = a < 60 ? `${Math.round(a)}s` : a < 3600 ? `${Math.round(a / 60)}m` : a < 86400 ? `${Math.round(a / 3600)}h` : `${Math.round(a / 86400)}d`;
  return fut ? `in ${v}` : `${v} ago`;
}

export const clock = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
export const pct = (v?: number | null) => (v === null || v === undefined ? "—" : `${Math.round(v * 100)}%`);

const STATUS_TONE: Record<string, string> = {
  new: "", enriching: "blue", qualified: "blue", contacted: "blue", replied: "warn", meeting_booked: "good",
  closed_won: "good", disqualified: "bad", closed_lost: "bad", unsubscribed: "bad",
  executed: "good", success: "good", completed: "good", approved: "good", pending: "warn", awaiting_approval: "warn",
  waiting: "", escalated: "warn", blocked: "bad", rejected: "bad", failed: "bad", stopped: "", no_action: "",
  expired: "", cancelled: "", needs_review: "warn", in_progress: "blue",
};
export function Status({ value }: { value?: string | null }) {
  if (!value) return <span className="muted">—</span>;
  return <span className={`badge ${STATUS_TONE[value] ?? ""}`}>{human(value)}</span>;
}

export function Confidence({ value }: { value?: number | null }) {
  if (value === null || value === undefined) return <span className="muted">—</span>;
  return (
    <span className="conf" title={`Model-reported confidence ${pct(value)}`}>
      <span className="conf-bar">
        <i style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }} />
      </span>
      <span className="sub">{pct(value)}</span>
    </span>
  );
}

export function EngineBadge({ engine, model }: { engine?: string | null; model?: string | null }) {
  if (!engine) return null;
  if (model?.includes("scripted")) return <span className="badge warn" title="Scripted demo playbook — not a language model">Demo AI (scripted)</span>;
  if (engine === "llm") return <span className="badge blue" title={model ?? ""}>AI · {model?.split("/").pop()}</span>;
  if (engine.startsWith("rules")) return <span className="badge">Rules{engine === "rules_fallback" ? " (fallback)" : ""}</span>;
  return <span className="badge">{engine}</span>;
}

// Horizontal single-series bars (one hue; values labelled; hover shows exact numbers).
export function BarList({ rows, format = (v: number) => String(v), max }: {
  rows: { label: string; value: number; note?: string }[];
  format?: (v: number) => string;
  max?: number;
}) {
  const top = max ?? Math.max(1, ...rows.map((r) => r.value));
  if (!rows.length) return <div className="empty">No data yet</div>;
  return (
    <div className="bars" role="list">
      {rows.map((r) => (
        <div className="bar-row" key={r.label} role="listitem" title={`${r.label}: ${format(r.value)}${r.note ? ` — ${r.note}` : ""}`}>
          <span className="sub">{human(r.label)}</span>
          <span className="bar-track">
            <span className="bar-fill" style={{ display: "block", width: `${(r.value / top) * 100}%` }} />
          </span>
          <span className="val">{format(r.value)}</span>
        </div>
      ))}
    </div>
  );
}

export function ErrorNote({ error }: { error: string | null }) {
  if (!error) return null;
  return <div className="banner bad">Could not load data: {error}</div>;
}
