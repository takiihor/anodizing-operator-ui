"""Submit and follow one Manual station visit against the loopback mock IPC."""

from threading import Event, Lock, Thread
from time import monotonic
from urllib.parse import urlencode
from uuid import uuid4

from .ipc_client import JobGatewayUnavailable
from .mock_ipc import MOCK_OWNER, MOCK_STATIONS, MockGatewayRefused, MockIPCClient

HEARTBEAT_SECONDS = 1
ARM_TIMEOUT_SECONDS = 8
JOB_TIMEOUT_SECONDS = 20


class MockStationVisit:
    """Keep the job alive on the backend while the browser polls local status."""

    def __init__(self, gateway: MockIPCClient, *, repeatable=False):
        self.gateway = gateway
        self.repeatable = repeatable
        self._lock = Lock()
        self._closed = Event()
        self._worker = None
        self._job = None

    def start(self, tank):
        if type(tank) is not int or tank not in MOCK_STATIONS:
            raise ValueError('The mock gateway demo serves Tank 3.')
        with self._lock:
            if self._closed.is_set() or (self._worker and self._worker.is_alive()):
                raise ValueError('Wait for the current station visit to finish.')
            if self._job:
                state = self._job['state']
                if state == 'unknown' or (state == 'done' and not self.repeatable):
                    raise ValueError('Restart the console to run another simulated visit.')
            job_id = f'visit-{uuid4().hex}'
            self._job = {'jobId': job_id, 'tank': tank, 'station': MOCK_STATIONS[tank],
                         'state': 'claiming', 'message': None}
            self._worker = Thread(target=self._visit, args=(job_id, MOCK_STATIONS[tank]), daemon=True)
            self._worker.start()

    def snapshot(self):
        with self._lock:
            return {'tanks': list(MOCK_STATIONS), 'job': dict(self._job) if self._job else None,
                    'busy': self._worker is not None and self._worker.is_alive()}

    def close(self):
        self._closed.set()
        with self._lock:
            worker = self._worker
        if worker is not None:
            worker.join(timeout=3)

    def _update(self, state, message=None):
        with self._lock:
            self._job.update(state=state, message=message)

    def _visit(self, job_id, station):
        lease = None
        submitted = settled = False
        next_heartbeat = 0
        heartbeat_sequence = 0

        def read_status(query=''):
            nonlocal next_heartbeat, heartbeat_sequence
            now = monotonic()
            if now >= next_heartbeat:
                heartbeat_sequence += 1
                heartbeat = self.gateway.request('POST', 'heartbeat', {
                    'lease_id': lease, 'sequence': heartbeat_sequence,
                })
                if heartbeat.get('lease_valid') is not True or heartbeat.get('boot') != boot:
                    raise JobGatewayUnavailable('The mock IPC lease changed.')
                next_heartbeat = now + HEARTBEAT_SECONDS
            status = self.gateway.request('GET', 'status' + query)
            if status.get('boot') != boot:
                raise JobGatewayUnavailable('The mock IPC restarted.')
            return status

        try:
            health = self.gateway.health()
            if (health.get('simulated') is not True or health.get('protocol') != 2
                    or health.get('gateway_enabled') is not True or health.get('motion_enabled') is not True):
                raise MockGatewayRefused('Use the loopback mock IPC with station visits enabled.')
            claim = self.gateway.request('POST', 'claim', {})
            lease, boot, generation = (claim.get(key) for key in ('lease_id', 'boot', 'stop_generation'))
            if not isinstance(lease, str) or not lease or boot != health.get('boot') or type(generation) is not int:
                raise JobGatewayUnavailable('The mock IPC did not grant a usable lease.')

            self._update('waiting_for_arm')
            deadline = monotonic() + ARM_TIMEOUT_SECONDS
            while not self._closed.is_set() and monotonic() < deadline:
                status = read_status()
                if (status.get('owner') != MOCK_OWNER or status.get('lease_valid') is not True
                        or status.get('stop_generation') != generation or status.get('recovery_required') is not False
                        or status.get('active_job_id') is not None or status.get('module_attestation_expired') is not False):
                    raise MockGatewayRefused('The mock IPC control state changed before submission.')
                if status.get('armed') is True and status.get('armed_for') == MOCK_OWNER:
                    break
                self._closed.wait(.2)
            else:
                raise MockGatewayRefused('The mock IPC did not arm in time.')

            if self._closed.is_set():
                return
            self._update('submitting')
            submitted = True
            try:
                self.gateway.request('POST', 'submit', {
                    'lease_id': lease, 'stop_generation': generation, 'job_id': job_id,
                    'type': 'station_visit', 'version': 1, 'params': {'station': station},
                })
            except MockGatewayRefused:
                settled = True
                raise
            except JobGatewayUnavailable:
                self._update('checking', 'Submit receipt missing. Checking the same job ID.')

            query = '?' + urlencode({'job_id': job_id})
            deadline = monotonic() + JOB_TIMEOUT_SECONDS
            while not self._closed.is_set() and monotonic() < deadline:
                status = read_status(query)
                job = status.get('job')
                if job is not None:
                    if not isinstance(job, dict) or job.get('job_id') != job_id:
                        raise JobGatewayUnavailable('The mock IPC did not return the requested job.')
                    state = job.get('state')
                    if state == 'done':
                        settled = True
                        result = job.get('result')
                        if (not isinstance(result, dict) or result.get('station') != station
                                or result.get('stopped') is not True):
                            raise JobGatewayUnavailable('The mock IPC did not confirm arrival and stopped feedback.')
                        self._update('done')
                        return
                    if state in ('refused', 'failed', 'stopped'):
                        settled = True
                        self._update(state)
                        return
                    if state in ('accepted', 'running'):
                        self._update(state)
                self._closed.wait(.25)
            self._update('unknown', 'No final job status received from the mock IPC.')
        except (JobGatewayUnavailable, MockGatewayRefused) as error:
            state = 'refused' if isinstance(error, MockGatewayRefused) else 'failed'
            if submitted and (not settled or state == 'failed'):
                state = 'unknown'
            self._update(state, str(error))
        finally:
            if isinstance(lease, str) and lease and (not submitted or settled):
                try:
                    self.gateway.request('POST', 'release', {'lease_id': lease})
                except (JobGatewayUnavailable, MockGatewayRefused):
                    with self._lock:
                        self._job['message'] = 'The mock IPC did not confirm release of the lease.'
