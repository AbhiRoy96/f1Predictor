"use client";

export default function ErrorPage({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <section role="alert" className="rounded-xl border border-red bg-asphalt p-8">
      <h1 className="display text-3xl">Data could not be loaded</h1>
      <p className="mt-4 text-fog">Check that Flask and PostgreSQL are running and the schema is initialized. If you selected a saved forecast, verify that it still exists.</p>
      <button onClick={reset} className="mt-5 rounded bg-red px-5 py-3 font-bold">Try again</button>
    </section>
  );
}
