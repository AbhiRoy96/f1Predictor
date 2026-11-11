import Link from "next/link";
import type { ReactNode } from "react";

import { RunPicker } from "@/components/RunPicker";
import { DataNotice } from "@/components/DataNotice";
import { Card } from "@/components/ui";
import { getRun } from "@/lib/data";
import type { ManifestEntry, Run } from "@/lib/types";

export type PageProps = { searchParams: Promise<{ run?: string; view?: string }> };

export async function loadRun(props: PageProps): Promise<{ run: Run | null; manifest: ManifestEntry[] }> {
  return getRun((await props.searchParams).run);
}

export function EmptyState() {
  return (
    <Card className="p-10 text-center">
      <p className="display text-2xl">No forecast data yet</p>
      <p className="mx-auto mt-3 max-w-xl text-sm text-fog">
        Submit a forecast above and follow its job status. To load forecasts generated through the CLI,
        run <code className="rounded bg-track px-1">flask --app backend.app:create_app import-data</code> in the API container.
      </p>
      <Link href="/settings" className="mt-6 inline-block rounded-full bg-red px-6 py-3 text-sm font-bold uppercase tracking-wider">
        Configure predictions
      </Link>
    </Card>
  );
}

export function PageHeader({ eyebrow, title, subtitle, run, manifest, children }: {
  eyebrow: string; title: string; subtitle?: string; run?: Run; manifest?: ManifestEntry[]; children?: ReactNode;
}) {
  return (
    <header>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="eyebrow text-red">{eyebrow}</p>
          <h1 className="display mt-1 text-4xl sm:text-5xl">{title}</h1>
          {subtitle && <p className="mt-3 max-w-3xl text-fog">{subtitle}</p>}
        </div>
        {run && manifest && <RunPicker manifest={manifest} current={run.meta.id} />}
      </div>
      {run && <div className="mt-5"><DataNotice meta={run.meta} /></div>}
      {children}
    </header>
  );
}
