import { LockOutlined, UserOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Checkbox, Form, Input, Space, Typography, theme } from 'antd'
import { useState } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { getSavedUsername, getSession } from '../api/client'
import { useAuth } from '../auth/AuthContext'

type FormValues = {
  base?: string
  username: string
  password: string
  remember: boolean
}

export default function LoginPage() {
  const { authed, login } = useAuth()
  const nav = useNavigate()
  const { token } = theme.useToken()
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(false)
  const session = getSession()

  if (authed) return <Navigate to="/overview" replace />

  const onFinish = async (v: FormValues) => {
    setErr('')
    setLoading(true)
    try {
      await login({
        username: v.username.trim(),
        password: v.password,
        base: (v.base || '').trim(),
        remember: !!v.remember,
      })
      nav('/overview', { replace: true })
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'grid',
        placeItems: 'center',
        padding: 24,
        background: `linear-gradient(145deg, ${token.colorBgLayout} 0%, ${token.colorFillQuaternary} 100%)`,
      }}
    >
      <Card style={{ width: 420, maxWidth: '100%' }} styles={{ body: { padding: 32 } }}>
        <Space orientation="vertical" size={4} style={{ width: '100%', marginBottom: 24 }}>
          <Typography.Title level={3} style={{ margin: 0 }}>
            管理台
          </Typography.Title>
          <Typography.Text type="secondary">
            与 App 共用后端。登录后配置聊天对象、人设、记忆、渠道与提取。
          </Typography.Text>
        </Space>

        <Form
          layout="vertical"
          requiredMark={false}
          initialValues={{
            // 公网务必留空，走当前站点（含 /chat-api）；不要默认 127.0.0.1
            base: session.base || '',
            username: getSavedUsername(),
            remember: session.remember,
          }}
          onFinish={onFinish}
        >
          <Form.Item
            label="后端地址"
            name="base"
            extra="公网访问请留空。仅本机直连 API 时填 http://127.0.0.1:8000"
          >
            <Input placeholder="留空 = 当前站点（推荐）" allowClear />
          </Form.Item>
          <Form.Item
            label="用户名"
            name="username"
            rules={[{ required: true, message: '请输入用户名' }]}
          >
            <Input prefix={<UserOutlined />} autoComplete="username" />
          </Form.Item>
          <Form.Item
            label="密码"
            name="password"
            rules={[{ required: true, message: '请输入密码' }]}
          >
            <Input.Password prefix={<LockOutlined />} autoComplete="current-password" />
          </Form.Item>
          <Form.Item name="remember" valuePropName="checked">
            <Checkbox>记住登录（下次自动进入）</Checkbox>
          </Form.Item>
          {err ? <Alert type="error" showIcon title={err} style={{ marginBottom: 16 }} /> : null}
          <Button type="primary" htmlType="submit" block loading={loading} size="large">
            登录
          </Button>
        </Form>
      </Card>
    </div>
  )
}
