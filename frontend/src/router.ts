import { ref } from 'vue'
import type { Page } from './types'

export const PATHS: Record<Page, string> = {
  run: '/',
  manual: '/manual',
  recipes: '/recipes',
  settings: '/settings',
  agv: '/agv',
}

export const TITLES: Record<Page, string> = {
  run: 'Run',
  manual: 'Manual',
  recipes: 'Recipes',
  settings: 'Settings',
  agv: 'AGV jog',
}

function pageFor(path: string): Page {
  return (Object.entries(PATHS).find(([, value]) => value === path)?.[0] as Page | undefined) ?? 'run'
}

export const page = ref<Page>(pageFor(window.location.pathname))

let unsaved: (() => boolean) | undefined

/** A page with editable forms registers a check that reports unsaved changes. */
export function setUnsavedCheck(check: (() => boolean) | undefined) {
  unsaved = check
}

export function confirmDiscard() {
  return !unsaved?.() || window.confirm('You have unsaved changes. Discard them?')
}

function updateTitle() {
  document.title = `${TITLES[page.value]} | Anodizing line`
}
updateTitle()

export function navigate(target: Page, { replace = false } = {}) {
  if (target === page.value) return true
  if (!confirmDiscard()) return false
  unsaved = undefined
  const url = PATHS[target]
  if (replace) window.history.replaceState(null, '', url)
  else window.history.pushState(null, '', url)
  page.value = target
  updateTitle()
  return true
}

window.addEventListener('popstate', () => {
  const target = pageFor(window.location.pathname)
  if (!confirmDiscard()) {
    window.history.pushState(null, '', PATHS[page.value])
    return
  }
  unsaved = undefined
  page.value = target
  updateTitle()
})

window.addEventListener('beforeunload', event => {
  if (unsaved?.()) event.preventDefault()
})
