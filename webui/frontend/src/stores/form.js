import { defineStore } from 'pinia'
import { reactive, watch } from 'vue'

const KEY = 'gpt_outlook_register_form_v2'

export const COUNTRY_OPTIONS = [
  { value: 'BR', label: '🇧🇷 巴西 (BR · Plus试用高爆推荐 ★★★★★)' },
  { value: 'DE', label: '🇩🇪 德国 (DE · 欧洲高爆推荐 ★★★★)' },
  { value: 'GB', label: '🇬🇧 英国 (GB · 欧洲推荐 ★★★★)' },
  { value: 'PL', label: '🇵🇱 波兰 (PL · 欧洲推荐 ★★★★)' },
  { value: 'ES', label: '🇪🇸 西班牙 (ES · 欧洲推荐 ★★★★)' },
  { value: 'AR', label: '🇦🇷 阿根廷 (AR · 拉美推荐 ★★★★)' },
  { value: 'US', label: '🇺🇸 美国 (US · 经典通用 ★★★)' },
  { value: 'JP', label: '🇯🇵 日本 (JP · 亚太通用 ★★)' },
  { value: '',   label: '🌐 自动 / 保持代理原样' },
]

// 跨页面共享 + localStorage 持久化的表单字段
// （proxy 在 注册 / 自动跑号 / Plus 检测 三处共用）
const defaults = {
  proxy: '',
  proxyCountry: 'BR',      // 单次注册代理目标国家 (默认高爆巴西)
  autoProxyCountry: 'BR',  // 全自动批量代理目标国家
  otpTimeout: 10,
  autoConcurrency: 1,
  autoCoolDown: 3,
  autoTargetCount: 0,
  // 注册后自动绑 2FA。单次 / 批量都**默认 true**：每个号都要 2FA。
  want2fa: true,
  autoWant2fa: true,
}

// el-select 的 clearable 清空时把值写成 **undefined**（不是 ''），而 proxy 在三个
// 页面都是 `form.value.proxy.trim()` 直接调 —— 主人点一次叉，下次提交就
// "Cannot read properties of undefined (reading 'trim')"。这里统一兜底成字符串，
// 免得每个调用点各写各的可选链，也顺手挡住 localStorage 里的历史脏值。
export function proxyText(form) {
  return String(form?.proxy ?? '').trim()
}

export const useFormStore = defineStore('form', () => {
  let saved = {}
  try { saved = JSON.parse(localStorage.getItem(KEY) || '{}') } catch (_) { saved = {} }
  const form = reactive({ ...defaults, ...saved })

  // clearable 清空后 proxy 会变成 undefined 并被持久化进 localStorage，
  // 刷新页面后依然是 undefined。这里watch 回填成 ''，保证存量数据也是干净的。
  watch(() => form.proxy, (v) => {
    if (v === undefined || v === null) form.proxy = ''
  })

  watch(form, (v) => {
    try { localStorage.setItem(KEY, JSON.stringify(v)) } catch (_) {}
  }, { deep: true })

  return { form }
})
