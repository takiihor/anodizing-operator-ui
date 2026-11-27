"""Map supplier tags to the operator's physical tank numbers."""

# The production test confirmed that the Tank12 API tags operate physical tank 13.
DOOR_SIGNALS = {
    1: (1, 'D1000.4', 'D1000.5'),
    3: (3, 'D1010.4', 'D1010.5'),
    5: (5, 'D1020.4', 'D1020.5'),
    7: (7, 'D1028.4', 'D1028.5'),
    10: (10, 'D1102.4', 'D1102.5'),
    13: (12, 'D1116.4', 'D1116.5'),
    14: (14, 'D1126.4', 'D1126.5'),
}
EQUIPMENT_STATUS = {
    'filter': 'FilterStatus', 'heater': 'HeaterStatus', 'ultrasonic': 'USStatus',
    'swing': 'SwingStatus', 'rectifier': 'RectStatus', 'pressure': 'SprayStatus',
}
TANK_ALARMS = {
    1: {21: 'filter pump overload', 30: 'heater alarm', 51: 'low water level',
        52: 'buffer level high', 53: 'buffer level low'},
    2: {5: 'emergency stop'},
    3: {22: 'filter pump overload', 31: 'heater alarm', 54: 'low water level',
        55: 'buffer level high', 56: 'buffer level low'},
    5: {23: 'filter pump overload', 24: 'swing overload', 32: 'heater alarm',
        57: 'low water level', 58: 'buffer level high', 59: 'buffer level low'},
    7: {25: 'filter pump overload', 33: 'heater alarm', 60: 'low water level',
        61: 'buffer level high', 62: 'buffer level low'},
    9: {6: 'emergency stop'},
    10: {26: 'filter pump overload', 34: 'heater alarm', 63: 'low water level',
         64: 'buffer level high', 65: 'buffer level low'},
    11: {27: 'filter pump overload', 66: 'low water level'},
    12: {4: 'emergency stop'},
    13: {35: 'heater alarm', 67: 'high water level', 68: 'low water level'},
    14: {28: 'filter pump overload', 36: 'heater alarm', 69: 'low water level',
         70: 'buffer level high', 71: 'buffer level low'},
    16: {29: 'filter pump overload', 37: 'heater alarm', 72: 'low water level',
         73: 'buffer level high', 74: 'buffer level low'},
}


def door_position(sample, tank):
    signals = DOOR_SIGNALS.get(tank)
    if signals is None:
        return None
    tag_tank, open_address, closed_address = signals
    tag = f'Tank{tag_tank:02}_Door'
    opened = sample.value(f'{tag}Opened', kind='Bool', address=open_address)
    closed = sample.value(f'{tag}Closed', kind='Bool', address=closed_address)
    if opened is None or closed is None:
        return 'Unavailable'
    return {
        (True, False): 'Open',
        (False, True): 'Closed',
        (False, False): 'Between limits',
        (True, True): 'Conflicting feedback',
    }[opened, closed]


def tank_status(tank, sample):
    tank_id = tank['id']
    prefix = f'Tank{tank_id:02}_'
    basket = sample.value(prefix + 'HaveBasket', kind='Bool')
    position = door_position(sample, tank_id)
    details = [{'label': 'Basket', 'value': {True: 'Present', False: 'Empty', None: 'Unavailable'}[basket]}]
    numeric_details = {
        7: (('Measured current', 'AmperePV', 'A'), ('Measured voltage', 'VoltagePV', 'V')),
        13: (('Conductivity', 'Conductivity', 'µS/cm'),),
        16: (('Conductivity', 'Conductivity', 'MΩ·cm'),),
    }
    for label, suffix, unit in numeric_details.get(tank_id, ()):
        value = sample.value(prefix + suffix, kind='number')
        details.append({'label': label, 'value': f'{value:g} {unit}' if value is not None else 'Unavailable'})
    alarms = [label for bit, label in TANK_ALARMS.get(tank_id, {}).items()
              if sample.value(f'Alarm{bit:02}', kind='Bool')]
    return {
        **tank,
        'temperature': sample.value(prefix + 'TempPV', kind='number'),
        'timer': sample.value(prefix + 'TimePV', kind='number'),
        'timerDone': sample.value(prefix + 'TimeUp', kind='Bool'),
        'doorPosition': position,
        'details': details,
        'alarm': f"Tank #{tank_id} {', '.join(alarms)}" if alarms else None,
        'fields': [
            {**field, 'value': sample.value(field['tag'], kind='number', permission='W')}
            for field in tank['fields']
        ],
        'controls': [
            {**control, 'active': sample.value(prefix + EQUIPMENT_STATUS[control['id']], kind='Bool')}
            for control in tank['controls'] if control['id'] in EQUIPMENT_STATUS
        ],
    }


def monitoring_status(status, sample):
    return {
        **status,
        'mode': None,
        'simulation': False,
        'scenario': None,
        'plc': {
            'quality': sample.quality,
            'sampleTime': sample.timestamp.isoformat() if sample.timestamp is not None else None,
            'remote': sample.remote if sample.quality == 'live' else None,
        },
        'agv': {'station': None, 'stationConfirmed': False, 'destination': None, 'moving': False},
        'run': {**status['run'], 'nextTank': None, 'blockers': []},
        'tanks': [tank_status(tank, sample) for tank in status['tanks']],
    }
