import { App as AntApp, ConfigProvider, Spin, theme } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { HashRouter, Navigate, Route, Routes, useParams } from 'react-router-dom'
import { AuthProvider, useAuth } from './auth/AuthContext'
import AdminLayout from './layouts/AdminLayout'
import AccountPage from './pages/AccountPage'
import ChannelsPage from './pages/ChannelsPage'
import LoginPage from './pages/LoginPage'
import OpsPage from './pages/OpsPage'
import OverviewPage from './pages/OverviewPage'
import PartnerPage from './pages/PartnerPage'
import PersonasPage from './pages/PersonasPage'
import ServerAdminPage from './pages/ServerAdminPage'
import UsagePage from './pages/UsagePage'

function Guard({ children }: { children: React.ReactNode }) {
  const { ready, authed } = useAuth()
  if (!ready) {
    return (
      <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center' }}>
        <Spin size="large" description="恢复登录中…" />
      </div>
    )
  }
  if (!authed) return <Navigate to="/login" replace />
  return children
}

function PersonaTabRedirect({ tab }: { tab: string }) {
  return <Navigate to={`/personas?tab=${tab}`} replace />
}

function LegacyPersonaRedirect() {
  const { tab } = useParams()
  return <Navigate to={`/personas?tab=${tab || 'profile'}`} replace />
}

function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/"
        element={
          <Guard>
            <AdminLayout />
          </Guard>
        }
      >
        <Route index element={<Navigate to="/overview" replace />} />
        <Route path="overview" element={<OverviewPage />} />
        <Route path="partner" element={<PartnerPage />} />
        <Route path="personas" element={<PersonasPage />} />
        <Route path="channels" element={<ChannelsPage />} />
        <Route path="usage" element={<UsagePage />} />
        <Route path="ops" element={<OpsPage />} />
        <Route path="account" element={<AccountPage />} />
        <Route path="server" element={<ServerAdminPage />} />

        {/* 旧入口 → 角色中心 / 账号 */}
        <Route path="schedules" element={<PersonaTabRedirect tab="schedules" />} />
        <Route path="memories" element={<PersonaTabRedirect tab="memory" />} />
        <Route path="companion" element={<PersonaTabRedirect tab="companion" />} />
        <Route path="extract" element={<PersonaTabRedirect tab="extract" />} />
        <Route path="devices" element={<Navigate to="/account?tab=devices" replace />} />
        <Route path="capabilities" element={<Navigate to="/ops?tab=capabilities" replace />} />
        <Route path="jobs" element={<Navigate to="/ops?tab=jobs" replace />} />
        <Route path="downloads" element={<Navigate to="/ops?tab=downloads" replace />} />
        <Route path="media" element={<Navigate to="/ops?tab=media" replace />} />
        <Route path="health" element={<Navigate to="/ops?tab=health" replace />} />
        <Route path="virtual-devices" element={<Navigate to="/ops?tab=virtual" replace />} />
        <Route path="persona/:tab" element={<LegacyPersonaRedirect />} />
      </Route>
      <Route path="*" element={<Navigate to="/overview" replace />} />
    </Routes>
  )
}

export default function App() {
  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        algorithm: theme.defaultAlgorithm,
        token: {
          colorPrimary: '#2563eb',
          colorInfo: '#2563eb',
          colorSuccess: '#059669',
          colorWarning: '#d97706',
          colorError: '#dc2626',
          colorBgLayout: '#f1f5f9',
          colorBgContainer: '#ffffff',
          colorBorder: '#e2e8f0',
          colorBorderSecondary: '#eef2f7',
          colorText: '#0f172a',
          colorTextSecondary: '#64748b',
          borderRadius: 10,
          fontFamily:
            "'IBM Plex Sans', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', -apple-system, BlinkMacSystemFont, sans-serif",
        },
        components: {
          Layout: {
            siderBg: '#ffffff',
            headerBg: '#ffffff',
            bodyBg: '#f1f5f9',
            triggerBg: '#f8fafc',
            triggerColor: '#475569',
          },
          Menu: {
            itemBg: 'transparent',
            subMenuItemBg: 'transparent',
            itemSelectedBg: '#eff6ff',
            itemSelectedColor: '#1d4ed8',
            itemHoverBg: '#f8fafc',
            itemActiveBg: '#eff6ff',
            itemColor: '#334155',
          },
          Button: {
            primaryShadow: 'none',
          },
          Card: {
            headerFontSize: 15,
          },
        },
      }}
    >
      <AntApp>
        <AuthProvider>
          <HashRouter>
            <AppRoutes />
          </HashRouter>
        </AuthProvider>
      </AntApp>
    </ConfigProvider>
  )
}
