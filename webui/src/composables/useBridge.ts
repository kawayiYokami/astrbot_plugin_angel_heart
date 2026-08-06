/**
 * AstrBot Plugin Page Bridge API 封装
 * 后端路由已带插件名前缀，这里 endpoint 传相对路径（如 "profiles"）。
 */

interface BridgeContext {
  pluginName: string
  displayName: string
  pageName: string
  pageTitle: string
  locale: string
  i18n: Record<string, unknown>
}

interface Bridge {
  ready(): Promise<BridgeContext>
  getContext(): BridgeContext
  getLocale(): string
  apiGet(endpoint: string, params?: Record<string, unknown>): Promise<unknown>
  apiPost(endpoint: string, body?: unknown): Promise<unknown>
  upload(endpoint: string, file: File): Promise<unknown>
  download(endpoint: string, params?: Record<string, unknown>, filename?: string): Promise<void>
}

declare global {
  interface Window {
    AstrBotPluginPage: Bridge
  }
}

const PLUGIN_NAME = 'astrbot_plugin_angel_heart'

let bridgeReady = false
let bridgeContext: BridgeContext | null = null

function getBridge(): Bridge | null {
  if (typeof window !== 'undefined' && window.AstrBotPluginPage) {
    return window.AstrBotPluginPage
  }
  return null
}

export function useBridge() {
  const bridge = getBridge()

  async function init(): Promise<BridgeContext | null> {
    if (bridgeReady && bridgeContext) return bridgeContext
    if (!bridge) {
      console.warn('[Bridge] AstrBotPluginPage 不可用，可能在独立开发模式下运行')
      return null
    }
    try {
      bridgeContext = await bridge.ready()
      bridgeReady = true
      return bridgeContext
    } catch (e) {
      console.error('[Bridge] 初始化失败:', e)
      return null
    }
  }

  async function apiGet<T = unknown>(endpoint: string, params?: Record<string, unknown>): Promise<T> {
    if (bridge) {
      return (await bridge.apiGet(endpoint, params)) as T
    }
    // 开发模式回退：直接请求本地代理
    const url = new URL(`/api/plug/${PLUGIN_NAME}/${endpoint}`, window.location.origin)
    if (params) {
      Object.entries(params).forEach(([k, v]) => {
        if (v !== undefined && v !== null) url.searchParams.set(k, String(v))
      })
    }
    const resp = await fetch(url.toString())
    const body = await resp.json()
    // 与 bridge 行为一致：成功返回 data，失败抛 message
    if (body?.status === 'error') {
      throw new Error(body.message || '请求失败')
    }
    return (body?.data ?? body) as T
  }

  async function apiPost<T = unknown>(endpoint: string, body?: unknown): Promise<T> {
    if (bridge) {
      return (await bridge.apiPost(endpoint, body)) as T
    }
    const url = `/api/plug/${PLUGIN_NAME}/${endpoint}`
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const json = await resp.json()
    // 与 bridge 行为一致：成功返回 data，失败抛 message
    if (json?.status === 'error') {
      throw new Error(json.message || '请求失败')
    }
    return (json?.data ?? json) as T
  }

  return { init, apiGet, apiPost }
}
