<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import type { JobGatewayStatus, MockStationVisit } from './types'

const props = defineProps<{
  request: (signal: AbortSignal) => Promise<JobGatewayStatus>
  stationVisit?: MockStationVisit
  testDisabled: boolean
}>()
defineEmits<{ testVisit: [] }>()
const gateway = ref<JobGatewayStatus>()
const error = ref('')
const checking = ref(false)
const verified = ref(false)
const job = computed(() => props.stationVisit?.job)
const visitStatus = computed(() => {
  if (!job.value) return ''
  if (job.value.state === 'done') return 'Arrived and stopped'
  if (job.value.state === 'unknown') return 'Outcome unconfirmed'
  const state = job.value.state.replaceAll('_', ' ')
  return state.charAt(0).toUpperCase() + state.slice(1)
})
let timer: ReturnType<typeof setInterval> | undefined
let request: AbortController | undefined

async function refresh(checkCommunication = false) {
  if (request) return
  const controller = new AbortController()
  request = controller
  checking.value = true
  try {
    gateway.value = await props.request(AbortSignal.any([controller.signal, AbortSignal.timeout(8000)]))
    verified.value = gateway.value.configured && (checkCommunication || verified.value)
    error.value = ''
  } catch (failure) {
    if (controller.signal.aborted) return
    gateway.value = undefined
    verified.value = false
    error.value = failure instanceof Error ? failure.message : 'Could not read the IPC job gateway.'
  } finally {
    request = undefined
    checking.value = false
  }
}

onMounted(() => {
  void refresh()
  timer = setInterval(() => void refresh(), 6000)
})

onUnmounted(() => {
  clearInterval(timer)
  request?.abort()
})
</script>

<template>
  <section class="panel" aria-labelledby="job-gateway-title">
    <div class="panel-heading">
      <div>
        <h2 id="job-gateway-title">IPC Job Gateway</h2>
        <p v-if="error" role="status">{{ error }}</p>
        <p v-else-if="!gateway" role="status">Checking connection…</p>
        <p v-else-if="!gateway.configured">Gateway connection is not configured.</p>
        <p v-else role="status">
          Protocol {{ gateway.health.protocol }} · {{ gateway.health.simulated ? 'Simulated IPC' : 'Live IPC' }}
          <template v-if="verified"> · Communication verified</template>
        </p>
      </div>
      <button type="button" class="button secondary" :disabled="checking" @click="refresh(true)">
        {{ checking ? 'Checking IPC…' : 'Check IPC communication' }}
      </button>
    </div>
    <dl v-if="gateway?.configured" class="agv-readings">
      <div>
        <dt>Gateway</dt>
        <dd>{{ gateway.health.enabled ? 'Enabled' : 'Disabled' }}</dd>
        <small>Motion {{ gateway.health.motionEnabled ? 'enabled' : 'disabled' }}</small>
      </div>
      <div v-if="!gateway.health.simulated">
        <dt>IPC to AGV</dt>
        <dd>{{ !gateway.agv ? 'No device feedback' : !gateway.agv.connected ? 'Disconnected' : gateway.agv.fresh ? 'Live feedback' : 'Status stale' }}</dd>
        <small v-if="gateway.agv?.fresh">
          <template v-if="gateway.agv.station !== null">Station #{{ gateway.agv.station }} · </template>
          {{ { moving: 'Moving', stopped: 'Stopped', unknown: 'Motion unknown' }[gateway.agv.motion] }}
        </small>
        <small v-if="gateway.agv?.statusAge != null">Last report {{ gateway.agv.statusAge.toFixed(1) }} s ago</small>
      </div>
      <div>
        <dt>Owner</dt>
        <dd>{{ gateway.status.owner ?? 'None' }}</dd>
        <small>{{ gateway.status.armed ? 'Armed' : 'Disarmed' }}<template v-if="gateway.status.recoveryRequired"> · Recovery required</template></small>
      </div>
      <div>
        <dt>Module jobs</dt>
        <dd>{{ gateway.jobs.length }}</dd>
        <small v-if="gateway.status.activeJobId">Active {{ gateway.status.activeJobId }}</small>
      </div>
    </dl>
    <div v-if="stationVisit" class="agv-command-panel">
      <div class="agv-link-actions">
        <button type="button" class="button secondary"
          :disabled="testDisabled || stationVisit.busy || job?.state === 'unknown'"
          @click="$emit('testVisit')">Test move to Tank #{{ stationVisit.tanks[0] }} (simulation)</button>
      </div>
      <p v-if="job" class="agv-message" role="status">
        <strong>{{ visitStatus }}</strong><template v-if="job.message"> · {{ job.message }}</template>
        <small class="mock-job-id"> · {{ job.jobId }}</small>
      </p>
    </div>
  </section>
</template>
