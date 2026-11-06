# Weekly report setup

This is an optional local automation. Docker Compose builds a small image containing n8n and Python, then runs n8n at http://localhost:5678. The notebook directory is mounted read-only; the report directory and n8n's own data are separate writable storage. Anyone who can access the n8n editor can configure and run commands, so keep it on your own computer and do not expose its port to the internet.

1. Install Docker Desktop on a computer where you want the automation to run and start it. Initialize your CRM first. Create an empty report directory that the container's `node` user (UID 1000) can write to. On Linux, for example, `mkdir -p "$HOME/outreach-reports" && sudo chown 1000:1000 "$HOME/outreach-reports"`. On Docker Desktop, check shared-folder permissions if the write fails.
2. Create `.env` in the repository (it is ignored by Git), replacing the examples with your own absolute paths and addresses:

   ```sh
   CRM_DIR=/absolute/path/to/the/directory/containing/crm.sqlite3
   REPORT_DIR=/absolute/path/to/outreach-reports
   REPORT_FROM=you@example.com
   REPORT_TO=you@example.com
   TZ=America/Toronto
   ```

   The default CRM directory is `~/.local/share/outreach-crm`. Mount its directory, not just the database file, so SQLite can see its journal files. The CRM database must already exist and be readable by the container. Avoid sharing a live database across network filesystems.
3. Run `docker compose up -d --build`. Open http://localhost:5678 and create your local n8n owner account. In Workflows, choose Import from File and select `automation/weekly-metrics.json`.
4. Open **Email summary**, create an SMTP credential for your mail provider, and select it. For Gmail use an app password and the provider's SMTP settings. Keep the password in n8n's credential store, not the workflow export or `.env` file. Review the sender and recipient addresses in `.env`. Set the workflow timezone if yours differs from Toronto.
5. Use **Execute workflow** to test, check the email and `weekly.csv` in your report directory, then activate the workflow. It runs Monday at 9 a.m. while the container is running. Stop it with `docker compose down`; the n8n volume and CSV remain for later use.

The snapshot counts each current pipeline stage and reminders due on or before the report date; overdue means strictly before that date. It does not count weekly activity or send outreach emails. Dates follow the container timezone. A CSV row is appended before the email is sent, so an SMTP failure leaves a row; a manual retry adds another row and can send a duplicate email. Check n8n execution history and the CSV before retrying. Missed schedules are not backfilled. The report contains aggregate counts but still deserves private storage. Back up your notebook and n8n data separately.
