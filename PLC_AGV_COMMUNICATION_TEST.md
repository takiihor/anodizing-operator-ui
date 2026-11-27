# PLC and AGV communication test

Verify the communication paths from the operator console to the tank PLC and
AGV. Test the live links in read-only mode. Report simulator results separately.

## Test boundaries

- Keep the PLC in its current Local or Remote mode. Use the console's
  `monitor` mode and leave the door pilot off. Use only PLC catalog and snapshot
  reads. Leave door commands, cycle controls, and setpoints untouched.
- Keep the AGV disarmed and stationary. Use **Connect AGV** only for the
  reviewed link when it is disconnected. **Query status** is the only AGV
  command to send. Leave Arm, Stop, Forward, Reverse, Rotate, station calls,
  and recipes untouched. If the AGV reports movement or an active operation,
  record the observation and end the communication test.
- Use the configured supplier API and the reviewed AGV serial device or IPC
  tunnel. If an endpoint, credential, or serial configuration is missing,
  report the blocker. Keep keys and tokens out of output, screenshots, and logs.
- Reuse services already running in the right mode. Use a free loopback port
  for a separate test console and avoid restarting services in use.

## Set up the test console

Use the existing supplier API configuration for `PLC_API_BASE_URL` and
`PLC_API_KEY`. Set `PLC_TIMESTAMP_TIMEZONE` if the supplier uses timestamps
without a timezone. Use the documented clock correction only if it has been
measured for this PLC.

For the AGV, use the mode that matches the deployment.

| Link | Configuration |
| --- | --- |
| Bare RS-485 on this computer | `ANODIZING_AGV_MODE=wired` and the reviewed `ANODIZING_AGV_DEVICE` |
| Framed serial on this computer | `ANODIZING_AGV_MODE=serial`, `ANODIZING_AGV_DEVICE`, and the reviewed `ANODIZING_AGV_ADDRESS` and `ANODIZING_AGV_CHANNEL` |
| Adapter on a separate IPC | Run the AGV API on the IPC with its reviewed local link settings. Use `ANODIZING_AGV_MODE=ipc` on the operator PC with the configured loopback tunnel and `ANODIZING_AGV_CONTROLLER_PORT` if it differs from 8870. |

Start a test console with the reviewed environment already set. The example
uses port 8872. [PLC and IPC setup](README.md)

```sh
ANODIZING_PLC_MODE=monitor ./start.sh 8872
```

## 1. PLC communication

1. Open **Manual** at `http://127.0.0.1:8872/manual`. Confirm the header says
   **PLC monitor** and that the line API reports `simulation: false`.
2. Read `GET /api/line/status` more than once. Confirm `plc.quality` is
   `live` and `plc.sampleTime` advances. Record the displayed PLC mode and
   at least one available tank reading, such as a PLC timer or actual
   temperature.
3. Compare that reading with the selected tank panel. Door position and
   setpoints may be observed as readback.
4. Record **Pass** when two distinct fresh supplier timestamps reach the console
   and the tank readback is available. A Local-mode snapshot can pass. If the
   sample is stale or unavailable, report its last good timestamp and any
   available connection, authentication, or clock error.

## 2. AGV communication

1. Open **AGV jog** at `http://127.0.0.1:8872/agv`. Confirm the configured
   transport is the intended framed serial or wired link. If using a separate
   IPC, check its forwarded `/api/agv/status` as well as the operator
   console's status.
2. If the reviewed link is disconnected and available for this test, select
   **Connect AGV**. Verify the status reports `armed: false`.
3. Select **Query status** once on the disarmed link. Opening the port only
   verifies the local adapter. Wait for a vehicle reply. Read
   `GET /api/agv/status` and check the latest `results.query_status` for
   `status received`, together with `connected: true`, `fresh: true`, and
   a real transport (`wired` or `serial`).
4. Record **Pass** when a fresh vehicle status reaches the console. Use the
   query reply or a fresh report arriving during observation as evidence. The
   API's `sequence` counter alone also advances on HTTP reads. Capture the
   transport, status age, reported motion, and any fault separately. A vehicle
   fault can coexist with working communication.
5. If the console can reach the IPC but vehicle feedback is absent, mark the
   IPC API separately from the vehicle link. Record the failing stage, such as
   tunnel, port open, reply timeout, or stale feedback.

## Optional status evidence from the terminal

Run from the operator PC. The following helper makes GET requests only and
keeps the console token in memory. Use the test console's port as the argument.

```sh
python3 - 8872 <<'PY'
import json
import sys
import time
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


base = f'http://127.0.0.1:{int(sys.argv[1])}'
opener = build_opener(ProxyHandler({}), NoRedirect())


def read(path, token=None):
    headers = {'X-Operator-Token': token} if token else {}
    with opener.open(Request(base + path, headers=headers), timeout=4) as response:
        return json.load(response)


try:
    token = read('/api/line/token')['token']
except URLError as error:
    raise SystemExit(f'Console session unavailable: {error}') from error

for poll in range(3):
    for device, path in (('PLC', '/api/line/status'), ('AGV', '/api/agv/status')):
        try:
            state = read(path, token)
        except URLError as error:
            print(device, 'status unavailable:', error)
            continue
        if device == 'PLC':
            evidence = {'simulation': state['simulation'], **state['plc']}
        else:
            evidence = {key: state.get(key) for key in
                        ('linkMode', 'transport', 'connected', 'armed', 'fresh',
                         'statusAge', 'motion', 'fault')}
            evidence['query'] = state.get('results', {}).get('query_status', {}).get('state')
        print(device, json.dumps(evidence))
    if poll < 2:
        time.sleep(1.5)
PY
```

## Report

For each link, record the test time, console mode, live or simulated link,
**Pass**, **Fail**, or **Blocked**, and the evidence. Where applicable,
distinguish **operator console → IPC** from **IPC → AGV**. Include the PLC
sample times and AGV reply evidence. List any status queries sent and confirm
that the test sent zero PLC writes and zero AGV motion commands.
