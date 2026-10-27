import { App, Button, Card, Form, Input, Space, Tabs, Upload } from 'antd'
import { UserOutlined } from '@ant-design/icons'
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, apiBase, getSession } from '../api/client'
import DevicesPage from './DevicesPage'

function AvatarPreview({ fileId }: { fileId: string }) {
  const [src, setSrc] = useState('')
  useEffect(() => {
    if (!fileId) {
      setSrc('')
      return
    }
    let revoked = ''
    let cancelled = false
    ;(async () => {
      try {
        const token = getSession().token
        const res = await fetch(`${apiBase()}/api/v1/media/files/${fileId}`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        })
        if (!res.ok || cancelled) return
        const blob = await res.blob()
        revoked = URL.createObjectURL(blob)
        if (!cancelled) setSrc(revoked)
      } catch {
        /* ignore */
      }
    })()
    return () => {
      cancelled = true
      if (revoked) URL.revokeObjectURL(revoked)
    }
  }, [fileId])

  return (
    <div
      style={{
        width: 72,
        height: 72,
        borderRadius: '50%',
        overflow: 'hidden',
        background: '#f0f0f0',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        border: '1px solid #e8e8e8',
        flexShrink: 0,
      }}
    >
      {src ? (
        <img src={src} alt="avatar" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
      ) : (
        <UserOutlined style={{ fontSize: 28, color: '#bbb' }} />
      )}
    </div>
  )
}

export default function AccountPage() {
  const { message } = App.useApp()
  const [params, setParams] = useSearchParams()
  const [form] = Form.useForm()
  const [profile] = Form.useForm()
  const [avatarId, setAvatarId] = useState('')
  const [localPreview, setLocalPreview] = useState('')
  const [savingProfile, setSavingProfile] = useState(false)
  const tab =
    params.get('tab') === 'devices' ? 'devices' : params.get('tab') === 'password' ? 'password' : 'profile'

  useEffect(() => {
    ;(async () => {
      try {
        const me = await api<{ display_name?: string; avatar_file_id?: string }>('/api/v1/auth/me')
        profile.setFieldsValue({ display_name: me.display_name })
        setAvatarId(me.avatar_file_id || '')
        setLocalPreview('')
      } catch {
        /* ignore */
      }
    })()
  }, [profile])

  useEffect(() => {
    return () => {
      if (localPreview) URL.revokeObjectURL(localPreview)
    }
  }, [localPreview])

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
          {
            key: 'profile',
            label: '资料',
            children: (
              <Form
                form={profile}
                layout="vertical"
                style={{ maxWidth: 420 }}
                onFinish={async (v) => {
                  setSavingProfile(true)
                  try {
                    const me = await api<{ avatar_file_id?: string }>('/api/v1/auth/me', {
                      method: 'PATCH',
                      body: JSON.stringify({
                        display_name: v.display_name,
                        avatar_file_id: avatarId,
                      }),
                    })
                    setAvatarId(me.avatar_file_id || '')
                    if (localPreview) {
                      URL.revokeObjectURL(localPreview)
                      setLocalPreview('')
                    }
                    message.success('资料已保存')
                  } catch (e) {
                    message.error(e instanceof Error ? e.message : String(e))
                  } finally {
                    setSavingProfile(false)
                  }
                }}
              >
                <Form.Item label="显示名" name="display_name" rules={[{ required: true, message: '请输入名字' }]}>
                  <Input maxLength={64} />
                </Form.Item>
                <Form.Item label="头像">
                  <Space align="start" size={16}>
                    {localPreview ? (
                      <div
                        style={{
                          width: 72,
                          height: 72,
                          borderRadius: '50%',
                          overflow: 'hidden',
                          border: '1px solid #e8e8e8',
                        }}
                      >
                        <img
                          src={localPreview}
                          alt="avatar"
                          style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                        />
                      </div>
                    ) : (
                      <AvatarPreview fileId={avatarId} />
                    )}
                    <div>
                      <Upload
                        accept="image/*"
                        showUploadList={false}
                        customRequest={async ({ file, onSuccess, onError }) => {
                          try {
                            const f = file as File
                            if (localPreview) URL.revokeObjectURL(localPreview)
                            setLocalPreview(URL.createObjectURL(f))
                            const fd = new FormData()
                            fd.append('file', f)
                            fd.append('kind', 'image')
                            const up = await api<{ file_id: string }>('/api/v1/media/chat-upload', {
                              method: 'POST',
                              body: fd,
                            })
                            setAvatarId(up.file_id)
                            onSuccess?.(up)
                            message.success('头像已上传，点保存生效')
                          } catch (e) {
                            onError?.(e as Error)
                            message.error(e instanceof Error ? e.message : String(e))
                          }
                        }}
                      >
                        <Button>上传头像</Button>
                      </Upload>
                      <div style={{ marginTop: 6, color: '#888', fontSize: 12 }}>
                        {avatarId ? '已设置，保存后全站生效' : '未设置'}
                      </div>
                    </div>
                  </Space>
                </Form.Item>
                <Button type="primary" htmlType="submit" loading={savingProfile}>
                  保存资料
                </Button>
              </Form>
            ),
          },
          {
            key: 'password',
            label: '密码',
            children: (
              <Form
                form={form}
                layout="vertical"
                style={{ maxWidth: 420 }}
                onFinish={async (v) => {
                  try {
                    await api('/api/v1/auth/change-password', {
                      method: 'POST',
                      body: JSON.stringify({
                        old_password: v.old_password,
                        new_password: v.new_password,
                      }),
                    })
                    message.success('密码已更新，其他设备已下线')
                    form.resetFields()
                  } catch (e) {
                    message.error(e instanceof Error ? e.message : String(e))
                  }
                }}
              >
                <Form.Item label="原密码" name="old_password" rules={[{ required: true, message: '请输入原密码' }]}>
                  <Input.Password autoComplete="current-password" />
                </Form.Item>
                <Form.Item
                  label="新密码"
                  name="new_password"
                  rules={[
                    { required: true, message: '请输入新密码' },
                    { min: 8, message: '密码至少 8 位' },
                  ]}
                >
                  <Input.Password autoComplete="new-password" />
                </Form.Item>
                <Form.Item
                  label="确认新密码"
                  name="confirm"
                  dependencies={['new_password']}
                  rules={[
                    { required: true, message: '请再输入一次' },
                    ({ getFieldValue }) => ({
                      validator(_, value) {
                        if (!value || getFieldValue('new_password') === value) return Promise.resolve()
                        return Promise.reject(new Error('两次输入不一致'))
                      },
                    }),
                  ]}
                >
                  <Input.Password autoComplete="new-password" />
                </Form.Item>
                <Button type="primary" htmlType="submit">
                  保存新密码
                </Button>
              </Form>
            ),
          },
          {
            key: 'devices',
            label: '登录设备',
            children: <DevicesPage embedded />,
          },
        ]}
      />
    </Card>
  )
}
