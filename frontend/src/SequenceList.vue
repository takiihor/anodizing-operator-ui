<script setup lang="ts">
import { computed } from 'vue'
import type { LineStatus } from './types'

const props = defineProps<{ status: LineStatus; connected: boolean; selected: number }>()
defineEmits<{ select: [id: number] }>()

const fresh = computed(() => props.connected && props.status.plc.quality === 'live')

const rows = computed(() => {
  const { run, recipe, tanks } = props.status
  return recipe.steps.map((step, index) => {
    const tank = tanks.find(item => item.id === step.tank)
    const setTime = tank?.fields.find(field => field.key === 'time')?.value ?? null
    const setTemp = tank?.fields.find(field => field.key === 'temperature')?.value ?? null
    let state: 'done' | 'current' | 'halted' | 'pending' = 'pending'
    if (run.state === 'complete') state = 'done'
    else if (run.step !== null && (run.state === 'running' || run.state === 'stopped')) {
      if (index < run.step) state = 'done'
      else if (index === run.step) state = run.state === 'running' ? 'current' : 'halted'
    }
    const issues: string[] = []
    if (fresh.value && setTime !== null && setTime !== step.plcTime) issues.push(`PLC time is ${setTime} s, recipe needs ${step.plcTime} s`)
    if (fresh.value && step.temperature !== null && setTemp !== null && setTemp !== step.temperature) {
      issues.push(`PLC temperature is ${setTemp} °C, recipe needs ${step.temperature} °C`)
    }
    return { ...step, index, state, name: tank?.name ?? '', issues }
  })
})
const mismatches = computed(() => rows.value.filter(row => row.issues.length).length)
const total = computed(() => props.status.recipe.steps.reduce((sum, step) => sum + step.dwell, 0))

function duration(seconds: number) {
  const minutes = Math.floor(seconds / 60)
  return minutes ? `${minutes} min${seconds % 60 ? ` ${seconds % 60} s` : ''}` : `${seconds} s`
}
</script>

<template>
  <section class="panel sequence-panel" aria-labelledby="sequence-title">
    <div class="panel-heading">
      <div>
        <h2 id="sequence-title">Sequence</h2>
        <p>{{ status.recipe.steps.length }} steps · {{ duration(total) }} process time</p>
      </div>
      <span v-if="!fresh" class="chip chip-caution">PLC not current</span>
      <span v-else-if="mismatches" class="chip chip-alert">{{ mismatches }} PLC {{ mismatches === 1 ? 'mismatch' : 'mismatches' }}</span>
      <span v-else class="chip chip-ok">PLC matches recipe</span>
    </div>
    <ol class="sequence-list">
      <li
        v-for="row in rows"
        :key="row.index"
        :class="[`row-${row.state}`, { 'row-selected': row.tank === selected, 'row-issue': row.issues.length }]"
        :aria-current="row.state === 'current' ? 'step' : undefined"
      >
        <button type="button" @click="$emit('select', row.tank)">
          <span class="seq-state" :class="`state-${row.state}`" aria-hidden="true"></span>
          <span class="seq-step">{{ row.index + 1 }}</span>
          <span class="seq-tank">#{{ row.tank }}</span>
          <span class="seq-name">{{ row.name }}</span>
          <span class="seq-values">
            <span v-if="row.temperature !== null">{{ row.temperature }} °C</span>
            <span>{{ duration(row.dwell) }}</span>
          </span>
          <span v-if="row.issues.length" class="seq-issue" :title="row.issues.join('. ')">≠ PLC</span>
          <span class="sr-only">
            {{ row.state === 'done' ? 'Done' : row.state === 'current' ? 'In progress' : row.state === 'halted' ? 'Stopped here' : 'Pending' }}.
            {{ row.issues.join('. ') }}
          </span>
        </button>
      </li>
    </ol>
  </section>
</template>
