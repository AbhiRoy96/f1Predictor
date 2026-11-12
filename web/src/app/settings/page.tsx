import { PageHeader } from "@/components/PageHeader";
import { SettingsForm } from "@/components/SettingsForm";

export default function SettingsPage() {
  return <><PageHeader eyebrow="Configuration" title="Prediction settings"
    subtitle="Configure an optional Formula 1 account for first-run collection and set reproducible defaults for new prediction jobs." />
    <SettingsForm /></>;
}
