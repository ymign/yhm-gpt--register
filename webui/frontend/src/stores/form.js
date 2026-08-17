import { defineStore } from 'pinia'
import { reactive, watch } from 'vue'

const KEY = 'gpt_outlook_register_form_v3'

export const COUNTRY_NAME_MAP = {
  US: { name: '美国', flag: '🇺🇸' },
  DE: { name: '德国', flag: '🇩🇪' },
  BR: { name: '巴西', flag: '🇧🇷' },
  AR: { name: '阿根廷', flag: '🇦🇷' },
  GB: { name: '英国', flag: '🇬🇧' },
  UK: { name: '英国', flag: '🇬🇧' },
  ES: { name: '西班牙', flag: '🇪🇸' },
  JP: { name: '日本', flag: '🇯🇵' },
  VN: { name: '越南', flag: '🇻🇳' },
  TH: { name: '泰国', flag: '🇹🇭' },
  PH: { name: '菲律宾', flag: '🇵🇭' },
  PL: { name: '波兰', flag: '🇵🇱' },
  NL: { name: '荷兰', flag: '🇳🇱' },
  FR: { name: '法国', flag: '🇫🇷' },
  IT: { name: '意大利', flag: '🇮🇹' },
  CA: { name: '加拿大', flag: '🇨🇦' },
  AU: { name: '澳大利亚', flag: '🇦🇺' },
  SG: { name: '新加坡', flag: '🇸🇬' },
  KR: { name: '韩国', flag: '🇰🇷' },
  IN: { name: '印度', flag: '🇮🇳' },
  ID: { name: '印尼', flag: '🇮🇩' },
  MY: { name: '马来西亚', flag: '🇲🇾' },
  CH: { name: '瑞士', flag: '🇨🇭' },
  SE: { name: '瑞典', flag: '🇸🇪' },
  NO: { name: '挪威', flag: '🇳🇴' },
  FI: { name: '芬兰', flag: '🇫🇮' },
  DK: { name: '丹麦', flag: '🇩🇰' },
  CZ: { name: '捷克', flag: '🇨🇿' },
  AT: { name: '奥地利', flag: '🇦🇹' },
  BE: { name: '比利时', flag: '🇧🇪' },
  IE: { name: '爱尔兰', flag: '🇮🇪' },
  NZ: { name: '新西兰', flag: '🇳🇿' },
  MX: { name: '墨西哥', flag: '🇲🇽' },
  CL: { name: '智利', flag: '🇨🇱' },
  CO: { name: '哥伦比亚', flag: '🇨🇴' },
  PE: { name: '秘鲁', flag: '🇵🇪' },
  TR: { name: '土耳其', flag: '🇹🇷' },
  AE: { name: '阿联酋', flag: '🇦🇪' },
  SA: { name: '沙特', flag: '🇸🇦' },
  ZA: { name: '南非', flag: '🇿🇦' },
  EG: { name: '埃及', flag: '🇪🇬' },
  NG: { name: '尼日利亚', flag: '🇳🇬' },
  HK: { name: '中国香港', flag: '🇭🇰' },
  TW: { name: '中国台湾', flag: '🇹🇼' },
  MO: { name: '中国澳门', flag: '🇲🇴' },
  CN: { name: '中国', flag: '🇨🇳' },
  RU: { name: '俄罗斯', flag: '🇷🇺' },
  UA: { name: '乌克兰', flag: '🇺🇦' },
  RO: { name: '罗马尼亚', flag: '🇷🇴' },
  BG: { name: '保加利亚', flag: '🇧🇬' },
  GR: { name: '希腊', flag: '🇬🇷' },
  PT: { name: '葡萄牙', flag: '🇵🇹' },
  HU: { name: '匈牙利', flag: '🇭🇺' },
  IL: { name: '以色列', flag: '🇮🇱' },
}

export function formatCountry(code) {
  if (!code) return ''
  const c = String(code).trim().toUpperCase()
  const info = COUNTRY_NAME_MAP[c]
  if (info) {
    return `${info.flag} ${info.name} ${c}`
  }
  return c
}

export function countryNameCn(code) {
  if (!code) return ''
  const c = String(code).trim().toUpperCase()
  const info = COUNTRY_NAME_MAP[c]
  if (info) {
    return info.name
  }
  return c
}

export const COUNTRY_OPTIONS = [
  { value: 'TH', label: '🇹🇭 泰国 (TH · 接码提链高爆推荐 ★★★★★)' },
  { value: 'JP', label: '🇯🇵 日本 (JP · 亚太高爆推荐 ★★★★★)' },
  { value: 'BR', label: '🇧🇷 巴西 (BR · Plus试用高爆推荐 ★★★★★)' },
  { value: 'VN', label: '🇻🇳 越南 (VN · 东南亚高爆推荐 ★★★★★)' },
  { value: 'PH', label: '🇵🇭 菲律宾 (PH · 亚太推荐 ★★★★)' },
  { value: 'AR', label: '🇦🇷 阿根廷 (AR · 拉美推荐 ★★★★)' },
  { value: 'ES', label: '🇪🇸 西班牙 (ES · 欧洲推荐 ★★★★)' },
  { value: 'PL', label: '🇵🇱 波兰 (PL · 欧洲推荐 ★★★★)' },
  { value: 'DE', label: '🇩🇪 德国 (DE · 欧洲推荐 ★★★★)' },
  { value: 'GB', label: '🇬🇧 英国 (GB · 欧洲推荐 ★★★★)' },
  { value: 'NL', label: '🇳🇱 荷兰 (NL · 欧洲推荐 ★★★★)' },
  { value: 'US', label: '🇺🇸 美国 (US · 经典通用 ★★★)' },
  { value: 'RANDOM_ALL', label: '🌍 全球可用国家随机轮换 (多国混合 ★★★★)' },
  { value: '',   label: '🌐 自动 / 保持代理原样' },
]

// 跨页面共享 + localStorage 持久化的表单字段
// （proxy 在 注册 / 自动跑号 / Plus 检测 三处共用）
const defaults = {
  proxy: '',
  proxyCountry: 'RANDOM_HOT',      // 单次注册代理目标国家 (默认高爆智能轮换)
  autoProxyCountry: 'RANDOM_HOT',  // 全自动批量代理目标国家
  otpTimeout: 10,
  autoConcurrency: 1,
  autoCoolDown: 3,
  autoTargetCount: 0,
  // 试用资格高爆推荐配置（对齐指纹浏览器 OTP-First 黄金轨迹，避免触发风控降级）：
  // 1. 默认免密 OTP 注册（不打 legacy user/register 设密接口，避免 Promotion Suppression）
  // 2. 默认不秒绑 2FA 和 Codex（避免注册第 1 秒触发自动化工具特征）
  want2fa: false,
  autoWant2fa: false,
  wantPassword: false,
  autoWantPassword: false,
  wantRefreshToken: false,
  autoWantRefreshToken: false,
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
