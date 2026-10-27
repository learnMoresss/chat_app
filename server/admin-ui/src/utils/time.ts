/** 后端多为 UTC ISO；管理台统一显示东八区 `MM-DD HH:mm:ss`。 */
export function fmtTime(v?: string | null): string {
  if (!v) return '—'
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) {
    return String(v).replace('T', ' ').slice(5, 19)
  }
  const parts = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).formatToParts(d)
  const get = (t: string) => parts.find((p) => p.type === t)?.value || '00'
  return `${get('month')}-${get('day')} ${get('hour')}:${get('minute')}:${get('second')}`
}
