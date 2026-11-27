import json
import unittest
from unittest.mock import patch

from backend.agv_client import AgvClient, ControllerUnavailable


TOKEN = 'a' * 43


def state(**changes):
    return {
        'connected': True, 'connection_id': 4, 'stop_generation': 8, 'sequence': 9,
        'armed': True, 'status_age_s': 0.1, 'stale': False, 'settings': {'mode': 'serial'},
        'status': {'mode': 0, 'stopped': True, 'actual_speed_m_h': 0, 'fault_code': 0, 'obstacle_stop': False},
        **changes,
    }


def json_reply(value, code=200):
    return code, json.dumps(value).encode()


def page_reply(token):
    return 200, f'<meta name="module-token" content="{token}">'.encode()


class AgvClientTest(unittest.TestCase):
    def setUp(self):
        self.client = AgvClient()
        self.client.token = TOKEN
        self.authority = self.client._view(state(), TOKEN)['authority']

    def test_motion_preserves_reviewed_link_and_stop_generation(self):
        with patch.object(self.client, '_http', side_effect=[json_reply(state()), json_reply(state())]) as http:
            self.client.command({'action': 'rotate_clockwise', 'authority': self.authority})
        self.assertEqual(http.call_args_list[-1].kwargs['body'], {
            'action': 'send', 'command': 'rotate_clockwise', 'connection_id': 4, 'stop_generation': 8,
        })
        self.assertEqual(http.call_count, 2)

    def test_motion_refuses_changed_authority_or_missing_interlocks(self):
        for changes in (
            {'armed': False}, {'status_age_s': 7}, {'stop_generation': 9},
            {'recipe_active': True}, {'settings': {'mode': 'wired'}},
        ):
            with self.subTest(changes=changes), patch.object(
                self.client, '_http', return_value=json_reply(state(**changes))
            ) as http:
                with self.assertRaises(ValueError):
                    self.client.command({'action': 'forward', 'authority': self.authority})
                http.assert_called_once_with('/api/agv?cached=1', token=TOKEN)

    def test_stop_bypasses_the_status_read_and_requires_stopped_feedback(self):
        ack = {'tx_id': 10, 'state': 'acknowledged', 'acknowledged_at': 'now', 'requested_command': 'stop'}
        with patch.object(self.client, '_http', return_value=json_reply(
            state(stale=True, stop_generation=9, results={'pause': ack})
        )) as http:
            stopped = self.client.command({'action': 'stop', 'authority': self.authority})
        http.assert_called_once()
        self.assertEqual(http.call_args.args, ('/api/agv',))
        self.assertEqual(http.call_args.kwargs['body']['command'], 'stop')
        self.assertFalse(stopped['results']['stop']['confirmedStopped'])

    def test_renewal_retries_a_status_read_but_never_replays_a_command(self):
        restarted = 'b' * 43
        refused = json_reply({'error': 'Unauthorized'}, 403)
        with patch.object(self.client, '_http', side_effect=[refused, page_reply(restarted), json_reply(state())]) as http:
            updated = self.client.status()
        self.assertEqual(http.call_count, 3)
        self.assertNotEqual(updated['authority'], self.authority)
        self.assertNotIn(restarted, json.dumps(updated))

        with patch.object(self.client, '_http', return_value=refused) as http:
            with self.assertRaisesRegex(ValueError, 'session changed'):
                self.client.command({'action': 'stop', 'authority': updated['authority']})
            http.assert_called_once()

    def test_transport_failure_never_replays_rotation(self):
        with patch.object(self.client, '_http', side_effect=[
            json_reply(state()), ControllerUnavailable('Outcome unknown.')
        ]) as http:
            with self.assertRaisesRegex(ControllerUnavailable, 'Outcome unknown'):
                self.client.command({'action': 'rotate_counterclockwise', 'authority': self.authority})
        self.assertEqual(http.call_count, 2)
