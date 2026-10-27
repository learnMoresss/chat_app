import {
  AppstoreOutlined,
  BarChartOutlined,
  CloudServerOutlined,
  DashboardOutlined,
  KeyOutlined,
  LogoutOutlined,
  RobotOutlined,
  SettingOutlined,
  TeamOutlined,
} from '@ant-design/icons'
import { Avatar, Button, Flex, Layout, Menu, Typography, theme } from 'antd'
import type { MenuProps } from 'antd'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import { useMemo, useState } from 'react'

const { Header, Sider, Content } = Layout

const titles: Record<string, { title: string; desc: string }> = {
  '/overview': { title: '总览', desc: '状态、后台开关与快捷入口' },
  '/partner': { title: '默认聊天', desc: '选定 App / 网页默认聊天对象' },
  '/personas': { title: '角色', desc: '按角色编辑资料、记忆、陪伴、定时与提取' },
  '/channels': { title: '渠道 / Key', desc: '模型接口与 Key' },
  '/usage': { title: '用量', desc: '近 30 天调用、上下文与缓存命中' },
  '/ops': { title: '运维', desc: '能力、任务、媒体、曲库、表情包、健康、虚拟电脑' },
  '/account': { title: '账号', desc: '资料、密码与登录设备' },
  '/server': { title: '服务器管理', desc: '注册方式、邀请码、用户停用' },
  // legacy titles (redirect targets still resolve briefly)
  '/schedules': { title: '角色 · 定时', desc: '' },
  '/memories': { title: '角色 · 记忆', desc: '' },
  '/companion': { title: '角色 · 陪伴', desc: '' },
  '/extract': { title: '角色 · 提取', desc: '' },
  '/devices': { title: '账号 · 设备', desc: '' },
}

function menuKey(pathname: string) {
  if (['/personas', '/schedules', '/memories', '/companion', '/extract'].some((p) => pathname.startsWith(p))) {
    return '/personas'
  }
  if (pathname.startsWith('/devices') || pathname.startsWith('/account')) return '/account'
  if (
    ['/capabilities', '/jobs', '/downloads', '/media', '/moments', '/health', '/virtual-devices', '/ops'].some((p) =>
      pathname.startsWith(p),
    )
  ) {
    return '/ops'
  }
  return pathname
}

export default function AdminLayout() {
  const nav = useNavigate()
  const loc = useLocation()
  const { user, logout, serverLabel } = useAuth()
  const { token } = theme.useToken()
  const [collapsed, setCollapsed] = useState(false)
  const meta = titles[loc.pathname] || titles[menuKey(loc.pathname)] || { title: '管理台', desc: '' }
  const name = user.display_name || user.username || '用户'
  const selected = menuKey(loc.pathname)

  const items: MenuProps['items'] = useMemo(() => {
    const base: MenuProps['items'] = [
      { key: '/overview', icon: <DashboardOutlined />, label: '总览' },
      { key: '/partner', icon: <TeamOutlined />, label: '默认聊天' },
      { key: '/personas', icon: <RobotOutlined />, label: '角色' },
      { key: '/channels', icon: <CloudServerOutlined />, label: '渠道 / Key' },
      { key: '/usage', icon: <BarChartOutlined />, label: '用量' },
      { key: '/ops', icon: <AppstoreOutlined />, label: '运维' },
      { key: '/account', icon: <KeyOutlined />, label: '账号' },
    ]
    if (user.is_admin) {
      base!.push({ key: '/server', icon: <SettingOutlined />, label: '服务器管理' })
    }
    return base
  }, [user.is_admin])

  return (
    <Layout hasSider style={{ height: '100vh', overflow: 'hidden' }}>
      <Sider
        collapsible
        collapsed={collapsed}
        onCollapse={setCollapsed}
        breakpoint="lg"
        collapsedWidth={64}
        width={208}
        theme="light"
        style={{
          height: '100vh',
          overflow: 'hidden',
          zIndex: 20,
          borderRight: `1px solid ${token.colorBorderSecondary}`,
          background: '#fff',
        }}
      >
        <div style={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          <Flex
            align="center"
            gap={10}
            style={{
              padding: collapsed ? '16px 12px' : '18px 16px 12px',
              flexShrink: 0,
              justifyContent: collapsed ? 'center' : 'flex-start',
            }}
          >
            <Avatar shape="square" size={36} style={{ background: token.colorPrimary, fontWeight: 700, flexShrink: 0 }}>
              己
            </Avatar>
            {!collapsed ? (
              <div style={{ minWidth: 0 }}>
                <Typography.Text strong style={{ display: 'block', lineHeight: 1.3, color: token.colorText }}>
                  自己的聊天
                </Typography.Text>
                <Typography.Text type="secondary" style={{ fontSize: 12, lineHeight: 1.3 }}>
                  {serverLabel}
                </Typography.Text>
              </div>
            ) : null}
          </Flex>
          <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
            <Menu
              theme="light"
              mode="inline"
              selectedKeys={[selected]}
              items={items}
              onClick={({ key }) => nav(key)}
              style={{ borderInlineEnd: 'none' }}
            />
          </div>
          <div
            style={{
              padding: collapsed ? 8 : 12,
              borderTop: `1px solid ${token.colorBorderSecondary}`,
              flexShrink: 0,
              background: '#fff',
            }}
          >
            <Flex vertical gap={8}>
              {!collapsed ? (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {name}
                  {user.is_admin ? ' · 管理员' : ''}
                </Typography.Text>
              ) : null}
              <Button
                icon={<LogoutOutlined />}
                block={!collapsed}
                onClick={() => logout().then(() => nav('/login'))}
              >
                {collapsed ? null : '退出登录'}
              </Button>
            </Flex>
          </div>
        </div>
      </Sider>

      <Layout
        style={{
          height: '100vh',
          minWidth: 0,
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          background: token.colorBgLayout,
        }}
      >
        <Header
          style={{
            background: token.colorBgContainer,
            padding: '12px 24px',
            borderBottom: `1px solid ${token.colorBorderSecondary}`,
            height: 'auto',
            lineHeight: 'normal',
            flexShrink: 0,
            display: 'flex',
            alignItems: 'center',
            zIndex: 10,
          }}
        >
          <div style={{ minWidth: 0 }}>
            <Typography.Title level={4} style={{ margin: 0, lineHeight: 1.35, fontSize: 18, fontWeight: 600 }}>
              {meta.title}
            </Typography.Title>
            {meta.desc ? (
              <Typography.Text type="secondary" style={{ fontSize: 13, lineHeight: 1.4, display: 'block', marginTop: 2 }}>
                {meta.desc}
              </Typography.Text>
            ) : null}
          </div>
        </Header>
        <Content style={{ flex: 1, minHeight: 0, overflow: 'auto', padding: 24 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}
