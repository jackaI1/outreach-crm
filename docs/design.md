# Storage and delivery design

I chose a command-line notebook so it runs without a web server, background worker
or dependencies. The main pieces are:

- `outreach_crm/core.py`: SQLite schema, company/contact records, rendering, stage transitions, reminders and reply matching.
- `outreach_crm/mail.py`: environment/Keychain settings and SMTP delivery over verified TLS.
- `outreach_crm/__main__.py`: commands and readable JSON output, a text format for structured records.
- `outreach_crm/templates/`: editable subject/body templates.
- `outreach_crm/demo_mail.py` and `scripts/demo.py`: an actual loopback SMTP receiver and a complete fictional workflow.
- `tests/test_crm.py`: persistence, transport, workflow and command-line regression checks.

## Data model

One company has research text and a source. A contact belongs to one company and
has a name, address, role and personal reason for writing. Each outbound attempt
stores the rendered content, sender, recipient, stable Message-ID, initial/follow-up
kind, created time, delivery state, confirmation time and reminder interval.
Replies store their text, recording time, origin (manual or exported email) and,
when imported, a unique message identifier. Foreign keys prevent orphan contacts.

The pipeline is `researched -> emailed -> followed_up -> replied -> interview`.
A reply can also move a researched or emailed contact directly to replied. Counts
include zero-value stages. Failed attempts do not advance the pipeline. Follow-ups
require a confirmed initial email. A reply or interview clears the reminder and
blocks further cold outreach. A reply recorded during a send wins over the sender's
later state update. Times in history are UTC; reminder dates follow local time.

## Network boundary and recovery

Before connecting, a `BEGIN IMMEDIATE` SQLite transaction checks the contact and
existing attempts, then durably saves a `sending` attempt. Other processes see that
attempt and refuse another send. No database write lock is held during the network
call. Once the server accepts the message, a transaction marks it sent and updates
the contact. A server rejection records failed; a disconnect during sending records
uncertain. If the process is killed or the final database write fails, the sending
record survives and blocks retries.

A successful SMTP handoff means server acceptance, not delivery to an inbox. SMTP
and SQLite cannot commit together. I do not claim exactly-once delivery across a
crash. I check the mailbox and use `resolve` only after the original process stops.
Resolution records my explanation; confirming delivery schedules a reminder from
that confirmation date. No background or automatic retry occurs. Outgoing records
are an audit trail rather than a queue that drains by itself.

Only a single recipient comes from the selected contact. The template subject is
one line, addresses exclude display names and header injection, and unknown template
variables fail closed. This is a single-user local tool, not a multi-tenant service.
It is intended for a small, deliberate list, not bulk mail. Research/contact edits,
attachments, bounce handling, inbox polling and browser sign-in are not implemented.
I retain private data only on my computer. The public workflow uses no real people,
mailboxes or credentials.

## Credentials and transport

Credentials come from environment variables or the Keychain item
`outreach-crm-gmail` for the exact SMTP account. They are never saved to SQLite.
The app password from the environment wins. Keychain errors are captured and never
printed. SMTP errors use fixed messages so provider responses cannot expose secrets.
The sender address must match the configured account.

Gmail defaults to TLS from connection start on port 465; STARTTLS on 587 upgrades
before login. Both use Python's default certificate verification. Plain SMTP is
only available for literal loopback addresses. The demo flag is stored in the
database and blocks external servers. The [Python SMTP reference](https://docs.python.org/3/library/smtplib.html)
describes the transport methods used here. Account-specific Gmail restrictions still
apply; no real account or network delivery is required by CI.

## Reproduce the evidence

```sh
python3 -m unittest discover -s tests -v
python3 scripts/demo.py
```

The suite contains 32 tests. Local SMTP tests send actual message bytes to a socket
receiver and verify subject/body identifiers and state. Separate tests check both
TLS connection paths and Keychain calls with test doubles, so CI needs no secret.
Command-line tests run the module and demo in fresh processes. Coverage includes
concurrent duplicate prevention, rejection versus uncertain delivery, manual
resolution, thread matching, duplicate imported replies and resource cleanup.
There were no pre-existing tests in the initial repository.

The demo starts with five fictional contacts, sends four initial messages and one
follow-up, records two replies and marks one interview. It asserts five accepted
messages, one contact in each of five stages and two due reminders. The receiver
cannot relay mail. It binds `127.0.0.1` on an operating-system-selected free port,
records ownership, PID, start time, port and cleanup procedure, then closes in a
`finally` path. The demo probes the closed port and removes its temporary database.
No browser pages or persistent processes are created.

GitHub Actions repeats both commands on Python 3.9, 3.12 and 3.13 for branch pushes,
pull requests and main. The tests use built-in `unittest`; SQLite and the email
modules are also part of Python. This keeps the example runnable without installing
any third-party package.
