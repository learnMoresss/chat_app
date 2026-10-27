import { App, Button, Card, Popconfirm, Space, Table, Tag, Typography } from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { useNavigate } from 'react-router-dom'

type Device = {
  id: string
  device_name: string
  created_at: string
  last_active_at: string
  current: boolean
}

type Props = { embedded?: boolean }

export default function DevicesPage({ embedded }: Props = {}) {
  const { message } = App.useApp()
  const { logout } = useAuth()
  const nav = useNavigate()
  const [items, setItems] = useState<Device[]>([])
  const [loading, setLoading] = useState(true)

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      setItems((await api<Device[]>('/api/v1/devices')) || [])
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [message])

  useEffect(() => {
    void reload()
  }, [reload])

  const table = (
    <>
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        App 每次登录会占一条会话。点「清理重复」：同名设备只留最近一条，并保留当前网页。
      </Typography.Paragraph>
      <Space wrap style={{ marginBottom: 12 }}>
        <Button onClick={() => void reload()}>刷新</Button>
        <Popconfirm
          title="清理重复登录会话？"
          description="同名只留最近活跃；当前设备保留"
          onConfirm={async () => {
            try {
              const r = await api<{ deleted: number; kept: number }>('/api/v1/devices/prune', { method: 'POST' })
              message.success(`已清理 ${r.deleted} 条，保留 ${r.kept} 条`)
              await reload()
            } catch (e) {
              message.error(e instanceof Error ? e.message : String(e))
            }
          }}
        >
          <Button type="primary">清理重复</Button>
        </Popconfirm>
      </Space>
      <Table
        rowKey="id"
        loading={loading}
        dataSource={items}
        pagination={{ pageSize: 10, hideOnSinglePage: true }}
        size="middle"
        columns={[
          {
            title: '设备',
            dataIndex: 'device_name',
            width: 160,
            render: (name: string, row) => (
              <Space size={6}>
                <span>{name || '未知设备'}</span>
                {row.current ? <Tag color="blue">当前</Tag> : null}
              </Space>
            ),
          },
          {
            title: '登录时间',
            dataIndex: 'created_at',
            width: 180,
            render: (v: string) => (v || '').replace('T', ' ').slice(0, 19),
          },
          {
            title: '最近活跃',
            dataIndex: 'last_active_at',
            width: 180,
            render: (v: string) => (v || '').replace('T', ' ').slice(0, 19),
          },
          {
            title: '操作',
            width: 100,
            render: (_, row) => (
              <Popconfirm
                title={row.current ? '踢掉当前设备会退出登录' : '踢掉此设备？'}
                onConfirm={async () => {
                  try {
                    const res = await api<{ revoked_current?: boolean }>(`/api/v1/devices/${row.id}`, {
                      method: 'DELETE',
                    })
                    message.success('已踢下线')
                    if (res.revoked_current || row.current) {
                      await logout()
                      nav('/login', { replace: true })
                      return
                    }
                    await reload()
                  } catch (e) {
                    message.error(e instanceof Error ? e.message : String(e))
                  }
                }}
              >
                <Button danger type="link" style={{ paddingInline: 0 }}>
                  踢下线
                </Button>
              </Popconfirm>
            ),
          },
        ]}
      />
    </>
  )

  if (embedded) return table
  return <Card title="登录设备">{table}</Card>
}
