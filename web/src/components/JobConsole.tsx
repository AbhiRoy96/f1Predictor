"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { api, errorMessage } from "@/lib/api";
import type { Job, SettingsResponse } from "@/lib/types";

const fieldClass = "rounded border border-line bg-track px-3 py-2 text-paper";
const buttonClass = "rounded bg-red px-5 py-3 font-bold disabled:opacity-50";

export function JobConsole({ mode }: { mode: "prediction" | "season_sync" }) {
  const router = useRouter();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [settings, setSettings] = useState<SettingsResponse | null>(null);
  const [year, setYear] = useState(new Date().getFullYear());
  const [event, setEvent] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [ready, setReady] = useState(false);
  const completed = useRef(new Set<string>());
  const initialized = useRef(false);

  const reload = useCallback(async () => {
    const [nextJobs, nextSettings] = await Promise.all([
      api<Job[]>("jobs"), api<SettingsResponse>("settings"),
    ]);
    const finished = nextJobs.filter((job) => job.status === "succeeded");
    const hasNewResult = initialized.current && finished.some((job) => !completed.current.has(job.id));
    completed.current = new Set(finished.map((job) => job.id));
    initialized.current = true;
    setJobs(nextJobs);
    setSettings(nextSettings);
    setReady(true);
    setError("");
    if (hasNewResult) router.refresh();
  }, [router]);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try { await reload(); }
      catch (error) { if (active) setError(errorMessage(error)); }
      finally { if (active) timer = setTimeout(poll, 5000); }
    }
    void poll();
    return () => { active = false; clearTimeout(timer); };
  }, [reload]);

  async function submit(form: React.FormEvent) {
    form.preventDefault();
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result = await api<{ id: string }>(
        mode === "prediction" ? "predictions" : `seasons/${year}/sync`, "POST",
        mode === "prediction" ? { ...settings?.values, year, event } : {},
      );
      setMessage(`Job ${result.id} queued. Work starts when the worker is available.`);
      await reload();
    } catch (error) { setError(errorMessage(error)); }
    finally { setBusy(false); }
  }

  const relevant = jobs.filter((job) => job.kind === mode);
  return (
    <section className="mb-8 rounded-xl border border-line bg-asphalt p-6">
      <h2 className="display text-2xl">{mode === "prediction" ? "Create a forecast" : "Sync a season"}</h2>
      <p className="mt-2 text-sm text-fog">
        {mode === "prediction"
          ? "Jobs collect missing evidence, reuse or train the selected model, then save a forecast. First runs can take a long time."
          : "Explicitly refresh the calendar, available race/sprint results and latest standings from Jolpica. No automatic backfill runs at startup."}
      </p>
      <form onSubmit={submit} className="mt-5 flex flex-wrap items-end gap-4">
        <label className="grid gap-2 text-sm">Season
          <input required type="number" min={mode === "prediction" ? 2021 : 1950} max={new Date().getFullYear() + 1}
            className={`${fieldClass} w-28`} value={year} onChange={(e) => setYear(Number(e.target.value))} />
        </label>
        {mode === "prediction" && <label className="grid flex-1 gap-2 text-sm">Event name
          <input required maxLength={160} placeholder="e.g. Brazilian Grand Prix" className={fieldClass}
            value={event} onChange={(e) => setEvent(e.target.value)} />
        </label>}
        <button disabled={busy || !ready} className={buttonClass}>{busy ? "Submitting..." : mode === "prediction" ? "Run prediction" : "Sync season"}</button>
      </form>
      {mode === "prediction" && settings && <p className="mt-3 text-xs text-fog">
        {settings.values.session} / {settings.values.stage} / {settings.values.strength_model} / {settings.values.n_simulations.toLocaleString()} simulations.
        {" "}<Link href="/settings" className="text-red underline">Change defaults in Settings</Link>
      </p>}
      {message && <p role="status" className="mt-4 text-sm text-teal">{message}</p>}
      {error && <p role="alert" className="mt-4 text-sm text-amber">{error}</p>}
      <details className="mt-5" open>
        <summary className="cursor-pointer text-sm font-bold">Recent jobs ({relevant.length})</summary>
        {!relevant.length && <p className="mt-3 text-sm text-fog">No jobs yet. Queued jobs require a running worker.</p>}
        <ul className="mt-3 max-h-80 divide-y divide-line overflow-y-auto">
          {relevant.map((job) => <li key={job.id} className="py-3 text-sm">
            <div className="flex flex-wrap justify-between gap-2">
              <span>{job.payload.year} {job.payload.event ?? "Season data"} <span className="text-fog">{job.payload.session}</span></span>
              <strong className={job.status === "failed" ? "text-amber" : job.status === "succeeded" ? "text-teal" : "text-paper"}>{job.status}</strong>
            </div>
            <p className="mt-1 break-all text-xs text-fog">{job.id}</p>
            {job.error && <p className="mt-2 whitespace-pre-wrap text-amber">{job.error}</p>}
            {job.run_id && <Link className="mt-2 inline-block text-red underline" href={`/predictions?run=${encodeURIComponent(job.run_id)}`}>Open forecast</Link>}
            {job.kind === "season_sync" && job.status === "succeeded" && <Link className="mt-2 inline-block text-red underline" href={`/seasons?year=${job.payload.year}`}>Open season</Link>}
          </li>)}
        </ul>
      </details>
    </section>
  );
}
