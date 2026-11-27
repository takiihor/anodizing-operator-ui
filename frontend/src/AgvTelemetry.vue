<script setup lang="ts">
import { computed } from 'vue'
import type { GatewayAgvFeedback } from './types'

const props = defineProps<{ device?: GatewayAgvFeedback | null; unavailable: boolean }>()
const signals = computed(() => props.device?.fresh ? props.device.signals ?? {} : {})
const battery = computed(() => {
  const percent = signals.value.battery_percent
  return typeof percent === 'number' ? percent : null
})
const feedbackLabel = computed(() => {
  if (props.unavailable) return 'IPC unavailable'
  if (props.device === undefined) return 'Checking feedback…'
  if (props.device === null) return 'No device feedback'
  if (!props.device.connected) return 'AGV disconnected'
  return props.device.fresh ? 'Live feedback' : 'Status stale'
})
const motionLabel = computed(() => ({ moving: 'Moving', stopped: 'Stopped', unknown: 'Unknown' })[props.device?.motion ?? 'unknown'])

interface SignalRow {
  field: string
  label: string
  unit?: string
  optional?: boolean
}

const groups: { title: string; rows: SignalRow[] }[] = [
  { title: 'Travel', rows: [
    { field: 'target_station', label: 'Target station' },
    { field: 'set_speed_m_h', label: 'Set speed', unit: 'm/h' },
    { field: 'mode', label: 'Operating mode' },
    { field: 'station_arrival', label: 'Arrival signal' },
    { field: 'stopped', label: 'Stopped signal' },
    { field: 'command_stop', label: 'Stop command signal' },
    { field: 'paused', label: 'Paused' },
    { field: 'max_stations', label: 'Station count (reported)' },
    { field: 'position_units', label: 'Position units' },
    { field: 'leading_end', label: 'Leading end' },
  ] },
  { title: 'Sensors and attachments', rows: [
    { field: 'obstacle_stop', label: 'Obstacle stop' },
    { field: 'obstacle_input', label: 'Obstacle input' },
    { field: 'customer_sensors', label: 'Customer sensor inputs' },
    { field: 'roller_left_blocked', label: 'Left roller beam flag', optional: true },
    { field: 'roller_center_blocked', label: 'Center roller beam flag', optional: true },
    { field: 'roller_right_blocked', label: 'Right roller beam flag', optional: true },
    { field: 'laser_blocked', label: 'Laser beam flag', optional: true },
    { field: 'lift_state', label: 'Lift state' },
    { field: 'attachment_active', label: 'Attachment active', optional: true },
    { field: 'roller_speed', label: 'Roller speed', optional: true },
  ] },
  { title: 'Vehicle and fault', rows: [
    { field: 'vehicle_id', label: 'Vehicle ID' },
    { field: 'fault_code', label: 'Fault code' },
    { field: 'fault_description', label: 'Fault description' },
  ] },
]
const frame: SignalRow[] = [
  { field: 'header', label: 'Header' },
  { field: 'length', label: 'Length', unit: 'bytes' },
  { field: 'command', label: 'Command' },
  { field: 'checksum', label: 'Checksum' },
]

function valueFor({ field, unit }: SignalRow) {
  const value = signals.value[field]
  if (value === undefined) return '—'
  if (field === 'mode' && value === 2) return 'Station call (2)'
  if (field === 'mode' && value === 0) return 'Standard (0)'
  if (field === 'target_station' && props.device?.targetTank != null) return `${value} (Tank #${props.device.targetTank})`
  if (field === 'obstacle_input' && typeof value === 'number') return `${value} (0x${value.toString(16).toUpperCase()})`
  if (typeof value === 'boolean') return value ? 'On' : 'Off'
  return unit ? `${value} ${unit}` : value
}
</script>

<template>
  <section class="panel" aria-labelledby="agv-feedback-title">
    <div class="panel-heading">
      <div>
        <h1 id="agv-feedback-title">AGV status</h1>
        <p role="status">{{ feedbackLabel }}<template v-if="device?.statusAge != null"> · Last report {{ device.statusAge.toFixed(1) }} s ago</template></p>
      </div>
    </div>
    <dl class="agv-readings">
      <div>
        <dt>Battery</dt>
        <dd>{{ battery === null ? '—' : `${battery}%` }}</dd>
        <progress v-if="battery !== null" class="battery-meter" :value="battery" max="100" aria-label="AGV battery percentage" />
      </div>
      <div>
        <dt>Position</dt>
        <dd>{{ device?.tank != null ? `Tank #${device.tank}` : device?.station != null ? `Station #${device.station}` : '—' }}</dd>
        <small v-if="device?.tank != null">Station #{{ device.station }}</small>
      </div>
      <div><dt>Motion</dt><dd>{{ motionLabel }}</dd></div>
      <div><dt>Actual speed</dt><dd>{{ device?.speed == null ? '—' : `${device.speed} m/h` }}</dd></div>
    </dl>
    <div class="agv-command-panel">
      <div class="telemetry-groups">
        <section v-for="group in groups" :key="group.title">
          <h2>{{ group.title }}</h2>
          <dl class="status-list telemetry-values">
            <div v-for="row in group.rows.filter(item => !item.optional || signals[item.field] !== undefined)" :key="row.field">
              <dt :title="row.field">{{ row.label }}</dt><dd>{{ valueFor(row) }}</dd>
            </div>
          </dl>
        </section>
      </div>
      <details class="disclosure">
        <summary>Frame details</summary>
        <dl class="status-list telemetry-values">
          <div v-for="row in frame" :key="row.field"><dt :title="row.field">{{ row.label }}</dt><dd>{{ valueFor(row) }}</dd></div>
        </dl>
      </details>
    </div>
  </section>
</template>

<style scoped>
.battery-meter { display: block; width: min(100%, 100px); margin-top: 4px; accent-color: var(--accent); }
.telemetry-groups { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 235px), 1fr)); gap: 16px; }
.telemetry-groups h2 { font-size: .9rem; margin-bottom: 7px; }
.telemetry-values dd { overflow-wrap: anywhere; }
</style>
