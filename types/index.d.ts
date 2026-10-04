/** What the Godot band shows: the project Claude Code's session is in, and its editor bridge's state. */
export type GodotView =
  | { kind: 'none' }
  | { kind: 'no-bridge'; project: string }
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
