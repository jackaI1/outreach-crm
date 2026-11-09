# Outreach CRM

I built a small contact notebook because I was cold-emailing companies and losing track of who I had contacted and when to follow up.

## Overview

My research notes, sent emails and reminders used to be separate. I wanted to open
one place and see who I planned to contact, what I had said and who had replied.
This is my personal project for keeping that information together. I can also compare
partner target lists with offline CRM report exports, review uncertain company
matches and make a weekly Excel report. It has no live Salesforce connection. CRM means
customer relationship management; here it is simply my outreach notebook.

I save a company, the research that caught my attention and the person I want to
contact. I preview a short email that uses those notes, then choose whether to send
it. The notebook keeps the message, sets a follow-up date and counts contacts at
five stages: researched, emailed, followed up, replied and interview. When I record
a reply, its follow-up reminder clears.

I use it through typed commands in a terminal. The public examples are fictional.
To try the complete example, install Python 3.9 or newer, download this repository,
open a terminal in its folder and run:

```sh
python3 scripts/demo.py
```

This delivers five messages to a receiver on my own computer, shows one contact at
each stage and finds two follow-ups due. It sends no real email, then removes its
temporary records and closes the receiver.

## Keep an example notebook

I can also keep fictional records between commands:

```sh
python3 -m outreach_crm --db /tmp/outreach-demo.sqlite3 demo
python3 -m outreach_crm --db /tmp/outreach-demo.sqlite3 list
python3 -m outreach_crm --db /tmp/outreach-demo.sqlite3 send 1
python3 -m outreach_crm --db /tmp/outreach-demo.sqlite3 pipeline
python3 -m outreach_crm --db /tmp/outreach-demo.sqlite3 history 1
```

`send 1` previews the first contact's email. It saves and sends nothing unless I add
`--deliver`. A demo notebook only allows delivery to a local receiver, even if Gmail
credentials are available. The `demo` command refuses to replace existing records.
I can delete this example afterward with `rm /tmp/outreach-demo.sqlite3`.

## Use my own notebook

I start an empty notebook with `python3 -m outreach_crm init`. Without `--db`, it
stays outside the repository at `~/.local/share/outreach-crm/crm.sqlite3`.
The [walkthrough](docs/usage.md) explains adding research and contacts, setting up
Gmail, choosing a template, sending an email and recording a reply.

I send one contact at a time after reviewing the preview. Gmail delivery uses SMTP,
the protocol for handing an email to a mail server. I keep its app password in the
macOS Keychain, the computer's password store, or a private environment variable.
Nothing needs an account to run the example. I never put my private notebook,
exported replies or credentials in this repository.

`due` shows reminders when I run it. `reply 1 --note "A fictional reply"` records a
reply by hand, and `import-reply message.eml` reads an email I have exported from my
mailbox. There is no automatic inbox checking or background outreach sender. I mark an
interview with `interview 1` after recording a reply.

## Check target lists and make a weekly Excel report

I added a separate target notebook because I wanted to learn how to compare company
lists without connecting to a company system. It reads CSV and Excel partner lists
and fictional Salesforce-shaped Account, Contact and Opportunity report exports.
It checks company names, shows uncertain matches for my review, assigns owners from
an editable territory table and flags conflicting contacts, missing owners and old
activity. I record outreach and replies, and new opportunities count once toward
my weekly goal.

```sh
python3 -m pip install -r requirements-reporting.txt
python3 scripts/target_demo.py --output /tmp/target-demo
python3 -m outreach_crm.target_audit --db /tmp/target-demo/targets.sqlite3 review
```

The example saves seven fictional targets, three entries to review and
`/tmp/target-demo/weekly.xlsx`. The workbook groups coverage by segment and owner,
shows new opportunities and progress toward a goal, and includes a linked chart.
Its native PivotTables are spreadsheet summaries I can refresh. XLOOKUP and
INDEX/MATCH formulas find owner and territory details from a reference sheet;
missing or duplicate keys stay visible. Nothing is sent or changed in Salesforce.
The [target-list guide](docs/target-audit.md) has the import command, review and
status steps, report definitions, Excel refresh instructions and limitations.

## Campaign performance

I can compare outreach messages by template and audience segment to see where I might improve. I keep one anonymous row per sent message in a CSV file (a simple spreadsheet table), with a 1 or 0 for each observed open, click, reply and conversion. A conversion is the outcome I choose for the campaign, such as an interview. These are manual observations: the notebook does not secretly track readers or automatically collect opens and clicks.

To try the fictional example, install the reporting packages and run:

```sh
python3 -m pip install -r requirements-reporting.txt
python3 -m outreach_crm.campaign examples/campaign.csv --previous-start 2026-01-01 --previous-end 2026-01-31 --start 2026-02-01 --end 2026-02-28 --output /tmp/campaign-report
```

Open `/tmp/campaign-report/campaign-summary.xlsx` for the comparison and breakdown, or `campaign-summary.pptx` for an editable PowerPoint slide. Each rate is the number of sends with that outcome divided by all sends in that period. The default benchmarks are illustrative targets, not industry averages; I can set my own using `--benchmark-open 0.5`, for example. The [campaign guide](docs/campaign-reporting.md) explains the columns, comparisons and limitations.

## Weekly reminder report

I can also receive a weekly email with my contact counts and the number of follow-ups due or overdue. It appends the same totals to a CSV file, a simple table I can open in a spreadsheet. The report contains counts, not contact names. It helps me see when reminders are piling up without opening the notebook every day.

To set it up, I run n8n, a tool that connects steps in an automated workflow, with Docker Compose. I import the included workflow, connect my email account and activate it. The computer must be running at the scheduled time. The [weekly report setup](docs/weekly-report.md) has the exact steps and explains retries and privacy.

## Design and checks

I keep the notebook in SQLite, a database stored in one local file. Email templates
are ordinary text files. Delivery attempts preserve the exact subject, body,
recipient and message identifier, so I can check what happened later. An interrupted
send blocks another attempt until I check my mailbox and resolve it.
The [design notes](docs/design.md) explain delivery recovery and the limits.

```sh
python3 -m unittest discover -s tests -v
python3 scripts/demo.py
```

The 64 tests cover saved records, personalized previews, real local mail delivery,
follow-ups, replies, duplicate protection, interrupted sends credential lookup, report counts and the workflow export.
The target tests also cover ambiguous and duplicate matches, conflicting records,
atomic imports, saved reviews and status history, repeated opportunity imports,
and native Excel caches. GitHub Actions runs the suite on Python 3.9, 3.12 and 3.13.
One workbook test runs in a separate Linux job with headless LibreOffice: it
recalculates lookups, refreshes all three pivots and compares their results with
independent totals, then changes inputs and checks again. The CRM uses Python’s built-in modules; campaign exports use the packages in `requirements-reporting.txt`, and the optional reminder report runs in a Docker container. The local tests exercise the
mail protocol without sending to Gmail or proving that an external inbox received it.
