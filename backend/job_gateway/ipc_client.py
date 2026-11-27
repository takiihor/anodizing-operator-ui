"""Read the IPC job API over certificate-verified HTTPS."""

from http.client import HTTPSConnection, HTTPException
import json
from pathlib import Path
import ssl
from urllib.parse import urlsplit

MAX_REPLY_BYTES = 512 * 1024


class JobGatewayUnavailable(Exception):
    """An IPC endpoint could not provide a usable response."""


class JobGatewayReads:
    def health(self):
        return self._get('health')

    def status(self):
        return self._get('status')

    def jobs(self):
        return self._get('jobs')

    def snapshot(self):
        health = self.health()
        if (not isinstance(health.get('build'), str) or not health['build']
                or type(health.get('protocol')) is not int
                or any(type(health.get(field)) is not bool for field in ('gateway_enabled', 'simulated', 'motion_enabled'))):
            raise JobGatewayUnavailable('IPC health returned incomplete capability information.')
        status = self.status()
        if (type(status.get('armed')) is not bool or type(status.get('recovery_required')) is not bool
                or any(field not in status or (status[field] is not None and not isinstance(status[field], str))
                       for field in ('owner', 'active_job_id'))):
            raise JobGatewayUnavailable('IPC status returned incomplete control information.')
        jobs = self.jobs().get('jobs')
        if not isinstance(jobs, list) or not all(isinstance(job, dict) for job in jobs):
            raise JobGatewayUnavailable('IPC jobs returned an invalid job list.')

        return {
            'configured': True,
            'health': {
                'build': health['build'],
                'protocol': health['protocol'],
                'enabled': health['gateway_enabled'],
                'simulated': health['simulated'],
                'motionEnabled': health['motion_enabled'],
            },
            'status': {
                'owner': status['owner'],
                'armed': status['armed'],
                'activeJobId': status['active_job_id'],
                'recoveryRequired': status['recovery_required'],
            },
            'jobs': [
                {key: job[key] for key in ('job_id', 'type', 'state') if isinstance(job.get(key), str)}
                for job in jobs
            ],
        }


class IPCJobGateway(JobGatewayReads):
    @classmethod
    def from_environment(cls, env):
        url = env.get('ANODIZING_JOB_GATEWAY_URL')
        cert_file = env.get('ANODIZING_JOB_GATEWAY_CERT_FILE')
        key_file = env.get('ANODIZING_JOB_GATEWAY_KEY_FILE')
        if not any((url, cert_file, key_file)):
            return None
        if not all((url, cert_file, key_file)):
            raise ValueError('Set the IPC job gateway URL, certificate file and module key file together.')
        return cls(url, cert_file, key_file)

    def __init__(self, url, cert_file, key_file, *, timeout=2):
        try:
            endpoint = urlsplit(url)
            port = endpoint.port
        except ValueError:
            raise ValueError('Enter a valid HTTPS origin for the IPC job gateway.') from None
        if (endpoint.scheme != 'https' or not endpoint.hostname or port == 0 or endpoint.username is not None
                or endpoint.path not in ('', '/') or endpoint.query or endpoint.fragment):
            raise ValueError('Use the IPC job gateway HTTPS origin without a path or credentials.')
        self.host, self.port = endpoint.hostname, port or 443
        self.timeout = timeout
        try:
            self.context = ssl.create_default_context(cafile=cert_file)
        except OSError:
            raise ValueError('Could not load the IPC job gateway certificate file.') from None
        try:
            key = Path(key_file).read_text(encoding='utf-8').strip()
        except (OSError, UnicodeError):
            raise ValueError('Could not read the IPC job gateway module key file.') from None
        if not key or not key.isascii() or not key.isprintable() or any(char.isspace() for char in key):
            raise ValueError('The IPC module key file must contain one bearer token.')
        self.headers = {'Accept': 'application/json', 'Authorization': f'Bearer {key}'}

    def _get(self, resource):
        connection = HTTPSConnection(self.host, self.port, timeout=self.timeout, context=self.context)
        try:
            connection.request('GET', f'/v1/{resource}', headers=self.headers)
            with connection.getresponse() as response:
                if response.status != 200:
                    raise JobGatewayUnavailable(f'IPC {resource} returned HTTP {response.status}.')
                data = response.read(MAX_REPLY_BYTES + 1)
            if len(data) > MAX_REPLY_BYTES:
                raise JobGatewayUnavailable(f'IPC {resource} returned too much data.')
            payload = json.loads(data)
            if not isinstance(payload, dict):
                raise JobGatewayUnavailable(f'IPC {resource} returned an invalid response.')
            return payload
        except ssl.SSLCertVerificationError:
            raise JobGatewayUnavailable('The IPC certificate could not be verified.') from None
        except (OSError, HTTPException, ValueError, RecursionError):
            raise JobGatewayUnavailable(f'Could not read IPC {resource}.') from None
        finally:
            connection.close()
