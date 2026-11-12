"use client";

import { useEffect, useState } from "react";

import { F1AccountForm } from "@/components/F1AccountForm";
import { api, errorMessage } from "@/lib/api";
import type { SettingsResponse } from "@/lib/types";

const labels = {
  session: "Session", stage: "Evidence stage", strength_model: "Strength model",
  n_simulations: "Simulations", seed: "Random seed", events_per_season: "Collected weekends per season",
};

export function SettingsForm() {
  const [settings, setSettings] = useState<SettingsResponse | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  async function load() {
    try { setSettings(await api<SettingsResponse>("settings")); setError(""); }
    catch (error) { setError(errorMessage(error)); }
  }
  useEffect(() => { void load(); }, []);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!settings) return;
    setBusy(true);
    setMessage("");
    setError("");
    try {
      const result = await api<{ values: SettingsResponse["values"] }>("settings", "PUT", settings.values);
      setSettings({ ...settings, values: result.values });
      setMessage("Prediction defaults saved to PostgreSQL. Existing jobs keep their original options.");
    } catch (error) { setError(errorMessage(error)); }
    finally { setBusy(false); }
  }

  return (
    <>
    {settings && <F1AccountForm key={JSON.stringify(settings.f1_account)} initialStatus={settings.f1_account} />}
    <section className="mt-8 rounded-xl border border-line bg-asphalt p-6">
      {error && <div role="alert" className="mb-5 text-amber">{error} <button type="button" className="underline" onClick={() => void load()}>Reload settings</button></div>}
      {!settings && !error && <p role="status">Loading settings...</p>}
      {settings && <form onSubmit={save} className="grid gap-5 sm:grid-cols-2">
        {(Object.keys(settings.choices) as (keyof SettingsResponse["choices"])[]).map((key) => (
          <label key={key} className="grid gap-2 text-sm">{labels[key]}
            <select className="rounded border border-line bg-track p-3" value={settings.values[key]}
              onChange={(event) => setSettings({ ...settings, values: { ...settings.values, [key]: event.target.value } })}>
              {settings.choices[key].map((value) => <option key={value} value={value}>{value.replaceAll("_", " ")}</option>)}
            </select>
          </label>
        ))}
        {(Object.keys(settings.limits) as (keyof SettingsResponse["limits"])[]).map((key) => (
          <label key={key} className="grid gap-2 text-sm">{labels[key]}
            <input required type="number" min={settings.limits[key][0]} max={settings.limits[key][1]} step={1}
              className="rounded border border-line bg-track p-3" value={settings.values[key]}
              onChange={(event) => setSettings({ ...settings, values: { ...settings.values, [key]: Number(event.target.value) } })} />
          </label>
        ))}
        <label className="flex items-center gap-3 text-sm sm:col-span-2">
          <input type="checkbox" checked={settings.values.skip_collection}
            onChange={(event) => setSettings({ ...settings, values: { ...settings.values, skip_collection: event.target.checked } })} />
          Use only existing local training data (skip FastF1 collection)
        </label>
        <p className="text-sm text-fog sm:col-span-2">Prediction defaults are shared in this private, single-user installation. Database credentials stay server-side; this page never receives them.</p>
        <button disabled={busy} className="justify-self-start rounded bg-red px-6 py-3 font-bold disabled:opacity-50">{busy ? "Saving..." : "Save defaults"}</button>
        {message && <p role="status" className="text-sm text-teal sm:col-span-2">{message}</p>}
      </form>}
    </section>
    </>
  );
}
