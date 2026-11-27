from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from backend.plc_module import DoorPilot, PLCClient, PLCMonitor
from backend.server import Server


@contextmanager
def serving(server):
    with server:
        thread = Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        thread.start()
        try:
            yield f'http://127.0.0.1:{server.server_port}'
        finally:
            server.shutdown()
            thread.join()


class PLCModuleTest(unittest.TestCase):
    def setUp(self):
        self.clock = 0
        self.now = datetime(2026, 11, 27, tzinfo=timezone.utc)
        self.read_failure = False
        self.lost_receipt = False
        self.calls = []
        self.writes = []
        self.metadata = []
        self.poll = {'timestamp': self.now.isoformat(), 'tags_failed': 0}
        for tank in range(1, 17):
            self.signal(f'Tank{tank:02}_TimePV', tank, kind='Word')
        self.signal('Remote', 1, address='L4')
        self.signal('Local', 0, address='L3')
        self.signal('Tank01_TempPV', 52.7, kind='Word')
        self.signal('Tank01_TimeSV', 120, kind='Word', permission='W')
        self.signal('Tank01_TimeUp', 0)
        self.signal('Tank01_FilterStatus', 1)
        self.signal('Alarm51', 1)
        self.signal('Tank01_DoorOpenRemote', 0, address='M300', permission='W')
        self.signal('Tank01_DoorCloseRemote', 0, address='M301', permission='W')
        self.signal('Tank01_DoorOpened', 0, address='D1000.4')
        self.signal('Tank01_DoorClosed', 1, address='D1000.5')
        self.signal('Tank12_DoorOpened', 1, address='D1116.4')
        self.signal('Tank12_DoorClosed', 0, address='D1116.5')

    def signal(self, name, value, *, kind='Bool', permission='R', address=None):
        self.metadata.append({'tag_name': name, 'data_type': kind, 'read_write': permission,
                              'device_address': address or str(len(self.metadata))})
        self.poll[name] = value

    def tick(self, seconds, *, new_sample=True):
        self.clock += seconds
        self.now += timedelta(seconds=seconds)
        if new_sample:
            self.poll['timestamp'] = self.now.isoformat()

    @contextmanager
    def operator(self, *, door_pilot=False):
        fixture = self

        class Supplier(BaseHTTPRequestHandler):
            def do_GET(self):
                fixture.calls.append((self.command, self.path, self.headers.get('X-API-Key')))
                if self.path == '/api/v1/tags/list':
                    payload = {'tags': fixture.metadata}
                elif self.path == '/api/v1/tags/read' and not fixture.read_failure:
                    payload = fixture.poll
                else:
                    self.send_error(503)
                    return
                self.reply(payload)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                fixture.writes.append(body)
                fixture.assertEqual(self.path, '/api/v1/tags/write')
                fixture.assertEqual(self.headers.get('X-API-Key'), 'test-supplier-key')
                tag = body['tag_name']
                fixture.poll[tag] = body['value']
                if fixture.lost_receipt:
                    self.send_error(503)
                    return
                metadata = next(item for item in fixture.metadata if item['tag_name'] == tag)
                self.reply({**metadata, 'value': body['value'], 'written': True})

            def reply(self, payload):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode())

            def log_message(self, *args):
                pass

        with serving(ThreadingHTTPServer(('127.0.0.1', 0), Supplier)) as supplier, TemporaryDirectory() as state:
            monitor = PLCMonitor(PLCClient(supplier, 'test-supplier-key'),
                                 clock=lambda: self.clock, now=lambda: self.now)
            pilot = DoorPilot(monitor, Path(state) / 'door.json') if door_pilot else None
            self.server = Server(0, plc_monitor=monitor, door_pilot=pilot)
            with serving(self.server) as url:
                with urlopen(url + '/api/line/token') as response:
                    token = json.load(response)['token']

                def request(body=None):
                    route = '/api/line/status' if body is None else '/api/line/command'
                    headers = {'X-Operator-Token': token, 'Content-Type': 'application/json'}
                    data = json.dumps(body).encode() if body is not None else None
                    with urlopen(Request(url + route, data, headers)) as response:
                        self.assertNotIn('test-supplier-key', str(response.headers))
                        raw = response.read()
                        self.assertNotIn(b'test-supplier-key', raw)
                        return json.loads(raw)

                yield request

    def test_projects_live_signals_and_rejects_line_commands(self):
        with self.operator() as request:
            status = request()
            self.assertFalse(status['simulation'])
            self.assertEqual(status['plc'], {'quality': 'live', 'sampleTime': self.now.isoformat(), 'remote': True})
            self.assertEqual([tank['timer'] for tank in status['tanks']], list(range(1, 17)))
            tank1 = status['tanks'][0]
            self.assertEqual(tank1['temperature'], 52.7)
            self.assertEqual(tank1['fields'][0]['value'], 120)
            self.assertTrue(tank1['controls'][0]['active'])
            self.assertEqual(tank1['alarm'], 'Tank #1 low water level')
            self.assertIsNone(status['agv']['station'])
            self.assertIsNone(status['tanks'][11]['doorPosition'])
            self.assertEqual(status['tanks'][12]['doorPosition'], 'Open')

            for command in ('mode', 'settings', 'move', 'tank-control', 'start', 'stop', 'simulation', 'reset', 'plc-door'):
                with self.subTest(command=command), self.assertRaises(HTTPError) as failure:
                    request({'action': command, 'tank': 1, 'control': 'door', 'active': True})
                with failure.exception as response:
                    self.assertEqual(response.code, 400)
                    self.assertIn('disabled', json.load(response)['error'])
            selected = request({'action': 'select-recipe', 'id': 'line-test'})
            self.assertEqual(selected['tanks'], status['tanks'])
            self.assertEqual(self.calls, [
                ('GET', '/api/v1/tags/list', 'test-supplier-key'),
                ('GET', '/api/v1/tags/read', 'test-supplier-key'),
            ])
            self.assertEqual(self.writes, [])

    def test_freezes_readings_and_recovers_only_with_a_new_supplier_sample(self):
        self.read_failure = True
        with self.operator() as request:
            unavailable = request()
            self.assertEqual(unavailable['plc']['quality'], 'unavailable')
            self.assertIsNone(unavailable['tanks'][0]['timer'])

            self.read_failure = False
            self.tick(1)
            live = request()
            self.assertEqual(live['plc']['quality'], 'live')
            self.tick(1, new_sample=False)
            self.poll['Tank12_DoorOpened'] = 0
            stale = request()
            self.assertEqual(stale['plc']['quality'], 'stale')
            self.assertEqual(stale['plc']['sampleTime'], live['plc']['sampleTime'])
            self.assertEqual(stale['tanks'], live['tanks'])

            self.tick(4)
            self.read_failure = True
            self.assertEqual(request()['tanks'], live['tanks'])
            self.read_failure = False
            self.tick(1)
            recovered = request()
            self.assertEqual(recovered['plc']['quality'], 'live')
            self.assertEqual(recovered['tanks'][12]['doorPosition'], 'Between limits')

            self.tick(1)
            self.poll['Tank12_DoorOpened'] = self.poll['Tank12_DoorClosed'] = 1
            self.assertEqual(request()['tanks'][12]['doorPosition'], 'Conflicting feedback')

    def door(self, status, operation, **overrides):
        return {'action': 'plc-door', 'tank': 1, 'operation': operation, 'confirmed': True,
                'revision': status['doorPilot']['revision'], **overrides}

    def test_pilot_opens_closes_and_waits_for_explicit_clear_and_new_feedback(self):
        with self.operator(door_pilot=True) as request:
            ready = request()
            self.assertEqual(ready['doorPilot']['nextAction'], 'open')
            for overrides in ({'confirmed': False}, {'tank': 13}):
                with self.assertRaises(HTTPError) as rejected:
                    request(self.door(ready, 'open', **overrides))
                rejected.exception.close()
            self.assertEqual(self.writes, [])

            for action, opposite in (('open', 'close'), ('close', 'open')):
                command = self.door(request(), action)
                queued = request(command)
                self.assertIsNone(queued['doorPilot']['nextAction'])
                self.tick(1)
                waiting = request()
                self.assertIsNone(waiting['doorPilot']['nextAction'])
                with self.assertRaises(HTTPError) as rejected:
                    request(self.door(waiting, 'clear'))
                rejected.exception.close()

                self.poll['Tank01_DoorOpened'] = int(action == 'open')
                self.poll['Tank01_DoorClosed'] = int(action == 'close')
                self.tick(1)
                reached = request()
                self.assertEqual(reached['doorPilot']['nextAction'], 'clear')
                with self.assertRaises(HTTPError) as replay:
                    request(command)
                replay.exception.close()

                self.assertIsNone(request(self.door(reached, 'clear'))['doorPilot']['nextAction'])
                self.tick(1)
                cleared = request()
                self.assertIn('confirmed the command clear', cleared['doorPilot']['message'])
                self.assertEqual(cleared['doorPilot']['nextAction'], opposite)

            self.assertEqual(self.writes, [
                {'tag_name': 'Tank01_DoorOpenRemote', 'value': 1},
                {'tag_name': 'Tank01_DoorOpenRemote', 'value': 0},
                {'tag_name': 'Tank01_DoorCloseRemote', 'value': 1},
                {'tag_name': 'Tank01_DoorCloseRemote', 'value': 0},
            ])

    def test_pilot_revalidates_mode_freshness_and_mapping_before_dispatch(self):
        with self.operator(door_pilot=True) as request:
            command = self.door(request(), 'open')
            self.poll.update(Remote=0, Local=1)
            self.tick(1)
            with self.assertRaises(HTTPError) as local:
                request(command)
            self.assertIn('Remote mode', json.load(local.exception)['error'])
            local.exception.close()
            self.poll.update(Remote=1, Local=0)
            self.tick(4, new_sample=False)
            with self.assertRaises(HTTPError) as stale:
                request(command)
            self.assertIn('Fresh PLC', json.load(stale.exception)['error'])
            stale.exception.close()
            self.tick(1)
            self.signal('DuplicateDoorAddress', 0, address='M300')
            with self.assertRaises(HTTPError) as duplicate:
                request(command)
            self.assertIn('tag mapping', json.load(duplicate.exception)['error'])
            duplicate.exception.close()
            self.assertEqual(self.writes, [])

    def test_pilot_resumes_feedback_after_restart_without_replaying_an_uncertain_write(self):
        with self.operator(door_pilot=True) as request:
            command = self.door(request(), 'open')
            self.lost_receipt = True
            with self.assertRaises(HTTPError) as lost:
                request(command)
            self.assertIn('outcome is unknown', json.load(lost.exception)['error'])
            lost.exception.close()
            pilot = self.server.door_pilot
            self.server.door_pilot = DoorPilot(pilot.monitor, pilot.state_path)
            self.tick(1)
            waiting = request()
            self.assertIsNone(waiting['doorPilot']['nextAction'])
            self.assertIn('outcome is unknown', waiting['doorPilot']['message'])
            with self.assertRaises(HTTPError) as replay:
                request(command)
            replay.exception.close()
            self.assertEqual(self.writes, [{'tag_name': 'Tank01_DoorOpenRemote', 'value': 1}])

            self.poll.update(Tank01_DoorOpened=1, Tank01_DoorClosed=0)
            self.tick(1)
            self.assertEqual(request()['doorPilot']['nextAction'], 'clear')


if __name__ == '__main__':
    unittest.main()
