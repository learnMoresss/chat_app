import { App, Button, Card, Input, Modal, Space, Spin, Table, Tag, Typography } from 'antd'
import { LinkOutlined, ReloadOutlined } from '@ant-design/icons'
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'

type Job = {
  id: string
  capability: string
  plugin_id: string
  status: string
  progress_pct?: number
  progress_msg?: string
  result?: Record<string, unknown>
  error?: string
  created_at?: string
  updated_at?: string
}

const statusColor: Record<string, string> = {
  queued: 'default',
  running: 'processing',
  done: 'success',
  failed: 'error',
  cancelled: 'warning',
  interrupted: 'warning',
}

function fileIdOf(job: Job): string | null {
  const r = job.result || {}
  const id = (r.file_id || r.media_id) as string | undefined
  return id || null
}

export default function JobsPage() {
  const { message } = App.useApp()
  const [loading, setLoading] = useState(true)
  const [jobs, setJobs] = useState<Job[]>([])
  const [shareOpen, setShareOpen] = useState(false)
  const [shareUrl, setShareUrl] = useState('')
  const [sharing, setSharing] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await api<{ jobs: Job[] }>('/api/v1/jobs?limit=80')
      setJobs(data?.jobs || [])
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [message])

  useEffect(() => {
    void load()
  }, [load])

  const createShare = async (fileId: string) => {
    setSharing(true)
    try {
      const res = await api<{ url: string }>('/api/v1/shares', {
        method: 'POST',
        body: JSON.stringify({ file_id: fileId, expires_hours: 24, max_downloads: 20 }),
      })
      setShareUrl(res.url)
      setShareOpen(true)
      message.success('已生成外链')
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    } finally {
      setSharing(false)
    }
  }

  if (loading && jobs.length === 0) return <Spin />

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <Card
        title="我的任务"
        extra={
          <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>
            刷新
          </Button>
        }
      >
        <Table
          rowKey="id"
          dataSource={jobs}
          loading={loading}
          pagination={{ pageSize: 20 }}
          columns={[
            {
              title: '时间',
              dataIndex: 'created_at',
              width: 170,
              render: (v) => (v ? String(v).replace('T', ' ').slice(0, 19) : '—'),
            },
            { title: '能力', dataIndex: 'capability', width: 140 },
            { title: '插件', dataIndex: 'plugin_id', width: 110 },
            {
              title: '状态',
              dataIndex: 'status',
              width: 110,
              render: (v: string, r) => (
                <Space size={4}>
                  <Tag color={statusColor[v] || 'default'}>{v}</Tag>
                  {v === 'running' && r.progress_pct != null ? (
                    <Typography.Text type="secondary">{r.progress_pct}%</Typography.Text>
                  ) : null}
                </Space>
              ),
            },
            {
              title: '结果 / 错误',
              ellipsis: true,
              render: (_, r) => {
                if (r.error) return <Typography.Text type="danger">{r.error}</Typography.Text>
                if (r.result && Object.keys(r.result).length) {
                  return (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {JSON.stringify(r.result)}
                    </Typography.Text>
                  )
                }
                return r.progress_msg || '—'
              },
            },
            {
              title: '外链',
              width: 100,
              render: (_, r) => {
                const fid = fileIdOf(r)
                if (!fid || r.status !== 'done') return '—'
                return (
                  <Button
                    size="small"
                    icon={<LinkOutlined />}
                    loading={sharing}
                    onClick={() => void createShare(fid)}
                  >
                    分享
                  </Button>
                )
              },
            },
          ]}
          locale={{ emptyText: '暂无任务；可在 App「更多 → 链接」发起下载' }}
        />
      </Card>

      <Modal
        title="外链已生成"
        open={shareOpen}
        onCancel={() => setShareOpen(false)}
        footer={[
          <Button key="ok" type="primary" onClick={() => setShareOpen(false)}>
            关闭
          </Button>,
        ]}
      >
        <Typography.Paragraph type="secondary">
          无需登录即可下载；默认 24 小时 / 20 次。可在后端撤销。
        </Typography.Paragraph>
        <Input.TextArea value={shareUrl} readOnly autoSize={{ minRows: 2, maxRows: 4 }} />
      </Modal>
    </div>
  )
}
