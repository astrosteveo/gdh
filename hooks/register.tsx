import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { GodotView } from '../types'

// A band above the prompt for a session in a Godot project. With a bridge running it shows which scene is open, which
// have unsaved changes, whether the game runs, and how many editor errors wait for Claude's next bridge command; it
// reads the bridge with a peek, which leaves those errors for Claude. With no bridge it shows nothing, unless a Godot
// editor has the project open: then saving there can overwrite Claude's edits, and the band says how to add the bridge.

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

// Whether a Godot command line opens the editor: `-e`/`--editor`, or a project.godot path. A game, the project manager
// and a headless Godot (gdh's own, among others) don't count.
function opensEditor(argv: readonly string[]): boolean {
  const args = argv.slice(1)
  return !args.includes('--headless') && args.some(a => a === '-e' || a === '--editor' || /(^|\/)project\.godot$/.test(a))
}

// Whether a Godot editor on this machine has the project open, from each Godot process's command line and working
// folder under /proc: Godot moves into the project's folder as it starts. Elsewhere (no /proc) it answers false, which
// shows nothing.
async function editorOpen($: EngineInterface, project: string): Promise<boolean> {
  const real = async (path: string) => (await $.fs.stat(path, { resolve: true })).realPath ?? path
  const { stdout } = await $.process.run(['ps', '-Ao', 'pid=,comm='], { timeoutMs: 5000 })
  const pids = stdout.split('\n').filter(line => /godot/i.test(line)).map(line => line.trim().split(/\s+/)[0])
  if (pids.length === 0) return false
  const want = await real(project)
  for (const pid of pids) {
    try {
      const argv = ((await $.fs.read(`/proc/${pid}/cmdline`)) as string).split('\0').filter(a => a !== '')
      if (opensEditor(argv) && (await real(`/proc/${pid}/cwd`)) === want) return true
    } catch {
      // The process ended, or isn't ours to read.
    }
  }
  return false
}

async function poll($: EngineInterface, project: string | null): Promise<void> {
  if (project === null) return
  let next: GodotView | null = null
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
  if (next === null) {
    let open = false
    try {
      open = await editorOpen($, project)
    } catch {
      // No ps: show nothing.
    }
    next = open ? { kind: 'editor-without-bridge', project } : { kind: 'none' }
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
    if (v.kind === 'editor-without-bridge') {
      return (
        <Box>
          <Text color="yellow">
            The Godot editor has this project open without the gdh bridge, so saving there can overwrite Claude's edits.
            Ask Claude to set up the bridge, or run gdh bridge install and enable gdh bridge in Project Settings {'>'}{' '}
            Plugins.{' '}
          </Text>
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
