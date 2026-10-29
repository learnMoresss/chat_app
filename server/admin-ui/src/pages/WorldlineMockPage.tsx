import { Alert } from 'antd'
import { useEffect, useRef } from 'react'
import { api } from '../api/client'
import shellHtml from '../neural-field/shell.html?raw'
import '../neural-field/neural-field.css'

type Props = {
  personaId?: string
  personaName?: string
  worldlineEnabled?: boolean
}

type Board = Record<string, unknown>
type WorldlineDetail = Record<string, unknown>
type TurnTraceResp = { traces?: Array<Record<string, unknown>>; latest_id?: string }

/** 世界线主路径监控：board / worldline / turn-trace / 主动意图（只读轮询）。 */
export default function WorldlineMockPage({
  personaId,
  personaName,
  worldlineEnabled = true,
}: Props) {
  const hostRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const host = hostRef.current
    if (!host || !personaId) return

    host.innerHTML = shellHtml
    const root = host.querySelector('#neural-field-demo') as HTMLElement | null
    if (!root) return

    let cleanup: (() => void) | undefined
    let cancelled = false

    ;(async () => {
      await import('../neural-field/neural-demo-engine.js')
      await import('../neural-field/neural-world-engine.js')
      await import('../neural-field/neural-brain.js')
      await import('../neural-field/neural-flow.js')
      await import('../neural-field/neural-world-view.js')
      const { mountNeuralField } = await import('../neural-field/neural-ui.js')
      if (cancelled) return

      const fetchLive = async () => {
        const [board, worldline, turnTrace] = await Promise.all([
          api<Board>(`/api/v1/companion/personas/${personaId}/board`),
          api<WorldlineDetail>(`/api/v1/companion/personas/${personaId}/worldline`),
          api<TurnTraceResp>(`/api/v1/companion/personas/${personaId}/turn-trace?limit=8`),
        ])
        return {
          board,
          worldline,
          traces: turnTrace.traces || [],
        }
      }

      cleanup = mountNeuralField(root, {
        mode: 'monitor',
        personaName: personaName || '',
        fetchLive,
      })
    })().catch((err) => {
      console.error('[worldline monitor]', err)
    })

    return () => {
      cancelled = true
      try {
        cleanup?.()
      } catch {
        /* ignore */
      }
      try {
        ;(root as { _neuralCleanup?: () => void })._neuralCleanup?.()
      } catch {
        /* ignore */
      }
      host.innerHTML = ''
    }
  }, [personaId, personaName])

  if (!personaId) {
    return (
      <Alert type="warning" showIcon message="请先选择一个人设，再打开世界线监控。" />
    )
  }

  return (
    <div>
      {worldlineEnabled === false ? (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message="世界线已关闭 · 仅对话驱动"
        />
      ) : null}
      <div ref={hostRef} />
    </div>
  )
}
