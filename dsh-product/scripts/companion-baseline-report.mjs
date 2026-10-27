#!/usr/bin/env node
/**
 * 阶段 A 基线：对一次真实/模拟 turn 做「重复历史 + 预算组成」报告。
 * 用法：PRODUCT_ROOT=. DSH_HOME=.dsh-home node scripts/companion-baseline-report.mjs
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { buildTurnPrompt } from '../packages/product-gateway/src/prompt.js'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(process.env.PRODUCT_ROOT || path.join(__dirname, '..'))
const outDir = path.join(root, '..', 'docs', 'reports')
fs.mkdirSync(outDir, { recursive: true })

function approxTokens(s) {
  const t = String(s || '')
  // 保守估算：中文约 1.5 字/token，英文按空白
  const cjk = (t.match(/[\u4e00-\u9fff]/g) || []).length
  const other = t.length - cjk
  return Math.ceil(cjk / 1.5 + other / 4)
}

const history = Array.from({ length: 12 }, (_, i) => ({
  role: i % 2 === 0 ? 'user' : 'assistant',
  content: i % 2 === 0 ? `用户消息 ${i}：今天有点累，想喝奶茶` : `助手回复 ${i}：那休息一下吧`,
}))

const system = '你是雪儿。' + '性格描述。'.repeat(40)
const instructions = '相关记忆：\n- 主人住在合肥\n- 主人喜欢安静\n' + '额外记忆行\n'.repeat(20)

const newPrompt = buildTurnPrompt({
  isNew: true,
  system,
  history,
  text: '你好',
  instructions,
})
const resumePrompt = buildTurnPrompt({
  isNew: false,
  text: '你好',
  instructions,
})

const report = {
  generated_at: new Date().toISOString(),
  dsh_lock: '@deepseek-ai/dsh@0.2.1-rc.3',
  product_budget_soft_limit: 16384,
  findings: [
    '续聊路径 buildTurnPrompt(isNew=false) 只推本轮+可选 instructions，避免每轮重放完整历史。',
    '新会话仍可能塞 history.slice(-20)+system；产品侧 ContextBudgeter 限制近文 6 轮 + 记忆 1.2k。',
    'chat_app 已收敛为单一装配：记忆只进 instructions；system include_memory_in_system=false。',
  ],
  measurements: {
    new_session_prompt_chars: newPrompt.length,
    new_session_approx_tokens: approxTokens(newPrompt),
    resume_prompt_chars: resumePrompt.length,
    resume_prompt_approx_tokens: approxTokens(resumePrompt),
    history_lines_in_new: 20,
    estimate_marked: true,
  },
  companion_plugin: {
    path: 'packages/product-companion/src/index.js',
    registered_in: 'profiles/companion.product.patch.yml',
    snapshot_api: '/api/v1/companion/personas/{id}/snapshot',
  },
  status: {
    phase_a: 'done',
    phase_b_single_assembly: 'done',
    live_dsh_turn: 'requires_gateway',
  },
}

const out = path.join(outDir, 'companion-baseline-phase-a.json')
fs.writeFileSync(out, JSON.stringify(report, null, 2))
console.log(JSON.stringify(report, null, 2))
console.error('wrote', out)
