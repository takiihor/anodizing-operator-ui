<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { Command, LineStatus } from './types'

const props = defineProps<{
  status: LineStatus
  connected: boolean
  busy: boolean
  disabled: boolean
  command: (action: Command) => Promise<boolean>
}>()
defineEmits<{ select: [id: number] }>()

const automatic = computed(() => props.status.mode === 'automatic')
const run = computed(() => props.status.run)
const steps = computed(() => props.status.recipe.steps)
const blocked = computed(() => props.connected && run.value.blockers.length > 0)
const currentStep = computed(() => run.value.step !== null ? steps.value[run.value.step] : undefined)
const activeIndex = computed(() => run.value.state === 'running' ? run.value.step : null)
const strip = ref<HTMLOListElement>()

const runLabel = computed(() => {
  if (!props.status.simulation) return 'PLC monitoring'
  if (!props.connected) return 'Disconnected'
  if (!automatic.value) return 'Line in Manual'
  if (run.value.state === 'running') return run.value.phase === 'travel' ? 'AGV travelling' : 'Processing'
  if (run.value.state === 'stopped') return 'Stopped'
  if (run.value.blockers.length) return 'Blocked'
  return run.value.state === 'complete' ? 'Complete' : 'Ready'
})
const runTone = computed(() => {
  if (!props.connected || blocked.value) return 'alert'
  if (!automatic.value) return 'caution'
  if (run.value.state === 'running') return 'running'
  if (run.value.state === 'stopped') return 'caution'
  if (run.value.state === 'complete') return 'done'
  return 'ready'
})

function blockerTank(blocker: string) {
  const match = /Tank #(\d+)/.exec(blocker)
  return match ? Number(match[1]) : null
}
const blockerTanks = computed(() => new Set(run.value.blockers.map(blockerTank).filter(tank => tank !== null)))

const progress = computed(() => steps.value.map((step, index) => {
  const { state, step: current } = run.value
  let tone: 'pending' | 'done' | 'current' | 'next' | 'halted' = 'pending'
  if (state === 'complete') tone = 'done'
  else if (current !== null && (state === 'running' || state === 'stopped')) {
    if (index < current) tone = 'done'
    else if (index === current) tone = state === 'running' ? 'current' : 'halted'
    else if (state === 'running' && index === current + 1) tone = 'next'
  }
  const issues: string[] = []
  if (props.connected && props.status.plc.quality === 'live') {
    const fields = props.status.tanks.find(tank => tank.id === step.tank)?.fields
    const time = fields?.find(field => field.key === 'time')?.value
    const temperature = fields?.find(field => field.key === 'temperature')?.value
    if (time != null && time !== step.plcTime) issues.push(`PLC time ${time} s, target ${step.plcTime} s`)
    if (temperature != null && step.temperature !== null && temperature !== step.temperature) {
      issues.push(`PLC temperature ${temperature} °C, target ${step.temperature} °C`)
    }
  }
  const stateLabel = { pending: '', done: 'Done', current: 'Current', next: 'Next', halted: 'Stopped' }[tone]
  const label = [`Step ${index + 1}, tank #${step.tank} ${tankName(step.tank)}`, stateLabel,
    `${step.dwell} s process`, step.temperature === null ? '' : `${step.temperature} °C target`, ...issues].filter(Boolean).join(' · ')
  return { ...step, index, tone, stateLabel, label,
    issue: props.connected && state !== 'running' && (issues.length > 0 || blockerTanks.value.has(step.tank)) }
}))
const doneCount = computed(() => progress.value.filter(step => step.tone === 'done').length)

watch([strip, activeIndex, () => props.status.recipe.id, () => props.status.recipe.revision], ([list, index], previous) => {
  if (!list) return
  if (index === null) {
    if (!previous[0] || previous[2] !== props.status.recipe.id || previous[3] !== props.status.recipe.revision) list.scrollLeft = 0
    return
  }
  const current = list.children[index]
  if (!current) return
  const last = current.nextElementSibling ?? current
  const viewport = list.getBoundingClientRect()
  const left = current.getBoundingClientRect().left - viewport.left
  const right = last.getBoundingClientRect().right - viewport.right
  if (left < 0) list.scrollLeft += left - 6
  else if (right > -6) list.scrollLeft += right + 6
}, { flush: 'post' })

function tankName(id: number) {
  return props.status.tanks.find(item => item.id === id)?.name ?? ''
}

async function selectRecipe(event: Event) {
  const select = event.currentTarget as HTMLSelectElement
  if (!await props.command({ action: 'select-recipe', id: select.value })) {
    select.value = props.status.recipe.id
  }
}
</script>

<template>
  <section class="panel run-bar" :class="`tone-${runTone}`" aria-labelledby="run-title">
    <div class="run-row">
      <div class="recipe-select-group">
        <label class="recipe-picker">
          <span>Selected recipe</span>
          <select :value="status.recipe.id" :disabled="disabled" @change="selectRecipe">
            <option v-for="recipe in status.recipes" :key="recipe.id" :value="recipe.id">{{ recipe.name }}</option>
          </select>
        </label>
        <p class="recipe-meta">
          {{ steps.length }} {{ steps.length === 1 ? 'step' : 'steps' }}<template v-if="status.recipe.part"> · {{ status.recipe.part }}</template>
          · <a href="/recipes" data-nav="recipes">Edit recipes</a>
        </p>
      </div>

      <div class="run-state">
        <h2 id="run-title" class="eyebrow">{{ status.simulation ? 'Automatic run' : 'Line status' }}</h2>
        <p class="run-headline">
          <span class="run-status" :class="`tone-${runTone}`"><i aria-hidden="true"></i>{{ runLabel }}</span>
        </p>
        <p id="run-description" class="run-description" role="status">
          <template v-if="!status.simulation">Recipe execution is disabled in PLC monitor mode.</template>
          <template v-else-if="!connected">Reconnect to control the recipe.</template>
          <template v-else-if="!automatic">Switch the line to Automatic to run a recipe.</template>
          <template v-else-if="run.state === 'running' && run.step !== null && currentStep">
            <strong>Step {{ run.step + 1 }} of {{ steps.length }}</strong>
            · {{ run.phase === 'travel' ? `Waiting for AGV at #${currentStep.tank}` : `Processing at #${currentStep.tank}` }}
            <span v-if="run.nextTank !== null"> · Next #{{ run.nextTank }}</span>
          </template>
          <template v-else-if="run.state === 'stopped' && run.step !== null">
            Stopped during step {{ run.step + 1 }}. Starting again begins at step 1.
          </template>
          <template v-else-if="run.blockers.length">{{ run.blockers.length }} {{ run.blockers.length === 1 ? 'issue' : 'issues' }} to resolve before starting</template>
          <template v-else-if="run.state === 'complete'">All {{ steps.length }} steps finished.</template>
          <template v-else>All checks passed. Ready to start.</template>
        </p>
      </div>

      <div v-if="status.simulation" class="run-actions" role="group" aria-label="Automatic recipe actions">
        <button
          type="button"
          class="button primary large"
          :disabled="disabled || !automatic || run.blockers.length > 0"
          :aria-describedby="blocked ? 'run-description run-blockers' : 'run-description'"
          @click="command({ action: 'start', recipeId: status.recipe.id, revision: status.recipe.revision })"
        ><span class="play-mark" aria-hidden="true"></span>Start recipe</button>
        <button
          type="button"
          class="button danger large"
          :disabled="busy || !connected || run.state !== 'running'"
          aria-describedby="run-description"
          @click="command({ action: 'stop' })"
        ><span class="stop-mark" aria-hidden="true"></span>Stop recipe</button>
      </div>
    </div>

    <ol v-if="steps.length" ref="strip" class="progress" :aria-label="status.simulation ? `Recipe sequence, ${doneCount} of ${steps.length} steps done` : 'Selected recipe sequence'">
      <li v-for="step in progress" :key="step.index" :class="[`is-${step.tone}`, { 'has-issue': step.issue }]">
        <button
          type="button"
          :aria-label="step.issue ? `${step.label} · Needs attention` : step.label"
          :title="step.issue ? `${step.label} · Needs attention` : step.label"
          :aria-current="step.tone === 'current' ? 'step' : undefined"
          @click="$emit('select', step.tank)"
        >
          <span class="progress-step">{{ step.index + 1 }}<template v-if="step.stateLabel && step.tone !== 'done'"> · {{ step.stateLabel }}</template></span>
          <span class="progress-tank">#{{ step.tank }}</span>
        </button>
      </li>
    </ol>

    <div v-if="automatic && blocked && run.state !== 'running'" id="run-blockers" class="blockers">
      <strong class="blockers-title"><span class="alert-mark" aria-hidden="true">!</span>Resolve before starting</strong>
      <ul>
        <li v-for="blocker in run.blockers" :key="blocker">
          <span>{{ blocker }}</span>
          <button v-if="blockerTank(blocker) !== null" type="button" class="link-button" @click="$emit('select', blockerTank(blocker)!)">
            Show tank #{{ blockerTank(blocker) }}
          </button>
        </li>
      </ul>
    </div>
  </section>
</template>
