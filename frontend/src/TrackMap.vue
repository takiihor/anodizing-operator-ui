<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import type { LineStatus } from './types'

const props = withDefaults(defineProps<{
  status: LineStatus
  selected: number | null
  connected: boolean
  /** Tank order to show instead of the selected recipe (used by the recipe editor). */
  sequence?: number[] | null
  /** Clicking a tank adds it to the sequence rather than selecting it. */
  pick?: boolean
  animateTravel?: boolean
  title?: string
  hint?: string
}>(), { sequence: null, pick: false, title: 'Line map', hint: '' })
defineEmits<{ select: [id: number] }>()

// Map coordinates in a 1000 × 460 box. The guide track runs outside both rows,
// with the bend on the left and ultrasonic cleaning at the lower right.
const WIDTH = 1000
const HEIGHT = 460
const CARD_W = 88
const CARD_H = 92
const TRACK_TOP = 38
const TRACK_BOTTOM = 422
const TRACK_LEFT = 60
const TRACK_RIGHT = 990
const TURN_RADIUS = 38
const ROW_LEFT = 265
const ROW_RIGHT = 935
const TANK_SPACING = (ROW_RIGHT - ROW_LEFT) / 6
const TURN_X = TRACK_LEFT + TURN_RADIUS
const UPPER_TURN_Y = TRACK_TOP + TURN_RADIUS
const LOWER_TURN_Y = TRACK_BOTTOM - TURN_RADIUS
const TRACK_PATH = `M ${TRACK_RIGHT} ${TRACK_TOP} H ${TURN_X} A ${TURN_RADIUS} ${TURN_RADIUS} 0 0 0 ${TRACK_LEFT} ${UPPER_TURN_Y} V ${LOWER_TURN_Y} A ${TURN_RADIUS} ${TURN_RADIUS} 0 0 0 ${TURN_X} ${TRACK_BOTTOM} H ${TRACK_RIGHT}`

function centre(tank: number) {
  if (tank <= 7) return { x: ROW_RIGHT - (tank - 1) * TANK_SPACING, y: 335 }
  if (tank <= 9) return { x: 151, y: tank === 8 ? 285 : 175 }
  return { x: ROW_LEFT + (tank - 10) * TANK_SPACING, y: 125 }
}

function trackPoint(tank: number) {
  const { x, y } = centre(tank)
  if (tank <= 7) return { x, y: TRACK_BOTTOM }
  if (tank <= 9) return { x: TRACK_LEFT, y }
  return { x, y: TRACK_TOP }
}

function pct(x: number, y: number) {
  return { left: `${(x / WIDTH) * 100}%`, top: `${(y / HEIGHT) * 100}%` }
}

function cardStyle(tank: number) {
  const { x, y } = centre(tank)
  return {
    ...pct(x - CARD_W / 2, y - CARD_H / 2),
    width: `${(CARD_W / WIDTH) * 100}%`,
    height: `${(CARD_H / HEIGHT) * 100}%`,
  }
}

function markerStyle(tank: number) {
  const { x, y } = trackPoint(tank)
  return pct(x, y)
}

function trackDistance(tank: number) {
  const { x, y } = trackPoint(tank)
  if (tank >= 10) return TRACK_RIGHT - x
  const upperStraight = TRACK_RIGHT - TURN_X
  const turnLength = Math.PI * TURN_RADIUS / 2
  if (tank >= 8) return upperStraight + turnLength + y - UPPER_TURN_Y
  return upperStraight + 2 * turnLength + LOWER_TURN_Y - UPPER_TURN_Y + x - TURN_X
}

const trackLine = ref<SVGPathElement>()
const travelProgress = ref<number | null>(null)
const manualTravel = computed(() => props.animateTravel && props.status.simulation ? props.status.manualTravel : null)
let frame = 0
let leg: string | undefined

watch([manualTravel, () => props.connected], ([travel, connected]) => {
  cancelAnimationFrame(frame)
  const { station, destination } = props.status.agv
  if (!travel || station === null || destination === null || matchMedia('(prefers-reduced-motion: reduce)').matches) {
    travelProgress.value = null
    leg = undefined
    return
  }
  const nextLeg = `${station}:${destination}`
  if (leg !== nextLeg) travelProgress.value = null
  leg = nextLeg
  if (!connected) return

  const { durationMs, remainingMs } = travel
  const startProgress = Math.max(travelProgress.value ?? 0, 1 - remainingMs / durationMs)
  const startTime = performance.now()
  travelProgress.value = startProgress
  function update(now: number) {
    const progress = Math.min(1, startProgress + (now - startTime) / durationMs)
    travelProgress.value = progress
    if (progress < 1) frame = requestAnimationFrame(update)
  }
  frame = requestAnimationFrame(update)
}, { immediate: true })

onBeforeUnmount(() => cancelAnimationFrame(frame))

const vehicleStyle = computed(() => {
  const { station, destination } = props.status.agv
  if (station === null) return undefined
  if (travelProgress.value === null || destination === null || !trackLine.value) return markerStyle(station)
  const start = trackDistance(station)
  const end = trackDistance(destination)
  const point = trackLine.value.getPointAtLength(start + (end - start) * travelProgress.value)
  return pct(point.x, point.y)
})

const live = computed(() => props.connected && props.status.plc.quality === 'live')

const stations = computed(() => {
  const { status, connected } = props
  const visits = new Map<number, number[]>()
  const order = props.sequence ?? status.recipe.steps.map(step => step.tank)
  order.forEach((tank, index) => {
    const previous = visits.get(tank) ?? []
    previous.push(index + 1)
    visits.set(tank, previous)
  })
  const activeStep = !props.sequence && connected && status.run.state === 'running' ? status.run.step : null
  return status.tanks.map(tank => ({
    ...tank,
    hasTemperature: tank.fields.some(field => field.key === 'temperature'),
    steps: visits.get(tank.id) ?? [],
    current: activeStep !== null && status.recipe.steps[activeStep].tank === tank.id ? activeStep + 1 : null,
    next: activeStep !== null && status.run.nextTank === tank.id ? activeStep + 2 : null,
    alarmActive: Boolean(tank.alarm) && live.value,
  }))
})

const agvConfirmed = computed(() => props.connected && props.status.agv.stationConfirmed)

function number(tank: number) {
  return String(tank).padStart(2, '0')
}

function formatTimer(seconds: number) {
  const s = Math.max(0, Math.round(seconds))
  return s >= 60 ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}` : `${s} s`
}
</script>

<template>
  <section class="panel map-panel" aria-labelledby="map-title">
    <div class="panel-heading">
      <div>
        <h2 id="map-title">{{ title }}</h2>
        <p>{{ hint || `Select a tank to see its status. Numbered chips show visits in ${status.recipe.name}.` }}</p>
      </div>
      <slot name="actions"><span class="chip">16 tanks</span></slot>
    </div>
    <div class="route-scroll" tabindex="0" aria-label="U-shaped tank and AGV layout">
      <div class="diagram" :class="{ 'is-pick': pick }">
        <svg class="track" :viewBox="`0 0 ${WIDTH} ${HEIGHT}`" aria-hidden="true" focusable="false">
          <path class="track-bed" :d="TRACK_PATH" />
          <path ref="trackLine" class="track-line" :d="TRACK_PATH" />
          <g class="track-stops">
            <circle v-for="tank in stations" :key="tank.id" :cx="trackPoint(tank.id).x" :cy="trackPoint(tank.id).y" r="4" />
          </g>
        </svg>

        <div class="map-legend" aria-hidden="true">
          <span class="legend-title">AGV guide track</span>
          <span><i class="key key-recipe"></i>In recipe</span>
          <span><i class="key key-unused"></i>Skipped</span>
          <span><i class="key key-step">1</i>Visit order</span>
          <span v-if="status.agv.station !== null"><i class="key key-agv"></i>{{ agvConfirmed ? 'AGV' : 'Last confirmed AGV' }}</span>
          <span v-if="status.simulation"><i class="key key-destination"></i>Destination</span>
          <span><i class="key key-alarm">!</i>Alarm</span>
        </div>

        <button
          v-for="tank in stations"
          :key="tank.id"
          type="button"
          class="station"
          :class="{
            'is-selected': tank.id === selected,
            'is-alarm': tank.alarmActive,
            'is-destination': tank.id === status.agv.destination,
            'is-current': tank.current !== null,
            'is-next': tank.next !== null,
            'is-used': tank.steps.length,
            'is-unused': !tank.steps.length,
          }"
          :style="cardStyle(tank.id)"
          :aria-pressed="pick ? undefined : tank.id === selected"
          :aria-label="[
            pick ? `Add tank #${tank.id} ${tank.name} to the recipe` : `Tank #${tank.id} ${tank.name}`,
            tank.steps.length ? `Recipe steps ${tank.steps.join(', ')}` : 'Not in the recipe',
            tank.current !== null ? `${status.run.phase === 'travel' ? 'AGV en route' : 'Processing'} step ${tank.current}` : '',
            tank.next !== null ? `Next step ${tank.next}` : '',
            tank.alarmActive ? `Alarm: ${tank.alarm}` : '',
            tank.id === status.agv.destination ? 'AGV destination' : '',
          ].filter(Boolean).join(', ')"
          @click="$emit('select', tank.id)"
        >
          <span class="station-top">
            <span class="station-number">{{ number(tank.id) }}</span>
            <span v-if="tank.steps.length" class="station-steps" :title="`Steps ${tank.steps.join(', ')}`">
              <span v-for="step in tank.steps.slice(0, 2)" :key="step" class="step-chip" :class="{ 'is-current': step === tank.current, 'is-next': step === tank.next }">{{ step }}</span>
              <span v-if="tank.steps.length > 2" class="step-more">+{{ tank.steps.length - 2 }}</span>
            </span>
          </span>
          <span class="station-name">{{ tank.name }}</span>
          <span v-if="tank.current !== null" class="station-foot foot-current">
            {{ status.run.phase === 'travel' ? 'En route' : tank.timer !== null && !tank.timerDone ? formatTimer(tank.timer) : 'Processing' }}
          </span>
          <span v-else-if="tank.next !== null" class="station-foot foot-next">Next</span>
          <span v-else-if="tank.alarmActive" class="station-foot foot-alarm">Alarm</span>
          <span v-else-if="tank.hasTemperature" class="station-foot foot-reading" :class="{ 'is-stale': !live }">
            {{ tank.temperature === null ? '—' : `${tank.temperature} °C` }}
          </span>
          <span v-if="tank.alarmActive" class="alarm-flag" aria-hidden="true">!</span>
        </button>

        <span
          v-if="status.agv.destination !== null"
          class="destination"
          :style="markerStyle(status.agv.destination)"
          title="Requested destination"
          aria-hidden="true"
        ></span>
        <span
          v-if="status.agv.station !== null"
          class="vehicle"
          :class="{ 'is-unconfirmed': !agvConfirmed && (!connected || travelProgress === null), 'is-moving': connected && status.agv.moving }"
          :style="vehicleStyle"
          :title="agvConfirmed ? 'Current AGV station' : travelProgress === null ? 'Last confirmed station' : connected ? 'Simulated AGV in transit' : 'Last displayed simulated position'"
        >
          <span aria-hidden="true" class="vehicle-icon"></span>AGV
        </span>
      </div>
    </div>
    <p class="map-scroll-hint">Scroll sideways to see all tanks.</p>
  </section>
</template>
