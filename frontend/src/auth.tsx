import { createContext, FormEvent, ReactNode, useContext, useEffect, useState } from "react";

// Sign-in gate. The API key is exchanged for an HttpOnly cookie (POST /auth/login), so the
// dashboard never stores it; fetch and the live EventSource send the cookie automatically.

interface AuthState {
  principal: string | null;
  required: boolean;
  signOut: () => void;
}

const AuthContext = createContext<AuthState>({ principal: null, required: false, signOut: () => {} });
export const useAuth = () => useContext(AuthContext);

export function AuthGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<{ loading: boolean; required: boolean; configured: boolean; principal: string | null; error?: string }>(
    { loading: true, required: false, configured: false, principal: null });
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);

  const check = async () => {
    try {
      const r = await fetch("/api/v1/auth/me");
      const j = await r.json();
      setState({ loading: false, required: j.auth_required, configured: j.configured, principal: j.principal });
    } catch {
      setState((s) => ({ ...s, loading: false, error: "Cannot reach the Oxygen API." }));
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

  const signOut = async () => {
    await fetch("/api/v1/auth/logout", { method: "POST" }).catch(() => {});
    await check();
  };

  if (state.loading) return <div className="empty" style={{ paddingTop: 80 }}>Loading…</div>;
  if (state.required && !state.principal) {
    return (
      <div className="login">
        <form className="card" onSubmit={signIn}>
          <div className="brand" style={{ padding: "0 0 12px" }}><span className="brand-mark" /><div><b>Oxygen</b><small>Autonomous AI Sales OS</small></div></div>
          {!state.configured ? (
            <div className="banner bad">Sign-in is not configured on this server. Set <span className="mono">OXYGEN_API_KEYS</span> and restart.</div>
          ) : (
            <>
              <label className="sub" htmlFor="api-key">API key</label>
              <input id="api-key" className="text" type="password" autoComplete="current-password" value={key}
                     onChange={(e) => setKey(e.target.value)} autoFocus />
              {state.error && <div className="sub" style={{ color: "var(--bad)", marginTop: 6 }}>{state.error}</div>}
              <button className="btn primary" style={{ marginTop: 12, width: "100%" }} disabled={busy || !key}>Sign in</button>
            </>
          )}
        </form>
      </div>
    );
  }
  return <AuthContext.Provider value={{ principal: state.principal, required: state.required, signOut }}>{children}</AuthContext.Provider>;
}
