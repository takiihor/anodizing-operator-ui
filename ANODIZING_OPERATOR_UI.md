# Anodizing line operator UI design

The operator uses one screen to see the AGV and tank status, run a recipe in **Automatic**, or adjust a tank and send the AGV to it in **Manual**.

## Development plan

### Architecture

- **Vue and TypeScript frontend.** Build the Operator page with Vue components for the U-shaped map, tank settings, and recipe progress. Share a typed status snapshot across the components and use TypeScript types for settings and command results. Model stale readings and confirmed AGV arrival explicitly. Keep editable fields configurable per tank so an individual setting can be removed without changing the form logic.
- **Vite build.** Build the frontend into static files served by a standalone Python service in this repository. The browser polls a JSON status endpoint and sends operator actions to the same service. Node is a build-time dependency. The service can run on the IPC alongside the existing robot tooling. [Existing robot IPC architecture](../ur10e-vision-calibration-full/START_HERE.md#split-between-the-pcs)
- **Python line control.** Validate commands on the IPC and keep one command owner for Manual and Automatic. The IPC tracks execution and reports command results to the browser.
- **PLC and AGV adapters.** The IPC reads tank data and verifies setting changes through the supplier PLC API. It maps tank numbers to station IDs and tracks moves through the AGV adapter. Simulated and live adapters expose the same operations to the line control service. [PLC API and validation](../plc-signal-test-ui/docs/architecture.md)

### Implementation order

1. Build the Vue page against simulated PLC and AGV data. Reproduce the U-shaped layout and display live, stale, and unavailable readings.
2. Add Manual settings and AGV moves, then the Automatic recipe workflow. Verify the line test with the tank #1 alarm, the #16 time mismatch, and the final AGV move from #16 to #13.
3. Connect the live adapters once the station mapping, controller feedback, and Automatic sequence ownership are confirmed. Check live status and settings readback before operating the full recipe.

## Operator screen

- **Top bar** shows the selected operating mode and separate connection status for the tank PLC and the AGV. Show the age of the last good PLC reading when data is stale.
- **Automatic run** shows **Start recipe**, **Stop recipe**, current progress, and any start blockers above the map. Keep the controls visible while scrolling during a run.
- **Line map** shows all 16 tanks in their physical positions, with the AGV guide track outside the two rows. Keep tanks that the recipe skips visible. Use distinct labels for the selected tank, the current AGV location, and the commanded destination.
- **Side panel** shows the selected tank’s status in either mode. In Manual it offers **Apply settings** and **Move AGV to tank #…** as separate actions. Selecting a tank opens its details.

### Physical layout

Use the orientation in Appendix 2, with ultrasonic cleaning at the lower right and the bend on the left. The sketch shows the station order and the guide track. Use the drawing to position and size the tank cards. [Anodizing line layout, Appendix 2](../../Downloads/ICDRC_Application_Anodizing.pdf)

```text
  ╭─────────────────────────────────────────────╴
  │       [10] [11] [12] [13] [14] [15] [16]
  │  [09]
  │  [08]
  │       [07] [06] [05] [04] [03] [02] [01]
  ╰─────────────────────────────────────────────╴
```

Show physical tank numbers and recipe visit numbers on the map. The selected recipe can skip stations, revisit a tank, or call for a return along the track. Highlight all included tanks and label the current step and the next visit. If the AGV controller reports only station arrivals, show the last confirmed station and the destination while the AGV is moving.

## Live tank status

Show the tank number and process name, active alarms with their plain-language meaning, the current temperature where available, and the current timer reading and completion state. Show applicable details such as door position, basket presence, and water flow under **More status**. Tank #7 can also show measured current and voltage. Tanks #13 and #16 can show conductivity with each meter’s reported units. [PLC tag catalog](../plc-signal-test-ui/config/mock-tags.json)

Keep the **live reading**, **PLC setting**, and **recipe target** distinct wherever they appear together. Use the PLC snapshot timestamp to flag stale readings. A missing value appears as **Unavailable**. Show the PLC’s **Local** or **Remote** state so the operator can see why a setting change is unavailable. Label the timer reading as **Remaining time** only once the PLC timer behavior is confirmed. [PLC polling and status](../plc-signal-test-ui/docs/architecture.md)

## Manual mode

Select a tank on the map to review its live readings and current PLC settings. From that panel, the operator can take either action.

- **Apply settings** sends the edited values to the PLC. Validate against current PLC tag permissions and limits, with values displayed in operator units. Show which values were confirmed by a fresh PLC readback and which could not be confirmed.
- **Move AGV to tank #…** sends the selected tank as the destination. Show **Move requested**, **Moving**, **Arrived**, or a fault as reported by the AGV controller.

| Editable setting | Tanks | Placement |
| --- | --- | --- |
| Time (s) | #1–#16 | Main settings |
| Temperature (°C) | #1, #3, #5, #7, #10, #13, #14, #16 | Main settings |
| Anodizing current (A) | #7 | More settings |
| Swing speed (cycles/min) | #3, #5, #10, #14 | More settings |

Define these fields in the tank configuration so individual controls can be removed later. A manual edit changes the selected tank’s PLC setting. Saved recipe targets stay as recorded in the recipe. [PLC tag catalog](../plc-signal-test-ui/config/mock-tags.json)

## Automatic mode

Operators can create, name, save, select, and edit recipes. Each recipe
contains a sequence of tank visits. Saved recipes and the selection survive
service restarts. Finish or stop a run before editing or switching recipes.

The operator reviews the sequence and any reported blockers, then presses **Start recipe** above the map. **Stop recipe** ends simulated travel and processing. Starting again begins at step 1. If a move was interrupted, show the last confirmed station until another arrival. Show the active step, the current and next tanks, and what the line is waiting for. If the IPC owns the sequence, it advances the run using confirmed AGV arrival and process-complete feedback. If an existing line controller owns the sequence, the IPC delegates the run and reports its progress to the browser.

Show recipe targets and PLC settings together before starting. The simulator checks the existing settings and shows any mismatches. The live start policy is open. Automatic could apply the recipe settings and verify them before starting, or check the existing settings and ask the operator to resolve mismatches. In either case, compare against the intended **PLC setting**, including any recipe pad.

## First recipe for the design

**Line test · Al part 600 × 400 mm · 2026-11-25**

| Step | Tank | Process | Target temperature (°C) | Process time (s) | Intended PLC time (s) |
| --- | --- | --- | ---: | ---: | ---: |
| 1 | #1 | Ultrasonic clean | 56 | 120 | 120 |
| 2 | #2 | Rinse | — | 10 | 10 |
| 3 | #3 | Etch | 40 | 40 | 40 |
| 4 | #4 | Rinse | — | 10 | 10 |
| 5 | #5 | Desmut | 30 | 120 | 120 |
| 6 | #6 | Rinse | — | 10 | 10 |
| 7 | #7 | Anodize | 20 | 600 | 681 |
| 8 | #8 | Rinse | — | 10 | 10 |
| 9 | #9 | Rinse | — | 10 | 10 |
| 10 | #12 | Rinse | — | 10 | 10 |
| 11 | #16 | DI rinse | 30 | 10 | 10 |
| 12 | #13 | Seal (DI) | 95 | 1200 | 1200 |

This recipe skips **#10 Dye (30 °C)**, **#11 Spray**, **#14 Passivation (38 °C)**, and **#15**. For the reported line-test conditions, the UI should show the following.

- **Step 1 blocked · Tank #1 low water level.** Show the reason on the run panel and on tank #1.
- **Tank #7 · 600 s process time, 681 s PLC setting.** Show the 81 s pad as part of the intended setting.
- **Tank #16 · 10 s required, 0 s reported.** Show the discrepancy before starting. In Manual, the operator can set the time and check the readback.
- **Tank #13 · 95 °C reported.** Show the measured temperature beside the 95 °C target.

## Integration details to confirm

- Confirm the AGV’s tank-to-station mapping, movement and arrival feedback, and supported travel from #16 back to #13. The route follows the guide track. The current layout sketch gives station positions.
- Confirm which PLC value means **low water active** for tank #1 (`Alarm51`), how the live timer counts, and how `RemoteStart` is triggered and reset. These affect the operator’s status labels and when Automatic can move to the next step. [PLC signal test plan](../plc-signal-test-ui/docs/signal-test-plan.md)
- Confirm who owns the Automatic sequence, which controller reports that the robot arm and part are clear for AGV travel, and how the operator changes from an active run to Manual. The current pick/place package expects the external system to coordinate AGV motion with robot operations. [Pick/place AGV coordination](../ur10e-vision-calibration-full/src/anodizing-modules/vendor/ur-pick-place/README.md#6-use-the-camera-pick-in-robot-bench)
- Confirm the shared water-flow address for tanks #8 and #9 before presenting the two readings as independently measured. [PLC catalog diagnostics](../plc-signal-test-ui/docs/architecture.md)
