# Outreach CRM

I built a small contact notebook because I was cold-emailing companies and losing track of who I had contacted and when to follow up.

## Overview

My research notes, sent emails and reminders used to be separate. I wanted to open
one place and see who I planned to contact, what I had said and who had replied.
This is my personal project for keeping that information together. CRM means
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
mailbox. There is no automatic inbox checking or background sender. I mark an
interview with `interview 1` after recording a reply.

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

The 32 tests cover saved records, personalized previews, real local mail delivery,
follow-ups, replies, duplicate protection, interrupted sends and credential lookup.
GitHub Actions runs the same checks on Python 3.9, 3.12 and 3.13. I use only Python's
built-in modules; there are no packages to install. The local tests exercise the
mail protocol without sending to Gmail or proving that an external inbox received it.
