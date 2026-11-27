from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from backend.line import OperatorLine


class OperatorLineTest(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.line = OperatorLine(clock=lambda: self.now)

    def command(self, action, **body):
        return self.line.command({'action': action, **body})

    def prepare_automatic(self):
        self.command('simulation', quality='live', lowWater=False)
        self.command('mode', mode='manual')
        self.command('settings', tank=16, values={'time': 10})
        self.command('mode', mode='automatic')

    def test_initial_blockers_and_recipe_timer_pad(self):
        status = self.line.snapshot()
        self.assertIn('Tank #1 low water level', status['run']['blockers'])
        self.assertIn('Tank #16 time is 0 s. Recipe requires 10 s.', status['run']['blockers'])
        self.assertEqual(status['recipe']['steps'][6]['dwell'], 600)
        self.assertEqual(status['recipe']['steps'][6]['plcTime'], 681)
        self.assertEqual(status['tanks'][12]['temperature'], 95)
        with self.assertRaisesRegex(ValueError, 'low water'):
            self.command('start')

    def test_manual_settings_validate_atomically_and_report_readback(self):
        self.command('mode', mode='manual')
        with self.assertRaisesRegex(ValueError, 'increments'):
            self.command('settings', tank=16, values={'time': 10, 'temperature': 30.02})
        status = self.line.snapshot()
        self.assertEqual(status['tanks'][15]['fields'][0]['value'], 0)
        status = self.command('settings', tank=16, values={'time': 10})
        self.assertEqual(status['tanks'][15]['fields'][0]['value'], 10)
        self.command('simulation', quality='stale', lowWater=False)
        with self.assertRaisesRegex(ValueError, 'Fresh PLC'):
            self.command('settings', tank=16, values={'time': 11})

    def test_stale_readings_preserve_the_last_good_sample_until_live(self):
        live = self.command('simulation', quality='live', lowWater=True)
        stale = self.command('simulation', quality='stale', lowWater=False)
        self.assertEqual(stale['plc']['quality'], 'stale')
        self.assertFalse(stale['scenario']['lowWater'])
        self.assertEqual(stale['plc']['sampleTime'], live['plc']['sampleTime'])
        self.assertEqual(stale['tanks'], live['tanks'])

        self.now = 10
        self.assertEqual(self.line.snapshot()['tanks'], live['tanks'])
        unavailable = self.command('simulation', quality='unavailable', lowWater=False)
        self.assertIsNone(unavailable['tanks'][0]['alarm'])
        self.assertIsNone(unavailable['tanks'][0]['temperature'])
        stale = self.command('simulation', quality='stale', lowWater=False)
        self.assertEqual(stale['plc']['sampleTime'], live['plc']['sampleTime'])
        self.assertEqual(stale['tanks'], live['tanks'])

        fresh = self.command('simulation', quality='live', lowWater=False)
        self.assertEqual(fresh['plc']['quality'], 'live')
        self.assertIsNone(fresh['tanks'][0]['alarm'])

    def test_manual_move_confirms_arrival_and_blocks_competing_commands(self):
        self.command('mode', mode='manual')
        status = self.command('move', tank=12)
        self.assertEqual(status['agv'], {'station': 1, 'stationConfirmed': False, 'destination': 12, 'moving': True})
        self.assertEqual(status['manualTravel'], {'durationMs': 4400, 'remainingMs': 4400})
        self.now = 2.2
        moving = self.line.snapshot()
        self.assertEqual(moving['manualTravel'], {'durationMs': 4400, 'remainingMs': 2200})
        self.assertFalse(moving['agv']['stationConfirmed'])
        with self.assertRaisesRegex(ValueError, 'current run or AGV'):
            self.command('mode', mode='automatic')
        self.now = 10
        arrived = self.line.snapshot()
        self.assertEqual(arrived['agv'], {'station': 12, 'stationConfirmed': True, 'destination': None, 'moving': False})
        self.assertIsNone(arrived['manualTravel'])

    def test_manual_controls_match_photographed_tank_panels(self):
        expected = [
            ('filter', 'heater', 'ultrasonic', 'door'), (),
            ('filter', 'heater', 'swing', 'door'), (),
            ('filter', 'heater', 'swing', 'door'), (),
            ('filter', 'rectifier', 'door'), (), (),
            ('filter', 'heater', 'swing', 'door'), ('pressure',), (),
            ('heater', 'door'), ('filter', 'heater', 'swing', 'door'), (),
            ('filter', 'heater'),
        ]
        tanks = self.line.snapshot()['tanks']
        self.assertEqual([tuple(control['id'] for control in tank['controls']) for tank in tanks],
                         [('cycle', *equipment) for equipment in expected])

    def test_manual_equipment_readback_and_command_gating(self):
        with self.assertRaisesRegex(ValueError, 'Switch to Manual'):
            self.command('tank-control', tank=13, control='door', active=True)
        self.command('mode', mode='manual')
        with self.assertRaisesRegex(ValueError, 'unavailable on tank #12'):
            self.command('tank-control', tank=12, control='door', active=True)
        with self.assertRaisesRegex(ValueError, 'low water'):
            self.command('tank-control', tank=1, control='cycle', active=True)

        opened = self.command('tank-control', tank=13, control='door', active=True)['tanks'][12]
        self.assertTrue(opened['controls'][-1]['active'])
        self.assertIn({'label': 'Door', 'value': 'Open'}, opened['details'])
        self.command('simulation', quality='stale', lowWater=False)
        self.assertEqual(self.line.snapshot()['tanks'][12], opened)
        with self.assertRaisesRegex(ValueError, 'Fresh PLC'):
            self.command('tank-control', tank=13, control='door', active=False)
        unavailable = self.command('simulation', quality='unavailable', lowWater=False)
        self.assertIsNone(unavailable['tanks'][12]['controls'][-1]['active'])
        self.command('simulation', quality='live', lowWater=False)
        closed = self.command('tank-control', tank=13, control='door', active=False)['tanks'][12]
        self.assertFalse(closed['controls'][-1]['active'])
        self.assertIn({'label': 'Door', 'value': 'Closed'}, closed['details'])

    def test_manual_cycles_run_and_stop_independently_of_the_agv(self):
        self.prepare_automatic()
        self.command('mode', mode='manual')
        self.command('tank-control', tank=3, control='cycle', active=True)
        started = self.command('tank-control', tank=5, control='cycle', active=True)
        self.assertEqual(started['agv']['station'], 1)
        self.assertFalse(started['agv']['moving'])
        self.assertEqual([started['tanks'][i]['timer'] for i in (2, 4)], [40, 120])
        self.command('mode', mode='automatic')
        with self.assertRaisesRegex(ValueError, 'Manual tank cycles are running'):
            self.command('start')
        self.command('mode', mode='manual')

        self.now = 0.5
        stopped = self.command('tank-control', tank=3, control='cycle', active=False)['tanks']
        self.assertEqual(stopped[2]['timer'], 0)
        self.assertFalse(stopped[2]['timerDone'])
        self.assertFalse(stopped[2]['controls'][0]['active'])
        self.assertEqual(stopped[4]['timer'], 90)
        self.assertTrue(stopped[4]['controls'][0]['active'])
        self.now = 2
        complete = self.line.snapshot()
        self.assertTrue(complete['tanks'][4]['timerDone'])
        self.assertFalse(complete['tanks'][4]['controls'][0]['active'])
        self.assertEqual(complete['run']['blockers'], [])

    def test_saved_recipes_and_selection_survive_restart_and_reset(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'recipes.json'
            self.line = OperatorLine(clock=lambda: self.now, recipe_path=path)
            steps = [
                {'tank': 5, 'temperature': 30, 'dwell': 120, 'plcTime': 120},
                {'tank': 2, 'temperature': None, 'dwell': 10, 'plcTime': 10},
            ]
            saved = self.command('save-recipe', name='Return rinse', steps=steps)['recipe']
            edited = self.command('save-recipe', id=saved['id'], revision=saved['revision'],
                                  name='Return rinse', steps=steps[::-1])
            self.assertEqual([recipe['name'] for recipe in edited['recipes']], ['Line test', 'Return rinse'])
            self.assertEqual(edited['recipe']['steps'], steps[::-1])
            self.assertEqual(edited['recipe']['revision'], 2)

            self.line = OperatorLine(clock=lambda: self.now, recipe_path=path)
            self.assertEqual(self.line.snapshot()['recipe'], edited['recipe'])
            self.assertEqual(self.command('reset')['recipe'], edited['recipe'])
            self.assertEqual(self.command('select-recipe', id='line-test')['recipe']['id'], 'line-test')
            self.assertEqual(self.command('select-recipe', id=saved['id'])['recipe'], edited['recipe'])

    def test_selected_recipe_controls_travel_and_consecutive_visits(self):
        steps = [
            {'tank': tank, 'temperature': temperature, 'dwell': 120, 'plcTime': 120}
            for tank, temperature in [(5, 30), (1, 56), (1, 56), (5, 30)]
        ]
        self.command('simulation', quality='live', lowWater=False)
        saved = self.command('save-recipe', name='Repeat cleaning', steps=steps)['recipe']
        with self.assertRaisesRegex(ValueError, 'selected recipe changed'):
            self.command('start', recipeId='line-test', revision=1)

        started = self.command('start', recipeId=saved['id'], revision=saved['revision'])
        self.assertEqual(started['agv']['destination'], 5)
        self.assertEqual(started['run']['nextTank'], 1)
        with self.assertRaisesRegex(ValueError, 'current run or AGV'):
            self.command('select-recipe', id='line-test')
        with self.assertRaisesRegex(ValueError, 'current run or AGV'):
            self.command('save-recipe', name='Other', steps=steps)

        self.now = 5.4
        processing = self.line.snapshot()
        self.assertEqual(processing['agv']['station'], 1)
        self.assertEqual(processing['run']['step'], 1)
        self.assertEqual(processing['run']['nextTank'], 1)
        self.now = 7.4
        repeated = self.line.snapshot()
        self.assertEqual(repeated['run']['step'], 2)
        self.assertEqual(repeated['run']['phase'], 'dwell')
        self.assertFalse(repeated['agv']['moving'])
        self.assertFalse(repeated['tanks'][0]['timerDone'])
        self.now = 30
        complete = self.line.snapshot()
        self.assertEqual(complete['run']['state'], 'complete')
        self.assertEqual(complete['agv']['station'], 5)

    def test_recipe_validation_leaves_the_saved_recipe_intact(self):
        steps = [{'tank': 2, 'temperature': None, 'dwell': 10, 'plcTime': 10}]
        saved = self.command('save-recipe', name='Rinse only', steps=steps)['recipe']
        self.assertEqual(self.line.snapshot()['run']['blockers'], [])
        with self.assertRaisesRegex(ValueError, 'same PLC time'):
            self.command('save-recipe', id=saved['id'], revision=saved['revision'], name=saved['name'],
                         steps=steps + [{**steps[0], 'plcTime': 20}])
        with self.assertRaisesRegex(ValueError, 'valid tank'):
            self.command('save-recipe', name='Invalid', steps=[{**steps[0], 'tank': 17}])
        with self.assertRaisesRegex(ValueError, 'recipe changed'):
            self.command('save-recipe', id=saved['id'], revision=0, name=saved['name'], steps=steps)
        self.assertEqual(self.line.snapshot()['recipe'], saved)

    def test_automatic_recipe_returns_to_seal_and_finishes(self):
        self.prepare_automatic()
        self.command('start')
        destinations = []
        for tick in range(600):
            self.now = tick / 10
            status = self.line.snapshot()
            agv = status['agv']
            move = (agv['station'], agv['destination'])
            if agv['moving'] and move not in destinations:
                destinations.append(move)
        self.assertEqual(destinations, [
            (1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 7), (7, 8), (8, 9),
            (9, 12), (12, 16), (16, 13),
        ])
        self.assertEqual(status['run']['state'], 'complete')
        self.assertEqual(status['agv']['station'], 13)

    def test_simulated_run_catches_up_after_idle_period(self):
        self.prepare_automatic()
        self.command('start')
        self.now = 120
        self.assertEqual(self.line.snapshot()['run']['state'], 'complete')


    def test_stop_during_dwell_cancels_the_timer_and_can_restart(self):
        with self.assertRaisesRegex(ValueError, 'No recipe is running'):
            self.command('stop')
        self.prepare_automatic()
        self.command('start')
        self.now = 1
        self.assertGreater(self.line.snapshot()['tanks'][0]['timer'], 0)
        stopped = self.command('stop')
        self.assertEqual(stopped['run']['state'], 'stopped')
        self.assertEqual(stopped['run']['step'], 0)
        self.assertIsNone(stopped['run']['phase'])
        self.assertTrue(stopped['agv']['stationConfirmed'])
        self.assertEqual(stopped['tanks'][0]['timer'], 0)
        self.assertFalse(stopped['tanks'][0]['timerDone'])
        self.now = 100
        self.assertEqual(self.line.snapshot()['run']['state'], 'stopped')
        restarted = self.command('start')
        self.assertEqual(restarted['run']['step'], 0)
        self.assertEqual(restarted['run']['phase'], 'dwell')
        self.assertEqual(restarted['tanks'][0]['timer'], 120)

    def test_stop_in_transit_preserves_last_confirmed_station(self):
        self.prepare_automatic()
        self.command('start')
        self.now = 2.25
        self.assertEqual(self.line.snapshot()['agv']['destination'], 2)
        stopped = self.command('stop')
        self.assertEqual(stopped['run']['step'], 1)
        self.assertEqual(stopped['run']['state'], 'stopped')
        self.assertEqual(stopped['agv'], {
            'station': 1, 'stationConfirmed': False, 'destination': None, 'moving': False,
        })
        self.now = 80
        self.assertEqual(self.line.snapshot()['agv'], stopped['agv'])
        restarted = self.command('start')
        self.assertEqual(restarted['run']['step'], 0)
        self.assertEqual(restarted['run']['phase'], 'travel')
        self.assertEqual(restarted['agv']['destination'], 1)
        self.now = 81
        self.assertTrue(self.line.snapshot()['agv']['stationConfirmed'])

    def test_recipe_description_listing_and_delete(self):
        steps = [{'tank': 2, 'temperature': None, 'dwell': 10, 'plcTime': 10}]
        saved = self.command('save-recipe', name='Rinse only', part='Bracket batch', steps=steps)['recipe']
        self.assertEqual(saved['part'], 'Bracket batch')
        library = self.line.recipe_library()
        self.assertEqual([recipe['name'] for recipe in library['recipes']], ['Line test', 'Rinse only'])
        self.assertEqual(library['selected'], saved['id'])
        self.assertEqual(library['recipes'][1]['steps'], steps)

        with self.assertRaisesRegex(ValueError, 'changed'):
            self.command('delete-recipe', id=saved['id'], revision=0)
        status = self.command('delete-recipe', id=saved['id'], revision=saved['revision'])
        self.assertEqual(status['recipe']['id'], 'line-test')
        self.assertEqual([recipe['id'] for recipe in status['recipes']], ['line-test'])
        with self.assertRaisesRegex(ValueError, 'at least one'):
            self.command('delete-recipe', id='line-test', revision=1)

    def test_tank_setup_renames_hides_and_limits_settings(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'tanks.json'
            self.line = OperatorLine(clock=lambda: self.now, tank_path=path)
            setup = {'name': 'Hot rinse', 'fields': {
                'time': {'editable': True, 'min': 0, 'max': 60},
                'temperature': {'editable': False, 'min': 20, 'max': 40},
            }}
            status = self.command('tank-setup', tank=16, setup=setup)
            tank = status['tanks'][15]
            self.assertEqual(tank['name'], 'Hot rinse')
            self.assertEqual([(f['key'], f['editable'], f['min'], f['max'], f['plcMax']) for f in tank['fields']],
                             [('time', True, 0, 60, 9999), ('temperature', False, 20, 40, 999.9)])

            self.command('mode', mode='manual')
            with self.assertRaisesRegex(ValueError, 'not available in Manual'):
                self.command('settings', tank=16, values={'temperature': 30})
            with self.assertRaisesRegex(ValueError, '0–60 s'):
                self.command('settings', tank=16, values={'time': 61})
            self.command('settings', tank=16, values={'time': 10})
            with self.assertRaisesRegex(ValueError, 'allows a PLC time of 0–60 s'):
                self.command('save-recipe', name='Long', steps=[{'tank': 16, 'temperature': 30, 'dwell': 10, 'plcTime': 90}])

            with self.assertRaisesRegex(ValueError, 'every setting'):
                self.command('tank-setup', tank=16, setup={'name': 'X', 'fields': {}})
            with self.assertRaisesRegex(ValueError, 'not be above'):
                self.command('tank-setup', tank=16, setup={**setup, 'fields': {**setup['fields'], 'time': {'editable': True, 'min': 9, 'max': 1}}})

            self.line = OperatorLine(clock=lambda: self.now, tank_path=path)
            self.assertEqual(self.line.snapshot()['tanks'][15]['name'], 'Hot rinse')
            self.assertEqual(self.command('tank-setup-default', tank=16)['tanks'][15]['name'], 'DI rinse')
