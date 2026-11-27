"""Supervised open, close and clear operations for the commissioned tank #1 door."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock
from uuid import uuid4

from .client import PLCUnavailable
from .monitor import Signal
from .tank_status import DOOR_SIGNALS, door_position

TANK = 1
COMMANDS = {
    'open': 'Tank01_DoorOpenRemote',
    'close': 'Tank01_DoorCloseRemote',
}
LIMIT = {'open': 'Open', 'close': 'Closed'}
_, OPEN_ADDRESS, CLOSED_ADDRESS, OPEN_COMMAND, CLOSE_COMMAND = DOOR_SIGNALS[TANK]
EXPECTED_SIGNALS = {
    COMMANDS['open']: Signal(OPEN_COMMAND, 'Bool', 'W'),
    COMMANDS['close']: Signal(CLOSE_COMMAND, 'Bool', 'W'),
    'Tank01_DoorOpened': Signal(OPEN_ADDRESS, 'Bool', 'R'),
    'Tank01_DoorClosed': Signal(CLOSED_ADDRESS, 'Bool', 'R'),
    'Remote': Signal('L4', 'Bool', 'R'),
    'Local': Signal('L3', 'Bool', 'R'),
}
MAX_WRITE_SAMPLE_AGE = timedelta(seconds=3)
OBSERVATION_WINDOW = timedelta(seconds=30)


@dataclass(frozen=True)
class DoorAttempt:
    direction: str
    clearing: bool
    after: datetime
    accepted: bool = False


class DoorPilot:
    def __init__(self, monitor, state_path):
        self.monitor = monitor
        self.state_path = Path(state_path)
        self.target = list(monitor.client.target)
        self.lock = Lock()
        self.revision = uuid4().hex
        self.attempt = self._load()

    def _load(self):
        try:
            data = json.loads(self.state_path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return None
        if data['target'] != self.target:
            raise ValueError('The door pilot record belongs to a different PLC endpoint.')
        attempt = data['attempt']
        if attempt is None:
            return None
        after = datetime.fromisoformat(attempt['after'])
        if (attempt['direction'] not in COMMANDS or after.tzinfo is None
                or type(attempt['clearing']) is not bool or type(attempt['accepted']) is not bool):
            raise ValueError('Invalid door pilot record. Check the physical door and PLC commands.')
        return DoorAttempt(attempt['direction'], attempt['clearing'], after, attempt['accepted'])

    def _save(self, attempt):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        data = {'target': self.target, 'attempt': None}
        if attempt is not None:
            data['attempt'] = {
                'direction': attempt.direction, 'clearing': attempt.clearing,
                'after': attempt.after.isoformat(), 'accepted': attempt.accepted,
            }
        with NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.state_path.parent, delete=False) as file:
            temporary_path = Path(file.name)
            try:
                json.dump(data, file, allow_nan=False)
                file.flush()
                os.fsync(file.fileno())
                file.close()
                temporary_path.replace(self.state_path)
                directory = os.open(self.state_path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                temporary_path.unlink(missing_ok=True)
        self.attempt = attempt
        self.revision = uuid4().hex

    def _next_action(self, sample):
        now = self.monitor.now()
        if (sample.quality != 'live' or sample.timestamp is None
                or abs(now - sample.timestamp) > MAX_WRITE_SAMPLE_AGE):
            return None, 'Fresh PLC feedback is required.'
        addresses = [signal.address for signal in sample.catalog.values()]
        if any(sample.catalog.get(tag) != signal or addresses.count(signal.address) != 1
               for tag, signal in EXPECTED_SIGNALS.items()):
            return None, 'The live tag mapping differs from the commissioned tank #1 mapping.'
        if sample.remote is not True:
            return None, 'The PLC must report Remote mode.'
        bits = {direction: sample.value(tag, kind='Bool', permission='W') for direction, tag in COMMANDS.items()}
        position = door_position(sample, TANK)
        if any(bit is None for bit in bits.values()) or position in ('Unavailable', 'Conflicting feedback'):
            return None, 'Check the door position and command feedback at tank #1.'

        attempt = self.attempt
        if attempt is None:
            if any(bits.values()):
                return None, 'A PLC door command is already active. Check it at the line.'
            return self._available_move(position)
        if sample.timestamp <= attempt.after:
            return None, 'Waiting for a newer PLC sample after the write.'

        at_target = position == LIMIT[attempt.direction]
        both_clear = not any(bits.values())
        if attempt.clearing and both_clear and at_target:
            action, _ = self._available_move(position)
            return action, f'PLC confirmed the command clear. Door {position.lower()}.'

        expired = now - attempt.after >= OBSERVATION_WINDOW
        if at_target and bits[attempt.direction] and sum(bits.values()) == 1 and (not attempt.clearing or expired):
            return 'clear', f'PLC reports the door {position.lower()}. Verify it at the tank before clearing the command.'
        if expired and both_clear and position in LIMIT.values():
            return 'resolve', 'The previous operation is unconfirmed. Inspect the door and verify both commands are clear.'
        if expired:
            return None, 'Door feedback was not confirmed. Check the door and PLC command bits at the line.'

        if not attempt.accepted:
            return None, 'PLC write outcome is unknown. Waiting for command and position feedback.'
        if attempt.clearing:
            return None, 'Clear accepted. Waiting for PLC confirmation.'
        return None, f'{attempt.direction.title()} accepted. Waiting for the door limit and command feedback.'

    @staticmethod
    def _available_move(position):
        if position == 'Closed':
            return 'open', 'Ready to request Open for tank #1.'
        if position == 'Open':
            return 'close', 'Ready to request Close for tank #1.'
        return None, 'The door must report an open or closed limit before a new request.'

    def status(self, sample):
        if not self.lock.acquire(blocking=False):
            return {'tank': TANK, 'revision': None, 'nextAction': None, 'message': 'Sending door command…'}
        try:
            action, message = self._next_action(sample)
            return {'tank': TANK, 'revision': self.revision, 'nextAction': action, 'message': message}
        finally:
            self.lock.release()

    def command(self, body):
        if type(body.get('tank')) is not int or body['tank'] != TANK or body.get('confirmed') is not True:
            raise ValueError('Confirm the supervised command for tank #1.')
        if not self.lock.acquire(blocking=False):
            raise ValueError('A door request is already being processed.')
        try:
            if body.get('revision') != self.revision:
                raise ValueError('Door status changed. Review the current status before sending another command.')
            sample = self.monitor.snapshot(revalidate=True)
            action, message = self._next_action(sample)
            if action is None or body.get('operation') != action:
                raise ValueError(message)
            if action == 'resolve':
                self._save(None)
                return

            direction = self.attempt.direction if action == 'clear' else action
            tag = COMMANDS[direction]
            value = 0 if action == 'clear' else 1
            previous_attempt = self.attempt
            attempt = DoorAttempt(direction, action == 'clear', max(sample.timestamp, self.monitor.now()))
            self._save(attempt)
            if not self.monitor.fresh(sample.timestamp) or abs(self.monitor.now() - sample.timestamp) > MAX_WRITE_SAMPLE_AGE:
                self._save(previous_attempt)
                raise ValueError('PLC feedback expired before the door write. Review the current status.')

            try:
                receipt = self.monitor.client.write_signal(tag, value)
            except PLCUnavailable as error:
                self._save(replace(attempt, after=max(attempt.after, self.monitor.now())))
                raise ValueError('PLC write outcome is unknown. Check the door before continuing.') from error
            accepted = self._matches_receipt(receipt, tag, value)
            self._save(replace(attempt, after=max(attempt.after, self.monitor.now()), accepted=accepted))
            if not accepted:
                raise ValueError('The PLC did not acknowledge the expected door write. Check the door before continuing.')
        finally:
            self.lock.release()

    @staticmethod
    def _matches_receipt(receipt, tag, value):
        expected = EXPECTED_SIGNALS[tag]
        return (receipt.get('written') is True and receipt.get('tag_name') == tag
                and receipt.get('device_address') == expected.address and receipt.get('data_type') == expected.data_type
                and receipt.get('read_write') == expected.permission and receipt.get('multiplier', 1) == expected.multiplier
                and type(receipt.get('value')) in (int, float) and receipt['value'] == value)
