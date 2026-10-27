import { App, Button, Card, Space, Spin, Table, Tag, Typography } from 'antd'
import { LinkOutlined, ReloadOutlined } from '@ant-design/icons'
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'

type Job = {
  id: string
  capability: string
  status: string
  progress_pct?: number
  result?: Record<string, unknown>
  error?: string
  created_at?: string
}

export default function DownloadsPage() {
  const { message } = App.useApp()
  const [loading, setLoading] = useState(true)
  const [jobs, setJobs] = useState<Job[]>([])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await api<{ jobs: Job[] }>('/api/v1/jobs?limit=80&capability=media.download')
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

  const share = async (fileId: string) => {
    try {
      const res = await api<{ url: string }>('/api/v1/shares', {
        method: 'POST',
        body: JSON.stringify({ file_id: fileId, expires_hours: 24, max_downloads: 20 }),
      })
      await navigator.clipboard.writeText(res.url)
      message.success('外链已复制')
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    }
  }

  if (loading && jobs.length === 0) return <Spin />

  return (
    <Card
      title="下载任务"
      extra={
        <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>
          刷新
        </Button>
      }
    >
      <Typography.Paragraph type="secondary">
        来自 App「链接 → 下载」或能力网关。完成后可生成外链分享。
      </Typography.Paragraph>
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
          {
            title: '标题',
            render: (_, r) => (r.result?.title as string) || (r.result?.webpage_url as string) || r.id,
          },
          {
            title: '状态',
            dataIndex: 'status',
            width: 120,
            render: (v: string, r) => (
              <Space>
                <Tag>{v}</Tag>
                {v === 'running' ? <span>{r.progress_pct ?? 0}%</span> : null}
              </Space>
            ),
          },
          {
            title: '大小',
            width: 100,
            render: (_, r) => {
              const s = r.result?.size
              if (typeof s !== 'number') return '—'
              if (s < 1024 * 1024) return `${Math.round(s / 1024)} KB`
              return `${(s / (1024 * 1024)).toFixed(1)} MB`
            },
          },
          {
            title: '外链',
            width: 100,
            render: (_, r) => {
              const fid = r.result?.file_id as string | undefined
              if (!fid || r.status !== 'done') return '—'
              return (
                <Button size="small" icon={<LinkOutlined />} onClick={() => void share(fid)}>
                  复制
                </Button>
              )
            },
          },
          {
            title: '错误',
            ellipsis: true,
            render: (_, r) => r.error || '—',
          },
        ]}
      />
    </Card>
  )
}
