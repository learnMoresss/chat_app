import {
  Alert,
  App,
  Button,
  Card,
  Col,
  Progress,
  Row,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import {
  DownloadOutlined,
  InfoCircleOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, downloadCsv } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { fmtTime } from '../utils/time'

type ModelRow = { model: string; calls: number; tokens: number }
type ClientRow = { client: string; calls: number; tokens: number }

type Summary = {
  days?: number
  calls?: number
  ok_calls?: number
  error_calls?: number
  total_tokens?: number
  total_context_tokens?: number
  total_cache_read_tokens?: number
  avg_cache_hit_rate?: number
  by_model?: ModelRow[]
  by_client?: ClientRow[]
}

type LogRow = {
  id: string
  created_at?: string
  model?: string
  conv_id?: string
  total_tokens?: number
  prompt_tokens?: number
  completion_tokens?: number
  input_tokens?: number
  context_tokens?: number
  cache_read_tokens?: number
  cache_write_tokens?: number
  cache_hit_rate?: number
  history_messages?: number
  model_calls?: number
  client?: string
  status?: string
  source?: string
  estimated?: number
}

type AdminUserRow = {
  username: string
  calls: number
  tokens: number
  context_tokens?: number
  cache_read_tokens?: number
  avg_cache_hit_rate?: number
}

type AdminSummary = {
  days?: number
  calls?: number
  total_tokens?: number
  total_context_tokens?: number
  total_cache_read_tokens?: number
  avg_cache_hit_rate?: number
  by_user?: AdminUserRow[]
  by_model?: ModelRow[]
}

function pct(v?: number) {
  if (v == null || Number.isNaN(Number(v))) return '-'
  const n = Number(v)
  return `${n.toFixed(1)}%`
}

function num(v?: number) {
  return v != null ? Number(v).toLocaleString() : '0'
}

function shortId(v?: string) {
  if (!v) return '-'
  return v.length > 14 ? `${v.slice(0, 8)}…${v.slice(-4)}` : v
}

const cardBody = { height: '100%' } as const

export default function UsagePage() {
  const { message } = App.useApp()
  const { user } = useAuth()
  const [loading, setLoading] = useState(true)
  const [sum, setSum] = useState<Summary>({})
  const [logs, setLogs] = useState<LogRow[]>([])
  const [admin, setAdmin] = useState<AdminSummary | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [s, l] = await Promise.all([
        api<Summary>('/api/v1/usage/summary?days=30'),
        api<LogRow[]>('/api/v1/usage/logs?limit=80'),
      ])
      setSum(s || {})
      setLogs(l || [])
      if (user.is_admin) {
        try {
          setAdmin(await api<AdminSummary>('/api/v1/usage/admin/summary?days=30'))
        } catch {
          setAdmin(null)
        }
      }
    } finally {
      setLoading(false)
    }
  }, [user.is_admin])

  useEffect(() => {
    void load()
  }, [load])

  const exportCsv = async (scope: 'me' | 'all') => {
    try {
      await downloadCsv(`/api/v1/usage/export.csv?days=30&scope=${scope}`, `usage_${scope}_30d.csv`)
      message.success('已开始下载')
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    }
  }

  const hit = sum.avg_cache_hit_rate || 0
  const ctx = sum.total_context_tokens || 0
  const cacheRead = sum.total_cache_read_tokens || 0
  const ok = sum.ok_calls || 0
  const fail = sum.error_calls || 0
  const calls = sum.calls || 0

  const breakdownEqual = useMemo(() => {
    const a = (sum.by_model || []).length
    const b = (sum.by_client || []).length
    return Math.max(a, b, 1)
  }, [sum.by_model, sum.by_client])

  if (loading) {
    return (
      <div style={{ padding: 48, textAlign: 'center' }}>
        <Spin />
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <Space style={{ justifyContent: 'space-between', width: '100%' }} wrap>
        <Space size={6}>
          <Typography.Text type="secondary">近 {sum.days || 30} 天</Typography.Text>
          <Tooltip title="命中率 = 缓存读 Token ÷ 上下文 Token；由每轮 DSH TokenUsage 写入 usage_logs">
            <InfoCircleOutlined style={{ color: 'rgba(0,0,0,0.35)' }} />
          </Tooltip>
        </Space>
        <Space wrap>
          <Button icon={<ReloadOutlined />} onClick={() => void load()}>
            刷新
          </Button>
          <Button icon={<DownloadOutlined />} onClick={() => exportCsv('me')}>
            导出我的
          </Button>
          {user.is_admin ? (
            <Button type="primary" icon={<DownloadOutlined />} onClick={() => exportCsv('all')}>
              导出全站
            </Button>
          ) : null}
        </Space>
      </Space>

      <Row gutter={[12, 12]}>
        <Col xs={24} sm={12} lg={6}>
          <Card size="small" styles={{ body: cardBody }}>
            <Statistic title="调用" value={calls} />
            <div style={{ marginTop: 8, fontSize: 13, color: 'rgba(0,0,0,0.45)' }}>
              成功 <Typography.Text type="success">{ok}</Typography.Text>
              <span style={{ margin: '0 6px', color: '#d9d9d9' }}>|</span>
              失败 <Typography.Text type="danger">{fail}</Typography.Text>
            </div>
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card size="small" styles={{ body: cardBody }}>
            <Statistic
              title={
                <Tooltip title="≈ 上下文 + 输出；上下文含 Prompt Cache 命中部分，数字会偏大">
                  总 Token
                </Tooltip>
              }
              value={sum.total_tokens || 0}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card size="small" styles={{ body: cardBody }}>
            <Statistic
              title={
                <Tooltip title="本轮模型看到的上下文总量 = 新输入 + 缓存读 + 缓存写。命中率高时绝大部分是缓存前缀，不是新写了十几万字。">
                  上下文 Token
                </Tooltip>
              }
              value={ctx}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card size="small" styles={{ body: cardBody }}>
            <Statistic title="缓存读 Token" value={cacheRead} />
            <div style={{ marginTop: 10 }}>
              <Space size={8} style={{ width: '100%', justifyContent: 'space-between' }}>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  平均命中
                </Typography.Text>
                <Typography.Text strong style={{ fontSize: 13 }}>
                  {pct(hit)}
                </Typography.Text>
              </Space>
              <Progress
                percent={Math.min(100, Math.max(0, hit))}
                size="small"
                status={hit >= 40 ? 'success' : hit > 0 ? 'normal' : 'exception'}
                showInfo={false}
                style={{ marginTop: 4, marginBottom: 0 }}
              />
            </div>
          </Card>
        </Col>
      </Row>

      {ctx > 0 && cacheRead > 0 && hit >= 90 ? (
        <Alert
          type="info"
          showIcon
          message="上下文偏大且命中率很高：多半是 DSH 会话前缀缓存，不是被刷接口"
          description="同一会话续聊会反复上报十几万「缓存读」。时间已按东八区显示。端=listen 是一起听短评价；已改为独立轻量补全，不再挂主会话巨型 cache。"
        />
      ) : null}

      {ctx === 0 && cacheRead === 0 ? (
        <Alert
          type="info"
          showIcon
          message="本周期尚未上报上下文 / 缓存指标"
          description="回合有调用但上下文为 0，通常是旧链路未带 TokenUsage。新回合会写入缓存读与命中率。"
        />
      ) : null}

      <Row gutter={[12, 12]} align="stretch">
        <Col xs={24} lg={12} style={{ display: 'flex' }}>
          <Card size="small" title="按模型" style={{ width: '100%' }} styles={{ body: { paddingTop: 8 } }}>
            <Table
              rowKey="model"
              size="small"
              pagination={false}
              dataSource={sum.by_model || []}
              locale={{ emptyText: '暂无' }}
              scroll={{ y: Math.max(120, breakdownEqual * 48) }}
              columns={[
                { title: '模型', dataIndex: 'model', ellipsis: true },
                { title: '调用', dataIndex: 'calls', width: 72, align: 'right' },
                {
                  title: 'Token',
                  dataIndex: 'tokens',
                  width: 100,
                  align: 'right',
                  render: (v: number) => num(v),
                },
              ]}
            />
          </Card>
        </Col>
        <Col xs={24} lg={12} style={{ display: 'flex' }}>
          <Card size="small" title="按客户端" style={{ width: '100%' }} styles={{ body: { paddingTop: 8 } }}>
            <Table
              rowKey="client"
              size="small"
              pagination={false}
              dataSource={sum.by_client || []}
              locale={{ emptyText: '暂无' }}
              scroll={{ y: Math.max(120, breakdownEqual * 48) }}
              columns={[
                {
                  title: '端',
                  dataIndex: 'client',
                  render: (v: string) => (
                    <Tag style={{ marginInlineEnd: 0 }}>{v || '-'}</Tag>
                  ),
                },
                { title: '调用', dataIndex: 'calls', width: 72, align: 'right' },
                {
                  title: 'Token',
                  dataIndex: 'tokens',
                  width: 100,
                  align: 'right',
                  render: (v: number) => num(v),
                },
              ]}
            />
          </Card>
        </Col>
      </Row>

      <Card
        size="small"
        title="回合明细"
        extra={
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            最近 {logs.length} 条
          </Typography.Text>
        }
      >
        <Table
          rowKey="id"
          size="small"
          dataSource={logs}
          scroll={{ x: 860 }}
          pagination={{ pageSize: 15, size: 'small', showSizeChanger: false }}
          locale={{ emptyText: '暂无记录' }}
          expandable={{
            expandedRowRender: (r) => (
              <Space wrap size={[16, 4]} style={{ fontSize: 12, color: 'rgba(0,0,0,0.55)' }}>
                <span>缓存写 {num(r.cache_write_tokens || 0)}</span>
                <span>历史条 {r.history_messages ?? '-'}</span>
                <span>模型调用 {r.model_calls ?? '-'}</span>
                <span>会话 {r.conv_id || '-'}</span>
                {r.source ? <span>来源 {r.source}</span> : null}
              </Space>
            ),
            rowExpandable: () => true,
          }}
          columns={[
            {
              title: '时间',
              dataIndex: 'created_at',
              width: 120,
              render: (v?: string) => fmtTime(v),
            },
            {
              title: '模型',
              dataIndex: 'model',
              width: 130,
              ellipsis: true,
              render: (v) => v || '-',
            },
            {
              title: '上下文',
              width: 88,
              align: 'right',
              render: (_: unknown, r: LogRow) =>
                num(r.context_tokens || r.input_tokens || r.prompt_tokens || 0),
            },
            {
              title: '缓存读',
              dataIndex: 'cache_read_tokens',
              width: 80,
              align: 'right',
              render: (v?: number) => num(v || 0),
            },
            {
              title: '命中',
              dataIndex: 'cache_hit_rate',
              width: 78,
              render: (v?: number) => {
                if (v == null || Number(v) <= 0) return <span style={{ color: '#bbb' }}>-</span>
                const n = Number(v)
                return <Tag color={n >= 40 ? 'green' : 'blue'}>{pct(n)}</Tag>
              },
            },
            {
              title: '输出',
              dataIndex: 'completion_tokens',
              width: 72,
              align: 'right',
              render: (v?: number) => num(v || 0),
            },
            {
              title: '端',
              dataIndex: 'client',
              width: 64,
              render: (v) => (v ? <Tag>{v}</Tag> : '-'),
            },
            {
              title: '状态',
              dataIndex: 'status',
              width: 72,
              render: (v) =>
                v === 'ok' ? <Tag color="success">成功</Tag> : <Tag color="error">失败</Tag>,
            },
            {
              title: '会话',
              dataIndex: 'conv_id',
              width: 120,
              ellipsis: true,
              render: (v?: string) => (
                <Tooltip title={v || ''}>
                  <span style={{ fontFamily: 'ui-monospace, monospace', fontSize: 12 }}>
                    {shortId(v)}
                  </span>
                </Tooltip>
              ),
            },
          ]}
        />
      </Card>

      {admin ? (
        <Card size="small" title="全站（管理员）">
          <Row gutter={[12, 12]} style={{ marginBottom: 12 }}>
            <Col xs={12} md={6}>
              <Statistic title="调用" value={admin.calls || 0} />
            </Col>
            <Col xs={12} md={6}>
              <Statistic title="Token" value={admin.total_tokens || 0} />
            </Col>
            <Col xs={12} md={6}>
              <Statistic title="上下文" value={admin.total_context_tokens || 0} />
            </Col>
            <Col xs={12} md={6}>
              <Statistic title="命中率" value={admin.avg_cache_hit_rate || 0} suffix="%" precision={1} />
            </Col>
          </Row>
          <Table
            rowKey="username"
            size="small"
            pagination={false}
            dataSource={admin.by_user || []}
            columns={[
              { title: '用户', dataIndex: 'username' },
              { title: '调用', dataIndex: 'calls', width: 72, align: 'right' },
              { title: 'Token', dataIndex: 'tokens', align: 'right', render: (v: number) => num(v) },
              {
                title: '上下文',
                dataIndex: 'context_tokens',
                align: 'right',
                render: (v?: number) => num(v || 0),
              },
              {
                title: '命中率',
                dataIndex: 'avg_cache_hit_rate',
                width: 88,
                render: (v?: number) => pct(v),
              },
            ]}
          />
        </Card>
      ) : null}
    </div>
  )
}
