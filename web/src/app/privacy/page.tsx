import { PageHeader } from "@/components/PageHeader";

export default function PrivacyPage() {
  return (
    <>
      <PageHeader eyebrow="Privacy" title="What Gridcast stores" />
      <div className="mt-8 max-w-3xl space-y-4 text-sm leading-relaxed text-fog">
        <p>
          Hosted access uses Microsoft Entra sign-in. Gridcast does not store Entra passwords or access tokens in its
          database. The hosting platform validates your sign-in before requests reach the web application.
        </p>
        <p>
          In private local installations, you can optionally save a Formula 1 account in Settings.
          The username and password are encrypted in PostgreSQL with a separate server-side encryption key
          and are shared by local collection jobs. Saved credentials are never returned to the browser.
          Remove the saved account in Settings to delete its database record; FastF1 may retain cached
          sign-in tokens separately. Database backups may retain encrypted records until the operator removes them.
          Hosted deployments disable this account editor and use administrator-managed environment secrets instead.
        </p>
        <p>
          Job records store a SHA-256 pseudonym of your platform account ID to enforce queue limits and let you
          see your own jobs. Write throttling stores account and client-address pseudonyms and counters; inactive
          counters are removed after one day when another write occurs. These pseudonyms are not anonymous data.
        </p>
        <p>
          Forecast requests, timestamps, status, errors and generated forecasts persist in PostgreSQL until the
          operator removes them. Forecasts and application settings are shared with other signed-in users.
          Do not include personal or confidential information in event names or other inputs.
        </p>
        <p>
          Application telemetry records operations, job IDs, timing and failures, not Entra account headers,
          passwords or proxy credentials. Azure platform access and sign-in logs are managed separately by the
          hosting operator. Ask the operator about their retention policy, access controls and deletion requests.
        </p>
      </div>
    </>
  );
}
