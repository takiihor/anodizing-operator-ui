from contextlib import contextmanager
import json
from threading import Thread
from time import monotonic, sleep
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from backend.job_gateway import JobGatewayUnavailable, MockStationVisit, running_mock_ipc
from backend.job_gateway.mock_ipc import MockIPC
from backend.server import Server


class RecordedIPC(MockIPC):
    def __init__(self):
        super().__init__()
        self.calls = []

    def request(self, method, path, body):
        self.calls.append((method, path, body))
        return super().request(method, path, body)


@contextmanager
def mock_console(gateway):
    with Server(0, job_gateway=gateway, mock_station_visit=MockStationVisit(gateway)) as server:
        thread = Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        thread.start()
        try:
            def request(body=None):
                base = f'http://127.0.0.1:{server.server_port}'
                path = '/api/line/status' if body is None else '/api/line/command'
                data = json.dumps(body).encode() if body is not None else None
                headers = {'X-Operator-Token': server.token, 'Content-Type': 'application/json'}
                with urlopen(Request(base + path, data, headers), timeout=3) as response:
                    return json.load(response)
            yield request
        finally:
            server.shutdown()
            thread.join()


def follow(request):
    observed = set()
    until = monotonic() + 10
    while monotonic() < until:
        status = request()
        visit = status['mockStationVisit']
        observed.add(visit['job']['state'])
        if not visit['busy']:
            return status, observed
        sleep(.05)
    raise AssertionError('The mock visit did not settle.')


class MockStationVisitTest(unittest.TestCase):
    def test_tank_three_submits_one_station_visit_and_confirms_arrival(self):
        state = RecordedIPC()
        with running_mock_ipc(state) as gateway, mock_console(gateway) as request:
            self.assertEqual(request()['mode'], 'manual')
            with self.assertRaises(HTTPError) as unrelated:
                request({'action': 'move', 'tank': 4})
            self.assertEqual(unrelated.exception.code, 400)
            unrelated.exception.close()
            self.assertEqual(state.calls, [])

            started = request({'action': 'move', 'tank': 3})
            job_id = started['mockStationVisit']['job']['jobId']
            self.assertIsNone(started['agv']['station'])
            with self.assertRaises(HTTPError) as duplicate:
                request({'action': 'move', 'tank': 3})
            self.assertEqual(duplicate.exception.code, 400)
            duplicate.exception.close()
            arrived, phases = follow(request)

        self.assertTrue({'accepted', 'running', 'done'} <= phases)
        self.assertEqual(arrived['mockStationVisit']['job']['jobId'], job_id)
        self.assertEqual(arrived['agv'], {'station': 3, 'stationConfirmed': True, 'destination': None, 'moving': False})
        self.assertIsNone(arrived['manualTravel'])
        writes = [(path, body) for method, path, body in state.calls if method == 'POST']
        self.assertEqual(writes[0], ('/v1/claim', {}))
        self.assertEqual(writes[-1][0], '/v1/release')
        self.assertEqual([body['sequence'] for path, body in writes if path == '/v1/heartbeat'], list(range(1, state.sequence + 1)))
        self.assertGreaterEqual(state.sequence, 2)
        submits = [body for path, body in writes if path == '/v1/submit']
        lease = writes[-1][1]['lease_id']
        self.assertEqual(submits, [{'lease_id': lease, 'stop_generation': 1, 'job_id': job_id,
                                    'type': 'station_visit', 'version': 1, 'params': {'station': 3}}])
        self.assertIn(('GET', f'/v1/status?job_id={job_id}', {}), state.calls)
        self.assertNotIn(lease, json.dumps(arrived))

    def test_missing_submit_receipt_checks_same_job_without_resending(self):
        state = RecordedIPC()
        with running_mock_ipc(state) as gateway:
            send = gateway.request

            def drop_submit_receipt(method, path, body=None):
                reply = send(method, path, body)
                if method == 'POST' and path == 'submit':
                    raise JobGatewayUnavailable('Receipt lost in the test.')
                return reply

            with patch.object(gateway, 'request', side_effect=drop_submit_receipt), mock_console(gateway) as request:
                initial = request({'action': 'move', 'tank': 3})
                settled, _ = follow(request)
        self.assertEqual(settled['mockStationVisit']['job']['state'], 'done')
        self.assertEqual(settled['mockStationVisit']['job']['jobId'], initial['mockStationVisit']['job']['jobId'])
        self.assertEqual(sum(path == '/v1/submit' for _, path, _ in state.calls), 1)

    def test_done_without_stopped_evidence_does_not_mark_arrival(self):
        class UnconfirmedIPC(RecordedIPC):
            def request(self, method, path, body):
                response = super().request(method, path, body)
                job = response.get('job')
                if job and job['state'] == 'done':
                    job['result'] = {'station': 3, 'stopped': False}
                return response

        with running_mock_ipc(UnconfirmedIPC()) as gateway, mock_console(gateway) as request:
            request({'action': 'move', 'tank': 3})
            settled, _ = follow(request)
            with self.assertRaises(HTTPError) as duplicate:
                request({'action': 'move', 'tank': 3})
            self.assertEqual(duplicate.exception.code, 400)
            duplicate.exception.close()
        self.assertEqual(settled['mockStationVisit']['job']['state'], 'unknown')
        self.assertFalse(settled['agv']['stationConfirmed'])
