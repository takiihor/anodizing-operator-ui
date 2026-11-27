# Anodizing Operator UI

An operator console for a 16-tank anodizing line. Save and reuse tank
sequences, follow the active and next steps on the map, and select any tank
to view its status or use its manual controls.

The console starts in simulation unless it has a deployment profile. PLC
monitor mode displays live tank readings. The **AGV jog** page shows the
IPC gateway and the console's standalone AGV module.
[Design and live integration notes](ANODIZING_OPERATOR_UI.md)

## Start the console

Run this command from the repository root on Linux.

```sh
./start.sh
```

The launcher opens the console when run from a Linux desktop. Use the console
from the same computer. [Open the console on port 8871](http://127.0.0.1:8871/)

On port 8871, the launcher loads
`~/.config/anodizing-operator-ui/monitor.env` if present. Use one `NAME=value`
setting per line. Exported environment variables take precedence. Use
`ANODIZING_PLC_MODE=monitor` for live readings or `door-pilot` for the
supervised Tank 1 door test. Keep the PLC and IPC keys in private files.

The start script builds the frontend when needed. It requires Python 3.10+,
Node.js 22.12+, npm, Bash, `curl`, `flock`, and `ss`.

- Stop the console with `./kill.sh`.
- To use another port, pass it to both scripts, such as `./start.sh 8872` and
  `./kill.sh 8872`.
- Logs are in `.runtime/operator-PORT.log`. Saved recipes, the selected recipe,
  and tank setup are stored per port in `.runtime/` and survive restarts.

## Operator pages

| Page | What you can do |
| --- | --- |
| **Run** | Select a recipe, start or stop it, and track visit order, the current tank, and the next tank. |
| **Manual** | Select a tank to start or stop its cycle, use its equipment controls, edit PLC settings, or send the simulated AGV to it. |
| **Recipes** | Create, edit, duplicate, or delete saved sequences. Click tanks in visit order and enter process times and setpoints. A sequence can visit a tank more than once. |
| **Settings** | Rename tanks, choose which setpoints operators can edit, configure their limits, or simulate stale and unavailable PLC readings. |
| **AGV jog** | Connect, check vehicle feedback, and use Forward, Reverse, Rotate, and Stop with a motion-capable link. |

The **Tank controls** section follows the photographed pushbuttons for each tank.
**Door signals** in Manual shows the opened and closed limits and both command
bits for tanks 1, 3, 5, 7, 10, 13, and 14. The door on physical tank #13 uses the
supplier's `Tank12_Door…` tags, as confirmed on the line.

## Test from the console

1. In **Manual**, click **Check PLC communication** to fetch a fresh catalog
   and PLC reading. Compare the displayed tank status with the line.
2. For the supervised Tank 1 pilot, follow the on-site procedure, select
   **Tank 1**, and use the available **Open door** or **Close door** action.
3. In **AGV status** or **AGV jog**, click **Check IPC communication** and review
   **IPC to AGV** for vehicle feedback. **Test move to Tank #3 (simulation)**
   runs an isolated station visit from **AGV status** or **Manual → Tank #3**.

## Try the built-in recipe

1. In **Settings → Simulation**, reset the simulator if needed, then clear
   **Tank #1 low water alarm**.
2. In **Manual**, select tank #16, set **Time** to **10 s**, and press
   **Apply settings**.
3. In **Run**, select **Line test** and press **Start recipe**. The simulated
   process timers run at 60× speed. The final visit returns from tank #16 to
   tank #13.

Use **Stop recipe** to end a run. Starting it again begins at step 1.

## Jog the AGV

In **AGV jog**, choose **Connect simulator**. Once stopped feedback arrives, arm
the AGV to enable Forward, Reverse, and either 180° rotation. Use **Stop AGV**
and check for stopped feedback. The module is in `backend/agv_module/`.

For a supervised framed serial link, install `pyserial` on the computer with
the adapter, then set `ANODIZING_AGV_MODE=serial`, `ANODIZING_AGV_DEVICE` to the
reviewed device path, and `ANODIZING_AGV_ADDRESS` and `ANODIZING_AGV_CHANNEL`
to the controller's two-byte address and one-byte channel in hex. The driver
uses 9600 baud, 8N1. Use **Connect AGV** from the jog page. Bare wired RS-485
uses `ANODIZING_AGV_MODE=wired` with the device path and exposes status reads.

When the adapter is on a separate AGV IPC, run the AGV API from this checkout
on the IPC with the appropriate serial environment.

```sh
python3 -m backend.server --agv-api-only --port 8870
```

Forward the IPC's loopback port to the operator PC with an SSH tunnel, then run
the operator console with `ANODIZING_AGV_MODE=ipc`. The forwarded local port
defaults to 8870 and can be set with `ANODIZING_AGV_CONTROLLER_PORT`.
The earlier commissioning API remains selectable with `ANODIZING_AGV_MODE=legacy`.
Recipe execution and the tank map still use simulated station moves.

## Connect the IPC Job Gateway

The operator-side integration is in [`backend/job_gateway/`](backend/job_gateway/).
Set the following in the operator backend's environment using the IPC origin,
certificate, and module key issued for the `read` scope.

| Variable | Value |
| --- | --- |
| `ANODIZING_JOB_GATEWAY_URL` | IPC HTTPS origin, such as `https://192.168.1.251:18875` |
| `ANODIZING_JOB_GATEWAY_CERT_FILE` | Path to the issued IPC certificate |
| `ANODIZING_JOB_GATEWAY_KEY_FILE` | Path to the module bearer key file outside the repository |

Use `ANODIZING_AGV_MODE=ipc-gateway` in the deployment profile to show live AGV
feedback in the header and **AGV status** page. IPC stations 2 and 3 map to
physical tanks 1 and 2 after confirmed arrival. The map animates estimated
travel between known tank positions and confirms the destination on arrival.
Other stations retain their IPC labels until their mappings are verified.
**AGV status** shows battery, speeds, station feedback, motion, sensors, faults,
and the decoded frame details. **Go to Tank** is visible in Manual. Live moves
are disabled in IPC status mode.
The **IPC Job Gateway** card reads `/v1/health`, `/v1/status`, and `/v1/jobs`.
[IPC field results and API contract](PLC_AGV_COMMUNICATION_TEST.md#ipc-module-gateway-api--192168125118875)

### Test a station visit with the mock Gateway

Start a separate console with the built-in loopback mock. It uses simulated
PLC and AGV links and a test-only mapping from Tank #3 to AGV station 3.

```sh
ANODIZING_PLC_MODE=simulation ANODIZING_AGV_MODE=simulator \
  ANODIZING_JOB_GATEWAY_MODE=mock ./start.sh 8874
```

Open [Manual on port 8874](http://127.0.0.1:8874/manual), select **Tank #3**,
and press **Move AGV to tank #3**. The backend claims mock control, keeps its
lease alive, and submits one `station_visit` after the mock reports armed.
Follow the job ID and status in the tank panel. **Arrived and stopped** appears
when the mock confirms the target station and stopped feedback. Restart the
mock console to repeat a completed demonstration.

## Monitor the PLC

The production PLC module is in [`backend/plc_module/`](backend/plc_module/).
Set `PLC_API_BASE_URL` to the supplier API origin. Set `PLC_API_KEY_FILE` to
the path of a private key file outside the repository, or use `PLC_API_KEY` if
your deployment already supplies the key through the environment. Start a
separate monitor instance if the simulator is already running.

```sh
ANODIZING_PLC_MODE=monitor ./start.sh 8872
```

[Open the tank monitor on port 8872](http://127.0.0.1:8872/manual). It shows PLC
timers, setpoints, alarms, equipment status, and door positions. Monitor mode
is read-only for the PLC. The last good readings remain visible when data goes
stale. Recipes and tank setup can still be edited locally.

Set `PLC_TIMESTAMP_TIMEZONE` if the supplier sends timestamps without an
offset. Use the supplier's confirmed timezone, such as `Asia/Hong_Kong`.
`PLC_STALE_AFTER_MS` defaults to 3000. Set `PLC_TIMESTAMP_OFFSET_SECONDS` only
if a measured clock correction is needed.

## Pilot the tank #1 door

With `ANODIZING_PLC_MODE=door-pilot` in the port 8871 deployment profile,
use **Manual → Tank 1**. To run the supervised pilot separately, use the
same PLC environment settings and another port.

```sh
ANODIZING_PLC_MODE=door-pilot ./start.sh 8873
```

Click **Check PLC communication** before requesting door movement. Confirm the
area is clear and the door can be observed before making the request. Once the PLC
reports the requested limit and the operator has seen the door stop, use
**Clear PLC command**. The pilot waits for fresh confirmation that both command
bits are clear before allowing the next movement.

The pilot checks the commissioned tag mapping and PLC Remote mode immediately
before each write. It records pending writes locally so a restart can resume
feedback monitoring. If an outcome is uncertain, inspect the door and PLC at
the line. The machine's safety interlocks and emergency controls remain in charge.

The pilot enables door writes for tank #1 only. Other PLC functions continue to
show readback. Simulation and monitor modes can still be started separately.

## Development checks

```sh
python3 -m unittest discover -s tests
npm --prefix frontend ci
npm --prefix frontend run build
```
