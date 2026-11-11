import { redirect } from "next/navigation";
import type { PageProps } from "@/components/PageHeader";

export default async function DriversPage(props: PageProps) {
  const { run } = await props.searchParams;
  redirect(`/predictions?view=drivers${run ? `&run=${encodeURIComponent(run)}` : ""}`);
}
