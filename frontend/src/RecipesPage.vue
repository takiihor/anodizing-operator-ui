<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import TrackMap from './TrackMap.vue'
import { confirmDiscard, setUnsavedCheck } from './router'
import type { Command, LineStatus, Recipe, RecipeLibrary, RecipeStep } from './types'

interface DraftStep {
  key: number
  tank: number
  dwell: number | ''
  plcTime: number | ''
  temperature: number | ''
}

interface Draft {
  id: string | null
  revision: number | null
  name: string
  part: string
  steps: DraftStep[]
}

const props = defineProps<{
  status: LineStatus
  connected: boolean
  disabled: boolean
  active: boolean
  command: (action: Command) => Promise<boolean>
  loadLibrary: () => Promise<RecipeLibrary>
}>()

const library = ref<Recipe[]>([])
const loadError = ref('')
const draft = ref<Draft>()
const baseline = ref('')
const stepList = ref<HTMLDivElement>()
let rowKey = 0

const dirty = computed(() => draft.value !== undefined && snapshot(draft.value) !== baseline.value)
const inUse = computed(() => props.status.recipe.id)
const totalProcess = computed(() => draft.value?.steps.reduce((sum, step) => sum + (step.dwell === '' ? 0 : Number(step.dwell)), 0) ?? 0)
const atLimit = computed(() => (draft.value?.steps.length ?? 0) >= props.status.recipeStepLimit)
const canSave = computed(() => !props.disabled && !!draft.value && !!draft.value.name.trim() && draft.value.steps.length > 0 && (dirty.value || draft.value.id === null))

function snapshot(value: Draft) {
  return JSON.stringify({ ...value, steps: value.steps.map(({ key: _key, ...step }) => step) })
}

function field(tank: number, key: string) {
  return props.status.tanks.find(item => item.id === tank)?.fields.find(item => item.key === key)
}

function tankName(tank: number) {
  return props.status.tanks.find(item => item.id === tank)?.name ?? ''
}

function edit(recipe: Recipe | null, copy = false) {
  const steps: DraftStep[] = (recipe?.steps ?? []).map(step => ({ ...step, temperature: step.temperature ?? '', key: rowKey++ }))
  const next: Draft = {
    id: copy || !recipe ? null : recipe.id,
    revision: copy || !recipe ? null : recipe.revision,
    name: !recipe ? '' : copy ? `${recipe.name} (copy)` : recipe.name,
    part: recipe?.part ?? '',
    steps,
  }
  draft.value = next
  baseline.value = recipe && !copy ? snapshot(next) : ''
}

function open(recipe: Recipe | null, copy = false) {
  if (!confirmDiscard()) return
  edit(recipe, copy)
}

async function reload(selectId?: string | null) {
  try {
    const data = await props.loadLibrary()
    library.value = data.recipes
    loadError.value = ''
    const target = data.recipes.find(recipe => recipe.id === (selectId ?? draft.value?.id ?? data.selected))
      ?? data.recipes.find(recipe => recipe.id === data.selected)
    edit(target ?? null)
  } catch (failure) {
    loadError.value = failure instanceof Error ? failure.message : 'Could not load the saved recipes.'
  }
}

function newStep(tank: number): DraftStep {
  const existing = draft.value?.steps.find(step => step.tank === tank)
  if (existing) return { ...existing, key: rowKey++ }
  const time = field(tank, 'time')
  const temperature = field(tank, 'temperature')
  const plcTime = Math.min(Math.max(time?.value ?? 0, time?.min ?? 0), time?.max ?? 9999)
  return {
    key: rowKey++, tank, dwell: plcTime, plcTime,
    temperature: temperature?.value === null || temperature === undefined
      ? '' : Math.min(Math.max(temperature.value, temperature.min), temperature.max),
  }
}

async function addStep(tank: number) {
  if (!draft.value || atLimit.value || props.disabled) return
  draft.value.steps.push(newStep(tank))
  await nextTick()
  if (stepList.value) stepList.value.scrollTop = stepList.value.scrollHeight
}

function replaceTank(index: number, event: Event) {
  if (!draft.value) return
  const tank = Number((event.currentTarget as HTMLSelectElement).value)
  draft.value.steps[index] = { ...newStep(tank), key: draft.value.steps[index].key }
}

function moveStep(index: number, offset: number) {
  if (!draft.value) return
  const [step] = draft.value.steps.splice(index, 1)
  draft.value.steps.splice(index + offset, 0, step)
}

// Every visit to a tank shares its PLC time and temperature targets.
function setTankTarget(tank: number, key: 'plcTime' | 'temperature', event: Event) {
  const input = event.currentTarget as HTMLInputElement
  const value = input.value === '' ? '' : input.valueAsNumber
  for (const step of draft.value?.steps ?? []) {
    if (step.tank === tank) step[key] = value
  }
}

async function save() {
  const current = draft.value
  if (!current || !canSave.value) return
  const steps: RecipeStep[] = current.steps.map(step => ({
    tank: step.tank,
    dwell: Number(step.dwell),
    plcTime: Number(step.plcTime),
    temperature: step.temperature === '' ? null : Number(step.temperature),
  }))
  if (await props.command({
    action: 'save-recipe', id: current.id, revision: current.revision,
    name: current.name, part: current.part, steps,
  })) {
    // Saving also selects the recipe for the next run.
    baseline.value = snapshot(current)
    await reload()
  }
}

async function remove() {
  const current = draft.value
  if (!current?.id || current.revision === null) return
  if (!window.confirm(`Delete the recipe "${current.name}"? This cannot be undone.`)) return
  if (await props.command({ action: 'delete-recipe', id: current.id, revision: current.revision })) {
    baseline.value = snapshot(current)
    await reload()
  }
}

async function use(recipe: Recipe) {
  await props.command({ action: 'select-recipe', id: recipe.id })
}

function duration(seconds: number) {
  const minutes = Math.floor(seconds / 60)
  return minutes ? `${minutes} min${seconds % 60 ? ` ${seconds % 60} s` : ''}` : `${seconds} s`
}

onMounted(() => {
  setUnsavedCheck(() => dirty.value)
  void reload()
})
onUnmounted(() => setUnsavedCheck(undefined))
</script>

<template>
  <div class="recipes-page">
    <section class="panel recipe-library" aria-labelledby="library-title">
      <div class="panel-heading">
        <div>
          <h2 id="library-title">Recipes</h2>
          <p>Each recipe is the tank order for one anodizing process.</p>
        </div>
      </div>
      <div class="library-actions">
        <button type="button" class="button primary full" :disabled="disabled" @click="open(null)">+ New recipe</button>
      </div>
      <p v-if="loadError" class="banner banner-alert">{{ loadError }}</p>
      <ul class="library-list">
        <li v-for="recipe in library" :key="recipe.id">
          <button
            type="button"
            class="library-item"
            :class="{ 'is-open': draft?.id === recipe.id }"
            :aria-current="draft?.id === recipe.id ? 'true' : undefined"
            @click="open(recipe)"
          >
            <span class="library-name">{{ recipe.name }}</span>
            <span class="library-meta">{{ recipe.steps.length }} steps<template v-if="recipe.part"> · {{ recipe.part }}</template></span>
            <span v-if="recipe.id === inUse" class="chip chip-info">Selected for Run</span>
          </button>
        </li>
      </ul>
    </section>

    <form v-if="draft" class="panel recipe-form" aria-labelledby="editor-title" @submit.prevent="save">
      <div class="recipe-form-head">
        <div class="recipe-form-title">
          <p class="eyebrow">{{ draft.id ? 'Edit recipe' : 'New recipe' }}<span v-if="dirty" class="unsaved"> · Unsaved changes</span></p>
          <h2 id="editor-title">{{ draft.name.trim() || 'Untitled recipe' }}</h2>
        </div>
        <div class="recipe-form-actions">
          <button v-if="draft.id && draft.id !== inUse && !dirty" type="button" class="button secondary" :disabled="disabled" @click="use(library.find(item => item.id === draft!.id)!)">Select for Run</button>
          <button v-if="draft.id" type="button" class="button ghost" :disabled="disabled" @click="open(library.find(item => item.id === draft!.id)!, true)">Duplicate</button>
          <button v-if="draft.id" type="button" class="button ghost danger-text" :disabled="disabled || library.length < 2" :title="library.length < 2 ? 'Keep at least one recipe' : undefined" @click="remove">Delete</button>
          <button v-if="dirty" type="button" class="button secondary" @click="edit(draft.id ? library.find(item => item.id === draft!.id) ?? null : null)">Undo changes</button>
          <button type="submit" class="button primary large" :disabled="!canSave">Save recipe</button>
        </div>
      </div>

      <p v-if="!connected" class="banner banner-alert" role="status">Line service disconnected. Reconnect to save.</p>
      <p v-else-if="active" class="banner banner-caution" role="status">A run or AGV move is in progress. You can review recipes, and save once it finishes.</p>

      <fieldset class="recipe-fields" :disabled="disabled">
        <label class="field">
          <span>Recipe name</span>
          <input v-model="draft.name" maxlength="80" required placeholder="e.g. Clear anodize, 10 µm">
        </label>
        <label class="field">
          <span>Description <small>(optional)</small></span>
          <input v-model="draft.part" maxlength="120" placeholder="e.g. Al part 600 × 400 mm">
        </label>
      </fieldset>

      <div class="steps-head">
        <h3>Tank order <span class="muted">· {{ draft.steps.length }} {{ draft.steps.length === 1 ? 'step' : 'steps' }} · {{ duration(totalProcess) }} process time</span></h3>
        <span class="muted">Click tanks on the map to add steps.</span>
      </div>
      <fieldset class="steps-fieldset" :disabled="disabled">
        <div v-if="draft.steps.length" ref="stepList" class="step-scroll" tabindex="0" aria-label="Recipe steps">
          <table class="recipe-edit-table">
            <thead>
              <tr>
                <th scope="col">Step</th>
                <th scope="col">Tank</th>
                <th scope="col">Process time (s)</th>
                <th scope="col">PLC time (s)</th>
                <th scope="col">Temperature (°C)</th>
                <th scope="col"><span class="sr-only">Order</span></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(step, index) in draft.steps" :key="step.key">
                <th scope="row">{{ index + 1 }}</th>
                <td class="step-tank">
                  <select :aria-label="`Tank for step ${index + 1}`" :value="step.tank" @change="replaceTank(index, $event)">
                    <option v-for="tank in status.tanks" :key="tank.id" :value="tank.id">#{{ tank.id }} {{ tank.name }}</option>
                  </select>
                </td>
                <td>
                  <input v-model.number="step.dwell" :aria-label="`Process time for step ${index + 1}`" type="number" min="0" :max="step.plcTime === '' ? 9999 : step.plcTime" required>
                </td>
                <td>
                  <input
                    :value="step.plcTime" :aria-label="`PLC time for step ${index + 1}`" type="number"
                    :min="Math.max(field(step.tank, 'time')?.min ?? 0, step.dwell === '' ? 0 : step.dwell)" :max="field(step.tank, 'time')?.max ?? 9999"
                    :title="`Allowed ${field(step.tank, 'time')?.min ?? 0}–${field(step.tank, 'time')?.max ?? 9999} s, at least the process time`"
                    required @input="setTankTarget(step.tank, 'plcTime', $event)"
                  >
                </td>
                <td>
                  <input
                    v-if="field(step.tank, 'temperature')"
                    :value="step.temperature" :aria-label="`Temperature for step ${index + 1}`" type="number" step="0.1"
                    :min="field(step.tank, 'temperature')!.min" :max="field(step.tank, 'temperature')!.max"
                    :title="`Allowed ${field(step.tank, 'temperature')!.min}–${field(step.tank, 'temperature')!.max} °C`"
                    @input="setTankTarget(step.tank, 'temperature', $event)"
                  >
                  <span v-else class="muted">No heater</span>
                </td>
                <td class="step-order">
                  <div class="step-actions">
                    <button class="button icon" type="button" :aria-label="`Move step ${index + 1} up`" :disabled="index === 0" @click="moveStep(index, -1)">↑</button>
                    <button class="button icon" type="button" :aria-label="`Move step ${index + 1} down`" :disabled="index === draft.steps.length - 1" @click="moveStep(index, 1)">↓</button>
                    <button class="button icon danger-text" type="button" :aria-label="`Remove step ${index + 1}`" @click="draft.steps.splice(index, 1)">✕</button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="recipe-empty">
          <strong>No steps yet.</strong>
          <span>Click the first tank on the map. Then click the next tanks in the order the AGV should visit them.</span>
        </div>
      </fieldset>
      <p class="form-footnote">PLC time and temperature apply to every visit to the same tank. PLC time must be at least the process time.</p>
    </form>
    <section v-else class="panel recipe-form recipe-empty">Loading recipes…</section>

    <div class="recipe-side">
      <TrackMap
        :status="status"
        :selected="null"
        :connected="connected"
        :sequence="draft?.steps.map(step => step.tank) ?? []"
        :pick="true"
        title="Add tanks"
        :hint="atLimit ? `A recipe holds up to ${status.recipeStepLimit} steps.` : 'Click a tank to add it as the next step. Numbers show the visit order.'"
        @select="addStep"
      >
        <template #actions>
          <button type="button" class="button ghost" :disabled="disabled || !draft?.steps.length" @click="draft?.steps.pop()">Undo last step</button>
        </template>
      </TrackMap>
      <section v-if="draft" class="panel route-summary" aria-label="Visit order">
        <div class="panel-heading"><h2>Visit order</h2></div>
        <ol v-if="draft.steps.length" class="route-chips">
          <li v-for="(step, index) in draft.steps" :key="step.key">
            <span class="route-step">{{ index + 1 }}</span>#{{ step.tank }} {{ tankName(step.tank) }}
          </li>
        </ol>
        <p v-else class="muted route-empty">The visit order appears here as you add tanks.</p>
      </section>
    </div>
  </div>
</template>
