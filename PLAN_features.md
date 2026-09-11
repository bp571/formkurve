# Implementation plan: team detail, remaining schedule, season simulation, archive

Temporary hand-off document. Delete it (do not commit it) once the four features are in.
Read `CLAUDE.md` first — every rule there still applies, in particular: no recency in the base
rating, no per-team venue terms, no overclaiming on the page, and every explanation sits next to
the thing it explains. Nothing below changes the model or any parameter in `src/config.py`.

Decisions already taken with the owner (do not re-open):

| Question | Decision |
|---|---|
| Team detail | One panel on `index.html`, team chosen by clicking a row or jersey. No per-team pages. |
| Promotion / relegation rule for the simulation | 1st goes up, 13th and 14th go down. No play-off places. |
| Remaining schedule | A `Rest` column in the form table (hidden on phones) **and** the fixture list in the team panel. |
| Archive page for a finished season | Dashboard only — pitch, form table, chart, team panel. No forecast, predictor table, simulation or `Rest` column. |

The four features share one switch: **`scheduled` rows exist for the season or they do not.**
A finished season (and therefore every archive page) has none, and then the `Rest` column, the
simulation and the forecast block are simply not rendered. Implement that as one boolean computed
once in `render()` and passed down; do not special-case "archive" anywhere else.

Implementation order: **A → B → C → D** below. A is plumbing that the other three depend on.

---

## A. Season archive (point 6)

Goal: `python src/report.py --season 2025/26` writes `docs/2025-26/index.html` instead of
overwriting `docs/index.html`, and every page carries a season switcher.

### Python — `src/report.py`

1. `season_path(season)`: `OUT_HTML` for `SEASON_CURRENT`, otherwise
   `docs/<season with '/' → '-'>/index.html`. `write_report(season, rows, path=None)` uses it
   when `path` is `None`. `run.py` needs no change — it already calls `write_report(season, …)`.
2. `render()` gets the scheduled rows too: `scheduled = load(season, "scheduled")` (from
   `predict`), `has_future = bool(scheduled)`. `forecast_section` already loads them itself —
   pass them in instead so the file is read once.
3. The whole outlook block (`fcast`, `predictors`, later the simulation) renders only when
   `has_future`. Today `predictor_section()` renders even for a finished season (`solo` branch);
   that branch goes away — the archive page has no prognosis part at all.
4. Season switcher in the masthead: a small list of every season in `STAFFEL_IDS`, newest first,
   the current page's season set as text rather than a link. Links are **relative**: from
   `docs/index.html` an archive is `2025-26/`, from an archive page the current season is `../`
   and a sibling archive `../2024-25/`. Write a helper `season_href(from_season, to_season)`.
5. Everything else in the page already copes with a finished season (the lede, the badges, the
   chart). Verify once by rendering 2025/26 and reading through.

### Docs

- `CLAUDE.md` → *Commands*: note that `--season` writes to the archive path. → *The page*: one
  line that archive pages carry the dashboard only, and why (nothing is scheduled, so nothing
  to forecast).
- Commit `docs/2025-26/index.html` — it is derived output like `docs/index.html`.

### Check

- `python src/report.py --season 2025/26` writes to `docs/2025-26/index.html`; `docs/index.html`
  is untouched (compare mtime).
- `python src/report.py` writes the current page with a link to `2025-26/`.
- Both files pass `check_css`. Open both in a browser: the switcher works in both directions.

---

## B. Remaining schedule (point 2)

Goal: one number per team — the mean season strength of the opponents still to play — plus the
list of those fixtures for the team panel.

### Python — `src/report.py`

```python
def remaining(scheduled, powers):
    """Per team: fixtures still to play, oldest first, and the mean season power
    of the opponents. `powers` is team -> season power score from build_table."""
    # returns {team: {"fixtures": [(date, matchday, opponent, is_home), ...],
    #                 "n_home": int, "n_away": int, "difficulty": float | None}}
```

- Strength = the opponent's **season power score** (`t["power"]`, shrunk, 0–100), unadjusted
  for venue. Deliberate: `HFA = 100` Elo is 33 points on the 0–100 scale and would swamp the
  number. The venue split is shown beside it as `4 H / 5 A` instead, and the legend says so.
- `difficulty` is `None` for a team with nothing left (end of season, or one team's fixtures all
  postponed past the data) → render `–`.
- Put `remaining[team]` onto each table row in `build_table()` **only if** `build_table` gets
  the scheduled rows as an optional argument (`build_table(rows, scheduled=())`), so `run.rank()`
  and the backtest keep working unchanged.

### Page

- New column `Rest` in the form table between `Diff` and `Tabelle`, class `s-hide`, rendered
  only when `has_future`. Value: `num(difficulty)` in the quiet `td.season` style, with the
  `4 H / 5 A` split as a muted 12.5px line beneath (same treatment as `.rt` under the club name).
  The column is context, not a headline — it must not compete with the form figure.
- Legend (the `.hint.legend` paragraph): one sentence — *Rest ist die mittlere Saisonstärke der
  verbleibenden Gegner, ohne Heimvorteil verrechnet; die Aufteilung dahinter sagt, wie viele
  davon zu Hause sind.*
- `run.rank()` terminal output: leave as is.

### Check

- Every team's `n_home + n_away + matches played == 26` on the current season.
- The 2025/26 archive has no `Rest` column.

---

## C. Team panel (point 1)

Goal: one section **Team im Detail** directly under the dashboard grid (form table + chart)
and above the forecast block, showing the team the reader last clicked. Rendered for all 14
teams up front, all but one hidden — no data in JS, no fetches.

### Python — `src/report.py`

New per-team data (extend `build_table()` or a separate `team_details(rows, scheduled, table)`
called from `render()`):

1. **Head**: crest, name, form rank + form, season power, official position, record, and the
   home/away split of the record (`W-D-L` at home, `W-D-L` away). All available from
   `team_stats`-style aggregation; add a `venue` split to `team_stats` or compute it in the new
   function — do not duplicate the W/D/L logic a third time, reuse `outcome_of` from `backtest`.
2. **Season chart**: render `svg_chart(table, matchdays, "series", "season")` **once** for the
   page (it draws all 14 lines; the existing `.line` CSS shows them grey until selected). The
   JS highlights only the panel's team in it. This is the existing season-power chart that the
   form rebuild removed from the page; it comes back here as the team's season trajectory.
3. **Results list**: every played match of the team, chronological: date, matchday, opponent
   with `H`/`A`, score from the team's perspective, and — current season only — what the model
   gave the actual outcome beforehand:
   - Source: `predict.walk_forward(played)` returns `(row, xg_diff)` for every match from the
     `MIN_MATCHES`th on; `probabilities(xg_diff, calibration())` turns it into `(p_away,
     p_draw, p_home)`. Index with `outcome_of(hg, ag)`. Build that dict **once** per render
     (keyed by `match_id`), not per team.
   - Display: the probability of what actually happened, e.g. `Sieg 41 %`, in the muted style.
     Matches before `MIN_MATCHES`: `–`. Archive pages: the column is absent (the calibration is
     fitted on 2025/26, so on that page it would be in-sample).
   - **One editorial marker, fixed rule with a floor**: *Überraschung* on the team's match
     whose actual outcome had the lowest prior probability, only if that probability is below
     **25 %**. Same amber badge treatment as *Formsprung*. Below the floor, no badge.
4. **Remaining fixtures**: from B — date, matchday, opponent with `H`/`A`, opponent's season
   power. Absent when `has_future` is false.

### Page and JS

- Section placement: after the `.dash` grid, before `{outlook}`; same max-width rules as
  `.fdash` (1080px stacked, full measure on wide screens).
- Markup: one `<section class="tdet" data-rank="i" hidden>` per team; the one for rank 0 is
  shown on load.
- JS: extend the existing handlers. Clicking a row or jersey **still toggles the highlight**
  (existing behaviour) **and** sets the panel to that team, independent of whether the toggle
  selected or deselected it. Add a `detail` variable next to `selected`, an `applyDetail()`
  that flips `hidden` on the panels and moves the `.sel` class on the `season` chart lines.
  Keyboard: the rows are already focusable; Enter/Space go through the same path.
- Do not add a team dropdown — the table and the pitch are the selector.

### Check

- Click a row: panel switches, form-chart highlight toggles as before, season chart highlights
  the panel's team only.
- The team with the least-likely result of the season carries *Überraschung* only if that
  probability is < 25 %. Verify against `python src/predict.py`-style output for one team by
  hand.
- Row counts: results list + remaining list == 26 per team.

---

## D. Season simulation (point 3)

Goal: for the current season, the probability per team of finishing first (promotion) and of
finishing 13th or 14th (relegation), plus the expected final points. Derived from the Poisson
model the forecast already uses; **no new modelling**.

### Python — `src/predict.py`

```python
SIM_RUNS = 4000
SIM_SEED = 1  # fixed: the page must be reproducible from matches.csv alone

def simulate_season(played, scheduled, runs=SIM_RUNS, seed=SIM_SEED):
    """Monte Carlo over the remaining fixtures. Returns {team: {"pts": current,
    "exp_pts": mean final, "p_first": float, "p_bottom2": float}} or None below
    MIN_MATCHES / with nothing scheduled."""
```

- Fit `poisson_model(played)` **once**. For every run: start from the current points, goal
  difference and goals scored (reuse `report.team_stats` or a local equivalent — but
  `predict` must not import `report`, that would be circular; compute the three totals locally).
  For every scheduled row draw `hg, ag ~ Poisson(expected_goals(model, home, away))` with a
  Knuth draw on `random.Random(seed)` — no numpy, the project is pure Python. Rank with the
  same tie-break as `report.official_positions` (points, goal difference, goals scored), then
  team name as a deterministic last resort. Count first place and places 13–14, accumulate
  final points.
- All scheduled rows count, including postponed ones from earlier matchdays.
- Sanity check inside the function: `abs(sum(p_first) - 1) < 1e-9`, same for `p_bottom2 == 2`.
  Raise if not — a wrong table is worse than no table.
- Runtime target: under 10 s in pure Python at 4000 runs (≈ 600k fixtures). Measure; if it is
  slower, drop to 2000 and say so in the page note (resolution ±1 pp either way).
- `__main__`: print the table after the forecast.

### Page — `src/report.py`

- New section **Saisonausblick** under the forecast/predictor row, rendered only when
  `simulate_season` returns something. Same `.card` table: team (crest, name), current points,
  expected final points, *Meister* %, *Abstieg* %. Sorted by expected final points. Show
  `<1 %` below 0.5 %, never `0 %` — a zero the simulation cannot actually assert.
- One bar per row for the two probabilities, using `--up` for first and `--down` for bottom-two,
  same 5px `.bar` treatment as the forecast. No third colour.
- Note under the table (the only prose; keep the page's voice):
  *Wie das gerechnet wird* — the remaining `N` fixtures are played `SIM_RUNS` times with the
  same expected goals the forecast uses, the tables are counted; *was das nicht ist* — the
  strengths are the current fit and are not themselves varied, so the spread is if anything
  too narrow; early in the season the current fit is mostly the league average, so the
  percentages mostly restate the table and the number of games left; a team at `<1 %` is not
  at zero. Link the wording to the forecast's own disclaimer: same model, same limits.
- Editorial: **no badge** in this section. Nothing here needs pointing at.

### Docs

- `CLAUDE.md` → *The forecast*: add the simulation as a third piece of the same block, with the
  two rules that must not be undone: it uses the forecast's Poisson fit unchanged (one model for
  the section, as before), and it is seeded so the page is a pure function of `matches.csv`.
- *Commands*: `python src/predict.py` now also prints the simulation.

### Check

- Column sums: `p_first` → 100 %, `p_bottom2` → 200 %.
- Two runs of `python src/report.py` produce byte-identical simulation tables (seeded).
- End of season (`--season 2025/26`): no section.

---

## Frontend: how to build the HTML/CSS

Invoke the **`frontend-design:frontend-design`** skill before writing any markup or CSS, and
work inside the page's existing visual language rather than beside it. The brief for the skill:

- **Subject and object**: a notice pinned up after the weekend — uncoated paper, printing ink,
  one narrow grotesque for figures (`--display`), a humanist sans for names and prose
  (`--body`), a hand face (`--hand`) **only** for the editorial markers. Tokens live on `:root`
  in `render()`; use them, add none. Green (`--up`) and wine (`--down`) mean above/below the
  50-point average everywhere; amber (`--mark*`) means "a person wrote this"; the pitch is the
  only dark surface. Do not introduce a new colour, a new radius, a card shadow, all-caps
  labels or eyebrow labels.
- **The team panel is the one new thing the reader has not seen** — spend the design attention
  there. It is a sheet about one club: the results list is the spine, the season line beside
  it, the remaining fixtures underneath. Ask what a coach would pin next to the table, not what
  a dashboard widget looks like. The simulation table and the `Rest` column are quiet
  additions in the existing table idiom.
- **Structure encodes information**: the results list is a true sequence, so a matchday
  number per row is right; the simulation table is not, so no numbering there.
- Copy in German, same register as the page (plain, second person avoided, sentence case,
  no exclamation marks). Every note sits next to its table. The disclaimer wording in D above
  is required content, not optional.
- Responsive as the rest of the page: `s-hide` on phones for the `Rest` column, the panel
  stacks, the season chart gets the same 19px tick rule under 700px.
- Keep CSS comments balanced — `check_css()` refuses to write the page otherwise. Match the
  comment density of the existing stylesheet: comments say *why* a rule exists.
- Take a screenshot of the rendered page at 1440px and at 390px before finishing and fix what
  looks wrong; do not ship from the code alone.

## Out of scope

Per-team pages, a team dropdown, play-off places, parameter uncertainty in the simulation,
storing simulation output, any change to `config.py`, `rating.py`, `score.py`, `backtest.py`,
`explore_predictors.py`, the scraper or the parser. Points 8–15 from the feature list get their
own plan afterwards.
