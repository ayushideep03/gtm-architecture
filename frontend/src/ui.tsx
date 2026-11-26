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
  active: "good", paused: "warn", draft: "", scheduled: "blue", due: "warn", drafted: "blue", sent: "good",
  submitted: "warn", superseded: "", invited: "blue", booked: "good", declined: "bad", requested: "warn",
  pending_approval: "warn", reschedule_requested: "warn", discovered: "good", discovering: "blue",
  verified: "good", unverified: "", guessed: "warn", invalid: "bad", strong: "good", good: "blue", weak: "warn",
  poor: "bad", "Demo Active": "blue", "Live Ready": "good", "Live Not Configured": "", "Integration Pending": "warn",
  Error: "bad", done: "good", skipped: "",
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


// ── shared building blocks ───────────────────────────────────────────────────

/** Visible label for simulated / fictional data. Never omit it on demo content. */
export function DemoTag({ title = "Simulated in demo mode — not a verified real-world fact" }: { title?: string }) {
  return <span className="badge demo" title={title}>DEMO</span>;
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return <div className="empty" aria-busy="true"><span className="spinner" aria-hidden /> {label}</div>;
}

export function Empty({ title, hint, action }: { title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="empty-state">
      <b>{title}</b>
      {hint && <div className="sub">{hint}</div>}
      {action && <div style={{ marginTop: 10 }}>{action}</div>}
    </div>
  );
}

export function Modal({ title, onClose, children, footer, wide }: {
  title: string; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="modal-bg" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-head"><h2>{title}</h2><button className="btn sm" onClick={onClose} aria-label="Close">✕</button></div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

type ConfirmOpts = { title: string; body?: ReactNode; confirm?: string; danger?: boolean };
const ConfirmContext = createContext<(o: ConfirmOpts) => Promise<boolean>>(async () => true);
export const useConfirm = () => useContext(ConfirmContext);
export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<(ConfirmOpts & { resolve: (v: boolean) => void }) | null>(null);
  const ask = useCallback((o: ConfirmOpts) => new Promise<boolean>((resolve) => setState({ ...o, resolve })), []);
  const done = (v: boolean) => { state?.resolve(v); setState(null); };
  return (
    <ConfirmContext.Provider value={ask}>
      {children}
      {state && (
        <Modal title={state.title} onClose={() => done(false)} footer={<>
          <button className="btn" onClick={() => done(false)}>Cancel</button>
          <button className={`btn ${state.danger ? "danger-solid" : "primary"}`} autoFocus onClick={() => done(true)}>
            {state.confirm ?? "Confirm"}
          </button></>}>
          {state.body}
        </Modal>
      )}
    </ConfirmContext.Provider>
  );
}

/** Run an action with busy state, success toast and error toast. */
export function useAction() {
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const run = useCallback(async <T,>(key: string, fn: () => Promise<T>, ok?: string | ((r: T) => string)) => {
    setBusy(key);
    try {
      const r = await fn();
      if (ok) toast(typeof ok === "function" ? ok(r) : ok);
      return r;
    } catch (e: any) {
      toast(e.message ?? String(e), true);
      return undefined;
    } finally {
      setBusy(null);
    }
  }, [toast]);
  return { busy, run };
}

export function Pager({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (total <= pageSize) return <div className="pager sub">{total} result{total === 1 ? "" : "s"}</div>;
  return (
    <div className="pager">
      <span className="sub">{(page - 1) * pageSize + 1}–{Math.min(page * pageSize, total)} of {total}</span>
      <button className="btn sm" disabled={page <= 1} onClick={() => onPage(page - 1)} aria-label="Previous page">‹</button>
      <span className="sub">Page {page} / {pages}</span>
      <button className="btn sm" disabled={page >= pages} onClick={() => onPage(page + 1)} aria-label="Next page">›</button>
    </div>
  );
}

export function Kpi({ v, l, hint, onClick }: { v: ReactNode; l: string; hint?: string; onClick?: () => void }) {
  return (
    <div className={`card kpi ${onClick ? "clickable" : ""}`} title={hint} onClick={onClick} role={onClick ? "button" : undefined}>
      <div className="v">{v}</div>
      <div className="l">{l}</div>
    </div>
  );
}

/** Two-series line chart (sent vs replies by day) with legend, direct end labels and hover tooltip. */
export function LineChart({ points, series, height = 180 }: {
  points: Record<string, any>[]; series: { key: string; label: string; color: string }[]; height?: number;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const w = 640, h = height, pad = { l: 32, r: 16, t: 12, b: 24 };
  const max = Math.max(1, ...points.flatMap((p) => series.map((s) => Number(p[s.key] ?? 0))));
  const x = (i: number) => pad.l + (i * (w - pad.l - pad.r)) / Math.max(1, points.length - 1);
  const y = (v: number) => pad.t + (1 - v / max) * (h - pad.t - pad.b);
  if (!points.length) return <div className="empty">No data yet</div>;
  return (
    <div className="linechart">
      <div className="legend">{series.map((s) => <span key={s.key}><i style={{ background: s.color }} />{s.label}</span>)}</div>
      <svg viewBox={`0 0 ${w} ${h}`} role="img" aria-label={series.map((s) => s.label).join(" and ") + " per day"}
           onMouseLeave={() => setHover(null)}>
        {[0, 0.5, 1].map((f) => (
          <g key={f}>
            <line x1={pad.l} x2={w - pad.r} y1={y(max * f)} y2={y(max * f)} className="grid" />
            <text x={pad.l - 6} y={y(max * f) + 4} className="axis" textAnchor="end">{Math.round(max * f)}</text>
          </g>
        ))}
        {points.map((p, i) => (i % Math.ceil(points.length / 7) === 0 || i === points.length - 1) && (
          <text key={i} x={x(i)} y={h - 6} className="axis" textAnchor="middle">{String(p.date).slice(5)}</text>
        ))}
        {series.map((s) => (
          <polyline key={s.key} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round"
                    points={points.map((p, i) => `${x(i)},${y(Number(p[s.key] ?? 0))}`).join(" ")} />
        ))}
        {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={pad.t} y2={h - pad.b} className="crosshair" />}
        {hover !== null && series.map((s) => (
          <circle key={s.key} cx={x(hover)} cy={y(Number(points[hover][s.key] ?? 0))} r={4} fill={s.color} stroke="var(--surface)" strokeWidth={2} />
        ))}
        {points.map((_, i) => (
          <rect key={i} x={x(i) - (w - pad.l - pad.r) / points.length / 2} y={0} width={(w - pad.l - pad.r) / points.length}
                height={h} fill="transparent" onMouseEnter={() => setHover(i)} />
        ))}
      </svg>
      {hover !== null && (
        <div className="tip">{points[hover].date}: {series.map((s) => `${s.label} ${points[hover][s.key] ?? 0}`).join(" · ")}</div>
      )}
    </div>
  );
}
