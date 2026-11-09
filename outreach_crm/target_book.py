"""Persistent audit snapshots, deliberate match decisions and outreach history."""
import json
import math
import sqlite3
from datetime import date
from pathlib import Path

from .target_audit import audit


class TargetBook:
    STAGES = ('researched', 'outreach', 'engaged', 'qualified', 'closed')

    def __init__(self, path):
        Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(Path(path).expanduser()))
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS snapshot (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS decisions (target_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, note TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS review_log (id INTEGER PRIMARY KEY, target_id TEXT NOT NULL,
                account_id TEXT NOT NULL, snapshot_date TEXT NOT NULL, note TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, target_id TEXT NOT NULL,
                stage TEXT NOT NULL, happened TEXT NOT NULL, note TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS funnel (id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
                name TEXT NOT NULL, created TEXT NOT NULL, amount REAL NOT NULL, stage TEXT NOT NULL);
        ''')

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.db.close()

    def snapshot(self):
        row = self.db.execute('SELECT payload FROM snapshot WHERE id=1').fetchone()
        if not row:
            raise ValueError('Import reports first')
        return json.loads(row[0])

    def import_reports(self, targets, accounts, contacts, opportunities, territories, today, stale_days=30):
        ids = [t.get('target_id', '') for t in targets]
        if not targets or any(not i.strip() for i in ids) or len(ids) != len(set(ids)):
            raise ValueError('Target IDs must be nonempty and unique across all partner lists')
        from .target_audit import key
        existing = self.db.execute('SELECT payload FROM snapshot WHERE id=1').fetchone()
        if existing:
            old_names = {t['target_id']: key(t['company']) for t in json.loads(existing[0])['targets']}
            if any(t['target_id'] in old_names and old_names[t['target_id']] != key(t['company']) for t in targets):
                raise ValueError('Target ID changed company; use a new ID to preserve outreach history')
        for label, rows in (('account', accounts), ('contact', contacts), ('opportunity', opportunities)):
            if any(not r.get('id', '').strip() for r in rows):
                raise ValueError(label + ' ID is required')
        # Duplicate account/contact records remain visible to the audit. Opportunities
        # are an additive ledger, so contradictory copies must fail atomically.
        validated = {}
        account_ids = {a['id'] for a in accounts}
        for o in opportunities:
            if o['account_id'] not in account_ids:
                raise ValueError('Opportunity references an unknown account')
            created = date.fromisoformat(o['created']).isoformat()
            amount = float(o['amount'])
            if not math.isfinite(amount) or amount < 0 or date.fromisoformat(created) > today:
                raise ValueError('Opportunity needs a finite nonnegative amount and nonfuture created date')
            record = (o['id'], o['account_id'], o['name'], created, amount, o['stage'])
            if o['id'] in validated and validated[o['id']] != record:
                raise ValueError('Conflicting duplicate opportunity ID')
            validated[o['id']] = record
            old = self.db.execute('SELECT * FROM funnel WHERE id=?', (o['id'],)).fetchone()
            if old and (old['account_id'], old['created']) != (o['account_id'], created):
                raise ValueError('Opportunity identity changed; correct the source export')
        result = audit(targets, accounts, contacts, opportunities, territories, today, stale_days)
        payload = dict(targets=targets, accounts=accounts, contacts=contacts, opportunities=opportunities,
                       territories=territories, today=today.isoformat(), stale_days=stale_days)
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO snapshot VALUES (1,?)', (json.dumps(payload),))
            # A new snapshot needs a fresh human decision, even if candidates are unchanged.
            self.db.execute('DELETE FROM decisions')
            for record in validated.values():
                self.db.execute('''INSERT INTO funnel VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name, amount=excluded.amount, stage=excluded.stage''', record)
        return result

    def rows(self, asof=None):
        s = self.snapshot()
        today = date.fromisoformat(asof or s['today'])
        if today < date.fromisoformat(s['today']):
            raise ValueError('Coverage cannot predate the current report snapshot')
        result = audit(*[s[k] for k in ('targets', 'accounts', 'contacts', 'opportunities', 'territories')],
                       today, s['stale_days'])
        decisions = {r['target_id']: r['account_id'] for r in self.db.execute('SELECT * FROM decisions')}
        for i, row in enumerate(result):
            tid = row['target']['target_id']
            if tid in decisions:
                selected = [a for a in s['accounts'] if a['id'] == decisions[tid]]
                # Explicit approval supplies the identity, even below the automatic threshold.
                resolved = audit([dict(row['target'], company=selected[0]['name'])], selected,
                                 s['contacts'], s['opportunities'], s['territories'], today, s['stale_days'])[0]
                resolved['target'] = row['target']
                resolved['flags'] += [f for f in row['flags'] if f == 'duplicate target']
                resolved['match'] = 'reviewed'
                resolved['candidates'] = row['candidates']
                row = result[i] = resolved
            history = [h for h in self.history(tid) if h['happened'] <= today.isoformat()]
            row['status'] = history[-1]['stage'] if history else 'researched'
            last = history[-1]['happened'] if history else None
            if last and (today - date.fromisoformat(last)).days <= s['stale_days']:
                row['flags'] = [f for f in row['flags'] if f != 'stalled']
            row['last_outreach_activity'] = last
        return result

    def resolve(self, target_id, account_id, note):
        if not note.strip():
            raise ValueError('A review note is required')
        rows = self.rows()
        row = next((r for r in rows if r['target']['target_id'] == target_id), None)
        if not row or row['match'] != 'review' or account_id not in row['candidates']:
            raise ValueError('Choose an account from this target\'s review candidates')
        if sum(a['id'] == account_id for a in self.snapshot()['accounts']) != 1:
            raise ValueError('Fix duplicate account IDs in the source before resolving')
        if any(r.get('account_id') == account_id for r in rows):
            raise ValueError('Account already linked to another target; deduplicate the partner list')
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO decisions VALUES (?,?,?)', (target_id, account_id, note.strip()))
            self.db.execute('INSERT INTO review_log(target_id,account_id,snapshot_date,note) VALUES (?,?,?,?)',
                            (target_id, account_id, self.snapshot()['today'], note.strip()))
        return {'target_id': target_id, 'account_id': account_id, 'note': note.strip()}

    def advance(self, target_id, stage, on, note):
        on = date.fromisoformat(on).isoformat()
        row = next((r for r in self.rows() if r['target']['target_id'] == target_id), None)
        if not row:
            raise ValueError('Unknown target ID')
        if row['match'] == 'review':
            raise ValueError('Resolve the match before recording outreach')
        history = self.history(target_id)
        previous = history[-1]['stage'] if history else 'researched'
        if stage not in self.STAGES or self.STAGES.index(stage) != self.STAGES.index(previous) + 1:
            raise ValueError('Advance exactly one stage: ' + ', '.join(self.STAGES))
        if on < self.snapshot()['today'] or (history and on < history[-1]['happened']):
            raise ValueError('Activity must be on or after the snapshot and previous activity')
        if not note.strip():
            raise ValueError('An activity note is required')
        with self.db:
            self.db.execute('INSERT INTO events(target_id,stage,happened,note) VALUES (?,?,?,?)',
                            (target_id, stage, on, note.strip()))
        return {'target_id': target_id, 'stage': stage, 'on': on}

    def history(self, target_id):
        return [dict(r) for r in self.db.execute('SELECT * FROM events WHERE target_id=? ORDER BY happened,id', (target_id,))]

    def review_history(self, target_id):
        return [dict(r) for r in self.db.execute('SELECT * FROM review_log WHERE target_id=? ORDER BY id', (target_id,))]

    def funnel(self):
        return [dict(r) for r in self.db.execute('SELECT * FROM funnel ORDER BY created,id')]
