# Anodizing Operator UI

An operator console for a 16-tank anodizing line. Save and reuse tank
sequences, follow the active and next steps on the map, and select any tank
to view its status or use its manual controls.

Tank PLC readings, recipe execution, and station-to-station AGV moves run in
simulation. The separate **AGV jog** page uses a compatible local robot
controller when one is available. [Design and live integration notes](ANODIZING_OPERATOR_UI.md)

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
| **AGV jog** | Check the separate AGV controller and use its jog controls. The controller defaults to loopback port 8870. |

The **Tank controls** section follows the photographed pushbuttons for each tank.
The simulator gives tank #13 a pneumatic-door control. Verify the door
mapping for tanks #12 and #13 against the installed PLC before live integration.

## Try the built-in recipe

1. In **Settings → Simulation**, reset the simulator if needed, then clear
   **Tank #1 low water alarm**.
2. In **Manual**, select tank #16, set **Time** to **10 s**, and press
   **Apply settings**.
3. In **Run**, select **Line test** and press **Start recipe**. The simulated
   process timers run at 60× speed. The final visit returns from tank #16 to
   tank #13.

Use **Stop recipe** to end a run. Starting it again begins at step 1.

## Development checks

```sh
python3 -m unittest discover -s tests
npm --prefix frontend ci
npm --prefix frontend run build
```
