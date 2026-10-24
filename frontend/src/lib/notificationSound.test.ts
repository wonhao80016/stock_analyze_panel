import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { activateVoice } from './voiceBroadcast'

vi.mock('./voiceBroadcast', () => ({ activateVoice: vi.fn() }))

// Browsers keep a context suspended unless resume() runs inside a user gesture.
class FakeAudioContext {
  static instances: FakeAudioContext[] = []
  static inGesture = false
  state: 'suspended' | 'running' = 'suspended'
  currentTime = 0
  destination = {}
  oscillators = 0
  resume = vi.fn(() => {
    if (FakeAudioContext.inGesture) this.state = 'running'
    return Promise.resolve()
  })
  constructor() {
    FakeAudioContext.instances.push(this)
  }
  createOscillator() {
    this.oscillators += 1
    return { type: 'sine', frequency: { value: 0 }, connect: vi.fn(), start: vi.fn(), stop: vi.fn() }
  }
  createGain() {
    return {
      gain: { setValueAtTime: vi.fn(), linearRampToValueAtTime: vi.fn(), exponentialRampToValueAtTime: vi.fn() },
      connect: vi.fn(),
    }
  }
}

function userGesture(type: 'pointerdown' | 'keydown') {
  FakeAudioContext.inGesture = true
  try {
    window.dispatchEvent(new Event(type))
  } finally {
    FakeAudioContext.inGesture = false
  }
}

beforeEach(() => {
  vi.resetModules()
  FakeAudioContext.instances = []
  vi.stubGlobal('AudioContext', FakeAudioContext)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

describe('notification sound autoplay unlock', () => {
  it('does not queue tones on a suspended context outside a user gesture', async () => {
    const { playNotificationSound } = await import('./notificationSound')

    playNotificationSound()

    const scheduled = FakeAudioContext.instances.reduce((n, ctx) => n + ctx.oscillators, 0)
    expect(scheduled).toBe(0)
  })

  it('unlocks audio and voice on the first pointer or key gesture, then plays', async () => {
    const { installAudioUnlock, playNotificationSound } = await import('./notificationSound')
    const uninstall = installAudioUnlock()

    userGesture('pointerdown')

    expect(FakeAudioContext.instances).toHaveLength(1)
    const ctx = FakeAudioContext.instances[0]
    expect(ctx.resume).toHaveBeenCalled()
    expect(activateVoice).toHaveBeenCalledTimes(1)

    playNotificationSound()
    expect(ctx.oscillators).toBeGreaterThan(0)

    userGesture('keydown')
    expect(activateVoice).toHaveBeenCalledTimes(1)
    uninstall()
  })
})
