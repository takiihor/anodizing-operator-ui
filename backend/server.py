"""Serve the built Operator UI and its simulated line API on loopback."""

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
from urllib.parse import urlsplit

from .agv_client import AgvClient, ControllerUnavailable
from .line import OperatorLine

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / 'frontend' / 'dist'
ASSET = re.compile(r'/assets/[A-Za-z0-9_-]+\.(js|css)')
PAGES = ('/', '/manual', '/recipes', '/settings', '/agv')
MIME = {'js': 'text/javascript; charset=utf-8', 'css': 'text/css; charset=utf-8'}


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port, dist=DIST, recipe_path=None, agv_controller_port=8870, tank_path=None):
        self.dist = dist
        self.line = OperatorLine(recipe_path=recipe_path, tank_path=tank_path)
        self.agv = AgvClient(agv_controller_port)
        self.token = secrets.token_urlsafe(32)
        super().__init__(('127.0.0.1', port), Handler)
        actual_port = self.server_address[1]
        self.hosts = {f'127.0.0.1:{actual_port}', f'localhost:{actual_port}'}


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def reply(self, status, body, mime='application/json; charset=utf-8', cache='no-store'):
        if not isinstance(body, bytes):
            body = json.dumps(body, allow_nan=False).encode()
        self.send_response(status)
        for name, value in (
            ('Content-Type', mime),
            ('Content-Length', str(len(body))),
            ('Cache-Control', cache),
            ('X-Content-Type-Options', 'nosniff'),
            ('Content-Security-Policy', "default-src 'self'; frame-ancestors 'none'; base-uri 'none'"),
        ):
            self.send_header(name, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def authorized(self, api=False):
        host = self.headers.get('Host')
        origin = self.headers.get('Origin')
        token = self.headers.get('X-Operator-Token', '')
        return (host in self.server.hosts and origin in (None, f'http://{host}')
                and (not api or secrets.compare_digest(token.encode(), self.server.token.encode())))

    def do_GET(self):
        path = urlsplit(self.path).path
        token_request = path == '/api/line/token'
        if not self.authorized(api=path.startswith('/api/') and not token_request):
            return self.reply(HTTPStatus.FORBIDDEN, {'error': 'Request is not authorized.'})
        if token_request:
            return self.reply(HTTPStatus.OK, {'token': self.server.token})
        if path == '/api/line/status':
            return self.reply(HTTPStatus.OK, self.server.line.snapshot())
        if path == '/api/line/recipes':
            return self.reply(HTTPStatus.OK, self.server.line.recipe_library())
        if path == '/api/agv/status':
            try:
                return self.reply(HTTPStatus.OK, self.server.agv.status())
            except (ControllerUnavailable, ValueError) as error:
                return self.reply(HTTPStatus.SERVICE_UNAVAILABLE, {
                    'error': str(error), 'setupUrl': self.server.agv.setup_url,
                })
        if path in PAGES:
            page = (self.server.dist / 'index.html').read_bytes()
            page = page.replace(b'__TOKEN__', self.server.token.encode())
            return self.reply(HTTPStatus.OK, page, 'text/html; charset=utf-8')
        if match := ASSET.fullmatch(path):
            asset = self.server.dist / path.lstrip('/')
            if asset.is_file():
                return self.reply(HTTPStatus.OK, asset.read_bytes(), MIME[match[1]],
                                  cache='public, max-age=31536000, immutable')
        return self.reply(HTTPStatus.NOT_FOUND, {'error': 'Not found.'})

    def do_POST(self):
        if not self.authorized(api=True):
            return self.reply(HTTPStatus.FORBIDDEN, {'error': 'Request is not authorized.'})
        path = urlsplit(self.path).path
        if path not in ('/api/line/command', '/api/agv/command'):
            return self.reply(HTTPStatus.NOT_FOUND, {'error': 'Not found.'})
        try:
            lengths = self.headers.get_all('Content-Length', [])
            if (self.headers.get('Transfer-Encoding') or len(lengths) != 1
                    or self.headers.get_content_type() != 'application/json'
                    or not 0 < (length := int(lengths[0])) <= 8192):
                raise ValueError('Send a bounded JSON request.')
            try:
                body = json.loads(self.rfile.read(length))
            except (UnicodeError, json.JSONDecodeError) as error:
                raise ValueError('Send a valid JSON request.') from error
            if not isinstance(body, dict):
                raise ValueError('Send a JSON object.')
            result = (self.server.agv if path == '/api/agv/command' else self.server.line).command(body)
        except ValueError as error:
            return self.reply(HTTPStatus.BAD_REQUEST, {'error': str(error)})
        except ControllerUnavailable as error:
            return self.reply(HTTPStatus.SERVICE_UNAVAILABLE, {'error': str(error)})
        except OSError as error:
            self.log_error('Could not save line data: %s', error)
            return self.reply(HTTPStatus.INTERNAL_SERVER_ERROR, {'error': 'Could not save the change to disk.'})
        return self.reply(HTTPStatus.OK, result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8871)
    parser.add_argument('--agv-controller-port', type=int, default=os.environ.get('ANODIZING_AGV_CONTROLLER_PORT', '8870'),
                        help='Loopback port of the robot service that owns the AGV.')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('Port must be 1 to 65535.')
    if not 1 <= args.agv_controller_port <= 65535:
        parser.error('AGV controller port must be 1 to 65535.')
    if not (DIST / 'index.html').is_file():
        parser.error('Build the frontend first with npm ci and npm run build in frontend/.')
    with Server(args.port, recipe_path=ROOT / '.runtime' / f'recipes-{args.port}.json',
                tank_path=ROOT / '.runtime' / f'tanks-{args.port}.json',
                agv_controller_port=args.agv_controller_port) as server:
        print(f'Operator simulation: http://127.0.0.1:{args.port}/', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
