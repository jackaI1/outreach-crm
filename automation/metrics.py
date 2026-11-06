#!/usr/bin/env python3
"""Take a read-only snapshot of CRM counts and append one CSV row."""
import csv
import json
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path
from urllib.parse import quote

QUERY = """
SELECT count(*) AS total,
       coalesce(sum(stage='researched'), 0) AS researched,
       coalesce(sum(stage='emailed'), 0) AS emailed,
       coalesce(sum(stage='followed_up'), 0) AS followed_up,
       coalesce(sum(stage='replied'), 0) AS replied,
       coalesce(sum(stage='interview'), 0) AS interview,
       coalesce(sum(stage IN ('emailed','followed_up') AND follow_up IS NOT NULL AND follow_up <= :today), 0) AS due,
       coalesce(sum(stage IN ('emailed','followed_up') AND follow_up IS NOT NULL AND follow_up < :today), 0) AS overdue
FROM contacts
"""


def snapshot(db_path, today):
    uri = 'file:' + quote(str(Path(db_path).resolve()), safe='/') + '?mode=ro'
    with sqlite3.connect(uri, uri=True) as db:
        db.row_factory = sqlite3.Row
        return dict(db.execute(QUERY, {'today': today.isoformat()}).fetchone())


def report(db_path, csv_path, today):
    row = {'date': today.isoformat(), **snapshot(db_path, today)}
    path = Path(csv_path)
    # The workflow runs one execution at a time. Keep the report outside git.
    with path.open('a+', newline='', encoding='utf-8') as output:
        import fcntl
        fcntl.flock(output, fcntl.LOCK_EX)
        output.seek(0, os.SEEK_END)
        writer = csv.DictWriter(output, fieldnames=list(row))
        if output.tell() == 0:
            writer.writeheader()
        writer.writerow(row)
        output.flush()
    return row


if __name__ == '__main__':
    try:
        print(json.dumps(report('/crm/crm.sqlite3', '/reports/weekly.csv', date.today())))
    except (OSError, sqlite3.Error) as exc:
        print('Report failed: ' + str(exc), file=sys.stderr)
        sys.exit(1)
