import { redirect } from "next/navigation";
import type { PageProps } from "@/components/PageHeader";

export default async function TeamsPage(props: PageProps) {
  const { run } = await props.searchParams;
  redirect(`/predictions?view=teams${run ? `&run=${encodeURIComponent(run)}` : ""}`);
}
