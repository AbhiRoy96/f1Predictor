import type { Metadata } from "next";
import { Suspense } from "react";

import { Footer } from "@/components/Footer";
import { Nav } from "@/components/Nav";

import "@fontsource-variable/noto-sans/wght.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "Gridcast | Race Intelligence for Formula 1 (unofficial)",
  description:
    "Gridcast: point-in-time Formula 1 race forecasts with win, podium, top-10 and retirement probabilities. Unofficial fan project.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded focus:bg-red focus:px-4 focus:py-2">
          Skip to content
        </a>
        <Suspense fallback={<div className="h-16 border-b border-line" />}>
          <Nav />
        </Suspense>
        <main id="main" className="mx-auto max-w-7xl px-4 pb-8 pt-8 sm:px-6">
          {children}
        </main>
        <Footer />
      </body>
    </html>
  );
}
