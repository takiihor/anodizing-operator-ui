"""Loopback client for the AGV IPC and legacy commissioning APIs."""

from hashlib import sha256
from html.parser import HTMLParser
from http.client import HTTPConnection, HTTPException
import json
from threading import Lock
from uuid import uuid4


MOTION = {'forward', 'reverse', 'rotate_clockwise', 'rotate_counterclockwise'}
ACTIONS = MOTION | {'stop', 'arm', 'disarm', 'query_status'}
MAX_REPLY = 1024 * 1024


class ControllerUnavailable(OSError):
    pass


def status_view(state, controller_id, *, setup_url=None):
    if any(type(state.get(key)) is not int for key in ('connection_id', 'stop_generation', 'sequence')):
        raise ControllerUnavailable('The AGV controller returned an incomplete link state.')
    settings = state.get('settings') or {}
    report = state.get('status') or {}
    transport = settings.get('mode')
    connected = state.get('connected') is True
    direct = transport in ('serial', 'simulator') and not state.get('wired_port') and not state.get('executor_link')
    age = state.get('status_age_s')
    fresh = connected and bool(report) and type(age) in (int, float) and 0 <= age < 5 and state.get('stale') is False
    speed = report.get('actual_speed_m_h') if fresh else None
    stopped = report.get('stopped') if fresh else None
    motion = ('moving' if stopped is False or (speed is not None and speed != 0) else
              'stopped' if stopped is True and speed == 0 else 'unknown')
    if not connected:
        motion_block = 'Connect the AGV in Controller setup.'
    elif not direct:
        motion_block = 'Forward, Reverse and Rotate require a direct serial or simulator connection.'
    elif state.get('recipe_active'):
        motion_block = 'A robot recipe is using the AGV.'
    elif state.get('halt_pending'):
        motion_block = 'Wait for the Stop outcome before moving.'
    elif state.get('motion_block'):
        motion_block = state['motion_block']
    elif not fresh or motion == 'unknown':
        motion_block = 'Fresh vehicle status is required. Query status and check the link.'
    elif report.get('fault_code') != 0 or report.get('obstacle_stop') is not False:
        motion_block = 'Clear the reported AGV fault or obstacle before moving.'
    elif report.get('mode') not in (0, 2):
        motion_block = 'Select standard or station-call mode in Controller setup.'
    else:
        motion_block = None

    results = {}
    for command, result in (state.get('results') or {}).items():
        name = result.get('requested_command', command)
        if name not in ACTIONS or type(result.get('tx_id')) is not int:
            continue
        if name in results and result['tx_id'] <= results[name]['id']:
            continue
        results[name] = dict(id=result['tx_id'], state=result.get('state'),
                             acknowledged=bool(result.get('acknowledged_at')),
                             confirmedStopped=result.get('confirmed_stopped') is True)
    return {
        'setupUrl': setup_url,
        'authority': {
            'controller': controller_id, 'connectionId': state['connection_id'],
            'stopGeneration': state['stop_generation'],
        },
        'sequence': state['sequence'], 'connected': connected, 'transport': transport,
        'armed': state.get('armed') is True, 'fresh': fresh, 'motion': motion, 'speed': speed,
        'fault': report.get('fault_code') if fresh else None,
        'statusAge': age, 'motionBlock': motion_block, 'error': state.get('error'),
        'canStop': connected and direct, 'canQuery': connected and not state.get('recipe_active'),
        'haltPending': state.get('halt_pending') is True,
        'results': results,
    }


class ModuleToken(HTMLParser):
    def __init__(self):
        super().__init__()
        self.token = None

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'meta' and values.get('name') == 'module-token':
            self.token = values.get('content')


class AgvClient:
    """Forward jog requests to the service that owns the AGV link."""

    def __init__(self, port=8870, *, native=False):
        self.port = port
        self.native = native
        self.setup_url = None if native else f'http://127.0.0.1:{port}/operator/#/agv'
        self.token = None
        self.lock = Lock()

    def _http(self, path, *, token=None, body=None):
        connection = HTTPConnection('127.0.0.1', self.port, timeout=2 if body is None else 6)
        header = 'X-Operator-Token' if self.native else 'X-Module-Token'
        headers = {header: token} if token else {}
        try:
            if body is None:
                connection.request('GET', path, headers=headers)
            else:
                headers['Content-Type'] = 'application/json'
                connection.request('POST', path, json.dumps(body), headers)
            response = connection.getresponse()
            data = response.read(MAX_REPLY + 1)
            if len(data) > MAX_REPLY:
                raise ControllerUnavailable('The AGV controller reply exceeded the expected size.')
            return response.status, data
        except (OSError, HTTPException) as error:
            raise ControllerUnavailable(
                'AGV controller unavailable.' if body is None else
                'The AGV command outcome is unknown. Check the controller before sending it again.'
            ) from error
        finally:
            connection.close()

    def _credential(self):
        with self.lock:
            if self.token is None:
                if self.native:
                    code, payload = self._http('/api/line/token')
                    token = self._decode(code, payload).get('token')
                else:
                    code, page = self._http('/operator/')
                    parser = ModuleToken()
                    parser.feed(page.decode('utf-8', errors='replace'))
                    token = parser.token
                if code != 200 or not isinstance(token, str) or not 32 <= len(token) <= 128 or not token.isascii() or not token.isprintable():
                    raise ControllerUnavailable('Could not obtain a session from the AGV controller.')
                self.token = token
            return self.token

    def _forget(self, token):
        with self.lock:
            if self.token == token:
                self.token = None

    @staticmethod
    def _controller_id(token):
        return sha256(token.encode()).hexdigest()

    @staticmethod
    def _decode(code, data):
        try:
            state = json.loads(data)
        except (ValueError, RecursionError) as error:
            raise ControllerUnavailable('Invalid reply from the AGV controller.') from error
        if not isinstance(state, dict):
            raise ControllerUnavailable('Invalid reply from the AGV controller.')
        if code != 200:
            message = state.get('error')
            if code >= 500:
                raise ControllerUnavailable('The AGV controller could not complete the request.')
            raise ValueError(message if isinstance(message, str) and message else 'The controller refused the request.')
        return state

    def _state(self, token):
        code, payload = self._http('/api/agv/status' if self.native else '/api/agv?cached=1', token=token)
        if code == 403:
            self._forget(token)
            return None
        state = self._decode(code, payload)
        return state if self.native else self._view(state, token)

    def _view(self, state, token):
        return status_view(state, self._controller_id(token), setup_url=self.setup_url)

    def status(self):
        for _ in range(2):
            state = self._state(self._credential())
            if state is not None:
                return state
        raise ControllerUnavailable('The AGV controller session changed. Refresh the status.')

    def command(self, body):
        if self.native:
            token = self._credential()
            code, data = self._http('/api/agv/command', token=token, body=body)
            if code == 403:
                self._forget(token)
                raise ValueError('The AGV controller session changed. Review the link before sending again.')
            return self._decode(code, data)

        action = body.get('action')
        authority = body.get('authority')
        if not isinstance(action, str) or action not in ACTIONS:
            raise ValueError('Choose a supported AGV command.')
        if (not isinstance(authority, dict) or type(authority.get('connectionId')) is not int
                or type(authority.get('stopGeneration')) is not int):
            raise ValueError('Read the AGV link status before sending a command.')
        token = self._credential()
        if authority.get('controller') != self._controller_id(token):
            raise ValueError('The AGV controller restarted. Review the current link status.')
        payload = {'connection_id': authority['connectionId'], 'stop_generation': authority['stopGeneration']}
        if action in MOTION or action == 'arm':
            state = self._state(token)
            if state is None or state['authority'] != authority:
                raise ValueError('The AGV link or Stop state changed. Review the current status.')
            if state['motionBlock']:
                raise ValueError(state['motionBlock'])
            if action == 'arm' and state['motion'] != 'stopped':
                raise ValueError('Arm requires fresh stopped feedback.')
            if action in MOTION and not state['armed']:
                raise ValueError('Arm the AGV before requesting motion.')
        if action in ('arm', 'disarm'):
            payload.update(action='arm', armed=action == 'arm')
        else:
            payload.update(action='send', command=action)
        if action == 'stop':
            payload['request_id'] = uuid4().hex

        # Stop bypasses the cached-status read and can overtake any normal command.
        # A lost command reply is never replayed by this client.
        code, data = self._http('/api/agv', token=token, body=payload)
        if code == 403:
            self._forget(token)
            raise ValueError('The AGV controller session changed. Review the link before sending again.')
        return self._view(self._decode(code, data), token)
