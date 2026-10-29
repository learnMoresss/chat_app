declare module '*neural-demo-engine.js'
declare module '*neural-world-engine.js'
declare module '*neural-brain.js'
declare module '*neural-flow.js'
declare module '*neural-world-view.js'
declare module '*live-adapter.js'

declare module '*neural-ui.js' {
  export type NeuralMountOptions = {
    mode?: 'demo' | 'monitor'
    personaName?: string
    fetchLive?: () => Promise<{
      board: Record<string, unknown>
      worldline: Record<string, unknown>
      traces: Array<Record<string, unknown>>
    }>
  }
  export function mountNeuralField(
    rootEl?: HTMLElement | null,
    options?: NeuralMountOptions,
  ): () => void
}
