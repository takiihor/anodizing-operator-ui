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

## AGV readback captured 2026-11-27T04:11:02Z

Result: **Pass**. The operator console at `http://127.0.0.1:8872/agv` was
connected through the SSH-forwarded AGV API on the cabinet IPC, using
`ANODIZING_AGV_MODE=wired` on the reviewed `/dev/ttyS2` port. The AGV stayed
disarmed and stopped throughout. To list every decoded value, the temporary IPC
adapter exposed its raw status report; that patch was only in the adapter copy
under `/tmp` on the IPC, not in this repository.

### Raw AGV status report — all fields present

| Field | Value |
| --- | --- |
| `header` | `A5 5A` |
| `length` | `29` |
| `command` | `25` = status reply |
| `checksum` | `AE` |
| `vehicle_id` | `1` |
| `battery_percent` | `50` |
| `set_speed_m_h` | `858` |
| `stopped` | `true` |
| `obstacle_stop` | `false` |
| `station_arrival` | `true` |
| `command_stop` | `true` |
| `actual_speed_m_h` | `0` |
| `current_station` | `1` |
| `target_station` | `1` |
| `fault_code` | `0` |
| `fault_description` | `No fault` |
| `max_stations` | `10` |
| `mode` | `2` = station-call mode |
| `customer_sensors` | `0` |
| `position_units` | `0` |
| `paused` | `false` |
| `leading_end` | `0` |
| `obstacle_input` | `204` (`0xCC`) |
| `roller_center_blocked` | `true` |
| `roller_left_blocked` | `true` |
| `roller_right_blocked` | `true` |
| `lift_state` | `0` |

`obstacle_input = 0xCC` makes the driver report all three `roller_*_blocked`
flags as `true`. That is the decoded value, but it coexists with
`obstacle_stop: false` and `fault_code: 0`, so treat it as the raw
sensor/sentinel value rather than a confirmed physical obstruction.

Fields the driver can decode but that were **not present in this 29-byte
reply**: `attachment_active`, `roller_speed`. The driver also has an internal
`read_speed` query that the operator UI does not expose as a control.

### UI-derived link state

| Field | Value |
| --- | --- |
| `transport` | `wired` |
| `connected` | `true` |
| `armed` | `false` |
| `fresh` | `true` |
| `motion` | `stopped` |
| `speed` | `0` |
| `fault` | `0` |
| `statusAge` | ~2.5 s |
| `motionBlock` | `Forward, Reverse and Rotate require a direct serial or simulator connection.` |
| `canQuery` | `true` |
| `canStop` | `false` |
| `canDisconnect` | `true` |
| `haltPending` | `false` |
| `error` | `null` |
| `linkMode` | `wired` |
| `sequence` | `8` |
| `results.query_status` | background read pending; no error |

### Commands and exclusions

- Console commands used for the AGV: **Connect AGV** once after restarting the
  temporary IPC adapter, and **Query status** once. The adapter’s periodic
  background `query_status` reads are read-only.
- Arm, Stop, Forward, Reverse, Rotate, station call, and recipe commands: **0**.
- PLC reads/writes: **0**. The PLC was not part of this readback.
- Temporary login helper and copied backend archive were removed after the
  readback. No credentials or tokens were written to this file.

## IPC module gateway API — 192.168.1.251:18875

This is a separate HTTPS JSON API on the IPC, not the operator UI and not the
loopback executor API used for the earlier AGV readback.

The gateway contract below is reference material. For this communication test,
use the read-only requests and keep control operations out of the test.

### Connectivity evidence

- TLS validates against `/home/taki/anodizing-module/ipc-cert.pem`.
- Unauthenticated `GET /` returns `403 {"error": "Request is not authorized."}`.
- Authenticated, read-only requests returned **200**:

| Path | Result |
| --- | --- |
| `GET /v1/health` | `build: f095de0c135d5529c8e1d4366ef7ca54f1d8fed0`, `gateway_enabled: true`, `protocol: 2`, `simulated: false`, `motion_enabled: false` |
| `GET /v1/status` | `owner: null`, `armed: false`, `active_job_id: null`, `recovery_required: false` |
| `GET /v1/jobs` | `{"jobs": []}` |

The key was read from `/home/taki/anodizing-module/module.key` into the
Authorization header and was not printed. No claim, heartbeat, arm, submit,
raw, or motion request was sent.

### Transport and authentication

- Base URL: `https://192.168.1.251:18875/v1/`
- Verify TLS with `--cacert /home/taki/anodizing-module/ipc-cert.pem`.
- Send exactly one `Authorization: Bearer <module.key>` header.
- `GET` is for reads. `POST` requires `Content-Type: application/json`, one
  bounded JSON object, and one `Content-Length`.
- Requests are accepted from the configured line LAN, including Katana and
  the IPC itself.

Avoid `-v`, shell tracing, or logs that would expose the key.

### Endpoints

| Method | Path | Body | Purpose |
| --- | --- | --- | --- |
| GET | `/v1/health` | — | Build, boot ID, gateway flags, heartbeat/lease settings |
| GET | `/v1/status` | — | Owner, armed state, Stop generation, device link |
| GET | `/v1/jobs` | — | Job history for this module |
| GET | `/v1/status?job_id=<id>` | — | One specific job status |
| POST | `/v1/claim` | `{}` | Take single ownership; returns `lease_id`, `boot`, `stop_generation`, `armed` |
| POST | `/v1/heartbeat` | `{"lease_id":"…","sequence":1}` | Keep the 10 s lease alive; send every ~1 s, incrementing `sequence` |
| POST | `/v1/release` | `{"lease_id":"…"}` | Give up ownership |
| POST | `/v1/submit` | see below | Submit a job |
| POST | `/v1/stop` | `{}` | Safety Stop path |
| POST | `/v1/raw` | `{"command":"…","args":{…}}` | Allowlisted raw AGV read/write |
| POST | `/v1/raw/undo` | `{"lease_id":"…","snapshot_id":"…"}` | Undo a raw speed setting |

Scopes on `taki-dev` are `read`, `jobs`, and `raw`:

- `read`: `health`, `status`, `jobs`.
- `jobs`: `claim`, `release`, `heartbeat`, `submit`.
- `raw`: `raw` and `raw/undo` for the duration of the temporary grant. The
  recorded expiry was around 19:50 on the test day. `read` and `jobs` stay valid afterward.
- `arm` is not a module API call. It is confirmed by the operator at the AGV
  page/e-stop and is valid for that module for 30 minutes.

### Read example

```sh
CERT=/home/taki/anodizing-module/ipc-cert.pem
KEY=$(cat /home/taki/anodizing-module/module.key)

curl -sS --cacert "$CERT" \
  -H "Authorization: Bearer $KEY" \
  https://192.168.1.251:18875/v1/status
```

### Control workflow reference

1. Read `health`, `status`, and `jobs`.
2. `POST /v1/claim` with `{}`. Save `lease_id`, `boot`, and
   `stop_generation`.
3. Send `POST /v1/heartbeat` every ~1 s with the saved `lease_id` and an
   increasing `sequence`. The module lease is 10 s. A missed heartbeat or
   raw-scope expiry makes the gateway Stop and drop ownership.
4. The operator Arms at the AGV page/e-stop. Check `/v1/status` for
   `owner: "taki-dev"`, `lease_valid: true`, `armed: true`,
   `armed_for: "taki-dev"`, `module_attestation_expired: false`, and a fresh
   `stop_generation`.
5. Submit a job:

```json
{
  "lease_id": "<from claim>",
  "stop_generation": <from claim/status>,
  "job_id": "<unique id>",
  "type": "station_visit",
  "version": 1,
  "params": { "station": 1 }
}
```

6. Poll `/v1/jobs` or `/v1/status?job_id=<id>` for the outcome.
7. Use `POST /v1/stop` only for a safety Stop.
8. `POST /v1/release` with the current `lease_id` when finished.

### Raw AGV access

While the `raw` scope is current:

```sh
curl -sS --cacert "$CERT" \
  -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  -d '{"command":"query_status","args":{}}' \
  https://192.168.1.251:18875/v1/raw
```

`query_status` and `read_speed` are reads. Writes require `lease_id`. The
allowlist includes `call_station`, `pause`, `stop`, and `set_speed`. Forward,
reverse, resume, rotations, attachment commands, auto-start, obstacle-sensing,
bumper, e-stop, and speed-limit weakening are blocked. Raw speed is capped at
500 m/h and sampled travel at 562 position units (~3 m). After the raw scope
expires, `read` and `jobs` continue to work but `raw` endpoints refuse with a
scope error.

### Administration

The grant can be revoked on the IPC with the administrator command:

```sh
anodizing-module-token ... revoke taki-dev
```

Raw access can be renewed by the IPC administrator, 8 hours by default and up
to 24 hours. Revocation, renewal, claim, arm, and job submission were **not**
performed while writing this note.

> Use the credentials issued for the IPC through the approved local credential store.

## System roles and access

### Operator UI — this repository

- Source: `/home/taki/development/anodizing-operator-ui`.
- Role: operator-facing console. It displays line/PLC status, tank panels, and
  the AGV jog page, and sends operator commands. It does **not** own the AGV
  serial port.
- Test instance used here: `http://127.0.0.1:8872/agv`, started with:

```sh
ANODIZING_PLC_MODE=simulation ANODIZING_AGV_MODE=ipc \
ANODIZING_AGV_CONTROLLER_PORT=8870 ./start.sh 8872
```

- The Vue frontend was built into `frontend/dist` with a temporary Node 22
  toolchain under `/tmp/opencode/node`; no system Node/npm was installed.
- The UI reached the AGV through a local SSH tunnel to the IPC, not by opening
  `/dev/ttyS2` locally.
- `http://operator.localhost:8880/operator/` is a different demo service owned
  by `icadmin`; it is not this repository and was not used for the AGV test.

### IPC — the cabinet computer

- IP: `192.168.1.251`; hostname: `interface`.
- SSH access from the operator PC:

```sh
ssh user@192.168.1.251
```

- The `user` account is in `dialout`, so it can access the reviewed wired AGV
  port.
- AGV wired port: `/dev/ttyS2`, configured in the site profile:
  `/home/user/.local/state/anodizing-ipc-2a67926/data/site-profile.json`
  (also mirrored at `/var/lib/anodizing-executor/data/site-profile.json`).
- Role: owns the physical RS-485 link, runs the executor/gateway, holds module
  keys and certificates, and is the single authority for ownership, heartbeat,
  Arm confirmation, Stop generation, job submission, and raw AGV allowlisting.
- Executor service: `anodizing-executor.service`.
- Code: `/opt/anodizing-executor/current` →
  `/opt/anodizing-executor/releases/f095de0c135d5529c8e1d4366ef7ca54f1d8fed0`,
  about 17 MB, containing `app/`, `operator-ui/`, `docs/`, `README.md`,
  `REVISIONS.txt`, and `SHA256SUMS`.
- Known listeners:

| Port | Role |
| --- | --- |
| `18873` | Loopback executor control API |
| `18874` | Loopback status display |
| `18875` | Line-LAN HTTPS module/job gateway |
| `18876` | Operator Stop API when configured |

- For the AGV readback, a temporary copy of this repository's backend was run
  on the IPC with `ANODIZING_AGV_MODE=wired ANODIZING_AGV_DEVICE=/dev/ttyS2`
  and exposed on IPC port `8870`, then forwarded to the operator PC:

```sh
ssh -L 8870:127.0.0.1:8870 user@192.168.1.251
```

  That was a test aid under `/tmp`, not a production service.

### PLC — the line controller

- Role: tank/door/temperature readings, interlocks, and recipe/cycle state.
- Real access is through the supplier PLC API configured with
  `PLC_API_BASE_URL`, `PLC_API_KEY`, and optionally
  `PLC_TIMESTAMP_TIMEZONE`. Those supplier credentials are not recorded here.
- In the AGV communication test the PLC was in **simulation**
  (`ANODIZING_PLC_MODE=simulation`). No PLC reads from the real controller and
  no PLC writes were performed.
- For a real read-only PLC check, use `ANODIZING_PLC_MODE=monitor`, confirm the
  header says **PLC monitor**, and use only catalog/snapshot reads. Leave door
  commands, cycle controls, setpoints, and recipe writes untouched.

### Connection map

```text
Operator UI (this repo), 127.0.0.1:8872
        |
        | local HTTP to 127.0.0.1:8870
        v
SSH tunnel local 8870 -> IPC 127.0.0.1:8870
        |
        | temporary AGV API on IPC, wired mode
        v
IPC /dev/ttyS2 (RS-485) -> AGV

Operator PC -> HTTPS 192.168.1.251:18875 (pinned cert + module key) -> IPC gateway
Operator PC -> SSH 192.168.1.251:22 -> IPC shell
Operator UI -> supplier PLC API (simulation in this test) -> PLC
```
