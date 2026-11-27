"""Expose the extracted link through the operator console's jog API."""

from threading import Lock
from types import SimpleNamespace
from uuid import uuid4

from ..agv_client import ACTIONS, MOTION, status_view
from .driver import Connection, hex_input


class AgvController:
    setup_url = None

    def __init__(self, mode='simulator', *, port=None, address=None, channel=None):
        if mode not in ('simulator', 'serial', 'wired'):
            raise ValueError('Choose simulator, serial, or wired for the local AGV link.')
        if mode != 'simulator' and not port:
            raise ValueError('Set ANODIZING_AGV_DEVICE to the reviewed serial device.')
        profile = {'wired_port': port} if mode == 'wired' else {}
        if mode == 'serial' or (mode == 'wired' and (address or channel)):
            hex_input(address, 2, 'Address')
            hex_input(channel, 1, 'Channel')
            profile.update(address=address, channel=channel)
        self.mode = mode
        self.port = port
        self.controller = uuid4().hex
        self.owner = SimpleNamespace(lock=Lock(), closing=False, active=None, storage_error=None,
                                     execution_uncertain=False)
        self.link = Connection(self.owner, profile)

    def _view(self, state):
        view = status_view(state, self.controller)
        view['linkMode'] = self.mode
        view['canConnect'] = not view['connected'] and not self.owner.closing
        view['canDisconnect'] = view['connected'] and not view['haltPending'] and (
            self.mode != 'serial' or (view['motion'] == 'stopped' and not view['armed']))
        if not view['connected']:
            view['motionBlock'] = 'Connect the configured AGV link.'
        return view

    def status(self):
        return self._view(self.link.commissioning())

    def command(self, body):
        action = body.get('action')
        if not isinstance(action, str) or action not in ACTIONS | {'connect', 'disconnect'}:
            raise ValueError('Choose a supported AGV command.')
        authority = body.get('authority')
        if (not isinstance(authority, dict) or authority.get('controller') != self.controller
                or type(authority.get('connectionId')) is not int or type(authority.get('stopGeneration')) is not int):
            raise ValueError('Review the current AGV link before sending a command.')
        requested = {'connection_id': authority['connectionId'], 'stop_generation': authority['stopGeneration']}
        if action == 'stop':
            return self._view(self.link.submit({**requested, 'action': 'send', 'command': 'stop'}))

        state = self.status()
        if state['authority'] != authority:
            raise ValueError('The AGV link or Stop state changed. Review the current status.')
        if action == 'connect':
            requested.update(action='connect', mode=self.mode, port=self.port)
        elif action == 'disconnect':
            requested['action'] = 'disconnect'
        elif action in ('arm', 'disarm'):
            if action == 'arm':
                if state['motionBlock'] or state['motion'] != 'stopped':
                    raise ValueError(state['motionBlock'] or 'Arm requires fresh stopped feedback.')
                if self.mode == 'serial' and body.get('confirmed') is not True:
                    raise ValueError('Confirm that the AGV travel area and robot are clear before arming.')
            requested.update(action='arm', armed=action == 'arm')
        else:
            if action in MOTION and (state['motionBlock'] or not state['armed']):
                raise ValueError(state['motionBlock'] or 'Arm the AGV before requesting motion.')
            requested.update(action='send', command=action)
        return self._view(self.link.submit(requested))

    def close(self):
        with self.owner.lock:
            self.owner.closing = True
        self.link.close()
