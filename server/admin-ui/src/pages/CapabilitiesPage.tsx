import { Alert, App, Button, Card, Spin, Table, Tag, Typography } from 'antd'
import { useEffect, useState } from 'react'
import { api } from '../api/client'

type ProductCap = {
  id: string
  label?: string
  description?: string
  system_available?: boolean
  enabled?: boolean
  registered?: boolean
}

type GwCap = {
  id?: string
  capability?: string
  plugin_id?: string
  title?: string
  description?: string
  daily_limit?: number | null
  allowed?: boolean
  available?: boolean
  offline?: boolean
}

type Status = {
  capabilities?: GwCap[]
  capabilities_count?: number
  workers?: { id: string; name?: string; base_url?: string; enabled?: boolean }[]
}

export default function CapabilitiesPage() {
  const { message } = App.useApp()
  const [loading, setLoading] = useState(true)
  const [product, setProduct] = useState<ProductCap[]>([])
  const [gwCaps, setGwCaps] = useState<GwCap[]>([])
  const [workers, setWorkers] = useState<Status['workers']>([])
  const [jobs, setJobs] = useState<{ id: string; status?: string; capability?: string }[]>([])
  const [workerReachable, setWorkerReachable] = useState<boolean | null>(null)

  useEffect(() => {
    ;(async () => {
      setLoading(true)
      try {
        const [meCaps, c, s, j] = await Promise.all([
          api<{ capabilities: ProductCap[] }>('/api/v1/auth/me/capabilities'),
          api<{ capabilities: GwCap[] }>('/api/v1/capabilities').catch(() => ({ capabilities: [] })),
          api<Status>('/api/v1/gateway/status').catch(() => null),
          api<{ jobs?: { id: string; status?: string; capability?: string }[] }>('/api/v1/jobs?limit=20').catch(
            () => null,
          ),
        ])
        setProduct(meCaps?.capabilities || [])
        setGwCaps(c?.capabilities || [])
        setWorkers(s?.workers || [])
        setJobs(j?.jobs || [])
        // gateway caps 若全标 offline，或 workers 配置了但拉不到插件，视为 worker 不可达
        const offline = (c?.capabilities || []).length > 0 && (c?.capabilities || []).every((x) => x.offline || x.available === false)
        setWorkerReachable(offline ? false : (c?.capabilities || []).some((x) => x.available !== false && !x.offline) ? true : null)
      } catch (e) {
        message.error(e instanceof Error ? e.message : String(e))
      } finally {
        setLoading(false)
      }
    })()
  }, [message])

  if (loading) {
    return (
      <div style={{ padding: 48, textAlign: 'center' }}>
        <Spin />
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <Card
        title="产品能力（聊天）"
        extra={
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            系统探测有才注册；子用户开关在「服务器管理」
          </Typography.Text>
        }
      >
        <Table
          rowKey="id"
          size="small"
          dataSource={product}
          pagination={false}
          columns={[
            { title: '能力', dataIndex: 'label', width: 120, render: (v, r) => v || r.id },
            { title: '说明', dataIndex: 'description', ellipsis: true },
            {
              title: '系统',
              dataIndex: 'system_available',
              width: 90,
              render: (v) => (v ? <Tag color="blue">已探测</Tag> : <Tag>未具备</Tag>),
            },
            {
              title: '本账号',
              dataIndex: 'enabled',
              width: 90,
              render: (v, r) =>
                !r.system_available ? (
                  <Tag>不可用</Tag>
                ) : v ? (
                  <Tag color="green">已开通</Tag>
                ) : (
                  <Tag color="orange">已关闭</Tag>
                ),
            },
            {
              title: '对话注入',
              dataIndex: 'registered',
              width: 100,
              render: (v) => (v ? <Tag color="green">会提示</Tag> : <Tag>—</Tag>),
            },
          ]}
          locale={{ emptyText: '暂无产品能力目录' }}
        />
      </Card>

      <Card title="网关插件能力（worker-agent）">
        {workerReachable === false ? (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 12 }}
            message="worker-agent 当前不可达"
            description="gateway.yaml 已授权下列能力，但本机 9108 未响应插件清单。Worker 配置仍可显示；聊天侧产品能力不受影响。"
          />
        ) : null}
        <Table
          rowKey={(r) => r.capability || r.id || JSON.stringify(r)}
          size="small"
          dataSource={gwCaps}
          pagination={false}
          columns={[
            {
              title: '能力',
              dataIndex: 'capability',
              render: (v, r) => v || r.id || '—',
            },
            { title: '插件', dataIndex: 'plugin_id', width: 120, render: (v) => v || '—' },
            {
              title: '说明',
              dataIndex: 'title',
              render: (v, r) => v || r.description || '—',
            },
            {
              title: '日限额',
              dataIndex: 'daily_limit',
              width: 90,
              render: (v) => (v == null ? '不限' : v),
            },
            {
              title: '状态',
              width: 110,
              render: (_, r) =>
                r.offline || r.available === false ? (
                  <Tag color="orange">插件离线</Tag>
                ) : r.allowed === false ? (
                  <Tag>不可用</Tag>
                ) : (
                  <Tag color="green">可用</Tag>
                ),
            },
          ]}
          locale={{ emptyText: 'gateway.yaml 未配置 grants' }}
        />
      </Card>

      {workers && workers.length > 0 ? (
        <Card title="Worker 配置">
          <Table
            rowKey="id"
            size="small"
            dataSource={workers}
            pagination={false}
            columns={[
              { title: 'ID', dataIndex: 'id', width: 100 },
              { title: '名称', dataIndex: 'name' },
              { title: '地址', dataIndex: 'base_url' },
              {
                title: '启用',
                dataIndex: 'enabled',
                width: 80,
                render: (v) => (v === false ? <Tag>否</Tag> : <Tag color="green">是</Tag>),
              },
            ]}
          />
        </Card>
      ) : null}

      {jobs.length > 0 ? (
        <Card title="近期任务">
          <Table
            rowKey="id"
            size="small"
            dataSource={jobs}
            pagination={false}
            columns={[
              { title: 'ID', dataIndex: 'id', ellipsis: true },
              { title: '能力', dataIndex: 'capability' },
              { title: '状态', dataIndex: 'status', width: 100 },
              {
                title: '操作',
                width: 100,
                render: (_, r) =>
                  r.status === 'running' || r.status === 'queued' ? (
                    <Button
                      danger
                      size="small"
                      onClick={async () => {
                        try {
                          await api(`/api/v1/jobs/${r.id}/interrupt`, { method: 'POST' })
                          message.success('已请求中断')
                          setJobs((prev) =>
                            prev.map((x) => (x.id === r.id ? { ...x, status: 'interrupted' } : x)),
                          )
                        } catch (e) {
                          message.error(e instanceof Error ? e.message : String(e))
                        }
                      }}
                    >
                      中断
                    </Button>
                  ) : (
                    '—'
                  ),
              },
            ]}
          />
        </Card>
      ) : null}
    </div>
  )
}
