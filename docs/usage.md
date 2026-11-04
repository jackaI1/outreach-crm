# My outreach workflow

I keep research next to the person I intend to contact, then review one message at
a time. All names, addresses, links and reply text below are fictional.
Run the commands from the repository folder. `python3 -m outreach_crm --help`
lists commands, and each command accepts `--help`.

## Start with research

These commands create the default private notebook outside Git. I replace the
fictional details locally when I use it myself. The printed IDs identify records;
if my notebook already has entries, I use the returned IDs instead of `1`.

```sh
python3 -m outreach_crm init
python3 -m outreach_crm company --name 'Example Instruments' \
  --research 'your article on miniature sensor test fixtures' \
  --source 'https://instruments.example/research'
python3 -m outreach_crm contact --company 1 --name 'Demo Engineer' \
  --email 'engineer@instruments.example' --role 'Test team lead' \
  --reason 'I am interested in how your team validates small sensors and would value a short conversation.'
python3 -m outreach_crm list
python3 -m outreach_crm send 1
```

I check the company source myself and choose a relevant contact. The notebook does
not scrape addresses or invent research. The reason is the sentence explaining why
I am contacting this particular person. A company can have several contacts; email
addresses are unique across the notebook. A duplicate is rejected instead of
silently overwriting my notes.

## Set up Gmail privately

The demo needs no credentials. For real delivery I use a Gmail account that permits
[app passwords](https://support.google.com/accounts/answer/185833). Google requires
2-Step Verification, and some managed accounts do not offer app passwords. I use an
app password instead of the account password. This client does not implement OAuth,
the browser-based sign-in alternative.

On macOS I open **Keychain Access**, choose **File > New Password Item**, set the
item name to `outreach-crm-gmail`, set the account to my complete sending address,
and enter the app password in the password field. The command-line client reads
that item using `security find-generic-password`; it never prints the returned value.
If access is denied or the item is absent, I fix access or use an environment
variable rather than trying to work around Keychain permissions.

I set the sending address in the terminal. The address below is a placeholder:

```sh
export CRM_SMTP_USER='sender@example.invalid'
```

Alternatively, in zsh or bash I can enter the app password without displaying it
or placing the password itself in shell history:

```sh
read -rs CRM_SMTP_PASSWORD
export CRM_SMTP_PASSWORD
```

I type the app password and press Return when `read` waits. I do not paste a real
password into a command, commit it, or put it in `.env.example`. The app reads the
environment directly; it does not automatically load `.env` files. The environment
password takes precedence over Keychain.

Defaults are `smtp.gmail.com`, port `465`, with encrypted SMTP. For STARTTLS,
which upgrades a mail connection to encryption before authentication:

```sh
export CRM_SMTP_SECURITY=starttls
export CRM_SMTP_PORT=587
```

I can set `CRM_SMTP_HOST` for another SMTP provider. Unencrypted mode (`plain`) is
restricted to literal `127.0.0.1` or `::1`, for local test receivers only. A demo
notebook rejects every non-loopback server. No live Gmail account was used for the
public demonstration. Google's [SMTP documentation](https://developers.google.com/workspace/gmail/imap/imap-smtp)
describes the server connection and authentication options.

## Review, send and follow up

I replace the example contact with an intended recipient in my private notebook,
check the preview and explicitly choose delivery:

```sh
python3 -m outreach_crm send 1 --sender-name 'Demo Sender'
python3 -m outreach_crm send 1 --sender-name 'Demo Sender' --deliver --follow-up-days 7
python3 -m outreach_crm history 1
python3 -m outreach_crm due
python3 -m outreach_crm pipeline
```

The sender must match `CRM_SMTP_USER`. Successful server acceptance changes the
contact to `emailed`; a preview or rejected send does not. Seven days later, `due`
includes the contact. `due --on 2026-12-01` checks another date without changing it.
Reminders use the computer's local calendar date. There are no notifications while
the command is closed.

```sh
python3 -m outreach_crm send 1 --kind followup
python3 -m outreach_crm send 1 --kind followup --deliver
```

A follow-up requires an earlier successful email and cannot be sent before its
reminder date. To intentionally change the date I use
`python3 -m outreach_crm remind 1 --on 2026-12-01`.
The follow-up adds an email thread reference and changes the stage to `followed_up`.
I can follow up again after the next reminder, but each send is my decision.

## Change the wording

I copy one of `outreach_crm/templates/initial.txt` or `followup.txt` to a private
text file. The first line is the subject, followed by a blank line and the body.
Placeholders insert saved values: `${first_name}`, `${name}`, `${company}`,
`${research}`, `${source}`, `${role}`, `${reason}`, `${email}` and `${sender_name}`.
`$$` means a literal dollar sign. Unknown placeholders cause an error.

```sh
python3 -m outreach_crm send 1 --template /tmp/my-outreach.txt
python3 -m outreach_crm send 1 --template /tmp/my-outreach.txt --deliver
```

I edit the contact-specific reason when adding the contact and edit the private
template for later wording. The current CLI does not edit existing research or
contact fields. Sent messages keep their own snapshots even if I change templates.

## Record a reply

I read incoming email in my normal mailbox, then either add a note or export the
reply as an `.eml` file, the standard saved-email format:

```sh
python3 -m outreach_crm reply 1 --note 'Fictional reply: the team offered a conversation.'
python3 -m outreach_crm interview 1
python3 -m outreach_crm history 1
# Alternatively, import an exported reply instead of entering the note:
python3 -m outreach_crm import-reply /path/to/reply.eml
```

Import matches `In-Reply-To` or `References` to a stored sent message, checks that
the sender matches the contact and requires a plain-text body and a unique message
identifier. An unmatched, forwarded, HTML-only or duplicate reply is rejected. I
can record a manual note for those cases. Sender matching is a convenience check,
not proof that an email is authentic. Imported time is the time I recorded it.
Replies clear reminders. The app blocks further outreach to a replied contact;
I continue that conversation in my mailbox. An interview stays marked even if
another reply arrives.

## Recover an interrupted delivery

If the connection breaks after delivery begins, the server might already have
accepted the message. `history 1` shows an `uncertain` attempt; a terminated sender
can leave `sending`. Both block retries. I first make sure the original sender
process has stopped, then check my sent mailbox and the recipient/thread details.
I use the attempt ID from history, not the contact ID:

```sh
python3 -m outreach_crm resolve 1 --outcome sent --note 'Confirmed in the sent mailbox.'
# Or, only if I confirmed it was not sent:
python3 -m outreach_crm resolve 1 --outcome not-sent --note 'Confirmed the server rejected it.'
```

Confirmed delivery sets the reminder from the confirmation date. There is no
automatic retry. If I cannot tell whether it was sent, I leave it unresolved.

## Keep private records private

The default path is `~/.local/share/outreach-crm/crm.sqlite3`; `--db PATH` before a
command selects another notebook. New database files have owner-only permissions.
SQLite is not encrypted, so I rely on my computer's account security and disk
encryption. `history` and `list` display private content; I do not paste their real
output into public issues. I back up the database while all CRM commands are closed.
Database files, saved emails and `.env` files are ignored by Git. Only fictional
fixtures belong in this repository. To remove the persistent `/tmp` example from
the README, I close all CRM commands and delete that example file.
