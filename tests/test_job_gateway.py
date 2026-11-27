from contextlib import contextmanager
from http.client import HTTPSConnection
import json
from pathlib import Path
import ssl
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from backend.job_gateway import IPCJobGateway, JobGatewayUnavailable
from backend.server import Server


@contextmanager
def running_console(gateway):
    with Server(0, job_gateway=gateway) as server:
        thread = Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        thread.start()
        try:
            yield f'http://127.0.0.1:{server.server_port}', server.token
        finally:
            server.shutdown()
            thread.join()


def reply(body, code=200):
    response = Mock()
    response.status = code
    response.read.return_value = json.dumps(body).encode()
    return response


class JobGatewayTest(unittest.TestCase):
    def setUp(self):
        self.files = TemporaryDirectory()
        self.addCleanup(self.files.cleanup)
        self.key_file = Path(self.files.name) / 'module.key'
        self.key_file.write_text('read-test-key\n', encoding='utf-8')
        self.cert_file = Path(self.files.name) / 'ipc-cert.pem'

    def client(self):
        with patch('backend.job_gateway.ipc_client.ssl.create_default_context') as context:
            gateway = IPCJobGateway('https://ipc.test:18875', self.cert_file, self.key_file)
        context.assert_called_once_with(cafile=self.cert_file)
        return gateway

    def test_console_reads_only_reviewed_resources_and_filters_the_response(self):
        responses = {
            '/v1/health': reply({'build': 'fixture', 'protocol': 2, 'gateway_enabled': True,
                                 'simulated': False, 'motion_enabled': False}),
            '/v1/status': reply({'owner': 'operator-ui', 'armed': False, 'recovery_required': False,
                                 'active_job_id': None, 'unrelated': 'read-test-key'}),
            '/v1/jobs': reply({'jobs': [{'job_id': 'visit-1', 'type': 'station_visit', 'state': 'done',
                                         'lease_id': 'omit-this-lease'}]}),
        }
        calls = []
        gateway = self.client()

        def connection(host, port, *, timeout, context):
            self.assertEqual((host, port, timeout, context), ('ipc.test', 18875, 2, gateway.context))

            class Connection:
                def request(self, method, path, *, headers):
                    calls.append((method, path, headers['Authorization']))
                    self.path = path

                @contextmanager
                def getresponse(self):
                    yield responses[self.path]

                def close(self):
                    pass

            return Connection()

        with patch('backend.job_gateway.ipc_client.HTTPSConnection', side_effect=connection), running_console(gateway) as (url, token):
            endpoint = url + '/api/job-gateway/status'
            with self.assertRaises(HTTPError) as refused:
                urlopen(endpoint, timeout=2)
            self.assertEqual(refused.exception.code, 403)
            refused.exception.close()
            headers = {'X-Operator-Token': token}
            with urlopen(Request(endpoint, headers=headers), timeout=2) as response:
                raw = response.read()
                status = json.loads(raw)
            self.assertTrue(status['configured'])
            self.assertFalse(status['health']['motionEnabled'])
            self.assertEqual(status['status']['owner'], 'operator-ui')
            self.assertEqual(status['jobs'], [{'job_id': 'visit-1', 'type': 'station_visit', 'state': 'done'}])
            self.assertNotIn(b'read-test-key', raw)
            self.assertNotIn(b'omit-this-lease', raw)
            with self.assertRaises(HTTPError) as read_only:
                urlopen(Request(endpoint, data=b'{}', headers=headers), timeout=2)
            self.assertEqual(read_only.exception.code, 404)
            read_only.exception.close()

        self.assertEqual(calls, [('GET', '/v1/' + path, 'Bearer read-test-key')
                                 for path in ('health', 'status', 'jobs')])

    def test_tls_failure_is_reported_without_connection_fallback(self):
        gateway = self.client()
        connection = Mock(spec=HTTPSConnection)
        connection.request.side_effect = ssl.SSLCertVerificationError('private network details')
        with patch('backend.job_gateway.ipc_client.HTTPSConnection', return_value=connection) as transport:
            with self.assertRaisesRegex(JobGatewayUnavailable, 'certificate could not be verified'):
                gateway.snapshot()
        transport.assert_called_once()
        connection.request.assert_called_once()
        connection.close.assert_called_once()

    def test_unavailable_and_unconfigured_gateway_states(self):
        with running_console(None) as (url, token):
            headers = {'X-Operator-Token': token}
            with urlopen(Request(url + '/api/job-gateway/status', headers=headers), timeout=2) as response:
                self.assertEqual(json.load(response), {'configured': False})

        gateway = self.client()
        with patch.object(gateway, 'health', side_effect=JobGatewayUnavailable('IPC health returned HTTP 403.')):
            with running_console(gateway) as (url, token):
                with self.assertRaises(HTTPError) as unavailable:
                    urlopen(Request(url + '/api/job-gateway/status', headers={'X-Operator-Token': token}), timeout=2)
                with unavailable.exception as response:
                    self.assertEqual(response.code, 503)
                    self.assertEqual(json.load(response), {'error': 'IPC health returned HTTP 403.'})
