"""Serve the live dashboard and allow its GitHub mirror to read live data."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import argparse


class DashboardHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-cache')
        super().end_headers()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True)
    parser.add_argument('--port', type=int, default=8786)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('0.0.0.0', args.port),
        partial(DashboardHandler, directory=args.directory))
    server.serve_forever()
