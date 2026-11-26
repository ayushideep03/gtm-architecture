import { FormEvent, useState } from "react";
import { api } from "../api";
import type { Nav } from "../App";
import { useAuth, useCan } from "../auth";
import { ago, DemoTag, Empty, ErrorNote, Loading, Modal, useAction, useConfirm, useData, useToast } from "../ui";
import "./Organisation.css";

interface User {
  id: string; name: string; email: string; role: string; is_active: boolean; is_demo: boolean;
  has_api_key: boolean; created_at: string;
}
interface OrgData {
  organisation: { id: string; name: string; slug: string } | null;
  you: { name: string; role: string } | null;
  users: User[];
  roles: Record<string, string>;
}
type Role = "admin" | "operator" | "approver";
const ROLES: Role[] = ["admin", "operator", "approver"];
const ROLE_TONE: Record<string, string> = { admin: "blue", operator: "good", approver: "warn" };

export default function Organisation({ nav }: { nav: Nav }) {
  void nav;
  const can = useCan();
  const { principal } = useAuth();
  const confirm = useConfirm();
  const toast = useToast();
  const { busy, run } = useAction();
  const { data, error, loading, reload } = useData<OrgData>("/admin/organisation");
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState<{ name: string; email: string; role: Role }>({ name: "", email: "", role: "operator" });
  const [created, setCreated] = useState<{ name: string; email: string; role: string; api_key: string; note?: string } | null>(null);
  const [copied, setCopied] = useState(false);

  const youName = data?.you?.name ?? principal;
  const isYou = (u: User) => !!youName && u.name === youName && u.role === data?.you?.role;

  const addUser = async (e: FormEvent) => {
    e.preventDefault();
    const r = await run("add", () => api.post<any>("/admin/users", form), (r) => `${r.name} added`);
    if (r) {
      setAdding(false);
      setForm({ name: "", email: "", role: "operator" });
      setCopied(false);
      setCreated({ name: r.name, email: r.email, role: r.role, api_key: r.api_key, note: r.note });
      reload();
    }
  };

  const changeRole = async (u: User, role: Role) => {
    if (role === u.role) return;
    const ok = await confirm({
      title: `Change ${u.name}'s role to ${role}?`, confirm: "Change role",
      body: <div><b>{role}</b>: {data?.roles[role] ?? ""}{isYou(u) && role !== "admin" && <div className="sub" style={{ marginTop: 6 }}>You cannot remove your own admin access; the server will refuse this.</div>}</div>,
    });
    if (!ok) return;
    if (await run(`u:${u.id}`, () => api.patch(`/admin/users/${u.id}`, { role }), `${u.name} is now ${role}`)) reload();
  };

  const toggleActive = async (u: User) => {
    const deactivate = u.is_active;
    const ok = await confirm({
      title: `${deactivate ? "Deactivate" : "Reactivate"} ${u.name}?`, danger: deactivate,
      confirm: deactivate ? "Deactivate user" : "Reactivate user",
      body: deactivate
        ? <div>{u.name} will no longer be able to sign in or use their API key. Their past actions stay in the audit trail. You can reactivate them later.</div>
        : <div>{u.name} will be able to sign in again with their existing API key and role ({u.role}).</div>,
    });
    if (!ok) return;
    if (await run(`u:${u.id}`, () => api.patch(`/admin/users/${u.id}`, { is_active: !deactivate }),
      `${u.name} ${deactivate ? "deactivated" : "reactivated"}`)) reload();
  };

  const copyKey = async () => {
    if (!created) return;
    try {
      await navigator.clipboard.writeText(created.api_key);
      setCopied(true);
      toast("API key copied to the clipboard");
    } catch {
      toast("Could not copy automatically: select the key and copy it manually", true);
    }
  };

  const closeKey = async () => {
    if (!copied && !(await confirm({ title: "Close without copying the key?", danger: true, confirm: "Close",
      body: <div>The API key will not be shown again. If it is lost, add the user again to issue a new key.</div> }))) return;
    setCreated(null);
  };

  return (
    <div className="org">
      <div className="page-head">
        <div>
          <h1>Organisation &amp; Users</h1>
          <p>Who belongs to this organisation, what each role may do, and access management.</p>
        </div>
        {can.admin && data && <button className="btn primary" onClick={() => setAdding(true)}>Add user</button>}
      </div>

      <div className="banner info">
        <span aria-hidden>🔒</span>
        <div>Data is isolated per organisation with database row-level security: every campaign, contact, email and approval belongs
          to one organisation and is invisible to the others. Two demo organisations exist so this isolation can be shown live by signing
          in to each in turn.</div>
      </div>

      <ErrorNote error={error} />
      {loading && !data ? <Loading label="Loading organisation…" /> : data && (
        <>
          <div className="grid g2" style={{ marginBottom: 14, alignItems: "start" }}>
            <div className="card">
              <h2>Organisation</h2>
              {data.organisation ? (
                <dl className="kv">
                  <dt>Name</dt><dd><b>{data.organisation.name}</b></dd>
                  <dt>Identifier</dt><dd className="mono">{data.organisation.slug}</dd>
                  <dt>Signed in as</dt><dd>{data.you?.name ?? principal ?? "—"}</dd>
                  <dt>Your role</dt><dd>{data.you ? <span className={`badge ${ROLE_TONE[data.you.role] ?? ""}`}>{data.you.role}</span> : "—"}</dd>
                  <dt>Users</dt><dd>{data.users.length} ({data.users.filter((u) => u.is_active).length} active)</dd>
                </dl>
              ) : <Empty title="No organisation" hint="This session is not linked to an organisation." />}
            </div>
            <div className="card">
              <h2>Roles</h2>
              <div className="roles">
                {ROLES.filter((r) => data.roles[r]).map((r) => (
                  <div key={r} className="role">
                    <span className={`badge ${ROLE_TONE[r]}`}>{r}</span>
                    <span className="sub">{data.roles[r]}</span>
                  </div>
                ))}
              </div>
              <p className="sub" style={{ marginBottom: 0 }}>Roles are enforced by the server on every request; hidden buttons are only a convenience.</p>
            </div>
          </div>

          <div className="card" style={{ padding: 0 }}>
            <h2 style={{ padding: "16px 16px 0" }}>Users <small>{data.users.length}</small></h2>
            {!data.users.length ? <Empty title="No users yet" hint={can.admin ? "Add the first user to issue an API key." : undefined} /> : (
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr><th>Name</th><th>Email</th><th>Role</th><th>Status</th><th>API key</th><th>Added</th>{can.admin && <th>Manage</th>}</tr>
                  </thead>
                  <tbody>
                    {data.users.map((u) => (
                      <tr key={u.id} className={u.is_active ? "" : "inactive"}>
                        <td>
                          <span className="row" style={{ gap: 6 }}>
                            <b>{u.name}</b>
                            {isYou(u) && <span className="badge blue">you</span>}
                            {u.is_demo && <DemoTag title="Demo user: fictional person in the demo dataset" />}
                          </span>
                        </td>
                        <td className="sub">{u.email}</td>
                        <td><span className={`badge ${ROLE_TONE[u.role] ?? ""}`}>{u.role}</span></td>
                        <td>{u.is_active ? <span className="badge good">active</span> : <span className="badge">inactive</span>}</td>
                        <td>{u.has_api_key ? <span className="badge">issued</span> : <span className="muted">none</span>}</td>
                        <td className="sub" title={u.created_at}>{ago(u.created_at)}</td>
                        {can.admin && (
                          <td>
                            <span className="row" style={{ flexWrap: "nowrap" }}>
                              <select aria-label={`Role for ${u.name}`} value={u.role} disabled={busy !== null}
                                      onChange={(e) => changeRole(u, e.target.value as Role)}>
                                {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                              </select>
                              <button className={`btn sm ${u.is_active ? "danger" : ""}`} disabled={busy !== null} onClick={() => toggleActive(u)}>
                                {busy === `u:${u.id}` ? "Saving…" : u.is_active ? "Deactivate" : "Reactivate"}
                              </button>
                            </span>
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
          {!can.admin && <p className="sub" style={{ marginTop: 12 }}>Only admins can add users, change roles or deactivate accounts.</p>}
        </>
      )}

      {adding && (
        <Modal title="Add user" onClose={() => setAdding(false)} footer={<>
          <button className="btn" type="button" onClick={() => setAdding(false)}>Cancel</button>
          <button className="btn primary" type="submit" form="add-user" disabled={busy === "add" || !form.name.trim() || !form.email.trim()}>
            {busy === "add" ? "Adding…" : "Add user"}
          </button></>}>
          <form id="add-user" className="form" onSubmit={addUser}>
            <label>Full name
              <input className="text" required maxLength={128} value={form.name} autoFocus
                     onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </label>
            <label>Work email
              <input className="text" type="email" required maxLength={255} value={form.email}
                     onChange={(e) => setForm({ ...form, email: e.target.value })} />
            </label>
            <label>Role
              <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })}>
                {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
              </select>
            </label>
            {data?.roles[form.role] && <div className="sub">{data.roles[form.role]}</div>}
            <div className="sub">The user is added to {data?.organisation?.name ?? "this organisation"} and receives a personal API key, shown once.</div>
          </form>
        </Modal>
      )}

      {created && (
        <Modal title={`API key for ${created.name}`} onClose={closeKey} footer={<>
          <button className="btn" onClick={copyKey}>{copied ? "Copied" : "Copy key"}</button>
          <button className="btn primary" onClick={closeKey}>Done</button></>}>
          <div className="banner warn" style={{ margin: 0 }}>
            <span aria-hidden>⚠</span>
            <div><b>This key is shown only once.</b> Only a hash is stored, so it cannot be recovered. Give it to {created.name} through a
              secure channel; it signs them in as <b>{created.role}</b>.</div>
          </div>
          <div className="key-box">
            <code className="mono" aria-label="API key">{created.api_key}</code>
          </div>
          <div className="sub">{created.email}</div>
        </Modal>
      )}
    </div>
  );
}
