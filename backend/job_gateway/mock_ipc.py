"""Loopback-only mock of the IPC station-visit API. It has no device adapter."""

from contextlib import contextmanager
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Lock, Thread
from time import monotonic
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from .ipc_client import JobGatewayReads, JobGatewayUnavailable, MAX_REPLY_BYTES

MOCK_OWNER = 'operator-ui'
MOCK_STATIONS = {3: 3}
LEASE_SECONDS = 10
ARM_DELAY = 0.6
ACCEPTED_SECONDS = 0.9
ARRIVAL_SECONDS = 3.8


class MockGatewayRefused(ValueError):
    """The mock rejected the request before accepting it."""


class MockIPC:
    def __init__(self):
        self.boot = uuid4().hex
        self.lock = Lock()
        self.lease = None
        self.claimed_at = self.deadline = 0
        self.sequence = 0
        self.stop_generation = 0
        self.jobs = {}
        self.requests = {}
        self.active_job_id = None

    def _advance(self, now):
        if self.lease is None:
            return
        if now >= self.deadline:
            if self.active_job_id is not None:
                self.jobs[self.active_job_id]['state'] = 'stopped'
                self.active_job_id = None
            self.lease = None
            return
        if self.active_job_id is not None:
            job = self.jobs[self.active_job_id]
            elapsed = now - job['accepted_at']
            if elapsed >= ARRIVAL_SECONDS:
                job.update(state='done', result={'station': job['station'], 'stopped': True})
                self.active_job_id = None
            elif elapsed >= ACCEPTED_SECONDS:
                job['state'] = 'running'

    def _status(self, now, job_id):
        armed = self.lease is not None and now - self.claimed_at >= ARM_DELAY
        job = self.jobs.get(job_id)
        return {
            'boot': self.boot, 'stop_generation': self.stop_generation,
            'owner': MOCK_OWNER if self.lease else None, 'armed': armed,
            'armed_for': MOCK_OWNER if armed else None, 'lease_valid': self.lease is not None,
            'recovery_required': False, 'module_attestation_expired': False,
            'active_job_id': self.active_job_id, 'job': self._public_job(job) if job else None,
        }

    @staticmethod
    def _public_job(job):
        return {key: value for key, value in job.items() if key != 'accepted_at'}

    def request(self, method, path, body):
        url = urlsplit(path)
        now = monotonic()
        with self.lock:
            self._advance(now)
            if method == 'GET':
                if url.path == '/v1/health':
                    return {'build': 'operator-ui-mock', 'protocol': 2, 'boot': self.boot,
                            'gateway_enabled': True, 'simulated': True, 'motion_enabled': True}
                if url.path == '/v1/status':
                    job_id = parse_qs(url.query).get('job_id', [None])[0]
                    return self._status(now, job_id)
                if url.path == '/v1/jobs':
                    return {'jobs': [self._public_job(job) for job in self.jobs.values()]}
                raise ValueError('Unknown mock read.')

            if url.path == '/v1/claim':
                if self.lease is not None:
                    raise ValueError('The mock AGV already has an owner.')
                self.lease = uuid4().hex
                self.claimed_at, self.deadline = now, now + LEASE_SECONDS
                self.sequence = 0
                self.stop_generation += 1
                return {'lease_id': self.lease, **self._status(now, None)}
            if self.lease is None or body.get('lease_id') != self.lease:
                raise ValueError('The mock ownership lease is no longer valid.')
            if url.path == '/v1/heartbeat':
                sequence = body.get('sequence')
                if type(sequence) is not int or sequence <= self.sequence:
                    raise ValueError('The mock heartbeat sequence must increase.')
                self.sequence, self.deadline = sequence, now + LEASE_SECONDS
                return {'boot': self.boot, 'lease_valid': True}
            if url.path == '/v1/release':
                if self.active_job_id is not None:
                    raise ValueError('The mock station visit is still active.')
                self.lease = None
                return {'released': True}
            if url.path != '/v1/submit':
                raise ValueError('Unknown mock command.')
            return self._submit(body, now)

    def _submit(self, body, now):
        job_id = body.get('job_id')
        params = body.get('params')
        if not isinstance(job_id, str) or not job_id:
            raise ValueError('Provide a mock job ID.')
        if job_id in self.requests:
            if self.requests[job_id] != body:
                raise ValueError('This job ID already has a different request.')
            return self._public_job(self.jobs[job_id])
        if (body.get('type') != 'station_visit' or type(body.get('version')) is not int or body['version'] != 1
                or not isinstance(params, dict) or type(params.get('station')) is not int
                or params['station'] not in MOCK_STATIONS.values()):
            raise ValueError('Choose a station from the mock map.')
        if (self.active_job_id is not None or not self._status(now, None)['armed']
                or body.get('stop_generation') != self.stop_generation):
            raise ValueError('The mock AGV is not ready for this visit.')
        job = {'job_id': job_id, 'type': 'station_visit', 'station': params['station'],
               'state': 'accepted', 'accepted_at': now}
        self.requests[job_id] = body
        self.jobs[job_id] = job
        self.active_job_id = job_id
        return self._public_job(job)


class MockIPCClient(JobGatewayReads):
    def __init__(self, port, key):
        self.port = port
        self.key = key

    def _get(self, resource):
        return self.request('GET', resource)

    def request(self, method, resource, body=None):
        connection = HTTPConnection('127.0.0.1', self.port, timeout=1)
        try:
            headers = {'Authorization': f'Bearer {self.key}', 'Accept': 'application/json'}
            if body is not None:
                headers['Content-Type'] = 'application/json'
            connection.request(method, f'/v1/{resource}', None if body is None else json.dumps(body), headers)
            with connection.getresponse() as response:
                data = response.read(MAX_REPLY_BYTES + 1)
                if len(data) > MAX_REPLY_BYTES:
                    raise JobGatewayUnavailable('Mock IPC response exceeded the expected size.')
                payload = json.loads(data)
                if not isinstance(payload, dict):
                    raise JobGatewayUnavailable('The mock IPC returned an invalid response.')
                if response.status == 400:
                    raise MockGatewayRefused(payload.get('error') or 'The mock gateway refused the request.')
                if response.status != 200:
                    raise JobGatewayUnavailable(f'Mock IPC returned HTTP {response.status}.')
                return payload
        except MockGatewayRefused:
            raise
        except (OSError, HTTPException, ValueError, RecursionError) as error:
            raise JobGatewayUnavailable('The mock IPC did not return a usable response.') from error
        finally:
            connection.close()


@contextmanager
def running_mock_ipc(state=None):
    state = MockIPC() if state is None else state
    key = uuid4().hex

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(2)

        def dispatch(self):
            if self.headers.get('Authorization') != f'Bearer {key}':
                code, result = 403, {'error': 'Mock client token is invalid.'}
            else:
                try:
                    body = {}
                    if self.command == 'POST':
                        length = int(self.headers.get('Content-Length', 0))
                        if not 0 < length <= 8192:
                            raise ValueError('Send a bounded mock request.')
                        body = json.loads(self.rfile.read(length))
                        if not isinstance(body, dict):
                            raise ValueError('Send a mock request object.')
                    result = state.request(self.command, self.path, body)
                    code = 200
                except ValueError as error:
                    code, result = 400, {'error': str(error)}
            data = json.dumps(result).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = dispatch

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        worker = Thread(target=server.serve_forever, kwargs={'poll_interval': .05}, daemon=True)
        worker.start()
        try:
            yield MockIPCClient(server.server_port, key)
        finally:
            server.shutdown()
            worker.join()
