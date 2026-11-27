"""Use the supplier REST API contract exercised by the commissioning client."""

from http.client import HTTPConnection, HTTPException, HTTPSConnection
import json
from urllib.parse import urlsplit

MAX_RESPONSE = 1024 * 1024


class PLCUnavailable(Exception):
    """The supplier could not provide a usable response."""


class PLCClient:
    def __init__(self, base_url, api_key, timeout=1.5):
        endpoint = urlsplit(base_url)
        if (endpoint.scheme not in ('http', 'https') or not endpoint.hostname
                or endpoint.username is not None or endpoint.path not in ('', '/')
                or endpoint.query or endpoint.fragment):
            raise ValueError('PLC_API_BASE_URL must be the HTTP or HTTPS origin of the supplier API.')
        if not api_key or not api_key.isascii() or not api_key.isprintable():
            raise ValueError('PLC_API_KEY must contain the supplier API key.')
        self.host = endpoint.hostname
        self.port = endpoint.port if endpoint.port is not None else (443 if endpoint.scheme == 'https' else 80)
        self.target = (endpoint.scheme, self.host, self.port)
        self.connection = HTTPSConnection if endpoint.scheme == 'https' else HTTPConnection
        self.headers = {'X-API-Key': api_key, 'Accept': 'application/json'}
        self.timeout = timeout

    def _request(self, method, path, body=None):
        connection = self.connection(self.host, self.port, timeout=self.timeout)
        try:
            headers = self.headers if body is None else {**self.headers, 'Content-Type': 'application/json'}
            connection.request(method, path, body=None if body is None else json.dumps(body), headers=headers)
            with connection.getresponse() as response:
                if response.status != 200:
                    raise PLCUnavailable(f'PLC API returned HTTP {response.status}.')
                payload = response.read(MAX_RESPONSE + 1)
            if len(payload) > MAX_RESPONSE:
                raise PLCUnavailable('PLC API response exceeded the expected size.')
            data = json.loads(payload)
            if not isinstance(data, dict):
                raise PLCUnavailable('PLC API returned an invalid object.')
            return data
        except (OSError, HTTPException, ValueError, RecursionError) as error:
            raise PLCUnavailable('Could not reach the PLC API or interpret its reply.') from error
        finally:
            connection.close()

    def list_tags(self):
        return self._request('GET', '/api/v1/tags/list')

    def read_signals(self):
        return self._request('GET', '/api/v1/tags/read')

    def write_signal(self, name, value):
        return self._request('POST', '/api/v1/tags/write', {'tag_name': name, 'value': value})
