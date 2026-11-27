from contextlib import contextmanager
from threading import Thread
from time import monotonic, sleep
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from backend.agv_client import AgvClient
from backend.agv_module import AgvController
from backend.agv_module import driver
from backend.server import Server


def command(controller, action, **values):
    return controller.command({'action': action, 'authority': controller.status()['authority'], **values})


def wait_status(controller, predicate, timeout=5):
    end = monotonic() + timeout
    while monotonic() < end:
        status = controller.status()
        if predicate(status):
            return status
        sleep(.02)
    raise AssertionError(f'Timed out waiting for AGV status: {status}')


@contextmanager
def running_server():
    with Server(0, agv_api_only=True) as server:
        thread = Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join()


class FakeSerial(driver.Simulator):
    def __init__(self, *, bare=False, **settings):
        super().__init__(driver.SIMULATOR_PREFIX)
        self.mode = 0
        self.bare = bare
        self.writes = []
        self.serial_settings = settings
        if bare:
            self.reply_prefix = b''

    def write(self, raw):
        self.writes.append(raw)
        super().write(self.prefix + raw if self.bare else raw)
        return len(raw)


class AgvModuleTest(unittest.TestCase):
    def test_framed_serial_jog_and_stop_fence(self):
        route = driver.SIMULATOR_PREFIX
        controller = AgvController('serial', port='/dev/test-agv', address=route[:2].hex(), channel=route[2:].hex())
        self.addCleanup(controller.close)
        serial = FakeSerial()
        with patch.object(driver, 'serial', SimpleNamespace(Serial=lambda **kw: serial)), patch.object(
            driver, 'serial_ports', return_value=[{'device': '/dev/test-agv'}],
        ):
            command(controller, 'connect')
            command(controller, 'query_status')
            wait_status(controller, lambda state: state['fresh'])
            with self.assertRaisesRegex(ValueError, 'Confirm'):
                command(controller, 'arm')
            command(controller, 'arm', confirmed=True)
            armed = controller.status()['authority']
            command(controller, 'forward')
            self.assertIn(driver.command_frame('forward', None, route).encode(), serial.writes)
            wait_status(controller, lambda state: state['motion'] == 'moving')
            command(controller, 'stop')
            reached = wait_status(controller, lambda state: state['results'].get('stop', {}).get('confirmedStopped'))
            self.assertEqual(reached['motion'], 'stopped')
            self.assertFalse(reached['armed'])
            self.assertIn(driver.command_frame('stop', None, route).encode(), serial.writes)
            with self.assertRaisesRegex(ValueError, 'changed'):
                controller.command({'action': 'reverse', 'authority': armed})
            wait_status(controller, lambda state: state['canDisconnect'], timeout=12)
            self.assertFalse(command(controller, 'disconnect')['connected'])

    def test_wired_link_sends_reads_only(self):
        controller = AgvController('wired', port='/dev/test-wired')
        self.addCleanup(controller.close)
        serial = FakeSerial(bare=True)
        with patch.object(driver, 'serial', SimpleNamespace(Serial=lambda **kw: serial)):
            command(controller, 'connect')
            command(controller, 'query_status')
            status = wait_status(controller, lambda state: state['fresh'])
            self.assertFalse(status['canStop'])
            for action in ('arm', 'forward', 'stop'):
                with self.subTest(action=action), self.assertRaises(ValueError):
                    command(controller, action, confirmed=True)
            self.assertTrue(serial.writes)
            self.assertTrue(all(raw == driver.command_frame('query_status', None, b'').encode() for raw in serial.writes))

    def test_operator_pc_can_reach_ipc_module_over_forwarded_api(self):
        with running_server() as ipc:
            client = AgvClient(ipc.server_port, native=True)
            connected = command(client, 'connect')
            self.assertEqual(connected['transport'], 'simulator')
            command(client, 'query_status')
            wait_status(client, lambda state: state['fresh'])
            command(client, 'arm')
            sent = command(client, 'rotate_clockwise')
            self.assertIn('rotate_clockwise', sent['results'])
            with ipc.agv.link.lock:
                writes = [entry['hex'] for entry in ipc.agv.link.log if entry['direction'] == 'TX']
            self.assertIn(driver.hex_bytes(driver.command_frame('rotate_clockwise', None, driver.SIMULATOR_PREFIX).encode()), writes)
