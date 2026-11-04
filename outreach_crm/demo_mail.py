"""Small loopback-only SMTP sink used by the demo and integration tests.

It has no relay capability, no credentials, and closes its listener on context exit.
"""
import os
import socketserver
import threading
from contextlib import contextmanager
from datetime import datetime, timezone


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(5)
        self.wfile.write(b"220 localhost demo receiver\r\n")
        body = None
        while True:
            line = self.rfile.readline(65537)
            if not line or len(line) > 65536:
                return
            if body is not None:
                if line == b".\r\n":
                    self.server.messages.append(b"".join(body))
                    body = None
                    self.wfile.write(b"250 accepted locally\r\n")
                else:
                    body.append(line[1:] if line.startswith(b"..") else line)
            elif line.upper().startswith((b"EHLO", b"HELO")):
                self.wfile.write(b"250 localhost\r\n")
            elif line.upper().startswith((b"MAIL FROM:", b"RCPT TO:", b"RSET")):
                self.wfile.write(b"250 OK\r\n")
            elif line.upper().startswith(b"DATA"):
                body = []
                self.wfile.write(b"354 end with dot\r\n")
            elif line.upper().startswith(b"QUIT"):
                self.wfile.write(b"221 bye\r\n")
                return
            else:
                self.wfile.write(b"502 unsupported\r\n")


class Receiver(socketserver.ThreadingTCPServer):
    daemon_threads = False
    block_on_close = True


@contextmanager
def receiver():
    server = Receiver(("127.0.0.1", 0), Handler)
    server.messages = []
    server.owner = {"owner": "outreach-crm local SMTP fixture", "pid": os.getpid(),
                    "started_at": datetime.now(timezone.utc).isoformat(), "port": server.server_address[1],
                    "cleanup": "context exit: shutdown(), server_close(), thread.join(); Ctrl-C also exits"}
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError("Local receiver failed to stop.")
