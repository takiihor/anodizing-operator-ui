"""Serve the Operator UI with simulation or PLC monitoring on loopback."""

import argparse
from contextlib import ExitStack
from fcntl import LOCK_EX, LOCK_NB, flock
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
from urllib.parse import urlsplit
from zoneinfo import ZoneInfoNotFoundError

from .agv_client import AgvClient, ControllerUnavailable
from .agv_module import AgvController
from .line import OperatorLine
from .plc_module import DoorPilot, PLCMonitor, monitoring_status

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / 'frontend' / 'dist'
ASSET = re.compile(r'/assets/[A-Za-z0-9_-]+\.(js|css)')
PAGES = ('/', '/manual', '/recipes', '/settings', '/agv')
MIME = {'js': 'text/javascript; charset=utf-8', 'css': 'text/css; charset=utf-8'}
MONITOR_ACTIONS = ('select-recipe', 'save-recipe', 'delete-recipe', 'tank-setup', 'tank-setup-default')


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port, dist=DIST, recipe_path=None, agv_controller_port=None, tank_path=None, plc_monitor=None,
                 door_pilot=None, agv_controller=None, agv_api_only=False):
        self.dist = dist
        self.agv_api_only = agv_api_only
        self.line = None if agv_api_only else OperatorLine(recipe_path=recipe_path, tank_path=tank_path)
        self.agv = agv_controller if agv_controller is not None else (
            AgvClient(agv_controller_port) if agv_controller_port is not None else AgvController())
        self.plc_monitor = plc_monitor
        self.door_pilot = door_pilot
        self.token = secrets.token_urlsafe(32)
        super().__init__(('127.0.0.1', port), Handler)
        actual_port = self.server_address[1]
        self.hosts = {f'127.0.0.1:{actual_port}', f'localhost:{actual_port}'}

    def server_close(self):
        if isinstance(self.agv, AgvController):
            self.agv.close()
        super().server_close()

    def line_status(self, status):
        if self.plc_monitor is None:
            return status
        sample = self.plc_monitor.snapshot()
        result = monitoring_status(status, sample)
        if self.door_pilot is not None:
            result['doorPilot'] = self.door_pilot.status(sample)
        return result

    def line_command(self, body):
        if body.get('action') == 'plc-door':
            if self.door_pilot is None:
                raise ValueError('The PLC door pilot is disabled.')
            self.door_pilot.command(body)
            return self.line_status(self.line.snapshot())
        if self.plc_monitor is not None and body.get('action') not in MONITOR_ACTIONS:
            raise ValueError('Line controls are disabled during PLC monitoring.')
        return self.line_status(self.line.command(body))


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
        if self.server.agv_api_only and path != '/api/agv/status':
            return self.reply(HTTPStatus.NOT_FOUND, {'error': 'This service hosts the AGV API.'})
        if path == '/api/line/status':
            return self.reply(HTTPStatus.OK, self.server.line_status(self.server.line.snapshot()))
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
        if path not in ('/api/line/command', '/api/agv/command') or (self.server.agv_api_only and path != '/api/agv/command'):
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
            result = self.server.agv.command(body) if path == '/api/agv/command' else self.server.line_command(body)
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
    parser.add_argument('--agv-controller-port', type=int, default=os.environ.get('ANODIZING_AGV_CONTROLLER_PORT'),
                        help='Loopback or forwarded port for the ipc or legacy AGV backend.')
    parser.add_argument('--agv-mode', choices=('simulator', 'serial', 'wired', 'ipc', 'legacy'),
                        default=os.environ.get('ANODIZING_AGV_MODE'))
    parser.add_argument('--agv-api-only', action='store_true', help='Run the AGV API on the robot IPC.')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('Port must be 1 to 65535.')
    controller_port = 8870 if args.agv_controller_port is None else args.agv_controller_port
    agv_mode = args.agv_mode or ('simulator' if args.agv_controller_port is None else 'legacy')
    if not 1 <= controller_port <= 65535:
        parser.error('AGV controller port must be 1 to 65535.')
    if agv_mode in ('ipc', 'legacy'):
        if args.agv_api_only:
            parser.error('The AGV-only API must own a local AGV link.')
        if controller_port == args.port:
            parser.error('Use a different port for the AGV controller.')
        agv = AgvClient(controller_port, native=agv_mode == 'ipc')
    else:
        try:
            agv = AgvController(agv_mode, port=os.environ.get('ANODIZING_AGV_DEVICE'),
                                address=os.environ.get('ANODIZING_AGV_ADDRESS'),
                                channel=os.environ.get('ANODIZING_AGV_CHANNEL'))
        except ValueError as error:
            parser.error(str(error))
    if not args.agv_api_only and not (DIST / 'index.html').is_file():
        parser.error('Build the frontend first with npm ci and npm run build in frontend/.')
    plc_mode = 'simulation' if args.agv_api_only else os.environ.get('ANODIZING_PLC_MODE', 'simulation')
    if plc_mode not in ('simulation', 'monitor', 'door-pilot'):
        parser.error('ANODIZING_PLC_MODE must be simulation, monitor or door-pilot.')
    try:
        plc = None if plc_mode == 'simulation' else PLCMonitor.from_environment(os.environ)
    except (ValueError, OverflowError, ZoneInfoNotFoundError):
        parser.error('Check the supplier URL, API key and PLC timestamp settings.')
    state = ROOT / '.runtime'
    with ExitStack() as stack:
        pilot = None
        if plc_mode == 'door-pilot':
            try:
                state.mkdir(parents=True, exist_ok=True)
                owner = stack.enter_context((state / 'plc-door-pilot.lock').open('a'))
                flock(owner, LOCK_EX | LOCK_NB)
                pilot = DoorPilot(plc, state / 'plc-door-pilot.json')
            except BlockingIOError:
                parser.error('A door pilot is already running from this checkout.')
            except (OSError, ValueError, KeyError, TypeError):
                parser.error('The door pilot record could not be loaded. Verify its PLC endpoint and physical door state.')
        server = stack.enter_context(Server(args.port, recipe_path=state / f'recipes-{args.port}.json',
                                           tank_path=state / f'tanks-{args.port}.json',
                                           agv_controller=agv, plc_monitor=plc, door_pilot=pilot,
                                           agv_api_only=args.agv_api_only))
        service = 'AGV API' if args.agv_api_only else f'Operator {plc_mode}'
        print(f'{service}: http://127.0.0.1:{args.port}/', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
