import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Field } from "../ui";
import { addPasskey, getPasskeys, passkeyRegisterOptions, removePasskey, type Passkey } from "../accountApi";
import { createPasskey, PasskeyCancelled, passkeysSupported } from "../passkeys";
import { asError } from "./forms";

const date = (iso: string) =>
  new Date(iso).toLocaleDateString("en-SG", { day: "numeric", month: "short", year: "numeric" });

/** v2: add a passkey (fingerprint, face or device PIN) to sign in and approve payments. */
export function PasskeysPage({ onEnded }: { onEnded: () => void }) {
  const [rows, setRows] = useState<Passkey[]>();
  const [limit, setLimit] = useState(10);
  const [name, setName] = useState("");
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const [busy, setBusy] = useState(false);
  const supported = passkeysSupported();

  const fail = useCallback((err: unknown) => {
    const x = asError(err);
    if (x.status === 401) onEnded();
    else setError(x.message);
  }, [onEnded]);

  const load = useCallback(() => {
    getPasskeys().then((r) => { setRows(r.data); setLimit(r.limit); }).catch(fail);
  }, [fail]);
  useEffect(load, [load]);

  async function onAdd(ev: FormEvent) {
    ev.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(undefined);
    setNotice(undefined);
    try {
      const { options } = await passkeyRegisterOptions();
      const credential = await createPasskey(options);
      const added = await addPasskey(credential, name.trim() || undefined);
      setName("");
      setNotice(`"${added.name}" was added. You can now sign in and approve payments with it.`);
      load();
    } catch (err) {
      if (err instanceof PasskeyCancelled) setError("No passkey was added.");
      else fail(err);
    } finally {
      setBusy(false);
    }
  }

  async function onRemove(p: Passkey) {
    setBusy(true);
    setError(undefined);
    try {
      await removePasskey(p.id);
      setNotice(`"${p.name}" was removed. Also delete it from your device's password manager.`);
      load();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="form">
      <h1>Passkeys</h1>
      <p className="muted small">
        A passkey lets you sign in and approve payments with your fingerprint, face or device PIN,
        instead of a password or an emailed code. It stays on your device; SecurePay only keeps a public key.
      </p>
      {notice && <p className="muted small" role="status">{notice}</p>}
      {error && <p className="alert" role="alert">{error}</p>}
      {rows === undefined ? (
        <p className="muted small">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="muted small">You have no passkeys yet.</p>
      ) : (
        <ul className="passkey-list">
          {rows.map((p) => (
            <li key={p.id} className="row-between">
              <span>
                <strong>{p.name}</strong>
                <span className="muted tiny">
                  {" "}· added {date(p.created_at)}
                  {p.last_used_at ? ` · last used ${date(p.last_used_at)}` : " · not used yet"}
                  {p.synced ? " · synced" : ""}
                </span>
              </span>
              <button className="link" type="button" disabled={busy} onClick={() => onRemove(p)}
                aria-label={`Remove ${p.name}`}>Remove</button>
            </li>
          ))}
        </ul>
      )}
      {!supported ? (
        <p className="muted small">This browser can't make passkeys. Try a recent Chrome, Safari, Edge or Firefox.</p>
      ) : rows && rows.length >= limit ? (
        <p className="muted small">You have the most passkeys an account can have ({limit}). Remove one to add another.</p>
      ) : (
        <form onSubmit={onAdd} noValidate className="form">
          <Field label="Name (optional)" id="passkey-name">
            <input id="passkey-name" maxLength={60} placeholder="My phone" value={name}
              onChange={(e) => setName(e.target.value)} />
          </Field>
          <button className="btn primary" type="submit" disabled={busy}>Add a passkey</button>
        </form>
      )}
    </div>
  );
}
