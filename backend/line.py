"""Deterministic, in-memory simulation for the line operator UI."""

from copy import deepcopy
from datetime import datetime, timezone
from math import ceil, isclose
from threading import Lock
from time import monotonic
from uuid import uuid4

from .recipes import RecipeLibrary
from .tanks import CONTROL_LABELS, TANK_EQUIPMENT, TankSetup

TANK_NAMES = (
    'Ultrasonic clean', 'Rinse', 'Etch', 'Rinse', 'Desmut', 'Rinse', 'Anodize',
    'Rinse', 'Rinse', 'Dye', 'Spray', 'Rinse', 'Seal (DI)', 'Passivation', 'Rinse', 'DI rinse',
)
# Tank, temperature target, process dwell, PLC timer setting.
LINE_TEST_STEPS = (
    (1, 56, 120, 120), (2, None, 10, 10), (3, 40, 40, 40),
    (4, None, 10, 10), (5, 30, 120, 120), (6, None, 10, 10),
    (7, 20, 600, 681), (8, None, 10, 10), (9, None, 10, 10),
    (12, None, 10, 10), (16, 30, 10, 10), (13, 95, 1200, 1200),
)
TEMPERATURES = {1: 56, 3: 40, 5: 30, 7: 20, 10: 30, 13: 95, 14: 38, 16: 30}
# Field definitions mirror the PLC catalog's engineering values and resolution.
FIELDS = {
    'time': ('Time', 's', 0, 9999, 1, 'TimeSV'),
    'temperature': ('Temperature', '°C', 0, 999.9, 0.1, 'TempSV'),
    'current': ('Anodizing current', 'A', 0, 200, 0.1, 'AmpereSV'),
    'swing': ('Swing speed', 'cycles/min', 5, 120, 1, 'SwingSV'),
}
SWING_TANKS = {3, 5, 10, 14}
DOOR_TANKS = {1, 3, 5, 7, 10, 12, 14}
TIME_SCALE = 60
LOW_WATER = 'Tank #1 low water level'
MAX_RECIPE_STEPS = 64
PART_LIMIT = 120
# Settings the PLC catalog provides for each tank.
AVAILABLE = {
    tank: tuple(key for key in FIELDS if key == 'time'
                or (key == 'temperature' and tank in TEMPERATURES)
                or (key == 'current' and tank == 7)
                or (key == 'swing' and tank in SWING_TANKS))
    for tank in range(1, len(TANK_NAMES) + 1)
}


def valid_setting(key, value):
    _, _, minimum, maximum, step, _ = FIELDS[key]
    return (type(value) in (int, float) and minimum <= value <= maximum
            and isclose(value / step, round(value / step), rel_tol=0, abs_tol=1e-8))


class OperatorLine:
    def __init__(self, clock=monotonic, recipe_path=None, tank_path=None):
        self.clock = clock
        self.lock = Lock()
        self.setup = TankSetup(dict(enumerate(TANK_NAMES, 1)), AVAILABLE, FIELDS, tank_path)
        self.library = RecipeLibrary({
            'id': 'line-test', 'revision': 1, 'name': 'Line test',
            'part': 'Al part 600 × 400 mm', 'date': '2026-11-25',
            'steps': [
                {'tank': tank, 'temperature': temperature, 'dwell': dwell, 'plcTime': plc_time}
                for tank, temperature, dwell, plc_time in LINE_TEST_STEPS
            ],
        }, recipe_path)
        self.reset()

    @property
    def recipe(self):
        return self.library.recipes[self.library.selected]

    def reset(self):
        self.settings = {tank: {'time': 0} for tank in range(1, len(TANK_NAMES) + 1)}
        for tank, _, _, plc_time in LINE_TEST_STEPS:
            self.settings[tank]['time'] = plc_time
        self.settings[16]['time'] = 0
        for tank, temperature in TEMPERATURES.items():
            self.settings[tank]['temperature'] = temperature
        self.settings[7]['current'] = 0
        for tank in SWING_TANKS:
            self.settings[tank]['swing'] = 5
        self.mode = 'automatic'
        self.low_water = True
        self.quality = 'live'
        self.station = 1
        self.station_confirmed = True
        self.destination = None
        self.travel_end = None
        self.step = None
        self.processing_end = None
        self.run = 'idle'
        self.completed_tanks = set()
        self.manual_cycles = {}
        self.equipment = {tank: dict.fromkeys(TANK_EQUIPMENT.get(tank, ()), False) for tank in self.settings}
        self.sample_plc(self.clock())

    def blockers(self):
        if self.quality != 'live':
            return ['PLC data is stale.' if self.quality == 'stale' else 'PLC data is unavailable.']
        result = []
        if self.manual_cycles:
            tanks = ', '.join(f'#{tank}' for tank in sorted(self.manual_cycles))
            result.append(f'Manual tank cycles are running at {tanks}. Stop them or wait for completion.')
        targets = {step['tank']: step for step in self.recipe['steps']}
        for tank, target in targets.items():
            if tank == 1 and self.low_water:
                result.append(LOW_WATER)
            intended_time = target['plcTime']
            temperature = target['temperature']
            settings = self.settings[tank]
            if settings['time'] != intended_time:
                result.append(f"Tank #{tank} time is {settings['time']:g} s. Recipe requires {intended_time} s.")
            if temperature is not None and settings.get('temperature') != temperature:
                result.append(f"Tank #{tank} temperature setting is {settings['temperature']:g} °C. "
                              f"Recipe requires {temperature} °C.")
        return result

    def advance(self, now):
        # Replay due events against their scheduled times so a disconnected browser
        # can return to the correct simulated state without driving the sequence.
        for tank, deadline in tuple(self.manual_cycles.items()):
            if deadline <= now:
                del self.manual_cycles[tank]
                self.completed_tanks.add(tank)
        while True:
            if self.travel_end is not None and self.travel_end <= now:
                arrived_at = self.travel_end
                self.station = self.destination
                self.station_confirmed = True
                self.destination = self.travel_end = None
                if self.run == 'running':
                    self.begin_dwell(arrived_at)
            elif self.processing_end is not None and self.processing_end <= now:
                finished_at = self.processing_end
                self.completed_tanks.add(self.station)
                self.processing_end = None
                if self.step + 1 == len(self.recipe['steps']):
                    self.run = 'complete'
                else:
                    self.step += 1
                    self.begin_step(finished_at)
            else:
                return

    def move(self, tank, now):
        self.destination = tank
        self.station_confirmed = False
        self.travel_end = now + max(1, abs(self.station - tank) * 0.4)

    def begin_dwell(self, now):
        self.completed_tanks.discard(self.station)
        self.processing_end = now + self.settings[self.station]['time'] / TIME_SCALE

    def begin_step(self, now):
        tank = self.recipe['steps'][self.step]['tank']
        if tank == self.station and self.station_confirmed:
            self.begin_dwell(now)
        else:
            self.move(tank, now)

    def snapshot(self):
        with self.lock:
            now = self.clock()
            self.advance(now)
            return self.status(now)

    def command(self, body):
        with self.lock:
            now = self.clock()
            self.advance(now)
            action = body.get('action')
            if action == 'reset':
                self.require_idle()
                self.reset()
            elif action == 'simulation':
                self.require_idle()
                if body.get('quality') not in ('live', 'stale', 'unavailable') or type(body.get('lowWater')) is not bool:
                    raise ValueError('Choose a PLC data state and alarm condition.')
                self.quality = body['quality']
                self.low_water = body['lowWater']
            elif action == 'mode':
                self.require_idle()
                if body.get('mode') not in ('manual', 'automatic'):
                    raise ValueError('Choose Manual or Automatic mode.')
                self.mode = body['mode']
            elif action in ('tank-setup', 'tank-setup-default'):
                self.require_idle()
                if action == 'tank-setup':
                    self.setup.update(body.get('tank'), body.get('setup'))
                else:
                    self.setup.restore(body.get('tank'))
            elif action == 'delete-recipe':
                self.require_idle()
                self.delete_recipe(body)
            elif action in ('select-recipe', 'save-recipe'):
                self.require_idle()
                if action == 'save-recipe':
                    self.save_recipe(body)
                else:
                    recipe_id = body.get('id')
                    if not isinstance(recipe_id, str) or recipe_id not in self.library.recipes:
                        raise ValueError('Choose a saved recipe.')
                    if recipe_id != self.library.selected:
                        self.activate_recipe(self.library.recipes, recipe_id)
            elif action == 'start':
                self.require_idle()
                if self.mode != 'automatic':
                    raise ValueError('Switch to Automatic to start the recipe.')
                if ('recipeId' in body or 'revision' in body) and (
                    body.get('recipeId'), body.get('revision')
                ) != (self.recipe['id'], self.recipe['revision']):
                    raise ValueError('The selected recipe changed. Review it before starting.')
                blockers = self.blockers()
                if blockers:
                    raise ValueError(blockers[0])
                self.run, self.step = 'running', 0
                self.completed_tanks.clear()
                self.begin_step(now)
            elif action == 'stop':
                if self.run != 'running':
                    raise ValueError('No recipe is running.')
                self.destination = self.travel_end = self.processing_end = None
                self.run = 'stopped'
            elif action in ('settings', 'move', 'tank-control'):
                self.require_idle()
                if self.mode != 'manual':
                    if action == 'tank-control':
                        raise ValueError('Switch to Manual to operate tank controls.')
                    raise ValueError('Switch to Manual to change settings or move the AGV.')
                tank = body.get('tank')
                if type(tank) is not int or tank not in self.settings:
                    raise ValueError('Select a valid tank.')
                if action == 'settings':
                    self.apply_settings(tank, body.get('values'))
                elif action == 'tank-control':
                    self.apply_control(tank, body.get('control'), body.get('active'), now)
                elif tank == self.station and self.station_confirmed:
                    raise ValueError(f'The AGV is already at tank #{tank}.')
                else:
                    self.move(tank, now)
            else:
                raise ValueError('Unknown operator action.')
            return self.status(now)

    def require_idle(self):
        if self.run == 'running' or self.travel_end is not None:
            raise ValueError('Wait for the current run or AGV move to finish.')

    def recipe_library(self):
        with self.lock:
            return {
                'recipes': deepcopy(list(self.library.recipes.values())),
                'selected': self.library.selected,
            }

    def delete_recipe(self, body):
        recipe_id = body.get('id')
        if not isinstance(recipe_id, str) or recipe_id not in self.library.recipes:
            raise ValueError('Choose a saved recipe to delete.')
        if body.get('revision') != self.library.recipes[recipe_id]['revision']:
            raise ValueError('This recipe changed. Review it before deleting.')
        if len(self.library.recipes) == 1:
            raise ValueError('Keep at least one recipe. Create another before deleting this one.')
        recipes = {key: recipe for key, recipe in self.library.recipes.items() if key != recipe_id}
        if recipe_id == self.library.selected:
            self.activate_recipe(recipes, next(iter(recipes)))
        else:
            self.library.update(recipes, self.library.selected)

    def activate_recipe(self, recipes, recipe_id):
        self.library.update(recipes, recipe_id)
        self.run, self.step = 'idle', None
        self.completed_tanks.clear()

    def save_recipe(self, body):
        recipe_id = body.get('id')
        existing = None
        if recipe_id is not None:
            if not isinstance(recipe_id, str) or recipe_id not in self.library.recipes:
                raise ValueError('Choose a saved recipe to edit.')
            existing = self.library.recipes[recipe_id]
            if body.get('revision') != existing['revision']:
                raise ValueError('This recipe changed. Reopen it before saving.')

        name = body.get('name')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise ValueError('Enter a recipe name of up to 80 characters.')
        name = name.strip()
        if any(recipe['id'] != recipe_id and recipe['name'].casefold() == name.casefold()
               for recipe in self.library.recipes.values()):
            raise ValueError('This recipe name is already in use.')

        part = body.get('part', existing['part'] if existing else '')
        if not isinstance(part, str) or len(part.strip()) > PART_LIMIT:
            raise ValueError(f'Keep the description to {PART_LIMIT} characters.')

        steps = body.get('steps')
        if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_RECIPE_STEPS:
            raise ValueError(f'Add 1 to {MAX_RECIPE_STEPS} recipe steps.')
        targets = {}
        validated_steps = []
        for index, step in enumerate(steps, 1):
            if not isinstance(step, dict) or type(tank := step.get('tank')) is not int or tank not in self.settings:
                raise ValueError(f'Select a valid tank for step {index}.')
            dwell, plc_time, temperature = step.get('dwell'), step.get('plcTime'), step.get('temperature')
            if (not valid_setting('time', dwell) or not valid_setting('time', plc_time) or dwell > plc_time):
                raise ValueError(f'Step {index}: Enter whole-second times from 0 to 9999 with PLC time at least process time.')
            if temperature is not None and (tank not in TEMPERATURES or not valid_setting('temperature', temperature)):
                raise ValueError(f'Step {index}: Select a supported temperature in increments of 0.1 °C.')
            low, high = self.setup.limits(tank, 'time')
            if not low <= plc_time <= high:
                raise ValueError(f'Step {index}: Tank #{tank} allows a PLC time of {low:g}–{high:g} s.')
            if temperature is not None:
                low, high = self.setup.limits(tank, 'temperature')
                if not low <= temperature <= high:
                    raise ValueError(f'Step {index}: Tank #{tank} allows {low:g}–{high:g} °C.')
            if targets.setdefault(tank, (plc_time, temperature)) != (plc_time, temperature):
                raise ValueError(f'Every visit to tank #{tank} must use the same PLC time and temperature target.')
            validated_steps.append({'tank': tank, 'dwell': dwell, 'plcTime': plc_time, 'temperature': temperature})

        recipe_id = recipe_id or uuid4().hex
        recipe = {
            'id': recipe_id, 'name': name, 'steps': validated_steps,
            'revision': existing['revision'] + 1 if existing else 1,
            'part': part.strip(),
            'date': datetime.now().date().isoformat(),
        }
        self.activate_recipe({**self.library.recipes, recipe_id: recipe}, recipe_id)

    def apply_settings(self, tank, values):
        if self.quality != 'live':
            raise ValueError('Fresh PLC readings are required to apply settings.')
        if not isinstance(values, dict) or not values or not values.keys() <= self.settings[tank].keys():
            raise ValueError('Choose settings available for this tank.')
        for key, value in values.items():
            label, unit, _, _, step, _ = FIELDS[key]
            if not self.setup.editable(tank, key):
                raise ValueError(f'{label} on tank #{tank} is not available in Manual.')
            minimum, maximum = self.setup.limits(tank, key)
            if not valid_setting(key, value) or not minimum <= value <= maximum:
                raise ValueError(f'{label} must be {minimum:g}–{maximum:g} {unit} in increments of {step:g}.')
        self.settings[tank].update(values)

    def apply_control(self, tank, control, active, now):
        if self.quality != 'live':
            raise ValueError('Fresh PLC readings are required to operate tank controls.')
        if type(active) is not bool or not isinstance(control, str):
            raise ValueError('Choose a tank control and its desired state.')
        if control == 'cycle':
            if not active:
                self.manual_cycles.pop(tank, None)
            elif tank not in self.manual_cycles:
                if tank == 1 and self.low_water:
                    raise ValueError(LOW_WATER)
                self.completed_tanks.discard(tank)
                duration = self.settings[tank]['time'] / TIME_SCALE
                if duration:
                    self.manual_cycles[tank] = now + duration
                else:
                    self.completed_tanks.add(tank)
        elif control in self.equipment[tank]:
            self.equipment[tank][control] = active
        else:
            raise ValueError(f'This control is unavailable on tank #{tank}.')

    def read_tanks(self, now, *, available):
        tank_data = []
        for tank in range(1, len(TANK_NAMES) + 1):
            name = TANK_NAMES[tank - 1]
            processing = self.processing_end if tank == self.station else None
            deadline = self.manual_cycles.get(tank, processing)
            remaining = ceil((deadline - now) * TIME_SCALE) if deadline is not None else 0
            equipment = self.equipment[tank]
            details = [{'label': 'Basket', 'value': 'Present' if processing is not None else 'Empty'}]
            if tank in DOOR_TANKS or 'door' in equipment:
                details.append({'label': 'Door', 'value': 'Open' if equipment.get('door') else 'Closed'})
            if tank == 7:
                details.extend((
                    {'label': 'Measured current', 'value': '0 A'},
                    {'label': 'Measured voltage', 'value': '0 V'},
                ))
            if tank in (13, 16):
                details.append({'label': 'Conductivity', 'value': '1.2 µS/cm' if tank == 13 else '12 MΩ·cm'})
            if not available:
                details = []
            tank_data.append({
                'id': tank, 'name': name,
                'temperature': TEMPERATURES.get(tank) if available else None,
                'timer': remaining if available else None,
                'timerDone': tank in self.completed_tanks,
                'alarm': LOW_WATER if available and tank == 1 and self.low_water else None,
                'details': details,
                'controls': [
                    {'id': control, 'label': CONTROL_LABELS[control], 'active': active if available else None}
                    for control, active in {'cycle': tank in self.manual_cycles, **equipment}.items()
                ],
                'fields': [
                    {'key': key, 'label': label, 'unit': unit, 'min': minimum, 'max': maximum,
                     'step': step, 'tag': f'Tank{tank:02}_{suffix}',
                     'value': self.settings[tank][key] if available else None}
                    for key, (label, unit, minimum, maximum, step, suffix) in FIELDS.items()
                    if key in self.settings[tank]
                ],
            })
        return tank_data

    def sample_plc(self, now):
        self.last_tank_sample = self.read_tanks(now, available=True)
        self.sample_time = datetime.now(timezone.utc).isoformat()

    def status(self, now):
        if self.quality == 'live':
            self.sample_plc(now)
        if self.quality == 'unavailable':
            tank_data = self.read_tanks(now, available=False)
        else:
            tank_data = deepcopy(self.last_tank_sample)
        # The tank setup is local configuration, not PLC data, so it applies to every reading.
        for tank in tank_data:
            tank['name'] = self.setup.name(tank['id'])
            for field in tank['fields']:
                low, high = self.setup.limits(tank['id'], field['key'])
                field.update(plcMin=field['min'], plcMax=field['max'], min=low, max=high,
                             editable=self.setup.editable(tank['id'], field['key']))

        recipe = self.recipe
        steps = recipe['steps']
        next_tank = None
        if self.run == 'idle':
            next_tank = steps[0]['tank']
        elif self.run == 'running' and self.step + 1 < len(steps):
            next_tank = steps[self.step + 1]['tank']
        return {
            'mode': self.mode, 'simulation': True,
            'plc': {'quality': self.quality, 'sampleTime': self.sample_time, 'remote': self.quality == 'live'},
            'agv': {
                'station': self.station,
                'stationConfirmed': self.station_confirmed,
                'destination': self.destination,
                'moving': self.travel_end is not None,
            },
            'scenario': {'lowWater': self.low_water, 'timeScale': TIME_SCALE},
            'run': {
                'state': self.run, 'step': self.step, 'nextTank': next_tank,
                'phase': 'travel' if self.destination is not None else 'dwell' if self.processing_end is not None else None,
                'blockers': self.blockers(),
            },
            'tanks': tank_data,
            'recipe': deepcopy(recipe),
            'recipes': [{'id': saved['id'], 'name': saved['name']} for saved in self.library.recipes.values()],
            'recipeStepLimit': MAX_RECIPE_STEPS,
        }
