# ExamFX Weekly Pacing

Rebuilds the **WoW Pacing** tab of the *ExamFX x HMDE - Budget Tracker* from live
ad-platform spend, so nobody has to pull each platform by hand on Monday morning.

It also produces **budget recommendations** - which lines to raise, cut or pause,
by how much, and which campaigns are driving the variance.

---

## What it does

1. Reads the month's budgets from the **2026 Monthly Tracker** tab (it finds the
   month's Budget column by its date header, so no cell references to maintain).
2. Pulls day-by-day campaign spend from Windsor.ai for Google Ads, Bing, Meta and
   LinkedIn - one request per platform for the whole month.
3. Maps every campaign to a business line (Insurance / Securities / Adjusters /
   Brand) and rolls spend up by line and channel.
4. Computes, for each week, the cumulative pacing goal (`monthly budget x share of
   the month elapsed`), actual spend to date, variance and Over/Under status.
5. Writes the rebuilt table back to the **WoW Pacing** tab, preserving the Notes
   column.
6. Writes budget recommendations - which lines to raise, cut or pause, and the
   campaigns driving each - to a **Budget Recommendations** tab, and prints the
   same thing to the terminal.

Analyst notes are matched back by week, category and channel, so comments like
*"Decreased tROAS slightly -Maddi"* survive a rebuild.

---

## Quick start

```bash
pip install -r requirements-dev.txt

# See this week's pacing without touching the spreadsheet.
export WINDSOR_API_KEY=...
python -m examfx_pacing --dry-run

# Rebuild the WoW Pacing tab for real.
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
python -m examfx_pacing --write
```

### Useful flags

| Flag | Purpose |
|---|---|
| `--month 2026-08` | Pace a specific month (defaults to the month containing `--as-of`). |
| `--as-of 2026-08-26` | Pretend it is this date. Useful for backfills and for re-running a past week. |
| `--write` | Write the result to the pacing tab. Without it nothing is written. |
| `--csv pacing.csv` | Also save the full table as CSV. |
| `--spend-csv FILE` | Read spend from a CSV instead of Windsor (backfills, or when Windsor is down). |
| `--budgets FILE` | Read budgets from JSON instead of the tracker tab (lets you run with no Sheets access). |
| `--rules FILE` | Override the campaign-to-category rules. See `category-rules.example.json`. |
| `--tolerance 0.10` | How far off pace a line must be before a budget change is recommended (default 5%). |
| `--no-recommendations` | Pacing table only - skips both the printed and the written recommendations. |
| `--check-auth` | Verify the Windsor key and Google access, then exit. |

---

## Scheduling

`.github/workflows/weekly-pacing.yml` runs every **Monday at 13:00 UTC** (9am ET)
and writes the tab. It can also be run on demand from the Actions tab, with an
optional month, as-of date and dry-run toggle. Each run publishes the table as a
job summary and uploads the CSV as an artifact.

Two repository secrets are required:

| Secret | Value |
|---|---|
| `WINDSOR_API_KEY` | Your Windsor.ai API key. |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | The full service-account JSON key, pasted as-is. |

### Setting up the two secrets

Nothing runs unattended until both exist. Check your work at any point with
`python -m examfx_pacing --check-auth` (see below) - it names whatever is still
missing.

**1. `WINDSOR_API_KEY`**

1. Sign in to [windsor.ai](https://windsor.ai) and copy the API key from your
   account settings.
2. In GitHub: **Settings -> Secrets and variables -> Actions -> New repository
   secret**, named `WINDSOR_API_KEY`.

The key must be on an account that can see all four ad accounts - Google Ads
`997-052-9086`, Bing `180013684`, Meta `253084931845072` and LinkedIn
`518468129`. The preflight probes each one separately, so a connector that is
not shared with your key shows up by name.

**2. `GOOGLE_SERVICE_ACCOUNT_JSON`**

1. In the [Google Cloud console](https://console.cloud.google.com), pick or
   create a project.
2. Enable the **Google Sheets API** for it (*APIs & Services -> Library*).
3. *IAM & Admin -> Service Accounts -> Create service account*. No roles are
   needed - access comes from sharing the sheet, not from IAM.
4. On the new account: *Keys -> Add key -> Create new key -> JSON*. Download it.
5. Copy the `client_email` out of that JSON (it ends
   `@...iam.gserviceaccount.com`) and **share the tracker with it as an
   Editor**. Viewer is not enough; the run writes two tabs.
6. Paste the whole JSON file, unedited, into a repository secret named
   `GOOGLE_SERVICE_ACCOUNT_JSON`.

Step 5 is the one that is usually missed. Sharing as Viewer passes every read
check and only fails when the run tries to write, which is why the preflight
tests write access explicitly.

### Verifying the credentials

```bash
export WINDSOR_API_KEY=...
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
python -m examfx_pacing --check-auth
```

It exits non-zero if anything is wrong, and prints the fix:

```
Credential preflight
====================

  [PASS] Windsor / Google           reachable, 13 row(s) for the probe day
  [PASS] Windsor / LinkedIn         reachable, no spend reported for the probe day
  [PASS] Spreadsheet access         opened the tracker as pacing@proj.iam.gserviceaccount.com
  [PASS] Tab '2026 Monthly Tracker' budgets are read from here
  [PASS] Tab 'WoW Pacing'           the pacing table is written here
  [PASS] Write access               the service account can edit the tracker
```

A channel reporting no spend is a pass, not a failure - LinkedIn routinely has
none. Write access is proven with an empty batch update, so the check never
touches a cell.

The same check runs at the start of every scheduled run, so a missing or
read-only credential fails with a named fix rather than a traceback. To run only
the check, trigger the workflow from the Actions tab with **check_auth_only**.

---

## How the numbers are defined

**Weeks** run Monday to Sunday. The first and last weeks of a month are
truncated to stay inside it, so August 2026 is `8/1-8/2`, `8/3-8/9`, `8/10-8/16`,
`8/17-8/23`, `8/24-8/30`, `8/31`.

**Pacing goal** is cumulative, not per-week:

```
cumulative % = days from the 1st through the week end / days in month
pacing goal  = monthly budget x cumulative %
```

**Status** follows the sheet's own rule: at or above goal reads as `Over`.

**Mid-week runs** clamp the week to the run date. If you run on Wednesday, both
the goal and the actual are measured through Wednesday, so the comparison stays
like-for-like instead of the line looking artificially under-paced.

**Recommendations** project month-end spend from the current daily run rate and
compare it to the budget:

```
projected month-end = spend to date / days elapsed x days in month
suggested daily     = budget remaining / days remaining
```

A line is called out when the projection misses the budget by more than the
tolerance (5% by default), and marked urgent past 15%. Lines that have already
spent their whole month's budget are told to pause rather than given a new daily
number. For each flagged line the top campaigns are listed with their share of
the line and whether they are accelerating or slowing over the last seven days.

### The Budget Recommendations tab

Recommendations are also written to a **Budget Recommendations** tab, so the
advice lives next to the pacing table instead of only in a terminal. The tab is
created on the first run - there is nothing to set up by hand.

One row per category/channel line, biggest lever first:

| Column | Meaning |
|---|---|
| `As Of` | The date the advice was computed. Mid-week runs measure through that day. |
| `Action` | `INCREASE`, `DECREASE`, `HOLD`, `OVER BUDGET`, `ALLOCATE OR PAUSE` or `NOT DELIVERING`. |
| `Priority` | `Urgent` past 15% projected variance, otherwise `Normal`. |
| `Current Daily` / `Suggested Daily` | Today's run rate, and the rate that lands the line on budget. |
| `Daily Change` | How far the daily budget has to move, as a share of today's rate. |
| `Projected Month-End` | Spend to date extrapolated over the whole month. |
| `Recommendation` | The one-line instruction, the same sentence the terminal prints. |
| `Top Campaigns` | The campaigns behind the line, with each one's share and whether it is accelerating or slowing. |

`Daily Change` and `Projected Variance %` are written as fractions so the sheet
can format them as percentages. Lines with no budget get no target daily figure -
there is no daily number that fixes an unbudgeted line, so those cells are blank
and the action is `ALLOCATE OR PAUSE`.

The tab is cleared before each write, so a quieter week cannot leave the previous
week's advice sitting underneath. Point it somewhere else with
`EXAMFX_RECOMMENDATIONS_TAB` if the name should differ.

---

## Campaign mapping

Campaigns map to business lines by keyword, case-insensitively, first match wins:

| Order | Category | Matches |
|---|---|---|
| 1 | Adjusters | `adjuster` |
| 2 | Securities | `securit` |
| 3 | Insurance | `insurance` |
| 4 | Brand | `brand` |

Order matters. `B2C - Insurance - Life & Health - Brand - PPC` is **Insurance**
spend - brand terms inside the Insurance line - while `B2C - ExamFX - Brand - PPC`
is the standalone Brand line. Because `Brand` is checked last, the business lines
always win.

Meta's underscore naming (`B2C_General_Insurance_Prospecting_Meta_LAL`) is handled
by the same keyword rules.

**A campaign that matches nothing is reported, never silently dropped.** New
campaigns that do not fit the convention show up under "Needs attention" so the
rules can be updated. To change them, copy `category-rules.example.json`, edit,
and pass `--rules`.

---

## Things the automation fixes

Reproducing the August 2026 tab against the real platform data surfaced two
problems with the manual process:

- **Securities Meta spend was being folded into the Insurance/Meta row** in weeks
  1-3 (it was entered correctly in week 4). There is no Securities/Meta row in the
  sheet because that line has no budget, so the spend had nowhere else to go. The
  tool gives it its own row and flags it.
- **Spend on lines with no budget was invisible.** Any line with spend now gets a
  row and a warning, whether or not it was budgeted.

Both are covered by tests in `tests/test_golden_august_2026.py`.

---

## Channels with no data feed

`Programmatic` has a budget in the tracker but no Windsor connector. Rather than
writing a misleading `$0`, those lines are left out of the table and reported
under "Needs attention" as still needing a manual update. Add a channel to
`DEFAULT_CHANNELS` in `examfx_pacing/config.py` once a connector exists.

---

## Tests

```bash
python -m pytest -q
```

`tests/test_golden_august_2026.py` is the important one: it replays real
month-to-date spend from each week boundary of August 2026 and asserts the result
matches the numbers a human typed into the tracker. Bing reconciles to the cent;
Google and Meta match within about a dollar, which is normal post-hoc drift in
reported ad spend.

---

## Layout

| Path | Purpose |
|---|---|
| `examfx_pacing/weeks.py` | Month/week calendar and cumulative pacing percentages. |
| `examfx_pacing/categories.py` | Campaign name to business line. |
| `examfx_pacing/spend.py` | Windsor.ai and CSV spend sources, with retries. |
| `examfx_pacing/sheets.py` | Reading budgets/notes and writing the pacing and recommendations tabs. |
| `examfx_pacing/pacing.py` | Building the pacing table. |
| `examfx_pacing/recommendations.py` | Budget actions and campaign drivers. |
| `examfx_pacing/report.py` | Terminal and CSV rendering. |
| `examfx_pacing/preflight.py` | Credential checks behind `--check-auth`. |
| `examfx_pacing/run.py` | Orchestration. |
| `examfx_pacing/cli.py` | Command line interface. |

---

## Windsor endpoint

Spend is fetched from `https://connectors.windsor.ai/{connector}` with
`api_key`, `date_from`, `date_to`, `fields` and `select_accounts`. Both the
`{"data": [...]}` and `{"result": [...]}` response envelopes are accepted.

If Windsor changes its REST contract, override the base URL with the
`WINDSOR_BASE_URL` environment variable rather than editing code. Account IDs
live in `examfx_pacing/config.py` and come from the HMDE client registry.
