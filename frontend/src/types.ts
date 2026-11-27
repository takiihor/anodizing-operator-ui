export type Mode = 'automatic' | 'manual'
export type Quality = 'live' | 'stale' | 'unavailable'

export type AgvAction = 'connect' | 'disconnect' | 'arm' | 'disarm' | 'query_status' | 'stop'
  | 'forward' | 'reverse' | 'rotate_clockwise' | 'rotate_counterclockwise'

export interface AgvAuthority {
  controller: string
  connectionId: number
  stopGeneration: number
}

export interface AgvStatus {
  setupUrl: string | null
  linkMode?: 'simulator' | 'serial' | 'wired'
  canConnect?: boolean
  canDisconnect?: boolean
  authority: AgvAuthority
  sequence: number
  connected: boolean
  transport: 'serial' | 'simulator' | 'wired' | null
  armed: boolean
  fresh: boolean
  motion: 'moving' | 'stopped' | 'unknown'
  speed: number | null
  fault: number | null
  statusAge: number | null
  motionBlock: string | null
  error: string | null
  canStop: boolean
  canQuery: boolean
  haltPending: boolean
  results: Partial<Record<AgvAction, { id: number; state: string; acknowledged: boolean; confirmedStopped: boolean }>>
}

export type JobGatewayStatus = { configured: false } | {
  configured: true
  health: { build: string; protocol: number; enabled: boolean; simulated: boolean; motionEnabled: boolean }
  status: { owner: string | null; armed: boolean; activeJobId: string | null; recoveryRequired: boolean }
  jobs: { job_id?: string; type?: string; state?: string }[]
}

export interface MockStationVisit {
  tanks: number[]
  busy: boolean
  job: {
    jobId: string
    tank: number
    station: number
    state: 'claiming' | 'waiting_for_arm' | 'submitting' | 'checking' | 'accepted' | 'running'
      | 'done' | 'stopped' | 'refused' | 'failed' | 'unknown'
    message: string | null
  } | null
}

export interface Field {
  key: string
  label: string
  unit: string
  /** Allowed range set in Settings (within the PLC limits). */
  min: number
  max: number
  /** Hard limits from the PLC catalog. */
  plcMin: number
  plcMax: number
  /** Whether operators can change this setting in Manual. */
  editable: boolean
  step: number
  tag: string
  value: number | null
}

export interface TankFieldSetup {
  editable: boolean
  min: number
  max: number
}

export interface TankSetupBody {
  name: string
  fields: Record<string, TankFieldSetup>
}

export interface RecipeLibrary {
  recipes: Recipe[]
  selected: string
}

export type Page = 'run' | 'manual' | 'recipes' | 'settings' | 'agv'

export type TankControlId = 'cycle' | 'filter' | 'heater' | 'ultrasonic' | 'door' | 'swing' | 'rectifier' | 'pressure'
export type DoorPilotAction = 'open' | 'close' | 'clear' | 'resolve'

export interface TankControl {
  id: TankControlId
  label: string
  active: boolean | null
}

export interface Tank {
  id: number
  name: string
  temperature: number | null
  timer: number | null
  timerDone: boolean | null
  doorPosition?: 'Open' | 'Closed' | 'Between limits' | 'Conflicting feedback' | 'Unavailable' | null
  alarm: string | null
  details: { label: string; value: string }[]
  fields: Field[]
  controls: TankControl[]
}

export interface RecipeStep {
  tank: number
  temperature: number | null
  dwell: number
  plcTime: number
}

export interface RecipeSummary {
  id: string
  name: string
}

export interface Recipe extends RecipeSummary {
  revision: number
  part: string
  date: string
  steps: RecipeStep[]
}

export interface LineStatus {
  mode: Mode | null
  simulation: boolean
  plc: { quality: Quality; sampleTime: string | null; remote: boolean | null }
  doorPilot?: { tank: number; revision: string | null; nextAction: DoorPilotAction | null; message: string }
  agv: { station: number | null; stationConfirmed: boolean; destination: number | null; moving: boolean }
  manualTravel?: { durationMs: number; remainingMs: number } | null
  mockStationVisit?: MockStationVisit
  scenario: { lowWater: boolean; timeScale: number } | null
  run: {
    state: 'idle' | 'running' | 'stopped' | 'complete'
    step: number | null
    nextTank: number | null
    phase: 'travel' | 'dwell' | null
    blockers: string[]
  }
  tanks: Tank[]
  recipe: Recipe
  recipes: RecipeSummary[]
  recipeStepLimit: number
}

export type Command =
  | { action: 'mode'; mode: Mode }
  | { action: 'settings'; tank: number; values: Record<string, number> }
  | { action: 'tank-control'; tank: number; control: TankControlId; active: boolean }
  | { action: 'plc-door'; tank: number; operation: DoorPilotAction; revision: string; confirmed: true }
  | { action: 'move'; tank: number }
  | { action: 'simulation'; lowWater: boolean; quality: Quality }
  | { action: 'select-recipe'; id: string }
  | { action: 'save-recipe'; id: string | null; revision: number | null; name: string; part: string; steps: RecipeStep[] }
  | { action: 'delete-recipe'; id: string; revision: number }
  | { action: 'tank-setup'; tank: number; setup: TankSetupBody }
  | { action: 'tank-setup-default'; tank: number }
  | { action: 'start'; recipeId: string; revision: number }
  | { action: 'stop' | 'reset' }
