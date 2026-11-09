"""Build a kept, fictional target audit and weekly workbook. No email or network."""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from outreach_crm.target_audit import read_rows
from outreach_crm.target_book import TargetBook
from outreach_crm.weekly_report import export

FIXTURES = Path(__file__).resolve().parents[1] / 'examples/target-audit'


def seed(book):
    layout = json.loads((FIXTURES / 'layout.json').read_text())['partner-b.xlsx']
    targets = read_rows(FIXTURES / 'partner-a.csv', 'targets') + read_rows(FIXTURES / 'partner-b.xlsx', 'targets', **layout)
    sources = [read_rows(FIXTURES / (kind+'.csv'), kind) for kind in ('accounts', 'contacts', 'opportunities', 'territories')]
    book.import_reports(targets, *sources, date(2026, 2, 8))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True)
    args = p.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    db = output / 'targets.sqlite3'
    if db.exists():
        p.error('Choose a new output folder; the demo never overwrites a notebook')
    with TargetBook(db) as book:
        seed(book)
        book.advance('T1', 'outreach', '2026-02-08', 'Fictional first message')
        book.advance('T1', 'engaged', '2026-02-08', 'Fictional reply')
        export(book, output / 'weekly.xlsx', '2026-02-02', '2026-02-08', 5)
        print(json.dumps({'targets': len(book.rows()), 'review': sum(r['match']=='review' for r in book.rows()),
                          'funnel_adds': sum('2026-02-02' <= o['created'] <= '2026-02-08' for o in book.funnel()),
                          'output': str(output)}, indent=2))


if __name__ == '__main__':
    main()
