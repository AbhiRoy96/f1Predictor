"use client";

import { useState } from "react";

import { api, errorMessage } from "@/lib/api";
import type { F1AccountStatus } from "@/lib/types";

export function F1AccountForm({ initialStatus }: { initialStatus: F1AccountStatus }) {
  const [status, setStatus] = useState(initialStatus);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  async function update(clear: boolean) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result = await api<{ f1_account: F1AccountStatus }>(
        "settings/f1-account", "PUT", clear ? { clear: true } : { username, password },
      );
      setStatus(result.f1_account);
      setUsername("");
      setMessage(clear
        ? "Saved account removed. Environment credentials, if set, still apply."
        : "Account saved encrypted and available to new collection jobs. Sign-in has not been verified; FastF1 may reuse a cached token.");
    } catch (error) { setError(errorMessage(error)); }
    finally { setPassword(""); setBusy(false); }
  }

  return (
    <section aria-labelledby="f1-account-heading" className="mt-8 rounded-xl border border-line bg-asphalt p-6">
      <h2 id="f1-account-heading" className="text-xl font-bold">First-run setup: Formula 1 account</h2>
      <p className="mt-3 text-sm text-fog">
        Optional for anonymous access. FastF1 warns when no account is available and some timing data may require sign-in.
        Jolpica does not use this account.
      </p>
      <p role="status" className="mt-3 text-sm">
        {status.source === "database" ? "Account saved (encrypted)." : status.source === "environment"
          ? "Account configured through the server environment." : "No F1 account configured. Collection will attempt anonymous access."}
      </p>
      {status.hosted ? <p className="mt-3 text-sm text-fog">Account editing is disabled in hosted mode. Ask your administrator to configure server-side credentials.</p> : <>
        <p className="mt-3 text-sm text-fog">
          This account is shared by this private installation. Credentials are encrypted in PostgreSQL using a separate server-side key.
          Saved credentials are never sent back to this page. Keep this installation private and use HTTPS outside localhost.
        </p>
        {status.error && <p role="alert" className="mt-3 text-sm text-amber">{status.error} Add the key to your local .env and recreate the API and worker containers.</p>}
        <form className="mt-5 grid gap-5 sm:grid-cols-2" onSubmit={(event) => { event.preventDefault(); void update(false); }}>
          <label className="grid gap-2 text-sm">F1 account username or email
            <input required name="f1-username" autoComplete="username" maxLength={320}
              disabled={!status.editable || busy} value={username}
              onChange={(event) => setUsername(event.target.value)}
              className="rounded border border-line bg-track p-3 disabled:opacity-50" />
          </label>
          <label className="grid gap-2 text-sm">F1 account password
            <input required name="f1-password" type="password" autoComplete="new-password" maxLength={1024}
              disabled={!status.editable || busy} value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="rounded border border-line bg-track p-3 disabled:opacity-50" />
          </label>
          <div className="flex flex-wrap gap-3 sm:col-span-2">
            <button disabled={!status.editable || busy} className="rounded bg-red px-6 py-3 font-bold disabled:opacity-50">
              {busy ? "Updating..." : status.source === "database" ? "Replace saved account" : "Save F1 account"}
            </button>
            {status.source === "database" && <button type="button" disabled={busy}
              onClick={() => void update(true)} className="rounded border border-line px-6 py-3 disabled:opacity-50">
              Remove saved account
            </button>}
          </div>
        </form>
      </>}
      {error && <p role="alert" className="mt-3 text-sm text-amber">{error}</p>}
      {message && <p role="status" className="mt-3 text-sm text-teal">{message}</p>}
    </section>
  );
}
