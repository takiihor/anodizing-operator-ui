<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import type { JobGatewayStatus } from './types'

const props = defineProps<{ request: (signal: AbortSignal) => Promise<JobGatewayStatus> }>()
const gateway = ref<JobGatewayStatus>()
const error = ref('')
let timer: ReturnType<typeof setInterval> | undefined
let request: AbortController | undefined

async function refresh() {
  if (request) return
  const controller = new AbortController()
  request = controller
  try {
    gateway.value = await props.request(AbortSignal.any([controller.signal, AbortSignal.timeout(8000)]))
    error.value = ''
  } catch (failure) {
    if (controller.signal.aborted) return
    gateway.value = undefined
    error.value = failure instanceof Error ? failure.message : 'Could not read the IPC job gateway.'
  } finally {
    request = undefined
  }
}

onMounted(() => {
  void refresh()
  timer = setInterval(refresh, 6000)
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
        <p v-else>Protocol {{ gateway.health.protocol }} · {{ gateway.health.simulated ? 'Simulated IPC' : 'Live IPC' }}</p>
      </div>
    </div>
    <dl v-if="gateway?.configured" class="agv-readings">
      <div><dt>Gateway</dt><dd>{{ gateway.health.enabled ? 'Enabled' : 'Disabled' }}</dd></div>
      <div><dt>Motion capability</dt><dd>{{ gateway.health.motionEnabled ? 'Enabled' : 'Disabled' }}</dd></div>
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
  </section>
</template>
