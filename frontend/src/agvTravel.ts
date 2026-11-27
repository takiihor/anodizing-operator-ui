import type { GatewayAgvFeedback } from './types'

export interface LiveAgvTravel {
  from: number
  to: number
  startedAt: number
  durationMs: number
  startProgress: number
  arriving: boolean
}

export interface AgvTracking {
  lastTank: number | null
  travel: LiveAgvTravel | null
}

export const emptyTracking: AgvTracking = { lastTank: null, travel: null }

export function estimatedProgress(travel: LiveAgvTravel, now: number) {
  const limit = travel.arriving ? 1 : 0.9
  const elapsed = Math.max(0, now - travel.startedAt)
  return Math.min(limit, travel.startProgress + (limit - travel.startProgress) * elapsed / travel.durationMs)
}

export function updateTracking(previous: AgvTracking, feedback: GatewayAgvFeedback | null, now: number): AgvTracking {
  if (!feedback?.fresh) return emptyTracking

  const { lastTank, travel } = previous
  if (feedback.arrived) {
    const tank = feedback.tank
    if (tank === null) return emptyTracking
    if (lastTank !== null && tank !== lastTank) {
      const approaching = travel?.from === lastTank && travel.to === tank
      return { lastTank: tank, travel: {
        from: lastTank, to: tank, startedAt: now, durationMs: approaching ? 450 : 900,
        startProgress: approaching ? estimatedProgress(travel, now) : 0, arriving: true,
      } }
    }
    if (travel?.arriving && travel.to === tank && estimatedProgress(travel, now) < 1) return previous
    return { lastTank: tank, travel: null }
  }

  const target = feedback.targetTank
  if (feedback.motion === 'moving' && lastTank !== null && target !== null && target !== lastTank) {
    if (travel?.from === lastTank && travel.to === target && !travel.arriving) return previous
    return { lastTank, travel: {
      from: lastTank, to: target, startedAt: now, durationMs: Math.min(16000, 2800 + 1200 * Math.abs(target - lastTank)),
      startProgress: 0, arriving: false,
    } }
  }
  return { lastTank, travel: null }
}
