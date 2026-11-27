<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { Command, DoorPilotAction, Field, LineStatus, Tank, TankControl } from './types'

const props = defineProps<{
  tank: Tank
  status: LineStatus
  disabled: boolean
  showControls?: boolean
  checkPlc?: () => Promise<LineStatus['plc']>
}>()
const emit = defineEmits<{ command: [command: Command] }>()
const draft = ref<Record<string, string | number>>({})
const checkingPlc = ref(false)
const plcCheckPassed = ref(false)
const plcCheckMessage = ref('')

const fields = computed(() => props.tank.fields)
const visits = computed(() => props.status.recipe.steps.flatMap((step, index) =>
  step.tank === props.tank.id ? [{ ...step, number: index + 1 }] : [],
))
const primary = computed(() => fields.value.filter(field => field.key === 'time' || field.key === 'temperature'))
const additional = computed(() => fields.value.filter(field => field.key === 'current' || field.key === 'swing'))
const readback = computed(() => fields.value.map(field => `${field.key}:${field.value}`).join(','))
const manual = computed(() => props.status.mode === 'manual')
const live = computed(() => props.status.plc.quality === 'live')
const alarmActive = computed(() => Boolean(props.tank.alarm) && live.value)
const hasTemperature = computed(() => fields.value.some(field => field.key === 'temperature'))
const agvAtTank = computed(() => props.status.agv.stationConfirmed && props.status.agv.station === props.tank.id)
const agvHeading = computed(() => props.status.agv.destination === props.tank.id)
const mockVisit = computed(() => props.status.mockStationVisit)
const mockJob = computed(() => mockVisit.value?.job?.tank === props.tank.id ? mockVisit.value.job : null)
const mockJobLabel = computed(() => {
  const state = mockJob.value?.state
  if (!state) return 'Ready for a mock station visit'
  return {
    claiming: 'Requesting mock control', waiting_for_arm: 'Waiting for mock arm', submitting: 'Submitting station visit',
    checking: 'Checking job status', accepted: 'Accepted', running: 'Moving', done: 'Arrived and stopped',
    stopped: 'Stopped', refused: 'Refused', failed: 'Failed', unknown: 'Outcome unconfirmed',
  }[state]
})
const doorPilot = computed(() => props.showControls && props.status.doorPilot?.tank === props.tank.id
  ? props.status.doorPilot : undefined)
const canTestDoor = computed(() => !props.disabled && live.value && plcCheckPassed.value && !checkingPlc.value)
const canEdit = computed(() => manual.value && !props.disabled && live.value && props.status.plc.remote)
const changed = computed(() =>
  fields.value.filter(field => field.editable && field.value !== null && String(field.value) !== String(draft.value[field.key] ?? '')),
)

watch([() => props.tank.id, readback], () => {
  draft.value = Object.fromEntries(fields.value.map(field => [field.key, field.value === null ? '' : String(field.value)]))
}, { immediate: true })

watch(live, fresh => {
  if (!fresh && plcCheckPassed.value) {
    plcCheckPassed.value = false
    plcCheckMessage.value = 'PLC feedback was interrupted. Check communication again.'
  }
})

async function checkCommunication() {
  if (!props.checkPlc || checkingPlc.value) return
  checkingPlc.value = true
  plcCheckPassed.value = false
  plcCheckMessage.value = ''
  try {
    await props.checkPlc()
    plcCheckPassed.value = true
    plcCheckMessage.value = 'PLC communication verified. Fresh reading received.'
  } catch (failure) {
    plcCheckMessage.value = failure instanceof Error ? failure.message : 'Could not check PLC communication.'
  } finally {
    checkingPlc.value = false
  }
}

function recipeTarget(field: Field) {
  const visit = visits.value[0]
  if (!visit) return null
  if (field.key === 'time') return visit.plcTime
  if (field.key === 'temperature') return visit.temperature
  return null
}

function mismatch(field: Field) {
  const target = recipeTarget(field)
  return live.value && target !== null && field.value !== null && field.value !== target
}

function controlLabel(control: TankControl) {
  if (control.id === 'cycle') return control.active ? 'Stop cycle' : 'Start cycle'
  if (control.id === 'door') return control.active ? 'Close door' : 'Open door'
  return control.label
}

function controlState(control: TankControl) {
  if (control.active === null) return 'Unknown'
  if (control.id === 'cycle') return control.active ? 'Running' : 'Idle'
  if (control.id === 'door') return control.active ? 'Open' : 'Closed'
  return control.active ? 'On' : 'Off'
}

function sendDoor(operation: DoorPilotAction) {
  const pilot = doorPilot.value
  if (props.disabled || !live.value || !pilot?.revision || pilot.nextAction !== operation) return
  if ((operation === 'open' || operation === 'close') && !canTestDoor.value) return
  const messages = {
    open: 'Open the physical door on tank #1? Confirm that the area is clear and you can observe the movement.',
    close: 'Close the physical door on tank #1? Confirm that the area is clear and you can observe the movement.',
    clear: 'Confirm that you observed the door on tank #1 reach its requested position and stop moving. Clear the PLC command?',
    resolve: 'Confirm that you inspected the door on tank #1 and verified at the line that both PLC commands are clear.',
  }
  if (!window.confirm(messages[operation])) return
  emit('command', { action: 'plc-door', tank: pilot.tank, operation, revision: pilot.revision, confirmed: true })
}

function revealInvalid(event: Event) {
  if (event.target instanceof HTMLInputElement) {
    const disclosure = event.target.closest('details')
    if (disclosure) disclosure.open = true
  }
}

function submit() {
  if (!canEdit.value || !changed.value.length || changed.value.some(field => !String(draft.value[field.key] ?? '').trim())) return
  emit('command', {
    action: 'settings',
    tank: props.tank.id,
    values: Object.fromEntries(changed.value.map(field => [field.key, Number(draft.value[field.key])])),
  })
}
</script>

<template>
  <section class="panel tank-panel" :class="{ 'is-alarm': alarmActive }" aria-labelledby="tank-title">
    <div class="tank-heading">
      <p class="eyebrow">Tank #{{ tank.id }}</p>
      <h2 id="tank-title">{{ tank.name }}</h2>
      <div class="tank-chips">
        <span v-if="alarmActive" class="chip chip-alert">Alarm</span>
        <span v-if="agvAtTank" class="chip chip-dark">AGV here</span>
        <span v-else-if="agvHeading" class="chip chip-info">AGV on the way</span>
        <span v-if="visits.length" class="chip chip-info">{{ visits.length === 1 ? 'Step' : 'Steps' }} {{ visits.map(visit => visit.number).join(', ') }}</span>
        <span v-else class="chip">Not in recipe</span>
      </div>
    </div>

    <div class="tank-content">
      <p v-if="alarmActive" class="tank-alert" role="status"><span class="alert-mark" aria-hidden="true">!</span>{{ tank.alarm }}</p>
      <p v-if="!live" class="tank-alert tank-caution">
        {{ status.plc.quality === 'stale' ? 'PLC readings are stale.' : 'PLC readings are unavailable.' }}
      </p>

      <div class="readings">
        <div v-if="hasTemperature">
          <span>Actual temperature</span>
          <strong :class="{ 'is-stale': !live }">{{ tank.temperature === null ? '—' : tank.temperature }}<small v-if="tank.temperature !== null"> °C</small></strong>
        </div>
        <div>
          <span>PLC timer</span>
          <strong :class="{ 'is-stale': !live }">
            <template v-if="tank.timer === null">—</template>
            <template v-else-if="tank.timerDone">Complete</template>
            <template v-else>{{ tank.timer }}<small> s</small></template>
          </strong>
        </div>
        <div v-if="tank.doorPosition">
          <span>Door position</span>
          <strong class="door-position" :class="{ 'is-stale': !live }">{{ tank.doorPosition }}</strong>
        </div>
      </div>

      <section v-if="manual && mockVisit?.tanks.includes(tank.id)" class="tank-controls" aria-labelledby="mock-visit-title">
        <div class="settings-heading">
          <h3 id="mock-visit-title">Station visit</h3>
          <span class="plc-mode">Mock IPC</span>
        </div>
        <p class="hint" role="status">
          <strong>{{ mockJobLabel }}</strong><template v-if="mockJob?.message"> · {{ mockJob.message }}</template>
        </p>
        <dl v-if="mockJob" class="status-list">
          <div><dt>AGV station</dt><dd>{{ mockJob.station }}</dd></div>
          <div><dt>Job ID</dt><dd class="mock-job-id">{{ mockJob.jobId }}</dd></div>
        </dl>
        <button type="button" class="button secondary full large"
          :disabled="disabled || agvAtTank || mockVisit.busy || mockJob?.state === 'unknown'"
          @click="$emit('command', { action: 'move', tank: tank.id })"
        >{{ agvAtTank ? 'AGV is at this tank' : `Move AGV to tank #${tank.id}` }}</button>
      </section>
      <p v-else-if="manual && mockVisit" class="hint">Select Tank #3 to run the mock station visit.</p>

      <section v-if="checkPlc && !status.simulation" class="tank-controls" aria-label="PLC communication">
        <button type="button" class="button secondary full" :disabled="checkingPlc" @click="checkCommunication">
          {{ checkingPlc ? 'Checking PLC…' : 'Check PLC communication' }}
        </button>
        <p v-if="plcCheckMessage" class="hint" role="status">{{ plcCheckMessage }}</p>
      </section>

      <section v-if="doorPilot" class="tank-controls" aria-labelledby="door-pilot-title">
        <div class="settings-heading">
          <h3 id="door-pilot-title">Door control</h3>
          <span class="plc-mode">Live PLC</span>
        </div>
        <p class="hint" role="status">
          {{ live ? doorPilot.message : 'Fresh PLC feedback is required.' }}
          <template v-if="live && !plcCheckPassed && (doorPilot.nextAction === 'open' || doorPilot.nextAction === 'close')"> Check PLC communication before requesting movement.</template>
        </p>
        <div class="tank-control-grid">
          <button type="button" class="button secondary" :disabled="!canTestDoor || doorPilot.nextAction !== 'open'" @click="sendDoor('open')">Open door</button>
          <button type="button" class="button secondary" :disabled="!canTestDoor || doorPilot.nextAction !== 'close'" @click="sendDoor('close')">Close door</button>
        </div>
        <button v-if="doorPilot.nextAction === 'clear'" type="button" class="button primary" :disabled="disabled || !live" @click="sendDoor('clear')">Clear PLC command</button>
        <button v-if="doorPilot.nextAction === 'resolve'" type="button" class="button secondary" :disabled="disabled || !live" @click="sendDoor('resolve')">Acknowledge after inspection</button>
      </section>

      <details v-if="tank.details.length" class="disclosure">
        <summary>More status</summary>
        <dl class="status-list">
          <div v-for="detail in tank.details" :key="detail.label"><dt>{{ detail.label }}</dt><dd>{{ detail.value }}</dd></div>
        </dl>
      </details>

      <section v-if="showControls && tank.controls?.length" class="tank-controls" aria-labelledby="tank-controls-title">
        <div class="settings-heading">
          <h3 id="tank-controls-title">{{ status.simulation ? 'Tank controls' : 'Equipment status' }}</h3>
          <span v-if="status.simulation" class="plc-mode">Simulation</span>
        </div>
        <div v-if="status.simulation" class="tank-control-grid">
          <button
            v-for="control in tank.controls"
            :key="control.id"
            type="button"
            class="button secondary tank-control"
            :class="{ 'is-active': control.active }"
            :disabled="!canEdit || control.active === null"
            :aria-pressed="control.id === 'cycle' || control.id === 'door' ? undefined : control.active ?? undefined"
            @click="$emit('command', { action: 'tank-control', tank: tank.id, control: control.id, active: !control.active })"
          >
            <span>{{ controlLabel(control) }}</span>
            <small>{{ controlState(control) }}</small>
          </button>
        </div>
        <dl v-else class="status-list">
          <div v-for="control in tank.controls" :key="control.id">
            <dt>{{ control.label }}</dt><dd>{{ controlState(control) }}</dd>
          </div>
        </dl>
      </section>

      <form class="settings" @invalid.capture="revealInvalid" @submit.prevent="submit">
        <div class="settings-heading">
          <h3>Settings</h3>
          <span v-if="live && status.plc.remote !== null" class="plc-mode">PLC {{ status.plc.remote ? 'Remote' : 'Local' }}</span>
        </div>
        <table class="setpoints">
          <thead>
            <tr>
              <th scope="col"><span class="sr-only">Setting</span></th>
              <th scope="col">PLC setting</th>
              <th scope="col">{{ visits.length ? 'Recipe target' : '' }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="field in primary" :key="field.key" :class="{ 'is-mismatch': mismatch(field) }">
              <th scope="row">{{ field.label }}</th>
              <td>
                <label v-if="manual && field.editable" class="input-unit">
                  <span class="sr-only">{{ field.label }} PLC setting</span>
                  <input v-model="draft[field.key]" type="number" inputmode="decimal" :min="field.min" :max="field.max" :step="field.step" :disabled="!canEdit" :title="`Allowed ${field.min}–${field.max} ${field.unit}`" required>
                  <span>{{ field.unit }}</span>
                </label>
                <strong v-else>{{ field.value === null ? 'Unavailable' : `${field.value} ${field.unit}` }}</strong>
                <small v-if="manual && field.editable" class="range">{{ field.min }}–{{ field.max }} {{ field.unit }}</small>
                <small v-else-if="manual" class="range">Locked in Settings</small>
              </td>
              <td class="target">
                <template v-if="recipeTarget(field) !== null">
                  <span v-if="mismatch(field)" class="mismatch-mark" title="PLC setting differs from recipe">≠</span>{{ recipeTarget(field) }} {{ field.unit }}
                </template>
                <template v-else-if="visits.length">—</template>
              </td>
            </tr>
          </tbody>
        </table>

        <ul v-if="visits.length" class="visit-notes">
          <li v-for="visit in visits" :key="visit.number">
            Step {{ visit.number }}: {{ visit.dwell }} s process<span v-if="visit.dwell !== visit.plcTime">, {{ visit.plcTime - visit.dwell }} s pad</span>
          </li>
        </ul>

        <details v-if="additional.length" class="disclosure">
          <summary>More settings</summary>
          <div class="setting-grid">
            <label v-for="field in additional" :key="field.key">
              <span>{{ field.label }}</span>
              <span v-if="manual && field.editable" class="input-unit">
                <input v-model="draft[field.key]" type="number" inputmode="decimal" :min="field.min" :max="field.max" :step="field.step" :disabled="!canEdit" :title="`Allowed ${field.min}–${field.max} ${field.unit}`" required>
                <span>{{ field.unit }}</span>
              </span>
              <strong v-else>{{ field.value === null ? 'Unavailable' : `${field.value} ${field.unit}` }}</strong>
              <small v-if="manual" class="range">{{ field.editable ? `${field.min}–${field.max} ${field.unit}` : 'Locked in Settings' }}</small>
            </label>
          </div>
        </details>

        <p v-if="manual && live && !status.plc.remote" class="hint">The PLC is in Local. Switch it to Remote to change settings here.</p>
        <button v-if="manual" class="button primary full large" :disabled="!canEdit || !changed.length" type="submit">
          Apply settings<span v-if="changed.length && canEdit" class="count"> ({{ changed.length }})</span>
        </button>
      </form>

      <button
        v-if="manual && !mockVisit"
        type="button"
        class="button secondary full large"
        :disabled="disabled || agvAtTank"
        @click="$emit('command', { action: 'move', tank: tank.id })"
      >{{ agvAtTank ? 'AGV is at this tank' : `Move AGV to tank #${tank.id}` }}</button>
      <p v-else-if="!manual && status.simulation" class="hint">Open the <a href="/manual" data-nav="manual">Manual</a> page to change settings or move the AGV.</p>
    </div>
  </section>
</template>

<style scoped>
.mock-job-id { overflow-wrap: anywhere; text-align: right; }
[aria-labelledby="mock-visit-title"] dt { flex: none; }
</style>
