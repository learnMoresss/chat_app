import { App, Button, Card, Drawer, Form, Input, Space, Spin, Table, Tag } from 'antd'
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { useCore, type Channel } from '../hooks/useCore'

export default function ChannelsPage() {
  const { message } = App.useApp()
  const { loading, channels, setChannels, reload } = useCore()
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<Channel | null>(null)
  const [form] = Form.useForm()

  useEffect(() => {
    if (!editing) return
    form.setFieldsValue({
      name: editing.name,
      model: editing.model,
      base_url: editing.base_url,
      api_key: editing.api_key || '',
    })
  }, [editing, form])

  if (loading) return <Spin />

  return (
    <>
      <Card title="渠道列表" extra={<Button onClick={() => reload()}>刷新</Button>}>
        <Table
          rowKey="id"
          dataSource={channels}
          pagination={false}
          columns={[
            {
              title: '名称',
              dataIndex: 'name',
              render: (name: string, row: Channel) => (
                <Space>
                  {name}
                  {row.shared ? <Tag color="green">公共</Tag> : <Tag>我的</Tag>}
                  {row.key_filled ? <Tag color="blue">Key 已填</Tag> : <Tag color="orange">未配置</Tag>}
                </Space>
              ),
            },
            { title: '模型', dataIndex: 'model' },
            { title: '接口', dataIndex: 'base_url', ellipsis: true },
            {
              title: '操作',
              width: 160,
              render: (_, row) => (
                <Space>
                  <Button
                    type="link"
                    disabled={!row.key_filled}
                    onClick={async () => {
                      try {
                        const r = await api<{ ok: boolean; ms?: number; error?: string }>(
                          `/api/v1/channels/${row.id}/probe`,
                          { method: 'POST' },
                        )
                        if (r.ok) message.success(`连通正常 · ${r.ms ?? 0}ms`)
                        else message.error(`失败 · ${r.ms ?? 0}ms：${r.error || '未知错误'}`)
                      } catch (e) {
                        message.error(e instanceof Error ? e.message : String(e))
                      }
                    }}
                  >
                    自检
                  </Button>
                  <Button
                    type="link"
                    onClick={async () => {
                      try {
                        const detail = await api<Channel>(`/api/v1/channels/${row.id}`)
                        setEditing(detail)
                        setOpen(true)
                      } catch (e) {
                        message.error(e instanceof Error ? e.message : String(e))
                      }
                    }}
                  >
                    编辑
                  </Button>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <Drawer
        title="编辑渠道"
        open={open}
        onClose={() => setOpen(false)}
        size={420}
        destroyOnHidden
        extra={
          <Button
            type="primary"
            onClick={() => form.submit()}
          >
            保存
          </Button>
        }
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={async (v) => {
            if (!editing) return
            try {
              await api(`/api/v1/channels/${editing.id}`, {
                method: 'PATCH',
                body: JSON.stringify({
                  name: v.name?.trim(),
                  model: v.model?.trim(),
                  base_url: v.base_url?.trim(),
                  api_key: v.api_key,
                }),
              })
              message.success('已保存')
              setOpen(false)
              const list = await api<Channel[]>('/api/v1/channels')
              setChannels(list || [])
            } catch (e) {
              message.error(e instanceof Error ? e.message : String(e))
            }
          }}
        >
          <Form.Item label="名称" name="name" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item label="模型" name="model" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item label="接口地址" name="base_url" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item label="API Key（明文）" name="api_key">
            <Input.Password visibilityToggle />
          </Form.Item>
        </Form>
      </Drawer>
    </>
  )
}
