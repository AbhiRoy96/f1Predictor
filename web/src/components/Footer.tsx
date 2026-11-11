import Link from "next/link";

export function Footer() {
  return (
    <footer className="mt-24 border-t border-line bg-asphalt">
      <div className="mx-auto max-w-7xl px-4 py-10 sm:px-6">
        <p className="display text-lg">Important information</p>
        <ul className="mt-4 grid gap-3 text-sm leading-relaxed text-fog md:grid-cols-2">
          <li>
            <strong className="text-paper">Unofficial fan project.</strong> This application is not affiliated with,
            endorsed by or sponsored by Formula 1, FOM, the FIA, F1 TV or any team or driver. F1, FORMULA 1, GRAND PRIX
            and related marks are trademarks of Formula One Licensing BV. The visual theme is inspired by the F1 TV
                        style; this repository ships no official logos, fonts, images or broadcast content.
          </li>
          <li>
            <strong className="text-paper">Probabilities, not predictions of certainty.</strong> Forecasts describe
            simulated outcomes from a statistical model. Races are affected by safety cars, weather, strategy and
            reliability that no model captures fully. A 40% favourite is expected to lose most of the time.
          </li>
          <li>
            <strong className="text-paper">Not betting, financial or safety advice.</strong> Do not use these figures to
            place wagers or make decisions where money or safety is at stake.
          </li>
          <li>
            <strong className="text-paper">Uncertainty ranges are conditional.</strong> P10 to P90 ranges describe
            simulated finishing outcomes, not parameter confidence intervals. Monte Carlo errors show numerical
            sampling error only. Scenario views other than the standard dry race are heuristic sensitivity analyses.
          </li>
          <li>
            <strong className="text-paper">Historical evidence is limited.</strong> Training covers collected events only,
            not complete championship history. Backtests span few races and do not guarantee future skill.
          </li>
          <li>
            <strong className="text-paper">Data sources.</strong> Prediction evidence via FastF1; season calendars, results and standings via Jolpica. Profile text and
            media, where shown, come from Wikipedia and Wikimedia Commons under their own licences.
          </li>
        </ul>
        <div className="mt-8 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-6 text-xs text-fog">
          <p>Released under the MIT licence. Forecasts are generated from saved point-in-time artifacts.</p>
          <nav className="flex gap-4" aria-label="Footer">
            <Link href="/settings" className="hover:text-paper">Settings</Link>
            <Link href="/lab" className="hover:text-paper">Model Lab</Link>
          </nav>
        </div>
      </div>
    </footer>
  );
}
