# Anodizing Operator UI

An operator console for a 16-tank anodizing line. Save and reuse tank
sequences, follow the active and next steps on the map, and select any tank
to view its status or use its manual controls.

The console starts in simulation. PLC monitor mode can display live tank
readings. The **AGV jog** page uses the console's standalone AGV module.
[Design and live integration notes](ANODIZING_OPERATOR_UI.md)

## Start the console

Run this command from the repository root on Linux.

```sh
./start.sh
```

Use the console from the same computer. [Open the console on port 8871](http://127.0.0.1:8871/)

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
The door on physical tank #13 uses the supplier's `Tank12_Door…` tags, as
confirmed on the line.

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

## Monitor the PLC

The production PLC module is in [`backend/plc_module/`](backend/plc_module/).
Set `PLC_API_BASE_URL` to the supplier API origin and `PLC_API_KEY` in the
operator backend's environment. Start a separate monitor instance if the
simulator is already running.

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

Use the same PLC environment settings and a separate port for the supervised
tank #1 pilot.

```sh
ANODIZING_PLC_MODE=door-pilot ./start.sh 8873
```

Open **Manual**, select **tank #1**, and confirm the area is clear and the door
can be observed before requesting **Open door** or **Close door**. Once the PLC
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
