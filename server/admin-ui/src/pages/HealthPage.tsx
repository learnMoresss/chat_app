import { App, Card, Spin, Table, Tag, Typography } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { Button } from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import { useAuth } from '../auth/AuthContext'

type Health = {
  ok?: boolean
  capabilities_count?: number
  workers?: {
    id: string
    name?: string
    base_url?: string
    enabled?: boolean
    ok?: boolean
    detail?: string
  }[]
}

export default function HealthPage() {
  const { message } = App.useApp()
  const { user } = useAuth()
  const [loading, setLoading] = useState(true)
  const [data, setData] = useState<Health | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setData(await api<Health>('/api/v1/gateway/health'))
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [message])

  useEffect(() => {
    void load()
  }, [load])

  if (loading && !data) return <Spin />

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <Card
        title="网关健康"
        extra={
          <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>
            刷新
          </Button>
        }
      >
        <Typography.Paragraph style={{ marginBottom: 8 }}>
          可用能力数：<Tag color="green">{data?.capabilities_count ?? 0}</Tag>
          网关进程：<Tag color={data?.ok ? 'green' : 'red'}>{data?.ok ? '正常' : '异常'}</Tag>
        </Typography.Paragraph>
        {!user.is_admin ? (
          <Typography.Text type="secondary">Worker 明细仅管理员可见。</Typography.Text>
        ) : (
          <Table
            rowKey="id"
            pagination={false}
            dataSource={data?.workers || []}
            columns={[
              { title: 'ID', dataIndex: 'id' },
              { title: '名称', dataIndex: 'name' },
              { title: '地址', dataIndex: 'base_url' },
              {
                title: '探测',
                width: 90,
                render: (_, r) =>
                  r.enabled === false ? (
                    <Tag>停用</Tag>
                  ) : r.ok ? (
                    <Tag color="green">通</Tag>
                  ) : (
                    <Tag color="red">不通</Tag>
                  ),
              },
              {
                title: '详情',
                dataIndex: 'detail',
                ellipsis: true,
              },
            ]}
            locale={{ emptyText: '无 worker 配置' }}
          />
        )}
      </Card>
    </div>
  )
}
