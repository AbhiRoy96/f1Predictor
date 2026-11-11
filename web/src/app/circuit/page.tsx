import { PageHeader, type PageProps } from "@/components/PageHeader";
import { Card, Section, Stat } from "@/components/ui";

const SCENARIOS_NOTE =
  "The High Safety Car Intervention scenario was written with venues like Interlagos and Singapore in mind: frequent incidents bunch the field and compress strategic gaps. Its multipliers are hand-set, not learned from data, so treat it as a what-if, never as a forecast. Open it under Predictions, then Scenario analysis.";

const FACTS = [
  { label: "Circuit", value: "Interlagos", hint: "Autódromo José Carlos Pace, São Paulo" },
  { label: "Lap length", value: "4.309 km" },
  { label: "Race distance", value: "71 laps" },
  { label: "Direction", value: "Anticlockwise", hint: "15 corners" },
];

const FEATURES = [
  ["Overtaking difficulty", "Average position change at the venue from past races lets the model judge how much grid position matters."],
  ["Circuit retirement rate", "A shrunk historical retirement rate at the circuit feeds the retirement model, so tracks with few samples stay near the field average."],
  ["Similar circuits", "Collection prioritises related venues. Interlagos is grouped with Austin, Spielberg, Barcelona, Silverstone and Suzuka by a documented preset, not a learned similarity."],
];

export default async function CircuitPage(props: PageProps) {
  await props.searchParams;
  return (
    <>
      <PageHeader
        eyebrow="Circuit"
        title="Interlagos"
        subtitle="Track facts for context, and how circuit effects enter the forecast."
      />
      <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {FACTS.map((fact) => <Stat key={fact.label} {...fact} />)}
      </div>
      <p className="mt-3 text-xs text-fog">
        Reference information entered by hand, not model output. Verify against official sources before relying on it.
      </p>

      <Section eyebrow="In the model" title="How the circuit shapes the odds">
        <div className="grid gap-4 md:grid-cols-3">
          {FEATURES.map(([title, text]) => (
            <Card key={title} className="p-6">
              <h3 className="display text-xl">{title}</h3>
              <p className="mt-2 text-sm text-fog">{text}</p>
            </Card>
          ))}
        </div>
      </Section>

      <Section eyebrow="Sensitivity" title="Interlagos-style chaos">
        <Card className="p-6" accent="#ffb800">
          <p className="text-sm text-fog">{SCENARIOS_NOTE}</p>
        </Card>
      </Section>
    </>
  );
}
