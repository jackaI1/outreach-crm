"""CI-only LibreOffice refresh/recalculation with an owned, isolated headless process.

Run with /usr/bin/python3 after installing libreoffice-calc and python3-uno.
No desktop apps are started on the development Mac.
"""
import argparse
from contextlib import suppress
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid


def recalculate(source, output):
    if sys.platform != 'linux':
        raise RuntimeError('LibreOffice verification runs headlessly in Linux CI only')
    import uno
    from com.sun.star.beans import PropertyValue

    def prop(name, value):
        p = PropertyValue()
        p.Name, p.Value = name, value
        return p

    with tempfile.TemporaryDirectory(prefix='outreach-lo-') as profile:
        pipe = 'outreach_' + uuid.uuid4().hex
        command = ['libreoffice', '--headless', '--nologo', '--nodefault', '--norestore',
                   '-env:UserInstallation=' + Path(profile).as_uri(),
                   '--accept=pipe,name=' + pipe + ';urp;StarOffice.ComponentContext']
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=True)
        print(f'Owned LibreOffice: PID={process.pid}, start={time.time():.3f}, pipe={pipe}, no TCP port; cleanup=SIGTERM process group {process.pid}')
        doc = desktop = None
        try:
            context = uno.getComponentContext()
            resolver = context.ServiceManager.createInstanceWithContext('com.sun.star.bridge.UnoUrlResolver', context)
            remote = None
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError('LibreOffice exited before connecting')
                try:
                    remote = resolver.resolve('uno:pipe,name=' + pipe + ';urp;StarOffice.ComponentContext')
                    break
                except Exception:
                    time.sleep(.1)
            if remote is None:
                raise RuntimeError('Timed out connecting to isolated LibreOffice')
            desktop = remote.ServiceManager.createInstanceWithContext('com.sun.star.frame.Desktop', remote)
            doc = desktop.loadComponentFromURL(Path(source).resolve().as_uri(), '_blank', 0,
                                              (prop('Hidden', True), prop('UpdateDocMode', 3)))
            if doc is None:
                raise RuntimeError('Could not open workbook')
            doc.calculateAll()
            count = 0
            for sheet in doc.Sheets:
                tables = sheet.getDataPilotTables()
                for name in tables.getElementNames():
                    tables.getByName(name).refresh()
                    count += 1
            if count != 3:
                raise RuntimeError(f'Expected 3 native pivots, found {count}')
            doc.calculateAll()
            doc.storeAsURL(Path(output).resolve().as_uri(), (prop('FilterName', 'Calc MS Excel 2007 XML'), prop('Overwrite', True)))
            print(f'Refreshed {count} native pivots and recalculated workbook')
        finally:
            if doc is not None:
                with suppress(Exception):
                    doc.close(True)
            if desktop is not None:
                with suppress(Exception):
                    desktop.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                import signal
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
            # A launcher may exit before its child. Check this owned session only.
            members = subprocess.check_output(['ps', '-eo', 'pid,sid,comm'], text=True)
            owned = [line for line in members.splitlines()[1:] if line.split()[1] == str(process.pid)]
            if owned:
                raise RuntimeError('Owned LibreOffice processes remain: ' + '; '.join(owned))
            print(f'Cleanup verified: PID/session {process.pid} exited; profile removed on return')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source')
    p.add_argument('output')
    a = p.parse_args()
    recalculate(a.source, a.output)
