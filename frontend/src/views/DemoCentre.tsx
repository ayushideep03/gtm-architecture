import { ReactNode, useContext, useEffect, useMemo, useState } from "react";
import { api, qs } from "../api";
import type { Nav, View } from "../App";
import { useAuth, useCan } from "../auth";
import { clock, Empty, ErrorNote, human, LiveContext, Loading, Status, useAction, useConfirm, useData } from "../ui";
import "./DemoCentre.css";

interface ControlStatus {
  profile: string;
  presenter: string;
  role: string;
  clock_offset_hours: number;
  autonomy_paused: boolean;
  dataset_loaded: boolean;
  leads: number;
  campaigns: { id: string; name: string; status: string }[];
  scenarios: { key: string; title: string; company: string; summary: string }[];
  reply_types: string[];
}
interface Contact { lead_id: string; name: string; company: string | null; lead_status: string; title?: string | null }
interface LogEvent { id: string; type: string; source: string | null; lead_id: string | null; at: string; payload: Record<string, any> }
interface ScenarioResult {
  scenario: string; title?: string; lead_id?: string; completed: boolean;
  log: { at: string; step: string; ok: boolean; detail: any }[];
}
interface ResultPanel { title: string; tone: "good" | "warn" | "bad" | ""; body: ReactNode; leadId?: string }

const TRIGGERS: { kind: string; label: string; confirm?: string }[] = [
  { kind: "interested", label: "Trigger reply (interested)" },
  { kind: "meeting_request", label: "Trigger meeting request" },
  { kind: "objection", label: "Trigger objection" },
  { kind: "question", label: "Trigger question" },
  { kind: "unsupported_question", label: "Trigger unsupported question" },
  { kind: "out_of_office", label: "Trigger out-of-office" },
  { kind: "unsubscribe", label: "Trigger unsubscribe",
    confirm: "The prospect will be suppressed, consent withdrawn and pending follow-ups cancelled. Nothing more will be sent to them." },
];

const CLOCK_STEPS = [
  { hours: 1, label: "+1 hour" },
  { hours: 24, label: "+1 day" },
  { hours: 72, label: "+3 days" },
  { hours: 168, label: "+7 days" },
];

const SCENARIO_LINKS: Record<string, { view: View; label: string }[]> = {
  successful_conversion: [{ view: "meetings", label: "Meetings" }],
  meeting_request: [{ view: "meetings", label: "Meetings" }],
  objection: [{ view: "inbox", label: "Inbox" }],
  question: [{ view: "inbox", label: "Inbox" }],
  unsupported_question: [{ view: "inbox", label: "Inbox" }],
  no_reply: [{ view: "followups", label: "Follow-ups" }],
  unsubscribe: [{ view: "inbox", label: "Inbox" }, { view: "contacts", label: "Contacts" }],
};

/** Scenarios required in the presentation, in story order; unknown keys from the backend follow. */
const SCENARIO_ORDER = ["successful_conversion", "objection", "question", "unsupported_question", "meeting_request",
  "no_reply", "unsubscribe"];

function formatOffset(h?: number | null): string {
  if (!h) return "+0 h";
  const r = Math.round(h);
  if (Math.abs(r) < 24) return `+${r} h`;
  const d = Math.floor(r / 24), rest = r % 24;
  return `+${r} h (${d} d${rest ? ` ${rest} h` : ""})`;
}

function short(v: any, max = 140): string {
  if (v === null || v === undefined || v === "") return "";
  const s = typeof v === "string" ? v : JSON.stringify(v);
  return s.length > max ? s.slice(0, max - 1) + "…" : s;
}

function shifted(v: any): number {
  if (typeof v === "number") return v;
  if (v && typeof v === "object") return Object.values(v).reduce<number>((a, n) => a + (Number(n) || 0), 0);
  return 0;
}

const HIDDEN_DETAIL = new Set(["lead_id", "approval_id"]);
function detailText(d: any): string {
  if (d === null || d === undefined) return "";
  if (typeof d !== "object") return String(d);
  return Object.entries(d)
    .filter(([k]) => !HIDDEN_DETAIL.has(k))
    .filter(([, v]) => v !== null && v !== undefined && !(Array.isArray(v) && !v.length))
    .map(([k, v]) => `${human(k)}: ${Array.isArray(v) ? v.map((x) => (typeof x === "string" ? human(x) : short(x, 60))).join(", ") : typeof v === "object" ? short(v, 100) : human(String(v))}`)
    .join(" · ");
}

const PAYLOAD_KEYS = ["action", "task_type", "status", "capability", "category", "kind", "channel", "provider", "reason", "subject", "step", "actor"];
function payloadSummary(p: Record<string, any>): string {
  if (!p || typeof p !== "object") return "";
  const picked = PAYLOAD_KEYS.filter((k) => p[k] !== undefined && p[k] !== null && typeof p[k] !== "object")
    .map((k) => `${human(k)}: ${typeof p[k] === "string" && k !== "subject" && k !== "reason" ? human(p[k]) : p[k]}`);
  if (picked.length) return short(picked.join(" · "), 180);
  const rest = Object.entries(p).filter(([, v]) => v !== null && v !== undefined).slice(0, 3)
    .map(([k, v]) => `${human(k)}: ${short(v, 50)}`);
  return short(rest.join(" · "), 180);
}

export default function DemoCentre({ nav }: { nav: Nav }) {
  const live = useContext(LiveContext);
  const { organisation } = useAuth();
  const can = useCan();
  const confirm = useConfirm();
  const { busy, run } = useAction();
  const { data: st, error, loading, reload } = useData<ControlStatus>("/demo/control/status");

  const [clockResult, setClockResult] = useState<ResultPanel | null>(null);
  const [envResult, setEnvResult] = useState<ResultPanel | null>(null);
  const [triggerResult, setTriggerResult] = useState<ResultPanel | null>(null);
  const [scenarioResults, setScenarioResults] = useState<Record<string, ScenarioResult>>({});
  const [autoApprove, setAutoApprove] = useState<Record<string, boolean>>({});

  // prospect picker (server-side search, debounced)
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  useEffect(() => {
    const t = window.setTimeout(() => setQuery(search.trim()), 300);
    return () => window.clearTimeout(t);
  }, [search]);
  const contacts = useData<{ items: Contact[]; total: number }>(
    st?.dataset_loaded ? `/contacts${qs({ page_size: 200, search: query })}` : null, [st?.dataset_loaded]);
  const [picked, setPicked] = useState<Contact | null>(null);
  // keep the picked prospect's stage current as the pipeline moves
  useEffect(() => {
    if (!picked) return;
    const fresh = contacts.data?.items.find((c) => c.lead_id === picked.lead_id);
    if (fresh && fresh.lead_status !== picked.lead_status) setPicked(fresh);
  }, [contacts.data, picked]);

  // workflow logs
  const [logType, setLogType] = useState("");
  const logs = useData<LogEvent[]>(`/workflow/logs${qs({ limit: 100, event_type: logType })}`, [logType]);
  const [knownTypes, setKnownTypes] = useState<string[]>([]);
  useEffect(() => {
    if (!logs.data?.length) return;
    setKnownTypes((prev) => {
      const s = new Set(prev);
      logs.data!.forEach((e) => s.add(e.type));
      return s.size === prev.length ? prev : Array.from(s).sort();
    });
  }, [logs.data]);

  const scenarios = useMemo(() => {
    const list = st?.scenarios ?? [];
    const rank = (k: string) => { const i = SCENARIO_ORDER.indexOf(k); return i < 0 ? 99 : i; };
    return [...list].sort((a, b) => rank(a.key) - rank(b.key));
  }, [st?.scenarios]);

  if (loading && !st) return <><Head /><Loading label="Loading demo environment…" /></>;
  if (error && !st) {
    const notDemo = /disabled|not found|404/i.test(error);
    return (
      <>
        <Head />
        {notDemo
          ? <div className="card"><Empty title="Demo controls are only available in demo mode"
              hint="This server runs a production profile. Start it with GTM_PROFILE=demo to present the demo environment." /></div>
          : <ErrorNote error={error} />}
      </>
    );
  }
  if (!st) return null;

  const presenter = st.presenter;
  const canRun = can.operate;
  const canAutoApprove = st.role === "admin" || st.role === "approver";
  const anyBusy = busy !== null;
  const refresh = () => { reload(); logs.reload(); contacts.reload(); };

  // ── environment actions ──────────────────────────────────────────────────
  const load = () => run("load", () => api.post<any>("/demo/load"), (r) =>
    `Demo dataset loaded for ${(r.loaded ?? []).length} organisation(s)`).then((r) => {
    if (r) {
      setEnvResult({ title: "Demo dataset loaded", tone: "good", body: (
        <ul className="sub" style={{ margin: 0, paddingLeft: 18 }}>
          {(r.loaded ?? []).map((o: any, i: number) => (
            <li key={i}><b>{o.organisation}</b>: campaign {o.campaign}, {o.accounts} target accounts, {o.users} users</li>
          ))}
        </ul>) });
      refresh();
    }
  });

  const reset = async () => {
    const ok = await confirm({
      title: "Reset the demo environment?", danger: true, confirm: "Delete and reload",
      body: (
        <div className="grid" style={{ gap: 8 }}>
          <div>This permanently deletes <b>all data of both demo organisations</b>: campaigns, companies, contacts, leads,
            research, drafts, approvals, emails, replies, follow-ups, meetings, workflow logs and the demo clock.</div>
          <div>The original demo dataset is then loaded again, so the pipeline starts from the beginning.
            Demo users and organisations are kept. Non-demo organisations are not touched.</div>
          <div className="sub">This cannot be undone.</div>
        </div>),
    });
    if (!ok) return;
    const r = await run("reset", () => api.post<any>("/demo/reset"), "Demo environment reset and reloaded");
    if (r) {
      const deleted = Object.entries(r.deleted ?? {}).filter(([, n]) => Number(n) > 0) as [string, number][];
      const total = deleted.reduce((a, [, n]) => a + Number(n), 0);
      setEnvResult({ title: `Reset complete: ${total} rows deleted, dataset reloaded`, tone: "good", body: (
        <div className="chip-row">
          {deleted.length ? deleted.map(([t, n]) => <span className="badge" key={t}>{human(t)} {n}</span>)
            : <span className="sub">Nothing needed deleting.</span>}
        </div>) });
      setPicked(null);
      setScenarioResults({});
      setClockResult(null);
      setTriggerResult(null);
      refresh();
    }
  };

  const start = async () => {
    const ok = await confirm({
      title: "Start the complete pipeline?", confirm: "Start pipeline",
      body: <div>Oxygen will discover target accounts and contacts for the demo campaign of <b>{organisation ?? "your organisation"}</b>,
        then research, qualify and draft outreach autonomously. Every outbound message still stops at the Approval Centre.</div>,
    });
    if (!ok) return;
    const r = await run("start", () => api.post<any>("/demo/start"), (r) => `Pipeline started: ${r.leads_created ?? 0} lead(s) created`);
    if (r) {
      setEnvResult({ title: `Pipeline started for ${r.campaign}`, tone: "good", body: (
        <div className="chip-row">
          <Status value={r.status} />
          <span className="badge">{r.accounts ?? 0} accounts</span>
          <span className="badge">{r.contacts ?? 0} contacts</span>
          <span className="badge">{r.leads_created ?? 0} leads created</span>
          {r.provider && <span className="badge">provider: {r.provider}</span>}
        </div>) });
      refresh();
    }
  };

  const pause = async () => {
    const ok = await confirm({
      title: "Pause all background workflow?", confirm: "Pause workflow",
      body: <div>Nothing will run automatically for any organisation until it is resumed: no research, drafts, sends or
        follow-ups. Manual triggers below still work. Recorded under <b>{presenter}</b>.</div>,
    });
    if (!ok) return;
    if (await run("pause", () => api.post("/autonomy/pause", { actor: presenter }), "Workflow paused")) refresh();
  };
  const resume = () => run("resume", () => api.post("/autonomy/resume", { actor: presenter }), "Workflow resumed")
    .then((r) => r && refresh());

  const tick = () => run("tick", () => api.post<any>("/demo/tick"), (r) =>
    r.paused ? "Workflow is paused: the tick did nothing" : `Worker tick: ${r.cycles?.length ?? 0} decision(s), ${r.tasks_executed} task(s)`)
    .then((r) => {
      if (!r) return;
      setEnvResult({ title: r.paused ? "Worker tick skipped: workflow is paused" : "Worker tick complete", tone: r.paused ? "warn" : "good", body: (
        <div className="grid" style={{ gap: 8 }}>
          <div className="chip-row">
            <span className="badge">{r.cycles?.length ?? 0} decisions</span>
            <span className="badge">{r.tasks_executed ?? 0} tasks executed</span>
            <span className="badge">{r.reactions ?? 0} reactions</span>
            <span className="badge">{r.follow_ups_due ?? 0} follow-ups due</span>
            {!!r.held && <span className="badge warn">{r.held} held</span>}
            {!!r.recovered && <span className="badge">{r.recovered} recovered</span>}
          </div>
          {!!r.cycles?.length && (
            <ul className="sub" style={{ margin: 0, paddingLeft: 18 }}>
              {r.cycles.slice(0, 8).map((c: any, i: number) => (
                <li key={i}>
                  <button className="linkish" onClick={() => nav.openLead(c.lead_id)}>lead {String(c.lead_id).slice(0, 8)}</button>{" "}
                  {human(c.action)} · {human(c.status)}
                </li>
              ))}
              {r.cycles.length > 8 && <li>and {r.cycles.length - 8} more</li>}
            </ul>
          )}
        </div>) });
      refresh();
    });

  const advance = (hours: number, label: string) => run(`clock:${hours}`, () => api.post<any>("/demo/clock/advance", { hours }),
    (r) => `Demo clock advanced ${label}: ${r.follow_ups_due} follow-up(s) due`).then((r) => {
    if (!r) return;
    setClockResult({ title: `Clock advanced ${label}`, tone: "good", body: (
      <div className="chip-row">
        <span className="badge blue">now {formatOffset(r.total_offset_hours)}</span>
        <span className="badge">{shifted(r.rows_shifted)} timestamps shifted</span>
        <span className="badge">{r.follow_ups_due ?? 0} follow-ups due</span>
      </div>) });
    refresh();
  });

  // ── prospect triggers ────────────────────────────────────────────────────
  const trigger = async (t: (typeof TRIGGERS)[number]) => {
    if (!picked) return;
    if (t.confirm) {
      const ok = await confirm({ title: `${t.label} for ${picked.name}?`, body: <div>{t.confirm}</div>, confirm: "Trigger", danger: true });
      if (!ok) return;
    }
    const r = await run(`reply:${t.kind}`, () => api.post<any>(`/demo/leads/${picked.lead_id}/reply`, { kind: t.kind }),
      (r) => r.matched ? `Reply classified as ${human(r.classification?.category ?? "unknown")}` : "Reply was not matched to a prospect");
    if (!r) return;
    setTriggerResult({
      title: `${human(t.kind)} reply from ${picked.name}`, tone: r.matched ? "good" : "warn", leadId: picked.lead_id,
      body: (
        <dl className="kv">
          <dt>Matched</dt><dd>{r.matched ? <span className="badge good">matched to prospect</span> : <span className="badge warn">not matched</span>}</dd>
          <dt>Classification</dt><dd>{r.classification?.category ? <span className="badge blue">{human(r.classification.category)}</span> : <span className="muted">—</span>}</dd>
          {r.classification?.summary && <><dt>Summary</dt><dd>{r.classification.summary}</dd></>}
          <dt>Actions taken</dt>
          <dd>{r.actions?.length ? <div className="chip-row">{r.actions.map((a: string, i: number) => <span className="badge" key={i}>{human(a)}</span>)}</div> : <span className="muted">none</span>}</dd>
        </dl>),
    });
    contacts.reload();
    logs.reload();
  };

  const simulateFollowUp = async () => {
    const a = await run("followup", () => api.post<any>("/demo/clock/advance", { hours: 72 }));
    if (!a) return;
    const t = await run("followup", () => api.post<any>("/demo/tick"),
      (t) => t.paused ? "Clock advanced, but the workflow is paused" : `Follow-ups: ${a.follow_ups_due} due after +3 days`);
    setTriggerResult({
      title: "Simulated follow-up: clock +3 days, worker tick run", tone: t?.paused ? "warn" : "good", leadId: picked?.lead_id,
      body: (
        <div className="grid" style={{ gap: 6 }}>
          <div className="chip-row">
            <span className="badge blue">clock now {formatOffset(a.total_offset_hours)}</span>
            <span className="badge">{a.follow_ups_due ?? 0} follow-ups due</span>
            {t && <span className="badge">{t.tasks_executed ?? 0} tasks executed</span>}
            {t && <span className="badge">{t.cycles?.length ?? 0} decisions</span>}
          </div>
          {t?.paused && <div className="sub">Workflow is paused: resume it so the follow-ups are drafted.</div>}
          <div className="sub">Follow-up drafts still stop at the Approval Centre before anything is sent.</div>
        </div>),
    });
    refresh();
  };

  // ── scenarios ────────────────────────────────────────────────────────────
  const runScenario = async (key: string, title: string) => {
    const auto = autoApprove[key] ?? true;
    const r = await run(`scenario:${key}`, () => api.post<ScenarioResult>(`/demo/scenarios/${key}/run`, { auto_approve: auto }),
      (r) => r.completed ? `Scenario complete: ${title}` : `Scenario stopped: ${title} (see the step log)`);
    if (r) {
      setScenarioResults((s) => ({ ...s, [key]: r }));
      refresh();
    }
  };
  const leadOf = (r?: ScenarioResult) => r?.lead_id ?? r?.log.find((l) => l.detail && typeof l.detail === "object" && l.detail.lead_id)?.detail.lead_id;

  return (
    <div className="dc">
      <Head connected={live.connected} />
      <div className="banner warn" role="note">
        <span aria-hidden>⚠</span>
        <div><b>Demo mode: fictional data, simulated providers, nothing leaves this system.</b> Emails, replies, calendar invitations
          and AI output are simulated and labelled DEMO.</div>
      </div>

      <div className="status-strip">
        <div className="card">
          <span className="lbl">Presenter</span>
          <span className="val">{presenter}</span>
          <span className="row"><span className="badge blue">{st.role}</span><span className="sub">{organisation ?? "—"}</span></span>
        </div>
        <div className="card">
          <span className="lbl">Demo clock</span>
          <span className="val">{formatOffset(st.clock_offset_hours)}</span>
          <span className="sub">ahead of real time for this organisation</span>
        </div>
        <div className="card">
          <span className="lbl">Autonomous workflow</span>
          <span className="val">{st.autonomy_paused ? "Paused" : "Running"}</span>
          <span>{st.autonomy_paused ? <span className="badge warn">nothing runs automatically</span> : <span className="badge good">worker active</span>}</span>
        </div>
        <div className="card">
          <span className="lbl">Demo dataset</span>
          <span className="val">{st.dataset_loaded ? "Loaded" : "Not loaded"}</span>
          <span className="sub">{st.campaigns.length} campaign(s) · {st.leads} lead(s) · profile {st.profile}</span>
        </div>
      </div>

      {!canRun && (
        <div className="banner info">
          <span aria-hidden>ℹ</span>
          <div>Your role ({st.role}) can watch the demo but not run it. Sign in as an admin or operator to use these controls.</div>
        </div>
      )}

      {/* ── Environment ── */}
      <div className="grid g2" style={{ alignItems: "start", marginBottom: 14 }}>
        <div className="card">
          <h2>Environment <small>dataset, pipeline and worker</small></h2>
          <div className="actions">
            {can.admin && (
              <button className="btn big" disabled={anyBusy} onClick={load}>
                {busy === "load" ? <><span className="spinner" aria-hidden /> Loading…</> : "Load demo dataset"}
              </button>
            )}
            {can.admin && (
              <button className="btn big danger" disabled={anyBusy} onClick={reset}>
                {busy === "reset" ? <><span className="spinner" aria-hidden /> Resetting…</> : "Reset demo environment"}
              </button>
            )}
            {canRun && (
              <button className="btn big primary" disabled={anyBusy || !st.dataset_loaded} onClick={start}
                      title={st.dataset_loaded ? undefined : "Load the demo dataset first"}>
                {busy === "start" ? <><span className="spinner" aria-hidden /> Starting…</> : "Start complete pipeline"}
              </button>
            )}
            {canRun && (st.autonomy_paused
              ? <button className="btn big good" disabled={anyBusy} onClick={resume}>{busy === "resume" ? "Resuming…" : "Resume workflow"}</button>
              : <button className="btn big" disabled={anyBusy} onClick={pause}>{busy === "pause" ? "Pausing…" : "Pause workflow"}</button>)}
            {canRun && (
              <button className="btn big" disabled={anyBusy} onClick={tick}>
                {busy === "tick" ? <><span className="spinner" aria-hidden /> Running…</> : "Run worker tick now"}
              </button>
            )}
          </div>
          {!can.admin && canRun && <p className="sub" style={{ marginBottom: 0 }}>Loading and resetting the dataset needs the admin role.</p>}
          {envResult && <ResultBox r={envResult} onClose={() => setEnvResult(null)} nav={nav} />}

          {!!st.campaigns.length && (
            <>
              <div className="subhead">Campaigns</div>
              {st.campaigns.map((c) => (
                <div className="camp" key={c.id}>
                  <span className="row">
                    <button className="linkish" onClick={() => nav.go("campaigns", { campaign: c.id })}>{c.name}</button>
                    <Status value={c.status} />
                  </span>
                  {canRun && c.status !== "stopped" && (
                    <span className="row">
                      {c.status === "active"
                        ? <button className="btn sm" disabled={anyBusy} onClick={() => run(`camp:${c.id}`,
                            () => api.post(`/campaigns/${c.id}/status`, { status: "paused" }), `${c.name} paused`).then((r) => r && refresh())}>Pause</button>
                        : <button className="btn sm" disabled={anyBusy} onClick={() => run(`camp:${c.id}`,
                            () => api.post(`/campaigns/${c.id}/status`, { status: "active" }), `${c.name} active`).then((r) => r && refresh())}>Activate</button>}
                      <button className="btn sm danger" disabled={anyBusy} onClick={async () => {
                        if (!(await confirm({ title: `Stop ${c.name}?`, danger: true, confirm: "Stop campaign",
                          body: <div>A stopped campaign sends nothing more and cannot be restarted from here. Use Reset demo environment to start over.</div> }))) return;
                        if (await run(`camp:${c.id}`, () => api.post(`/campaigns/${c.id}/status`, { status: "stopped" }), `${c.name} stopped`)) refresh();
                      }}>Stop</button>
                    </span>
                  )}
                </div>
              ))}
            </>
          )}
        </div>

        <div className="card">
          <h2>Demo clock <small>now {formatOffset(st.clock_offset_hours)}</small></h2>
          <p className="sub section-note">Moves time forward for this organisation only, so delays such as follow-up waits elapse
            during the presentation. The worker then runs twice.</p>
          {canRun ? (
            <div className="actions">
              {CLOCK_STEPS.map((c) => (
                <button key={c.hours} className="btn big" disabled={anyBusy} onClick={() => advance(c.hours, c.label)}>
                  {busy === `clock:${c.hours}` ? <><span className="spinner" aria-hidden /> Advancing…</> : c.label}
                </button>
              ))}
            </div>
          ) : <div className="sub">Advancing the clock needs the admin or operator role.</div>}
          {clockResult && <ResultBox r={clockResult} onClose={() => setClockResult(null)} nav={nav} />}
        </div>
      </div>

      {/* ── Prospect triggers ── */}
      <div className="card" style={{ marginBottom: 14 }}>
        <h2>Prospect triggers <small>simulated replies go through the real inbound pipeline</small></h2>
        {!st.dataset_loaded ? (
          <Empty title="No prospects yet" hint="Load the demo dataset and start the pipeline first." />
        ) : (
          <>
            <div className="picker">
              <input className="search" type="search" placeholder="Search name, company or email" aria-label="Search prospects"
                     value={search} onChange={(e) => setSearch(e.target.value)} />
              <select aria-label="Prospect" value={picked?.lead_id ?? ""}
                      onChange={(e) => setPicked(contacts.data?.items.find((c) => c.lead_id === e.target.value) ?? null)}>
                <option value="">
                  {contacts.loading ? "Loading prospects…" : contacts.data?.items.length
                    ? `Choose a prospect (${contacts.data.total} found)` : "No prospects match"}
                </option>
                {picked && !contacts.data?.items.some((c) => c.lead_id === picked.lead_id) && (
                  <option value={picked.lead_id}>{picked.name} · {picked.company ?? "—"} · {human(picked.lead_status)}</option>
                )}
                {contacts.data?.items.map((c) => (
                  <option key={c.lead_id} value={c.lead_id}>{c.name || "Unnamed"} · {c.company ?? "—"} · {human(c.lead_status)}</option>
                ))}
              </select>
            </div>
            {contacts.error && <ErrorNote error={contacts.error} />}
            {!contacts.loading && !contacts.data?.total && !query && (
              <div className="sub" style={{ marginTop: 8 }}>No contacts discovered yet: start the complete pipeline, then run a worker tick.</div>
            )}
            {picked && (
              <div className="picked">
                <b>{picked.name}</b>
                <span className="sub">{picked.title ? `${picked.title}, ` : ""}{picked.company ?? "—"}</span>
                <Status value={picked.lead_status} />
                <button className="btn sm" onClick={() => nav.openLead(picked.lead_id)}>Open prospect</button>
              </div>
            )}
            <div className="actions" style={{ marginTop: 12 }}>
              {TRIGGERS.map((t) => (
                <button key={t.kind} className={`btn big ${t.kind === "unsubscribe" ? "danger" : ""}`}
                        disabled={!picked || !canRun || anyBusy} onClick={() => trigger(t)}>
                  {busy === `reply:${t.kind}` ? <><span className="spinner" aria-hidden /> Sending…</> : t.label}
                </button>
              ))}
              <button className="btn big" disabled={!canRun || anyBusy} onClick={simulateFollowUp}
                      title="Advances the demo clock 3 days, then runs the worker so due follow-ups are drafted">
                {busy === "followup" ? <><span className="spinner" aria-hidden /> Simulating…</> : "Simulate follow-up (+3 days)"}
              </button>
            </div>
            {!picked && <p className="sub" style={{ marginBottom: 0 }}>Choose a prospect to enable the reply triggers.</p>}
            {triggerResult && <ResultBox r={triggerResult} onClose={() => setTriggerResult(null)} nav={nav} links={[{ view: "inbox", label: "Inbox" }, { view: "approvals", label: "Approval Centre" }]} />}
          </>
        )}
      </div>

      {/* ── Scenarios ── */}
      <h2 className="subhead" style={{ marginTop: 4 }}>Predefined scenarios</h2>
      <p className="sub section-note" style={{ marginTop: 0 }}>
        Each scenario drives one fictional prospect end to end. Auto-approval only works for the admin and approver roles and
        is recorded under your name ({presenter}); otherwise the run stops at the approval checkpoint for someone to approve in the Approval Centre.
        A run takes about 5 to 20 seconds.
      </p>
      {!scenarios.length ? (
        <div className="card" style={{ marginBottom: 14 }}><Empty title="No scenarios available" /></div>
      ) : (
        <div className="grid g3" style={{ marginBottom: 14, alignItems: "start" }}>
          {scenarios.map((s) => {
            const r = scenarioResults[s.key];
            const lead = leadOf(r);
            const running = busy === `scenario:${s.key}`;
            const auto = autoApprove[s.key] ?? true;
            return (
              <div className="card scenario" key={s.key}>
                <h2>{s.title} {r && (r.completed ? <span className="badge good">completed</span> : <span className="badge warn">stopped</span>)}</h2>
                <div className="target">{s.company}</div>
                <div className="sub">{s.summary}</div>
                {canRun && (
                  <>
                    <label className="check">
                      <input type="checkbox" checked={auto} disabled={anyBusy}
                             onChange={(e) => setAutoApprove((a) => ({ ...a, [s.key]: e.target.checked }))} />
                      Auto-approve as {presenter}
                    </label>
                    {auto && !canAutoApprove && (
                      <div className="sub">Your role ({st.role}) cannot approve: the run will stop at the first approval checkpoint.</div>
                    )}
                    <button className="btn big primary" disabled={anyBusy} onClick={() => runScenario(s.key, s.title)}>
                      {running ? <><span className="spinner" aria-hidden /> Running scenario… (5–20 s)</> : r ? "Run scenario again" : "Run scenario"}
                    </button>
                  </>
                )}
                {r && (
                  <div className="result" style={{ marginTop: 0 }}>
                    <ol className="steps">
                      {r.log.map((l, i) => (
                        <li key={i} className={l.ok ? "ok" : "fail"}>
                          <span className="mark" aria-label={l.ok ? "succeeded" : "did not succeed"}>{l.ok ? "✓" : "✗"}</span>
                          <span>
                            <span>{l.step}</span>
                            {detailText(l.detail) && <div className="detail">{short(detailText(l.detail), 260)}</div>}
                          </span>
                        </li>
                      ))}
                    </ol>
                    <div className="row">
                      {lead && <button className="btn sm" onClick={() => nav.openLead(lead)}>Open prospect</button>}
                      {(SCENARIO_LINKS[s.key] ?? []).map((l) => (
                        <button key={l.view} className="btn sm" onClick={() => nav.go(l.view)}>{l.label}</button>
                      ))}
                      {!r.completed && <button className="btn sm" onClick={() => nav.go("approvals")}>Approval Centre</button>}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* ── Workflow logs ── */}
      <div className="card">
        <h2>
          Workflow execution log <small><span className={`live-dot ${live.connected ? "" : "off"}`} /> {live.connected ? "live" : "offline"} · latest 100</small>
        </h2>
        <div className="toolbar">
          <select aria-label="Filter by event type" value={logType} onChange={(e) => setLogType(e.target.value)}>
            <option value="">All event types</option>
            {knownTypes.map((t) => <option key={t} value={t}>{human(t)}</option>)}
          </select>
          <button className="btn sm" onClick={logs.reload}>Refresh</button>
        </div>
        <ErrorNote error={logs.error} />
        {logs.loading && !logs.data ? <Loading /> : !logs.data?.length ? (
          <Empty title={logType ? `No ${human(logType)} events yet` : "No workflow events yet"}
                 hint="Start the pipeline or run a scenario; events appear here as they happen." />
        ) : (
          <div className="logs">
            {logs.data.map((e) => (
              <div className="log-row" key={e.id}>
                <span className="t" title={e.at}>{clock(e.at)}</span>
                <span>
                  <b>{human(e.type)}</b>
                  {e.source && <span className="sub"> · {human(e.source)}</span>}
                  {e.lead_id && <> · <button className="linkish" onClick={() => nav.openLead(e.lead_id!)}>lead {e.lead_id.slice(0, 8)}</button></>}
                  {payloadSummary(e.payload) && <div className="payload">{payloadSummary(e.payload)}</div>}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function Head({ connected }: { connected?: boolean }) {
  return (
    <div className="page-head">
      <div>
        <h1>Demo Control Centre</h1>
        <p>Run the autonomous sales pipeline live: load data, trigger prospect behaviour and play complete scenarios.</p>
      </div>
      {connected !== undefined && (
        <span className="sub"><span className={`live-dot ${connected ? "" : "off"}`} /> {connected ? "live updates on" : "live updates offline"}</span>
      )}
    </div>
  );
}

function ResultBox({ r, onClose, nav, links }: { r: ResultPanel; onClose: () => void; nav: Nav; links?: { view: View; label: string }[] }) {
  return (
    <div className="result" role="status">
      <h3>
        <span className="row">
          {r.tone && <span className={`badge ${r.tone}`}>{r.tone === "good" ? "done" : r.tone === "warn" ? "check" : "failed"}</span>}
          {r.title}
        </span>
        <button className="btn sm" onClick={onClose} aria-label="Dismiss result">✕</button>
      </h3>
      {r.body}
      {(r.leadId || links?.length) && (
        <div className="row">
          {r.leadId && <button className="btn sm" onClick={() => nav.openLead(r.leadId!)}>Open prospect</button>}
          {links?.map((l) => <button key={l.view} className="btn sm" onClick={() => nav.go(l.view)}>{l.label}</button>)}
        </div>
      )}
    </div>
  );
}
