"""Models & Strategies — editorial content for /strategies (July 2026).

Hand-written field guide: how the leading playbooks behaved recently in the US
vs India, with hand-curated example stocks per strategy. This is CONTENT, not
computation — nothing here feeds the screener, and the page says so honestly.
Curated the same way metrics.PEERS is: by hand, dated, revisable.

Each stock row is (ticker, company, why-it-fits-today). Tickers link into the
regular /analyze → /stock deep-dive flow so every editorial claim lands on
Otto's numbers-first page. `funds` rows are funds/indices (no deep dive).
"""

# Inline SVG line icons (24×24, stroke = currentColor) for the five strategies.
ICONS = {
    "capex": '<path d="M3 21h18M6 21V8h6v13M15 21v-8h4v8M6 8V4l4 2v2"/>',
    "quality": '<rect x="7" y="7" width="10" height="10" rx="2"/>'
               '<path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>',
    "momentum": '<path d="M3 17l6-6 4 4 8-9M15 6h6v6"/>',
    "value": '<path d="M20 12l-8 8-9-9V4h7l10 8z"/><circle cx="7.5" cy="7.5" r="1.5"/>',
    "smartbeta": '<path d="M4 6h16M4 12h16M4 18h16"/>'
                 '<circle cx="9" cy="6" r="2"/><circle cx="15" cy="12" r="2"/><circle cx="7" cy="18" r="2"/>',
}

STRATEGIES = [
    dict(
        id="capex", icon="capex", name="Capex & infrastructure",
        take="Own the companies that build the country.",
        status={"US": "selective outperformance", "IN": "#1 in India right now"},
        history=(
            "The oldest playbook in markets: when a government or an industry commits "
            "to a decade of physical building — railways, grids, factories — the "
            "builders' order books fill years in advance. The strategy is simply to "
            "hold the companies collecting those orders while the spending cycle runs."),
        markets={
            "US": dict(
                read=(
                    "Traditional infrastructure moves slowly here, but three capex waves "
                    "are real: the CHIPS Act, re-shoring of manufacturing, and above all "
                    "the physical build-out behind AI. Data centres need power "
                    "generation, cooling and grid equipment — and the companies "
                    "supplying them have traded like growth stocks."),
                stocks=[
                    ("ETN", "Eaton", "Electrical gear for data centres and the grid — the picks-and-shovels of the AI build-out."),
                    ("VRT", "Vertiv", "Cooling and power management for AI server halls; its order book tracks data-centre construction."),
                    ("PWR", "Quanta Services", "Builds and upgrades the transmission grid every new gigawatt has to cross."),
                    ("GEV", "GE Vernova", "Turbines and grid kit as US power demand grows for the first time in decades."),
                ]),
            "IN": dict(
                read=(
                    "Arguably the single best-performing strategy in India. \"Make in "
                    "India\", PLI incentives and record budget capex turned railway, "
                    "defence, power and capital-goods order books into multi-year "
                    "growth — and the builders into multi-baggers."),
                stocks=[
                    ("HAL.NS", "Hindustan Aeronautics", "Defence indigenisation flagship with a decade-deep fighter and helicopter order book."),
                    ("BEL.NS", "Bharat Electronics", "Defence electronics on nearly every Indian platform — steady state orders, high margins."),
                    ("RVNL.NS", "Rail Vikas Nigam", "Executes the railway build-out that sits at the centre of budget capex."),
                    ("LT.NS", "Larsen & Toubro", "India's engineering bellwether — wins a slice of almost every large public project."),
                ]),
        }),
    dict(
        id="quality", icon="quality", name="Mega-cap quality growth",
        take="Buy the undisputed leaders, pay up, hold on.",
        status={"US": "#1 in the US", "IN": "trailing — money chased premiumisation"},
        history=(
            "\"Quality growth\" means fortress balance sheets, fat cash flows and "
            "pricing power — and accepting you'll rarely get them cheap. The modern "
            "twist: in a market as efficient as the US, simply holding the biggest "
            "winners has beaten hunting for hidden gems."),
        markets={
            "US": dict(
                read=(
                    "US returns have been historically concentrated in a handful of "
                    "mega-cap tech monopolies — the \"Magnificent Seven\" and the "
                    "AI-adjacent chip and cloud names. The winning move was refusing "
                    "to be clever: own the leaders."),
                stocks=[
                    ("NVDA", "NVIDIA", "The AI build-out's toll collector — data-centre chips with monopoly pricing power."),
                    ("MSFT", "Microsoft", "Cloud plus AI, distributed through an enterprise base nobody can dislodge."),
                    ("GOOGL", "Alphabet", "Search cash flows funding a full AI stack, still priced like an ad company."),
                    ("META", "Meta Platforms", "Ad pricing power on the largest social graph; AI spend already paying back in engagement."),
                    ("AMZN", "Amazon", "Retail margins finally showing while AWS rides the AI wave."),
                ]),
            "IN": dict(
                read=(
                    "India's classic quality names — big IT services, consumer staples "
                    "— lagged on rich valuations and soft global demand. The growth "
                    "money went to premiumisation instead: whatever the rising "
                    "upper-middle class trades up to."),
                stocks=[
                    ("TITAN.NS", "Titan Company", "Jewellery and watches — the cleanest listed play on Indians trading up."),
                    ("DLF.NS", "DLF", "Luxury housing in full swing; record pre-sales quarter after quarter."),
                    ("M&M.NS", "Mahindra & Mahindra", "Premium SUVs sold out months ahead — discretionary wheels for the new middle class."),
                    ("ICICIBANK.NS", "ICICI Bank", "Private banking and wealth management for the households doing the upgrading."),
                ]),
        }),
    dict(
        id="momentum", icon="momentum", name="Pure price momentum",
        take="Buy what's already going up — and respect your stops.",
        status={"US": "works, but algorithm-dominated", "IN": "crushing value investing in SMIDs"},
        history=(
            "Momentum is the most stubborn anomaly in finance: winners keep winning "
            "longer than theory says they should. It works until the liquidity that "
            "feeds it turns — which is why position sizing and exits matter more "
            "than entries."),
        markets={
            "US": dict(
                read=(
                    "US momentum is heavily algorithmic. Trend-following has paid best "
                    "riding large-cap structural uptrends with tight risk management, "
                    "to dodge sudden machine-driven reversals."),
                stocks=[
                    ("AVGO", "Broadcom", "Custom AI silicon orders keep forcing estimates — and the trend — higher."),
                    ("PLTR", "Palantir", "The market's favourite AI-adoption trade; a pure sentiment-and-flows momentum name."),
                    ("NFLX", "Netflix", "Post-crackdown earnings beats built a textbook large-cap uptrend."),
                    ("LLY", "Eli Lilly", "GLP-1 demand so far ahead of supply that the stock trends like a tech name."),
                ]),
            "IN": dict(
                read=(
                    "A historic flood of domestic SIP money into mutual funds has "
                    "poured disproportionately into small and mid caps. Buying what is "
                    "already rising — regardless of valuation — has beaten "
                    "fundamentals-first investing in the Indian SMID space."),
                stocks=[
                    ("SUZLON.NS", "Suzlon Energy", "Wind-energy turnaround that became the retail momentum favourite."),
                    ("BSE.NS", "BSE", "The exchange itself — revenue rises with the very retail wave it rides."),
                    ("DIXON.NS", "Dixon Technologies", "PLI-driven electronics manufacturing, compounding order wins every quarter."),
                    ("CDSL.NS", "CDSL", "Every new demat account is its customer — a direct bet on retail participation."),
                ]),
        }),
    dict(
        id="value", icon="value", name="The value re-rating play",
        take="Buy what everyone ignored, wait for the story to change.",
        status={"US": "value-trap risk", "IN": "a historic PSU windfall"},
        history=(
            "Classic value: buy statistically cheap assets and wait for the market to "
            "change its mind. The catch — cheap stays cheap without a catalyst. The "
            "strategy only pays when something forces the re-rating: policy, profits "
            "or scarcity."),
        markets={
            "US": dict(
                read=(
                    "US deep value has underperformed for a decade — cheap American "
                    "companies are usually cheap for a reason (legacy media, indebted "
                    "industrials, disrupted retail). The exceptions: selective energy "
                    "and financials during inflationary spikes."),
                stocks=[
                    ("XOM", "Exxon Mobil", "Capital discipline turned cheap barrels into buybacks — value that finally paid."),
                    ("CVX", "Chevron", "Low-cost reserves plus dividends: the inflation-spike version of value that works."),
                    ("JPM", "JPMorgan Chase", "The financial that re-rates first whenever rates and spreads move value's way."),
                    ("WFC", "Wells Fargo", "A discount to peers that narrows as its regulatory penalty box opens."),
                ]),
            "IN": dict(
                read=(
                    "Public Sector Undertakings — companies majority-owned by the "
                    "government — traded at dirt-cheap multiples with fat dividends "
                    "because nobody trusted the management. When the state pivoted to "
                    "profitability and execution (defence, railways, state banks), the "
                    "re-rating was historic."),
                stocks=[
                    ("SBIN.NS", "State Bank of India", "India's largest bank went from \"bureaucratic discount\" to record profits."),
                    ("BHEL.NS", "BHEL", "Power-equipment order book reborn as the grid build-out returned."),
                    ("IRFC.NS", "Indian Railway Finance", "The railway financing arm, re-rated with the whole rail capex complex."),
                    ("COALINDIA.NS", "Coal India", "Paid a double-digit yield while powering the grid — the market finally noticed."),
                ]),
        }),
    dict(
        id="smartbeta", icon="smartbeta", name="Quantitative smart beta",
        take="Fire the stock picker, hire the rule.",
        status={"US": "mainstream — yield & quality factors", "IN": "exploding from a small base"},
        history=(
            "Factor investing sits between indexing and stock picking: buy whatever "
            "passes a transparent screen — momentum, quality, yield — rebalance on "
            "schedule, and let the rule remove the emotion. It's how retail money "
            "increasingly buys \"strategy\" itself."),
        markets={
            "US": dict(
                read=(
                    "Blended-factor ETFs — quality plus momentum — have performed "
                    "exceptionally, and covered-call income funds have pulled in "
                    "hundreds of billions from investors who want risk-adjusted yield "
                    "more than pure upside."),
                stocks=[],
                funds=[
                    ("JEPI", "Covered-call income — yield first, upside second."),
                    ("SCHD", "The dividend-quality factor in one ticker."),
                    ("MTUM", "Large-cap momentum, rebalanced by rule."),
                ],
                note="Funds, not single companies — Otto deep-dives individual stocks only."),
            "IN": dict(
                read=(
                    "Rules-based investing is new but exploding in India. Momentum "
                    "index funds — like those tracking the Nifty 200 Momentum 30 — "
                    "have routinely beaten active managers, and investors are shifting "
                    "from discretionary tips to systematic screens."),
                stocks=[],
                funds=[
                    ("Nifty 200 Momentum 30", "The index that keeps embarrassing active fund managers."),
                    ("Nifty Alpha 50", "Higher-octane rules-based momentum."),
                    ("Quality-factor index funds", "The calm end of the factor menu."),
                ],
                note="Indices and funds, not single companies — Otto deep-dives individual stocks only."),
        }),
]

FRAMEWORKS = [
    dict(
        id="canslim", name="CANSLIM", img="oneil",
        founder="William J. O'Neil", initials="WO", dates="1933–2023",
        role="Founded Investor's Business Daily · wrote How to Make Money in Stocks (1988)",
        history=(
            "Seven letters, one idea: buy fundamentally accelerating companies "
            "(Current and Annual earnings) exactly when the chart confirms "
            "institutional buying — the cup-and-handle breakout — and cut every "
            "loss at 7–8%, no exceptions."),
        markets={
            "US": dict(
                read=(
                    "High-frequency algorithms now hunt the obvious retail patterns — "
                    "nudging price just past the breakout to trigger buying, then "
                    "fading it to hit the tight stops. Outside the mega-cap AI "
                    "leaders, US CANSLIM has been a choppy, low-win-rate ride."),
                stocks=[
                    ("NVDA", "NVIDIA", "The rare US name where every classic breakout actually followed through."),
                    ("LLY", "Eli Lilly", "Accelerating earnings plus institutional accumulation — CANSLIM by the book."),
                ]),
            "IN": dict(
                read=(
                    "India is in a classic structural bull market — huge retail and "
                    "domestic institutional participation, far less algorithmic gaming "
                    "in the SMID space. Cup-and-handle breakouts have tended to be "
                    "genuine and sustained."),
                stocks=[
                    ("DIXON.NS", "Dixon Technologies", "A textbook base-breakout-rebase ladder, two years running."),
                    ("KAYNES.NS", "Kaynes Technology", "Electronics smallcap whose breakouts kept resolving upward, CANSLIM-style."),
                ]),
        }),
    dict(
        id="sepa", name="SEPA / VCP", img="minervini",
        founder="Mark Minervini", initials="MM", dates="b. 1965",
        role="US Investing Champion 1997 & 2021 · wrote Trade Like a Stock Market Wizard",
        history=(
            "Specific Entry Point Analysis: wait for a leader's volatility to "
            "contract through successively tighter pullbacks — the Volatility "
            "Contraction Pattern — then enter as price pivots out on volume, "
            "risking fractions of a percent to make multiples."),
        markets={
            "US": dict(
                read=(
                    "The same whipsaw problem as CANSLIM: tight pivots are exactly "
                    "what fake-out algorithms feed on, so pure VCP in US mid caps has "
                    "been rough outside the leadership names."),
                stocks=[
                    ("AVGO", "Broadcom", "Volatility contractions kept resolving upward through the AI re-rating."),
                ]),
            "IN": dict(
                read=(
                    "A golden era. When an Indian defence or manufacturing name coils "
                    "into a VCP, the breakout has tended to run for months, not "
                    "minutes."),
                stocks=[
                    ("HAL.NS", "Hindustan Aeronautics", "Coiled through long consolidations and broke out repeatedly."),
                    ("POLYCAB.NS", "Polycab India", "Multi-month VCPs resolving into sustained trend legs."),
                ]),
        }),
    dict(
        id="zulu", name="The Zulu Principle", img="slater",
        founder="Jim Slater", initials="JS", dates="1929–2015",
        role="British financier · popularised the PEG ratio",
        history=(
            "Slater's rule: specialise narrowly (\"be a Zulu expert\"), hunt small, "
            "under-researched companies growing EPS 15%+ — and only pay a PEG under "
            "about 0.75, so the growth costs less than it's worth."),
        markets={
            "US": dict(
                read=(
                    "Nearly impossible in the US now: predictable 15% growers get "
                    "priced to PEGs of 1.5+ instantly, and a genuinely low US PEG "
                    "usually flags a value trap — one-off growth about to mean-revert. "
                    "No honest picks here."),
                stocks=[]),
            "IN": dict(
                read=(
                    "India was the perfect hunting ground — dozens of under-researched "
                    "smallcaps compounding 25%+ on 10–15× earnings. The liquidity wave "
                    "has stretched those valuations, so true Zulu bargains are far "
                    "scarcer than two years ago."),
                stocks=[
                    ("GRSE.NS", "Garden Reach Shipbuilders", "Fit every Slater test before its re-rating — the growth was cheap until the crowd arrived."),
                ]),
        }),
    dict(
        id="darvas", name="Darvas Box", img="darvas",
        founder="Nicolas Darvas", initials="ND", dates="1920–1977",
        role="Ballroom dancer · wrote How I Made $2,000,000 in the Stock Market (1960)",
        history=(
            "Darvas bought only stocks punching to new all-time highs on heavy "
            "volume, drew a \"box\" around each consolidation, bought the break of "
            "the box top and trailed his stop beneath it. No forecasts — price "
            "only."),
        markets={
            "US": dict(
                read=(
                    "Concentrated success: the boxes forced you into semiconductors, "
                    "AI and GLP-1 names — which happened to be the biggest moves of "
                    "the decade. Outside them, mostly sideways frustration."),
                stocks=[
                    ("NVDA", "NVIDIA", "Ascending boxes for two years — the purest Darvas ride of the decade."),
                ]),
            "IN": dict(
                read=(
                    "An absolute goldmine — whole sectors (PSU banks, railways, power, "
                    "real estate) broke to all-time highs and stacked boxes for months. "
                    "Pure price-action kept you fully exposed to the leaders without "
                    "needing the fundamental story."),
                stocks=[
                    ("RVNL.NS", "Rail Vikas Nigam", "Stacked ascending boxes through the railway re-rating."),
                    ("BHEL.NS", "BHEL", "Sector-wide all-time-high breakout — box after box."),
                ]),
        }),
]

BOTTOM_LINE = {
    "US": (
        "The winning US strategy has been recognising structural technological "
        "shifts — AI, cloud, data centres — and not being afraid to pay a premium "
        "for the highest-quality global monopolies."),
    "IN": (
        "The winning Indian strategy has been following government capital "
        "expenditure (capex, defence, PSUs) and riding the wave of domestic "
        "liquidity into small and mid caps, where momentum has trumped valuation. "
        "India today behaves much like the US of the 1980s–90s — which is exactly "
        "why the classic frameworks are working so well there."),
}


def ask_context(market):
    """Ground Ask Otto in this page's content, market-first. Same shape as the
    stock context: plain text lines the LLM can quote from."""
    label = "India" if market == "IN" else "the US"
    lines = [
        f"PAGE: InvestRight's Models & Strategies field guide, currently viewing {label}.",
        "This is hand-curated editorial content (July 2026) about how investment "
        "strategies performed recently in the US vs India. It is not the screener "
        "and not advice.",
    ]
    for s in STRATEGIES:
        m = s["markets"][market]
        lines.append(f"STRATEGY: {s['name']} — status in {label}: {s['status'][market]}. "
                     f"{s['history']} In {label}: {m['read']}")
        for t, n, why in m.get("stocks", []):
            lines.append(f"  example: {t} ({n}) — {why}")
        for f, why in m.get("funds", []):
            lines.append(f"  fund/index example: {f} — {why}")
    for fw in FRAMEWORKS:
        m = fw["markets"][market]
        lines.append(f"FRAMEWORK: {fw['name']} by {fw['founder']} ({fw['dates']}, {fw['role']}). "
                     f"{fw['history']} In {label}: {m['read']}")
        for t, n, why in m.get("stocks", []):
            lines.append(f"  example: {t} ({n}) — {why}")
    lines.append("BOTTOM LINE US: " + BOTTOM_LINE["US"])
    lines.append("BOTTOM LINE INDIA: " + BOTTOM_LINE["IN"])
    return "\n".join(lines)
