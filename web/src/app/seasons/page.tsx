import { JobConsole } from "@/components/JobConsole";
import { PageHeader } from "@/components/PageHeader";
import { Card, Section } from "@/components/ui";
import { backendGet } from "@/lib/data";
import type { SeasonData, SeasonRace, Standing } from "@/lib/types";

function Standings({ rows, type }: { rows: Standing[]; type: "driver" | "constructor" }) {
  if (!rows.length) return <p className="text-sm text-fog">No standings available from the source.</p>;
  return <div className="overflow-x-auto"><table className="w-full text-left text-sm">
    <caption className="sr-only">{type} championship standings</caption>
    <thead className="text-fog"><tr><th className="p-3">Position</th><th>Name</th><th>Points</th><th>Wins</th></tr></thead>
    <tbody>{rows.map((row) => <tr key={row.position} className="border-t border-line">
      <td className="p-3">{row.position}</td><td>{row.Driver ? `${row.Driver.givenName} ${row.Driver.familyName}` : row.Constructor?.name}</td>
      <td>{row.points}</td><td>{row.wins}</td>
    </tr>)}</tbody>
  </table></div>;
}

function RaceResults({ races, sprint = false }: { races: SeasonRace[]; sprint?: boolean }) {
  if (!races.length) return <p className="text-sm text-fog">No published classifications available.</p>;
  return <div className="space-y-3">{races.map((race) => (
    <details key={race.round} className="rounded border border-line bg-asphalt p-4">
      <summary className="cursor-pointer font-bold">Round {race.round}: {race.raceName} <span className="text-sm font-normal text-fog">{race.date}</span></summary>
      <div className="mt-4 overflow-x-auto"><table className="w-full text-left text-sm">
        <caption className="sr-only">{race.raceName} {sprint ? "sprint" : "race"} classification</caption>
        <thead className="text-fog"><tr><th className="p-2">Pos</th><th>Driver</th><th>Constructor</th><th>Grid</th><th>Points</th><th>Status</th></tr></thead>
        <tbody>{(sprint ? race.SprintResults : race.Results)?.map((row) => (
          <tr key={row.Driver.driverId} className="border-t border-line">
            <td className="p-2">{row.position}</td><td>{row.Driver.givenName} {row.Driver.familyName}</td>
            <td>{row.Constructor.name}</td><td>{row.grid}</td><td>{row.points}</td><td>{row.status}</td>
          </tr>
        ))}</tbody>
      </table></div>
    </details>
  ))}</div>;
}

export default async function SeasonsPage({ searchParams }: { searchParams: Promise<{ year?: string }> }) {
  const requested = (await searchParams).year;
  const current = new Date().getUTCFullYear();
  if (requested !== undefined && (!/^\d{4}$/.test(requested) || Number(requested) < 1950 || Number(requested) > current + 1)) {
    notFound();
  }
  const year = requested ? Number(requested) : current;
  const years = await backendGet<number[]>("seasons");
  const season = years.includes(year) ? await backendGet<SeasonData>(`seasons/${year}`) : null;
  const options = [...new Set([current, year, ...years])].sort((a, b) => b - a);
  const data = season?.payload;
  const drivers = data?.driver_standings[0];
  const constructors = data?.constructor_standings[0];
  return (
    <>
      <PageHeader eyebrow="Seasons & history" title="The championship archive"
        subtitle="Actual calendars, classifications and standings, separate from model forecasts. Source coverage and refresh times are shown explicitly." />
      <div className="mt-8"><JobConsole mode="season_sync" /></div>
      <form className="mb-6 flex items-end gap-3">
        <label className="grid gap-2 text-sm">Browse season
          <select name="year" defaultValue={year} className="rounded border border-line bg-asphalt px-4 py-3">
            {options.map((value) => <option key={value} value={value}>{value}{years.includes(value) ? "" : " (not synced)"}</option>)}
          </select>
        </label>
        <button className="rounded bg-red px-5 py-3 font-bold">Open season</button>
      </form>
      {!data && <Card className="p-8"><h2 className="display text-2xl">No data for {year} yet</h2><p className="mt-3 text-fog">Sync this season above, or import existing local data from the API container.</p></Card>}
      {data && <>
        <p className="text-sm text-fog">Source: {data.source}. Coverage: {data.coverage.replaceAll("_", " ")}. Last updated: {season?.updated_at}.</p>
        <Section eyebrow={String(year)} title="Calendar & circuits">
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {data.calendar.map((event, index) => {
              const race = event as Partial<SeasonRace>;
              const local = event as Record<string, unknown>;
              return <Card key={String(race.round ?? local.RoundNumber ?? index)} className="p-5">
                <p className="eyebrow">Round {String(race.round ?? local.RoundNumber ?? "?")}</p>
                <h3 className="mt-2 font-bold">{String(race.raceName ?? local.EventName ?? "Event")}</h3>
                <p className="mt-2 text-sm text-fog">{race.Circuit?.circuitName ?? String(local.Location ?? "")}</p>
                <p className="mt-1 text-xs text-fog">{race.date ?? String(local.EventDate ?? "")}</p>
              </Card>;
            })}
          </div>
        </Section>
        <Section eyebrow={`After round ${drivers?.round ?? "?"}`} title="Driver standings">
          <Standings rows={drivers?.DriverStandings ?? []} type="driver" />
        </Section>
        <Section eyebrow={`After round ${constructors?.round ?? "?"}`} title="Constructor standings">
          <Standings rows={constructors?.ConstructorStandings ?? []} type="constructor" />
        </Section>
        <Section eyebrow="Classifications" title="Race results"><RaceResults races={data.races} /></Section>
        <Section eyebrow="Classifications" title="Sprint results"><RaceResults races={data.sprints} sprint /></Section>
        {data.source === "local_parquet" && <Section eyebrow="Partial local import" title="Collected session results">
          <p className="mb-4 text-sm text-amber">These are sampled sessions, not full-season standings. Sync the season for published championship data.</p>
          <div className="max-h-96 overflow-auto"><table className="w-full text-left text-sm">
            <thead className="text-fog"><tr>{["Round", "Event", "Session", "Driver", "Team", "Position", "Points", "Status"].map((label) => <th key={label} className="p-2">{label}</th>)}</tr></thead>
            <tbody>{data.local_results.map((row, index) => <tr key={index} className="border-t border-line">
              {["RoundNumber", "EventName", "SessionType", "Abbreviation", "TeamName", "Position", "Points", "Status"].map((key) => <td key={key} className="p-2">{String(row[key] ?? "")}</td>)}
            </tr>)}</tbody>
          </table></div>
        </Section>}
      </>}
    </>
  );
}
import { notFound } from "next/navigation";
