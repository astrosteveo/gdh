import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { GodotView } from '../types'

// A band above the prompt for a session in a Godot project: whether an editor runs gdh's bridge, which scene is
// open, which have unsaved changes, whether the game runs, and how many editor errors wait for Claude's next bridge
// command. It reads the bridge with a peek, which leaves those errors for Claude.

const view = atom({ plugin: 'gdh', key: 'view' } as const, { kind: 'none' } as GodotView)
const hidden = atom({ plugin: 'gdh', key: 'hidden' } as const, false)
const POLL_MS = 3000

type BridgeInfo = { port: number; token: string }
type BridgeStatus = {
  ok: boolean
  godot: string
  headless: boolean
  current_scene: string
  unsaved_scenes: string[]
  playing: boolean
  playing_scene: string
  pending_errors?: number
}

const parent = (dir: string) => dir.replace(/\/[^/]+\/?$/, '') || '/'

async function findProject($: EngineInterface, start: string): Promise<string | null> {
  let dir = start
  for (let i = 0; i < 40; i++) {
    if (await $.fs.exists(`${dir}/project.godot`)) return dir
    const up = parent(dir)
    if (up === dir) return null
    dir = up
  }
  return null
}

const short = (res: string) => res.replace(/^res:\/\//, '')

async function poll($: EngineInterface, project: string | null): Promise<void> {
  if (project === null) return
  let next: GodotView = { kind: 'no-bridge', project }
  try {
    const info = JSON.parse((await $.fs.read(`${project}/.godot/gdh_bridge.json`)) as string) as BridgeInfo
    const res = await $.http.fetch(`http://127.0.0.1:${info.port}/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Gdh-Token': info.token },
      body: JSON.stringify({ cmd: 'status', peek: true }),
    })
    const s = JSON.parse(res.text) as BridgeStatus
    if (s.ok) {
      next = {
        kind: 'bridge',
        project,
        godot: s.godot,
        headless: s.headless,
        current: s.current_scene,
        unsaved: s.unsaved_scenes,
        playing: s.playing ? s.playing_scene || 'the main scene' : null,
        errors: s.pending_errors ?? 0,
      }
    }
  } catch {
    // No info file, or no editor answering it: no bridge.
  }
  const before = JSON.stringify(await read($, view))
  if (JSON.stringify(next) !== before) await update($, view, () => next)
}


export const register: Register = on => {
  let project: string | null = null

  on('session.start', async ($, e, next) => {
    const result = await next(e)
    project = await findProject($, await $.session.root())
    if (project !== null) {
      await poll($, project)
      $.clock.every(POLL_MS, () => poll($, project))
    }
    return result
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const v = await read($, view)
    if (e.props.hasSurvey || v.kind === 'none' || (await read($, hidden))) return next(e)
    const { Box, Button, Text } = $.ui.resolve(e)
    const hide = <Button key="hide" label="Hide" onPress={() => update($, hidden, () => true)} />
    if (v.kind === 'no-bridge') {
      return (
        <Box>
          <Text dimColor>Godot: no editor bridge (gdh bridge start, or enable the gdh bridge addon) </Text>
          {hide}
        </Box>
      )
    }
    const where = v.headless ? "gdh's headless editor" : 'editor'
    return (
      <Box>
        <Text dimColor>Godot {v.godot.replace(/-.*/, '')} · {where}</Text>
        {v.current ? <Text dimColor> · {short(v.current)}</Text> : null}
        {v.unsaved.length > 0 ? <Text color="yellow"> · unsaved: {v.unsaved.map(short).join(', ')}</Text> : null}
        {v.playing ? <Text color="green"> · ▶ {short(v.playing)}</Text> : null}
        {v.errors > 0 ? <Text color="red"> · {v.errors} editor error{v.errors === 1 ? '' : 's'}</Text> : null}
        <Text> </Text>
        {hide}
      </Box>
    )
  })
}
