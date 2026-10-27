import { App, Button, Card, List, Spin, Tag, Typography } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import { useCore } from '../hooks/useCore'
import { fmtTime } from '../utils/time'

type MomentRow = {
  id: string
  author_id: string
  author_kind: string
  text: string
  media: string[]
  likes: string[]
  comments: { id: string; actor_id: string; text: string }[]
  created_at: string
}

export default function MomentsPage() {
  const { message } = App.useApp()
  const { personas } = useCore()
  const [loading, setLoading] = useState(true)
  const [items, setItems] = useState<MomentRow[]>([])

  const nameOf = useCallback(
    (id: string, kind: string) => {
      if (kind === 'persona') {
        const p = personas.find((x) => x.id === id)
        return p ? p.remark || p.name || id.slice(0, 8) : id.slice(0, 8)
      }
      return kind === 'user' ? '我' : id.slice(0, 8)
    },
    [personas],
  )

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await api<{ moments: MomentRow[] }>('/api/v1/moments?limit=50')
      setItems(data?.moments || [])
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [message])

  useEffect(() => {
    void load()
  }, [load])

  if (loading && items.length === 0) return <Spin />

  return (
    <Card
      title="朋友圈动态"
      extra={
        <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>
          刷新
        </Button>
      }
    >
      <Typography.Paragraph type="secondary">
        当前账号可见的朋友圈（含角色调度自发）。用户发 / AI 发都会出现在这里。
      </Typography.Paragraph>
      <List
        dataSource={items}
        locale={{ emptyText: '还没有动态' }}
        renderItem={(m) => (
          <List.Item>
            <List.Item.Meta
              title={
                <span>
                  {nameOf(m.author_id, m.author_kind)}{' '}
                  <Tag color={m.author_kind === 'persona' ? 'blue' : 'default'}>
                    {m.author_kind === 'persona' ? '角色' : '用户'}
                  </Tag>
                  <Typography.Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
                    {fmtTime(m.created_at)}
                  </Typography.Text>
                </span>
              }
              description={
                <div>
                  <div style={{ whiteSpace: 'pre-wrap' }}>{m.text || '（无文字）'}</div>
                  {m.media?.length ? (
                    <Typography.Text type="secondary">媒体 {m.media.length} 个</Typography.Text>
                  ) : null}
                  <div style={{ marginTop: 4, color: '#888', fontSize: 12 }}>
                    赞 {m.likes?.length || 0} · 评 {m.comments?.length || 0}
                  </div>
                </div>
              }
            />
          </List.Item>
        )}
      />
    </Card>
  )
}
