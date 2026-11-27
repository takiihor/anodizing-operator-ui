"""Persist the operator-editable tank setup: process names, which PLC settings
operators may change in Manual, and the allowed range for each setting.

The PLC catalog decides which settings exist for a tank and their hard limits.
The setup can only rename a tank, hide a setting from Manual, or narrow its range.
"""

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

NAME_LIMIT = 40

# Green pushbuttons observed on the physical tank panels, in panel order.
# The cycle button is common to all tanks.
CONTROL_LABELS = {
    'cycle': 'Cycle',
    'filter': 'Filter pump',
    'heater': 'Heater',
    'ultrasonic': 'Ultrasonic',
    'door': 'Pneumatic door',
    'swing': 'Swing motor',
    'rectifier': 'Rectifier',
    'pressure': 'Pressure pump',
}
TANK_EQUIPMENT = {
    1: ('filter', 'heater', 'ultrasonic', 'door'),
    3: ('filter', 'heater', 'swing', 'door'),
    5: ('filter', 'heater', 'swing', 'door'),
    7: ('filter', 'rectifier', 'door'),
    10: ('filter', 'heater', 'swing', 'door'),
    11: ('pressure',),
    13: ('heater', 'door'),
    14: ('filter', 'heater', 'swing', 'door'),
    16: ('filter', 'heater'),
}


class TankSetup:
    def __init__(self, names, available, fields, path=None):
        """names: default process names by tank number.
        available: tank number -> iterable of field keys the PLC provides.
        fields: key -> (label, unit, minimum, maximum, step, tag suffix)."""
        self.path = Path(path) if path is not None else None
        self.fields = fields
        self.available = {tank: tuple(keys) for tank, keys in available.items()}
        self.defaults = dict(names)
        self.tanks = {tank: self.default(tank) for tank in self.available}
        if self.path is not None:
            try:
                data = json.loads(self.path.read_text(encoding='utf-8'))
            except FileNotFoundError:
                return
            for key, saved in data.get('tanks', {}).items():
                tank = int(key)
                if tank in self.tanks:
                    try:
                        self.tanks[tank] = self.validate(tank, saved)
                    except ValueError:
                        # Keep the default for a tank whose saved setup no longer fits the PLC catalog.
                        pass

    def default(self, tank):
        return {
            'name': self.defaults[tank],
            'fields': {
                key: {'editable': True, 'min': self.fields[key][2], 'max': self.fields[key][3]}
                for key in self.available[tank]
            },
        }

    def limits(self, tank, key):
        setting = self.tanks[tank]['fields'][key]
        return setting['min'], setting['max']

    def editable(self, tank, key):
        return self.tanks[tank]['fields'][key]['editable']

    def name(self, tank):
        return self.tanks[tank]['name']

    def validate(self, tank, body):
        if not isinstance(body, dict):
            raise ValueError('Send the tank setup as an object.')
        name = body.get('name')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= NAME_LIMIT:
            raise ValueError(f'Enter a process name of up to {NAME_LIMIT} characters.')
        settings = body.get('fields')
        if not isinstance(settings, dict) or set(settings) != set(self.available[tank]):
            raise ValueError(f'Send every setting available on tank #{tank}.')
        result = {}
        for key in self.available[tank]:
            label, unit, minimum, maximum, step, _ = self.fields[key]
            setting = settings[key]
            if not isinstance(setting, dict) or type(setting.get('editable')) is not bool:
                raise ValueError(f'{label}: choose whether operators can change it.')
            low, high = setting.get('min'), setting.get('max')
            for value in (low, high):
                if (type(value) not in (int, float) or not minimum <= value <= maximum
                        or abs(value / step - round(value / step)) > 1e-8):
                    raise ValueError(f'{label}: the allowed range must be within {minimum:g}–{maximum:g} {unit} '
                                     f'in steps of {step:g}.')
            if low > high:
                raise ValueError(f'{label}: the lowest allowed value must not be above the highest.')
            result[key] = {'editable': setting['editable'], 'min': low, 'max': high}
        return {'name': name.strip(), 'fields': result}

    def update(self, tank, body):
        if type(tank) is not int or tank not in self.tanks:
            raise ValueError('Select a valid tank.')
        tanks = {**self.tanks, tank: self.validate(tank, body)}
        self.save(tanks)

    def restore(self, tank):
        if type(tank) is not int or tank not in self.tanks:
            raise ValueError('Select a valid tank.')
        self.save({**self.tanks, tank: self.default(tank)})

    def save(self, tanks):
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent, delete=False) as file:
                temporary_path = Path(file.name)
                try:
                    json.dump({'tanks': {str(tank): setup for tank, setup in tanks.items()}}, file,
                              ensure_ascii=False, allow_nan=False, indent=2)
                    file.flush()
                    os.fsync(file.fileno())
                    file.close()
                    temporary_path.replace(self.path)
                finally:
                    temporary_path.unlink(missing_ok=True)
        self.tanks = tanks
