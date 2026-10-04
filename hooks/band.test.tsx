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
  await band.press({ key: 'hide' })
  expect(await band.find({ text: /Godot/ })).toBeUndefined()
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
