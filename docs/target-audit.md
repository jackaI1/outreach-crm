# Target lists and the weekly workbook

I wanted to learn how to compare a partner's company list with my outreach notebook without connecting to a company system. These examples use invented companies, owners and opportunities. They read report exports shaped like Salesforce Account, Contact and Opportunity reports. There is no live Salesforce connection, login, API call or automatic sending.

## Try a complete example

```sh
python3 -m pip install -r requirements-reporting.txt
python3 scripts/target_demo.py --output /tmp/target-demo
python3 -m outreach_crm.target_audit --db /tmp/target-demo/targets.sqlite3 review
```

The demo saves seven targets, three review entries, four distinct opportunities and `weekly.xlsx`. It records a fictional message and reply for T1. The week of February 2 through 8 has three funnel adds, including one still unlinked because its account needs review. Funnel add means a new opportunity identified by its report ID, not an email sent or revenue earned.

I can inspect T3's candidates, approve one with a reason, then move it through outreach:

```sh
python3 -m outreach_crm.target_audit --db /tmp/target-demo/targets.sqlite3 resolve T3 --account A3 --note 'Checked the fictional partner entry against account A3'
python3 -m outreach_crm.target_audit --db /tmp/target-demo/targets.sqlite3 status T3 outreach --on 2026-02-08 --note 'Fictional first message'
python3 -m outreach_crm.target_audit --db /tmp/target-demo/targets.sqlite3 status T3 engaged --on 2026-02-09 --note 'Fictional reply'
python3 -m outreach_crm.target_audit --db /tmp/target-demo/targets.sqlite3 history T3
python3 -m outreach_crm.target_audit --db /tmp/target-demo/targets.sqlite3 report --start 2026-02-02 --end 2026-02-09 --goal 5 --output /tmp/target-demo/updated.xlsx
```

The report includes stages through its end date. Stages advance one step at a time: researched, outreach, engaged, qualified, closed. These are notes I enter deliberately, independent of the original notebook's email-delivery stages. A closed target remains in coverage and in the count of targets that reached engagement. None of these commands sends email or writes into Salesforce.

## Import a new report snapshot

```sh
python3 -m outreach_crm.target_audit --db /tmp/my-targets.sqlite3 import \
  --targets examples/target-audit/partner-a.csv \
  --targets examples/target-audit/partner-b.xlsx \
  --layout examples/target-audit/layout.json \
  --accounts examples/target-audit/accounts.csv \
  --contacts examples/target-audit/contacts.csv \
  --opportunities examples/target-audit/opportunities.csv \
  --territories examples/target-audit/territories.csv \
  --today 2026-02-08 --stale-days 30
```

`--targets` can appear several times. CSV files can have a UTF-8 byte-order mark; Excel files must be `.xlsx`. The mapping file is keyed by input filename. Each entry can set `sheet`, a one-based `header_row`, and `columns` mapping internal field names to the file's actual headers. The second partner fixture uses a different column order, a title and a named sheet. Unknown columns are ignored. Missing or ambiguous required headers stop the import instead of silently dropping data.

| Input | Internal columns (all headers required, some cells may be blank) |
| --- | --- |
| Partner targets | `target_id`, `company`, `territory`, `contact`, `segment` |
| Accounts | `id`, `name`, `territory`, `sales`, `architect`, `last_activity` |
| Contacts | `id`, `account_id`, `email` |
| Opportunities | `id`, `account_id`, `name`, `created`, `amount`, `stage` |
| Territory assignment | `territory`, `sales`, `architect` |

The checked-in CSVs show recognized Salesforce-style labels, including Account Owner: Full Name and Last Activity Date. These are fictional flat, detail-only report exports, not a claim that every Salesforce report has the same columns. Map custom field names explicitly. Export ISO dates (`YYYY-MM-DD`), numeric amounts without currency symbols or separators, and one currency per report. Remove report totals, grouping rows and footers first. Excel date cells are accepted. Target IDs must be nonempty and unique across partner lists. Keep those IDs stable between imports; a different company needs a different ID.

Each import replaces the active source snapshot atomically. Activity history stays saved by target ID. A new snapshot clears current match approvals so changed source data gets reviewed again; earlier review notes remain in `history`. Opportunities accumulate once per Opportunity ID. Stage and amount updates revise that opportunity, while changing its account or creation date is rejected. An opportunity absent from a later export remains in the ledger; this is not deletion synchronization.

## Matching and checks

Names lose accents, punctuation, letter case and trailing company suffixes such as Inc or LLC. An ampersand becomes “and”. A unique exact normalized key wins. Otherwise, a text similarity score from 0 to 1 ranks candidates. Only one candidate at or above 0.92, with no competing candidate at or above 0.80, is linked automatically. Other candidates at or above 0.80 go to review. Lower scores remain unmatched. This is a conservative text heuristic, not proof of company identity.

Duplicate normalized targets, duplicate account keys and conflicting account IDs go to review. Multiple targets cannot quietly link the same account. Manual approval must choose a listed candidate with a note; duplicate Account IDs must be corrected in the export first. Duplicate partner entries cannot both be approved against the same account.

The territory table assigns both Sales and solution-architect owners, the person who helps plan the technical solution. Missing owners and duplicate territory rows remain unassigned. For linked targets I compare the expected territory and owners with the Account report, and the partner contact email with Contact rows. A contact appearing under another account is a conflict, even when one copy belongs to the matched account. Missing CRM owners, conflicting assignments, invalid/future activity dates and stale activity are visible flags. “Stalled” means no known activity or more than the configured number of days since it; exactly the cutoff is still current. Unmatched targets have no known CRM activity and start as stalled; unresolved candidates show activity unverified. Recorded outreach activity also counts toward recency.

## Read and refresh the workbook

`Weekly` shows total targets, coverage, engagement, funnel adds, unlinked adds and progress toward an editable positive goal. Covered means linked to an account with both expected owners assigned. A covered row can still have a CRM conflict to investigate; read `Targets` for its flags. Engagement means the target has reached engaged or a later stage. Coverage describes the active snapshot at the report end date, not historical membership; an end date before the imported snapshot is rejected.

Three native Excel PivotTables summarize coverage by segment and owner, and weekly funnel adds and amounts by owner. A PivotTable groups detail rows into totals; its pivot cache stores the source records so Excel can refresh the totals. Two real caches back the three tables. The chart links to the segment pivot cells. All report data is fictional in the demo.

`Targets` and `Funnel` use XLOOKUP for Sales owners and INDEX/MATCH for territory and architect details from `Reference`. These spreadsheet formulas find the matching territory key. Missing keys or blank reference values show `MISSING`; duplicate keys show `DUPLICATE` rather than choosing the first row. The formulas use exact lookups. XLOOKUP needs modern Excel or LibreOffice 24.8 or newer. I edit owner values in `Reference`, then use Data > Refresh All. Adding rows or changing territory keys requires regenerating the workbook so every table, lookup range, cache and chart range expands together. Statuses, coverage indicators and reporting dates are generated from the notebook; I update them through the commands and regenerate.

Funnel adds count distinct opportunities created in the inclusive reporting window, including opportunities whose account still needs review. Unlinked entries appear separately and are never credited to a target. They receive owner `MISSING` until a unique target link exists. The target coverage pivots only credit uniquely linked opportunities, so their funnel totals may be lower than the all-opportunity headline. Amounts are opportunity values in the input's single currency, not bookings. The report is a point-in-time export, not a forecast or a live sync.

## Verification

```sh
python3 -m unittest discover -s tests -v
```

The normal suite reopens the workbook and checks its PivotTable definitions, unique cache relationships, source ranges, records, lookup formulas and chart links. Linux CI additionally installs LibreOffice and its Python bridge, opens a hidden document, recalculates formulas, refreshes all three pivots, saves and reopens it. Tests independently aggregate source values and compare every pivot group and grand total, every owner/territory lookup and the goal calculation. They change reference owners and source metrics, poison saved pivot totals, then repeat refresh and reconciliation to prove the file works beyond its saved values. Missing and duplicate keys are exercised in the calculation engine. The isolated process, temporary profile and notebook are cleaned up even on failure. The recalculated fictional report is downloadable from the workflow's `fictional-weekly-report` artifact.

The low-level pivot writer is isolated in `weekly_report.py` because openpyxl has no high-level API for creating pivots. Its [pivot documentation](https://openpyxl.readthedocs.io/en/stable/pivot.html) describes that limitation. LibreOffice documents [XLOOKUP availability from 24.8](https://help.libreoffice.org/latest/en-US/text/scalc/01/func_xlookup.html). CI tests LibreOffice's compatibility, not an installed copy of Microsoft Excel.
