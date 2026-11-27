<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { confirmDiscard, setUnsavedCheck } from './router'
import type { Command, LineStatus, Quality, TankSetupBody } from './types'

const props = defineProps<{
  status: LineStatus
  connected: boolean
  disabled: boolean
  active: boolean
  command: (action: Command) => Promise<boolean>
}>()

const selected = ref(1)
const draft = ref<TankSetupBody>()
const baseline = ref('')

const tank = computed(() => props.status.tanks.find(item => item.id === selected.value))
const dirty = computed(() => draft.value !== undefined && JSON.stringify(draft.value) !== baseline.value)
const invalid = computed(() => {
  const current = draft.value
  const fields = tank.value?.fields ?? []
  if (!current?.name.trim()) return 'Enter a process name.'
  for (const field of fields) {
    const setting = current.fields[field.key]
    if (typeof setting.min !== 'number' || typeof setting.max !== 'number' || Number.isNaN(setting.min) || Number.isNaN(setting.max)) {
      return `${field.label}: enter both ends of the allowed range.`
    }
    if (setting.min < field.plcMin || setting.max > field.plcMax) {
      return `${field.label}: the range must stay within the PLC limit of ${field.plcMin}–${field.plcMax} ${field.unit}.`
    }
    if (setting.min > setting.max) return `${field.label}: the lowest value must not be above the highest.`
  }
  return ''
})

function load() {
  const current = tank.value
  if (!current) return
  draft.value = {
    name: current.name,
    fields: Object.fromEntries(current.fields.map(field => [field.key, { editable: field.editable, min: field.min, max: field.max }])),
  }
  baseline.value = JSON.stringify(draft.value)
}

function choose(id: number) {
  if (id === selected.value || !confirmDiscard()) return
  selected.value = id
}

watch(selected, load)

async function save() {
  if (!draft.value || invalid.value || !dirty.value) return
  if (await props.command({ action: 'tank-setup', tank: selected.value, setup: draft.value })) {
    baseline.value = JSON.stringify(draft.value)
  }
}

async function restoreDefaults() {
  if (!window.confirm(`Restore tank #${selected.value} to its default name, settings and ranges?`)) return
  if (await props.command({ action: 'tank-setup-default', tank: selected.value })) {
    // The status that comes back carries the restored setup.
    setTimeout(load, 0)
  }
}

function lockedCount(id: number) {
  return props.status.tanks.find(item => item.id === id)?.fields.filter(field => !field.editable).length ?? 0
}

function changeScenario(change: { quality?: Quality; lowWater?: boolean }) {
  const scenario = props.status.scenario
  if (!scenario) return
  void props.command({
    action: 'simulation',
    quality: change.quality ?? props.status.plc.quality,
    lowWater: change.lowWater ?? scenario.lowWater,
  })
}

onMounted(() => {
  load()
  setUnsavedCheck(() => dirty.value)
})
onUnmounted(() => setUnsavedCheck(undefined))
</script>

<template>
  <div class="settings-page">
    <section class="panel tank-list-panel" aria-labelledby="tanks-title">
      <div class="panel-heading">
        <div>
          <h2 id="tanks-title">Tanks</h2>
          <p>Choose a tank to change its setup.</p>
        </div>
      </div>
      <ul class="tank-list">
        <li v-for="item in status.tanks" :key="item.id">
          <button type="button" :class="{ 'is-open': item.id === selected }" :aria-current="item.id === selected ? 'true' : undefined" @click="choose(item.id)">
            <span class="tank-list-number">{{ String(item.id).padStart(2, '0') }}</span>
            <span class="tank-list-name">{{ item.name }}</span>
            <span class="tank-list-meta">{{ item.fields.map(field => field.label.split(' ')[0]).join(' · ') }}</span>
            <span v-if="lockedCount(item.id)" class="chip">{{ lockedCount(item.id) }} locked</span>
          </button>
        </li>
      </ul>
    </section>

    <form v-if="tank && draft" class="panel tank-setup" aria-labelledby="setup-title" @submit.prevent="save">
      <div class="recipe-form-head">
        <div class="recipe-form-title">
          <p class="eyebrow">Tank #{{ tank.id }} setup<span v-if="dirty" class="unsaved"> · Unsaved changes</span></p>
          <h2 id="setup-title">{{ draft.name.trim() || 'Unnamed tank' }}</h2>
        </div>
        <div class="recipe-form-actions">
          <button type="button" class="button ghost" :disabled="disabled" @click="restoreDefaults">Restore defaults</button>
          <button v-if="dirty" type="button" class="button secondary" @click="load">Undo changes</button>
          <button type="submit" class="button primary large" :disabled="disabled || !dirty || !!invalid">Save tank setup</button>
        </div>
      </div>

      <p v-if="!connected" class="banner banner-alert" role="status">Line service disconnected. Reconnect to save.</p>
      <p v-else-if="active" class="banner banner-caution" role="status">A run or AGV move is in progress. Save once it finishes.</p>

      <fieldset class="setup-body" :disabled="disabled">
        <label class="field setup-name">
          <span>Process name</span>
          <input v-model="draft.name" maxlength="40" required>
          <small>Shown on the map, in recipes and on every page.</small>
        </label>

        <h3>Settings on this tank</h3>
        <p class="muted setup-intro">The PLC decides which settings this tank has. Here you choose whether operators may change each one in Manual, and the range they may use. Recipes must also stay within the range.</p>

        <div class="setup-fields">
          <div v-for="field in tank.fields" :key="field.key" class="setup-field" :class="{ 'is-locked': !draft.fields[field.key].editable }">
            <div class="setup-field-head">
              <strong>{{ field.label }} <span class="muted">({{ field.unit }})</span></strong>
              <label class="switch">
                <input v-model="draft.fields[field.key].editable" type="checkbox">
                <span>{{ draft.fields[field.key].editable ? 'Operators can change it in Manual' : 'Locked: shown, but not changeable' }}</span>
              </label>
            </div>
            <div class="setup-range">
              <label>
                <span>Lowest allowed</span>
                <span class="input-unit"><input v-model.number="draft.fields[field.key].min" type="number" :step="field.step" :min="field.plcMin" :max="field.plcMax" required><span>{{ field.unit }}</span></span>
              </label>
              <label>
                <span>Highest allowed</span>
                <span class="input-unit"><input v-model.number="draft.fields[field.key].max" type="number" :step="field.step" :min="field.plcMin" :max="field.plcMax" required><span>{{ field.unit }}</span></span>
              </label>
              <p class="muted">PLC limit {{ field.plcMin }}–{{ field.plcMax }} {{ field.unit }}<br>Now set to {{ field.value === null ? 'unavailable' : `${field.value} ${field.unit}` }}</p>
            </div>
          </div>
        </div>
        <p v-if="invalid && dirty" class="banner banner-caution" role="status">{{ invalid }}</p>
      </fieldset>
    </form>

    <div class="settings-side">
      <section class="panel help-panel" aria-labelledby="help-title">
        <div class="panel-heading"><h2 id="help-title">How setup is used</h2></div>
        <ul class="help-list">
          <li><strong>Process name</strong> replaces the label on the map, in recipes and on the Run and Manual pages.</li>
          <li><strong>Locked</strong> settings stay visible to operators, but only Settings can unlock them.</li>
          <li><strong>Allowed range</strong> limits what operators can enter in Manual and what recipes may use.</li>
          <li>Changes are saved on the line computer and kept after a restart.</li>
        </ul>
      </section>

      <section v-if="status.scenario" class="panel simulation-panel" aria-labelledby="simulation-title">
        <div class="panel-heading">
          <div>
            <h2 id="simulation-title">Simulation</h2>
            <p>Test conditions for the simulated line. Timers run at {{ status.scenario.timeScale }}× speed.</p>
          </div>
        </div>
        <div class="simulation-content">
          <label class="check">
            <input type="checkbox" :checked="status.scenario.lowWater" :disabled="disabled" @change="changeScenario({ lowWater: ($event.currentTarget as HTMLInputElement).checked })">
            Tank #1 low water alarm
          </label>
          <label class="scenario-select">
            <span>PLC readings</span>
            <select :value="status.plc.quality" :disabled="disabled" @change="changeScenario({ quality: ($event.currentTarget as HTMLSelectElement).value as Quality })">
              <option value="live">Live</option>
              <option value="stale">Stale</option>
              <option value="unavailable">Unavailable</option>
            </select>
          </label>
          <button type="button" class="button secondary" :disabled="disabled" @click="command({ action: 'reset' })">Reset simulation</button>
        </div>
      </section>
    </div>
  </div>
</template>
