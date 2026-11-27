<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import AgvControls from './AgvControls.vue'
import RecipesPage from './RecipesPage.vue'
import RunBar from './RunBar.vue'
import SettingsPage from './SettingsPage.vue'
import TankPanel from './TankPanel.vue'
import TrackMap from './TrackMap.vue'
import { navigate, page, TITLES } from './router'
import type { AgvStatus, Command, JobGatewayStatus, LineStatus, Mode, Page, RecipeLibrary } from './types'

let token = document.querySelector<HTMLMetaElement>('meta[name="module-token"]')?.content ?? ''
const snapshot = ref<LineStatus>()
const selected = ref(1)
const busy = ref(false)
const connected = ref(false)
const error = ref('')
const notice = ref('')
const wallTime = ref(Date.now())
let poll: AbortController | undefined
let timer: ReturnType<typeof setInterval> | undefined
let noticeTimer: ReturnType<typeof setTimeout> | undefined

const NAV: Page[] = ['run', 'manual', 'recipes', 'settings', 'agv']
const PAGE_MODE: Partial<Record<Page, Mode>> = { run: 'automatic', manual: 'manual' }

const status = computed<LineStatus | undefined>(() => {
  const current = snapshot.value
  if (!current || connected.value || current.plc.quality !== 'live') return current
  return { ...current, plc: { ...current.plc, quality: 'stale' } }
})
const tank = computed(() => status.value?.tanks.find(item => item.id === selected.value))
const active = computed(() => status.value?.run.state === 'running' || status.value?.agv.moving === true
  || status.value?.mockStationVisit?.busy === true)
const controlsDisabled = computed(() => busy.value || !connected.value || active.value)
const freshData = computed(() => connected.value && status.value?.plc.quality === 'live')
const alarms = computed(() => status.value?.tanks.filter(item => item.alarm) ?? [])
const pageMode = computed(() => PAGE_MODE[page.value])
const modeMismatch = computed(() => status.value?.simulation && !!pageMode.value && status.value.mode !== pageMode.value)
const age = computed(() => {
  const stamp = status.value?.plc.sampleTime
  if (!stamp) return ''
  const seconds = Math.max(0, Math.floor((wallTime.value - Date.parse(stamp)) / 1000))
  return seconds < 60 ? `${seconds} s ago` : `${Math.floor(seconds / 60)} min ago`
})
const plcLabel = computed(() => {
  const plc = status.value?.plc
  if (!plc) return '—'
  if (!connected.value) return 'Disconnected'
  if (plc.quality === 'live') return plc.remote === null ? 'Mode unknown' : plc.remote ? 'Remote' : 'Local'
  return plc.quality === 'stale' ? 'Data stale' : 'Unavailable'
})

async function request<T>(path: string, init: RequestInit): Promise<T> {
  const url = `/api/${path}`
  const headers = new Headers(init.headers)
  headers.set('X-Operator-Token', token)
  let response = await fetch(url, { ...init, headers })
  if (init.method !== 'POST' && response.status === 403) {
    connected.value = false
    const tokenResponse = await fetch('/api/line/token', { signal: init.signal })
    if (!tokenResponse.ok) throw new Error('Could not reconnect to the line service.')
    const { token: refreshedToken } = await tokenResponse.json()
    if (typeof refreshedToken !== 'string' || !refreshedToken) {
      throw new Error('The line service returned an invalid token.')
    }
    if (token !== refreshedToken) notice.value = ''
    token = refreshedToken
    headers.set('X-Operator-Token', token)
    response = await fetch(url, { ...init, headers })
  }
  const data = await response.json()
  if (!response.ok) throw Object.assign(new Error(data.error ?? 'The line service rejected the request.'), { setupUrl: data.setupUrl })
  return data
}

function requestAgv(path: 'status' | 'command', init: RequestInit) {
  return request<AgvStatus>(`agv/${path}`, init)
}

function requestJobGateway(signal: AbortSignal) {
  return request<JobGatewayStatus>('job-gateway/status', { signal })
}

function loadLibrary() {
  return request<RecipeLibrary>('line/recipes', { signal: AbortSignal.timeout(5000) })
}

async function refresh() {
  if (poll || busy.value) return
  const controller = new AbortController()
  poll = controller
  try {
    snapshot.value = await request<LineStatus>('line/status', { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(4000)]) })
    connected.value = true
  } catch {
    if (!controller.signal.aborted) connected.value = false
  } finally {
    poll = undefined
  }
}

const NOTICES: Partial<Record<Command['action'], string>> = {
  'save-recipe': 'Recipe saved and selected for Run.',
  'delete-recipe': 'Recipe deleted.',
  'select-recipe': 'Recipe selected for Run.',
  'tank-setup': 'Tank setup saved.',
  'tank-setup-default': 'Tank setup restored to defaults.',
  reset: 'Simulation reset to the starting test conditions.',
}

async function command(action: Command): Promise<boolean> {
  if (busy.value || !connected.value) return false
  busy.value = true
  poll?.abort()
  error.value = notice.value = ''
  try {
    snapshot.value = await request<LineStatus>('line/command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(action),
      signal: AbortSignal.timeout(action.action === 'plc-door' ? 10000 : 5000),
    })
    if (action.action === 'settings') notice.value = `Settings confirmed for tank #${action.tank}.`
    else if (action.action === 'move') notice.value = snapshot.value.mockStationVisit
      ? `Mock station visit requested for tank #${action.tank}.` : `Move to tank #${action.tank} requested.`
    else notice.value = NOTICES[action.action] ?? ''
    if (action.action === 'reset') selected.value = 1
    return true
  } catch (failure) {
    if (action.action === 'plc-door' && (failure instanceof DOMException || failure instanceof TypeError)) {
      error.value = 'Door request outcome is unknown. Check the door and PLC feedback before continuing.'
    } else {
      error.value = failure instanceof Error ? failure.message : 'Could not reach the line service.'
    }
    return false
  } finally {
    busy.value = false
    void refresh()
  }
}

watch(notice, value => {
  clearTimeout(noticeTimer)
  if (value) noticeTimer = setTimeout(() => { notice.value = '' }, 5000)
})

function switchMode(mode: Mode) {
  if (!status.value?.simulation || status.value.mode === mode) return
  void command({ action: 'mode', mode })
}

/** Run and Manual pages put the line in their mode; other pages leave it alone. */
function go(target: Page) {
  if (!navigate(target)) return
  const mode = PAGE_MODE[target]
  if (mode && !controlsDisabled.value) switchMode(mode)
}

function tankName(id: number) {
  return status.value?.tanks.find(item => item.id === id)?.name ?? ''
}

function selectFirstAlarm() {
  if (!freshData.value || !alarms.value.length) return
  selected.value = alarms.value[0].id
  if (page.value !== 'run' && page.value !== 'manual') go(status.value?.mode === 'manual' ? 'manual' : 'run')
}

function interceptLinks(event: MouseEvent) {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
  const link = (event.target as Element | null)?.closest<HTMLAnchorElement>('a[data-nav]')
  if (!link) return
  event.preventDefault()
  go(link.dataset.nav as Page)
}

onMounted(() => {
  document.addEventListener('click', interceptLinks)
  void refresh()
  timer = setInterval(() => {
    wallTime.value = Date.now()
    void refresh()
  }, 700)
})

onUnmounted(() => {
  document.removeEventListener('click', interceptLinks)
  clearInterval(timer)
  poll?.abort()
})
</script>

<template>
  <div class="app-shell">
    <header class="top-bar">
      <div class="top-inner">
        <a href="/" data-nav="run" class="brand" aria-label="Anodizing line, Run page">
          <span class="brand-mark" aria-hidden="true"><i></i><i></i><i></i></span>
          <span>Anodizing line</span>
        </a>

        <nav class="page-nav" aria-label="Pages">
          <a
            v-for="item in NAV"
            :key="item"
            :href="item === 'run' ? '/' : `/${item}`"
            :data-nav="item"
            :class="{ 'is-current': page === item, 'is-mode': !!PAGE_MODE[item], 'mode-active': status?.mode === PAGE_MODE[item] }"
            :aria-current="page === item ? 'page' : undefined"
          >
            <span class="nav-icon" aria-hidden="true">{{ { run: '▶', manual: '✋', recipes: '☰', settings: '⚙', agv: '⇄' }[item] }}</span>
            {{ TITLES[item] }}
            <span v-if="PAGE_MODE[item] && status?.mode === PAGE_MODE[item]" class="nav-mode" title="Current line mode">mode</span>
          </a>
        </nav>

        <dl v-if="status" class="top-status" aria-label="Line status">
          <div class="status-tile" :class="{ caution: !freshData }">
            <dt>Tank PLC</dt>
            <dd>
              <strong><i class="dot" aria-hidden="true"></i>{{ plcLabel }}</strong>
              <small>{{ freshData ? 'Readings up to date' : age ? `Last good reading ${age}` : 'Awaiting PLC data' }}</small>
            </dd>
          </div>
          <div class="status-tile" :class="{ moving: connected && status.agv.moving }">
            <dt>AGV</dt>
            <dd v-if="status.agv.station !== null">
              <strong v-if="connected && status.agv.moving"><i class="dot" aria-hidden="true"></i>Moving to #{{ status.agv.destination }}</strong>
              <strong v-else-if="connected && status.agv.stationConfirmed"><i class="dot" aria-hidden="true"></i>At tank #{{ status.agv.station }}</strong>
              <strong v-else><i class="dot" aria-hidden="true"></i>Last seen at #{{ status.agv.station }}</strong>
              <small v-if="status.agv.moving">Last confirmed at #{{ status.agv.station }}</small>
              <small v-else-if="connected && !status.agv.stationConfirmed">Stopped between stations</small>
              <small v-else>{{ tankName(status.agv.station) }}</small>
            </dd>
            <dd v-else>
              <strong>Station unavailable</strong>
              <small>{{ status.mockStationVisit ? 'Mock station visit' : 'See AGV jog for link status' }}</small>
            </dd>
          </div>
          <div class="status-tile" :class="{ alarm: freshData && alarms.length > 0, caution: !freshData }">
            <dt>{{ status.simulation ? 'Alarms' : 'Tank alarms' }}</dt>
            <dd>
              <component
                :is="freshData && alarms.length ? 'button' : 'span'"
                :type="freshData && alarms.length ? 'button' : undefined"
                class="alarm-summary"
                @click="selectFirstAlarm"
              >
                <strong><i class="dot" aria-hidden="true"></i>{{ freshData ? alarms.length ? `${alarms.length} active` : 'None' : '—' }}</strong>
                <small>{{ !freshData ? 'Fresh PLC data required' : alarms.length ? alarms[0].alarm : 'No tank alarms reported' }}</small>
              </component>
            </dd>
          </div>
        </dl>
        <a v-if="status?.simulation" href="/settings" data-nav="settings" class="environment" title="Simulation controls are on the Settings page">
          <i aria-hidden="true"></i> Simulation
        </a>
        <span v-else-if="status" class="environment environment-monitor">{{ status.doorPilot ? 'PLC door pilot' : 'PLC monitor' }}</span>
      </div>
    </header>

    <div class="page-body">
      <AgvControls v-if="page === 'agv'" :request="requestAgv" :request-job-gateway="requestJobGateway" />

      <main v-else-if="!status" class="page">
        <p class="banner banner-alert" role="status">Connecting to the line service…</p>
      </main>

      <main v-else class="page" :class="`page-${page}`">
        <h1 class="sr-only">{{ TITLES[page] }}</h1>

        <p v-if="modeMismatch" class="banner banner-caution mode-banner" role="status">
          <span>The line is in <strong>{{ status.mode === 'automatic' ? 'Automatic' : 'Manual' }}</strong>.
            {{ page === 'manual' ? 'Switch to Manual to operate tank controls, change settings, or move the AGV.' : 'Switch to Automatic to run a recipe.' }}</span>
          <button type="button" class="button secondary" :disabled="controlsDisabled" @click="switchMode(pageMode!)">
            Switch line to {{ pageMode === 'manual' ? 'Manual' : 'Automatic' }}
          </button>
          <small v-if="active">Available once the current run or AGV move finishes.</small>
        </p>

        <div v-if="page === 'run' && tank" class="run-page">
          <RunBar
            class="area-bar"
            :status="status"
            :connected="connected"
            :busy="busy"
            :disabled="controlsDisabled"
            :command="command"
            @select="id => selected = id"
          />
          <TrackMap class="area-map" :status="status" :selected="selected" :connected="connected" @select="id => selected = id" />
          <TankPanel class="area-side" :tank="tank" :status="status" :disabled="controlsDisabled" @command="command" />
        </div>

        <div v-else-if="page === 'manual' && tank" class="manual-page">
          <TrackMap
            class="area-map"
            :status="status"
            :selected="selected"
            :connected="connected"
            :title="status.simulation ? 'Manual control' : 'Tank monitor'"
            :hint="status.simulation ? 'Select a tank to access its controls, PLC settings, and AGV move command.' : 'Select a tank to view its PLC readings and equipment status.'"
            animate-travel
            @select="id => selected = id"
          >
            <template #actions>
              <a href="/agv" data-nav="agv" class="button ghost button-link">AGV jog controls</a>
            </template>
          </TrackMap>
          <TankPanel class="area-side" :tank="tank" :status="status" :disabled="controlsDisabled" show-controls @command="command" />
        </div>

        <RecipesPage
          v-else-if="page === 'recipes'"
          :status="status"
          :connected="connected"
          :disabled="controlsDisabled"
          :active="active"
          :command="command"
          :load-library="loadLibrary"
        />

        <SettingsPage
          v-else-if="page === 'settings'"
          :status="status"
          :connected="connected"
          :disabled="controlsDisabled"
          :active="active"
          :command="command"
        />
      </main>
    </div>

    <div class="toasts" aria-live="polite">
      <p v-if="!connected && status" class="toast toast-alert" role="status">Line service disconnected. Showing the last received status.</p>
      <p v-if="error" class="toast toast-alert" role="alert">
        <span>{{ error }}</span>
        <button type="button" class="toast-close" aria-label="Dismiss" @click="error = ''">✕</button>
      </p>
      <p v-if="notice" class="toast toast-info" role="status">{{ notice }}</p>
    </div>
  </div>
</template>
