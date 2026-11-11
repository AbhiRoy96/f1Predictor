"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";

const LINKS = [
  { href: "/", label: "Home" },
  { href: "/predictions", label: "Predictions" },
  { href: "/seasons", label: "Seasons" },
  { href: "/lab", label: "Model Lab" },
  { href: "/settings", label: "Settings" },
];

export function Nav() {
  const pathname = usePathname();
  const run = useSearchParams().get("run");
  const suffix = run ? `?run=${encodeURIComponent(run)}` : "";
  return (
    <header className="sticky top-0 z-40 border-b border-line bg-track/95 backdrop-blur">
      <div className="mx-auto flex h-16 max-w-7xl items-center gap-6 px-4 sm:px-6">
        <Link href="/" className="flex items-center gap-3" aria-label="Gridcast home">
          <span className="display grid h-9 w-9 place-items-center rounded bg-red text-xl leading-none">G</span>
          <span className="display hidden text-xl leading-none sm:block">
            Gridcast
            <span className="block font-sans text-[0.6rem] font-bold tracking-[0.22em] text-fog">Race Intelligence</span>
          </span>
        </Link>
        <nav aria-label="Primary" className="no-scrollbar -mx-1 flex flex-1 gap-1 overflow-x-auto">
          {LINKS.map(({ href, label }) => {
            const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
            return (
              <Link
                key={href}
                href={`${href}${["/predictions", "/lab"].includes(href) ? suffix : ""}`}
                aria-current={active ? "page" : undefined}
                className={`relative whitespace-nowrap px-3 py-5 text-sm font-bold uppercase tracking-wide transition-colors ${
                  active ? "text-paper" : "text-fog hover:text-paper"
                }`}
              >
                {label}
                {active && <span className="absolute inset-x-3 bottom-0 h-[3px] bg-red" />}
              </Link>
            );
          })}
        </nav>
      </div>
    </header>
  );
}
