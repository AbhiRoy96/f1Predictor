import { PageHeader } from "@/components/PageHeader";
import { SettingsForm } from "@/components/SettingsForm";

export default function SettingsPage() {
  return <><PageHeader eyebrow="Configuration" title="Prediction settings"
    subtitle="Set reproducible defaults for new prediction jobs. No API credentials are required by the current data providers." />
    <SettingsForm /></>;
}
