import { createContext, FormEvent, ReactNode, useContext, useEffect, useState } from "react";

// Sign-in gate. The API key is exchanged for an HttpOnly cookie (POST /auth/login), so the
// dashboard never stores it; fetch and the live EventSource send the cookie automatically.

interface AuthState {
  principal: string | null;
  required: boolean;
  role: string | null;
  organisation: string | null;
  demoLogin: boolean;
  signOut: () => void;
}

const AuthContext = createContext<AuthState>({ principal: null, required: false, role: null, organisation: null,
                                                demoLogin: false, signOut: () => {} });

/** Can the signed-in role do this? (The backend enforces it; this only hides controls.) */
export function useCan() {
  const { role } = useAuth();
  return {
    approve: role === "admin" || role === "approver",
    operate: role === "admin" || role === "operator",
    admin: role === "admin",
  };
}
export const useAuth = () => useContext(AuthContext);

export function AuthGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<{ loading: boolean; required: boolean; demoLogin: boolean; principal: string | null;
                                       role: string | null; organisation: string | null;
                                       error?: string; unreachable?: boolean }>(
    { loading: true, required: false, demoLogin: false, principal: null, role: null, organisation: null });
  const [demoUsers, setDemoUsers] = useState<any[]>([]);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);

  const check = async () => {
    try {
      const r = await fetch("/api/v1/auth/me");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();
      setState({ loading: false, required: j.auth_required, demoLogin: j.demo_login, principal: j.principal,
                 role: j.role, organisation: j.organisation });
      if (j.demo_login && !j.principal) {
        const u = await fetch("/api/v1/auth/demo-users");
        if (u.ok) setDemoUsers(await u.json());
      }
    } catch {
      // Never fall through to "no sign-in needed" just because the server could not be asked.
      setState((s) => ({ ...s, loading: false, unreachable: true }));
    }
  };

  useEffect(() => {
    check();
    const onUnauthorized = () => setState((s) => ({ ...s, principal: null, required: true }));
    window.addEventListener("oxygen:unauthorized", onUnauthorized);
    return () => window.removeEventListener("oxygen:unauthorized", onUnauthorized);
  }, []);

  const signIn = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      const r = await fetch("/api/v1/auth/login", { method: "POST", headers: { "content-type": "application/json" },
                                                     body: JSON.stringify({ api_key: key }) });
      if (!r.ok) throw new Error(r.status === 401 ? "That key is not valid." : `Sign-in failed (${r.status}).`);
      setKey("");
      await check();
    } catch (err: any) {
      setState((s) => ({ ...s, error: err.message }));
    } finally {
      setBusy(false);
    }
  };

  const demoSignIn = async (userId: string) => {
    setBusy(true);
    try {
      const r = await fetch("/api/v1/auth/demo-login", { method: "POST", headers: { "content-type": "application/json" },
                                                          body: JSON.stringify({ user_id: userId }) });
      if (!r.ok) throw new Error(`Sign-in failed (${r.status}).`);
      await check();
      window.location.hash = "/overview";
    } catch (err: any) {
      setState((s) => ({ ...s, error: err.message }));
    } finally {
      setBusy(false);
    }
  };

  const signOut = async () => {
    await fetch("/api/v1/auth/logout", { method: "POST" }).catch(() => {});
    await check();
  };

  if (state.loading) return <div className="empty" style={{ paddingTop: 80 }}>Loading…</div>;
  if (state.unreachable) {
    return (
      <div className="login">
        <div className="card">
          <div className="banner bad">Cannot reach the Oxygen API. Check that the server is running.</div>
          <button className="btn primary" style={{ width: "100%" }}
                  onClick={() => { setState((s) => ({ ...s, loading: true, unreachable: false })); check(); }}>Retry</button>
        </div>
      </div>
    );
  }
  if (state.required && !state.principal) {
    const orgs = Array.from(new Set(demoUsers.map((u) => u.organisation)));
    const roleHelp: Record<string, string> = {
      admin: "Everything: campaigns, approvals, users, integrations, demo reset",
      operator: "Runs campaigns and workflows; cannot approve",
      approver: "Approves or rejects outreach; read-only otherwise",
    };
    return (
      <div className="login">
        <div className="card login-card">
          <div className="brand" style={{ padding: "0 0 12px" }}><span className="brand-mark" /><div><b>Oxygen</b><small>Autonomous AI Sales OS</small></div></div>
          {state.demoLogin && demoUsers.length > 0 && (
            <div className="grid" style={{ gap: 12 }}>
              <div className="banner warn" style={{ margin: 0 }}><span>⚠</span><div><b>Demo mode.</b> Fictional organisations and data; no email or API call leaves this system. Choose who to sign in as.</div></div>
              {orgs.map((o) => (
                <div key={o}>
                  <div className="nav-group" style={{ padding: "4px 0" }}>{o}</div>
                  <div className="grid" style={{ gap: 6 }}>
                    {demoUsers.filter((u) => u.organisation === o).map((u) => (
                      <button key={u.id} className="btn demo-user" disabled={busy} onClick={() => demoSignIn(u.id)}>
                        <span><b>{u.name}</b> <span className="badge">{u.role}</span></span>
                        <span className="sub">{roleHelp[u.role]}</span>
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
          {state.demoLogin && demoUsers.length === 0 && (
            <div className="banner info" style={{ margin: 0 }}>No demo users yet. Start with <span className="mono">python start.py</span> (it loads the demo dataset).</div>
          )}
          {!state.demoLogin && (
            <form className="form" onSubmit={signIn}>
              <label htmlFor="api-key">API key</label>
              <input id="api-key" className="text" type="password" autoComplete="current-password" value={key}
                     onChange={(e) => setKey(e.target.value)} autoFocus />
              <button className="btn primary" style={{ width: "100%" }} disabled={busy || !key}>Sign in</button>
            </form>
          )}
          {state.error && <div className="sub" style={{ color: "var(--bad)", marginTop: 6 }}>{state.error}</div>}
        </div>
      </div>
    );
  }
  return (
    <AuthContext.Provider value={{ principal: state.principal, required: state.required, role: state.role,
                                   organisation: state.organisation, demoLogin: state.demoLogin, signOut }}>
      {children}
    </AuthContext.Provider>
  );
}
