"""Keep one last good PLC snapshot and reject stale or invalid supplier polls."""

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from math import isfinite
from pathlib import Path
from threading import Lock
from time import monotonic
from zoneinfo import ZoneInfo

from .client import PLCClient, PLCUnavailable

POLL_PERIOD = 0.7
CATALOG_PERIOD = 30
NUMBER_TYPES = ('Word', 'SignedWord', 'Float')


@dataclass(frozen=True)
class Signal:
    address: str
    data_type: str
    permission: str
    multiplier: float = 1


@dataclass(frozen=True)
class PLCSnapshot:
    quality: str = 'unavailable'
    timestamp: datetime | None = None
    values: dict = field(default_factory=dict)
    catalog: dict = field(default_factory=dict)

    def value(self, name, *, kind, permission='R', address=None):
        signal = self.catalog.get(name)
        if signal is None or signal.permission != permission:
            return None
        if address is not None and signal.address != address:
            return None
        value = self.values.get(name)
        if kind == 'Bool':
            return bool(value) if signal.data_type == kind and type(value) in (bool, int, float) and value in (0, 1) else None
        if signal.data_type not in NUMBER_TYPES:
            return None
        if type(value) is int and abs(value) < 2**53:
            return value
        return value if type(value) is float and isfinite(value) else None

    @property
    def remote(self):
        remote = self.value('Remote', kind='Bool')
        local = self.value('Local', kind='Bool')
        return remote if remote is not None and local is not None and remote != local else None


def read_catalog(data):
    entries = data.get('tags')
    if not isinstance(entries, list) or not entries:
        raise PLCUnavailable('PLC API returned an empty or invalid catalog.')
    catalog = {}
    for item in entries:
        if not isinstance(item, dict):
            raise PLCUnavailable('PLC API returned an invalid catalog entry.')
        name, address, data_type, permission = (
            item.get(key) for key in ('tag_name', 'device_address', 'data_type', 'read_write')
        )
        if not all(isinstance(value, str) and value.strip() for value in (name, address, data_type, permission)):
            raise PLCUnavailable('PLC API returned incomplete tag metadata.')
        if name in catalog:
            raise PLCUnavailable('PLC API returned duplicate tag names.')
        multiplier = item.get('multiplier', 1)
        if type(multiplier) not in (int, float) or not 0 < multiplier < float('inf'):
            raise PLCUnavailable('PLC API returned an invalid tag multiplier.')
        catalog[name] = Signal(address, data_type, permission, multiplier)
    return catalog


class PLCMonitor:
    @classmethod
    def from_environment(cls, env):
        api_key = env.get('PLC_API_KEY', '')
        key_file = env.get('PLC_API_KEY_FILE')
        if key_file:
            if api_key:
                raise ValueError('Set either PLC_API_KEY_FILE or PLC_API_KEY.')
            try:
                api_key = Path(key_file).read_text(encoding='utf-8').strip()
            except (OSError, UnicodeError):
                raise ValueError('Could not read the PLC API key file.') from None
        client = PLCClient(env.get('PLC_API_BASE_URL', ''), api_key)
        timezone_name = env.get('PLC_TIMESTAMP_TIMEZONE')
        max_age = timedelta(milliseconds=int(env.get('PLC_STALE_AFTER_MS', '3000')))
        if max_age <= timedelta():
            raise ValueError('PLC_STALE_AFTER_MS must be positive.')
        return cls(client, timestamp_timezone=ZoneInfo(timezone_name) if timezone_name else None,
                   timestamp_offset=timedelta(seconds=int(env.get('PLC_TIMESTAMP_OFFSET_SECONDS', '0'))),
                   max_age=max_age)

    def __init__(self, client, *, timestamp_timezone=None, timestamp_offset=timedelta(),
                 max_age=timedelta(seconds=3), now=lambda: datetime.now(timezone.utc), clock=monotonic):
        self.client = client
        self.timestamp_timezone = timestamp_timezone
        self.timestamp_offset = timestamp_offset
        self.max_age = max_age
        self.now = now
        self.clock = clock
        self._lock = Lock()
        self._next_poll = 0
        self._next_catalog = 0
        self._catalog = {}
        self._sample = PLCSnapshot()

    def timestamp(self, raw):
        try:
            stamp = datetime.fromisoformat(raw.replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                if self.timestamp_timezone is None:
                    return None
                stamp = stamp.replace(tzinfo=self.timestamp_timezone)
            return stamp.astimezone(timezone.utc) + self.timestamp_offset
        except (AttributeError, ValueError, OverflowError):
            return None

    def fresh(self, stamp):
        return stamp is not None and abs(self.now() - stamp) <= self.max_age

    def refresh(self, *, force_catalog=False):
        now = self.clock()
        self._next_poll = now + POLL_PERIOD
        try:
            if force_catalog or now >= self._next_catalog:
                self._catalog = read_catalog(self.client.list_tags())
                self._next_catalog = now + CATALOG_PERIOD
            data = self.client.read_signals()
        except PLCUnavailable:
            self._next_catalog = 0
            self._mark_stale()
            return
        stamp = self.timestamp(data.get('timestamp'))
        if (not self.fresh(stamp) or type(data.get('tags_failed')) is not int or data['tags_failed'] != 0
                or not any(name in data and data[name] is not None for name in self._catalog)):
            self._mark_stale()
            return
        previous = self._sample
        if previous.timestamp is None or stamp > previous.timestamp:
            self._sample = PLCSnapshot('live', stamp, data, self._catalog)
        elif (stamp < previous.timestamp or self._catalog != previous.catalog
              or any(data.get(tag) != previous.values.get(tag) for tag in self._catalog)):
            self._mark_stale()

    def _mark_stale(self):
        if self._sample.quality == 'live':
            self._sample = replace(self._sample, quality='stale')

    def snapshot(self, *, revalidate=False):
        if revalidate:
            with self._lock:
                self.refresh(force_catalog=True)
        elif self.clock() >= self._next_poll and self._lock.acquire(blocking=False):
            try:
                if self.clock() >= self._next_poll:
                    self.refresh()
            finally:
                self._lock.release()
        sample = self._sample
        if sample.quality == 'live' and not self.fresh(sample.timestamp):
            return replace(sample, quality='stale')
        return sample
