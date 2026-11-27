<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import type { AgvAction, AgvAuthority, AgvStatus } from './types'

const props = defineProps<{
  request: (path: 'status' | 'command', init: RequestInit) => Promise<AgvStatus>
}>()

const movements = [
  { action: 'forward', label: 'Forward', symbol: '↑' },
  { action: 'reverse', label: 'Reverse', symbol: '↓' },
  { action: 'rotate_counterclockwise', label: 'Rotate left 180°', symbol: '↶' },
  { action: 'rotate_clockwise', label: 'Rotate right 180°', symbol: '↷' },
] as const

const link = ref<AgvStatus>()
const setupUrl = ref<string | null>(null)
const busy = ref(false)
const stopping = ref(false)
const connectionError = ref('')
const commandError = ref('')
const notice = ref('')
const armedHere = ref<AgvAuthority>()
const watched = ref<{ action: AgvAction; id: number; authority: AgvAuthority }>()
let polling = false
let requestOrder = 0
let appliedOrder = 0
let commandOrder = 0
let timer: ReturnType<typeof setInterval> | undefined

function sameConnection(before: AgvAuthority, after: AgvAuthority) {
  return before.controller === after.controller && before.connectionId === after.connectionId
}

function apply(next: AgvStatus, order: number) {
  const previous = link.value
  if (previous && (next.authority.controller === previous.authority.controller
    ? next.sequence < previous.sequence : order < appliedOrder)) return

  appliedOrder = Math.max(appliedOrder, order)
  setupUrl.value = next.setupUrl
  link.value = next
  connectionError.value = ''
  const approval = armedHere.value
  if (approval && (!next.armed || !next.connected || !sameConnection(approval, next.authority)
    || approval.stopGeneration !== next.authority.stopGeneration)) armedHere.value = undefined
}

const motionBlock = computed(() => connectionError.value || link.value?.motionBlock || '')
const canArm = computed(() => !motionBlock.value && link.value?.motion === 'stopped' && !busy.value && !stopping.value)
const canMove = computed(() => !motionBlock.value && Boolean(armedHere.value) && !busy.value && !stopping.value)
const linkLabel = computed(() => {
  const mode = link.value?.linkMode ?? link.value?.transport
  return mode === 'simulator' ? 'AGV simulator' : mode === 'serial' ? 'Framed serial link' : mode === 'wired' ? 'Wired RS-485 · Status only' : 'AGV controller'
})
const outcome = computed(() => {
  const command = watched.value
  const current = link.value
  if (!command || !current || !sameConnection(command.authority, current.authority)) return ''
  const result = current.results[command.action]
  if (!result || result.id !== command.id) return 'The command outcome is unavailable.'
  if (command.action === 'stop') {
    if (result.confirmedStopped) return 'Stop confirmed by the AGV.'
    return current.haltPending || stopping.value ? 'Stop requested. Waiting for stopped feedback.' : 'Stop unconfirmed.'
  }
  if (['no reply', 'not checked'].includes(result.state)) return 'The command outcome is unconfirmed.'
  if (result.state === 'confirmed by status') return 'Command confirmed by AGV status.'
  if (result.acknowledged) return 'Command acknowledged by the AGV.'
  return 'Command sent. Waiting for AGV feedback.'
})

async function refresh() {
  if (polling) return
  polling = true
  const order = ++requestOrder
  try {
    apply(await props.request('status', { signal: AbortSignal.timeout(6500) }), order)
  } catch (failure) {
    if (order < appliedOrder) return
    armedHere.value = undefined
    connectionError.value = failure instanceof Error ? failure.message : 'Could not read the AGV controller.'
    if (failure instanceof Error && 'setupUrl' in failure && typeof failure.setupUrl === 'string') {
      setupUrl.value = failure.setupUrl
    }
  } finally {
    polling = false
  }
}

async function send(action: AgvAction) {
  const authority = link.value?.authority
  const priority = action === 'stop'
  if (!authority || stopping.value || (!priority && busy.value)) return
  const confirmed = action === 'arm' && link.value?.linkMode === 'serial'
  if (confirmed && !window.confirm('Confirm the AGV travel area is clear and the mounted robot is in its approved travel position.')) return
  if (priority) {
    stopping.value = true
    armedHere.value = undefined
  } else {
    busy.value = true
  }
  commandError.value = notice.value = ''
  watched.value = undefined
  const attempt = ++commandOrder
  const order = ++requestOrder
  try {
    const next = await props.request('command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, authority, confirmed }),
      signal: AbortSignal.timeout(9500),
    })
    apply(next, order)
    if (attempt !== commandOrder) return
    if (action === 'connect' || action === 'disconnect') {
      notice.value = next.connected ? 'AGV link connected.' : 'AGV link disconnected.'
    } else if (action === 'arm' && link.value?.armed && sameConnection(authority, link.value.authority)
      && link.value.authority.stopGeneration === authority.stopGeneration) {
      armedHere.value = authority
      notice.value = 'AGV armed.'
    } else if (action === 'disarm') {
      notice.value = 'AGV disarmed.'
    } else if (action === 'query_status') {
      notice.value = next.fresh ? 'Fresh AGV status received.' : 'Waiting for a fresh AGV report.'
    } else if (next.results[action]) {
      watched.value = { action, id: next.results[action].id, authority: next.authority }
    } else {
      notice.value = priority ? 'Stop unconfirmed.' : 'The command outcome is unavailable.'
    }
  } catch (failure) {
    if (attempt !== commandOrder) return
    armedHere.value = undefined
    commandError.value = failure instanceof Error ? failure.message : 'The AGV command outcome is unknown.'
    if (priority) commandError.value = `Stop unconfirmed. ${commandError.value}`
  } finally {
    if (priority) stopping.value = false
    else busy.value = false
    void refresh()
  }
}

onMounted(() => {
  void refresh()
  timer = setInterval(refresh, 700)
})
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <section class="panel agv-controls" aria-labelledby="agv-title">
      <div class="panel-heading">
        <div>
          <h1 id="agv-title">AGV manual controls</h1>
          <p>{{ linkLabel }}</p>
        </div>
        <button class="button danger large" type="button" :disabled="!link?.canStop || stopping" @click="send('stop')">
          <span class="stop-mark" aria-hidden="true"></span>{{ stopping ? 'Requesting Stop…' : 'Stop AGV' }}
        </button>
      </div>
      <dl class="agv-readings">
        <div>
          <dt>Connection</dt>
          <dd>{{ connectionError ? 'Unavailable' : !link ? 'Checking…' : !link.connected ? 'Disconnected' : link.fresh ? 'Connected' : 'Status stale' }}</dd>
          <small v-if="!connectionError && link?.connected && link.statusAge !== null">Last report {{ link.statusAge.toFixed(1) }} s ago</small>
        </div>
        <div>
          <dt>Motion</dt>
          <dd>{{ connectionError || !link || link.motion === 'unknown' ? 'Unknown' : link.motion === 'moving' ? 'Moving' : 'Stopped' }}</dd>
        </div>
        <div>
          <dt>Actual speed</dt>
          <dd>{{ connectionError || link?.speed == null ? '—' : `${link.speed} m/h` }}</dd>
        </div>
        <div>
          <dt>Fault</dt>
          <dd>{{ connectionError || link?.fault == null ? '—' : link.fault === 0 ? 'None' : `Code ${link.fault}` }}</dd>
        </div>
      </dl>
      <div class="agv-command-panel">
        <div class="agv-link-actions">
          <a v-if="setupUrl" :href="setupUrl" class="button secondary button-link">Controller setup</a>
          <button v-if="link?.canConnect" class="button secondary" type="button" :disabled="busy || stopping || !!connectionError" @click="send('connect')">Connect {{ link.linkMode === 'simulator' ? 'simulator' : 'AGV' }}</button>
          <button v-else-if="link?.linkMode && link.connected" class="button secondary" type="button" :disabled="!link.canDisconnect || busy || stopping || !!connectionError" @click="send('disconnect')">Disconnect</button>
          <button class="button secondary" type="button" :disabled="!link?.canQuery || busy || stopping" @click="send('query_status')">Query status</button>
          <button v-if="armedHere" class="button secondary" type="button" :disabled="busy || stopping" @click="send('disarm')">Disarm</button>
          <button v-else class="button primary" type="button" :disabled="!canArm" @click="send('arm')">Arm AGV</button>
        </div>
        <p v-if="motionBlock || link?.error" class="agv-message" role="status">{{ link?.error || motionBlock }}</p>
        <p v-else-if="!armedHere" class="agv-message">Check the travel path and arm the AGV to enable movement.</p>
        <div class="agv-movement" role="group" aria-label="AGV movement">
          <button
            v-for="move in movements" :key="move.action" class="button secondary large" type="button"
            :disabled="!canMove" @click="send(move.action)"
          ><span aria-hidden="true">{{ move.symbol }}</span>{{ move.label }}</button>
        </div>
        <p v-if="commandError" class="banner banner-alert" role="alert">{{ commandError }}</p>
        <p v-else-if="notice || outcome" class="agv-message" role="status">{{ notice || outcome }}</p>
      </div>
  </section>
</template>
