/**
 * What the Godot band shows: nothing, a warning that a Godot editor has the session's project open without the bridge,
 * or the state of the bridge's editor.
 */
export type GodotView =
  | { kind: 'none' }
  | { kind: 'editor-without-bridge'; project: string }
  | {
      kind: 'bridge'
      project: string
      godot: string
      headless: boolean
      current: string
      unsaved: string[]
      playing: string | null
      errors: number
    }

declare module 'claude-code' {
  interface PluginState {
    gdh: { view: GodotView; hidden: boolean }
  }
}
