/**
 * 产品侧驱动 DSH runtime（SDK 客户端）。
 * 运行位置：与 DSH 同机（Docker 内），不在开发者本机长期跑 Agent。
 */
import path from 'node:path'
import { DeepSeekHarness } from '@deepseek-ai/dsh-sdk-client'

export function createProductHarness(options = {}) {
  const root = path.resolve(process.env.PRODUCT_ROOT || '/app')
  const dshHome = path.resolve(process.env.DSH_HOME || '/dsh-home')
  return new DeepSeekHarness({
    profile: options.profile || 'companion',
    dshHome,
    dshBin: path.join(root, 'node_modules', '@deepseek-ai', 'dsh', 'lib', 'bin.js'),
    patches: [path.join(root, 'profiles', 'companion.product.patch.yml')],
    provider: options.provider || process.env.P0_PROVIDER || 'deepseek-official',
    model: options.model || process.env.P0_MODEL || 'deepseek-flash',
    cwd: root,
    initializeTimeoutMs: options.initializeTimeoutMs ?? 120_000,
  })
}
