import {
  App,
  Button,
  Card,
  Input,
  Popconfirm,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
} from 'antd'
import { CloudSyncOutlined, DeleteOutlined, ReloadOutlined } from '@ant-design/icons'
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'

type TrackRow = {
  id: string
  source?: string
  title?: string
  artist?: string
  rel_path?: string
  root_label?: string
  duration_sec?: number
  updated_at?: string
}

type LibraryStats = {
  total: number
  scope?: string
  note?: string
  by_source?: { source: string; count: number }[]
  by_root?: { root: string; count: number }[]
}

type TracksResp = {
  total: number
  items: TrackRow[]
  limit: number
  offset: number
}

type PlaylistRow = {
  id: string
  name: string
  kind: string
  source_url?: string
  track_count?: number
  updated_at?: string
}

type PlaylistItem = {
  item_id: string
  position?: number
  id: string
  title?: string
  artist?: string
  source?: string
  duration_sec?: number
}

function fmtDur(s?: number) {
  if (!s) return '—'
  const m = Math.floor(s / 60)
  const sec = Math.round(s % 60)
  return `${m}:${String(sec).padStart(2, '0')}`
}

export default function MusicPage() {
  const { message } = App.useApp()
  const [loading, setLoading] = useState(true)
  const [scanning, setScanning] = useState(false)
  const [stats, setStats] = useState<LibraryStats | null>(null)
  const [tracks, setTracks] = useState<TrackRow[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)
  const [q, setQ] = useState('')
  const [qDraft, setQDraft] = useState('')

  const [playlists, setPlaylists] = useState<PlaylistRow[]>([])
  const [plLoading, setPlLoading] = useState(false)
  const [activePl, setActivePl] = useState<string>('')
  const [plItems, setPlItems] = useState<PlaylistItem[]>([])
  const [plItemsLoading, setPlItemsLoading] = useState(false)
  const [newPlName, setNewPlName] = useState('')
  const [newPlKind, setNewPlKind] = useState<'library' | 'network'>('library')
  const [newPlUrl, setNewPlUrl] = useState('')
  const [addTrackId, setAddTrackId] = useState('')

  const loadStats = useCallback(async () => {
    try {
      const data = await api<LibraryStats>('/api/v1/music/library')
      setStats(data)
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    }
  }, [message])

  const loadTracks = useCallback(async () => {
    setLoading(true)
    try {
      const offset = (page - 1) * pageSize
      const qs = new URLSearchParams({
        limit: String(pageSize),
        offset: String(offset),
        q: q,
      })
      const data = await api<TracksResp>(`/api/v1/music/tracks?${qs}`)
      setTracks(data?.items || [])
      setTotal(data?.total ?? 0)
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [message, page, pageSize, q])

  const loadPlaylists = useCallback(async () => {
    setPlLoading(true)
    try {
      const data = await api<PlaylistRow[]>('/api/v1/music/playlists')
      setPlaylists(Array.isArray(data) ? data : [])
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    } finally {
      setPlLoading(false)
    }
  }, [message])

  const loadPlItems = useCallback(
    async (pid: string) => {
      if (!pid) {
        setPlItems([])
        return
      }
      setPlItemsLoading(true)
      try {
        const data = await api<{ items: PlaylistItem[] }>(`/api/v1/music/playlists/${pid}/items`)
        setPlItems(data?.items || [])
      } catch (e) {
        message.error(e instanceof Error ? e.message : String(e))
      } finally {
        setPlItemsLoading(false)
      }
    },
    [message],
  )

  useEffect(() => {
    void loadStats()
    void loadPlaylists()
  }, [loadStats, loadPlaylists])

  useEffect(() => {
    void loadTracks()
  }, [loadTracks])

  useEffect(() => {
    void loadPlItems(activePl)
  }, [activePl, loadPlItems])

  const scan = async () => {
    setScanning(true)
    try {
      const res = await api<{ tracks: number; nas?: unknown }>('/api/v1/music/scan', { method: 'POST' })
      message.success(`扫描完成，曲库共 ${res.tracks} 首`)
      setPage(1)
      await loadStats()
      await loadTracks()
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    } finally {
      setScanning(false)
    }
  }

  const deleteTrack = async (id: string) => {
    try {
      await api(`/api/v1/music/tracks/${id}`, { method: 'DELETE' })
      message.success('已从曲库删除')
      await Promise.all([loadStats(), loadTracks(), loadPlaylists()])
      if (activePl) await loadPlItems(activePl)
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    }
  }

  const createPlaylist = async () => {
    const name = newPlName.trim()
    if (!name) {
      message.warning('请输入歌单名')
      return
    }
    try {
      await api('/api/v1/music/playlists', {
        method: 'POST',
        body: JSON.stringify({
          name,
          kind: newPlKind,
          source_url: newPlKind === 'network' ? newPlUrl.trim() : '',
        }),
      })
      message.success('歌单已创建')
      setNewPlName('')
      setNewPlUrl('')
      await loadPlaylists()
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    }
  }

  const deletePlaylist = async (id: string) => {
    try {
      await api(`/api/v1/music/playlists/${id}`, { method: 'DELETE' })
      message.success('歌单已删除')
      if (activePl === id) setActivePl('')
      await loadPlaylists()
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    }
  }

  const removeFromPlaylist = async (itemId: string) => {
    if (!activePl) return
    try {
      await api(`/api/v1/music/playlists/${activePl}/items/${itemId}`, { method: 'DELETE' })
      message.success('已从歌单移除')
      await Promise.all([loadPlItems(activePl), loadPlaylists()])
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    }
  }

  const addToPlaylist = async () => {
    if (!activePl || !addTrackId.trim()) {
      message.warning('请选择歌单并填写曲目 ID')
      return
    }
    try {
      await api(`/api/v1/music/playlists/${activePl}/items`, {
        method: 'POST',
        body: JSON.stringify({ track_id: addTrackId.trim() }),
      })
      message.success('已加入歌单')
      setAddTrackId('')
      await Promise.all([loadPlItems(activePl), loadPlaylists()])
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    }
  }

  const tracksTab = (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card
        title="账号曲库"
        extra={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={() => void Promise.all([loadStats(), loadTracks()])} loading={loading}>
              刷新
            </Button>
            <Button type="primary" icon={<CloudSyncOutlined />} loading={scanning} onClick={() => void scan()}>
              扫描 NAS / 缓存
            </Button>
          </Space>
        }
      >
        <Typography.Paragraph type="secondary" style={{ marginBottom: 12 }}>
          {stats?.note ||
            '曲库按账号共享：所有人设一起听、点歌都从这里抽。会话只挂临时歌单，不各自占一份库。'}
        </Typography.Paragraph>
        <Space wrap size="middle">
          <Typography.Text>
            合计 <Typography.Text strong>{stats?.total ?? total}</Typography.Text> 首
          </Typography.Text>
          {(stats?.by_source || []).map((s) => (
            <Tag key={s.source}>
              {s.source || '—'}: {s.count}
            </Tag>
          ))}
        </Space>
        {(stats?.by_root || []).length ? (
          <div style={{ marginTop: 8 }}>
            {(stats?.by_root || []).map((r) => (
              <Tag key={r.root || '_'} color="blue" style={{ marginBottom: 4 }}>
                {r.root || '（无根）'}: {r.count}
              </Tag>
            ))}
          </div>
        ) : null}
      </Card>

      <Card
        title="曲目列表"
        extra={
          <Input.Search
            allowClear
            placeholder="搜标题 / 艺人 / 路径"
            style={{ width: 260 }}
            value={qDraft}
            onChange={(e) => setQDraft(e.target.value)}
            onSearch={(v) => {
              setPage(1)
              setQ(v.trim())
            }}
          />
        }
      >
        <Table
          rowKey="id"
          loading={loading}
          dataSource={tracks}
          pagination={{
            current: page,
            pageSize,
            total,
            showSizeChanger: true,
            pageSizeOptions: ['20', '50', '100'],
            showTotal: (t) => `共 ${t} 首`,
            onChange: (p, ps) => {
              setPage(p)
              setPageSize(ps)
            },
          }}
          columns={[
            { title: '标题', dataIndex: 'title', ellipsis: true },
            { title: '艺人', dataIndex: 'artist', width: 140, ellipsis: true },
            {
              title: '来源',
              dataIndex: 'source',
              width: 90,
              render: (v) => <Tag>{v || '—'}</Tag>,
            },
            { title: '根', dataIndex: 'root_label', width: 100, ellipsis: true },
            { title: '路径', dataIndex: 'rel_path', ellipsis: true },
            {
              title: '时长',
              dataIndex: 'duration_sec',
              width: 80,
              render: (s: number) => fmtDur(s),
            },
            {
              title: '操作',
              width: 100,
              render: (_, r) => (
                <Space>
                  <Button
                    size="small"
                    disabled={!activePl}
                    onClick={() => {
                      setAddTrackId(r.id)
                      void (async () => {
                        if (!activePl) {
                          message.info('请先在「歌单」页选中一个歌单')
                          return
                        }
                        try {
                          await api(`/api/v1/music/playlists/${activePl}/items`, {
                            method: 'POST',
                            body: JSON.stringify({ track_id: r.id }),
                          })
                          message.success('已加入当前歌单')
                          await Promise.all([loadPlItems(activePl), loadPlaylists()])
                        } catch (e) {
                          message.error(e instanceof Error ? e.message : String(e))
                        }
                      })()
                    }}
                  >
                    加歌单
                  </Button>
                  <Popconfirm title="从曲库删除此曲？" onConfirm={() => void deleteTrack(r.id)}>
                    <Button size="small" danger icon={<DeleteOutlined />} />
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      </Card>
    </Space>
  )

  const playlistsTab = (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card title="歌单管理" extra={<Button icon={<ReloadOutlined />} onClick={() => void loadPlaylists()} loading={plLoading}>刷新</Button>}>
        <Space wrap style={{ marginBottom: 12 }}>
          <Input
            placeholder="新歌单名"
            value={newPlName}
            onChange={(e) => setNewPlName(e.target.value)}
            style={{ width: 180 }}
          />
          <Select
            value={newPlKind}
            style={{ width: 120 }}
            options={[
              { value: 'library', label: '本地歌单' },
              { value: 'network', label: '网络歌单' },
            ]}
            onChange={(v) => setNewPlKind(v)}
          />
          {newPlKind === 'network' ? (
            <Input
              placeholder="source_url"
              value={newPlUrl}
              onChange={(e) => setNewPlUrl(e.target.value)}
              style={{ width: 280 }}
            />
          ) : null}
          <Button type="primary" onClick={() => void createPlaylist()}>
            创建
          </Button>
        </Space>
        <Table
          rowKey="id"
          loading={plLoading}
          dataSource={playlists}
          pagination={false}
          rowSelection={{
            type: 'radio',
            selectedRowKeys: activePl ? [activePl] : [],
            onChange: (keys) => setActivePl(String(keys[0] || '')),
          }}
          onRow={(r) => ({
            onClick: () => setActivePl(r.id),
          })}
          columns={[
            { title: '名称', dataIndex: 'name' },
            {
              title: '类型',
              dataIndex: 'kind',
              width: 100,
              render: (v) => <Tag>{v}</Tag>,
            },
            { title: '曲数', dataIndex: 'track_count', width: 80 },
            {
              title: '操作',
              width: 100,
              render: (_, r) =>
                r.kind === 'session' ? (
                  <Typography.Text type="secondary">会话</Typography.Text>
                ) : (
                  <Popconfirm title="删除歌单？" onConfirm={() => void deletePlaylist(r.id)}>
                    <Button size="small" danger icon={<DeleteOutlined />} />
                  </Popconfirm>
                ),
            },
          ]}
        />
      </Card>

      <Card
        title={activePl ? `歌单曲目 · ${playlists.find((p) => p.id === activePl)?.name || activePl}` : '歌单曲目'}
      >
        {!activePl ? (
          <Typography.Text type="secondary">在上方选中一个歌单以管理曲目（移除、加曲）。</Typography.Text>
        ) : (
          <>
            <Space style={{ marginBottom: 12 }} wrap>
              <Input
                placeholder="曲目 ID（可从曲库列表复制）"
                value={addTrackId}
                onChange={(e) => setAddTrackId(e.target.value)}
                style={{ width: 320 }}
              />
              <Button type="primary" onClick={() => void addToPlaylist()}>
                加入歌单
              </Button>
            </Space>
            <Table
              rowKey="item_id"
              loading={plItemsLoading}
              dataSource={plItems}
              pagination={{ pageSize: 50 }}
              columns={[
                { title: '#', dataIndex: 'position', width: 60 },
                { title: '标题', dataIndex: 'title', ellipsis: true },
                { title: '艺人', dataIndex: 'artist', width: 140, ellipsis: true },
                { title: '时长', dataIndex: 'duration_sec', width: 80, render: (s: number) => fmtDur(s) },
                {
                  title: '操作',
                  width: 100,
                  render: (_, r) => (
                    <Popconfirm title="从歌单移除？" onConfirm={() => void removeFromPlaylist(r.item_id)}>
                      <Button size="small">移除</Button>
                    </Popconfirm>
                  ),
                },
              ]}
              locale={{ emptyText: '歌单为空' }}
            />
          </>
        )}
      </Card>
    </Space>
  )

  return (
    <Tabs
      items={[
        { key: 'tracks', label: '曲库', children: tracksTab },
        { key: 'playlists', label: '歌单', children: playlistsTab },
      ]}
    />
  )
}
