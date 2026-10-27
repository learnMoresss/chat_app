import { App, Button, Card, Checkbox, Col, Form, Input, Row, Select, Space, Spin, Table, Tag, Typography, Upload } from 'antd'
import { InboxOutlined } from '@ant-design/icons'
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import { personaLabel, useCore } from '../hooks/useCore'

type VoiceProfile = {
  id: string
  filename: string
  duration_sec: number
  embed_status: string
  embed_note: string
}

type Props = { embeddedPersonaId?: string }

export default function ExtractPage({ embeddedPersonaId }: Props = {}) {
  const { message } = App.useApp()
  const { loading, personas, reload } = useCore()
  const [pid, setPid] = useState<string>()
  const [voices, setVoices] = useState<VoiceProfile[]>([])
  const [habitOut, setHabitOut] = useState('')
  const [habitBusy, setHabitBusy] = useState(false)
  const [voiceBusy, setVoiceBusy] = useState(false)
  const [habitForm] = Form.useForm()
  const embedded = !!embeddedPersonaId

  const loadVoices = useCallback(async (id: string) => {
    try {
      const list = await api<VoiceProfile[]>(`/api/v1/extract/voice/${id}`)
      setVoices(list || [])
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    }
  }, [message])

  useEffect(() => {
    if (embeddedPersonaId) {
      setPid(embeddedPersonaId)
      void loadVoices(embeddedPersonaId)
      return
    }
    const id = pid || personas[0]?.id
    if (!id) return
    if (!pid) setPid(id)
    void loadVoices(id)
  }, [personas, pid, loadVoices, embeddedPersonaId])

  if (loading) return <Spin />

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      {embedded ? null : (
      <Card title="应用到哪个人设">
        <Select
          style={{ width: 280 }}
          value={pid}
          options={personas.map((p) => ({ value: p.id, label: personaLabel(p) }))}
          onChange={(v) => {
            setPid(v)
            void loadVoices(v)
          }}
        />
        <Typography.Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0 }}>
          习惯提取对齐 OpenSelf / clone-chat；声音特征对齐 Resemblyzer / OpenVoice。
        </Typography.Paragraph>
      </Card>
      )}

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <Card title="① 聊天习惯">
            <Form
              form={habitForm}
              layout="vertical"
              initialValues={{ apply_to_bio: true }}
              onFinish={async (v) => {
                if (!pid) return
                const fd = new FormData()
                fd.append('persona_id', pid)
                fd.append('text', v.text || '')
                fd.append('apply_to_bio', v.apply_to_bio ? 'true' : 'false')
                const file = v.file?.[0]?.originFileObj as File | undefined
                if (file) fd.append('file', file)
                setHabitBusy(true)
                try {
                  const res = await api<{ profile: unknown }>('/api/v1/extract/habits', {
                    method: 'POST',
                    body: fd,
                    headers: {},
                  })
                  setHabitOut(JSON.stringify(res.profile, null, 2))
                  message.success('习惯提取完成')
                  await reload()
                } catch (e) {
                  message.error(e instanceof Error ? e.message : String(e))
                } finally {
                  setHabitBusy(false)
                }
              }}
            >
              <Form.Item label="粘贴聊天文本" name="text">
                <Input.TextArea rows={8} placeholder="导出的微信/QQ 聊天记录文本…" />
              </Form.Item>
              <Form.Item
                label="或上传 .txt"
                name="file"
                valuePropName="fileList"
                getValueFromEvent={(e) => (Array.isArray(e) ? e : e?.fileList)}
              >
                <Upload beforeUpload={() => false} maxCount={1} accept=".txt,text/plain">
                  <Button icon={<InboxOutlined />}>选择文件</Button>
                </Upload>
              </Form.Item>
              <Form.Item name="apply_to_bio" valuePropName="checked">
                <Checkbox>提取后写回人设 bio</Checkbox>
              </Form.Item>
              <Button type="primary" htmlType="submit" loading={habitBusy}>
                开始提取习惯
              </Button>
            </Form>
            {habitOut ? (
              <pre
                style={{
                  marginTop: 16,
                  padding: 12,
                  background: '#111',
                  color: '#d7dde5',
                  borderRadius: 8,
                  maxHeight: 280,
                  overflow: 'auto',
                  fontSize: 12,
                }}
              >
                {habitOut}
              </pre>
            ) : null}
          </Card>
        </Col>

        <Col xs={24} lg={12}>
          <Card title="② 声音特征">
            <Upload.Dragger
              accept="audio/*,.wav,.mp3,.m4a,.ogg,.flac,.webm"
              maxCount={1}
              beforeUpload={() => false}
              disabled={!pid || voiceBusy}
              onChange={async (info) => {
                const file = info.fileList[0]?.originFileObj
                if (!file || !pid) return
                const fd = new FormData()
                fd.append('persona_id', pid)
                fd.append('file', file)
                setVoiceBusy(true)
                try {
                  const res = await api<{ embed_note: string }>('/api/v1/extract/voice', {
                    method: 'POST',
                    body: fd,
                    headers: {},
                  })
                  message.success(res.embed_note || '已上传')
                  await loadVoices(pid)
                } catch (e) {
                  message.error(e instanceof Error ? e.message : String(e))
                } finally {
                  setVoiceBusy(false)
                }
              }}
            >
              <p className="ant-upload-drag-icon">
                <InboxOutlined />
              </p>
              <p className="ant-upload-text">上传参考语音</p>
              <p className="ant-upload-hint">建议 5–30 秒清晰人声（wav / mp3 / m4a）</p>
            </Upload.Dragger>

            <Table
              style={{ marginTop: 16 }}
              size="small"
              loading={voiceBusy}
              locale={{ emptyText: '还没有声音档案' }}
              dataSource={voices}
              rowKey="id"
              pagination={false}
              columns={[
                {
                  title: '文件',
                  dataIndex: 'filename',
                  render: (name: string, v) => (
                    <Space>
                      {name}
                      <Tag color={v.embed_status === 'ready' ? 'green' : 'orange'}>{v.embed_status}</Tag>
                    </Space>
                  ),
                },
                {
                  title: '说明',
                  render: (_, v) => `${v.duration_sec}s · ${v.embed_note}`,
                },
              ]}
            />
          </Card>
        </Col>
      </Row>
    </Space>
  )
}
