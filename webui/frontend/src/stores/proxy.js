import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'

const KEY = 'dango_proxy_pool_v1'
const PROTOCOL_KEY = 'dango_proxy_protocol_v1'
const OLD_FORM_KEY = 'gpt_outlook_register_form_v2'

function parseLines(s) {
  return String(s || '').split('\n').map((x) => x.trim()).filter(Boolean)
}
function dedup(arr) {
  return [...new Set(arr)]
}

/** 规范化单条代理为指定协议 (http 或 socks5h) */
export function changeProxyProtocol(proxyStr, targetProto = 'http') {
  if (!proxyStr) return ''
  let p = proxyStr.trim()
  const targetPrefix = targetProto.toLowerCase() === 'http' ? 'http://' : 'socks5h://'
  // 匹配已知协议头
  const m = p.match(/^(socks5h?|socks4|https?):\/\/(.+)$/i)
  if (m) {
    return targetPrefix + m[2]
  }
  // 裸地址 host:port 或 user:pass@host:port
  return targetPrefix + p
}

// 代理池：独立管理的代理列表与协议偏好，localStorage 持久化。
// 自动跑号时按 worker 顺序轮流取用（后端 /api/auto/start 的 proxy_pool 字段）。
export const useProxyStore = defineStore('proxy', () => {
  // 当前电脑偏好的代理协议：'http' | 'socks5h'
  const savedProto = localStorage.getItem(PROTOCOL_KEY) || 'socks5h'
  const protocol = ref(savedProto === 'http' ? 'http' : 'socks5h')

  let saved = []
  try { saved = JSON.parse(localStorage.getItem(KEY) || '[]') } catch (_) { saved = [] }
  // 从旧版「全自动批量」页的 autoProxyPool textarea 迁移一次
  if (!saved.length) {
    try {
      const old = JSON.parse(localStorage.getItem(OLD_FORM_KEY) || '{}')
      if (old.autoProxyPool) saved = dedup(parseLines(old.autoProxyPool))
    } catch (_) { /* ignore */ }
  }

  // 默认开箱即用代理预置（新设备首次打开自动按当前协议填入）
  if (!saved.length) {
    const defaultHost = 'egyd1230749-region-US-sid-auto:3wnuqht8@us.cliproxy.io:3010'
    saved = [protocol.value === 'http' ? `http://${defaultHost}` : `socks5h://${defaultHost}`]
  }

  const list = ref(saved)
  const text = computed(() => list.value.join('\n'))
  const count = computed(() => list.value.length)

  watch(list, (v) => {
    try { localStorage.setItem(KEY, JSON.stringify(v)) } catch (_) {}
  }, { deep: true })

  watch(protocol, (val) => {
    try { localStorage.setItem(PROTOCOL_KEY, val) } catch (_) {}
  })

  /** 设置全局首选协议，并自动一键批量将当前代理池转换为该协议 */
  function setProtocol(proto, autoConvert = true) {
    const p = proto === 'http' ? 'http' : 'socks5h'
    protocol.value = p
    try { localStorage.setItem(PROTOCOL_KEY, p) } catch (_) {}
    if (autoConvert && list.value.length) {
      list.value = list.value.map((item) => changeProxyProtocol(item, p))
    }
  }

  /** 一键将当前池内全部代理批量转换为当前协议 (或者指定协议) */
  function convertAll(targetProto = protocol.value) {
    list.value = list.value.map((item) => changeProxyProtocol(item, targetProto))
  }

  /** 用整段文本覆盖代理池（自动去重并对裸地址补全当前协议）。返回 { added, duplicated } 供提示。 */
  function setFromText(s) {
    const parsed = parseLines(s).map((item) => {
      // 若未指定协议，补上当前偏好协议
      return /^([a-z0-9]+):\/\//i.test(item) ? item : changeProxyProtocol(item, protocol.value)
    })
    const unique = dedup(parsed)
    list.value = unique
    return { total: parsed.length, kept: unique.length, duplicated: parsed.length - unique.length }
  }

  /** 追加一批（去重合并）。 */
  function append(s) {
    const incoming = parseLines(s).map((item) => {
      return /^([a-z0-9]+):\/\//i.test(item) ? item : changeProxyProtocol(item, protocol.value)
    })
    const merged = dedup([...list.value, ...incoming])
    const added = merged.length - list.value.length
    list.value = merged
    return { added }
  }

  function remove(proxy) {
    list.value = list.value.filter((x) => x !== proxy)
  }
  function clear() {
    list.value = []
  }

  return { protocol, setProtocol, convertAll, list, text, count, setFromText, append, remove, clear }
})

/**
 * 判断代理格式是否合法：[scheme://][user:pass@]host:port
 * 协议可省略——省略时 curl 按 HTTP 代理处理，所以裸写 host:port 也算合法。
 */
export function isValidProxy(p) {
  return /^((socks5h?|socks4|https?):\/\/)?\S+:\d+$/i.test(p.trim())
}

/** 该代理生效的协议类型（用于提示：未写协议默认 http）。 */
export function proxyScheme(p) {
  const m = /^(socks5h?|socks4|https?):\/\//i.exec(p.trim())
  return m ? m[1].toLowerCase() : 'http(默认)'
}
