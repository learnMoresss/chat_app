import { Card, Tabs } from 'antd'
import { useSearchParams } from 'react-router-dom'
import CapabilitiesPage from './CapabilitiesPage'
import DownloadsPage from './DownloadsPage'
import HealthPage from './HealthPage'
import JobsPage from './JobsPage'
import MediaPage from './MediaPage'
import MomentsPage from './MomentsPage'
import MusicPage from './MusicPage'
import StickersPage from './StickersPage'
import VirtualDevicesPage from './VirtualDevicesPage'

const KEYS = ['health', 'capabilities', 'jobs', 'media', 'music', 'stickers', 'moments', 'downloads', 'virtual'] as const

export default function OpsPage() {
  const [params, setParams] = useSearchParams()
  const raw = params.get('tab') || 'health'
  const tab = (KEYS as readonly string[]).includes(raw) ? raw : 'health'

  return (
    <Card styles={{ body: { paddingTop: 8 } }}>
      <Tabs
        activeKey={tab}
        onChange={(k) => {
          const next = new URLSearchParams(params)
          next.set('tab', k)
          setParams(next, { replace: true })
        }}
        items={[
          { key: 'health', label: '健康', children: <HealthPage /> },
          { key: 'capabilities', label: '能力', children: <CapabilitiesPage /> },
          { key: 'jobs', label: '任务', children: <JobsPage /> },
          { key: 'media', label: '媒体库', children: <MediaPage /> },
          { key: 'music', label: '曲库', children: <MusicPage /> },
          { key: 'stickers', label: '表情包', children: <StickersPage /> },
          { key: 'moments', label: '朋友圈', children: <MomentsPage /> },
          { key: 'downloads', label: '下载', children: <DownloadsPage /> },
          { key: 'virtual', label: '虚拟电脑', children: <VirtualDevicesPage /> },
        ]}
      />
    </Card>
  )
}
