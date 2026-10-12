import { expect, test } from 'claude-code/testing'

const BAND = { component: 'AbovePrompt', props: { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns: 100, scroll: { offset: 0, bodyRows: 10 }, view: {} } } as const

const STATUS = {
  ok: true,
  godot: '4.7.2-stable (arch_linux)',
  headless: false,
  current_scene: 'res://levels/level_1.tscn',
  unsaved_scenes: ['res://levels/level_1.tscn'],
  playing: true,
  playing_scene: 'res://main.tscn',
  pending_errors: 2,
}

test('the band shows the editor, the unsaved scene, the running game and waiting errors', async ($, on) => {
  let bridgeUp = true
  const asked: unknown[] = []
  on('session.start', async (_$, e) => ({ cwd: e.cwd }))
  on('session.root', async () => ({ value: '/home/me/game/src' }))
  on('fs.exists', async (_$, e) => ({ value: e.path === '/home/me/game/project.godot' }))
  on('fs.read', async (_$, e) => {
    if (e.path !== '/home/me/game/.godot/gdh_bridge.json' || !bridgeUp) throw new Error('no such file')
    return { value: JSON.stringify({ port: 47800, token: 't0k' }) }
  })
  on('http.fetch', async (_$, e) => {
    asked.push(JSON.parse(e.init?.body ?? '{}'))
    return { value: { status: 200, ok: true, headers: {}, text: JSON.stringify(STATUS) } }
  })
  on('clock.every', async () => ({ value: undefined }))
  on('ui.render', { component: 'AbovePrompt' }, async ($$, e) => {
    const { Box } = $$.ui.resolve(e)
    return <Box key="engine" />
  })

  await $.session.start({ cwd: '/home/me/game/src', surface: 'terminal', source: 'startup' } as never)
  expect(asked).toEqual([{ cmd: 'status', peek: true }])

  const band = await $.ui.mount({ plugin: 'gdh', surface: 'terminal', ...BAND })
  expect(await band.find({ text: /Godot 4\.7\.2 · editor/ })).toBeDefined()
  expect(await band.find({ text: /unsaved: levels\/level_1\.tscn/ })).toBeDefined()
  expect(await band.find({ text: /▶ main\.tscn/ })).toBeDefined()
  expect(await band.find({ text: /2 editor errors/ })).toBeDefined()
  // Other mods' bands, drawn beneath, stay visible under this one.
  expect(await band.find({ key: 'engine' })).toBeDefined()
  await band.press({ key: 'hide' })
  expect(await band.find({ text: /Godot/ })).toBeUndefined()
  expect(await band.find({ key: 'engine' })).toBeDefined()
  await band.unmount()
})

test('outside a Godot project the band stays out of the way', async ($, on) => {
  on('session.start', async (_$, e) => ({ cwd: e.cwd }))
  on('session.root', async () => ({ value: '/home/me/website' }))
  on('fs.exists', async () => ({ value: false }))
  on('ui.render', { component: 'AbovePrompt' }, async ($$, e) => {
    const { Box } = $$.ui.resolve(e)
    return <Box key="engine" />
  })
  await $.session.start({ cwd: '/home/me/website', surface: 'terminal', source: 'startup' } as never)
  const band = await $.ui.mount({ plugin: 'gdh', surface: 'terminal', ...BAND })
  expect(await band.find({ text: /Godot/ })).toBeUndefined()
  expect(await band.find({ key: 'engine' })).toBeDefined()
  await band.unmount()
})

// A Godot project at /home/me/game with no bridge, and these processes on the machine: pid to command line and working
// folder. Godot moves into the project's folder as it starts.
function noBridge(on: Parameters<Parameters<typeof test>[1]>[1], procs: Record<string, { argv: string[]; cwd: string }>) {
  on('session.start', async (_$, e) => ({ cwd: e.cwd }))
  on('session.root', async () => ({ value: '/home/me/game' }))
  on('fs.exists', async (_$, e) => ({ value: e.path === '/home/me/game/project.godot' }))
  on('fs.read', async (_$, e) => {
    const m = /^\/proc\/(\d+)\/cmdline$/.exec(e.path)
    if (m && procs[m[1]]) return { value: procs[m[1]].argv.join('\0') + '\0' }
    throw new Error('no such file')
  })
  on('fs.stat', async (_$, e) => {
    const m = /^\/proc\/(\d+)\/cwd$/.exec(e.path)
    const realPath = m ? procs[m[1]]?.cwd : e.path
    return { value: { kind: 'directory', size: 0, mtimeMs: 0, isLink: false, realPath } }
  })
  on('process.run', async () => {
    const lines = Object.entries(procs).map(([pid, p]) => `${pid.padStart(7)} ${p.argv[0].split('/').pop()!.slice(0, 15)}`)
    return { value: { exitCode: 0, stdout: ['      1 systemd', ...lines].join('\n') + '\n', stderr: '' } }
  })
  on('clock.every', async () => ({ value: undefined }))
  on('ui.render', { component: 'AbovePrompt' }, async ($$, e) => {
    const { Box } = $$.ui.resolve(e)
    return <Box key="engine" />
  })
}

test('with no bridge and no editor on the project the band shows nothing', async ($, on) => {
  noBridge(on, {
    // A game run from the editor, gdh's headless Godot, and an editor on another project.
    '4100': { argv: ['/usr/bin/godot', '--path', '/home/me/game', '--remote-debug', 'tcp://127.0.0.1:6007'], cwd: '/home/me/game' },
    '4200': { argv: ['/usr/bin/godot', '--headless', '--editor', '--path', '/home/me/game'], cwd: '/home/me/game' },
    '4300': { argv: ['/usr/bin/godot', '--path', '/home/me/other', '--editor'], cwd: '/home/me/other' },
  })
  await $.session.start({ cwd: '/home/me/game', surface: 'terminal', source: 'startup' } as never)
  const band = await $.ui.mount({ plugin: 'gdh', surface: 'terminal', ...BAND })
  expect(await band.find({ text: /Godot|bridge/ })).toBeUndefined()
  expect(await band.find({ key: 'engine' })).toBeDefined()
  await band.unmount()
})

for (const [how, proc] of [
  ['from the project manager', { argv: ['/usr/bin/godot', '--path', 'game', '--editor'], cwd: '/home/me/game' }],
  ['by its project.godot', { argv: ['/opt/Godot_v4.7-stable_linux.x86_64', 'game/project.godot'], cwd: '/home/me/game' }],
  ['with -e in its folder', { argv: ['godot', '-e'], cwd: '/home/me/game' }],
] as const) {
  test(`an editor opened ${how} without the bridge gets a warning`, async ($, on) => {
    noBridge(on, { '5100': { argv: [...proc.argv], cwd: proc.cwd } })
    await $.session.start({ cwd: '/home/me/game', surface: 'terminal', source: 'startup' } as never)
    const band = await $.ui.mount({ plugin: 'gdh', surface: 'terminal', ...BAND })
    expect(await band.find({ text: /Godot editor has this project open without the gdh bridge/ })).toBeDefined()
    expect(await band.find({ text: /gdh bridge install/ })).toBeDefined()
    await band.unmount()
  })
}
