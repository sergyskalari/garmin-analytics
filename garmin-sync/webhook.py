"""Minimal webhook to launch sync.py on demand (OPTIONAL).

It exposes a single endpoint, POST /sync, protected by a token. It is meant
so that an agent (Hermes) can ask to "sync now" without having access to the
host's Docker socket, which would amount to giving it control over any
container. Here it can only trigger this one action.

Security:
  - The token is read from the WEBHOOK_TOKEN environment variable (it is
    never written in the code or in the compose file). If it is not set,
    the service refuses to start.
  - The compose file publishes the port only on the host's 127.0.0.1, never
    on the local network.

Known limitations: single-threaded server (simultaneous requests queue up)
and no mutual exclusion with the scheduled sync; if they overlapped, SQLite
would serialize the writes.
"""

import http.server
import subprocess
import os
import json
from datetime import datetime

TOKEN = os.environ.get('WEBHOOK_TOKEN', '')
PORT = 8099


class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != '/sync':
            self.send_response(404)
            self.end_headers()
            return

        auth = self.headers.get('X-Webhook-Token', '')
        if not TOKEN or auth != TOKEN:
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"error": "unauthorized"}')
            return

        print(f"[{datetime.now()}] Sincronizacion bajo demanda solicitada...")
        try:
            result = subprocess.run(
                ['python3', '/app/data/sync.py'],
                capture_output=True, text=True, timeout=600
            )
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                'ok': result.returncode == 0,
                'stdout': result.stdout[-4000:],
                'stderr': result.stderr[-2000:],
            }).encode())
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def log_message(self, format, *args):
        print(f"[{datetime.now()}] {format % args}")


if __name__ == '__main__':
    if not TOKEN:
        print("ERROR: WEBHOOK_TOKEN no definido, abortando.")
        exit(1)
    server = http.server.HTTPServer(('0.0.0.0', PORT), Handler)
    print(f"Webhook de sincronizacion escuchando en el puerto {PORT}...")
    server.serve_forever()
