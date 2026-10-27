import { App, Button, Card, Form, Select, Space, Spin, Switch, Typography } from 'antd'
import { useEffect } from 'react'
import { api } from '../api/client'
import { personaLabel, useCore } from '../hooks/useCore'

export default function PartnerPage() {
  const { message } = App.useApp()
  const { loading, personas, prefs, setPrefs, reload } = useCore()
  const [form] = Form.useForm()

  useEffect(() => {
    form.setFieldsValue({
      active_persona_id: prefs.active_persona_id || undefined,
      shape_enabled: prefs.shape_enabled !== false,
      split_send: !!prefs.split_send,
    })
  }, [prefs, form])

  if (loading) return <Spin />

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      <Card title="默认聊天人">
        <Typography.Paragraph type="secondary">
          选定后，App / 网页都以此为人设偏好（存 user_prefs）。
        </Typography.Paragraph>
        <Form
          form={form}
          layout="vertical"
          style={{ maxWidth: 480 }}
          onFinish={async (v) => {
            try {
              const next = await api('/api/v1/prefs', {
                method: 'PUT',
                body: JSON.stringify({
                  active_persona_id: v.active_persona_id || null,
                  shape_enabled: !!v.shape_enabled,
                  split_send: !!v.split_send,
                }),
              })
              setPrefs(next as typeof prefs)
              message.success('已保存')
              await reload()
            } catch (e) {
              message.error(e instanceof Error ? e.message : String(e))
            }
          }}
        >
          <Form.Item label="人设" name="active_persona_id">
            <Select
              allowClear
              placeholder="选择默认聊天对象"
              options={personas.map((p) => ({ value: p.id, label: personaLabel(p) }))}
            />
          </Form.Item>
          <Form.Item label="回复整形" name="shape_enabled" valuePropName="checked" extra="关掉后出站不拆壳/去 MD/回声，与旧行为一致">
            <Switch />
          </Form.Item>
          <Form.Item label="长回复分句发送" name="split_send" valuePropName="checked" extra="按句号拆成 2–3 条，间隔模拟打字">
            <Switch />
          </Form.Item>
          <Button type="primary" htmlType="submit">
            保存
          </Button>
        </Form>
      </Card>
      {(prefs.echo_streak ?? 0) >= 2 && (
        <Card title="整形提示">
          <Typography.Text type="warning">
            最近回声连续命中 {prefs.echo_streak} 次，建议给人设写一条课程记忆（例如：不要总用同一个开场）。
          </Typography.Text>
        </Card>
      )}
    </Space>
  )
}
