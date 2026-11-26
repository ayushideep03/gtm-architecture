import { Fragment, useState } from "react";
import { api } from "../api";
import type { Nav } from "../App";
import { useCan } from "../auth";
import { DemoTag, Empty, ErrorNote, human, Loading, Status, useAction, useData } from "../ui";
import "./Integrations.css";

interface Integration {
  key: string; name: string; type: string; status: string; mode: string;
  demo_provider: string | null; live_provider: string | null; live_configured: boolean;
  requirements: string[]; optional: string[]; missing: string[]; setup: string[]; note: string | null; error: string | null;
}
interface TestResult { key: string; ok: boolean; simulated: boolean; message: string; detail?: any }

const LEGEND: { status: string; text: string }[] = [
  { status: "Demo Active", text: "A simulated provider is in use. Nothing is sent to an external service." },
  { status: "Live Ready", text: "Live credentials are configured on the server and the live provider is in use." },
  { status: "Live Not Configured", text: "A live adapter exists but its environment variables are not set." },
  { status: "Integration Pending", text: "No live adapter yet; the demo provider defines the interface it will implement." },
  { status: "Error", text: "The provider failed to start. See the error on its card." },
];

export default function Integrations({ nav }: { nav: Nav }) {
  void nav;
  const can = useCan();
  const { busy, run } = useAction();
  const { data, error, loading } = useData<{ profile: string; demo_mode: boolean; integrations: Integration[] }>("/integrations");
  const [results, setResults] = useState<Record<string, TestResult>>({});

  const test = (i: Integration) => run(`test:${i.key}`, () => api.post<TestResult>(`/integrations/${i.key}/test`),
    (r) => `${i.name}: ${r.simulated ? "simulated test " : ""}${r.ok ? "succeeded" : "failed"}`)
    .then((r) => r && setResults((s) => ({ ...s, [i.key]: r })));

  const list = data?.integrations ?? [];
  const counts = list.reduce<Record<string, number>>((a, i) => ({ ...a, [i.status]: (a[i.status] ?? 0) + 1 }), {});

  return (
    <div className="intg">
      <div className="page-head">
        <div>
          <h1>Integrations</h1>
          <p>Which external services Oxygen uses, whether they run live or simulated, and what each one needs.</p>
        </div>
        {data && <span className="row"><span className="sub">Profile</span><span className="badge blue">{data.profile}</span></span>}
      </div>

      {data?.demo_mode && (
        <div className="banner warn">
          <span aria-hidden>⚠</span>
          <div><b>Demo mode.</b> Every integration uses a simulated provider and connection tests are simulated. Live calls are
            disabled even where credentials are present.</div>
        </div>
      )}
      <div className="banner info">
        <span aria-hidden>🔒</span>
        <div>Live API keys and passwords are configured as <b>environment variables on the server</b> and read at start-up.
          They are never entered, stored or shown in the browser. Set the variables listed on each card, then restart the server.</div>
      </div>

      <ErrorNote error={error} />

      <div className="card" style={{ marginBottom: 14 }}>
        <h2>Status legend</h2>
        <div className="legend">
          {LEGEND.map((l) => (
            <div key={l.status} className="legend-item">
              <span className="row"><Status value={l.status} />{counts[l.status] ? <span className="sub">{counts[l.status]}</span> : null}</span>
              <span className="sub">{l.text}</span>
            </div>
          ))}
        </div>
      </div>

      {loading && !data ? <Loading label="Loading integrations…" /> : !list.length ? (
        !error && <div className="card"><Empty title="No integrations registered" hint="The server reported no integration catalogue." /></div>
      ) : (
        <div className="grid g2" style={{ alignItems: "start" }}>
          {list.map((i) => {
            const r = results[i.key];
            return (
              <div className="card intg-card" key={i.key}>
                <h2>
                  <span>{i.name} <span className="sub" style={{ fontWeight: 400 }}>· {i.type}</span></span>
                  <Status value={i.status} />
                </h2>
                <dl className="kv">
                  <dt>Mode</dt>
                  <dd>{i.mode === "Demo" ? <span className="row"><span className="badge warn">Demo</span><DemoTag title="Simulated provider: nothing leaves this system" /></span>
                    : i.mode === "Live" ? <span className="badge good">Live</span> : <span className="muted">Not in use</span>}</dd>
                  <dt>Demo provider</dt><dd className="mono">{i.demo_provider ?? "—"}</dd>
                  <dt>Live provider</dt><dd className="mono">{i.live_provider ?? <span className="muted">no live adapter yet</span>}</dd>
                  <dt>Live credentials</dt><dd>{i.live_configured ? <span className="badge good">configured</span> : <span className="badge">not configured</span>}</dd>
                  <dt>Required</dt>
                  <dd>
                    {i.requirements.length ? (
                      <div className="vars">
                        {i.requirements.map((v) => (
                          <span key={v} className={`var ${i.missing.includes(v) ? "missing" : "set"}`}>
                            <span className="mono">{v}</span>
                            {i.missing.includes(v) ? <span className="badge bad">missing</span> : <span className="badge good">set</span>}
                          </span>
                        ))}
                      </div>
                    ) : <span className="muted">none</span>}
                    {i.missing.filter((m) => !i.requirements.includes(m)).map((m) => (
                      <div key={m} className="var missing" style={{ marginTop: 4 }}><span className="mono">{m}</span><span className="badge bad">missing</span></div>
                    ))}
                  </dd>
                  {!!i.optional.length && <>
                    <dt>Optional</dt>
                    <dd><div className="vars">{i.optional.map((v) => <span key={v} className="var"><span className="mono">{v}</span></span>)}</div></dd>
                  </>}
                </dl>
                {i.note && <div className="sub note">{i.note}</div>}
                {i.error && <div className="banner bad" style={{ margin: 0 }}><span aria-hidden>✗</span><div className="mono">{i.error}</div></div>}
                {!!i.setup.length && (
                  <details className="setup">
                    <summary>Setup instructions</summary>
                    <ol>{i.setup.map((s, n) => <li key={n}>{s}</li>)}</ol>
                  </details>
                )}
                {can.admin && (
                  <div className="row">
                    <button className="btn" disabled={busy !== null} onClick={() => test(i)}>
                      {busy === `test:${i.key}` ? <><span className="spinner" aria-hidden /> Testing…</> : "Test connection"}
                    </button>
                  </div>
                )}
                {r && (
                  <div className={`test-result ${r.ok ? "ok" : "fail"}`} role="status">
                    <div className="row">
                      {r.ok ? <span className="badge good">passed</span> : <span className="badge bad">failed</span>}
                      {r.simulated
                        ? <span className="badge warn" title="Ran against the demo provider; no external service was contacted">Simulated</span>
                        : r.ok && <span className="badge" title="Checks configuration only; no email is sent and no paid API is called">configuration check</span>}
                    </div>
                    <div>{r.message}</div>
                    {r.detail && typeof r.detail === "object" && (
                      <dl className="kv">
                        {Object.entries(r.detail).slice(0, 8).map(([k, v]) => (
                          <Fragment key={k}><dt>{human(k)}</dt><dd className="sub">{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd></Fragment>
                        ))}
                      </dl>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
      {!can.admin && !!list.length && <p className="sub" style={{ marginTop: 14 }}>Connection tests are available to admins.</p>}
    </div>
  );
}
