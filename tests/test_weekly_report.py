"""Reopen native structures locally; refresh and independently reconcile in Linux CI."""
from collections import defaultdict
from datetime import date
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from openpyxl import load_workbook
from outreach_crm.target_book import TargetBook
from outreach_crm.weekly_report import export
from scripts.target_demo import seed


class WeeklyReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.book = TargetBook(self.root/'targets.sqlite3')
        self.addCleanup(self.book.db.close)
        seed(self.book)
        self.book.advance('T1','outreach','2026-02-08','Fictional message')
        self.book.advance('T1','engaged','2026-02-08','Fictional reply')
        self.source = self.root/'weekly.xlsx'
        export(self.book, self.source, '2026-02-02','2026-02-08',5)

    def reopen(self, path=None, data_only=False):
        w=load_workbook(path or self.source, data_only=data_only)
        self.addCleanup(w.close)
        return w

    def test_pivot_definitions_caches_records_and_chart_links(self):
        w=self.reopen()
        ps=w['Weekly']._pivots
        self.assertEqual([p.name for p in ps],['CoverageBySegment','CoverageByOwner','FunnelByOwner'])
        self.assertEqual(len({p.cacheId for p in ps}),2)
        self.assertEqual([p.cache.recordCount for p in ps],[7,7,4])
        for p in ps:
            self.assertTrue(p.cache.refreshOnLoad)
            self.assertTrue(p.cache.enableRefresh)
            self.assertEqual(len(p.cache.records.r),p.cache.recordCount)
            self.assertEqual(p.cache.cacheSource.type,'worksheet')
            self.assertIn(p.cache.cacheSource.worksheetSource.sheet,('Targets','Funnel'))
            self.assertTrue(all(f.subtotal=='sum' for f in p.dataFields))
        self.assertIn('Weekly',w['Weekly']._charts[0].series[0].val.numRef.f)
        self.assertIn('XLOOKUP',w['Targets']['F2'].value)
        self.assertIn('INDEX(',w['Targets']['E2'].value)
        self.assertIn('MATCH(',w['Funnel']['N2'].value)
        self.assertIn('XLOOKUP',w['Funnel']['I2'].value)
        with ZipFile(self.source) as z:
            names=z.namelist()
            self.assertEqual(sum(n.startswith('xl/pivotTables/pivotTable') and n.endswith('.xml') for n in names),3)
            self.assertEqual(sum(n.startswith('xl/pivotCache/pivotCacheRecords') and n.endswith('.xml') for n in names),2)
            for n in names:
                if n.startswith('xl/pivotCache/pivotCacheRecords') and n.endswith('.xml'):
                    root=ET.fromstring(z.read(n))
                    self.assertEqual(int(root.attrib['count']),len(root))

    def test_date_window_goal_and_unlinked_cases(self):
        w=self.reopen(data_only=True)
        # Independent source controls: O1,O2,O4 are in the inclusive week;
        # O3 is just outside it. O4 is unlinked until T3 review is resolved.
        self.assertEqual(sum(r[9] for r in w['Funnel'].iter_rows(min_row=2,values_only=True)),3)
        self.assertEqual(sum(r[11] for r in w['Targets'].iter_rows(min_row=2,values_only=True)),2)
        self.assertEqual(sum(r[9] for r in w['Targets'].iter_rows(min_row=2,values_only=True)),3)
        for start,end,goal in [('2026-02-09','2026-02-08',5),('2026-02-02','2026-02-08',0),('2026-02-01','2026-02-07',5)]:
            with self.subTest(start=start,end=end,goal=goal),self.assertRaises(ValueError):
                export(self.book,self.root/'bad.xlsx',start,end,goal)

    def test_export_source_strings_cannot_become_formulas(self):
        s=self.book.snapshot()
        s['targets'][0]['company']='=HYPERLINK("https://example.invalid")'
        s['targets'][0]['target_id']='NEW'
        self.book.import_reports(*[s[k] for k in ('targets','accounts','contacts','opportunities','territories')],date(2026,2,8))
        export(self.book,self.source,'2026-02-02','2026-02-08',5)
        self.assertEqual(self.reopen()['Targets']['B2'].data_type,'s')

    def test_empty_funnel_retains_refreshable_pivot(self):
        with TargetBook(self.root/'empty.sqlite3') as b:
            s=self.book.snapshot()
            b.import_reports(s['targets'],s['accounts'],s['contacts'],[],s['territories'],date(2026,2,8))
            export(b,self.root/'empty.xlsx','2026-02-02','2026-02-08',5)
        w=self.reopen(self.root/'empty.xlsx')
        self.assertEqual(w['Funnel']['J2'].value,0)
        self.assertEqual(len(w['Weekly']._pivots),3)

    def recalc(self, source, name):
        dest=self.root/name
        subprocess.run(['/usr/bin/python3','scripts/recalculate_report.py',str(source),str(dest)],
                       check=True, timeout=80)
        return dest

    def assert_lookups(self, w):
        refs=defaultdict(list)
        for key,territory,sales,architect in w['Reference'].iter_rows(min_row=2,values_only=True):
            refs[key].append((territory or 'MISSING',sales or 'MISSING',architect or 'MISSING'))
        for sheet, keycol, cols in [('Targets',3,(4,5,6)),('Funnel',11,(12,8,13))]:
            for row in w[sheet].iter_rows(min_row=2,values_only=True):
                matches=refs[row[keycol]]
                expected=matches[0] if len(matches)==1 else ('DUPLICATE' if matches else 'MISSING',)*3
                self.assertEqual(tuple(row[c] for c in cols),expected,(sheet,row[0]))

    def assert_pivots(self, w):
        # Independently aggregate recalculated source rows, not pivot caches or
        # exporter grouping code, then reconcile every pivot group and total.
        for p,source,group,measures in zip(w['Weekly']._pivots,
                ['Targets','Targets','Funnel'],[2,5,8],[(9,10,11,12),(9,10,11,12),(9,10)]):
            expected=defaultdict(lambda:[0]*len(measures))
            for row in w[source].iter_rows(min_row=2,values_only=True):
                for j,m in enumerate(measures):
                    expected[str(row[group])][j]+=row[m]
            cells=list(w['Weekly'][p.location.ref])
            for key,counts in expected.items():
                matches=[row for row in cells if str(row[0].value)==key]
                self.assertEqual(len(matches),1,(p.name,key,p.location.ref))
                self.assertEqual([c.value for c in matches[0][1:len(measures)+1]],counts,(p.name,key))
            totals=[row for row in cells if row[0].value in ('Total Result','Grand Total')]
            self.assertEqual(len(totals),1,p.name)
            self.assertEqual([c.value for c in totals[0][1:len(measures)+1]],
                             [sum(v[j] for v in expected.values()) for j in range(len(measures))])

    @unittest.skipUnless(os.environ.get('RUN_LIBREOFFICE')=='1','Headless LibreOffice runs in Linux CI')
    def test_headless_refresh_lookup_results_and_real_input_changes(self):
        output=self.recalc(self.source,'recalculated.xlsx')
        w=self.reopen(output,True)
        self.assert_lookups(w)
        self.assert_pivots(w)
        self.assertFalse([(s.title,c.coordinate,c.value) for s in w for row in s for c in row if c.data_type=='e'])
        self.assertEqual([w['Weekly'][c].value for c in ('B5','B6','B7','B8','B9','E8')],[7,3,1,3,1,.6])
        # Input mutation proves both lookup engines recalculate and the owner
        # pivot really refreshes its cache. Poison saved pivot cells as well.
        changed=self.reopen()
        changed['Reference']['B2']='Eastern'
        changed['Reference']['C2']='Robin'
        changed['Reference']['D2']='Taylor'
        changed['Targets']['J2']=0
        changed['Targets']['L2']=4
        for p in changed['Weekly']._pivots:
            for row in changed['Weekly'][p.location.ref]:
                for c in row:
                    if isinstance(c.value,(int,float)):
                        c.value=-999
        changed.save(self.root/'changed.xlsx')
        updated=self.reopen(self.recalc(self.root/'changed.xlsx','changed-result.xlsx'),True)
        self.assert_lookups(updated)
        self.assert_pivots(updated)
        self.assertEqual(updated['Targets']['F2'].value,'Robin')
        self.assertEqual(updated['Funnel']['I2'].value,'Robin')
        self.assertEqual(updated['Weekly']['B6'].value,2)
        # Duplicate and blank reference values must stay visible after engine recalculation.
        # Extend bounded lookups by regenerating from a new snapshot, the supported workflow.
        s=self.book.snapshot()
        s['territories'].append({'territory':'East','sales':'Other','architect':'Other'})
        self.book.import_reports(*[s[k] for k in ('targets','accounts','contacts','opportunities','territories')],date(2026,2,8))
        export(self.book,self.root/'duplicate.xlsx','2026-02-02','2026-02-08',5)
        dup=self.reopen(self.recalc(self.root/'duplicate.xlsx','duplicate-result.xlsx'),True)
        self.assert_lookups(dup)
        self.assert_pivots(dup)
        self.assertEqual(dup['Targets']['F2'].value,'DUPLICATE')
        self.assertEqual(dup['Targets']['F5'].value,'MISSING')
