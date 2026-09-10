<script setup>
import { computed, nextTick, onActivated, onDeactivated, onMounted, onUnmounted, reactive, ref, shallowRef, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Refresh,
  Download,
  Delete,
  CopyDocument,
  Setting,
  Compass,
  Document,
  Check,
  Close,
  VideoPlay,
  SwitchButton,
  Warning,
  CircleCheck,
  Search,
  Link,
  ArrowDown,
  ArrowUp,
  Loading,
  Key,
  Message,
  Lock,
  Timer,
  DataAnalysis,
  Phone,
  Discount,
  Money,
  CircleCheckFilled,
  Operation,
  Sunny,
  MagicStick,
  Cpu,
  Sunrise,
  Histogram,
  Filter,
  User,
  ArrowRight,
  View,
  Hide,
  EditPen,
} from '@element-plus/icons-vue'
import {
  getRegisteredSummary,
  listRegistered,
  listRegisteredEmails,
  listRegisteredDomains,
  listRegisteredCountries,
  getRegistered,
  deleteRegistered,
  bulkDeleteRegistered,
  cleanInvalidRegistered,
  recoverOAuthCredentials,
  bulkDeleteAccounts,
  listExportFormats,
  exportRegistered,
  updateExportNote,
  convertSessionToSub2,
  convertSessionToCpa,
  updateCredentials,
  getAccountTotp,
  fetchMailOtp,
  bindAccount2fa,
  setAccountPassword,
  bulkBind2fa,
  bulkSetPassword,
  startPlusCheck,
  stopPlusCheck,
  plusCheckStreamUrl,
  getPlusCheckLog,
  startHealthCheck,
  stopHealthCheck,
  healthCheckStreamUrl,
  getHealthCheckLog,
  startOACheck,
  stopOACheck,
  oaCheckStreamUrl,
  getOACheckLog,
  startOAuthExport,
  retryOAuthExport,
  stopOAuthExport,
  oauthExportStreamUrl,
  getOAuthExportLog,
  downloadOAuthExportCpa,
  downloadOAuthExportSub2,
  getOAuthExportFeatures,
  getOAuthExportFeatureWeights,
  startSecurityTask,
  stopSecurityTask,
  retrySecurityTask,
  securityTaskStreamUrl,
  getSecurityTaskLog,
  startWarmingTask,
  stopWarmingTask,
  warmingStreamUrl,
  getWarmingLog,
  getSentinelPoolStats,
  getProxyHealthSummary,
} from '@/api/register'
import { saveSmsConfig, getSmsPriceTiers, getSmsAllCountries, getSmsConfig, getSmsCdkPoolStats, getSmsProviders } from '@/api/settings'
import { copyText, fmtTime, createSSE } from '@/api/request'
import { useFormStore, proxyText, COUNTRY_OPTIONS, COUNTRY_NAME_MAP, formatCountry } from '@/stores/form'
import { useProxyStore } from '@/stores/proxy'
import { useRuntimeStore } from '@/stores/runtime'
import StatusDot from '@/components/StatusDot.vue'
import ElapsedTimer from '@/components/ElapsedTimer.vue'
import ExtractTaskModal from '@/components/ExtractTaskModal.vue'
import TokenRefreshStudio from '@/components/TokenRefreshStudio.vue'

const { form } = storeToRefs(useFormStore())
// 检测用的代理必须能从代理池里挑
const { list: proxyList } = storeToRefs(useProxyStore())
const runtime = useRuntimeStore()
const { dataVersion } = storeToRefs(runtime)

// ════════════════════════ 全渠道提炼模态任务台 ════════════════════════
const extractModalVisible = ref(false)
const extractModalChannel = ref('paypal')
const extractModalEmails = ref([])
const extractModalAutoPay = ref(false)

async function openExtractChannel(channelKey) {
  let targetChannel = channelKey
  let autoPay = false
  if (channelKey === 'paypal_pipeline') {
    targetChannel = 'paypal'
    autoPay = true
  }
  extractModalChannel.value = targetChannel
  extractModalAutoPay.value = autoPay
  if (selected.value.length > 0) {
    extractModalEmails.value = selected.value.map((r) => r.email)
    extractModalVisible.value = true
  } else {
    try {
      await ElMessageBox.confirm(
        '当前未勾选账号，是否针对当前筛选视图下的所有账号打开提炼任务台？',
        '一键提炼任务确认',
        { confirmButtonText: '确定', cancelButtonText: '取消', type: 'info' }
      )
      const res = await listRegisteredEmails({
        filter_health: filterHealth.value,
        filter_plan: filterPlan.value,
        filter_sec: filterSec.value,
        filter_extract: filterExtract.value,
        filter_oauth: filterOAuth.value,
        filter_domain: filterDomain.value,
        filter_country: filterCountry.value,
        filter_at_export: filterAtExport.value,
        filter_at_exp: filterAtExp.value,
        search: searchKeyword.value.trim(),
      })
      extractModalEmails.value = res.emails || []
      extractModalVisible.value = true
    } catch (_) {}
  }
}

// 分页与多维度结构化筛选
const rows = ref([])
const total = ref(0)
const page = ref(1)
const PAGE_SIZE_KEY = 'reg_page_size'
const PAGE_SIZE_PRESETS = [10, 20, 30, 50, 100, 200, 500, 1000]
const PAGE_SIZE_MIN = 1
const PAGE_SIZE_MAX = 2000

function clampPageSize(val) {
  const n = parseInt(val, 10)
  if (!Number.isFinite(n)) return 20
  return Math.min(PAGE_SIZE_MAX, Math.max(PAGE_SIZE_MIN, n))
}

function readSavedPageSize() {
  try {
    return clampPageSize(localStorage.getItem(PAGE_SIZE_KEY) || '20')
  } catch (_) {
    return 20
  }
}

const pageSize = ref(readSavedPageSize())
const customPageSize = ref(pageSize.value)
const pageSizeOptions = computed(() => {
  const set = new Set(PAGE_SIZE_PRESETS)
  if (pageSize.value) set.add(pageSize.value)
  return [...set].sort((a, b) => a - b)
})
const filterHealth = ref('all') // 验活/存活状态筛选: all / token_invalid / banned / dead / alive / ...
const filterPlan = ref('all')
const filterSec = ref('all')
const filterExtract = ref('all')
const filterOAuth = ref('all')
const filterDomain = ref('all')
const domainOptions = ref([])
const filterCountry = ref('all')
const filterAtExport = ref('all') // AT 导出留痕筛选：all / exported / unexported
const filterAtExp = ref('all') // AT 有效期：all / valid / expiring / expired / no_at / unknown
const sortBy = ref('created_at')
const sortOrder = ref('desc')
const countryOptions = ref([])
const searchKeyword = ref('')
const selected = ref([])
const selectedCount = computed(() => selected.value.length)
const loading = ref(false)
let searchTimer = null

const POPULAR_FILTER_COUNTRIES = [
  'JP', 'PH', 'VN', 'US', 'GB', 'BR', 'DE', 'PL', 'AR', 'ES', 'TH', 'SG', 'KR', 'ID', 'NL', 'FR', 'CA', 'AU'
]

function getCountryOptionLabel(code, count) {
  if (!code || code === 'all') return '全部出口国家'
  if (code === 'NONE' || code === 'EMPTY') return '⚪ 未记录出口国家'
  const c = String(code).trim().toUpperCase()
  const info = COUNTRY_NAME_MAP[c]
  const flag = info?.flag ? `${info.flag} ` : '🌐 '
  const name = info?.name ? `${info.name} (${c})` : c
  if (count !== undefined && count !== null) {
    return `${flag}${name} · ${count}个`
  }
  return `${flag}${name}`
}

async function loadDomains() {
  try {
    const res = await listRegisteredDomains()
    if (res && res.domains) {
      domainOptions.value = res.domains || []
    }
  } catch (_) {}
}

async function loadCountries() {
  try {
    const res = await listRegisteredCountries()
    if (res && res.countries) {
      countryOptions.value = res.countries || []
    }
  } catch (_) {}
}

const hasActiveFilter = computed(() => {
  return (
    searchKeyword.value.trim() !== '' ||
    filterHealth.value !== 'all' ||
    filterPlan.value !== 'all' ||
    filterSec.value !== 'all' ||
    filterExtract.value !== 'all' ||
    filterOAuth.value !== 'all' ||
    filterDomain.value !== 'all' ||
    filterCountry.value !== 'all' ||
    filterAtExport.value !== 'all' ||
    filterAtExp.value !== 'all' ||
    Boolean(form.value?.proxy)
  )
})

const advancedFilterCount = computed(() => {
  let count = 0
  if (filterPlan.value !== 'all') count++
  if (filterDomain.value !== 'all') count++
  if (filterCountry.value !== 'all') count++
  if (filterOAuth.value !== 'all') count++
  if (filterExtract.value !== 'all') count++
  if (form.value?.proxy) count++
  return count
})

const hasActiveAttributeFilter = computed(() => {
  return (
    filterAtExport.value !== 'all' ||
    filterOAuth.value !== 'all' ||
    filterHealth.value !== 'all' ||
    filterSec.value !== 'all' ||
    filterPlan.value !== 'all' ||
    filterCountry.value !== 'all' ||
    filterDomain.value !== 'all' ||
    filterExtract.value !== 'all' ||
    filterAtExp.value !== 'all' ||
    Boolean(form.value?.proxy)
  )
})

const activeAttributeFilterCount = computed(() => {
  let count = 0
  if (filterSec.value !== 'all') count++
  if (filterPlan.value !== 'all') count++
  if (filterCountry.value !== 'all') count++
  if (filterDomain.value !== 'all') count++
  if (form.value?.proxy) count++
  return count
})

function getSecFilterLabel(val) {
  if (val === 'with_2fa') return '已绑 2FA'
  if (val === 'missing_2fa') return '待补 2FA'
  if (val === 'with_pwd') return '已设密'
  if (val === 'missing_pwd') return '免密未设'
  if (val === 'missing_security') return '待补安全'
  return val
}

function getPlanFilterLabel(val) {
  if (val === 'plus') return 'Plus/试用'
  if (val === 'pro') return 'Pro特权'
  if (val === 'free') return 'Free正常号'
  return val
}

function getDomainFilterLabel(val) {
  if (val === 'microsoft') return '微软全系'
  if (val === 'outlook') return 'Outlook'
  if (val === 'hotmail') return 'Hotmail'
  if (val === 'gmail') return 'Gmail'
  return val
}

function removeFilter(key) {
  if (key === 'sec') filterSec.value = 'all'
  else if (key === 'plan') filterPlan.value = 'all'
  else if (key === 'country') filterCountry.value = 'all'
  else if (key === 'domain') filterDomain.value = 'all'
  else if (key === 'proxy') { if (form.value) form.value.proxy = '' }
  else if (key === 'health') filterHealth.value = 'all'
  else if (key === 'oauth') filterOAuth.value = 'all'
  else if (key === 'export' || key === 'at_export') filterAtExport.value = 'all'
  else if (key === 'at_exp') filterAtExp.value = 'all'
  else if (key === 'search') searchKeyword.value = ''
  load(true)
}

function applyFilter(key, val) {
  if (key === 'sec') filterSec.value = val
  else if (key === 'plan') filterPlan.value = val
  else if (key === 'country') filterCountry.value = val
  else if (key === 'domain') filterDomain.value = val
  else if (key === 'proxy') { if (form.value) form.value.proxy = val }
  load(true)
}

function resetAdvancedFilters() {
  filterSec.value = 'all'
  filterPlan.value = 'all'
  filterDomain.value = 'all'
  filterCountry.value = 'all'
  filterOAuth.value = 'all'
  filterExtract.value = 'all'
  filterAtExport.value = 'all'
  filterHealth.value = 'all'
  filterAtExp.value = 'all'
  if (form.value) form.value.proxy = ''
  load(true)
}

function clearAllFilters() {
  searchKeyword.value = ''
  filterHealth.value = 'all'
  filterPlan.value = 'all'
  filterSec.value = 'all'
  filterExtract.value = 'all'
  filterOAuth.value = 'all'
  filterDomain.value = 'all'
  filterCountry.value = 'all'
  filterAtExport.value = 'all'
  filterAtExp.value = 'all'
  sortBy.value = 'created_at'
  sortOrder.value = 'desc'
  if (form.value) form.value.proxy = ''
  load(true)
}

// ════════════════════════ 资产全景驾驶舱 Cockpit HUD ════════════════════════
const COCKPIT_COLLAPSED_KEY = 'reg_cockpit_collapsed'
const cockpitCollapsed = ref(localStorage.getItem(COCKPIT_COLLAPSED_KEY) === 'true')
const regSummary = reactive({
  total: 0,
  both_sec: 0,
  with_pwd: 0,
  with_2fa: 0,
  exported_cnt: 0,
  unexported_cnt: 0,
  with_oauth: 0,
  missing_sec_cnt: 0,
  dead_cnt: 0,
  sec_rate: 0,
  pwd_rate: 0,
  twofa_rate: 0,
  top_countries: [],
})

function toggleCockpit() {
  cockpitCollapsed.value = !cockpitCollapsed.value
  try {
    localStorage.setItem(COCKPIT_COLLAPSED_KEY, String(cockpitCollapsed.value))
  } catch (_) {}
}

async function loadRegSummary() {
  try {
    const res = await getRegisteredSummary()
    if (res && res.ok) {
      Object.assign(regSummary, res)
    }
  } catch (_) {}
}

// ════════════════ 胶囊分段多选组合快速视图 (Multi-select Quick Filter Rail) ════════════════
const isAllActive = computed(() => {
  return (
    filterAtExport.value === 'all' &&
    filterOAuth.value === 'all' &&
    filterSec.value === 'all' &&
    filterHealth.value === 'all'
  )
})
const isUnexportedActive = computed(() => filterAtExport.value === 'unexported')
const isExportedActive = computed(() => filterAtExport.value === 'exported')
const isOAuthActive = computed(() => filterOAuth.value === 'oauth_success')
const isNeedsSecActive = computed(() => filterSec.value === 'missing_security')
const isDeadActive = computed(() => filterHealth.value === 'dead')

const activeQuickFiltersCount = computed(() => {
  let cnt = 0
  if (isUnexportedActive.value || isExportedActive.value) cnt++
  if (isOAuthActive.value) cnt++
  if (isNeedsSecActive.value) cnt++
  if (isDeadActive.value) cnt++
  return cnt
})

function toggleQuickFilter(key) {
  if (key === 'all') {
    // 点击全部资产：清空所有快捷多选条件
    filterAtExport.value = 'all'
    filterOAuth.value = 'all'
    filterSec.value = 'all'
    filterHealth.value = 'all'
    load(true)
    return
  }

  if (key === 'unexported') {
    // 纯新未导：支持 toggle 取消与同维（已导出）互斥切换
    filterAtExport.value = filterAtExport.value === 'unexported' ? 'all' : 'unexported'
  } else if (key === 'exported') {
    // 已导出：支持 toggle 取消与同维（纯新未导）互斥切换
    filterAtExport.value = filterAtExport.value === 'exported' ? 'all' : 'exported'
  } else if (key === 'oauth') {
    // Codex 已授权：独立多选 toggle，不干扰导出状态与安全状态
    filterOAuth.value = filterOAuth.value === 'oauth_success' ? 'all' : 'oauth_success'
  } else if (key === 'needs_sec') {
    // 待补安全：独立多选 toggle，可与纯新未导/已授权随意组合
    filterSec.value = filterSec.value === 'missing_security' ? 'all' : 'missing_security'
  } else if (key === 'dead') {
    // 坏号隔离：独立多选 toggle
    filterHealth.value = filterHealth.value === 'dead' ? 'all' : 'dead'
  }
  load(true)
}


function timeAgo(ts) {
  if (!ts) return '刚刚'
  const now = Date.now() / 1000
  const sec = Math.max(0, Math.floor(now - Number(ts)))
  if (sec < 60) return '刚刚'
  if (sec < 3600) return `${Math.floor(sec / 60)} 分钟前`
  if (sec < 86400) return `${Math.floor(sec / 3600)} 小时前`
  if (sec < 86400 * 30) return `${Math.floor(sec / 86400)} 天前`
  return `${Math.floor(sec / (86400 * 30))} 个月前`
}

function getStatusBadges(row) {
  const badges = []
  if (!row) return badges

  // 1. 坏号状态（最优先高亮）
  const hl = (row.plus_check?.status || '').toLowerCase()
  if (hl === 'banned' || hl === 'deactivated' || hl === 'account_deactivated' || hl === 'token_invalid') {
    const banned = hl !== 'token_invalid'
    badges.push({
      type: 'danger',
      label: banned ? '🚫 官方封禁' : '❌ 凭证失效',
      desc: banned ? '账号已被 OpenAI 注销或封禁' : 'Access Token 失效或被吊销',
      effect: 'dark',
    })
    return badges
  }

  // 2. Plus / Pro 权益
  const p = plusOf(row)
  if (p && p.status && p.status !== 'free' && p.status !== 'unchecked') {
    badges.push({
      type: PLUS_TYPE[p.status] || 'primary',
      label: p.label || 'Plus',
      desc: '具备 Plus / Pro 特权订阅',
    })
  }

  // 3. OAuth 授权状态
  if (row.oauth_status === 'success_phone' || row.oauth_export?.phone_verified) {
    badges.push({ type: 'success', label: '📱 Codex已接码', desc: 'Codex 手机接码成功' })
  } else if (row.oauth_status === 'success' || row.oauth_status === 'success_direct') {
    badges.push({ type: 'cyan', label: '⚡ OAuth已授权', desc: '已拥有 Codex 授权凭证' })
  } else if (row.oauth_status === 'need_phone') {
    badges.push({ type: 'warning', label: '📱 待接码', desc: 'Codex OAuth 等待接码' })
  }

  // 4. 提链结果
  if (row.extract_link?.link_url) {
    const ch = (row.extract_link.channel || '提链').toUpperCase()
    badges.push({
      type: row.extract_link.is_zero_trial === false ? 'warning' : 'success',
      label: `🎁 ${ch}${row.extract_link.is_zero_trial === false ? '' : '(0元)'}`,
      url: row.extract_link.link_url,
      desc: '点击直达提链链接',
    })
  }

  // 5. 保温状态
  if (row.last_warmed_at && row.warm_status === 'success') {
    badges.push({
      type: 'info',
      label: `☀️ 保温${row.warm_count ? `x${row.warm_count}` : ''}`,
      desc: `最后保温: ${fmtTime(row.last_warmed_at)}`,
    })
  }

  return badges
}


function maskSecret(str, lead = 3, tail = 3) {
  if (!str) return ''
  if (str.length <= lead + tail) return str
  return `${str.slice(0, lead)}...${str.slice(-tail)}`
}

function formatProxyHost(proxy) {
  if (!proxy) return ''
  try {
    if (proxy.includes('@')) {
      return proxy.split('@')[1]
    }
    return proxy.replace(/^[a-z0-9]+:\/\//i, '')
  } catch (_) {
    return proxy
  }
}

function formatExportDateShort(ts) {
  if (!ts) return ''
  const d = new Date(Number(ts) < 1e11 ? Number(ts) * 1000 : Number(ts))
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  const h = String(d.getHours()).padStart(2, '0')
  const min = String(d.getMinutes()).padStart(2, '0')
  return `${m}/${day} ${h}:${min}`
}

function isHotCountry(code) {
  if (!code) return false
  return ['JP', 'BR', 'VN', 'DE', 'GB', 'PL', 'ES', 'AR', 'TH', 'US', 'SG'].includes(String(code).toUpperCase())
}

const _emailMetaCache = new Map()
function getEmailProviderMeta(email) {
  if (!email) return { name: 'Mail', icon: '✉️', bg: 'rgba(100, 116, 139, 0.12)', color: '#94a3b8' }
  const atIdx = email.indexOf('@')
  const domain = atIdx >= 0 ? email.slice(atIdx + 1).toLowerCase() : email.toLowerCase()
  const cached = _emailMetaCache.get(domain)
  if (cached) return cached

  let meta
  if (domain.startsWith('outlook.') || domain.startsWith('hotmail.') || domain.startsWith('live.') || domain.startsWith('msn.')) {
    if (domain.startsWith('hotmail.')) {
      meta = { name: 'Hotmail', icon: 'Ⓜ️', bg: 'rgba(0, 120, 212, 0.15)', color: '#0078d4' }
    } else {
      meta = { name: 'Outlook', icon: '📫', bg: 'rgba(2, 132, 199, 0.15)', color: '#0284c7' }
    }
  } else if (domain.startsWith('gmail.') || domain.startsWith('googlemail.')) {
    meta = { name: 'Gmail', icon: '🇬', bg: 'rgba(234, 67, 53, 0.12)', color: '#ea4335' }
  } else if (domain.startsWith('icloud.') || domain.startsWith('me.') || domain.startsWith('mac.')) {
    meta = { name: 'iCloud', icon: '🍎', bg: 'rgba(148, 163, 184, 0.15)', color: '#cbd5e1' }
  } else if (domain.startsWith('yahoo.')) {
    meta = { name: 'Yahoo', icon: '🇾', bg: 'rgba(147, 51, 234, 0.15)', color: '#9333ea' }
  } else if (domain.startsWith('proton.') || domain.startsWith('protonmail.')) {
    meta = { name: 'Proton', icon: '⚡', bg: 'rgba(124, 58, 237, 0.15)', color: '#7c3aed' }
  } else {
    meta = { name: 'Custom', icon: '🌐', bg: 'rgba(100, 116, 139, 0.12)', color: '#94a3b8' }
  }
  _emailMetaCache.set(domain, meta)
  return meta
}

function prepareRowData(r) {
  if (!r) return r
  r._providerMeta = getEmailProviderMeta(r.email)
  r._badges = getStatusBadges(r)
  r._createdTime = fmtTime(r.created_at)
  r._timeAgo = timeAgo(r.created_at)
  r._countryLabel = r.reg_country ? formatCountry(r.reg_country) : ''
  r._proxyHost = r.reg_proxy ? formatProxyHost(r.reg_proxy) : ''
  r._exportDate = (r.exported_at || r.at_exported_at) ? formatExportDateShort(r.exported_at || r.at_exported_at) : ''
  r._atExp = formatAtExpiry(r)
  return r
}

function formatDurationShort(sec) {
  const s = Math.max(0, Math.floor(Number(sec) || 0))
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  if (d > 0) return h > 0 ? `${d}天${h}小时` : `${d}天`
  if (h > 0) return m > 0 ? `${h}小时${m}分` : `${h}小时`
  if (m > 0) return `${m}分钟`
  return `${s}秒`
}

function formatAtExpiry(row) {
  if (!row || !row.at_len) {
    return { text: '无 AT', cls: 'at-exp-none', title: '没有 Access Token，点击刷新获取', expired: true }
  }
  const exp = Number(row.at_expires_at) || 0
  if (exp <= 0) {
    return { text: '无法解析到期', cls: 'at-exp-unknown', title: 'Token 不是标准 JWT 或没有 exp 字段', expired: false }
  }
  const remain = exp - Date.now() / 1000
  const absText = formatDurationShort(Math.abs(remain))
  const until = new Date(exp * 1000).toLocaleString('zh-CN', { hour12: false })
  if (remain <= 0) {
    return { text: `已过期 ${absText}`, cls: 'at-exp-dead', title: `到期时间 ${until}，点击刷新 Token`, expired: true }
  }
  if (remain <= 86400) {
    return { text: `剩余 ${absText}`, cls: 'at-exp-warn', title: `${until} 到期，即将失效，点击可刷新`, expired: false }
  }
  return { text: `剩余 ${absText}`, cls: 'at-exp-ok', title: `${until} 到期`, expired: false }
}

function getRowClassName({ row }) {
  return focusedRow.value && focusedRow.value.email === row.email ? 'is-focused-row' : ''
}

function onSearchInput() {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    load(true)
  }, 300)
}

const PLUS_TYPE = {
  pro_20x: 'danger',
  pro_5x: 'warning',
  pro_active: 'danger',
  pro_eligible: 'success',
  team_active: 'primary',
  plus_eligible: 'success',
  plus_active: 'primary',
  free: 'info',
  token_invalid: 'danger',
  banned: 'danger',
  deactivated: 'danger',
  account_deactivated: 'danger',
  error: 'danger',
}
function plusOf(row) {
  if (!row || !row.plus_check) return null
  const p = row.plus_check
  let label = p.label || p.status || ''
  if (p.status === 'plus_eligible' || label === '可领Plus试用' || label === '🎁 可领Plus试用' || label.includes('试用')) {
    label = 'Plus资格'
  }
  return { ...p, label }
}

const OAUTH_STATUS_META = {
  success:        { type: 'success', label: '✅ 授权成功', effect: 'light' },
  success_phone:  { type: 'success', label: '✅ 成功(已接码📱)', effect: 'dark' },
  success_direct: { type: 'success', label: '⚡ 成功(免接码)', effect: 'light' },
  need_phone:     { type: 'warning', label: '📱 需接码', effect: 'light' },
  failed:         { type: 'danger',  label: '❌ 失败', effect: 'light' },
  error:          { type: 'danger',  label: '⚠️ 异常', effect: 'light' },
}

function oauthMeta(row) {
  const st = (row.oauth_status || row.oauth_export?.status || '').toLowerCase().trim()
  if (!st) return null
  const isPhone = Boolean(
    row.oauth_export?.phone_verified
    || st === 'success_phone'
    || (row.oauth_export?.auth_method === 'phone_verified')
  )
  if (isPhone) return OAUTH_STATUS_META.success_phone
  if (st === 'success_direct' || row.oauth_export?.auth_method === 'no_phone_needed') {
    return OAUTH_STATUS_META.success_direct
  }
  if (st === 'success' || st.startsWith('success')) {
    return OAUTH_STATUS_META.success
  }
  return OAUTH_STATUS_META[st] || { type: 'info', label: st, effect: 'plain' }
}

// ════════════════════════ Plus / Pro 状态检测控制台 ════════════════════════
const plusVisible = ref(false)
const plusRunning = ref(false)
const plusTaskId = ref('')
const plusEs = ref(null)
const plusConfigCollapsed = ref(true) // 默认收起参数配置
const plusTargetEmails = ref([])
const plusItems = ref({})
const plusLogs = ref([])
const plusForm = reactive({
  proxy: '__POOL__', // 默认使用全局代理池轮询，也可选单个或直连
  proxyCountry: 'BR', // 代理目标国家重写（默认高爆巴西）
  workers: 5,
  timeout: 20,
})

// 弹窗内单账号日志终端
const plusLogModalVisible = ref(false)
const currentPlusLogItem = ref(null)
const plusLogLines = ref([])
const plusLogLoading = ref(false)

function guessProxyCountry(text) {
  if (!text) return ''
  const m = text.match(/(?:-region-|-country-|_country-)([a-zA-Z]{2})/i) || text.match(/-([a-zA-Z]{2})-\d+-\d+/i)
  if (m && m[1]) return m[1].toUpperCase()
  return ''
}

function loadProxyListToPlus() {
  plusForm.proxies = proxyList.value.join('\n')
}

const plusFilter = ref('all') // 'all' | 'dead' | 'banned' | 'token_invalid' | 'error' | 'plus_active' | 'plus_eligible' | 'free'

function isPlusDeadRow(row) {
  const st = String(row?.result?.status || '')
  return ['banned', 'token_invalid', 'deactivated', 'account_deactivated'].includes(st)
}

function setPlusFilter(f) {
  plusFilter.value = plusFilter.value === f ? 'all' : f
}

const plusRows = computed(() => {
  const list = Object.values(plusItems.value).map((item) => ({ ...item }))
  const f = plusFilter.value
  if (f === 'all') return list
  if (f === 'dead') return list.filter((i) => isPlusDeadRow(i))
  if (f === 'banned') return list.filter((i) => i.result && i.result.status === 'banned')
  if (f === 'token_invalid') return list.filter((i) => i.result && i.result.status === 'token_invalid')
  if (f === 'error') {
    return list.filter((i) => i.result && ['error', 'no_at', 'not_found'].includes(i.result.status))
  }
  if (f === 'plus_active') return list.filter((i) => i.result && i.result.status === 'plus_active')
  if (f === 'plus_eligible') return list.filter((i) => i.result && i.result.status === 'plus_eligible')
  if (f === 'free') return list.filter((i) => i.result && i.result.status === 'free')
  return list
})

const plusStats = computed(() => {
  const items = Object.values(plusItems.value)
  const tot = items.length || plusTargetEmails.value.length || 0
  const done = items.filter((i) => i.status === 'done').length
  const running = items.filter((i) => i.status === 'running').length
  const pending = items.filter((i) => i.status === 'pending').length
  const pro_20x = items.filter((i) => i.result && i.result.status === 'pro_20x').length
  const pro_5x = items.filter((i) => i.result && i.result.status === 'pro_5x').length
  const pro_active = items.filter((i) => i.result && i.result.status === 'pro_active').length
  const pro_eligible = items.filter((i) => i.result && i.result.status === 'pro_eligible').length
  const team_active = items.filter((i) => i.result && i.result.status === 'team_active').length
  const plus_active = items.filter((i) => i.result && i.result.status === 'plus_active').length
  const plus_eligible = items.filter((i) => i.result && i.result.status === 'plus_eligible').length
  const free = items.filter((i) => i.result && i.result.status === 'free').length
  const banned = items.filter((i) => i.result && i.result.status === 'banned').length
  const token_invalid = items.filter((i) => i.result && i.result.status === 'token_invalid').length
  const error = items.filter((i) => i.result && (i.result.status === 'error' || i.result.status === 'no_at' || i.result.status === 'not_found')).length
  const total_pro = pro_20x + pro_5x + pro_active + pro_eligible
  const percent = tot > 0 ? Math.round((done / tot) * 100) : 0
  return {
    total: tot, done, running, pending,
    pro_20x, pro_5x, pro_active, pro_eligible, total_pro, team_active,
    plus_active, plus_eligible, free, banned, token_invalid, error,
    percent,
  }
})

const PLUS_STATE_META = {
  pro_20x:       { type: 'danger',  label: '👑 Pro 20x', icon: 'Check' },
  pro_5x:        { type: 'warning', label: '👑 Pro 5x', icon: 'Check' },
  pro_active:    { type: 'danger',  label: '👑 Pro', icon: 'Check' },
  pro_eligible:  { type: 'success', label: '◆ 可领Pro试用', icon: 'Check' },
  team_active:   { type: 'primary', label: '💎 Team', icon: 'Check' },
  plus_active:   { type: 'primary', label: 'Plus生效中', icon: 'Check' },
  plus_eligible: { type: 'success', label: 'Plus试用', icon: 'Check' },
  free:          { type: 'info',    label: 'Free', icon: '' },
  banned:        { type: 'danger',  label: '已封号', icon: 'Close' },
  token_invalid: { type: 'danger',  label: '凭证失效', icon: 'Close' },
  no_at:         { type: 'info',    label: '无AT', icon: '' },
  not_found:     { type: 'info',    label: '未找到', icon: '' },
  error:         { type: 'danger',  label: '错误/异常', icon: 'Close' },
  cancelled:     { type: 'info',    label: '已取消', icon: '' },
}

async function openPlusCheck(mode) {
  let emails = []
  if (mode === 'selected') {
    if (!selected.value.length) {
      ElMessage.warning('请先在表格中勾选要检测的账号')
      return
    }
    emails = selected.value.map((r) => r.email)
  } else if (mode === 'unchecked') {
    loading.value = true
    try {
      const res = await listRegisteredEmails('unchecked')
      emails = res.emails || []
    } catch (e) {
      ElMessage.error('获取未检测账号失败: ' + e.message)
      return
    } finally {
      loading.value = false
    }
    if (!emails.length) {
      ElMessage.info('当前没有未检测 Plus 状态的账号')
      return
    }
  } else if (mode === 'all') {
    loading.value = true
    try {
      const res = await listRegisteredEmails('all')
      emails = res.emails || []
    } catch (e) {
      ElMessage.error('获取全量账号失败: ' + e.message)
      return
    } finally {
      loading.value = false
    }
    if (!emails.length) {
      ElMessage.info('当前号池为空，无账号可检测')
      return
    }
  }

  plusTargetEmails.value = emails
  plusFilter.value = 'all'
  if (!plusForm.proxyCountry && form.value.proxyCountry) {
    plusForm.proxyCountry = form.value.proxyCountry
  }

  if (!plusRunning.value) {
    plusTaskId.value = ''
    plusLogs.value = []
    plusConfigCollapsed.value = true
    const initMap = {}
    for (const em of emails) {
      initMap[em] = { email: em, status: 'pending', result: null, elapsed: 0 }
    }
    plusItems.value = initMap
  }

  plusVisible.value = true
}

function closePlusCheck() {
  if (plusRunning.value) {
    ElMessage.info('检测任务在后台继续运行，可随时重新打开查看进度')
  }
  if (plusEs.value && !plusRunning.value) {
    plusEs.value.close()
    plusEs.value = null
  }
  plusVisible.value = false
}

async function stopPlusCheckTask() {
  if (!plusTaskId.value) {
    plusRunning.value = false
    return
  }
  try {
    await stopPlusCheck(plusTaskId.value)
    ElMessage.success('已发送停止指令')
  } catch (_) {
    ElMessage.info('任务已结束')
  } finally {
    plusRunning.value = false
  }
}

async function startPlusCheckTask() {
  const emails = plusTargetEmails.value
  if (!emails.length) {
    ElMessage.warning('没有待检测的账号列表')
    return
  }

  if (plusEs.value) {
    plusEs.value.close()
    plusEs.value = null
  }

  plusRunning.value = true
  plusLogs.value = []
  plusConfigCollapsed.value = true

  const initMap = {}
  for (const em of emails) {
    initMap[em] = { email: em, status: 'pending', result: null, elapsed: 0 }
  }
  plusItems.value = initMap

  let proxiesParam = ''
  let proxyParam = ''
  if (plusForm.proxy === '__POOL__') {
    proxiesParam = proxyList.value.join('\n')
  } else {
    proxyParam = (plusForm.proxy || '').trim()
  }

  try {
    const res = await startPlusCheck({
      emails,
      proxies: proxiesParam,
      proxy: proxyParam,
      proxy_country: plusForm.proxyCountry || '',
      workers: plusForm.workers || 5,
      timeout: plusForm.timeout || 20,
    })
    const taskId = res.taskId || res.task_id
    if (!taskId) throw new Error('未获取到任务 ID')
    plusTaskId.value = taskId

    plusEs.value = createSSE(plusCheckStreamUrl(taskId), {
      init: (ev) => {
        try {
          const snap = JSON.parse(ev.data)
          if (snap.items) plusItems.value = snap.items
        } catch (_) {}
      },
      progress: (ev) => {
        try {
          const msg = JSON.parse(ev.data)
          if (msg.email) {
            if (!plusItems.value[msg.email]) {
              plusItems.value[msg.email] = { email: msg.email }
            }
            plusItems.value[msg.email].status = msg.status
            if (msg.result !== undefined) plusItems.value[msg.email].result = msg.result
            if (msg.elapsed !== undefined) plusItems.value[msg.email].elapsed = msg.elapsed
          }
        } catch (_) {}
      },
      log: (ev) => {
        try {
          const msg = JSON.parse(ev.data)
          if (msg.line) {
            plusLogs.value.push(msg.line)
            if (plusLogs.value.length > 500) plusLogs.value.splice(0, plusLogs.value.length - 500)
            nextTick(scrollPlusLog)
            if (plusLogModalVisible.value && currentPlusLogItem.value) {
              const targetEmail = currentPlusLogItem.value.email
              if (msg.email ? msg.email === targetEmail : String(msg.line || '').includes(targetEmail)) {
                plusLogLines.value.push(msg.line)
                scrollPlusModalLog()
              }
            }
          }
        } catch (_) {}
      },
      end: () => {
        plusRunning.value = false
        if (plusEs.value) {
          plusEs.value.close()
          plusEs.value = null
        }
        ElMessage.success('Plus 状态检测已全部完成！')
        load(false) // 刷新主表格
      },
    }, () => {
      if (!plusRunning.value && plusEs.value) {
        plusEs.value.close()
        plusEs.value = null
      }
    })
  } catch (e) {
    plusRunning.value = false
    plusConfigCollapsed.value = false
    ElMessage.error('启动检测失败: ' + (e.response?.data?.detail || e.message))
  }
}

const plusModalLogBoxRef = ref(null)
const plusUserScrolledUp = ref(false)
function onPlusLogScroll() {
  const el = plusModalLogBoxRef.value
  if (!el) return
  const isNearBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= 45
  plusUserScrolledUp.value = !isNearBottom
}
function scrollPlusModalLog(force = false) {
  if (!force && plusUserScrolledUp.value) return
  nextTick(() => {
    const el = plusModalLogBoxRef.value
    if (el && (force || !plusUserScrolledUp.value)) {
      el.scrollTop = el.scrollHeight
    }
  })
}
function scrollToBottomPlusLog() {
  plusUserScrolledUp.value = false
  scrollPlusModalLog(true)
}

function scrollPlusLog() {
  const box = document.getElementById('plus-log-box')
  if (box) box.scrollTop = box.scrollHeight
}

async function openPlusItemLog(row) {
  currentPlusLogItem.value = row
  plusLogLines.value = []
  plusUserScrolledUp.value = false
  plusLogModalVisible.value = true
  plusLogLoading.value = true

  try {
    if (plusTaskId.value) {
      const res = await getPlusCheckLog(plusTaskId.value, row.email)
      plusLogLines.value = res.lines || []
    } else {
      plusLogLines.value = row.logs || ['暂无日志']
    }
    scrollPlusModalLog(true)
  } catch (e) {
    plusLogLines.value = ['读取日志失败: ' + (e.response?.data?.detail || e.message)]
  } finally {
    plusLogLoading.value = false
  }
}

// ════════════════════════ 账号批量验活 (Token 验活 & 套餐验活) ════════════════════════
const healthVisible = ref(false)
const healthRunning = ref(false)
const healthTaskId = ref('')
const healthEs = ref(null)
const healthConfigCollapsed = ref(true)
const healthTargetEmails = ref([])
const healthItems = shallowRef({})
const healthTick = ref(0)
const healthTableRef = ref(null)
const healthNeedsReload = ref(false)
const HEALTH_FORM_KEY = 'gpt_health_check_form_v1'
const HEALTH_FORM_DEFAULTS = {
  mode: 'plan',
  proxy: '__POOL__',
  proxyCountry: 'US',
  workers: 10,
  timeout: 20,
}

function loadHealthForm() {
  let saved = {}
  try {
    saved = JSON.parse(localStorage.getItem(HEALTH_FORM_KEY) || '{}')
  } catch (_) {
    saved = {}
  }
  if (!saved || typeof saved !== 'object') saved = {}
  const workers = Number(saved.workers)
  const timeout = Number(saved.timeout)
  const mode = saved.mode === 'token' || saved.mode === 'plan' ? saved.mode : HEALTH_FORM_DEFAULTS.mode
  return {
    ...HEALTH_FORM_DEFAULTS,
    ...saved,
    mode,
    proxy: saved.proxy == null ? HEALTH_FORM_DEFAULTS.proxy : String(saved.proxy),
    proxyCountry: Object.prototype.hasOwnProperty.call(saved, 'proxyCountry')
      ? String(saved.proxyCountry ?? '')
      : HEALTH_FORM_DEFAULTS.proxyCountry,
    workers: Number.isFinite(workers) && workers >= 1 ? Math.min(20, Math.round(workers)) : HEALTH_FORM_DEFAULTS.workers,
    timeout: Number.isFinite(timeout) && timeout >= 5 ? Math.min(60, Math.round(timeout)) : HEALTH_FORM_DEFAULTS.timeout,
  }
}

const healthForm = reactive(loadHealthForm())

function persistHealthForm() {
  try {
    localStorage.setItem(HEALTH_FORM_KEY, JSON.stringify({ ...healthForm }))
  } catch (_) {}
}

watch(healthForm, persistHealthForm, { deep: true })
watch(healthConfigCollapsed, () => layoutHealthTable())
watch(
  () => healthForm.mode,
  (m) => {
    if (healthRunning.value) return
    const copy = { ...healthItems.value }
    let changed = false
    for (const em of Object.keys(copy)) {
      if (copy[em]?.mode !== m) {
        copy[em] = { ...copy[em], mode: m }
        changed = true
      }
    }
    if (changed) setHealthItems(copy)
  },
)

// 弹窗内单账号日志终端
const healthLogModalVisible = ref(false)
const currentHealthLogItem = ref(null)
const healthLogLines = ref([])
const healthLogLoading = ref(false)

// ── 验活高性能分页、状态筛选与批量节流更新 (防万号卡死) ──
const healthPage = ref(1)
const healthPageSize = ref(50)
const healthFilter = ref('all') // 'all' | 'running' | 'failed' | 'done' | 'pending' | 'dead' | 'banned' | 'token_invalid' | 'plus_active' | 'plus_eligible' | 'free' | 'token_valid'
const healthSearch = ref('')

function isHealthDeadRow(row) {
  const st = String(row?.result?.status || '')
  return ['banned', 'token_invalid', 'deactivated', 'account_deactivated'].includes(st)
}

function healthResultStatus(row) {
  return String(row?.result?.status || '')
}

function setHealthFilter(f) {
  healthFilter.value = healthFilter.value === f ? 'all' : f
  healthPage.value = 1
}

const healthFilteredRows = computed(() => {
  const _tick = healthTick.value
  const list = Object.values(healthItems.value)
  const kw = healthSearch.value.trim().toLowerCase()
  const f = healthFilter.value
  return list.filter((item) => {
    if (kw && !String(item.email || '').toLowerCase().includes(kw)) return false
    if (f === 'all') return true
    if (f === 'running') return item.status === 'running'
    if (f === 'pending') return item.status === 'pending'
    if (f === 'failed') return isHealthFailedRow(item)
    if (f === 'dead') return isHealthDeadRow(item)
    if (f === 'banned') return healthResultStatus(item) === 'banned'
    if (f === 'token_invalid') return healthResultStatus(item) === 'token_invalid'
    if (f === 'plus_active') return healthResultStatus(item) === 'plus_active'
    if (f === 'pro_active') return ['pro_active', 'pro_20x', 'pro_5x', 'pro_eligible'].includes(healthResultStatus(item))
    if (f === 'plus_eligible') return healthResultStatus(item) === 'plus_eligible'
    if (f === 'free') return healthResultStatus(item) === 'free'
    if (f === 'token_valid') return healthResultStatus(item) === 'token_valid'
    if (f === 'done') {
      return item.status === 'done' && !isHealthFailedRow(item) && !isHealthDeadRow(item)
    }
    return true
  })
})

const healthDisplayRows = computed(() => {
  const rows = healthFilteredRows.value
  const start = (healthPage.value - 1) * healthPageSize.value
  return rows.slice(start, start + healthPageSize.value)
})

let healthUpdateTimer = null
let healthPendingUpdates = Object.create(null)

function bumpHealthTick() {
  healthTick.value++
}

function setHealthItems(map) {
  healthItems.value = map
  bumpHealthTick()
}

function stripHealthItemLogs(raw) {
  if (!raw || typeof raw !== 'object') return {}
  const slim = Object.create(null)
  for (const em of Object.keys(raw)) {
    const it = raw[em] || {}
    const row = { ...it }
    delete row.logs
    row.email = row.email || em
    slim[em] = row
  }
  return slim
}

function layoutHealthTable() {
  nextTick(() => {
    healthTableRef.value?.doLayout?.()
  })
}

function flushHealthUpdates() {
  if (healthUpdateTimer) {
    clearTimeout(healthUpdateTimer)
    healthUpdateTimer = null
  }
  const patches = healthPendingUpdates
  healthPendingUpdates = Object.create(null)
  const emails = Object.keys(patches)
  if (!emails.length) return
  const map = healthItems.value
  for (const em of emails) {
    const up = patches[em]
    const cur = map[em]
    if (!cur) {
      map[em] = {
        email: em,
        mode: healthForm.mode,
        status: 'pending',
        step_text: '排队中...',
        result: null,
        elapsed: 0,
        ...up,
      }
    } else {
      Object.assign(cur, up)
    }
  }
  setHealthItems({ ...map })
  layoutHealthTable()
}

function scheduleHealthUpdate() {
  if (healthUpdateTimer) return
  healthUpdateTimer = window.setTimeout(() => {
    healthUpdateTimer = null
    flushHealthUpdates()
  }, 250)
}

const healthStats = computed(() => {
  const _tick = healthTick.value
  const items = Object.values(healthItems.value)
  const tot = items.length || healthTargetEmails.value.length || 0
  let done = 0
  let running = 0
  let pending = 0
  let token_valid = 0
  let plus_active = 0
  let plus_eligible = 0
  let pro_active = 0
  let team_active = 0
  let free = 0
  let banned = 0
  let token_invalid = 0
  let error = 0
  let dead = 0
  let doneOk = 0
  for (const i of items) {
    if (i.status === 'done') done++
    else if (i.status === 'running') running++
    else if (i.status === 'pending') pending++
    const st = i.result && i.result.status
    if (st === 'token_valid') token_valid++
    else if (st === 'plus_active') plus_active++
    else if (st === 'plus_eligible') plus_eligible++
    else if (st === 'pro_active' || st === 'pro_20x' || st === 'pro_5x' || st === 'pro_eligible') pro_active++
    else if (st === 'team_active') team_active++
    else if (st === 'free') free++
    else if (st === 'banned') banned++
    else if (st === 'token_invalid') token_invalid++
    if (isHealthFailedRow(i)) error++
    if (isHealthDeadRow(i)) dead++
    if (i.status === 'done' && !isHealthFailedRow(i) && !isHealthDeadRow(i)) doneOk++
  }
  return {
    total: tot, done, running, pending, doneOk, dead,
    token_valid, plus_active, plus_eligible, pro_active, team_active, free, banned, token_invalid, error,
    percent: tot > 0 ? Math.round((done / tot) * 100) : 0,
  }
})

async function openHealthCheck(scope = 'selected', mode = '') {
  let emails = []
  if (scope === 'selected') {
    if (!selected.value.length) {
      ElMessage.warning('请先在表格中勾选要验活的账号')
      return
    }
    emails = selected.value.map((r) => r.email)
  } else if (scope === 'unchecked') {
    loading.value = true
    try {
      const res = await listRegisteredEmails('unchecked')
      emails = res.emails || []
    } catch (e) {
      ElMessage.error('获取未验活账号失败: ' + e.message)
      return
    } finally {
      loading.value = false
    }
    if (!emails.length) {
      ElMessage.info('当前没有未验活的账号')
      return
    }
  } else if (scope === 'all') {
    loading.value = true
    try {
      const res = await listRegisteredEmails('all')
      emails = res.emails || []
    } catch (e) {
      ElMessage.error('获取全量账号失败: ' + e.message)
      return
    } finally {
      loading.value = false
    }
    if (!emails.length) {
      ElMessage.info('当前号池为空，无账号可验活')
      return
    }
  }

  healthTargetEmails.value = emails
  if (mode === 'token' || mode === 'plan') {
    healthForm.mode = mode
  }
  healthPage.value = 1
  healthFilter.value = 'all'
  healthSearch.value = ''

  if (!healthRunning.value) {
    healthTaskId.value = ''
    healthConfigCollapsed.value = true
    const initMap = Object.create(null)
    const rowMode = healthForm.mode || 'plan'
    for (const em of emails) {
      initMap[em] = { email: em, mode: rowMode, status: 'pending', step_text: '排队中...', result: null, elapsed: 0 }
    }
    setHealthItems(initMap)
  }

  healthVisible.value = true
}

function handleHealthCheckCommand(cmd) {
  if (cmd === 'token_selected') openHealthCheck('selected', 'token')
  else if (cmd === 'token_unchecked') openHealthCheck('unchecked', 'token')
  else if (cmd === 'token_all') openHealthCheck('all', 'token')
  else if (cmd === 'plan_selected') openHealthCheck('selected', 'plan')
  else if (cmd === 'plan_unchecked') openHealthCheck('unchecked', 'plan')
  else if (cmd === 'plan_all') openHealthCheck('all', 'plan')
}

function closeHealthCheck() {
  flushHealthUpdates()
  stopHealthLogPoll()
  if (healthRunning.value) {
    ElMessage.info('验活任务在后台继续运行，可随时重新打开查看进度')
  }
  if (healthEs.value && !healthRunning.value) {
    healthEs.value.close()
    healthEs.value = null
  }
  healthVisible.value = false
  if (healthNeedsReload.value) {
    healthNeedsReload.value = false
    load(false)
  }
}

async function stopHealthCheckTask() {
  if (!healthTaskId.value) {
    healthRunning.value = false
    return
  }
  try {
    await stopHealthCheck(healthTaskId.value)
    ElMessage.success('已发送停止指令')
  } catch (_) {
    ElMessage.info('任务已结束')
  } finally {
    flushHealthUpdates()
    healthRunning.value = false
  }
}

async function startHealthCheckTask() {
  const emails = healthTargetEmails.value
  if (!emails.length) {
    ElMessage.warning('没有待验活的账号列表')
    return
  }

  if (healthEs.value) {
    healthEs.value.close()
    healthEs.value = null
  }

  flushHealthUpdates()
  healthRunning.value = true
  healthNeedsReload.value = false
  healthPage.value = 1
  healthFilter.value = 'all'
  healthSearch.value = ''
  healthConfigCollapsed.value = true

  const initMap = Object.create(null)
  for (const em of emails) {
    initMap[em] = { email: em, mode: healthForm.mode, status: 'pending', step_text: '排队中...', result: null, elapsed: 0 }
  }
  setHealthItems(initMap)

  let proxiesParam = ''
  let proxyParam = ''
  if (healthForm.proxy === '__POOL__') {
    proxiesParam = proxyList.value.join('\n')
  } else {
    proxyParam = (healthForm.proxy || '').trim()
  }

  try {
    const res = await startHealthCheck({
      emails,
      mode: healthForm.mode,
      proxies: proxiesParam,
      proxy: proxyParam,
      proxy_country: healthForm.proxyCountry || '',
      workers: healthForm.workers || 10,
      timeout: healthForm.timeout || 20,
    })
    const taskId = res.taskId || res.task_id
    if (!taskId) throw new Error('未获取到任务 ID')
    healthTaskId.value = taskId

    healthEs.value = createSSE(healthCheckStreamUrl(taskId), {
      init: (ev) => {
        try {
          const snap = JSON.parse(ev.data)
          if (snap.items) {
            setHealthItems(stripHealthItemLogs(snap.items))
          }
        } catch (_) {}
      },
      progress: (ev) => {
        try {
          const msg = JSON.parse(ev.data)
          if (msg.email) {
            if (!healthPendingUpdates[msg.email]) healthPendingUpdates[msg.email] = {}
            if (msg.status !== undefined) healthPendingUpdates[msg.email].status = msg.status
            if (msg.started_at !== undefined) healthPendingUpdates[msg.email].started_at = msg.started_at
            if (msg.step_text !== undefined) healthPendingUpdates[msg.email].step_text = msg.step_text
            if (msg.result !== undefined) healthPendingUpdates[msg.email].result = msg.result
            if (msg.elapsed !== undefined) healthPendingUpdates[msg.email].elapsed = msg.elapsed
            scheduleHealthUpdate()
          }
        } catch (_) {}
      },
      end: () => {
        flushHealthUpdates()
        healthRunning.value = false
        if (healthEs.value) {
          healthEs.value.close()
          healthEs.value = null
        }
        healthNeedsReload.value = true
        ElMessage.success('批量验活任务已全部执行完成！')
      },
    }, () => {
      if (!healthRunning.value && healthEs.value) {
        healthEs.value.close()
        healthEs.value = null
      }
    })
  } catch (e) {
    flushHealthUpdates()
    healthRunning.value = false
    healthConfigCollapsed.value = false
    ElMessage.error('启动验活失败: ' + (e.response?.data?.detail || e.message))
  }
}

const healthModalLogBoxRef = ref(null)
const healthUserScrolledUp = ref(false)
function onHealthLogScroll() {
  const el = healthModalLogBoxRef.value
  if (!el) return
  const isNearBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= 45
  healthUserScrolledUp.value = !isNearBottom
}
function scrollHealthModalLog(force = false) {
  if (!force && healthUserScrolledUp.value) return
  nextTick(() => {
    const el = healthModalLogBoxRef.value
    if (el && (force || !healthUserScrolledUp.value)) {
      el.scrollTop = el.scrollHeight
    }
  })
}
function scrollToBottomHealthLog() {
  healthUserScrolledUp.value = false
  scrollHealthModalLog(true)
}

function scrollHealthLog() {
  const box = document.getElementById('health-log-box')
  if (box) box.scrollTop = box.scrollHeight
}

let healthLogPollTimer = 0
function stopHealthLogPoll() {
  if (healthLogPollTimer) {
    clearInterval(healthLogPollTimer)
    healthLogPollTimer = 0
  }
}

async function refreshHealthItemLog(silent = false) {
  const row = currentHealthLogItem.value
  if (!row) return
  if (!silent) healthLogLoading.value = true
  try {
    if (healthTaskId.value) {
      const res = await getHealthCheckLog(healthTaskId.value, row.email)
      healthLogLines.value = res.lines || []
    } else {
      healthLogLines.value = row.logs || ['暂无日志']
    }
    scrollHealthModalLog(false)
  } catch (e) {
    if (!silent) healthLogLines.value = ['读取日志失败: ' + (e.response?.data?.detail || e.message)]
  } finally {
    if (!silent) healthLogLoading.value = false
  }
}

function startHealthLogPoll() {
  stopHealthLogPoll()
  if (!healthLogModalVisible.value || !healthRunning.value || !healthTaskId.value) return
  healthLogPollTimer = window.setInterval(() => refreshHealthItemLog(true), 1000)
}

watch([healthLogModalVisible, healthRunning], () => {
  if (healthLogModalVisible.value && healthRunning.value) startHealthLogPoll()
  else stopHealthLogPoll()
})

async function openHealthItemLog(row) {
  currentHealthLogItem.value = row
  healthLogLines.value = []
  healthUserScrolledUp.value = false
  healthLogModalVisible.value = true
  await refreshHealthItemLog(false)
  nextTick(() => {
    scrollHealthModalLog(true)
  })
  startHealthLogPoll()
}

// ── 验活异常重试相关计算与操作 (支持批量与单行重试) ──
const failedHealthEmails = computed(() => {
  const _tick = healthTick.value
  return Object.values(healthItems.value)
    .filter((i) => {
      if (i.status === 'error') return true
      if (i.result && ['error', 'no_at', 'not_found', 'exception'].includes(i.result.status)) return true
      if (i.result && (i.result.label?.includes('异常') || i.result.label?.includes('失败') || i.result.label?.includes('429'))) return true
      return false
    })
    .map((i) => i.email)
})

function isHealthFailedRow(row) {
  if (row.status === 'error') return true
  if (row.result && ['error', 'no_at', 'not_found', 'exception'].includes(row.result.status)) return true
  if (row.result && (row.result.label?.includes('异常') || row.result.label?.includes('失败') || row.result.label?.includes('429'))) return true
  return false
}

function retryFailedHealthCheck() {
  const fails = failedHealthEmails.value
  if (!fails.length) {
    ElMessage.info('当前没有验活失败的账号')
    return
  }
  healthTargetEmails.value = [...fails]
  const newMap = { ...healthItems.value }
  for (const em of fails) {
    newMap[em] = { email: em, mode: healthForm.mode, status: 'pending', step_text: '待重试...', result: null, elapsed: 0 }
  }
  setHealthItems(newMap)
  startHealthCheckTask()
}

function retrySingleHealthCheck(row) {
  healthTargetEmails.value = [row.email]
  setHealthItems({
    ...healthItems.value,
    [row.email]: { email: row.email, mode: healthForm.mode, status: 'pending', step_text: '准备重试...', result: null, elapsed: 0 },
  })
  startHealthCheckTask()
}

// ════════════════════════ Token 刷新与重登工作台 (Token Refresh Studio) ════════════════════════
const refreshVisible = ref(false)
const refreshTargetEmails = ref([])

async function openTokenRefresh(scope = 'selected', customEmails = null) {
  let emails = []
  if (Array.isArray(customEmails) && customEmails.length) {
    emails = customEmails.map((e) => String(e).trim().toLowerCase()).filter(Boolean)
  } else if (typeof scope === 'string' && scope.includes('@')) {
    emails = [scope.trim().toLowerCase()]
  } else if (scope === 'selected') {
    if (!selected.value.length) {
      ElMessage.warning('请先在表格中勾选要刷新 Token 的账号')
      return
    }
    emails = selected.value.map((r) => r.email)
  } else if (scope === 'no_token' || scope === 'missing') {
    loading.value = true
    try {
      const res = await listRegisteredEmails('no_at')
      emails = res.emails || []
    } catch (e) {
      ElMessage.error('获取缺少Token账号失败: ' + e.message)
      return
    } finally {
      loading.value = false
    }
    if (!emails.length) {
      ElMessage.info('当前没有缺少或失效 Token 的账号')
      return
    }
  } else if (scope === 'all') {
    loading.value = true
    try {
      const res = await listRegisteredEmails('all')
      emails = res.emails || []
    } catch (e) {
      ElMessage.error('获取全量账号失败: ' + e.message)
      return
    } finally {
      loading.value = false
    }
    if (!emails.length) {
      ElMessage.info('号池为空，无账号可刷新')
      return
    }
  }

  refreshTargetEmails.value = emails
  refreshVisible.value = true
}

function openTokenRefreshForOne(row) {
  const email = typeof row === 'string' ? row : row?.email
  if (!email) return
  openTokenRefresh('single', [email])
}

function handleRefreshCommand(cmd) {
  if (cmd === 'refresh_selected') openTokenRefresh('selected')
  else if (cmd === 'refresh_no_token') openTokenRefresh('no_token')
  else if (cmd === 'refresh_all') openTokenRefresh('all')
}

function onTokenRefreshCompleted() {
  load(false)
}

// ════════════════════════ OAICS 资格检测 ════════════════════════
const oaVisible = ref(false)
const oaRunning = ref(false)
const oaTaskId = ref('')
const oaEs = ref(null)
const oaForm = reactive({
  proxies: '',
  workers: 2,
  rounds: 1,
  billingCountry: 'DE',
  currency: 'EUR',
  proxyCountry: 'BR',
  withPromo: false,
  skipProxyCheck: true,
  timeout: 30,
})

function loadProxyListToOA() {
  oaForm.proxies = proxyList.value.join('\n')
  const g = guessProxyCountry(oaForm.proxies)
  if (g) oaForm.proxyCountry = g
}

const oaItems = ref({})
const oaLogs = ref([])
const oaSummary = ref('')
const oaConfigCollapsed = ref(true) // 默认收起参数配置
const oaLogModalVisible = ref(false)
const currentOaLogItem = ref(null)
const oaLogLines = ref([])
const oaLogLoading = ref(false)

async function openOaItemLog(row) {
  currentOaLogItem.value = row
  oaLogLines.value = []
  oaUserScrolledUp.value = false
  oaLogModalVisible.value = true
  oaLogLoading.value = true

  try {
    if (oaTaskId.value) {
      const res = await getOACheckLog(oaTaskId.value, row.email)
      oaLogLines.value = res.lines || []
    } else {
      oaLogLines.value = row.logs || ['暂无日志']
    }
    scrollOaModalLog(true)
  } catch (e) {
    oaLogLines.value = ['读取日志失败: ' + (e.response?.data?.detail || e.message)]
  } finally {
    oaLogLoading.value = false
  }
}

const oaRows = computed(() =>
  Object.entries(oaItems.value).map(([email, item]) => ({ email, ...item })),
)

const oaStats = computed(() => {
  const items = Object.values(oaItems.value)
  const total = items.length || selected.value.length || 0
  const done = items.filter((i) => i.status === 'done').length
  const running = items.filter((i) => i.status === 'running').length
  const pending = items.filter((i) => i.status === 'pending').length
  const hit = items.filter((i) => i.result && i.result.state === 'OAICS').length
  const cs = items.filter((i) => i.result && i.result.state === 'CS').length
  const err = items.filter((i) => i.result && (i.result.state === 'ERROR' || i.result.state === 'NO_AT')).length
  const percent = total > 0 ? Math.round((done / total) * 100) : 0
  return { total, done, running, pending, hit, cs, err, percent }
})

function formatTerminalLine(line) {
  if (!line) return { time: '', text: '' }
  const str = String(line)
  const match = str.match(/^(\[\d{2}:\d{2}:\d{2}\])\s*(.*)$/)
  if (match) {
    return { time: match[1], text: match[2] }
  }
  return { time: '', text: str }
}

function getLogClass(line) {
  if (!line) return ''
  const s = String(line)
  if (s.includes('HIT') || s.includes('oaics_') || s.includes('★') || s.includes('◆') || s.includes('成功') || s.includes('存活') || s.includes('已入库')) return 'log-hit'
  if (s.includes('err=') || s.includes('ERROR') || s.includes('失败') || s.includes('封号') || s.includes('失效') || s.includes('异常') || s.includes('banned')) return 'log-err'
  if (s.includes('MISS') || s.includes('state=CS') || s.includes('Free') || s.includes('等待') || s.includes('需接码') || s.includes('未接')) return 'log-warn'
  if (s.includes('[1/') || s.includes('[2/') || s.includes('[3/') || s.includes('[4/') || s.includes('[5/') || s.includes('[6/') || s.includes('发起') || s.includes('开始')) return 'log-step'
  if (s.includes('网络出口') || s.includes('代理') || s.includes('cliproxy') || s.includes('目标国家')) return 'log-net'
  if (s.includes('[task]') || s.includes('HTTP')) return 'log-task'
  return ''
}

const OA_STATE_META = {
  OAICS:     { type: 'success', label: 'OAICS 命中' },
  CS:        { type: 'warning', label: 'CS (普通)' },
  OAIC:      { type: 'primary', label: 'OAIC' },
  NONE:      { type: 'info',    label: 'NONE' },
  ERROR:     { type: 'danger',  label: '出错' },
  NO_AT:     { type: 'danger',  label: '无AT' },
  CANCELLED: { type: 'info',    label: '已取消' },
  UNKNOWN:   { type: 'info',    label: '未知' },
}

function oaMeta(row) {
  if (!row || !row.oa_check) return null
  return OA_STATE_META[row.oa_check.state] || { type: 'info', label: row.oa_check.state || '未知' }
}

function openOA() {
  if (!selected.value.length) { ElMessage.info('请先勾选要检测的账号'); return }
  if (!oaForm.proxies && proxyList.value.length) {
    oaForm.proxies = proxyList.value.join('\n')
  }
  const g = guessProxyCountry(oaForm.proxies)
  if (g && (!oaForm.proxyCountry || oaForm.proxyCountry === 'BR')) {
    oaForm.proxyCountry = g
  }
  if (!oaRunning.value) {
    oaTaskId.value = ''
    oaItems.value = {}
    oaLogs.value = []
    oaSummary.value = ''
    oaConfigCollapsed.value = true
  }
  oaVisible.value = true
}

function closeOA() {
  if (oaRunning.value) {
    ElMessage.info('检测任务在后台继续运行，可随时重新打开查看进度')
  }
  if (oaEs.value && !oaRunning.value) {
    oaEs.value.close()
    oaEs.value = null
  }
  oaVisible.value = false
}

async function stopOA() {
  if (!oaTaskId.value) {
    oaRunning.value = false
    return
  }
  try {
    await stopOACheck(oaTaskId.value)
    ElMessage.success('已发送停止指令')
  } catch (_) {
    ElMessage.info('任务已结束')
  } finally {
    oaRunning.value = false
  }
}

function oaCount() {
  const items = Object.values(oaItems.value)
  return {
    total: items.length,
    done: items.filter((i) => i.status === 'done').length,
    running: items.filter((i) => i.status === 'running').length,
    pending: items.filter((i) => i.status === 'pending').length,
    cancelled: items.filter((i) => i.status === 'cancelled').length,
    hit: items.filter((i) => i.result && i.result.state === 'OAICS').length,
  }
}

async function startOA() {
  const emails = selected.value.map((r) => r.email)
  if (!emails.length) { ElMessage.info('请先勾选要检测的账号'); return }
  if (!oaForm.proxies.trim()) { ElMessage.warning('请先粘贴接码代理池（每行一个代理）'); return }
  if (oaEs.value) {
    oaEs.value.close()
    oaEs.value = null
  }
  oaRunning.value = true
  oaItems.value = {}
  oaLogs.value = []
  oaConfigCollapsed.value = true
  oaSummary.value = `任务启动中... (${emails.length} 个账号)`
  try {
    const res = await startOACheck({
      emails,
      proxies: oaForm.proxies,
      workers: oaForm.workers || 1,
      rounds: oaForm.rounds || 1,
      billing_country: oaForm.billingCountry || 'DE',
      currency: oaForm.currency || 'EUR',
      proxy_country: oaForm.proxyCountry || 'BR',
      with_promo: oaForm.withPromo,
      skip_proxy_check: oaForm.skipProxyCheck,
      timeout: oaForm.timeout || 30,
    })
    const taskId = res.taskId || res.task_id
    if (!taskId) throw new Error('未获取到任务 ID')
    oaTaskId.value = taskId
    oaSummary.value = `正在检测 0/${emails.length} 个账号...`
    oaEs.value = createSSE(oaCheckStreamUrl(taskId), {
      init: (ev) => {
        try {
          const snap = JSON.parse(ev.data)
          if (snap.items) oaItems.value = snap.items
        } catch (_) {}
      },
      progress: (ev) => {
        try {
          const msg = JSON.parse(ev.data)
          if (msg.email) {
            oaItems.value[msg.email] = { status: msg.status, result: msg.result || null }
            const c = oaCount()
            oaSummary.value = `正在检测：已完成 ${c.done}/${c.total} (命中 ${c.hit} 个 OAICS)`
          }
        } catch (_) {}
      },
      log: (ev) => {
        try {
          const msg = JSON.parse(ev.data)
          if (msg.line) {
            oaLogs.value.push(msg.line)
            if (oaLogs.value.length > 500) oaLogs.value.splice(0, oaLogs.value.length - 500)
            nextTick(scrollOaLog)
            if (oaLogModalVisible.value && currentOaLogItem.value) {
              const targetEmail = currentOaLogItem.value.email
              if (msg.email ? msg.email === targetEmail : String(msg.line || '').includes(targetEmail)) {
                oaLogLines.value.push(msg.line)
                scrollOaModalLog()
              }
            }
          }
        } catch (_) {}
      },
      end: () => {
        const c = oaCount()
        oaSummary.value = `检测完成！共 ${c.total} 个账号，完成 ${c.done} 个，命中 ${c.hit} 个 OAICS`
        oaRunning.value = false
        if (oaEs.value) {
          oaEs.value.close()
          oaEs.value = null
        }
        load(false)
      },
    }, () => {
      if (!oaRunning.value && oaEs.value) {
        oaEs.value.close()
        oaEs.value = null
      }
    })
  } catch (e) {
    oaRunning.value = false
    oaSummary.value = ''
    oaConfigCollapsed.value = false
    ElMessage.error('启动资格检测失败: ' + (e.response?.data?.detail || e.message))
  }
}

const oaModalLogBoxRef = ref(null)
const oaUserScrolledUp = ref(false)
function onOaLogScroll() {
  const el = oaModalLogBoxRef.value
  if (!el) return
  const isNearBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= 45
  oaUserScrolledUp.value = !isNearBottom
}
function scrollOaModalLog(force = false) {
  if (!force && oaUserScrolledUp.value) return
  nextTick(() => {
    const el = oaModalLogBoxRef.value
    if (el && (force || !oaUserScrolledUp.value)) {
      el.scrollTop = el.scrollHeight
    }
  })
}
function scrollToBottomOaLog() {
  oaUserScrolledUp.value = false
  scrollOaModalLog(true)
}

function scrollOaLog() {
  const box = document.getElementById('oa-log-box')
  if (box) box.scrollTop = box.scrollHeight
}

// ════════════════════════ OAuth 导出 (Codex OAuth / CPA / Sub2API) ════════════════════════
const oauthVisible = ref(false)
const oauthRunning = ref(false)
const oauthTaskId = ref('')
const oauthEs = ref(null)
const oauthConfigCollapsed = ref(true)
const oauthTargetEmails = ref([])
const oauthItems = shallowRef({})
const oauthTick = ref(0)
const oauthTableRef = ref(null)
const oauthPage = ref(1)
const oauthPageSize = ref(50)
const oauthFilter = ref('all')
const oauthSearch = ref('')

let oauthUpdateTimer = null
let oauthPendingUpdates = Object.create(null)

function bumpOAuthTick() {
  oauthTick.value++
}

function stripOAuthItemLogs(raw) {
  if (!raw || typeof raw !== 'object') return {}
  const slim = Object.create(null)
  for (const em of Object.keys(raw)) {
    const it = raw[em] || {}
    const row = { ...it }
    delete row.logs
    row.email = row.email || em
    if (!row._providerMeta) row._providerMeta = getEmailProviderMeta(row.email)
    slim[em] = row
  }
  return slim
}

function flushOAuthUpdates() {
  if (oauthUpdateTimer) {
    clearTimeout(oauthUpdateTimer)
    oauthUpdateTimer = null
  }
  const patches = oauthPendingUpdates
  oauthPendingUpdates = Object.create(null)
  const emails = Object.keys(patches)
  if (!emails.length) return
  const map = oauthItems.value
  for (const em of emails) {
    const up = patches[em]
    const cur = map[em]
    if (!cur) {
      map[em] = {
        email: em,
        status: 'pending',
        result: null,
        elapsed: 0,
        _providerMeta: getEmailProviderMeta(em),
        ...up,
      }
    } else {
      Object.assign(cur, up)
    }
  }
  oauthItems.value = { ...map }
  bumpOAuthTick()
  layoutOAuthTable()
}

function layoutOAuthTable() {
  nextTick(() => {
    oauthTableRef.value?.doLayout?.()
  })
}

function scheduleOAuthUpdate() {
  if (oauthUpdateTimer) return
  oauthUpdateTimer = window.setTimeout(() => {
    oauthUpdateTimer = null
    flushOAuthUpdates()
  }, 250)
}

function resetOAuthListView() {
  oauthPage.value = 1
  oauthFilter.value = 'all'
  oauthSearch.value = ''
}

const OAUTH_FORM_KEY = 'gpt_oauth_export_form_v2'
let savedOAuth = {}
try { savedOAuth = JSON.parse(localStorage.getItem(OAUTH_FORM_KEY) || '{}') } catch (_) {}

const DEFAULT_SMS_PROVIDERS = [
  {
    kind: 'smsbower',
    display_name: 'SmsBower',
    short_label: '即退款',
    description: '遇到手机号验证时自动租号收码，未接通即时取消即退款',
    uses_cdk_pool: false,
    uses_country: true,
    uses_price_tiers: true,
    uses_provider_ids: true,
    uses_auto_country: true,
    country_scheme: 'activate',
    default_country: '52',
    recommended_timeout: 80,
    max_timeout: 90,
    timeout_hint: '推荐 60~85 秒。超过 90 秒容易导致 OpenAI 授权会话过期。',
  },
  {
    kind: 'herosms',
    display_name: 'HeroSMS',
    short_label: '20分退',
    description: '与 SmsBower 同协议，号码约 20 分钟未用自动退款',
    uses_cdk_pool: false,
    uses_country: true,
    uses_price_tiers: true,
    uses_provider_ids: true,
    uses_auto_country: true,
    country_scheme: 'activate',
    default_country: '52',
    recommended_timeout: 80,
    max_timeout: 90,
    timeout_hint: '推荐 60~85 秒。超过 90 秒容易导致 OpenAI 授权会话过期。',
  },
  {
    kind: 'cdk_sms',
    aliases: ['cdk', 'ndk', 'ndk_cdk', 'lubansms'],
    display_name: 'CDK 卡密兑换',
    short_label: 'ndk.cc.cd',
    description: '从 CDK 号池提取卡密兑换号码，被拒自动免费换号，多次卡支持长期复用',
    uses_cdk_pool: true,
    uses_country: false,
    uses_price_tiers: false,
    uses_provider_ids: false,
    uses_auto_country: false,
    country_scheme: 'activate',
    default_country: '44',
    recommended_timeout: 35,
    max_timeout: 60,
    timeout_hint: 'CDK 推荐 30~35 秒。超过 45 秒容易导致 OpenAI 授权会话过期。',
  },
  {
    kind: 'vaksms',
    aliases: ['vak-sms', 'vak_sms', 'vak'],
    display_name: 'Vak-SMS',
    short_label: 'vak-sms.com',
    description: 'vak-sms.com 官方 JSON 接码，国家用 ISO2（th 泰国），OpenAI 业务码 dr',
    uses_cdk_pool: false,
    uses_country: true,
    uses_price_tiers: true,
    uses_provider_ids: false,
    uses_auto_country: true,
    country_scheme: 'iso2',
    default_country: 'th',
    recommended_timeout: 80,
    max_timeout: 90,
    timeout_hint: '推荐 60~85 秒。超过 90 秒容易导致 OpenAI 授权会话过期。',
  },
]
const smsProviders = ref(DEFAULT_SMS_PROVIDERS)

function findSmsProviderMeta(kind) {
  const k = String(kind || '').trim()
  return (
    smsProviders.value.find((p) => p.kind === k || (p.aliases || []).includes(k)) || null
  )
}

function normalizeOAuthSmsStrategy(saved) {
  const raw = saved?.smsStrategy
  if (raw === 'skip') return 'skip'
  if (raw === 'cdk') return 'cdk_sms'
  if (raw === 'api') {
    const p = saved?.smsProvider
    return p && p !== 'cdk_sms' ? p : 'smsbower'
  }
  if (raw) return raw
  if (saved?.smsEnabled === false) return 'skip'
  if (saved?.smsProvider) return saved.smsProvider
  return 'smsbower'
}

const _oauthSmsStrategy = normalizeOAuthSmsStrategy(savedOAuth)

const oauthForm = reactive({
  proxy: savedOAuth.proxy || '__POOL__',
  proxyCountry: savedOAuth.proxyCountry || 'RANDOM_HOT',
  workers: savedOAuth.workers || 5,
  timeout: savedOAuth.timeout || 45,
  smsStrategy: _oauthSmsStrategy,
  smsEnabled: _oauthSmsStrategy !== 'skip',
  smsProvider: _oauthSmsStrategy === 'skip' ? (savedOAuth.smsProvider || 'smsbower') : _oauthSmsStrategy,
  smsApiKey: savedOAuth.smsApiKey || '',
  smsCdkUrl: savedOAuth.smsCdkUrl || 'https://ndk.cc.cd',
  smsCountry: savedOAuth.smsCountry || '52',
  smsMaxPrice: savedOAuth.smsMaxPrice || '',
  smsProviderIds: savedOAuth.smsProviderIds || '',
  smsExceptProviderIds: Array.isArray(savedOAuth.smsExceptProviderIds)
    ? savedOAuth.smsExceptProviderIds
    : String(savedOAuth.smsExceptProviderIds || '')
        .split(/[,;]/)
        .map((s) => s.trim())
        .filter(Boolean),
  smsMaxAttempts: savedOAuth.smsMaxAttempts || 3,
  smsTimeout: savedOAuth.smsTimeout || 80,
})

const oauthCdkStats = ref({
  total: 0,
  available: 0,
  exhausted: 0,
  expired: 0,
  total_success_codes: 0,
})

const oauthSmsMeta = computed(() =>
  oauthForm.smsStrategy === 'skip' ? null : findSmsProviderMeta(oauthForm.smsStrategy),
)

function oauthCountryFits(cid, meta) {
  const s = String(cid || '').trim()
  if (!s || s.toUpperCase() === 'AUTO') return true
  if (meta?.country_scheme === 'iso2' || /^[a-z]{2}$/i.test(String(meta?.default_country || ''))) {
    return /^[a-z]{2}$/i.test(s)
  }
  return /^\d+$/.test(s)
}

function coerceOAuthSmsCountry() {
  const meta = oauthSmsMeta.value
  if (!meta?.uses_country) return
  const resolved = resolveOAuthSmsCountry(oauthForm.smsCountry)
  if (resolved && resolved !== oauthForm.smsCountry) {
    oauthForm.smsCountry = resolved
  }
  if (!oauthCountryFits(oauthForm.smsCountry, meta)) {
    oauthForm.smsCountry = meta.default_country || (meta.country_scheme === 'iso2' ? 'th' : '52')
    if (!meta.uses_provider_ids) {
      oauthForm.smsProviderIds = ''
      oauthForm.smsExceptProviderIds = []
    }
  }
}

function onOAuthStrategyChange(val) {
  if (val === 'skip') {
    oauthForm.smsEnabled = false
    return
  }
  oauthForm.smsEnabled = true
  oauthForm.smsProvider = val
  const meta = findSmsProviderMeta(val)
  if (meta?.recommended_timeout) {
    oauthForm.smsTimeout = meta.recommended_timeout
  }
  coerceOAuthSmsCountry()
  oauthActiveTab.value = 'sms'
  loadSmsCountries()
  loadOAuthPriceTiers()
}

async function loadSmsProviderCatalog() {
  try {
    const res = await getSmsProviders()
    if (Array.isArray(res.providers) && res.providers.length) {
      smsProviders.value = res.providers
    }
  } catch (_) {}
}

watch(oauthForm, (v) => {
  try { localStorage.setItem(OAUTH_FORM_KEY, JSON.stringify(v)) } catch (_) {}
}, { deep: true })

const featVisible = ref(false)
const featLoading = ref(false)
const featOutcome = ref('')
const featWeights = ref({
  overall: { n: 0, ok: 0, rate: 0 },
  by_proxy_country: [],
  by_impersonate: [],
  by_browser: [],
  by_sms_country: [],
  by_sms_operator: [],
  by_login_path: [],
  by_error_class: [],
})
const featRows = ref([])

const FEAT_OUTCOME_META = {
  success: { label: '成功', tone: 'ok' },
  failed: { label: '失败', tone: 'bad' },
  error: { label: '异常', tone: 'warn' },
  need_phone: { label: '需接码', tone: 'warn' },
  cancelled: { label: '取消', tone: 'mute' },
  not_found: { label: '未找到', tone: 'mute' },
  banned: { label: '封号', tone: 'bad' },
}

const FEAT_ERROR_META = {
  need_phone: '需接码',
  session_expired: '会话过期',
  sms_no_numbers: '无号',
  phone_rejected: '号段拒',
  sms_fail: '接码失败',
  otp_fail: 'OTP 失败',
  password_fail: '密码失败',
  callback_fail: '缺 callback',
  token_fail: '换 token 失败',
  cancelled: '取消',
  not_found: '未找到',
  banned: '封号',
  other: '其他',
}

function featOutcomeMeta(v) {
  return FEAT_OUTCOME_META[v] || { label: v || '—', tone: 'mute' }
}

function featErrorLabel(v) {
  if (!v) return '—'
  return FEAT_ERROR_META[v] || v
}

function featPct(rate) {
  const n = Number(rate || 0)
  return `${Math.round(n * 100)}%`
}

function featWhen(ts) {
  const n = Number(ts || 0)
  if (!n) return '—'
  const d = new Date(n * 1000)
  const p = (x) => String(x).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

function featBarItems(list, key) {
  return (list || []).slice(0, 6).map((it) => ({
    key: String(it[key] || '未填'),
    n: it.n || 0,
    ok: it.ok || 0,
    rate: it.rate || 0,
  }))
}

const featKpis = computed(() => {
  const o = featWeights.value.overall || { n: 0, ok: 0, rate: 0 }
  const failN = Math.max(0, (o.n || 0) - (o.ok || 0))
  const needN = (featWeights.value.by_error_class || []).find((x) => x.error_class === 'need_phone')?.n || 0
  return [
    { label: '总尝试', value: o.n || 0, tone: 'plain' },
    { label: '成功', value: o.ok || 0, tone: 'ok' },
    { label: '失败/异常', value: failN, tone: 'bad' },
    { label: '需接码', value: needN, tone: 'warn' },
    { label: '成功率', value: featPct(o.rate), tone: 'ok' },
  ]
})

const featProxyBars = computed(() => featBarItems(featWeights.value.by_proxy_country, 'proxy_country'))
const featFpBars = computed(() => featBarItems(featWeights.value.by_impersonate, 'impersonate'))
const featSmsBars = computed(() => featBarItems(featWeights.value.by_sms_country, 'sms_country'))
const featPathBars = computed(() => featBarItems(featWeights.value.by_login_path, 'login_path'))
const featRecent = computed(() => (featRows.value || []).slice(0, 8))

async function loadFeatBoard() {
  featLoading.value = true
  try {
    const featParams = { limit: 8 }
    if (featOutcome.value) featParams.outcome = featOutcome.value
    const [w, f] = await Promise.all([
      getOAuthExportFeatureWeights(1),
      getOAuthExportFeatures(featParams),
    ])
    featWeights.value = {
      overall: w.overall || { n: 0, ok: 0, rate: 0 },
      by_proxy_country: w.by_proxy_country || [],
      by_impersonate: w.by_impersonate || [],
      by_browser: w.by_browser || [],
      by_sms_country: w.by_sms_country || [],
      by_sms_operator: w.by_sms_operator || [],
      by_login_path: w.by_login_path || [],
      by_error_class: w.by_error_class || [],
    }
    featRows.value = f.rows || []
  } catch (e) {
    ElMessage.error('加载授权特征失败: ' + (e.message || e))
  } finally {
    featLoading.value = false
  }
}

async function openFeatBoard() {
  featVisible.value = true
  await loadFeatBoard()
}

async function onFeatOutcome(v) {
  featOutcome.value = v
  await loadFeatBoard()
}

const oauthActiveTab = ref('network')

// ──────────── 表格自定义列显示配置 (持久化到 localStorage) ────────────
const DEFAULT_COLUMN_VISIBILITY = {
  security: true,
  tokens: true,
  atExp: true,
  status: true,
  export: true,
  time: true,
}

const columnVisibility = reactive({ ...DEFAULT_COLUMN_VISIBILITY })

try {
  const savedCols = localStorage.getItem('reg_col_visibility')
  if (savedCols) {
    Object.assign(columnVisibility, JSON.parse(savedCols))
  }
} catch (_) {}

watch(
  () => ({ ...columnVisibility }),
  (val) => {
    try {
      localStorage.setItem('reg_col_visibility', JSON.stringify(val))
    } catch (_) {}
  },
  { deep: true }
)

function resetColumnVisibility() {
  Object.assign(columnVisibility, DEFAULT_COLUMN_VISIBILITY)
  ElMessage.success('已恢复默认列配置')
}

// ──────────── 表格密度切换 (Compact / Default / Relaxed) ────────────
const savedDensity = localStorage.getItem('reg_table_density')
const tableDensity = ref(['compact', 'default', 'relaxed'].includes(savedDensity) ? savedDensity : 'default')

function setTableDensity(val) {
  tableDensity.value = val
  try {
    localStorage.setItem('reg_table_density', val)
  } catch (_) {}
  nextTick(() => {
    tableRef.value?.doLayout?.()
  })
  const labelMap = {
    compact: '紧凑模式 (高屏效)',
    default: '标准模式 (默认)',
    relaxed: '宽松模式 (大间距)'
  }
  ElMessage.success(`表格行高已切换为：${labelMap[val] || val}`)
}

// ──────────── 表格引用与全局快捷键 ────────────
const tableRef = ref(null)
const searchInputRef = ref(null)

function clearSelected() {
  selected.value = []
  if (tableRef.value) {
    tableRef.value.clearSelection()
  }
}

function handleGlobalKeydown(e) {
  // ESC: 取消勾选
  if (e.key === 'Escape') {
    if (selected.value.length > 0) {
      clearSelected()
    }
  }
  // Cmd/Ctrl + K: 聚焦搜索
  if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
    e.preventDefault()
    if (searchInputRef.value) {
      searchInputRef.value.focus()
    }
  }
}

onMounted(() => {
  load()
  loadDomains()
  loadCountries()
  loadSmsProviderCatalog()
  loadSmsCountries()
  loadRegSummary()
  window.addEventListener('keydown', handleGlobalKeydown)
})

onUnmounted(() => {
  stopOAuthLogPoll()
  flushOAuthUpdates()
  window.removeEventListener('keydown', handleGlobalKeydown)
})

function saveOAuthFormDefault() {
  try {
    localStorage.setItem(OAUTH_FORM_KEY, JSON.stringify(oauthForm))
    if (oauthForm.smsApiKey && oauthForm.smsApiKey !== '***') {
      saveSmsConfig({
        sms_enabled: oauthForm.smsEnabled ? '1' : '0',
        sms_provider: (oauthForm.smsStrategy !== 'skip' ? oauthForm.smsStrategy : oauthForm.smsProvider) || 'smsbower',
        sms_api_key: oauthForm.smsApiKey,
        sms_country: String(oauthForm.smsCountry || '52').trim(),
        sms_max_price: String(oauthForm.smsMaxPrice || '').trim(),
        sms_provider_ids: String(oauthForm.smsProviderIds || '').trim(),
        sms_except_provider_ids: Array.isArray(oauthForm.smsExceptProviderIds)
          ? oauthForm.smsExceptProviderIds.join(',')
          : String(oauthForm.smsExceptProviderIds || '').trim(),
        sms_max_phone_attempts: String(oauthForm.smsMaxAttempts || '3'),
        sms_per_phone_timeout: String(oauthForm.smsTimeout || '80'),
      }).catch(() => {})
    }
    ElMessage.success('OAuth 参数配置已成功保存为默认！')
  } catch (e) {
    ElMessage.error('保存配置失败: ' + e.message)
  }
}

const smsAllCountries = ref([])
const smsCountriesLoading = ref(false)

function formatStockCount(raw) {
  const n = Number(raw)
  if (!Number.isFinite(n) || n <= 0) return '暂无库存'
  if (n >= 10000) {
    const w = n / 10000
    const s = w >= 10 ? String(Math.round(w * 10) / 10) : w.toFixed(2).replace(/0+$/, '').replace(/\.$/, '')
    return `余${s}万`
  }
  return `余${n}`
}

function formatSmsCountryOption(c) {
  const id = String(c.id || '')
  const iso2 = /^[a-z]{2}$/i.test(id)
  if (iso2) {
    const bits = [`${c.name_cn} ${id.toLowerCase()}`]
    if (c.price != null && c.price !== '') bits.push(`起${c.price}$`)
    if (c.count != null && c.count !== '') bits.push(formatStockCount(c.count))
    else bits.push('暂无库存')
    return { value: id, label: bits.join(' · '), safe: !!c.openai_sms_safe }
  }
  const bits = [`${id} · ${c.name_cn}`]
  if (c.count != null && c.count !== '') {
    const n = Number(c.count)
    bits.push(Number.isFinite(n) && n > 0 ? `余${c.count}` : '暂无库存')
  }
  if (c.price != null && c.price !== '') bits.push(`${c.price}`)
  return { value: id, label: bits.join(' · '), safe: !!c.openai_sms_safe }
}

const SMS_COUNTRY_OPTIONS = computed(() => {
  const rest = (smsAllCountries.value || []).map(formatSmsCountryOption)
  if (oauthSmsMeta.value?.country_scheme === 'iso2') return rest
  const auto = { value: 'AUTO', label: '🌐 智能多国自动轮换', safe: false }
  return [auto, ...rest]
})

async function loadSmsCountries() {
  const kind = oauthForm.smsStrategy === 'skip' ? oauthForm.smsProvider : oauthForm.smsStrategy
  const meta = findSmsProviderMeta(kind)
  if (meta && !meta.uses_country) {
    smsAllCountries.value = []
    return
  }
  smsCountriesLoading.value = true
  try {
    const r = await getSmsAllCountries(kind || oauthForm.smsProvider || 'smsbower')
    smsAllCountries.value = r.countries || []
    coerceOAuthSmsCountry()
  } catch (e) {
    if (!smsAllCountries.value.length) {
      ElMessage.warning('加载接码国家失败，可直接输入国家 ID')
    }
  } finally {
    smsCountriesLoading.value = false
  }
}

const oauthPriceTiers = ref([])
const oauthPriceTiersLoading = ref(false)

function resolveOAuthSmsCountry(raw) {
  const v = String(raw || '').trim()
  if (!v || v === 'AUTO') return v
  if (oauthSmsMeta.value?.country_scheme !== 'iso2') return v
  if (/^[a-z]{2}$/i.test(v)) return v.toLowerCase()
  const list = smsAllCountries.value || []
  const hit = list.find((c) => String(c.name_cn) === v || String(c.id) === v)
  if (hit?.id) return String(hit.id)
  const fuzzy = list.find((c) => String(c.name_cn || '').includes(v) || String(c.id || '').toLowerCase() === v.toLowerCase())
  return fuzzy?.id ? String(fuzzy.id) : v
}

function onOAuthSmsCountryChange(val) {
  const resolved = resolveOAuthSmsCountry(val)
  if (resolved && resolved !== oauthForm.smsCountry) {
    oauthForm.smsCountry = resolved
  }
  loadOAuthPriceTiers()
}

async function loadOAuthPriceTiers() {
  const kind = oauthForm.smsStrategy === 'skip' ? oauthForm.smsProvider : oauthForm.smsStrategy
  const meta = findSmsProviderMeta(kind)
  const c = resolveOAuthSmsCountry(oauthForm.smsCountry)
  if (c && c !== oauthForm.smsCountry) oauthForm.smsCountry = c
  if (!c || c === 'AUTO' || !meta?.uses_price_tiers) {
    oauthPriceTiers.value = []
    return
  }
  oauthPriceTiersLoading.value = true
  try {
    const res = await getSmsPriceTiers(c, 'dr', kind || 'smsbower')
    if (res.country && res.country !== oauthForm.smsCountry) {
      oauthForm.smsCountry = res.country
    }
    oauthPriceTiers.value = res.tiers || []
    if (oauthPriceTiers.value.length) {
      const sum = oauthPriceTiers.value.reduce((s, t) => s + (Number(t.count) || 0), 0)
      const prices = oauthPriceTiers.value.map((t) => Number(t.price)).filter((n) => Number.isFinite(n) && n > 0)
      const idx = smsAllCountries.value.findIndex((x) => String(x.id) === String(c))
      if (idx >= 0) {
        const cur = smsAllCountries.value[idx]
        smsAllCountries.value[idx] = {
          ...cur,
          count: Math.max(Number(cur.count) || 0, sum),
          price: prices.length ? Math.min(Number(cur.price) || prices[0], ...prices) : cur.price,
        }
      }
    }
  } catch (e) {
    oauthPriceTiers.value = []
  } finally {
    oauthPriceTiersLoading.value = false
  }
}

watch(
  () => [oauthForm.smsCountry, oauthForm.smsProvider, oauthForm.smsStrategy],
  () => {
    loadOAuthPriceTiers()
  },
  { immediate: true },
)

watch(
  () => oauthForm.smsProvider,
  () => {
    loadSmsCountries()
  },
)

const oauthLogModalVisible = ref(false)
const currentOAuthLogItem = ref(null)
const oauthLogLines = ref([])
const oauthLogLoading = ref(false)
const oauthLogView = computed(() =>
  oauthLogLines.value.map((line, idx) => {
    const p = formatTerminalLine(line)
    return { idx, cls: getLogClass(line), time: p.time, text: p.text }
  }),
)

const oauthStats = computed(() => {
  const _tick = oauthTick.value
  const map = oauthItems.value
  const tot = oauthTargetEmails.value.length || Object.keys(map).length
  let done = 0
  let running = 0
  let pending = 0
  let success = 0
  let need_phone = 0
  let banned = 0
  let error = 0
  for (const em in map) {
    const i = map[em]
    const st = i.status
    const rs = i.result && i.result.status
    if (st === 'running') running++
    else if (st === 'pending') pending++
    else if (st === 'done') {
      done++
      if (rs === 'success') success++
      else if (rs === 'need_phone') need_phone++
      else if (rs === 'banned' || rs === 'deactivated') banned++
      else error++
    }
  }
  return {
    total: tot,
    done,
    running,
    pending,
    success,
    need_phone,
    banned,
    error,
    percent: tot > 0 ? Math.round((done / tot) * 100) : 0,
  }
})

const oauthFilteredRows = computed(() => {
  const _tick = oauthTick.value
  const map = oauthItems.value
  const kw = oauthSearch.value.trim().toLowerCase()
  const f = oauthFilter.value
  const source = oauthTargetEmails.value.length ? oauthTargetEmails.value : Object.keys(map)
  const out = []
  for (const em of source) {
    const item = map[em]
    if (!item) continue
    if (kw && !em.toLowerCase().includes(kw)) continue
    const st = item.status
    const rs = item.result && item.result.status
    if (f === 'running' && st !== 'running') continue
    else if (f === 'pending' && st !== 'pending') continue
    else if (f === 'success' && rs !== 'success') continue
    else if (f === 'phone' && rs !== 'need_phone') continue
    else if (f === 'banned' && rs !== 'banned' && rs !== 'deactivated') continue
    else if (f === 'fail' && !(st === 'done' && rs && rs !== 'success' && rs !== 'need_phone' && rs !== 'banned' && rs !== 'deactivated')) continue
    out.push(item)
  }
  return out
})

const oauthDisplayRows = computed(() => {
  const rows = oauthFilteredRows.value
  const start = (oauthPage.value - 1) * oauthPageSize.value
  return rows.slice(start, start + oauthPageSize.value)
})

watch(
  () => [oauthFilteredRows.value.length, oauthPageSize.value],
  ([len, size]) => {
    const maxPage = Math.max(1, Math.ceil((len || 0) / (size || 50)))
    if (oauthPage.value > maxPage) oauthPage.value = maxPage
    layoutOAuthTable()
  },
)

watch(oauthConfigCollapsed, () => layoutOAuthTable())

const oauthBatchHint = computed(() => {
  const n = oauthStats.value.total
  if (n < 80) return ''
  return `已选 ${n} 个账号：表格按页显示（每页 ${oauthPageSize.value} 条），可用上方筛选只看进行中 / 失败。关闭弹窗不会中断任务。`
})

function setOAuthFilter(f) {
  oauthFilter.value = f
  oauthPage.value = 1
  layoutOAuthTable()
}

// 失败或未成功的账号邮箱列表 (用于批量重新授权)
const failedOAuthEmails = computed(() => {
  const _tick = oauthTick.value
  const map = oauthItems.value
  const out = []
  for (const em in map) {
    const i = map[em]
    if (i.status === 'done' && i.result && i.result.status !== 'success' && i.result.status !== 'banned' && i.result.status !== 'deactivated') out.push(i.email)
  }
  return out
})

function handleOAuthCommand(cmd) {
  if (cmd === 'oauth_selected') {
    openOAuthExport('selected')
  } else if (cmd === 'oauth_all') {
    openOAuthExport('all')
  } else if (cmd === 'recover_selected') {
    doRecoverOAuth('selected')
  } else if (cmd === 'recover_all') {
    doRecoverOAuth('all')
  }
}

async function openOAuthExport(target = 'selected') {
  let emails = []
  if (target === 'selected') {
    emails = selected.value.map((r) => r.email)
    if (!emails.length) {
      ElMessage.warning('请先在表格中勾选要导出的账号')
      return
    }
  } else if (target === 'all') {
    try {
      const res = await listRegisteredEmails('all')
      emails = res.emails || []
    } catch (e) {
      ElMessage.error('加载账号列表失败: ' + e.message)
      return
    }
  }

  oauthTargetEmails.value = emails

  if (!oauthRunning.value) {
    oauthTaskId.value = ''
    oauthConfigCollapsed.value = true
    resetOAuthListView()
    const initMap = {}
    const rowMap = new Map(rows.value.map((r) => [r.email, r]))
    for (const em of emails) {
      const r = rowMap.get(em)
      const hasOauth = r && (r.oauth_status === 'success' || (r.refresh_token && r.refresh_token.length > 10))
      const meta = getEmailProviderMeta(em)
      if (hasOauth) {
        initMap[em] = {
          email: em,
          status: 'done',
          step_text: '已拥有 OAuth 凭证',
          result: { status: 'success', label: '已授权' },
          elapsed: 0,
          _providerMeta: meta,
        }
      } else {
        initMap[em] = { email: em, status: 'pending', result: null, elapsed: 0, _providerMeta: meta }
      }
    }
    oauthItems.value = initMap
    bumpOAuthTick()
  }

  oauthVisible.value = true
  loadSmsProviderCatalog()
  loadSmsCountries()
  loadOAuthPriceTiers()
  loadOAuthSmsMeta()
}

async function loadOAuthSmsMeta() {
  try {
    const [cfgRes, statsRes] = await Promise.allSettled([
      getSmsConfig(),
      getSmsCdkPoolStats(),
    ])
    if (cfgRes.status === 'fulfilled' && cfgRes.value?.config) {
      const cfg = cfgRes.value.config
      if (!localStorage.getItem(OAUTH_FORM_KEY)) {
        if (cfg.sms_provider) {
          oauthForm.smsStrategy = cfg.sms_enabled === '1' ? cfg.sms_provider : 'skip'
          oauthForm.smsProvider = cfg.sms_provider
          oauthForm.smsEnabled = cfg.sms_enabled === '1'
        }
      }
      if (cfg.sms_cdk_url) {
        oauthForm.smsCdkUrl = cfg.sms_cdk_url
      }
      coerceOAuthSmsCountry()
    }
    if (statsRes.status === 'fulfilled' && statsRes.value?.stats) {
      oauthCdkStats.value = statsRes.value.stats
    }
  } catch (_) {}
}

function closeOAuthExport() {
  if (oauthRunning.value) {
    ElMessage.info('OAuth 导出任务在后台继续运行，可随时重新打开查看进度')
  }
  if (oauthEs.value && !oauthRunning.value) {
    oauthEs.value.close()
    oauthEs.value = null
  }
  flushOAuthUpdates()
  oauthVisible.value = false
}

async function stopOAuthExportTask() {
  if (!oauthTaskId.value) {
    oauthRunning.value = false
    return
  }
  try {
    await stopOAuthExport(oauthTaskId.value)
    ElMessage.success('已发送停止指令')
  } catch (_) {
    ElMessage.info('任务已结束')
  } finally {
    flushOAuthUpdates()
    oauthRunning.value = false
  }
}

async function startOAuthExportTask() {
  const emails = oauthTargetEmails.value
  if (!emails.length) {
    ElMessage.warning('没有待导出的账号列表')
    return
  }

  if (oauthEs.value) {
    oauthEs.value.close()
    oauthEs.value = null
  }

  flushOAuthUpdates()
  oauthRunning.value = true
  oauthConfigCollapsed.value = true
  oauthPage.value = 1
  oauthSearch.value = ''

  const initMap = {}
  for (const em of emails) {
    initMap[em] = { email: em, status: 'pending', result: null, elapsed: 0, _providerMeta: getEmailProviderMeta(em) }
  }
  oauthItems.value = initMap
  bumpOAuthTick()

  let proxiesParam = ''
  let proxyParam = ''
  if (oauthForm.proxy === '__POOL__') {
    proxiesParam = proxyList.value.join('\n')
  } else {
    proxyParam = (oauthForm.proxy || '').trim()
  }

  try {
    const skipSms = oauthForm.smsStrategy === 'skip'
    const effectiveProvider = skipSms
      ? (oauthForm.smsProvider || 'smsbower')
      : oauthForm.smsStrategy

    const res = await startOAuthExport({
      emails,
      proxies: proxiesParam,
      proxy: proxyParam,
      proxy_country: oauthForm.proxyCountry || '',
      workers: oauthForm.workers || 5,
      timeout: oauthForm.timeout || 45,
      sms_enabled: !skipSms,
      sms_provider: effectiveProvider,
      sms_api_key: oauthForm.smsApiKey || '',
      sms_cdk_url: oauthForm.smsCdkUrl || 'https://ndk.cc.cd',
      sms_country: String(oauthForm.smsCountry || '52').trim(),
      sms_max_price: String(oauthForm.smsMaxPrice || '').trim(),
      sms_provider_ids: String(oauthForm.smsProviderIds || '').trim(),
      sms_except_provider_ids: Array.isArray(oauthForm.smsExceptProviderIds)
        ? oauthForm.smsExceptProviderIds.join(',')
        : String(oauthForm.smsExceptProviderIds || '').trim(),
      sms_max_attempts: Number(oauthForm.smsMaxAttempts) || 3,
      sms_timeout: Number(oauthForm.smsTimeout) || 80,
    })
    const taskId = res.taskId || res.task_id
    if (!taskId) throw new Error('未获取到任务 ID')
    oauthTaskId.value = taskId
    connectOAuthStream(taskId)
  } catch (e) {
    oauthRunning.value = false
    oauthConfigCollapsed.value = false
    ElMessage.error('启动 OAuth 导出失败: ' + (e.response?.data?.detail || e.message))
  }
}

function connectOAuthStream(taskId) {
  if (oauthEs.value) {
    oauthEs.value.close()
    oauthEs.value = null
  }

  oauthEs.value = createSSE(oauthExportStreamUrl(taskId), {
    init: (ev) => {
      try {
        const snap = JSON.parse(ev.data)
        if (snap.items) {
          oauthItems.value = stripOAuthItemLogs(snap.items)
          bumpOAuthTick()
        }
      } catch (_) {}
    },
    progress: (ev) => {
      try {
        const msg = JSON.parse(ev.data)
        if (!msg.email) return
        const up = oauthPendingUpdates[msg.email] || (oauthPendingUpdates[msg.email] = {})
        if (msg.status !== undefined) {
          up.status = msg.status
          if (msg.status === 'running' && !up.started_at) {
            up.started_at = msg.started_at || (Date.now() / 1000)
          }
        }
        if (msg.started_at) up.started_at = msg.started_at
        if (msg.step !== undefined) up.step = msg.step
        if (msg.step_text !== undefined) up.step_text = msg.step_text
        if (msg.result !== undefined) up.result = msg.result
        if (msg.elapsed !== undefined) up.elapsed = msg.elapsed
        scheduleOAuthUpdate()
      } catch (_) {}
    },
    end: () => {
      flushOAuthUpdates()
      oauthRunning.value = false
      stopOAuthLogPoll()
      if (oauthEs.value) {
        oauthEs.value.close()
        oauthEs.value = null
      }
      ElMessage.success('OAuth 任务已全部完成！')
      load(false)
      if (featVisible.value) loadFeatBoard()
    },
  }, () => {
    if (!oauthRunning.value && oauthEs.value) {
      oauthEs.value.close()
      oauthEs.value = null
    }
  })
}

// ── 重新授权：支持单个失败重试 与 批量重新授权失败账号 ──
async function retryOAuthExportRunner(targetEmails = null) {
  if (!oauthTaskId.value) {
    ElMessage.warning('当前无任务实例，请点击开始任务')
    return
  }

  let emails = targetEmails
  if (!emails || !emails.length) {
    emails = failedOAuthEmails.value
  }

  if (!emails.length) {
    ElMessage.info('当前没有需要重新授权的失败账号')
    return
  }

  let proxiesParam = ''
  let proxyParam = ''
  if (oauthForm.proxy === '__POOL__') {
    proxiesParam = proxyList.value.join('\n')
  } else {
    proxyParam = (oauthForm.proxy || '').trim()
  }

  try {
    const skipSms = oauthForm.smsStrategy === 'skip'
    const effectiveProvider = skipSms
      ? (oauthForm.smsProvider || 'smsbower')
      : oauthForm.smsStrategy

    const res = await retryOAuthExport(oauthTaskId.value, {
      emails,
      proxies: proxiesParam,
      proxy: proxyParam,
      proxy_country: oauthForm.proxyCountry || '',
      workers: oauthForm.workers || 5,
      timeout: oauthForm.timeout || 45,
      sms_enabled: !skipSms,
      sms_provider: effectiveProvider,
      sms_api_key: oauthForm.smsApiKey || '',
      sms_cdk_url: oauthForm.smsCdkUrl || 'https://ndk.cc.cd',
      sms_country: String(oauthForm.smsCountry || '52').trim(),
      sms_max_price: String(oauthForm.smsMaxPrice || '').trim(),
      sms_provider_ids: String(oauthForm.smsProviderIds || '').trim(),
      sms_except_provider_ids: Array.isArray(oauthForm.smsExceptProviderIds)
        ? oauthForm.smsExceptProviderIds.join(',')
        : String(oauthForm.smsExceptProviderIds || '').trim(),
      sms_max_attempts: Number(oauthForm.smsMaxAttempts) || 3,
      sms_timeout: Number(oauthForm.smsTimeout) || 80,
    })

    flushOAuthUpdates()
    const copy = { ...oauthItems.value }
    for (const em of emails) {
      if (copy[em]) {
        copy[em] = {
          ...copy[em],
          status: 'pending',
          step_text: '排队重新授权中...',
          result: null,
          elapsed: 0,
        }
      }
    }
    oauthItems.value = copy
    bumpOAuthTick()
    oauthPage.value = 1

    oauthRunning.value = true
    ElMessage.success(`已开始重新授权 ${res.retrying_count || emails.length} 个账号`)

    connectOAuthStream(oauthTaskId.value)
  } catch (e) {
    ElMessage.error('重新授权失败: ' + (e.response?.data?.detail || e.message))
  }
}

const oauthModalLogBoxRef = ref(null)
const oauthUserScrolledUp = ref(false)
let oauthModalScrollRaf = 0
let oauthLogPollTimer = 0

function onOAuthLogScroll() {
  const el = oauthModalLogBoxRef.value
  if (!el) return
  const isNearBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= 45
  oauthUserScrolledUp.value = !isNearBottom
}

function scrollOAuthModalLog(force = false) {
  if (!force && oauthUserScrolledUp.value) return
  if (oauthModalScrollRaf) return
  oauthModalScrollRaf = requestAnimationFrame(() => {
    oauthModalScrollRaf = 0
    const el = oauthModalLogBoxRef.value
    if (!el) return
    if (force || !oauthUserScrolledUp.value) {
      el.scrollTop = el.scrollHeight
    }
  })
}

function scrollToBottomOAuthLog() {
  oauthUserScrolledUp.value = false
  scrollOAuthModalLog(true)
}

function stopOAuthLogPoll() {
  if (oauthLogPollTimer) {
    clearInterval(oauthLogPollTimer)
    oauthLogPollTimer = 0
  }
}

async function refreshOAuthItemLog(silent = false) {
  const row = currentOAuthLogItem.value
  if (!row) return
  if (!silent) oauthLogLoading.value = true
  try {
    if (oauthTaskId.value) {
      const res = await getOAuthExportLog(oauthTaskId.value, row.email)
      oauthLogLines.value = res.lines || []
    } else {
      oauthLogLines.value = row.logs || ['暂无日志']
    }
    scrollOAuthModalLog(false)
  } catch (e) {
    if (!silent) oauthLogLines.value = ['读取日志失败: ' + (e.response?.data?.detail || e.message)]
  } finally {
    if (!silent) oauthLogLoading.value = false
  }
}

function startOAuthLogPoll() {
  stopOAuthLogPoll()
  if (!oauthLogModalVisible.value || !oauthRunning.value || !oauthTaskId.value) return
  oauthLogPollTimer = window.setInterval(() => refreshOAuthItemLog(true), 1000)
}

watch([oauthLogModalVisible, oauthRunning], () => {
  if (oauthLogModalVisible.value && oauthRunning.value) startOAuthLogPoll()
  else stopOAuthLogPoll()
})

async function openOAuthItemLog(row) {
  currentOAuthLogItem.value = row
  oauthLogLines.value = []
  oauthUserScrolledUp.value = false
  oauthLogModalVisible.value = true
  await refreshOAuthItemLog(false)
  nextTick(() => {
    scrollOAuthModalLog(true)
  })
  startOAuthLogPoll()
}

async function downloadCpaJson() {
  if (!oauthTaskId.value && !oauthTargetEmails.value.length) {
    ElMessage.warning('没有可下载的导出数据')
    return
  }
  try {
    const taskId = oauthTaskId.value || 'current'
    const emailsParam = oauthTargetEmails.value.join(',')
    const res = await downloadOAuthExportCpa(taskId, emailsParam)
    const blob = new Blob([res.data || res], { type: 'application/json' })
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `cpa-oauth-${taskId}.json`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    window.URL.revokeObjectURL(url)
    ElMessage.success('CPA JSON 凭证下载成功')
  } catch (e) {
    ElMessage.error('下载 CPA JSON 失败: ' + (e.response?.data?.detail || e.message))
  }
}

async function downloadSub2Json() {
  if (!oauthTaskId.value && !oauthTargetEmails.value.length) {
    ElMessage.warning('没有可下载的导出数据')
    return
  }
  try {
    const taskId = oauthTaskId.value || 'current'
    const emailsParam = oauthTargetEmails.value.join(',')
    const res = await downloadOAuthExportSub2(taskId, emailsParam)
    const blob = new Blob([res.data || res], { type: 'application/json' })
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `sub2api-oauth-${taskId}.json`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    window.URL.revokeObjectURL(url)
    ElMessage.success('Sub2API JSON 凭证下载成功')
  } catch (e) {
    ElMessage.error('下载 Sub2API JSON 失败: ' + (e.response?.data?.detail || e.message))
  }
}

// 下载单账号 CPA / Codex JSON (优先从本地 Session 极速转换导出，无需重跑 OAuth)
async function downloadSingleOAuthJson(email) {
  if (!email) return
  try {
    const res = await convertSessionToCpa({ email })
    if (res && res.data && res.data.length > 0) {
      const cpaDoc = res.data[0]
      if (!cpaDoc.refresh_token || !cpaDoc.refresh_token.trim()) {
        cpaDoc.refresh_token = '1'
      }
      const blob = new Blob([JSON.stringify(cpaDoc, null, 2)], { type: 'application/json' })
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `codex-${email}.json`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      window.URL.revokeObjectURL(url)
      ElMessage.success(`账号 ${email} 的 CPA JSON 凭证已生成并下载`)
      return
    }
    const taskId = oauthTaskId.value || 'single'
    const oRes = await downloadOAuthExportCpa(taskId, email)
    const blob = new Blob([oRes.data || oRes], { type: 'application/json' })
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `codex-${email}.json`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    window.URL.revokeObjectURL(url)
    ElMessage.success(`账号 ${email} 的 CPA JSON 下载成功`)
  } catch (e) {
    ElMessage.error('生成/下载 CPA JSON 失败: ' + (e.response?.data?.detail || e.message))
  }
}

// 下载单账号 Sub2API JSON (优先从本地 Session 极速转换导出，无需重跑 OAuth)
async function downloadSingleSub2Json(email) {
  if (!email) return
  try {
    const res = await convertSessionToSub2({ email })
    if (res && res.data) {
      const sub2Payload = res.data
      if (Array.isArray(sub2Payload.accounts)) {
        sub2Payload.accounts.forEach((acc) => {
          if (acc && acc.credentials && (!acc.credentials.refresh_token || !acc.credentials.refresh_token.trim())) {
            acc.credentials.refresh_token = '1'
          }
        })
      }
      const blob = new Blob([JSON.stringify(sub2Payload, null, 2)], { type: 'application/json' })
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `sub2api-${email}.json`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      window.URL.revokeObjectURL(url)
      ElMessage.success(`账号 ${email} 的 Sub2API JSON 凭证已生成并下载`)
      return
    }
    const taskId = oauthTaskId.value || 'single'
    const oRes = await downloadOAuthExportSub2(taskId, email)
    const blob = new Blob([oRes.data || oRes], { type: 'application/json' })
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `sub2api-${email}.json`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    window.URL.revokeObjectURL(url)
    ElMessage.success(`账号 ${email} 的 Sub2API JSON 下载成功`)
  } catch (e) {
    ElMessage.error('生成/下载 Sub2API JSON 失败: ' + (e.response?.data?.detail || e.message))
  }
}

// 单账号发起 OAuth 导出 / 手机接码弹窗
function openOAuthExportForSingle(email) {
  if (!email) return
  oauthTargetEmails.value = [email]
  oauthTaskId.value = ''
  initOAuthRows([email])
  oauthVisible.value = true
  loadSmsCountries()
}

// ════════════════ 选中账号全景档案 (Focused Account Profile) ════════════════
const focusedRow = ref(null)
const focusedTotpCode = ref('')
const focusedTotpNextCode = ref('')
const focusedTotpRemaining = ref(30)
const focusedTotpLoading = ref(false)
let focusedTotpTimer = null
const detailsCollapsed = ref(true)
const focusedPwdVisible = ref(false)
const focusedSecretVisible = ref(false)

const totpTimerUrgencyClass = computed(() => {
  const r = focusedTotpRemaining.value
  if (r <= 5) return 'urgency-critical'
  if (r <= 10) return 'urgency-warning'
  return 'urgency-normal'
})

function formatDossierCountry(countryCode) {
  if (!countryCode) return '🌐 全球节点'
  const c = String(countryCode).trim().toUpperCase()
  const info = COUNTRY_NAME_MAP?.[c]
  if (info) {
    return `🌐 ${info.name} (${c})`
  }
  return `🌐 ${c}`
}

const focusedHealthReport = computed(() => {
  if (!focusedRow.value) return { score: 0, grade: 'C', label: '待就绪', theme: 'info', checks: { pwd: false, totp: false, token: false }, tips: [] }
  const r = focusedRow.value
  const checks = {
    pwd: Boolean(r.password),
    totp: Boolean(r.totp_secret),
    token: Boolean(r.at_len && r.at_len > 0)
  }
  let score = 0
  if (checks.pwd) score += 35
  if (checks.totp) score += 40
  if (checks.token) score += 25

  let grade = 'C'
  let label = '低密模式'
  let theme = 'warn'
  if (score >= 95) {
    grade = 'S'
    label = '极致健全 · 最高防御'
    theme = 'success'
  } else if (score >= 65) {
    grade = 'A'
    label = '防护良好 · 建议加固'
    theme = 'info'
  } else {
    grade = 'B'
    label = '基础模式 · 亟需补全'
    theme = 'warn'
  }

  const tips = []
  if (!checks.totp) tips.push({ type: 'warning', text: '未绑定 2FA，抗风控与防封能力偏弱' })
  if (!checks.pwd) tips.push({ type: 'info', text: '未设独立密码，目前处于免密模式' })
  if (score === 100) tips.push({ type: 'success', text: '账号全景凭据完备，处于最高可用安全等级' })

  return { score, grade, label, theme, checks, tips }
})

async function refreshFocusedAccount() {
  if (!focusedRow.value) return
  if (focusedRow.value.totp_secret) {
    await fetchFocusedTotp()
    ElMessage.success('2FA 动态口令已刷新')
  } else {
    ElMessage.info('当前账号未绑定 2FA 密钥')
  }
}

async function setFocusedRow(row) {
  focusedPwdVisible.value = false
  focusedSecretVisible.value = false
  if (!row) {
    focusedRow.value = null
    stopFocusedTotpTicker()
    focusedTotpCode.value = ''
    focusedTotpNextCode.value = ''
    return
  }
  focusedRow.value = row
  if (row.totp_secret) {
    await fetchFocusedTotp()
    startFocusedTotpTicker()
  } else {
    stopFocusedTotpTicker()
    focusedTotpCode.value = ''
    focusedTotpNextCode.value = ''
  }
}

async function fetchFocusedTotp() {
  if (!focusedRow.value || !focusedRow.value.email) return
  focusedTotpLoading.value = true
  try {
    const res = await getAccountTotp(focusedRow.value.email)
    focusedTotpCode.value = res.code || ''
    focusedTotpNextCode.value = res.next_code || ''
    focusedTotpRemaining.value = res.remaining_seconds || 30
  } catch (e) {
    focusedTotpCode.value = ''
  } finally {
    focusedTotpLoading.value = false
  }
}

function startFocusedTotpTicker() {
  stopFocusedTotpTicker()
  focusedTotpTimer = setInterval(() => {
    if (!focusedRow.value || !focusedRow.value.totp_secret) {
      stopFocusedTotpTicker()
      return
    }
    if (focusedTotpRemaining.value > 1) {
      focusedTotpRemaining.value--
    } else {
      focusedTotpRemaining.value = 30
      fetchFocusedTotp()
    }
  }, 1000)
}

function stopFocusedTotpTicker() {
  if (focusedTotpTimer) {
    clearInterval(focusedTotpTimer)
    focusedTotpTimer = null
  }
}

// ════════════════ 左侧分类折叠组 ════════════════
const categoryGroupsOpen = ref({
  status: true,
  security: true,
  entitlement: false,
  country: true,
})

function toggleCategoryGroup(k) {
  categoryGroupsOpen.value[k] = !categoryGroupsOpen.value[k]
}

// ════════════════════════ 数据加载与分页 ════════════════════════
function onTableSort({ prop, order }) {
  if (!order) {
    sortBy.value = 'created_at'
    sortOrder.value = 'desc'
  } else {
    const mapped = prop === 'at_expires_at' ? 'at_expires_at' : 'created_at'
    sortBy.value = mapped
    sortOrder.value = order === 'ascending' ? 'asc' : 'desc'
  }
  load(true)
}

async function load(resetPage = false) {
  if (resetPage) page.value = 1
  loading.value = true
  try {
    const { items, total: t } = await listRegistered({
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
      filter: 'all',
      filter_health: filterHealth.value,
      filter_plan: filterPlan.value,
      filter_sec: filterSec.value,
      filter_extract: filterExtract.value,
      filter_oauth: filterOAuth.value,
      filter_domain: filterDomain.value,
      filter_country: filterCountry.value,
      filter_at_export: filterAtExport.value,
      filter_at_exp: filterAtExp.value,
      sort_by: sortBy.value,
      sort_order: sortOrder.value,
      search: searchKeyword.value.trim(),
    })
    rows.value = (items || []).map(prepareRowData)
    total.value = t || 0
    if (rows.value.length) {
      const cur = focusedRow.value
      const found = cur ? rows.value.find((r) => r.email === cur.email) : null
      setFocusedRow(found || rows.value[0])
    } else {
      setFocusedRow(null)
    }
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

async function refreshAll() {
  await Promise.allSettled([
    load(false),
    loadRegSummary(),
    loadDomains(),
    loadCountries(),
  ])
}

function persistPageSize(n) {
  const size = clampPageSize(n)
  pageSize.value = size
  customPageSize.value = size
  try {
    localStorage.setItem(PAGE_SIZE_KEY, String(size))
  } catch (_) {}
  return size
}

function handleSizeChange(val) {
  persistPageSize(val)
  load(true)
}

function applyCustomPageSize() {
  persistPageSize(customPageSize.value)
  load(true)
}

function handleCurrentChange(val) {
  page.value = val
  load(false)
}

async function confirm(msg) {
  try {
    await ElMessageBox.confirm(msg, '确认', {
      type: 'warning', confirmButtonText: '确定', cancelButtonText: '取消',
      customClass: 'confirm-multiline',
    })
    return true
  } catch (_) { return false }
}

async function deleteOne(email) {
  if (!(await confirm(`删除 ${email} 的凭证？`))) return
  try {
    await deleteRegistered(email)
    ElMessage.success('已删除')
    load()
    loadRegSummary()
  } catch (e) { ElMessage.error(e.message) }
}

async function deleteSelected() {
  const emails = selected.value.map((r) => r.email)
  if (!emails.length) return
  if (!(await confirm(`确定删除选中的 ${emails.length} 条凭证？(不可恢复)`))) return
  try {
    const r = await bulkDeleteRegistered({ emails })
    ElMessage.success(`已删除 ${r.deleted} 条`)
    clearSelected()
    load()
    loadRegSummary()
  } catch (e) { ElMessage.error(e.message) }
}

async function cleanInvalid() {
  if (!(await confirm('将自动清理所有没有有效 Token 凭证（AT/ST/RT 全为空）的未完成废号，确定？'))) return
  try {
    const r = await cleanInvalidRegistered()
    ElMessage.success(`已清理 ${r.deleted} 个无凭证空号`)
    load()
    loadRegSummary()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

// 🛡️ 一键自愈/找回历史授权凭据（防止因网络超时重跑误判为失败）
async function doRecoverOAuth(emails = null) {
  const isBatch = Array.isArray(emails) && emails.length > 0
  const isSelected = selected.value.length > 0
  const targetEmails = isBatch ? emails : (isSelected ? selected.value.map(r => r.email) : null)
  const tip = targetEmails
    ? `确定扫描并自愈选中的 ${targetEmails.length} 个账号的历史 OAuth 授权与 Refresh Token？`
    : '将全库扫描所有已有 RT 凭据及本地 exports 历史导出文件，自动找回并修复所有授权成功的账号，确定？'

  if (!(await confirm(tip))) return
  loading.value = true
  try {
    const res = await recoverOAuthCredentials({ emails: targetEmails })
    const data = res.data || {}
    const totalRec = data.total_recovered || 0
    if (totalRec > 0) {
      ElMessage.success({
        message: `🎉 成功找回/修复 ${totalRec} 个账号的授权凭证！(库内自愈: ${data.recovered_from_db || 0}, 本地历史文件追回: ${data.recovered_from_files || 0})，已满血恢复状态！`,
        duration: 5000,
      })
      loadRegSummary()
    } else {
      ElMessage.info('扫描完成，当前所选账号均已是最新正常状态，无需找回')
    }
    load(false)
  } catch (e) {
    ElMessage.error('找回凭证失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

async function deleteAll() {
  if (!(await confirm('这会清空注册结果表里的所有凭证！邮箱列表不受影响，确定？'))) return
  if (!(await confirm('再次确认：真的要删除全部凭证吗？此操作不可恢复！'))) return
  try {
    const r = await bulkDeleteRegistered({ all: true })
    ElMessage.success(`已清空 ${r.deleted} 条`)
    clearSelected()
    load()
    loadRegSummary()
  } catch (e) { ElMessage.error(e.message) }
}

// ──────────── 批量导出与全格式留痕归档 ────────────
const exportFormats = ref([])
const exportChunkSize = ref(0) // 每卷条数，0 = 不分卷（单文件）
const exporting = ref(false)
const exportVisible = ref(false)
const exportText = ref('')
const exportCount = ref(0)
const exportFilename = ref('')
const exportLabel = ref('')
const exportedEmails = ref([])
const deletingExported = ref(false)

// 全新导出配置与留痕确认弹窗
const exportConfigModalVisible = ref(false)
const exportTargetFmt = ref(null)
const exportScope = ref('selected') // 'selected' | 'all'
const exportCustomChunk = ref(false)
const exportCustomChunkSize = ref(100)
const exportNoteInput = ref('')
const exportPresetNotes = [
  '客户交付',
  'Sub2API导入',
  'CPA批量归档',
  '自用备用',
  '验活合格',
  '2026-09-02批次',
]

// 导出后保持弹窗设置（默认开启：方便用户继续导出其他格式且不刷新破坏勾选）
const KEEP_EXPORT_MODAL_KEY = 'reg_keep_export_modal'
const keepModalAfterExport = ref(localStorage.getItem(KEEP_EXPORT_MODAL_KEY) !== 'false')
const lastExportedInfo = ref(null)

function toggleKeepExportModal(val) {
  keepModalAfterExport.value = val
  try {
    localStorage.setItem(KEEP_EXPORT_MODAL_KEY, String(val))
  } catch (_) {}
}

// 导出分割线 / 分隔符自定义设置 (Delimiter)
const exportDelimiterMode = ref('----') // '----' | '---' | '--' | '|' | ',' | ':' | '\t' | 'custom'
const exportCustomDelimiter = ref('----')
const exportDelimiterPresets = [
  { label: '---- (默认4横杠)', value: '----' },
  { label: '--- (3横杠)', value: '---' },
  { label: '-- (双横杠)', value: '--' },
  { label: '| (竖线)', value: '|' },
  { label: ', (逗号/CSV)', value: ',' },
  { label: ': (冒号)', value: ':' },
  { label: '\\t (Tab制表符)', value: '\t' },
  { label: '自定义', value: 'custom' },
]

const isTextDelimiterFormat = computed(() => {
  const f = exportTargetFmt.value?.id || ''
  return ['email_at', 'email_pw', 'email_pw_2fa', 'email_pw_2fa_relay'].includes(f)
})

const isPw2faExportFormat = computed(() => {
  const f = exportTargetFmt.value?.id || ''
  return f === 'email_pw_2fa' || f === 'email_pw_2fa_relay'
})

const effectiveExportDelimiter = computed(() => {
  if (!isTextDelimiterFormat.value) return '----'
  if (exportDelimiterMode.value === 'custom') {
    return exportCustomDelimiter.value ?? '----'
  }
  return exportDelimiterMode.value || '----'
})

const sampleDelimiterPreview = computed(() => {
  const f = exportTargetFmt.value?.id || 'email_pw_2fa'
  const d = effectiveExportDelimiter.value
  const displayD = d === '\t' ? ' ⇥ ' : d
  if (f === 'email_at') return `user@outlook.com${displayD}eyJhbGciOi...`
  if (f === 'email_pw') return `user@outlook.com${displayD}Password123`
  if (f === 'email_pw_2fa') return `user@outlook.com${displayD}Password123${displayD}JBSWY3DPEHPK3PXP`
  if (f === 'email_pw_2fa_relay') return `user@outlook.com${displayD}Password123${displayD}JBSWY3DPEHPK3PXP${displayD}https://remail.aishop6.com/pickup?...`
  return `user@outlook.com${displayD}data`
})

const exportTargetCount = computed(() =>
  exportScope.value === 'selected' ? selected.value.length : total.value
)

const effectiveExportChunk = computed(() => {
  if (exportCustomChunk.value) {
    return Math.max(0, parseInt(exportCustomChunkSize.value || 0, 10))
  }
  return parseInt(exportChunkSize.value || 0, 10)
})

const estimatedChunksCount = computed(() => {
  const cnt = exportTargetCount.value || 0
  const chk = effectiveExportChunk.value || 0
  if (!chk || chk <= 0) return 1
  return Math.ceil(cnt / chk)
})

const exportBtnText = computed(() =>
  selected.value.length ? `导出选中 (${selected.value.length})` : '导出全部'
)

async function loadExportFormats() {
  if (exportFormats.value.length) return
  try {
    const { formats } = await listExportFormats()
    exportFormats.value = formats || []
    if (!exportTargetFmt.value && exportFormats.value.length) {
      exportTargetFmt.value = exportFormats.value[0]
    }
  } catch (e) {
    ElMessage.error('加载导出格式失败: ' + e.message)
  }
}

function openExportModal(fmt = null) {
  loadExportFormats()
  if (fmt && fmt.id) {
    exportTargetFmt.value = fmt
  } else if (!exportTargetFmt.value && exportFormats.value.length) {
    exportTargetFmt.value = exportFormats.value[0]
  }
  exportScope.value = selected.value.length ? 'selected' : 'all'
  exportNoteInput.value = ''
  lastExportedInfo.value = null
  exportConfigModalVisible.value = true
}

function appendPresetNote(note) {
  if (!exportNoteInput.value) {
    exportNoteInput.value = note
  } else if (!exportNoteInput.value.includes(note)) {
    exportNoteInput.value = `${exportNoteInput.value} · ${note}`
  }
}

async function submitExport() {
  const fmt = exportTargetFmt.value
  if (!fmt) {
    ElMessage.warning('请选择导出格式')
    return
  }

  const isSelectedScope = exportScope.value === 'selected' && selected.value.length > 0
  const emails = isSelectedScope ? selected.value.map((r) => r.email) : []
  const chunk = effectiveExportChunk.value || 0
  const note = (exportNoteInput.value || '').trim()
  const delim = isTextDelimiterFormat.value ? effectiveExportDelimiter.value : '----'

  const payload = isSelectedScope
    ? { format: fmt.id, emails, chunk_size: chunk, note, delimiter: delim }
    : { format: fmt.id, all: true, chunk_size: chunk, note, delimiter: delim }

  // 若未勾选“导出后保持弹窗”，则先关闭弹窗
  if (!keepModalAfterExport.value) {
    exportConfigModalVisible.value = false
  }
  exporting.value = true

  try {
    const r = await exportRegistered(payload)
    exportedEmails.value = (r.emails || []).filter(Boolean)
    const skipped = Number(r.skipped || 0)
    const skipBit = skipped ? `，已跳过 ${skipped} 个缺少密码或 2FA` : ''

    if (!r.count) {
      ElMessage.warning(
        skipped
          ? `没有可导出的账号：已跳过 ${skipped} 个缺少密码或 2FA`
          : '没有可导出的账号',
      )
      return
    }

    // 本地只把真正导出去的号标成已导出（缺密码/2FA 被跳过的不打印）
    const exportedSet = new Set(exportedEmails.value.map((e) => String(e).toLowerCase()))
    const nowSec = Math.floor(Date.now() / 1000)
    if (isSelectedScope) {
      selected.value.forEach((row) => {
        if (!exportedSet.has(String(row.email || '').toLowerCase())) return
        if (!row.exported_at) row.exported_at = nowSec
        if (note) row.export_note = note
        row.export_fmt_label = fmt.label
      })
    }

    lastExportedInfo.value = {
      filename: r.filename || 'export.txt',
      format: fmt.label,
      count: r.count || 0,
      skipped,
      time: new Date().toLocaleTimeString('zh-CN', { hour12: false }),
    }

    if (r.mode === 'download') {
      saveBlob(b64ToBytes(r.b64), r.filename, r.mime)
      const parts = r.parts ? ` · 分卷打包 ${r.parts} 个文件` : ''
      const mark = ' · 已记录留痕'
      if (keepModalAfterExport.value) {
        ElMessage.success(`🎉 已下载 ${r.filename}（${r.count} 个账号${parts}${skipBit}）${mark}！弹窗已保留，可继续选择其他格式导出`)
      } else {
        ElMessage.success(`🎉 已下载 ${r.filename}（${r.count} 个账号${parts}${skipBit}）${mark}（未刷新界面，保留当前勾选）`)
      }
      return
    }

    exportText.value = r.text || ''
    exportCount.value = r.count || 0
    exportFilename.value = r.filename || 'export.txt'
    exportLabel.value = r.label || fmt.label
    ElMessage.success(`🎉 已成功生成 ${r.count} 个账号数据并记录导出留痕${skipBit}`)
    exportVisible.value = true
  } catch (e) {
    ElMessage.error('导出失败: ' + e.message)
  } finally {
    exporting.value = false
  }
}

// 格式化时间戳
function formatExportDate(ts) {
  if (!ts) return ''
  try {
    const d = new Date(ts * 1000)
    return d.toLocaleString('zh-CN', { hour12: false })
  } catch {
    return ''
  }
}

// 获取导出徽章文案与样式
function getExportBadgeLabel(row) {
  const f = (row.export_fmt || '').toLowerCase()
  if (f.includes('sub2api') || f.includes('sub2')) return 'Sub2✓'
  if (f.includes('cpa')) return 'CPA✓'
  if (f.includes('session')) return 'Session✓'
  if (f.includes('2fa') || f.includes('pwd')) return '2FA✓'
  if (f === 'at' || f === 'email_at' || row.at_exported_at) return 'AT✓'
  return '已导出✓'
}

function getExportBadgeClass(row) {
  const f = (row.export_fmt || '').toLowerCase()
  if (f.includes('sub2api') || f.includes('sub2')) return 'badge-sub2'
  if (f.includes('cpa')) return 'badge-cpa'
  if (f.includes('session')) return 'badge-session'
  if (f.includes('2fa') || f.includes('pwd')) return 'badge-pwd2fa'
  return 'badge-at'
}

// 快速修改账号导出备注
async function quickEditExportNote(row) {
  try {
    const currentNote = row.export_note || row.at_export_note || ''
    const { value } = await ElMessageBox.prompt(
      `修改账号 ${row.email} 的导出备注：`,
      '修改导出备注',
      {
        confirmButtonText: '保存',
        cancelButtonText: '取消',
        inputValue: currentNote,
        inputPlaceholder: '例如：交付客户老王 · 2026-09-02',
        customClass: 'confirm-multiline',
      }
    )
    const newNote = (value || '').trim()
    await updateExportNote({ email: row.email, note: newNote })
    row.export_note = newNote
    row.at_export_note = newNote
    ElMessage.success('导出备注已更新')
  } catch (_) {}
}

function b64ToBytes(b64) {
  const bin = atob(b64 || '')
  const bytes = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
  return bytes
}

function saveBlob(data, filename, mime) {
  const blob = data instanceof Blob ? data : new Blob([data], { type: mime || 'application/octet-stream' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

function downloadExport() {
  const mime = exportFilename.value.endsWith('.json') || exportFilename.value.endsWith('.jsonl')
    ? 'application/json;charset=utf-8'
    : 'text/plain;charset=utf-8'
  saveBlob(exportText.value, exportFilename.value, mime)
}

async function downloadAndDelete() {
  downloadExport()
  const emails = exportedEmails.value
  if (!emails.length) {
    ElMessage.warning('这批导出没有拿到 email 列表，只下载不删除')
    return
  }
  const ok = await confirm(
    `已下载 ${exportFilename.value}。\n\n` +
    `现在删除这 ${emails.length} 个号：\n` +
    `  · 注册结果（凭证、2FA secret）\n` +
    `  · 邮箱列表（号池那一行，含取件链接）\n\n` +
    `删掉后只剩刚下载的备份文件这一份，不可恢复。确定？`,
  )
  if (!ok) return

  deletingExported.value = true
  try {
    const r1 = await bulkDeleteRegistered({ emails })
    let poolDeleted = 0
    try {
      const r2 = await bulkDeleteAccounts({ emails })
      poolDeleted = r2.deleted || 0
    } catch (e) {
      ElMessage.warning('注册结果已删，但邮箱列表删除失败: ' + e.message)
    }
    ElMessage.success(`已删除：注册结果 ${r1.deleted} 条 / 邮箱列表 ${poolDeleted} 条`)
    exportVisible.value = false
    exportedEmails.value = []
    selected.value = []
    load(true)
    runtime.bumpData()
  } catch (e) {
    ElMessage.error('删除失败: ' + e.message)
  } finally {
    deletingExported.value = false
  }
}

// 凭证弹窗 (macOS 风格)
const credVisible = ref(false)
const credEmail = ref('')
const credData = ref(null)
const CRED_KEYS = ['totp_secret', 'totp_factor_id', 'access_token', 'session_token', 'refresh_token', 'id_token', 'device_id', 'csrf_token', 'cookie_header', 'password']
const credRows = computed(() => {
  if (!credData.value) return []
  const items = CRED_KEYS.filter((k) => credData.value[k]).map((k) => ({ key: k, val: credData.value[k] }))
  const sess = credData.value.session_data || credData.value.extra?.session_data
  if (sess && typeof sess === 'object') {
    items.unshift({ key: 'session_data', val: JSON.stringify(sess, null, 2) })
  }
  const ext = credData.value.extract_link || credData.value.extra?.extract_link
  if (ext && ext.link_url) {
    items.unshift({ key: 'extract_link', val: ext.link_url })
  }
  // Remail 专属取件地址与凭证
  const mo = credData.value.extra?.mail_oauth || credData.value.mail_oauth
  if (mo && typeof mo === 'object') {
    if (mo.pickup_url) {
      items.unshift({ key: 'pickup_url', val: mo.pickup_url })
    }
    if (mo.service_token) {
      items.push({ key: 'service_token', val: mo.service_token })
    }
    if (mo.order_no) {
      items.push({ key: 'order_no', val: mo.order_no })
    }
  } else if (credData.value.relay_url) {
    items.unshift({ key: 'pickup_url', val: credData.value.relay_url })
  }
  return items
})

const CRED_META_DICT = {
  pickup_url:     { badge: '取件链接', bg: 'rgba(93, 164, 177, 0.22)', color: '#5da4b1' },
  service_token:  { badge: 'RemailToken', bg: 'rgba(93, 164, 177, 0.16)', color: '#7bb8c2' },
  order_no:       { badge: 'Remail订单', bg: 'rgba(93, 164, 177, 0.14)', color: '#7bb8c2' },
  session_data:   { badge: 'SessionJSON', bg: 'rgba(93, 164, 177, 0.22)', color: '#5da4b1' },
  extract_link:   { badge: 'Extract', bg: 'rgba(93, 164, 177, 0.18)', color: '#5da4b1' },
  totp_secret:    { badge: '2FA', bg: 'rgba(93, 164, 177, 0.18)', color: '#5da4b1' },
  totp_factor_id: { badge: '2FA', bg: 'rgba(93, 164, 177, 0.14)', color: '#7bb8c2' },
  access_token:   { badge: 'OAuth', bg: 'rgba(93, 164, 177, 0.18)', color: '#7bb8c2' },
  session_token:  { badge: 'Session', bg: 'rgba(248, 243, 233, 0.1)', color: '#f8f3e9' },
  refresh_token:  { badge: 'OAuth', bg: 'rgba(93, 164, 177, 0.16)', color: '#7bb8c2' },
  id_token:       { badge: 'Token', bg: 'rgba(168, 159, 144, 0.16)', color: '#a89f90' },
  device_id:      { badge: 'Device', bg: 'rgba(168, 159, 144, 0.16)', color: '#a89f90' },
  csrf_token:     { badge: 'Security', bg: 'rgba(168, 159, 144, 0.16)', color: '#a89f90' },
  cookie_header:  { badge: 'Cookie', bg: 'rgba(196, 165, 116, 0.16)', color: '#c4a574' },
  password:       { badge: 'Auth', bg: 'rgba(93, 164, 177, 0.18)', color: '#5da4b1' },
}

function getCredMeta(key) {
  return CRED_META_DICT[key] || { badge: 'KEY', bg: 'rgba(148, 163, 184, 0.2)', color: '#cbd5e1' }
}

async function viewCred(email) {
  try {
    const { data } = await getRegistered(email)
    credData.value = data
    credEmail.value = email
    credVisible.value = true
  } catch (e) { ElMessage.error('加载凭证失败: ' + e.message) }
}

function copyAllJson() {
  if (credData.value) copyText(JSON.stringify(credData.value, null, 2))
}

// 手动编辑凭证
const editVisible = ref(false)
const editSaving = ref(false)
const editEmail = ref('')
const editPassword = ref('')
const editSecret = ref('')
const editOrigPassword = ref('')
const editOrigSecret = ref('')

function openEdit(row) {
  editEmail.value = row.email
  editPassword.value = row.password || ''
  editSecret.value = row.totp_secret || ''
  editOrigPassword.value = row.password || ''
  editOrigSecret.value = row.totp_secret || ''
  editVisible.value = true
}

async function saveEdit() {
  const pw = editPassword.value
  const sec = editSecret.value.trim()
  const payload = { email: editEmail.value }
  if (pw !== editOrigPassword.value) payload.password = pw
  if (sec !== editOrigSecret.value) payload.totp_secret = sec
  if (payload.password === undefined && payload.totp_secret === undefined) {
    ElMessage.info('没有改动')
    editVisible.value = false
    return
  }
  if (payload.totp_secret !== undefined && editOrigSecret.value) {
    try {
      await ElMessageBox.confirm(
        `该账号已有 2FA secret：\n${editOrigSecret.value}\n\n` +
        '覆盖后原 secret 将永久丢失，服务端取不回。\n' +
        '若原 secret 仍是账号上生效的那个，覆盖会导致该号 2FA 永远登不上。',
        '确认覆盖 2FA secret？',
        { type: 'warning', confirmButtonText: '确认覆盖', cancelButtonText: '取消' },
      )
    } catch { return }
  }
  editSaving.value = true
  try {
    const r = await updateCredentials(payload)
    ElMessage.success(`已保存：${(r.changed || []).join(' + ') || '无改动'}`)
    editVisible.value = false
    await load()
  } catch (e) {
    ElMessage.error('保存失败: ' + (e.response?.data?.detail || e.message))
  } finally { editSaving.value = false }
}

// ════════════════════════ 2FA 实时动态码 & 邮箱 OTP 抓取 & 补密补2FA ════════════════════════
// 1. 2FA 实时动态码弹窗
const totpModalVisible = ref(false)
const totpEmail = ref('')
const totpCode = ref('')
const totpNextCode = ref('')
const totpRemaining = ref(30)
const totpSecret = ref('')
const totpLoading = ref(false)
let totpTimer = null

async function openTotpModal(row) {
  totpEmail.value = row.email
  totpSecret.value = row.totp_secret || ''
  totpCode.value = ''
  totpNextCode.value = ''
  totpRemaining.value = 30
  totpModalVisible.value = true
  await fetchTotpCode()
  startTotpTicker()
}

async function fetchTotpCode() {
  if (!totpEmail.value) return
  totpLoading.value = true
  try {
    const res = await getAccountTotp(totpEmail.value)
    totpCode.value = res.code || ''
    totpNextCode.value = res.next_code || ''
    totpRemaining.value = res.remaining_seconds || 30
    totpSecret.value = res.totp_secret || totpSecret.value
  } catch (e) {
    ElMessage.error('获取 2FA 动态码失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    totpLoading.value = false
  }
}

function startTotpTicker() {
  stopTotpTicker()
  totpTimer = setInterval(() => {
    if (!totpModalVisible.value) {
      stopTotpTicker()
      return
    }
    if (totpRemaining.value > 1) {
      totpRemaining.value--
    } else {
      totpRemaining.value = 30
      fetchTotpCode()
    }
  }, 1000)
}

function stopTotpTicker() {
  if (totpTimer) {
    clearInterval(totpTimer)
    totpTimer = null
  }
}

function closeTotpModal() {
  stopTotpTicker()
  totpModalVisible.value = false
}

// 2. 现代化邮箱收件箱与验证码实时抓取工作台 (Mailbox Studio)
const mailOtpModalVisible = ref(false)
const mailOtpEmail = ref('')
const mailOtpCode = ref('')
const mailOtpFound = ref(false)
const mailOtpProvider = ref('')
const mailOtpProtocol = ref('')
const mailOtpMessages = ref([])
const mailOtpLoading = ref(false)
const mailOtpError = ref('')
const mailOtpCustomLine = ref('')
const mailOtpElapsed = ref(0)
const mailOtpLastUpdated = ref('')
const mailOtpAutoPolling = ref(false)
const mailOtpFilterOnlyOtp = ref(false)
const mailSearchQuery = ref('')
const expandedMailIds = ref(new Set())
let mailPollingTimer = null

function getSenderAvatar(fromStr) {
  const s = String(fromStr || '').toLowerCase()
  if (s.includes('openai') || s.includes('chatgpt')) {
    return { text: 'OAI', color: '#5da4b1', bg: 'rgba(93, 164, 177, 0.15)' }
  }
  if (s.includes('microsoft') || s.includes('outlook') || s.includes('live')) {
    return { text: 'MS', color: '#38bdf8', bg: 'rgba(56, 189, 248, 0.15)' }
  }
  return { text: 'MAIL', color: '#a78bfa', bg: 'rgba(167, 139, 250, 0.15)' }
}

function formatMailDate(dtStr) {
  if (!dtStr) return ''
  try {
    const d = new Date(dtStr)
    if (isNaN(d.getTime())) return dtStr
    const pad = (n) => String(n).padStart(2, '0')
    const y = d.getFullYear()
    const m = pad(d.getMonth() + 1)
    const day = pad(d.getDate())
    const h = pad(d.getHours())
    const min = pad(d.getMinutes())
    const s = pad(d.getSeconds())
    return `${y}-${m}-${day} ${h}:${min}:${s}`
  } catch (e) {
    return dtStr
  }
}

const filteredMailMessages = computed(() => {
  let list = mailOtpMessages.value || []
  if (mailOtpFilterOnlyOtp.value) {
    list = list.filter(m => Boolean(m.otp))
  }
  const q = mailSearchQuery.value.trim().toLowerCase()
  if (q) {
    list = list.filter(m =>
      (m.subject && m.subject.toLowerCase().includes(q)) ||
      (m.from && m.from.toLowerCase().includes(q)) ||
      (m.snippet && m.snippet.toLowerCase().includes(q)) ||
      (m.otp && m.otp.includes(q))
    )
  }
  return list
})

function toggleMailExpanded(id) {
  if (!id) return
  const s = new Set(expandedMailIds.value)
  if (s.has(id)) {
    s.delete(id)
  } else {
    s.add(id)
  }
  expandedMailIds.value = s
}

async function openMailOtpModal(row) {
  mailOtpEmail.value = row.email
  mailOtpCode.value = ''
  mailOtpFound.value = false
  mailOtpMessages.value = []
  mailOtpError.value = ''
  mailOtpCustomLine.value = ''
  mailOtpElapsed.value = 0
  mailOtpLastUpdated.value = ''
  mailOtpAutoPolling.value = false
  mailOtpFilterOnlyOtp.value = false
  mailSearchQuery.value = ''
  expandedMailIds.value = new Set()
  mailOtpModalVisible.value = true
  await doFetchMailOtp(true)
}

function handleMailOtpModalClosed() {
  stopMailPolling()
  mailSearchQuery.value = ''
  expandedMailIds.value = new Set()
}

function toggleMailPolling() {
  if (mailOtpAutoPolling.value) {
    startMailPolling()
  } else {
    stopMailPolling()
  }
}

function startMailPolling() {
  stopMailPolling()
  mailOtpAutoPolling.value = true
  mailPollingTimer = setInterval(async () => {
    if (!mailOtpModalVisible.value) {
      stopMailPolling()
      return
    }
    await doFetchMailOtp(false)
  }, 3500)
}

function stopMailPolling() {
  if (mailPollingTimer) {
    clearInterval(mailPollingTimer)
    mailPollingTimer = null
  }
  mailOtpAutoPolling.value = false
}

async function doFetchMailOtp(isManual = false) {
  if (!mailOtpEmail.value) return
  if (isManual) {
    mailOtpLoading.value = true
  }
  mailOtpError.value = ''
  const t0 = Date.now()
  try {
    const payload = {}
    if (mailOtpCustomLine.value.trim()) {
      payload.raw_line = mailOtpCustomLine.value.trim()
    }
    const res = await fetchMailOtp(mailOtpEmail.value, payload)
    mailOtpElapsed.value = res.elapsed_s || roundNumber((Date.now() - t0) / 1000, 2)
    mailOtpLastUpdated.value = new Date().toLocaleTimeString()
    if (res.ok === false && res.error) {
      mailOtpError.value = res.error
      mailOtpProvider.value = res.provider || ''
      mailOtpProtocol.value = res.protocol || ''
      mailOtpCode.value = ''
      mailOtpFound.value = false
      mailOtpMessages.value = []
      return
    }
    const previousCode = mailOtpCode.value
    mailOtpCode.value = res.otp || ''
    mailOtpFound.value = Boolean(res.found && res.otp)
    mailOtpProvider.value = res.provider || ''
    mailOtpProtocol.value = res.protocol || ''
    mailOtpMessages.value = res.messages || []

    // 如果展开列表为空，自动展开第一封邮件
    if (expandedMailIds.value.size === 0 && res.messages && res.messages.length > 0) {
      expandedMailIds.value = new Set([res.messages[0].id || 'mail_0'])
    }

    if (res.otp && (!previousCode || isManual)) {
      ElMessage.success(`成功抓取到验证码: ${res.otp}`)
    } else if (isManual && !res.otp) {
      ElMessage.info('已刷新，暂未检索到包含验证码的近期邮件')
    }
  } catch (e) {
    mailOtpError.value = e.response?.data?.detail || e.message
    if (isManual) {
      ElMessage.error('抓取邮箱验证码失败: ' + mailOtpError.value)
    }
  } finally {
    if (isManual) {
      mailOtpLoading.value = false
    }
  }
}

function roundNumber(num, dec = 2) {
  return Number(Math.round(num + 'e' + dec) + 'e-' + dec)
}

// 3. 补设密码弹窗
const repairPwVisible = ref(false)
const repairPwEmail = ref('')
const repairPwVal = ref('')
const repairPwOfficial = ref(true) // 默认勾选：全自动走官方服务端设置密码
const repairPwLoading = ref(false)

function openRepairPassword(row) {
  repairPwEmail.value = row.email
  repairPwVal.value = generateRandomPassword(16)
  repairPwOfficial.value = true
  repairPwVisible.value = true
}

function generateRandomPassword(len = 16) {
  const chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*'
  let pw = ''
  pw += 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'[Math.floor(Math.random() * 26)]
  pw += 'abcdefghijklmnopqrstuvwxyz'[Math.floor(Math.random() * 26)]
  pw += '0123456789'[Math.floor(Math.random() * 10)]
  pw += '!@#$%^&*'[Math.floor(Math.random() * 8)]
  for (let i = 4; i < len; i++) {
    pw += chars[Math.floor(Math.random() * chars.length)]
  }
  return pw.split('').sort(() => Math.random() - 0.5).join('')
}

async function submitRepairPassword() {
  const pw = repairPwVal.value.trim()
  if (!pw) {
    ElMessage.warning('请输入或生成密码')
    return
  }
  repairPwLoading.value = true
  try {
    const res = await setAccountPassword(repairPwEmail.value, {
      password: pw,
      official_reset: repairPwOfficial.value,
      proxy: proxyText(form.value),
    })
    ElMessage.success(res.message || `账号 ${repairPwEmail.value} 密码已设置成功`)
    repairPwVisible.value = false
    load(false)
  } catch (e) {
    ElMessage.error('设置密码失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    repairPwLoading.value = false
  }
}

// 4. 补绑 2FA 弹窗
const repair2faVisible = ref(false)
const repair2faEmail = ref('')
const repair2faRow = ref(null)
const repair2faLoading = ref(false)

function openRepair2FA(row) {
  repair2faEmail.value = row.email
  repair2faRow.value = row
  repair2faVisible.value = true
}

async function submitRepair2FA() {
  if (!repair2faEmail.value) return
  repair2faLoading.value = true
  try {
    const res = await bindAccount2fa(repair2faEmail.value, { proxy: proxyText(form.value) })
    ElMessage.success(res.message || '2FA 绑定成功')
    repair2faVisible.value = false
    load(false)
  } catch (e) {
    ElMessage.error('补绑 2FA 失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    repair2faLoading.value = false
  }
}

// 5. 单元格与凭证单项复制
async function copyCell(email, field = 'access_token') {
  try {
    const { data } = await getRegistered(email)
    const val = data?.[field]
    const fieldName = field === 'access_token' ? 'Access Token' : field === 'session_token' ? 'Session Token' : 'Refresh Token'
    if (!val) {
      ElMessage.warning(`该账号暂无 ${fieldName}`)
      return
    }
    const label = field === 'access_token' ? 'Access Token (AT) 已复制' : field === 'session_token' ? 'Session Token (ST) 已复制' : 'Refresh Token (RT) 已复制'
    copyText(val, label)
  } catch (e) {
    ElMessage.error('获取凭证失败: ' + e.message)
  }
}

// 批量复制 AT 处理器
async function handleCopyAtCommand(cmd) {
  try {
    let payload = {}
    let modeLabel = ''
    if (cmd === 'copy_at_selected') {
      const emails = selected.value.map((r) => r.email)
      if (!emails.length) {
        ElMessage.warning('请先勾选要复制 AT 的账号')
        return
      }
      payload = { format: 'at', emails }
      modeLabel = `已复制选中的 ${emails.length} 个账号 Access Token`
    } else if (cmd === 'copy_at_page') {
      const pageEmails = rows.value.filter((r) => r.at_len > 0).map((r) => r.email)
      if (!pageEmails.length) {
        ElMessage.warning('当前页没有拥有 Access Token 的账号')
        return
      }
      payload = { format: 'at', emails: pageEmails }
      modeLabel = `已复制当前页 ${pageEmails.length} 个账号 Access Token`
    } else if (cmd === 'copy_at_all') {
      payload = { format: 'at', all: true }
      modeLabel = '已全量复制所有账号 Access Token'
    } else if (cmd === 'copy_at_with_email') {
      const emails = selected.value.map((r) => r.email)
      if (!emails.length) {
        ElMessage.warning('请先勾选要复制的账号')
        return
      }
      payload = { format: 'email_at', emails }
      modeLabel = `已复制选中的 ${emails.length} 个「邮箱----AT」`
    }

    const r = await exportRegistered(payload)
    const text = (r.text || '').trim()
    if (!text) {
      ElMessage.warning('未能提取到有效的 Access Token 数据')
      return
    }
    const count = text.split('\n').filter((l) => l.trim().length > 0).length
    copyText(text, `${modeLabel} (共 ${count} 条)`)
  } catch (e) {
    ElMessage.error('批量复制 AT 失败: ' + (e.response?.data?.detail || e.message))
  }
}

// 单账号 Session JSON 复制
async function copySessionJson(email) {
  try {
    const { data } = await getRegistered(email)
    let sess = data?.session_data || data?.extra?.session_data
    if (!sess || typeof sess !== 'object' || !Object.keys(sess).length) {
      const at = data?.access_token || ''
      const st = data?.session_token || ''
      sess = {
        WARNING_BANNER: "!!!!!!!!!!!!!!!!!!!! DO NOT SHARE ANY PART OF THE INFORMATION YOU SEE HERE. THIS INFORMATION IS SENSITIVE AND CAN GRANT ACCESS TO YOUR ACCOUNT. SHARING THIS INFORMATION IS LIKE SHARING YOUR PASSWORD. !!!!!!!!!!!!!!!!!!!!",
        user: {
          id: `user-${email.split('@')[0]}`,
          name: email.split('@')[0],
          email: email,
          image: "https://cdn.oaistatic.com/assets/favicon-32x32-p60t9m4g.png",
          picture: "https://cdn.oaistatic.com/assets/favicon-32x32-p60t9m4g.png",
          idp: "auth0",
          iat: Math.floor(data?.created_at || Date.now() / 1000),
          mfa: Boolean(data?.totp_secret),
        },
        expires: new Date(Date.now() + 86400000 * 30).toISOString(),
        account: {
          id: "",
          createdTime: data?.created_at || Date.now() / 1000,
          planType: data?.plus_check?.status || "free",
          structure: "personal",
          isUsageBasedSeatEnabled: false,
          isConversationClassifierEnabledForWorkspace: true,
          hasFloraFeature: false,
          isFedrampCompliantWorkspace: false,
          isDelinquent: false,
          residencyRegion: "no_constraint",
          computeResidency: "no_constraint",
        },
        accessToken: at,
        authProvider: "openai",
        sessionToken: st,
      }
    }
    copyText(JSON.stringify(sess, null, 2), `ChatGPT Session JSON 已复制 (${email})`)
  } catch (e) {
    ElMessage.error('复制 Session JSON 失败: ' + e.message)
  }
}

// 批量复制 Session JSON 处理器
async function handleCopySessionCommand(cmd) {
  try {
    let emails = []
    let modeLabel = ''
    let asLines = false

    if (cmd === 'copy_session_selected' || cmd === 'copy_session_lines_selected') {
      emails = selected.value.map((r) => r.email)
      if (!emails.length) {
        ElMessage.warning('请先勾选要复制 Session JSON 的账号')
        return
      }
      asLines = cmd === 'copy_session_lines_selected'
      modeLabel = `已复制选中的 ${emails.length} 个账号 Session JSON`
    } else if (cmd === 'copy_session_page' || cmd === 'copy_session_lines_page') {
      emails = rows.value.filter((r) => r.st_len > 0 || r.at_len > 0).map((r) => r.email)
      if (!emails.length) {
        ElMessage.warning('当前页没有拥有有效 Session 的账号')
        return
      }
      asLines = cmd === 'copy_session_lines_page'
      modeLabel = `已复制当前页 ${emails.length} 个账号 Session JSON`
    } else if (cmd === 'copy_session_all' || cmd === 'copy_session_lines_all') {
      asLines = cmd === 'copy_session_lines_all'
      modeLabel = '已全量复制所有账号 Session JSON'
    }

    const payload = {
      format: asLines ? 'session_json_lines' : 'session_json',
      emails: emails.length ? emails : undefined,
      all: !emails.length,
    }

    const r = await exportRegistered(payload)
    let text = ''
    if (r.text) {
      text = r.text
    } else if (r.b64) {
      try {
        text = decodeURIComponent(escape(atob(r.b64)))
      } catch (_) {
        text = atob(r.b64)
      }
    }

    if (!text) {
      ElMessage.warning('未能提取到有效的 Session JSON 数据')
      return
    }

    copyText(text, `${modeLabel}`)
  } catch (e) {
    ElMessage.error('批量复制 Session JSON 失败: ' + (e.response?.data?.detail || e.message))
  }
}

// 6. 行内更多操作菜单处理
function handleRowMoreCommand(cmd, row) {
  if (cmd === 'refresh_token') openTokenRefreshForOne(row)
  else if (cmd === 'edit') openEdit(row)
  else if (cmd === 'recover_oauth') doRecoverOAuth([row.email])
  else if (cmd === 'copy_session') copySessionJson(row.email)
  else if (cmd === 'copy_at') copyCell(row.email, 'access_token')
  else if (cmd === 'copy_st') copyCell(row.email, 'session_token')
  else if (cmd === 'copy_rt') copyCell(row.email, 'refresh_token')
  else if (cmd === 'download_cpa') downloadSingleOAuthJson(row.email)
  else if (cmd === 'download_sub2') downloadSingleSub2Json(row.email)
  else if (cmd === 'oauth_export') openOAuthExportForSingle(row.email)
  else if (cmd === 'repair_pwd') openRepairPassword(row)
  else if (cmd === 'repair_2fa') openRepair2FA(row)
  else if (cmd === 'totp_code') openTotpModal(row)
  else if (cmd === 'fetch_mail') openMailOtpModal(row)
  else if (cmd === 'copy_pwd') copyText(row.password, '密码已复制')
  else if (cmd === 'copy_2fa') copyText(row.totp_secret, '2FA Secret 已复制')
  else if (cmd === 'delete') deleteOne(row.email)
}

// ════════════════════════ 安全加固任务台 (批量补密码 & 批量补2FA 控制台) ════════════════════════
const securityVisible = ref(false)
const securityRunning = ref(false)
const securityTaskId = ref('')
const securityEs = ref(null)
const securityConfigCollapsed = ref(true)
const securityTargetEmails = ref([])
const securityItems = ref({})
const securityLogs = ref([])
const securityAction = ref('password') // 'password' | '2fa'
const securityForm = reactive({
  action: 'password', // 'password' | '2fa'
  officialReset: true, // 走官方服务端全自动生效 (密码模式)
  proxy: '__POOL__',
  proxyCountry: 'RANDOM_HOT',
  workers: 5,
  timeout: 60,
})

// 弹窗内单账号日志终端
const securityLogModalVisible = ref(false)
const currentSecurityLogItem = ref(null)
const securityLogLines = ref([])
const securityLogLoading = ref(false)
const securityModalLogBoxRef = ref(null)

// 实时耗时秒表
const securityElapsed = ref(0)
const securityNowTime = ref(Date.now())
let securityLiveTimer = null

function getSecurityRowElapsed(row) {
  if (!row) return '—'
  if (row.status === 'running') {
    const st = row.started_at || (Date.now() / 1000)
    const now = securityNowTime.value / 1000
    const sec = Math.max(0, Math.floor(now - st))
    return `${sec}s`
  }
  if (row.elapsed !== undefined && row.elapsed !== null && row.elapsed > 0) {
    return `${row.elapsed}s`
  }
  return '—'
}

// ── 安全加固高性能分页、状态筛选与批量节流更新 ──
const securityPage = ref(1)
const securityPageSize = ref(50)
const securityFilter = ref('all') // 'all' | 'running' | 'failed' | 'success' | 'pending'
const securitySearch = ref('')

function isSecurityFailedRow(item) {
  if (!item) return false
  if (item.status === 'failed') return true
  if (item.result && (item.result.status === 'failed' || item.result.status === 'error')) return true
  return false
}

const securityFilteredRows = computed(() => {
  const list = Object.values(securityItems.value)
  const kw = securitySearch.value.trim().toLowerCase()
  const f = securityFilter.value
  return list.filter((item) => {
    if (kw && !item.email.toLowerCase().includes(kw)) return false
    if (f === 'all') return true
    if (f === 'running') return item.status === 'running'
    if (f === 'pending') return item.status === 'pending'
    if (f === 'failed') return isSecurityFailedRow(item)
    if (f === 'success') return item.status === 'success' || (item.status === 'done' && !isSecurityFailedRow(item))
    return true
  })
})

const securityDisplayRows = computed(() => {
  const rows = securityFilteredRows.value
  const start = (securityPage.value - 1) * securityPageSize.value
  return rows.slice(start, start + securityPageSize.value)
})

let securityUpdateTimer = null
let securityPendingUpdates = {}
let securityPendingLogs = []

function flushSecurityUpdates() {
  if (securityUpdateTimer) {
    cancelAnimationFrame(securityUpdateTimer)
    securityUpdateTimer = null
  }
  if (Object.keys(securityPendingUpdates).length > 0) {
    const copy = { ...securityItems.value }
    for (const [em, up] of Object.entries(securityPendingUpdates)) {
      if (!copy[em]) {
        copy[em] = { email: em, action: securityAction.value, status: 'pending', step_text: '排队中...', result: null, elapsed: 0 }
      }
      Object.assign(copy[em], up)
    }
    securityItems.value = copy
    securityPendingUpdates = {}
  }
  if (securityPendingLogs.length > 0) {
    securityLogs.value.push(...securityPendingLogs)
    if (securityLogs.value.length > 200) {
      securityLogs.value = securityLogs.value.slice(-200)
    }
    securityPendingLogs = []
  }
}

function scheduleSecurityUpdate() {
  if (securityUpdateTimer) return
  securityUpdateTimer = requestAnimationFrame(() => {
    securityUpdateTimer = null
    flushSecurityUpdates()
  })
}

const securityStats = computed(() => {
  const items = Object.values(securityItems.value)
  const tot = items.length || securityTargetEmails.value.length || 0
  const success = items.filter((i) => i.status === 'success').length
  const fail = items.filter((i) => isSecurityFailedRow(i)).length
  const skipped = items.filter((i) => i.status === 'skipped').length
  const done = success + fail + skipped
  const running = items.filter((i) => i.status === 'running').length
  const pending = items.filter((i) => i.status === 'pending').length
  const percent = tot > 0 ? Math.round((done / tot) * 100) : 0
  return {
    total: tot, done, running, pending,
    success, fail, skipped,
    percent,
  }
})

async function openSecurityTask(action = 'password', scope = 'selected') {
  let emails = []
  if (scope === 'selected') {
    if (!selected.value.length) {
      ElMessage.warning('请先在表格中勾选要处理的账号')
      return
    }
    emails = selected.value.map((r) => r.email)
  } else if (scope === 'all_missing_pwd') {
    loading.value = true
    try {
      const res = await listRegisteredEmails('no_password')
      emails = res.emails || []
      if (!emails.length) {
        ElMessage.info('当前没有未设置密码的账号')
        loading.value = false
        return
      }
    } catch (e) {
      ElMessage.error('获取账号列表失败: ' + e.message)
      loading.value = false
      return
    } finally {
      loading.value = false
    }
  } else if (scope === 'all_missing_2fa') {
    loading.value = true
    try {
      const res = await listRegisteredEmails('no_2fa')
      emails = res.emails || []
      if (!emails.length) {
        ElMessage.info('当前没有未绑定 2FA 的账号')
        loading.value = false
        return
      }
    } catch (e) {
      ElMessage.error('获取账号列表失败: ' + e.message)
      loading.value = false
      return
    } finally {
      loading.value = false
    }
  } else if (scope === 'all') {
    loading.value = true
    try {
      const res = await listRegisteredEmails('all')
      emails = res.emails || []
    } catch (e) {
      ElMessage.error('获取账号列表失败: ' + e.message)
      loading.value = false
      return
    } finally {
      loading.value = false
    }
  }

  securityAction.value = action
  securityForm.action = action
  securityTargetEmails.value = emails
  securityTaskId.value = ''
  securityRunning.value = false
  securityElapsed.value = 0
  securityLogs.value = []
  securityPage.value = 1
  securityFilter.value = 'all'
  securitySearch.value = ''
  if (securityLiveTimer) {
    clearInterval(securityLiveTimer)
    securityLiveTimer = null
  }

  const initMap = Object.create(null)
  for (const em of emails) {
    initMap[em] = { email: em, action, status: 'pending', step_text: '待启动...', result: null, elapsed: 0 }
  }
  securityItems.value = initMap
  securityVisible.value = true
}

function closeSecurityTask() {
  flushSecurityUpdates()
  if (securityRunning.value) {
    ElMessage.info('安全加固任务在后台继续运行，可随时重新打开查看进度')
  }
  if (securityEs.value && !securityRunning.value) {
    securityEs.value.close()
    securityEs.value = null
  }
  if (!securityRunning.value && securityLiveTimer) {
    clearInterval(securityLiveTimer)
    securityLiveTimer = null
  }
  securityVisible.value = false
}

async function stopSecurityTaskRunner() {
  if (!securityTaskId.value) {
    securityRunning.value = false
    return
  }
  try {
    await stopSecurityTask(securityTaskId.value)
    ElMessage.success('已发送停止指令')
  } catch (_) {
    ElMessage.info('任务已结束')
  } finally {
    flushSecurityUpdates()
    securityRunning.value = false
    if (securityLiveTimer) {
      clearInterval(securityLiveTimer)
      securityLiveTimer = null
    }
  }
}

async function startSecurityTaskRunner() {
  const emails = securityTargetEmails.value
  if (!emails.length) {
    ElMessage.warning('没有待处理的账号列表')
    return
  }

  if (securityEs.value) {
    securityEs.value.close()
    securityEs.value = null
  }
  if (securityLiveTimer) {
    clearInterval(securityLiveTimer)
    securityLiveTimer = null
  }

  flushSecurityUpdates()
  securityRunning.value = true
  securityLogs.value = []
  securityPage.value = 1
  securityFilter.value = 'all'
  securitySearch.value = ''
  securityConfigCollapsed.value = true
  securityElapsed.value = 0

  const secStartTime = Date.now()
  securityNowTime.value = Date.now()
  securityLiveTimer = setInterval(() => {
    securityNowTime.value = Date.now()
    if (securityRunning.value) {
      securityElapsed.value = Math.max(0, Math.floor((Date.now() - secStartTime) / 1000))
    }
  }, 1000)

  const initMap = Object.create(null)
  for (const em of emails) {
    initMap[em] = { email: em, action: securityAction.value, status: 'pending', step_text: '排队中...', result: null, elapsed: 0 }
  }
  securityItems.value = initMap

  let proxiesParam = ''
  let proxyParam = ''
  if (securityForm.proxy === '__POOL__') {
    proxiesParam = proxyList.value.join('\n')
  } else {
    proxyParam = (securityForm.proxy || '').trim()
  }

  try {
    const res = await startSecurityTask({
      action: securityAction.value,
      emails,
      proxies: proxiesParam,
      proxy: proxyParam,
      proxy_country: securityForm.proxyCountry || '',
      official_reset: securityForm.officialReset,
      workers: securityForm.workers || 5,
      timeout: securityForm.timeout || 60,
    })
    const taskId = res.taskId || res.task_id
    if (!taskId) throw new Error('未获取到任务 ID')
    securityTaskId.value = taskId

    securityEs.value = createSSE(securityTaskStreamUrl(taskId), {
      init: (ev) => {
        try {
          const snap = JSON.parse(ev.data)
          if (snap.items) {
            securityItems.value = snap.items
          }
        } catch (_) {}
      },
      progress: (ev) => {
        try {
          const msg = JSON.parse(ev.data)
          if (msg.email) {
            if (!securityPendingUpdates[msg.email]) securityPendingUpdates[msg.email] = {}
            const it = securityPendingUpdates[msg.email]
            if (msg.status !== undefined) {
              it.status = msg.status
              if (msg.status === 'running' && !it.started_at) {
                it.started_at = msg.started_at || (Date.now() / 1000)
              }
            }
            if (msg.started_at) it.started_at = msg.started_at
            if (msg.step_text !== undefined) it.step_text = msg.step_text
            if (msg.result !== undefined) it.result = msg.result
            if (msg.error !== undefined) it.error = msg.error
            if (msg.elapsed !== undefined) it.elapsed = msg.elapsed
            scheduleSecurityUpdate()
          }
        } catch (_) {}
      },
      log: (ev) => {
        try {
          const msg = JSON.parse(ev.data)
          if (msg.line) {
            securityPendingLogs.push(msg.line)
            scheduleSecurityUpdate()
            if (securityLogModalVisible.value && currentSecurityLogItem.value) {
              const targetEmail = currentSecurityLogItem.value.email
              if (msg.email ? msg.email === targetEmail : String(msg.line || '').includes(targetEmail)) {
                securityLogLines.value.push(msg.line)
                scrollSecurityModalLog()
              }
            }
          }
        } catch (_) {}
      },
      done: () => {
        flushSecurityUpdates()
        securityRunning.value = false
        if (securityLiveTimer) {
          clearInterval(securityLiveTimer)
          securityLiveTimer = null
        }
        ElMessage.success('安全加固任务已全部完成！')
        load(false)
      },
      end: () => {
        flushSecurityUpdates()
        securityRunning.value = false
        if (securityLiveTimer) {
          clearInterval(securityLiveTimer)
          securityLiveTimer = null
        }
        load(false)
      },
      error: () => {
        flushSecurityUpdates()
        securityRunning.value = false
        if (securityLiveTimer) {
          clearInterval(securityLiveTimer)
          securityLiveTimer = null
        }
      },
    })
  } catch (e) {
    securityRunning.value = false
    if (securityLiveTimer) {
      clearInterval(securityLiveTimer)
      securityLiveTimer = null
    }
    ElMessage.error('启动任务失败: ' + (e.response?.data?.detail || e.message))
  }
}

async function retrySecurityTaskRunner(targetEmails = null) {
  if (!securityTaskId.value) {
    ElMessage.warning('任务未初始化，请先启动任务')
    return
  }
  let toRetry = []
  if (Array.isArray(targetEmails) && targetEmails.length) {
    toRetry = targetEmails
  } else {
    toRetry = Object.values(securityItems.value).filter(isSecurityFailedRow).map((i) => i.email)
  }
  if (!toRetry.length) {
    ElMessage.info('没有需要重试的失败账号')
    return
  }

  try {
    flushSecurityUpdates()
    for (const em of toRetry) {
      if (securityItems.value[em]) {
        securityItems.value[em].status = 'pending'
        securityItems.value[em].step_text = '排队重试中...'
        securityItems.value[em].error = ''
      }
    }
    securityRunning.value = true
    const res = await retrySecurityTask(securityTaskId.value, { emails: toRetry })
    ElMessage.success(`已开始重试 ${res.retrying_count || toRetry.length} 个账号`)
  } catch (e) {
    ElMessage.error('重试失败: ' + (e.response?.data?.detail || e.message))
  }
}

const securityUserScrolledUp = ref(false)
function onSecurityLogScroll() {
  const el = securityModalLogBoxRef.value
  if (!el) return
  const isNearBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= 45
  securityUserScrolledUp.value = !isNearBottom
}
function scrollSecurityModalLog(force = false) {
  if (!force && securityUserScrolledUp.value) return
  nextTick(() => {
    const el = securityModalLogBoxRef.value
    if (el && (force || !securityUserScrolledUp.value)) {
      el.scrollTop = el.scrollHeight
    }
  })
}
function scrollToBottomSecurityLog() {
  securityUserScrolledUp.value = false
  scrollSecurityModalLog(true)
}

async function openSecurityItemLog(row) {
  currentSecurityLogItem.value = row
  securityLogLines.value = []
  securityUserScrolledUp.value = false
  securityLogModalVisible.value = true
  securityLogLoading.value = true
  try {
    const res = await getSecurityTaskLog(securityTaskId.value, row.email)
    securityLogLines.value = res.lines || []
    scrollSecurityModalLog(true)
  } catch (e) {
    securityLogLines.value = row.logs || ['暂无日志']
  } finally {
    securityLogLoading.value = false
  }
}

// 6. 顶部安全加固批量操作菜单跳转
function handleSecurityCommand(cmd) {
  if (cmd === 'batch_pwd_selected') {
    openSecurityTask('password', 'selected')
  } else if (cmd === 'batch_pwd_all_missing') {
    openSecurityTask('password', 'all_missing_pwd')
  } else if (cmd === 'batch_2fa_selected') {
    openSecurityTask('2fa', 'selected')
  } else if (cmd === 'batch_2fa_all_missing') {
    openSecurityTask('2fa', 'all_missing_2fa')
  }
}

// ════════════════════════ 账号批量保温与保鲜控制台 (Account Warming Daemon) ════════════════════════
const warmingVisible = ref(false)
const warmingRunning = ref(false)
const warmingTaskId = ref('')
const warmingEs = ref(null)
const warmingConfigCollapsed = ref(true)
const warmingTargetEmails = ref([])
const warmingItems = ref({})
const warmingLogs = ref([])
const warmingForm = reactive({
  proxy: '__POOL__',
  proxyCountry: 'RANDOM_HOT',
  workers: 5,
})

// 弹窗内单账号日志终端
const warmingLogModalVisible = ref(false)
const currentWarmingLogItem = ref(null)
const warmingLogLines = ref([])
const warmingLogLoading = ref(false)
const warmingModalLogBoxRef = ref(null)

// 实时耗时秒表
const warmingElapsed = ref(0)
const warmingNowTime = ref(Date.now())
let warmingLiveTimer = null

function getWarmingRowElapsed(row) {
  if (!row) return '—'
  if (row.status === 'running') {
    const st = row.started_at || (Date.now() / 1000)
    const now = warmingNowTime.value / 1000
    const sec = Math.max(0, Math.floor(now - st))
    return `${sec}s`
  }
  if (row.elapsed !== undefined && row.elapsed !== null && row.elapsed > 0) {
    return `${row.elapsed}s`
  }
  return '—'
}

// 分页、筛选
const warmingPage = ref(1)
const warmingPageSize = ref(50)
const warmingFilter = ref('all') // 'all' | 'running' | 'failed' | 'success' | 'pending'
const warmingSearch = ref('')

const warmingFilteredRows = computed(() => {
  const list = Object.values(warmingItems.value)
  const kw = warmingSearch.value.trim().toLowerCase()
  const f = warmingFilter.value
  return list.filter((item) => {
    if (kw && !item.email.toLowerCase().includes(kw)) return false
    if (f === 'all') return true
    if (f === 'running') return item.status === 'running'
    if (f === 'pending') return item.status === 'pending'
    if (f === 'failed') return item.status === 'failed'
    if (f === 'success') return item.status === 'success' || item.status === 'done'
    return true
  })
})

const warmingDisplayRows = computed(() => {
  const rows = warmingFilteredRows.value
  const start = (warmingPage.value - 1) * warmingPageSize.value
  return rows.slice(start, start + warmingPageSize.value)
})

let warmingUpdateTimer = null
let warmingPendingUpdates = {}
let warmingPendingLogs = []

function flushWarmingUpdates() {
  if (warmingUpdateTimer) {
    cancelAnimationFrame(warmingUpdateTimer)
    warmingUpdateTimer = null
  }
  if (Object.keys(warmingPendingUpdates).length > 0) {
    const copy = { ...warmingItems.value }
    for (const [em, up] of Object.entries(warmingPendingUpdates)) {
      if (!copy[em]) {
        copy[em] = { email: em, status: 'pending', step: '排队中...', elapsed: 0 }
      }
      Object.assign(copy[em], up)
    }
    warmingItems.value = copy
    warmingPendingUpdates = {}
  }
  if (warmingPendingLogs.length > 0) {
    warmingLogs.value.push(...warmingPendingLogs)
    if (warmingLogs.value.length > 200) {
      warmingLogs.value = warmingLogs.value.slice(-200)
    }
    warmingPendingLogs = []
  }
}

function scheduleWarmingUpdate() {
  if (warmingUpdateTimer) return
  warmingUpdateTimer = requestAnimationFrame(() => {
    warmingUpdateTimer = null
    flushWarmingUpdates()
  })
}

const warmingStats = computed(() => {
  const items = Object.values(warmingItems.value)
  const tot = items.length || warmingTargetEmails.value.length || 0
  const success = items.filter((i) => i.status === 'success' || i.status === 'done').length
  const fail = items.filter((i) => i.status === 'failed').length
  const running = items.filter((i) => i.status === 'running').length
  const pending = items.filter((i) => i.status === 'pending').length
  const done = success + fail
  const pct = tot ? Math.min(100, Math.round((done / tot) * 100)) : 0
  return { total: tot, success, fail, running, pending, done, pct }
})

function openWarmingModal(emailsOrScope = 'selected') {
  let targetList = []
  if (Array.isArray(emailsOrScope)) {
    targetList = emailsOrScope
  } else if (emailsOrScope === 'selected') {
    targetList = selected.value.map((r) => r.email)
  } else if (emailsOrScope === 'cold') {
    targetList = rows.value.filter((r) => !r.last_warmed_at).map((r) => r.email)
    if (!targetList.length) {
      targetList = rows.value.map((r) => r.email)
    }
  } else if (emailsOrScope === 'all') {
    targetList = rows.value.map((r) => r.email)
  }

  if (!targetList.length) {
    ElMessage.warning('没有可供保温的账号')
    return
  }

  warmingTargetEmails.value = targetList
  const initMap = {}
  for (const em of targetList) {
    initMap[em] = {
      email: em,
      status: 'pending',
      step: '等待就绪...',
      models_count: 0,
      user_name: '',
      elapsed: 0,
    }
  }
  warmingItems.value = initMap
  warmingLogs.value = []
  warmingVisible.value = true
  warmingRunning.value = false
  warmingTaskId.value = ''
  warmingElapsed.value = 0
}

function buildWarmingProxyPayload() {
  const p = warmingForm.proxy
  let proxiesStr = ''
  let singleProxy = ''
  if (p === '__NONE__') {
    singleProxy = ''
  } else if (p === '__POOL__') {
    proxiesStr = proxyText(proxyList.value)
  } else if (p) {
    singleProxy = p
  }
  return {
    proxies: proxiesStr,
    proxy: singleProxy,
    proxy_country: warmingForm.proxyCountry === 'RANDOM_HOT' ? '' : warmingForm.proxyCountry,
  }
}

async function startWarmingTaskRun() {
  if (warmingRunning.value) return
  if (!warmingTargetEmails.value.length) {
    ElMessage.warning('没有指定目标账号')
    return
  }

  warmingRunning.value = true
  warmingLogs.value = []
  warmingElapsed.value = 0
  warmingNowTime.value = Date.now()

  if (warmingLiveTimer) clearInterval(warmingLiveTimer)
  warmingLiveTimer = setInterval(() => {
    warmingElapsed.value += 1
    warmingNowTime.value = Date.now()
  }, 1000)

  try {
    const proxyConfig = buildWarmingProxyPayload()
    const res = await startWarmingTask({
      emails: warmingTargetEmails.value,
      proxies: proxyConfig.proxies,
      proxy: proxyConfig.proxy,
      proxy_country: proxyConfig.proxy_country,
      workers: warmingForm.workers,
    })

    warmingTaskId.value = res.task_id

    if (warmingEs.value) {
      warmingEs.value.close()
      warmingEs.value = null
    }

    warmingEs.value = createSSE(warmingStreamUrl(res.task_id), {
      onInit: (data) => {
        if (data.items) {
          warmingPendingUpdates = { ...data.items }
          scheduleWarmingUpdate()
        }
      },
      onProgress: (data) => {
        if (data.email) {
          warmingPendingUpdates[data.email] = {
            ...data,
            step: data.step || data.message || '',
          }
          scheduleWarmingUpdate()
        }
      },
      onLog: (data) => {
        if (data.line) {
          warmingPendingLogs.push(data.line)
          scheduleWarmingUpdate()
        }
      },
      onDone: (data) => {
        flushWarmingUpdates()
        warmingRunning.value = false
        if (warmingLiveTimer) {
          clearInterval(warmingLiveTimer)
          warmingLiveTimer = null
        }
        ElMessage.success('🎉 账号批量保温与保鲜完成！')
        load(false)
      },
      onError: (err) => {
        flushWarmingUpdates()
        warmingRunning.value = false
        if (warmingLiveTimer) {
          clearInterval(warmingLiveTimer)
          warmingLiveTimer = null
        }
      },
    })
  } catch (e) {
    warmingRunning.value = false
    if (warmingLiveTimer) {
      clearInterval(warmingLiveTimer)
      warmingLiveTimer = null
    }
    ElMessage.error('启动保温任务失败: ' + (e.response?.data?.detail || e.message))
  }
}

async function stopWarmingTaskRun() {
  if (!warmingTaskId.value) return
  try {
    await stopWarmingTask(warmingTaskId.value)
    warmingRunning.value = false
    ElMessage.info('已请求停止保温任务')
  } catch (e) {
    ElMessage.error('停止失败: ' + (e.response?.data?.detail || e.message))
  }
}

const warmingUserScrolledUp = ref(false)
function onWarmingLogScroll() {
  const el = warmingModalLogBoxRef.value
  if (!el) return
  const isNearBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= 45
  warmingUserScrolledUp.value = !isNearBottom
}
function scrollWarmingModalLog(force = false) {
  if (!force && warmingUserScrolledUp.value) return
  nextTick(() => {
    const el = warmingModalLogBoxRef.value
    if (el && (force || !warmingUserScrolledUp.value)) {
      el.scrollTop = el.scrollHeight
    }
  })
}
function scrollToBottomWarmingLog() {
  warmingUserScrolledUp.value = false
  scrollWarmingModalLog(true)
}

async function openWarmingItemLog(row) {
  currentWarmingLogItem.value = row
  warmingLogLines.value = []
  warmingUserScrolledUp.value = false
  warmingLogModalVisible.value = true
  warmingLogLoading.value = true
  try {
    const res = await getWarmingLog(warmingTaskId.value, row.email)
    warmingLogLines.value = res.lines || []
    scrollWarmingModalLog(true)
  } catch (e) {
    warmingLogLines.value = ['暂无日志']
  } finally {
    warmingLogLoading.value = false
  }
}

function handleWarmingCommand(cmd) {
  if (cmd === 'warm_selected') {
    openWarmingModal('selected')
  } else if (cmd === 'warm_cold') {
    openWarmingModal('cold')
  } else if (cmd === 'warm_all') {
    openWarmingModal('all')
  }
}

// 账号列表完全自主受控：进入页面或切换 tab 时加载，避免后台并发跑号每次完成都强制全表刷新打扰用户
onActivated(() => {
  load()
  if (focusedRow.value && focusedRow.value.totp_secret) {
    fetchFocusedTotp()
    startFocusedTotpTicker()
  }
})

function cleanupResources() {
  if (plusEs.value) {
    plusEs.value.close()
    plusEs.value = null
  }
  if (oaEs.value) {
    oaEs.value.close()
    oaEs.value = null
  }
  if (oauthEs.value) {
    oauthEs.value.close()
    oauthEs.value = null
  }
  stopOAuthLogPoll()
  stopHealthLogPoll()
  if (securityEs.value) {
    securityEs.value.close()
    securityEs.value = null
  }
  if (securityLiveTimer) {
    clearInterval(securityLiveTimer)
    securityLiveTimer = null
  }
  if (warmingEs.value) {
    warmingEs.value.close()
    warmingEs.value = null
  }
  if (warmingLiveTimer) {
    clearInterval(warmingLiveTimer)
    warmingLiveTimer = null
  }
  stopFocusedTotpTicker()
}

// 全局弹窗状态监测：任意弹窗激活时自动收起底部悬浮操作栏，避免遮挡与视觉穿透冲突
const hasAnyModalOpen = computed(() => {
  return (
    plusVisible.value ||
    plusLogModalVisible.value ||
    oaVisible.value ||
    oaLogModalVisible.value ||
    oauthVisible.value ||
    featVisible.value ||
    oauthLogModalVisible.value ||
    healthVisible.value ||
    healthLogModalVisible.value ||
    refreshVisible.value ||
    securityVisible.value ||
    securityLogModalVisible.value ||
    warmingVisible.value ||
    warmingLogModalVisible.value ||
    exportConfigModalVisible.value ||
    exportVisible.value ||
    credVisible.value ||
    editVisible.value ||
    totpModalVisible.value ||
    mailOtpModalVisible.value ||
    repairPwVisible.value ||
    repair2faVisible.value ||
    extractModalVisible.value ||
    focusedPwdVisible.value ||
    focusedSecretVisible.value
  )
})

onDeactivated(() => {
  cleanupResources()
})

onUnmounted(() => {
  cleanupResources()
})
</script>

<template>
  <div class="registered-page">
    <!-- 背景流体微光环境光球 (Ambient Glass Glow Orbs) -->
    <div class="glass-ambient-sphere sphere-1"></div>
    <div class="glass-ambient-sphere sphere-2"></div>
    <div class="glass-ambient-sphere sphere-3"></div>

    <div class="macos-window-panel">
      <div class="card-glass-specular"></div>
      <div class="liquid-caustic-flare"></div>
      <!-- ──────────── 顶部多维控制中枢 (Linear Command Deck) ──────────── -->
      <header class="linear-command-deck">
        <!-- 第 1 行：核心状态视图切轨 + 全局搜索 + 视图控制 -->
        <div class="command-deck-main">
          <!-- 快速视图分段切轨 (支持多维独立/多选复合筛选) -->
          <div class="linear-segmented-rail">
            <!-- 1. 全部资产 (清空/全选) -->
            <button
              class="segmented-tab tab-all"
              :class="{ 'is-active': isAllActive }"
              :title="isAllActive ? '当前展示全量资产' : '点击清空快捷组合，展示全部资产'"
              @click="toggleQuickFilter('all')"
            >
              <span>全部资产</span>
              <span class="tab-count-badge">{{ regSummary.total || total }}</span>
            </button>

            <!-- 2. 纯新未导 -->
            <button
              class="segmented-tab tab-cyan"
              :class="{ 'is-active': isUnexportedActive }"
              :title="isUnexportedActive ? '点击取消【纯新未导】过滤' : '多选/过滤【纯新未导】'"
              @click="toggleQuickFilter('unexported')"
            >
              <span class="status-dot dot-cyan"></span>
              <span>纯新未导</span>
              <span class="tab-count-badge count-cyan">{{ regSummary.unexported_cnt }}</span>
              <span v-if="isUnexportedActive" class="tab-close-pill">✕</span>
            </button>

            <!-- 3. 已导出 -->
            <button
              class="segmented-tab tab-slate"
              :class="{ 'is-active': isExportedActive }"
              :title="isExportedActive ? '点击取消【已导出】过滤' : '多选/过滤【已导出】'"
              @click="toggleQuickFilter('exported')"
            >
              <span class="status-dot dot-slate"></span>
              <span>已导出</span>
              <span class="tab-count-badge">{{ regSummary.exported_cnt }}</span>
              <span v-if="isExportedActive" class="tab-close-pill">✕</span>
            </button>

            <!-- 4. Codex 已授权 -->
            <button
              class="segmented-tab tab-amber"
              :class="{ 'is-active': isOAuthActive }"
              :title="isOAuthActive ? '点击取消【Codex 已授权】过滤' : '多选/过滤【Codex 已授权】'"
              @click="toggleQuickFilter('oauth')"
            >
              <span class="status-dot dot-amber"></span>
              <span>Codex 已授权</span>
              <span class="tab-count-badge count-amber">{{ regSummary.with_oauth }}</span>
              <span v-if="isOAuthActive" class="tab-close-pill">✕</span>
            </button>

            <!-- 5. 待补安全 -->
            <button
              class="segmented-tab tab-emerald"
              :class="{ 'is-active': isNeedsSecActive }"
              :title="isNeedsSecActive ? '点击取消【待补安全】过滤' : '多选/过滤【待补安全】(缺密码或2FA)'"
              @click="toggleQuickFilter('needs_sec')"
            >
              <span class="status-dot dot-emerald"></span>
              <span>待补安全</span>
              <span v-if="regSummary.missing_sec_cnt !== undefined" class="tab-count-badge count-emerald">{{ regSummary.missing_sec_cnt }}</span>
              <span v-if="isNeedsSecActive" class="tab-close-pill">✕</span>
            </button>

            <!-- 6. 坏号隔离 -->
            <button
              class="segmented-tab tab-rose"
              :class="{ 'is-active': isDeadActive }"
              :title="isDeadActive ? '点击取消【坏号隔离】过滤' : '多选/过滤【坏号隔离】(封号/失效)'"
              @click="toggleQuickFilter('dead')"
            >
              <span class="status-dot dot-rose"></span>
              <span>坏号隔离</span>
              <span v-if="regSummary.dead_cnt !== undefined" class="tab-count-badge count-rose">{{ regSummary.dead_cnt }}</span>
              <span v-if="isDeadActive" class="tab-close-pill">✕</span>
            </button>

            <!-- 多选组合指示标签 (当激活多于1个条件时) -->
            <div v-if="activeQuickFiltersCount >= 2" class="rail-combo-indicator" title="已激活多维复合筛选">
              <span class="combo-text">组合: {{ activeQuickFiltersCount }} 维</span>
              <button class="combo-clear-btn" title="清空全部快捷组合" @click.stop="toggleQuickFilter('all')">清空</button>
            </div>
          </div>

          <!-- 右侧：全局搜索 + 视图工具 + 抽屉开关 -->
          <div class="command-deck-search">
            <div class="search-glass-chassis">
              <el-input
                ref="searchInputRef"
                v-model="searchKeyword"
                placeholder="搜索邮箱、备注、IP、域名 (⌘K)..."
                clearable
                size="small"
                class="linear-search-input"
                :prefix-icon="Search"
                @input="onSearchInput"
                @clear="load(true)"
                @keyup.enter="load(true)"
              >
                <template #suffix>
                  <span class="cmd-k-tag">⌘K</span>
                </template>
              </el-input>
              <div class="search-chassis-flare"></div>
            </div>

            <!-- 密度切换 -->
            <el-dropdown trigger="click" @command="setTableDensity">
              <button
                class="ghost-tool-btn"
                :class="{ 'is-active': tableDensity !== 'default' }"
                :title="`调整表格行高密度 (当前: ${tableDensity === 'compact' ? '紧凑' : tableDensity === 'relaxed' ? '宽松' : '标准'})`"
              >
                <el-icon><Histogram /></el-icon>
              </button>
              <template #dropdown>
                <el-dropdown-menu class="extract-dropdown-menu density-menu-popover">
                  <div class="dropdown-group-title">表格行高密度</div>
                  <el-dropdown-item command="compact" :class="{ 'is-density-active': tableDensity === 'compact' }">
                    <span class="density-item-content">⚡ 紧凑模式 (高屏效)</span>
                    <span v-if="tableDensity === 'compact'" class="density-check-mark">✓</span>
                  </el-dropdown-item>
                  <el-dropdown-item command="default" :class="{ 'is-density-active': tableDensity === 'default' }">
                    <span class="density-item-content">✨ 标准模式 (默认)</span>
                    <span v-if="tableDensity === 'default'" class="density-check-mark">✓</span>
                  </el-dropdown-item>
                  <el-dropdown-item command="relaxed" :class="{ 'is-density-active': tableDensity === 'relaxed' }">
                    <span class="density-item-content">🌊 宽松模式 (大间距)</span>
                    <span v-if="tableDensity === 'relaxed'" class="density-check-mark">✓</span>
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>

            <!-- 自定义列显示 -->
            <el-popover placement="bottom-end" :width="190" trigger="click" popper-class="col-setting-popover">
              <template #reference>
                <button class="ghost-tool-btn" title="自定义显示列">
                  <el-icon><Operation /></el-icon>
                </button>
              </template>
              <div class="col-settings-panel">
                <div class="col-settings-header">
                  <span>自定义表格列</span>
                  <el-button size="small" text type="primary" @click="resetColumnVisibility">重置</el-button>
                </div>
                <div class="col-checkbox-list">
                  <el-checkbox v-model="columnVisibility.security">安全凭据 (密码/2FA)</el-checkbox>
                  <el-checkbox v-model="columnVisibility.tokens">Token 凭据状态</el-checkbox>
                  <el-checkbox v-model="columnVisibility.atExp">AT 有效期</el-checkbox>
                  <el-checkbox v-model="columnVisibility.status">套餐与特权订阅</el-checkbox>
                  <el-checkbox v-model="columnVisibility.export">导出留痕与备注</el-checkbox>
                  <el-checkbox v-model="columnVisibility.time">注册时间</el-checkbox>
                </div>
              </div>
            </el-popover>

            <!-- 全景档案 / 实时 2FA TOTP 面板显隐开关 -->
            <button
              class="ghost-tool-btn"
              :class="{ 'is-active': !detailsCollapsed }"
              :title="detailsCollapsed ? '展开右侧全景档案与 2FA 演算面板' : '收起右侧全景档案'"
              @click="detailsCollapsed = !detailsCollapsed"
            >
              <el-icon><DataAnalysis /></el-icon>
            </button>

            <!-- 刷新 -->
            <button class="ghost-tool-btn" :class="{ 'is-loading': loading }" title="刷新数据与汇总统计" @click="refreshAll">
              <el-icon :class="{ 'is-spinning': loading }"><Refresh /></el-icon>
            </button>
          </div>
        </div>

        <!-- ──────── 第 2 行：经典全量水平下拉筛选栏 (All Dropdowns Filter Strip) ──────── -->
        <div class="command-deck-filters-bar">
          <div class="filters-wrap-scroll">
            <!-- 1. 域名筛选 (包含重点 iCloud 苹果邮箱及库内全部实际后缀) -->
            <div class="filter-item-wrap" :class="{ 'is-filtered': filterDomain !== 'all' }">
              <span class="filter-label">域名:</span>
              <el-select
                v-model="filterDomain"
                placeholder="全部域名"
                size="small"
                class="acct-select acct-select-domain"
                filterable
                clearable
                @change="load(true)"
                @clear="filterDomain = 'all'; load(true)"
              >
                <el-option label="全部邮箱域名" value="all" />
                <el-option-group label="常用快捷">
                  <el-option label="🍎 iCloud (苹果邮箱)" value="icloud" />
                  <el-option label="微软全系 (@outlook/@hotmail)" value="microsoft" />
                  <el-option label="Outlook (@outlook.com)" value="outlook" />
                  <el-option label="Hotmail (@hotmail.com)" value="hotmail" />
                  <el-option label="Gmail (@gmail.com)" value="gmail" />
                  <el-option label="其它自定义域名" value="custom" />
                </el-option-group>
                <el-option-group v-if="domainOptions.length > 0" label="库内实际后缀">
                  <el-option
                    v-for="d in domainOptions"
                    :key="d.domain"
                    :label="`${d.domain} (${d.count}个)`"
                    :value="d.domain"
                  />
                </el-option-group>
              </el-select>
            </div>

            <!-- 2. 出口国家筛选 -->
            <div class="filter-item-wrap" :class="{ 'is-filtered': filterCountry !== 'all' }">
              <span class="filter-label">出口:</span>
              <el-select
                v-model="filterCountry"
                placeholder="全部出口国家"
                size="small"
                class="acct-select acct-select-country"
                filterable
                clearable
                @change="load(true)"
                @clear="filterCountry = 'all'; load(true)"
              >
                <el-option label="全部出口国家" value="all" />
                <el-option-group v-if="countryOptions.length > 0" label="库内实际出口">
                  <el-option
                    v-for="c in countryOptions"
                    :key="c.country"
                    :label="getCountryOptionLabel(c.country, c.count)"
                    :value="c.country"
                  />
                  <el-option label="⚪ 未记录出口国家" value="NONE" />
                </el-option-group>
                <el-option-group label="常用国家">
                  <el-option
                    v-for="c in POPULAR_FILTER_COUNTRIES"
                    :key="c"
                    :label="getCountryOptionLabel(c)"
                    :value="c"
                  />
                </el-option-group>
              </el-select>
            </div>

            <!-- 3. 安全防护筛选 -->
            <div class="filter-item-wrap" :class="{ 'is-filtered': filterSec !== 'all' }">
              <span class="filter-label">安全:</span>
              <el-select
                v-model="filterSec"
                placeholder="安全防护"
                size="small"
                class="acct-select acct-select-sec"
                @change="load(true)"
              >
                <el-option label="全部安全状态" value="all" />
                <el-option :label="`已绑 2FA (${regSummary.with_2fa})`" value="with_2fa" />
                <el-option :label="`待补 2FA (${Math.max(0, (regSummary.total || total) - regSummary.with_2fa)})`" value="missing_2fa" />
                <el-option :label="`已设密码 (${regSummary.with_pwd})`" value="with_pwd" />
                <el-option :label="`免密未设 (${Math.max(0, (regSummary.total || total) - regSummary.with_pwd)})`" value="missing_pwd" />
                <el-option label="⚠️ 密码或2FA不全" value="missing_security" />
                <el-option label="✅ 密码与2FA双全" value="both_secured" />
              </el-select>
            </div>

            <!-- 4. 套餐特权筛选 -->
            <div class="filter-item-wrap" :class="{ 'is-filtered': filterPlan !== 'all' }">
              <span class="filter-label">套餐:</span>
              <el-select
                v-model="filterPlan"
                placeholder="全部套餐"
                size="small"
                class="acct-select acct-select-plan"
                @change="load(true)"
              >
                <el-option label="全部套餐特权" value="all" />
                <el-option label="💎 Plus / 试用特权" value="plus" />
                <el-option label="👑 Pro 高级特权" value="pro" />
                <el-option label="⚪ Free 基础号" value="free" />
                <el-option label="🎁 可领 Plus 免单" value="extract_eligible" />
              </el-select>
            </div>

            <!-- 5. 授权状态筛选 -->
            <div class="filter-item-wrap" :class="{ 'is-filtered': filterOAuth !== 'all' }">
              <span class="filter-label">授权:</span>
              <el-select
                v-model="filterOAuth"
                placeholder="全部授权"
                size="small"
                class="acct-select acct-select-oauth"
                @change="load(true)"
              >
                <el-option label="全部授权状态" value="all" />
                <el-option label="✅ Codex 授权成功" value="oauth_success" />
                <el-option label="📱 手机接码成功" value="oauth_phone_verified" />
                <el-option label="⚡ 免接码直接授权" value="oauth_no_phone" />
                <el-option label="🔄 具备 RT 凭据" value="has_rt" />
                <el-option label="📱 需接码 (未接)" value="oauth_need_phone" />
                <el-option label="❌ 授权失败" value="oauth_failed" />
                <el-option label="⚪ 从未授权" value="oauth_unchecked" />
              </el-select>
            </div>

            <!-- 6. 导出留痕筛选 -->
            <div class="filter-item-wrap" :class="{ 'is-filtered': filterAtExport !== 'all' }">
              <span class="filter-label">导出:</span>
              <el-select
                v-model="filterAtExport"
                placeholder="全部导出"
                size="small"
                class="acct-select acct-select-export"
                @change="load(true)"
              >
                <el-option label="全部导出状态" value="all" />
                <el-option label="⭕ 纯新未导 (未出库)" value="unexported" />
                <el-option label="✅ 已导出 (全部已导)" value="exported" />
                <el-option label="🔑 已导 AT 凭据" value="at" />
                <el-option label="🔐 已导 账密/2FA" value="email_pw" />
                <el-option label="📦 已导 Sub2API" value="sub2api" />
                <el-option label="📦 已导 CPA" value="cpa" />
                <el-option label="🌐 已导 Session" value="session" />
              </el-select>
            </div>

            <!-- 7. Access Token 有效期筛选 -->
            <div class="filter-item-wrap" :class="{ 'is-filtered': filterAtExp !== 'all' }">
              <span class="filter-label">AT有效期:</span>
              <el-select
                v-model="filterAtExp"
                placeholder="全部有效期"
                size="small"
                class="acct-select acct-select-at-exp"
                @change="load(true)"
              >
                <el-option label="全部 AT 有效期" value="all" />
                <el-option label="✅ 仍有效" value="valid" />
                <el-option label="⏳ 24小时内到期" value="expiring" />
                <el-option label="❌ 已过期" value="expired" />
                <el-option label="⚪ 无 AT" value="no_at" />
                <el-option label="❔ 无法解析到期" value="unknown" />
              </el-select>
            </div>

            <!-- 8. 验活健康度筛选 -->
            <div class="filter-item-wrap" :class="{ 'is-filtered': filterHealth !== 'all' }">
              <span class="filter-label">验活:</span>
              <el-select
                v-model="filterHealth"
                placeholder="全部验活"
                size="small"
                class="acct-select acct-select-health"
                @change="load(true)"
              >
                <el-option label="全部验活状态" value="all" />
                <el-option label="💀 失效与封号 (全部坏号)" value="dead" />
                <el-option label="❌ 凭证失效 (401/过期)" value="token_invalid" />
                <el-option label="🚫 账号封禁 (Banned)" value="banned" />
                <el-option label="✅ 全部存活有效" value="alive" />
                <el-option label="⏳ 未验活" value="unchecked" />
              </el-select>
            </div>

            <!-- 8. 提链状态筛选 -->
            <div class="filter-item-wrap" :class="{ 'is-filtered': filterExtract !== 'all' }">
              <span class="filter-label">提链:</span>
              <el-select
                v-model="filterExtract"
                placeholder="全部提链"
                size="small"
                class="acct-select acct-select-extract"
                @change="load(true)"
              >
                <el-option label="全部提链状态" value="all" />
                <el-option label="🎁 待提链" value="extract_eligible" />
                <el-option label="✅ 提链成功" value="extract_success" />
                <el-option label="❌ 提链失败" value="extract_failed" />
              </el-select>
            </div>

            <!-- 9. 代理出口 -->
            <div class="filter-item-wrap" :class="{ 'is-filtered': Boolean(form?.proxy) }">
              <span class="filter-label">代理:</span>
              <el-select
                v-model="form.proxy"
                placeholder="全部代理 / 直连"
                size="small"
                class="acct-select acct-select-proxy"
                filterable
                clearable
                allow-create
                @change="load(true)"
                @clear="form.proxy = ''; load(true)"
              >
                <el-option v-for="p in proxyList" :key="p" :label="p" :value="p" />
              </el-select>
            </div>
          </div>

          <!-- 一键重置全部条件 (在有激活筛选时显示于右侧) -->
          <button
            v-if="hasActiveAttributeFilter"
            class="filter-reset-link-btn"
            title="清空所有下拉条件与快捷筛选"
            @click="resetAdvancedFilters"
          >
            <span>✕ 重置</span>
          </button>
        </div>
      </header>

      <!-- ──────────── 核心工作区：全宽高密度数据表格 + 右侧全景档案抽屉 ──────────── -->
      <div class="registered-workspace-body" :class="{ 'with-details-panel': !detailsCollapsed }">

        <!-- ───── 中间栏: 高信噪比暗黑数据网格 (Center Table Pane) ───── -->
        <section class="center-table-pane panel">
          <!-- Action Ribbon 工具栏 -->
          <div class="command-deck-actions">
            <!-- 左侧：操作菜单组 -->
            <div class="actions-group-left">
              <!-- 勾选指示器 -->
              <div v-if="selectedCount" class="selection-pill">
                <span class="selection-dot"></span>
                <span>已选 <b>{{ selectedCount }}</b> 项</span>
                <button class="clear-sel-link" @click="clearSelected" title="取消全部勾选">✕</button>
              </div>

              <!-- 1. 批量流水线 (Tasks ▾) -->
              <el-dropdown trigger="click" @command="handleHealthCheckCommand">
                <button class="action-menu-btn" :class="{ 'has-selected': selectedCount }">
                  <el-icon><Timer /></el-icon>
                  <span>批量流水线{{ selectedCount ? ` (${selectedCount})` : '' }}</span>
                  <el-icon class="arrow-down"><ArrowDown /></el-icon>
                </button>
                <template #dropdown>
                  <el-dropdown-menu class="extract-dropdown-menu">
                    <div class="dropdown-group-title">Token 验活与探测</div>
                    <el-dropdown-item command="token_selected" :disabled="!selectedCount">验活选中账号 ({{ selectedCount }})</el-dropdown-item>
                    <el-dropdown-item command="token_unchecked">验活未检账号</el-dropdown-item>
                    <el-dropdown-item command="token_all">全量全库重验</el-dropdown-item>
                    <div class="dropdown-group-title divider-title">OAuth 接码授权</div>
                    <el-dropdown-item @click="handleOAuthCommand('oauth_selected')" :disabled="!selectedCount">📱 Codex OAuth 接码 (选中)</el-dropdown-item>
                    <el-dropdown-item @click="handleOAuthCommand('oauth_all')">📱 Codex OAuth 全量接码</el-dropdown-item>
                    <div class="dropdown-group-title divider-title">提链 / 出码</div>
                    <el-dropdown-item @click="openExtractChannel('paypal_pipeline')">🎁 PayPal 提链+代付 (一条龙)</el-dropdown-item>
                    <el-dropdown-item @click="openExtractChannel('paypal')">🔗 PayPal 仅提链</el-dropdown-item>
                    <el-dropdown-item @click="openExtractChannel('gcash')">🇵🇭 GCash 提链/出码</el-dropdown-item>
                    <div class="dropdown-group-title divider-title">账号保温保鲜</div>
                    <el-dropdown-item @click="handleWarmingCommand('warm_selected')" :disabled="!selectedCount">☀️ 保温选中账号 ({{ selectedCount }})</el-dropdown-item>
                    <el-dropdown-item @click="handleWarmingCommand('warm_cold')">☀️ 保温未保鲜冷号</el-dropdown-item>
                    <el-dropdown-item @click="handleWarmingCommand('warm_all')">☀️ 全库轮询保温</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>

              <!-- 2. 复制凭证 (Copy ▾) -->
              <el-dropdown trigger="click" @command="handleCopyAtCommand">
                <button class="action-menu-btn">
                  <el-icon><CopyDocument /></el-icon>
                  <span>复制凭据</span>
                  <el-icon class="arrow-down"><ArrowDown /></el-icon>
                </button>
                <template #dropdown>
                  <el-dropdown-menu class="extract-dropdown-menu">
                    <div class="dropdown-group-title">Access Token (AT)</div>
                    <el-dropdown-item command="copy_at_selected" :disabled="!selectedCount">复制选中 AT ({{ selectedCount }})</el-dropdown-item>
                    <el-dropdown-item command="copy_at_with_email" :disabled="!selectedCount">复制「邮箱----AT」格式</el-dropdown-item>
                    <el-dropdown-item command="copy_at_page">复制当前页 AT</el-dropdown-item>
                    <el-dropdown-item command="copy_at_all" divided>全库复制 AT</el-dropdown-item>
                    <div class="dropdown-group-title divider-title">Session 结构</div>
                    <el-dropdown-item @click="handleCopySessionCommand('copy_session_selected')" :disabled="!selectedCount">复制选中 Session (JSON 数组)</el-dropdown-item>
                    <el-dropdown-item @click="handleCopySessionCommand('copy_session_lines_selected')" :disabled="!selectedCount">复制选中 Session (单行)</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>

              <!-- 3. Token 刷新与自愈 (Refresh ▾) -->
              <el-dropdown trigger="click">
                <button class="action-menu-btn action-refresh-btn" :class="{ 'has-selected': selectedCount }" title="双模 Token 刷新与失效重登自愈">
                  <el-icon><Refresh /></el-icon>
                  <span>刷新 Token{{ selectedCount ? ` (${selectedCount})` : '' }}</span>
                  <el-icon class="arrow-down"><ArrowDown /></el-icon>
                </button>
                <template #dropdown>
                  <el-dropdown-menu class="extract-dropdown-menu">
                    <div class="dropdown-group-title">Token 快速刷新与重获</div>
                    <el-dropdown-item @click="handleRefreshCommand('refresh_selected')" :disabled="!selectedCount">
                      🔄 刷新选中 Token ({{ selectedCount }})
                    </el-dropdown-item>
                    <el-dropdown-item @click="handleRefreshCommand('refresh_no_token')">
                      ⚡ 一键刷新缺少/失效 Token 账号
                    </el-dropdown-item>
                    <el-dropdown-item @click="handleRefreshCommand('refresh_all')">
                      🔄 全量全库刷新 Token
                    </el-dropdown-item>
                    <div class="dropdown-group-title divider-title">授权自愈</div>
                    <el-dropdown-item @click="() => doRecoverOAuth()">
                      ⚡ 扫描并找回历史授权 (RT自愈)
                    </el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>

              <!-- 4. 运维管理 (Ops ▾) -->
              <el-dropdown trigger="click">
                <button class="action-menu-btn">
                  <el-icon><Setting /></el-icon>
                  <span>运维管理</span>
                  <el-icon class="arrow-down"><ArrowDown /></el-icon>
                </button>
                <template #dropdown>
                  <el-dropdown-menu class="extract-dropdown-menu">
                    <div class="dropdown-group-title">安全补密与 2FA</div>
                    <el-dropdown-item @click="handleSecurityCommand('batch_pwd_selected')" :disabled="!selectedCount">🔑 批量设密码 (选中无密)</el-dropdown-item>
                    <el-dropdown-item @click="handleSecurityCommand('batch_pwd_all_missing')">🔑 全量补设密码</el-dropdown-item>
                    <el-dropdown-item @click="handleSecurityCommand('batch_2fa_selected')" :disabled="!selectedCount">🛡️ 批量补绑 2FA (选中)</el-dropdown-item>
                    <el-dropdown-item @click="handleSecurityCommand('batch_2fa_all_missing')">🛡️ 全量补绑 2FA</el-dropdown-item>
                    <div class="dropdown-group-title divider-title">Token 刷新与自愈</div>
                    <el-dropdown-item @click="handleRefreshCommand('refresh_selected')" :disabled="!selectedCount">🔄 刷新选中 Token</el-dropdown-item>
                    <el-dropdown-item @click="handleRefreshCommand('refresh_no_token')">⚡ 一键刷新缺少/失效 Token 账号</el-dropdown-item>
                    <el-dropdown-item @click="handleRefreshCommand('refresh_all')">🔄 全量刷新 Token</el-dropdown-item>
                    <el-dropdown-item @click="() => doRecoverOAuth()">⚡ 扫描并找回历史授权 (RT自愈)</el-dropdown-item>
                    <el-dropdown-item @click="openFeatBoard">📊 特征工程大屏</el-dropdown-item>
                    <div class="dropdown-group-title divider-title">数据清理与删除</div>
                    <el-dropdown-item @click="cleanInvalid">🧹 清理库内空号</el-dropdown-item>
                    <el-dropdown-item :disabled="!selectedCount" @click="deleteSelected" style="color: var(--el-color-danger)">🗑️ 删除选中账号 ({{ selectedCount }})</el-dropdown-item>
                    <el-dropdown-item @click="deleteAll" style="color: var(--el-color-danger)">⚠️ 清空全部账号</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>

            <!-- 右侧：分卷设置与一键导出 -->
            <div class="actions-group-right">
              <el-select v-model="exportChunkSize" size="small" class="linear-chunk-select" title="分卷文件账号条数设置">
                <el-option label="不分卷" :value="0" />
                <el-option label="50条/卷" :value="50" />
                <el-option label="100条/卷" :value="100" />
                <el-option label="200条/卷" :value="200" />
                <el-option label="500条/卷" :value="500" />
              </el-select>

              <el-dropdown trigger="click" @command="openExportModal" @visible-change="(v) => v && loadExportFormats()">
                <button class="primary-export-btn" :disabled="exporting" @click.stop="openExportModal(null)">
                  <el-icon><Download /></el-icon>
                  <span>{{ exportBtnText }}</span>
                  <el-icon class="arrow-down"><ArrowDown /></el-icon>
                </button>
                <template #dropdown>
                  <el-dropdown-menu class="macos-dropdown-menu">
                    <el-dropdown-item v-for="f in exportFormats" :key="f.id" :command="f">
                      {{ f.label }}
                      <span v-if="f.note" class="hint" style="margin-left: 6px">{{ f.note }}</span>
                    </el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
          </div>

          <!-- 核心数据网格 (Table) -->
          <div
            class="table-scroll-wrap"
            v-memo="[rows, loading, selected, focusedRow, tableDensity, page, pageSize, columnVisibility.security, columnVisibility.tokens, columnVisibility.atExp, columnVisibility.status, columnVisibility.export, columnVisibility.time]"
          >
            <el-skeleton v-if="loading && !rows.length" :rows="8" animated style="padding: 16px" />
            <el-table
              v-else
              ref="tableRef"
              v-loading="loading"
              :data="rows"
              row-key="email"
              height="100%"
              size="small"
              :row-class-name="getRowClassName"
              :class="['octopus-table-grid', `density-${tableDensity}`]"
              @row-click="setFocusedRow"
              @selection-change="(v) => (selected = v)"
              @sort-change="onTableSort"
            >
              <!-- 1. 勾选列 (开启 reserve-selection，确保换页/导出勾选永不丢失) -->
              <el-table-column type="selection" width="38" align="center" header-align="center" fixed="left" :reserve-selection="true" />

              <!-- 2. 账号与网络出口 (靠左对齐，基线笔直规整) -->
              <el-table-column prop="email" label="账号与网络出口" min-width="260" fixed="left" align="left" header-align="left" show-overflow-tooltip>
                <template #default="{ row }">
                  <div class="account-cell-container">
                    <!-- 主行：品牌微标 + 邮箱等宽字 + 复制按键 -->
                    <div class="account-main-line">
                      <span class="provider-avatar-badge" :style="{ background: (row._providerMeta || getEmailProviderMeta(row.email)).bg, color: (row._providerMeta || getEmailProviderMeta(row.email)).color }">
                        {{ (row._providerMeta || getEmailProviderMeta(row.email)).icon }}
                      </span>
                      <span class="email-text mono" @click.stop="copyText(row.email)" title="点击复制邮箱">{{ row.email }}</span>
                      <el-icon class="email-copy-btn" @click.stop="copyText(row.email)"><CopyDocument /></el-icon>
                    </div>

                    <!-- 次行：规范化微型芯片胶囊群 -->
                    <div class="account-meta-line">
                      <span
                        v-if="row.reg_country"
                        class="meta-badge-pill country-pill"
                        :title="`注册出口: ${row._countryLabel || formatCountry(row.reg_country)} [${row.reg_country}] (点击过滤该国家)`"
                        @click.stop="applyFilter('country', row.reg_country)"
                      >
                        {{ row._countryLabel || formatCountry(row.reg_country) }}
                      </span>
                      <span
                        v-if="row.reg_ip"
                        class="meta-badge-pill ip-pill mono"
                        :title="`出口IP: ${row.reg_ip} (点击复制)`"
                        @click.stop="copyText(row.reg_ip, 'IP已复制')"
                      >
                        {{ row.reg_ip }}
                      </span>
                      <span
                        v-else-if="row.reg_proxy"
                        class="meta-badge-pill ip-pill mono"
                        :title="`出口代理: ${row.reg_proxy}`"
                      >
                        {{ row._proxyHost || formatProxyHost(row.reg_proxy) }}
                      </span>
                      <span
                        v-if="row.mail_oauth?.pickup_url"
                        class="meta-badge-pill pickup-pill"
                        @click.stop="window.open(row.mail_oauth.pickup_url, '_blank')"
                        title="点击在线取件"
                      >
                        取件
                      </span>
                    </div>
                  </div>
                </template>
              </el-table-column>

              <!-- 3. 安全凭据 (密码/2FA 经典紧凑胶囊徽章) -->
              <el-table-column v-if="columnVisibility.security" label="密码/2FA" width="126" align="center" header-align="center">
                <template #default="{ row }">
                  <div class="sec-col-wrapper">
                    <span
                      v-if="row.password"
                      class="sec-badge sec-pwd-ok"
                      title="已设置登录密码 (点击复制密码)"
                      @click.stop="copyText(row.password, '密码已复制')"
                    >
                      密码✓
                    </span>
                    <span
                      v-else
                      class="sec-badge sec-pwd-no"
                      title="未设置密码 (点击快速补设独立密码)"
                      @click.stop="openRepairPassword(row)"
                    >
                      密码×
                    </span>

                    <span
                      v-if="row.totp_secret"
                      class="sec-badge sec-2fa-ok"
                      title="已绑定 2FA (点击查看实时动态码)"
                      @click.stop="openTotpModal(row)"
                    >
                      2FA✓
                    </span>
                    <span
                      v-else
                      class="sec-badge sec-2fa-no"
                      title="未绑定 2FA (点击快速补绑)"
                      @click.stop="openRepair2FA(row)"
                    >
                      2FA×
                    </span>
                  </div>
                </template>
              </el-table-column>

              <!-- 4. Token 凭据健康度 (表头与内容居中对齐，内部两行垂直对齐) -->
              <el-table-column v-if="columnVisibility.tokens" label="Token 凭据健康" min-width="160" align="center" header-align="center">
                <template #default="{ row }">
                  <div class="cell-token-block">
                    <div class="token-status-line">
                      <span class="pulse-indicator-dot" :class="row.at_len ? 'dot-emerald' : 'dot-rose'"></span>
                      <span
                        class="token-main-text mono"
                        :class="{ 'is-ok': row.at_len, 'is-refreshable': !row.at_len }"
                        :title="!row.at_len ? '该账号 Token 缺失或已过期，点击立即唤起双模刷新重登' : 'Access Token 正常 (点击亦可手动刷新)'"
                        @click.stop="openTokenRefreshForOne(row)"
                      >
                        {{ row.at_len ? `AT 正常 (${(row.at_len / 1024).toFixed(1)}KB)` : 'AT 缺失失效' }}
                      </span>
                    </div>
                    <div class="token-sub-line">
                      <span class="token-sub-dot" :class="row.rt_len && row.rt_len > 20 ? 'sub-dot-active' : 'sub-dot-none'"></span>
                      <span
                        v-if="row.rt_len && row.rt_len > 20"
                        class="rt-active-text mono rt-click-refresh"
                        title="具备 Refresh Token，支持无感自愈续签 (点击刷新)"
                        @click.stop="openTokenRefreshForOne(row)"
                      >
                        RT 凭据在库 (可自愈)
                      </span>
                      <span
                        v-else
                        class="rt-none-text"
                        title="无 Refresh Token，点击可通过密码/OTP重登自愈"
                        @click.stop="openTokenRefreshForOne(row)"
                      >
                        仅 AT 凭据 (无RT)
                      </span>
                    </div>
                  </div>
                </template>
              </el-table-column>

              <!-- 5. Access Token 剩余有效期 (现代 Linear 风格微芯片) -->
              <el-table-column
                v-if="columnVisibility.atExp !== false"
                prop="at_expires_at"
                label="AT 有效期"
                min-width="140"
                align="center"
                header-align="center"
                sortable="custom"
              >
                <template #default="{ row }">
                  <div
                    class="at-expiry-modern-badge"
                    :class="(row._atExp || formatAtExpiry(row)).cls"
                    :title="(row._atExp || formatAtExpiry(row)).title"
                    @click.stop="openTokenRefreshForOne(row)"
                  >
                    <span class="expiry-status-dot"></span>
                    <span class="expiry-val-text mono">{{ (row._atExp || formatAtExpiry(row)).text }}</span>
                  </div>
                </template>
              </el-table-column>

              <!-- 6. 套餐与特权订阅 (克制高信噪比设计) -->
              <el-table-column v-if="columnVisibility.status" label="套餐与业务特权" min-width="160" align="center" header-align="center">
                <template #default="{ row }">
                  <div class="cell-entitlements-block">
                    <template v-if="(row._badges || getStatusBadges(row)).length">
                      <span
                        v-for="(b, idx) in (row._badges || getStatusBadges(row))"
                        :key="idx"
                        class="entitlement-badge"
                        :class="[b.type, b.effect]"
                        :title="b.desc"
                        @click.stop="b.url && window.open(b.url, '_blank')"
                      >
                        {{ b.label }}
                      </span>
                    </template>
                    <span v-else class="free-plain-text"><span class="free-dot"></span>Free</span>
                  </div>
                </template>
              </el-table-column>

              <!-- 6. 导出留痕与批次备注 -->
              <el-table-column v-if="columnVisibility.export" label="导出留痕与备注" min-width="150" align="center" header-align="center">
                <template #default="{ row }">
                  <div class="cell-export-block">
                    <div v-if="row.exported_at || row.at_exported_at" class="export-status-line" @click.stop="quickEditExportNote(row)">
                      <span class="pulse-indicator-dot dot-cyan"></span>
                      <span class="export-tag-label">已导: {{ row.export_fmt_label || row.export_fmt || 'AT' }}</span>
                      <span class="export-date-mono mono">({{ row._exportDate || formatExportDateShort(row.exported_at || row.at_exported_at) }})</span>
                    </div>
                    <div v-else class="export-status-line">
                      <span class="pulse-indicator-dot dot-emerald"></span>
                      <span class="export-fresh-text">纯新未导出</span>
                    </div>
                    <div class="export-note-text" @click.stop="quickEditExportNote(row)" :title="row.export_note || row.at_export_note || '点击添加备注'">
                      <span>{{ row.export_note || row.at_export_note || '+ 添加备注' }}</span>
                    </div>
                  </div>
                </template>
              </el-table-column>

              <!-- 8. 注册时间 -->
              <el-table-column v-if="columnVisibility.time" prop="created_at" label="注册时间" min-width="135" align="center" header-align="center" sortable="custom">
                <template #default="{ row }">
                  <div class="cell-time-block">
                    <div class="time-main-mono mono">{{ row._createdTime || fmtTime(row.created_at) }}</div>
                    <div class="time-relative-text">{{ row._timeAgo || timeAgo(row.created_at) }}</div>
                  </div>
                </template>
              </el-table-column>

              <!-- 8. 快捷操作列 (固定右侧，加宽至 280 彻底解决显示不全/遮挡) -->
              <el-table-column label="快捷操作" width="280" fixed="right" align="center" header-align="center">
                <template #default="{ row }">
                  <div class="cell-actions-block">
                    <button class="octopus-row-btn btn-cred" @click.stop="viewCred(row.email)" title="查看完整账号凭据">
                      凭证
                    </button>
                    <button v-if="row.totp_secret" class="octopus-row-btn btn-2fa" @click.stop="openTotpModal(row)" title="生成当前 6 位 2FA 动态码">
                      2FA
                    </button>
                    <button class="octopus-row-btn btn-refresh" @click.stop="openTokenRefreshForOne(row)" title="刷新 Access Token / 重新登录获取凭证">
                      刷Token
                    </button>
                    <button class="octopus-row-btn btn-mail" @click.stop="openMailOtpModal(row)" title="检索邮件验证码">
                      查码
                    </button>

                    <el-dropdown trigger="click" @command="(cmd) => handleRowMoreCommand(cmd, row)">
                      <button class="octopus-row-btn btn-more" title="更多高级操作" @click.stop>
                        ···
                      </button>
                      <template #dropdown>
                        <el-dropdown-menu class="extract-dropdown-menu">
                          <div class="dropdown-group-title">Token 凭证与自愈</div>
                          <el-dropdown-item command="refresh_token">
                            <el-icon><Refresh /></el-icon> 🔄 刷新此账号 Token
                          </el-dropdown-item>
                          <el-dropdown-item command="recover_oauth">
                            <el-icon><CircleCheckFilled /></el-icon> 找回历史授权凭据 (RT自愈)
                          </el-dropdown-item>
                          <div class="dropdown-group-title divider-title">数据与凭证导出</div>
                          <el-dropdown-item command="edit">
                            <el-icon><Setting /></el-icon> 编辑/补全凭证
                          </el-dropdown-item>
                          <el-dropdown-item command="copy_session">
                            <el-icon><Document /></el-icon> 复制 Session JSON
                          </el-dropdown-item>
                          <el-dropdown-item command="download_sub2">
                            <el-icon><Download /></el-icon> 导出 Sub2API JSON
                          </el-dropdown-item>
                          <el-dropdown-item command="download_cpa">
                            <el-icon><Download /></el-icon> 导出 CPA JSON
                          </el-dropdown-item>
                          <div class="dropdown-group-title divider-title">操作与运维</div>
                          <el-dropdown-item v-if="row.at_len" command="copy_at">
                            <el-icon><Key /></el-icon> 复制 Access Token (AT)
                          </el-dropdown-item>
                          <el-dropdown-item v-if="row.rt_len" command="copy_rt">
                            <el-icon><Refresh /></el-icon> 复制 Refresh Token (RT)
                          </el-dropdown-item>
                          <el-dropdown-item command="oauth_export">
                            <el-icon><Phone /></el-icon> Codex OAuth 接码授权
                          </el-dropdown-item>
                          <el-dropdown-item command="fetch_mail">
                            <el-icon><Message /></el-icon> 检索邮件验证码
                          </el-dropdown-item>
                          <el-dropdown-item v-if="!row.password" command="repair_pwd">
                            <el-icon><Key /></el-icon> 补设密码
                          </el-dropdown-item>
                          <el-dropdown-item v-if="!row.totp_secret" command="repair_2fa">
                            <el-icon><Lock /></el-icon> 补绑 2FA
                          </el-dropdown-item>
                          <el-dropdown-item v-if="row.password" command="copy_pwd">
                            <el-icon><CopyDocument /></el-icon> 复制密码
                          </el-dropdown-item>
                          <el-dropdown-item v-if="row.totp_secret" command="copy_2fa">
                            <el-icon><CopyDocument /></el-icon> 复制 2FA Secret
                          </el-dropdown-item>
                          <el-dropdown-item divided command="delete" style="color: var(--el-color-danger)">
                            <el-icon><Delete /></el-icon> 删除账号
                          </el-dropdown-item>
                        </el-dropdown-menu>
                      </template>
                    </el-dropdown>
                  </div>
                </template>
              </el-table-column>
            </el-table>
          </div>

          <!-- 分页底栏 -->
          <div class="table-footer-bar">
            <div class="footer-left-info">
              <span v-if="selectedCount" class="selected-badge">已勾选 <b>{{ selectedCount }}</b> 项</span>
              <span v-else class="total-badge">当前页共 {{ rows.length }} 条记录</span>
            </div>

            <div class="footer-pagination-right">
              <span class="page-size-custom">
                <span class="page-size-custom-label">自定义</span>
                <el-input-number
                  v-model="customPageSize"
                  :min="PAGE_SIZE_MIN"
                  :max="PAGE_SIZE_MAX"
                  :step="10"
                  size="small"
                  controls-position="right"
                  class="page-size-custom-input"
                  @change="applyCustomPageSize"
                  @keyup.enter="applyCustomPageSize"
                />
                <span class="page-size-custom-unit">条/页</span>
              </span>
              <el-pagination
                v-model:current-page="page"
                v-model:page-size="pageSize"
                :page-sizes="pageSizeOptions"
                :total="total"
                layout="total, sizes, prev, pager, next, jumper"
                size="small"
                background
                class="octopus-pagination"
                @size-change="handleSizeChange"
                @current-change="handleCurrentChange"
              />
            </div>
          </div>
        </section>

        <!-- ───── 右侧栏: 选中账号全景档案 (Details Panel) ───── -->
        <aside v-if="!detailsCollapsed" class="details-panel-drawer">
          <!-- 头部：全景档案标题栏 -->
          <div class="details-heading">
            <div class="details-head-title">
              <span class="dossier-pulse-beacon"></span>
              <div class="head-text-meta">
                <span class="title-main">账号全景档案</span>
                <span class="title-sub mono">ACCOUNT DOSSIER</span>
              </div>
            </div>
            <div class="details-head-actions">
              <button
                v-if="focusedRow"
                class="dossier-tool-btn"
                :class="{ 'is-loading': focusedTotpLoading }"
                title="刷新当前账号 2FA 动态码"
                @click="refreshFocusedAccount"
              >
                <el-icon :class="{ 'is-spinning': focusedTotpLoading }"><Refresh /></el-icon>
              </button>
              <button class="dossier-tool-btn close-btn" title="收起全景档案面板" @click="detailsCollapsed = true">
                <el-icon><Close /></el-icon>
              </button>
            </div>
          </div>

          <div v-if="focusedRow" class="details-content">
            <!-- 1. 账号核心身份卡片 (Profile Hero) -->
            <div class="dossier-card profile-hero-card">
              <div class="profile-hero-top">
                <div
                  class="provider-avatar-frame"
                  :style="{ background: getEmailProviderMeta(focusedRow.email).bg, color: getEmailProviderMeta(focusedRow.email).color }"
                >
                  <span class="avatar-icon">{{ getEmailProviderMeta(focusedRow.email).icon }}</span>
                </div>
                <div class="profile-meta-info">
                  <div class="profile-email-row" @click="copyText(focusedRow.email, '邮箱已复制')" title="点击一键复制完整邮箱">
                    <span class="profile-email mono">{{ focusedRow.email }}</span>
                    <el-icon class="copy-hint-icon"><CopyDocument /></el-icon>
                  </div>
                  <div class="profile-tags-row">
                    <span class="dossier-tag tag-country">
                      {{ formatDossierCountry(focusedRow.reg_country) }}
                    </span>
                    <span class="dossier-tag tag-time">
                      ⏱️ {{ timeAgo(focusedRow.created_at) }}
                    </span>
                  </div>
                </div>
              </div>
            </div>

            <!-- 2. 实时 2FA 动态口令认证器 (Live TOTP Authenticator) -->
            <div v-if="focusedRow.totp_secret" class="dossier-card totp-auth-card">
              <div class="card-title-bar">
                <div class="title-left">
                  <span class="live-dot-radar"></span>
                  <span class="title-text">2FA 实时动态口令</span>
                </div>
                <div class="totp-timer-chip" :class="totpTimerUrgencyClass">
                  <span class="timer-num mono">{{ focusedTotpRemaining }}s</span>
                </div>
              </div>

              <!-- 动态码两段式高质感显示卡槽 -->
              <div class="totp-display-screen" @click="copyText(focusedTotpCode, '2FA动态码已复制')" title="点击复制当前 6 位验证码">
                <div class="totp-code-val mono">
                  <span class="code-seg">{{ (focusedTotpCode || '------').slice(0, 3) }}</span>
                  <span class="code-dot">·</span>
                  <span class="code-seg">{{ (focusedTotpCode || '------').slice(3, 6) }}</span>
                </div>
                <div class="totp-action-hover">
                  <el-icon><CopyDocument /></el-icon>
                  <span>复制</span>
                </div>
              </div>

              <!-- 倒计时进度条与下期预告 -->
              <div class="totp-progress-track">
                <div
                  class="totp-progress-bar"
                  :class="totpTimerUrgencyClass"
                  :style="{ width: `${(focusedTotpRemaining / 30) * 100}%` }"
                ></div>
              </div>
              <div v-if="focusedTotpNextCode" class="totp-next-hint">
                <span class="next-label">下期预备:</span>
                <span class="next-val mono">{{ focusedTotpNextCode }}</span>
              </div>
            </div>

            <!-- 未绑定 2FA 时优雅的高质感虚位引导卡 -->
            <div v-else class="dossier-card totp-empty-card">
              <div class="empty-card-inner">
                <div class="empty-shield-icon">
                  <el-icon><Lock /></el-icon>
                </div>
                <div class="empty-totp-info">
                  <div class="empty-title">未启用 2FA 双因子认证</div>
                  <div class="empty-desc">绑定 TOTP 密钥可显著提升账号抗风控与防封能力</div>
                </div>
                <button class="action-ghost-btn btn-repair" @click="openRepair2FA(focusedRow)">
                  + 立即补绑
                </button>
              </div>
            </div>

            <!-- 3. 核心安全凭据 (Credentials Slot) -->
            <div class="dossier-card">
              <div class="card-title-bar">
                <div class="title-left">
                  <span class="section-icon cred-icon"><el-icon><Key /></el-icon></span>
                  <span class="title-text">核心安全凭据</span>
                  <span class="title-code mono">CREDENTIALS</span>
                </div>
              </div>
              <div class="card-rows-wrap">
                <!-- 登录密码 -->
                <div class="dossier-row-slot">
                  <div class="slot-label">
                    <span>登录密码</span>
                    <span v-if="focusedRow.password" class="slot-badge badge-success">已设密</span>
                    <span v-else class="slot-badge badge-warn">免密未设</span>
                  </div>
                  <div class="slot-value-box">
                    <template v-if="focusedRow.password">
                      <span class="mono secret-text">
                        {{ focusedPwdVisible ? focusedRow.password : maskSecret(focusedRow.password, 3, 3) }}
                      </span>
                      <button class="slot-icon-btn" :title="focusedPwdVisible ? '隐藏明文' : '查看明文'" @click.stop="focusedPwdVisible = !focusedPwdVisible">
                        <el-icon><View v-if="!focusedPwdVisible" /><Hide v-else /></el-icon>
                      </button>
                      <button class="slot-icon-btn" title="复制密码" @click.stop="copyText(focusedRow.password, '密码已复制')">
                        <el-icon><CopyDocument /></el-icon>
                      </button>
                    </template>
                    <template v-else>
                      <button class="slot-text-action-btn" @click="openRepairPassword(focusedRow)">+ 补设密码</button>
                    </template>
                  </div>
                </div>

                <!-- 2FA Secret -->
                <div class="dossier-row-slot">
                  <div class="slot-label">
                    <span>2FA Secret</span>
                    <span v-if="focusedRow.totp_secret" class="slot-badge badge-success">受保护</span>
                    <span v-else class="slot-badge badge-danger">待补绑</span>
                  </div>
                  <div class="slot-value-box">
                    <template v-if="focusedRow.totp_secret">
                      <span class="mono secret-text">
                        {{ focusedSecretVisible ? focusedRow.totp_secret : maskSecret(focusedRow.totp_secret, 4, 4) }}
                      </span>
                      <button class="slot-icon-btn" :title="focusedSecretVisible ? '隐藏明文' : '查看完整密钥'" @click.stop="focusedSecretVisible = !focusedSecretVisible">
                        <el-icon><View v-if="!focusedSecretVisible" /><Hide v-else /></el-icon>
                      </button>
                      <button class="slot-icon-btn" title="复制2FA密钥" @click.stop="copyText(focusedRow.totp_secret, '2FA密钥已复制')">
                        <el-icon><CopyDocument /></el-icon>
                      </button>
                    </template>
                    <template v-else>
                      <button class="slot-text-action-btn btn-rose" @click="openRepair2FA(focusedRow)">+ 补绑2FA</button>
                    </template>
                  </div>
                </div>

                <!-- Token 凭据 -->
                <div class="dossier-row-slot">
                  <div class="slot-label">
                    <span>Token 凭据</span>
                    <span v-if="focusedRow.at_len" class="slot-badge badge-success">AT正常</span>
                    <span v-else class="slot-badge badge-danger">AT缺失</span>
                  </div>
                  <div class="slot-value-box">
                    <span class="mono secret-text" :class="focusedRow.at_len ? 'text-emerald' : 'text-rose'">
                      {{ focusedRow.at_len ? `AT (${(focusedRow.at_len / 1024).toFixed(1)} KB)` : 'AT 缺失' }}
                    </span>
                    <span
                      class="dossier-tag mono"
                      :class="(focusedRow._atExp || formatAtExpiry(focusedRow)).cls"
                      :title="(focusedRow._atExp || formatAtExpiry(focusedRow)).title"
                    >
                      {{ (focusedRow._atExp || formatAtExpiry(focusedRow)).text }}
                    </span>
                    <span v-if="focusedRow.rt_len" class="dossier-tag tag-cyan mono">RT 在库</span>
                  </div>
                </div>
              </div>
            </div>

            <!-- 4. 出口与交付留痕 (Network & Delivery) -->
            <div class="dossier-card">
              <div class="card-title-bar">
                <div class="title-left">
                  <span class="section-icon net-icon"><el-icon><Compass /></el-icon></span>
                  <span class="title-text">出口与交付留痕</span>
                  <span class="title-code mono">TRACE</span>
                </div>
              </div>
              <div class="card-rows-wrap">
                <!-- 注册出口代理 -->
                <div class="dossier-row-slot">
                  <div class="slot-label">
                    <span>出口代理</span>
                  </div>
                  <div class="slot-value-box">
                    <span class="mono secret-text truncate-text" :title="focusedRow.reg_proxy || '直连出口'">
                      {{ formatProxyHost(focusedRow.reg_proxy) || '直连出口' }}
                    </span>
                    <button
                      v-if="focusedRow.reg_proxy"
                      class="slot-icon-btn"
                      title="复制代理地址"
                      @click.stop="copyText(focusedRow.reg_proxy, '代理地址已复制')"
                    >
                      <el-icon><CopyDocument /></el-icon>
                    </button>
                  </div>
                </div>

                <!-- 出口 IP -->
                <div v-if="focusedRow.reg_ip" class="dossier-row-slot">
                  <div class="slot-label">
                    <span>出口 IP</span>
                  </div>
                  <div class="slot-value-box">
                    <span class="mono secret-text">{{ focusedRow.reg_ip }}</span>
                    <button class="slot-icon-btn" title="复制出口IP" @click.stop="copyText(focusedRow.reg_ip, '出口IP已复制')">
                      <el-icon><CopyDocument /></el-icon>
                    </button>
                  </div>
                </div>

                <!-- 导出交付状态 -->
                <div class="dossier-row-slot">
                  <div class="slot-label">
                    <span>交付归档</span>
                  </div>
                  <div class="slot-value-box">
                    <span v-if="focusedRow.exported_at || focusedRow.at_exported_at" class="dossier-tag tag-cyan">
                      已导出 ({{ formatExportDateShort(focusedRow.exported_at || focusedRow.at_exported_at) }})
                    </span>
                    <span v-else class="dossier-tag tag-emerald">
                      纯新在库 · 随时可导
                    </span>
                  </div>
                </div>

                <!-- 批次备注 -->
                <div class="dossier-row-slot">
                  <div class="slot-label">
                    <span>批次备注</span>
                  </div>
                  <div class="slot-value-box">
                    <div class="note-edit-cell" @click="quickEditExportNote(focusedRow)" title="点击编辑批次备注">
                      <el-icon class="note-pen-icon"><EditPen /></el-icon>
                      <span class="note-content">{{ focusedRow.export_note || focusedRow.at_export_note || '添加备注...' }}</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- 5. 安全防御诊断与健康度 (Security Health) -->
            <div class="dossier-card health-diagnostic-card">
              <div class="card-title-bar">
                <div class="title-left">
                  <span class="section-icon shield-icon"><el-icon><CircleCheckFilled /></el-icon></span>
                  <span class="title-text">安全态势诊断</span>
                  <span class="title-code mono">HEALTH</span>
                </div>
                <div class="health-grade-chip" :class="`grade-${focusedHealthReport.theme}`">
                  <span class="grade-letter mono">{{ focusedHealthReport.grade }} 级</span>
                  <span class="grade-label">{{ focusedHealthReport.label }}</span>
                </div>
              </div>

              <!-- 3 格安全度量刻度条 -->
              <div class="health-tri-meter">
                <div class="meter-bar" :class="{ 'is-active': focusedHealthReport.checks.pwd }" title="独立登录密码">
                  <span class="meter-label">密保</span>
                </div>
                <div class="meter-bar" :class="{ 'is-active': focusedHealthReport.checks.totp }" title="2FA 双因子认证">
                  <span class="meter-label">2FA</span>
                </div>
                <div class="meter-bar" :class="{ 'is-active': focusedHealthReport.checks.token }" title="Token 凭据健全">
                  <span class="meter-label">Token</span>
                </div>
              </div>

              <!-- 诊断建议条 -->
              <div class="health-tips-list">
                <div v-for="(tip, idx) in focusedHealthReport.tips" :key="idx" class="health-tip-item" :class="`tip-${tip.type}`">
                  <span class="tip-dot"></span>
                  <span class="tip-msg">{{ tip.text }}</span>
                </div>
              </div>
            </div>
          </div>

          <!-- 空状态：未选中账号时 -->
          <div v-else class="details-empty-state">
            <div class="empty-radar-wrap">
              <div class="radar-circle circle-1"></div>
              <div class="radar-circle circle-2"></div>
              <div class="radar-circle circle-3"></div>
              <el-icon class="empty-radar-icon"><DataAnalysis /></el-icon>
            </div>
            <div class="empty-title">等待激活账号档案</div>
            <div class="empty-desc">在左侧数据网格点击任意行，即刻开启该账号的 2FA 动态口令、会话凭据与全景留痕</div>
          </div>

          <!-- 底部固定多维操作面板 -->
          <footer v-if="focusedRow" class="dossier-footer-actions">
            <!-- 主操作大按键：查看完整凭证 -->
            <button class="footer-btn btn-primary" @click="viewCred(focusedRow.email)">
              <el-icon><Check /></el-icon>
              <span>查看完整凭证详情</span>
            </button>
            <!-- 次操作双按键并列 -->
            <div class="footer-sub-grid">
              <button class="footer-btn btn-secondary" @click="copySessionJson(focusedRow.email)" title="复制完整 Session JSON">
                <el-icon><CopyDocument /></el-icon>
                <span>复制 Session</span>
              </button>
              <button class="footer-btn btn-secondary btn-mail" @click="openMailOtpModal(focusedRow)" title="实时查收邮件验证码">
                <el-icon><Message /></el-icon>
                <span>查收邮件码</span>
              </button>
            </div>
          </footer>
        </aside>
      </div>
    </div>

    <!-- ──────────────── 底部毛玻璃极客悬浮批量操作栏 (Floating Action Bar) ──────────────── -->
    <transition name="floating-bar-slide">
      <div v-if="selectedCount && !hasAnyModalOpen" class="floating-action-bar">
        <div class="floating-bar-pill">
          <!-- 选中计数 -->
          <div class="floating-counter-chip">
            <span class="pulse-counter-dot"></span>
            <span class="counter-text">已选 <strong>{{ selectedCount }}</strong> 项</span>
          </div>

          <div class="floating-divider"></div>

          <!-- 快捷操作按钮组 (统一极简黑曜石 Dock 风格) -->
          <div class="floating-actions-group">
            <button
              class="dock-btn-primary"
              @click="openExportModal(null)"
              title="一键多格式批量导出"
            >
              <el-icon><Download /></el-icon>
              <span>导出选中 ({{ selectedCount }})</span>
            </button>

            <button
              class="dock-btn"
              @click="openHealthCheck('selected')"
              title="套餐与试用探测（默认美国出口、10 并发）"
            >
              <el-icon class="ico-blue"><Timer /></el-icon>
              <span>验活</span>
            </button>

            <button
              class="dock-btn"
              @click="handleSecurityCommand('batch_2fa_selected')"
              title="批量为选中账号补绑 2FA"
            >
              <el-icon class="ico-emerald"><Lock /></el-icon>
              <span>补绑2FA</span>
            </button>

            <button
              class="dock-btn"
              @click="handleSecurityCommand('batch_pwd_selected')"
              title="批量为选中无密码账号补设密码"
            >
              <el-icon class="ico-amber"><Key /></el-icon>
              <span>批量改密</span>
            </button>

            <button
              class="dock-btn"
              @click="handleOAuthCommand('oauth_selected')"
              title="Codex OAuth 授权与接码"
            >
              <el-icon class="ico-purple"><Phone /></el-icon>
              <span>OAuth接码</span>
            </button>

            <button
              class="dock-btn"
              @click="openWarmingModal('selected')"
              title="自动与官方交互保温保鲜"
            >
              <el-icon class="ico-warm"><Sunny /></el-icon>
              <span>保温</span>
            </button>

            <button
              class="dock-btn"
              @click="handleCopyAtCommand('copy_at_selected')"
              title="复制选中 Access Token"
            >
              <el-icon class="ico-gray"><CopyDocument /></el-icon>
              <span>复制AT</span>
            </button>

            <button
              class="dock-btn dock-btn-danger"
              @click="deleteSelected"
              title="从数据库删除选中账号"
            >
              <el-icon><Delete /></el-icon>
              <span>删除</span>
            </button>
          </div>

          <div class="floating-divider"></div>

          <!-- 取消勾选 -->
          <button
            type="button"
            class="dock-close-btn"
            title="取消勾选 (Esc)"
            @click="clearSelected"
          >
            <el-icon><Close /></el-icon>
          </button>
        </div>
      </div>
    </transition>

    <!-- ──────────────── Plus 状态并发检测控制台弹窗 (macOS 架构) ──────────────── -->
    <el-dialog
      v-model="plusVisible" width="880px" top="5vh"
      class="oa-custom-dialog plus-dialog"
      :close-on-click-modal="false" @closed="closePlusCheck"
    >
      <template #header>
        <div class="oa-header">
          <div class="oa-header-title">
            <span class="oa-title-badge plus-badge">PLUS</span>
            <span class="oa-title-text">Plus 状态与封号并发检测控制台</span>
            <el-tag size="small" type="info" round effect="plain">{{ plusTargetEmails.length }} 个账号</el-tag>
          </div>
          <div class="oa-header-extra">
            <el-button size="small" text @click="plusConfigCollapsed = !plusConfigCollapsed">
              <el-icon><Setting /></el-icon>{{ plusConfigCollapsed ? '展开参数配置' : '收起参数配置' }}
            </el-button>
          </div>
        </div>
      </template>

      <div class="oa-dialog-container">
        <!-- 参数配置卡片 (默认折叠收起) -->
        <el-collapse-transition>
          <div v-show="!plusConfigCollapsed" class="oa-config-card">
            <el-form label-position="top" :disabled="plusRunning" size="small">
              <el-row :gutter="12">
                <el-col :xs="24" :sm="12" :md="8">
                  <el-form-item label="检测代理（支持下拉选择/代理池轮询/手动输入/直连）">
                    <el-select
                      v-model="plusForm.proxy" filterable clearable allow-create default-first-option
                      :reserve-keyword="false" placeholder="选择或手动输入代理" style="width: 100%"
                    >
                      <el-option
                        v-if="proxyList.length"
                        label="🌐 全局代理池轮询 (自动多Worker分配)"
                        value="__POOL__"
                      />
                      <el-option v-for="p in proxyList" :key="p" :label="p" :value="p" />
                    </el-select>
                  </el-form-item>
                </el-col>
                <el-col :xs="24" :sm="12" :md="8">
                  <el-form-item label="代理目标国家（自动重写代理与请求特征）">
                    <el-select
                      v-model="plusForm.proxyCountry" filterable allow-create
                      placeholder="选择目标国家" style="width: 100%"
                    >
                      <el-option
                        v-for="c in COUNTRY_OPTIONS" :key="c.value"
                        :label="c.label" :value="c.value"
                      />
                    </el-select>
                  </el-form-item>
                </el-col>
                <el-col :xs="12" :sm="6" :md="4">
                  <el-form-item label="并发 Worker 数">
                    <el-input-number v-model="plusForm.workers" :min="1" :max="20" style="width: 100%" />
                  </el-form-item>
                </el-col>
                <el-col :xs="12" :sm="6" :md="4">
                  <el-form-item label="单请求超时 (秒)">
                    <el-input-number v-model="plusForm.timeout" :min="5" :max="60" style="width: 100%" />
                  </el-form-item>
                </el-col>
              </el-row>
              <div style="font-size: 11.5px; color: var(--el-text-color-secondary); line-height: 1.5; margin-top: 2px">
                💡 <b>运作逻辑</b>：从左侧选定的代理（或全局代理池）获取基础通道，系统会自动将其重写为<b>【{{ plusForm.proxyCountry || '保持原样' }}】</b>出口并分配独立 Session，两者<b>协同生效</b>。
              </div>
            </el-form>
          </div>
        </el-collapse-transition>

        <!-- KPI 统计看板：点卡片即筛选表格 -->
        <div class="plus-kpi-grid">
          <div
            class="plus-kpi-card clickable-card"
            :class="{ 'is-filter-active': plusFilter === 'all' }"
            title="查看全部账号"
            @click="setPlusFilter('all')"
          >
            <span class="kpi-label">已检测 / 总数</span>
            <span class="kpi-num">{{ plusStats.done }} / {{ plusStats.total }}</span>
          </div>
          <div v-if="plusStats.total_pro > 0" class="plus-kpi-card hit-pro">
            <span class="kpi-label">👑 Pro (20x:{{ plusStats.pro_20x }} / 5x:{{ plusStats.pro_5x }})</span>
            <span class="kpi-num text-pro">{{ plusStats.total_pro }}</span>
          </div>
          <div v-if="plusStats.team_active > 0" class="plus-kpi-card hit-team">
            <span class="kpi-label">💎 Team 团队版</span>
            <span class="kpi-num text-team">{{ plusStats.team_active }}</span>
          </div>
          <div
            class="plus-kpi-card hit-active clickable-card"
            :class="{ 'is-filter-active': plusFilter === 'plus_active' }"
            title="只看 Plus 生效"
            @click="setPlusFilter('plus_active')"
          >
            <span class="kpi-label">★ Plus 生效中</span>
            <span class="kpi-num text-primary">{{ plusStats.plus_active }}</span>
          </div>
          <div
            class="plus-kpi-card hit-promo clickable-card"
            :class="{ 'is-filter-active': plusFilter === 'plus_eligible' }"
            title="只看 Plus 试用"
            @click="setPlusFilter('plus_eligible')"
          >
            <span class="kpi-label">◆ Plus 试用</span>
            <span class="kpi-num text-success">{{ plusStats.plus_eligible }}</span>
          </div>
          <div
            class="plus-kpi-card clickable-card"
            :class="{ 'is-filter-active': plusFilter === 'free' }"
            title="只看 Free"
            @click="setPlusFilter('free')"
          >
            <span class="kpi-label">Free 普通号</span>
            <span class="kpi-num">{{ plusStats.free }}</span>
          </div>
          <div
            class="plus-kpi-card clickable-card"
            :class="{ 'card-warn': plusStats.banned > 0 || plusStats.token_invalid > 0, 'is-filter-active': plusFilter === 'dead' }"
            title="点这里只看封号和凭证失效"
            @click="setPlusFilter('dead')"
          >
            <span class="kpi-label">封号 / 凭证失效</span>
            <span class="kpi-num text-danger">{{ plusStats.banned + plusStats.token_invalid }}</span>
            <span v-if="plusStats.banned + plusStats.token_invalid > 0" class="kpi-sub-hint">封号 {{ plusStats.banned }} · 失效 {{ plusStats.token_invalid }}</span>
          </div>
          <div class="plus-progress-cell">
            <el-progress
              :percentage="plusStats.percent"
              :status="plusStats.done === plusStats.total && plusStats.total > 0 ? 'success' : ''"
              :stroke-width="8"
              striped
              :striped-flow="plusRunning"
            />
          </div>
        </div>

        <!-- 核心表格：每个账号一行全宽监控列表 (单栏无右侧流水，纯净自适应) -->
        <div class="plus-table-box">
          <el-table :data="plusRows" size="small" stripe height="340" class="macos-table" :highlight-current-row="false">
            <el-table-column prop="email" label="账号邮箱" min-width="240" show-overflow-tooltip>
              <template #default="{ row }">
                <button
                  class="macos-tag-btn copy-btn"
                  title="点击复制邮箱"
                  @click="copyText(row.email)"
                >
                  <span class="mono">{{ row.email }}</span>
                  <el-icon class="copy-ico"><CopyDocument /></el-icon>
                </button>
              </template>
            </el-table-column>

            <el-table-column label="检测状态" width="130" align="center">
              <template #default="{ row }">
                <div v-if="row.status === 'running'" class="running-pill">
                  <span class="pulse-dot"></span> 检测中...
                </div>
                <el-tag v-else-if="row.status === 'done'" type="success" size="small" effect="light">已完成</el-tag>
                <el-tag v-else-if="row.status === 'cancelled'" type="info" size="small">已取消</el-tag>
                <el-tag v-else type="info" size="small" effect="plain">排队中</el-tag>
              </template>
            </el-table-column>

            <el-table-column label="Plus 结论" min-width="180">
              <template #default="{ row }">
                <template v-if="row.result">
                  <el-tag
                    :type="(PLUS_STATE_META[row.result.status] || {}).type || 'info'"
                    size="small"
                    :effect="row.result.status === 'plus_eligible' || row.result.status === 'plus_active' ? 'dark' : 'light'"
                  >
                    {{ (PLUS_STATE_META[row.result.status] || { label: row.result.status }).label }}
                  </el-tag>
                  <span v-if="row.result.plan" class="hint" style="margin-left: 6px; font-size: 11px">
                    plan: {{ row.result.plan }}
                  </span>
                  <el-tooltip v-if="row.result.error" :content="row.result.error" placement="top">
                    <span class="hint error-hint" style="margin-left: 6px; color: var(--el-color-danger); cursor: help">⚠</span>
                  </el-tooltip>
                </template>
                <span v-else class="hint">—</span>
              </template>
            </el-table-column>

            <el-table-column label="耗时" width="85" align="right">
              <template #default="{ row }">
                <span class="mono" style="font-size: 11.5px">{{ row.elapsed ? row.elapsed + 's' : (row.status === 'running' ? '计时中' : '—') }}</span>
              </template>
            </el-table-column>

            <el-table-column label="操作" width="85" align="center" fixed="right">
              <template #default="{ row }">
                <el-button size="small" text type="primary" @click="openPlusItemLog(row)">
                  <el-icon><Document /></el-icon>日志
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </div>

      <template #footer>
        <div class="oa-dialog-footer">
          <div class="footer-tip">
            <span v-if="plusRunning" class="running-indicator">
              <span class="pulse-dot"></span> 正在并发检测 (Workers: {{ plusForm.workers }})...
            </span>
            <span v-else-if="plusTaskId" class="finished-indicator">
              检测已完成，结果已实时写回注册结果数据库
            </span>
          </div>
          <div class="footer-btns">
            <el-button @click="closePlusCheck">关闭</el-button>
            <el-button v-if="plusRunning" type="danger" plain @click="stopPlusCheckTask">
              <el-icon><SwitchButton /></el-icon>停止检测
            </el-button>
            <el-button v-else type="primary" class="start-gradient-btn" :loading="plusRunning" @click="startPlusCheckTask">
              <el-icon><VideoPlay /></el-icon>{{ plusTaskId ? '重新检测' : '开始检测' }}
            </el-button>
          </div>
        </div>
      </template>
    </el-dialog>

    <!-- ──────────────── 单账号详细检测日志终端弹窗 ──────────────── -->
    <el-dialog
      v-model="plusLogModalVisible"
      width="780px"
      top="8vh"
      class="macos-terminal-dialog"
      :close-on-click-modal="false"
    >
      <template #header>
        <div class="modal-header">
          <div class="window-dots">
            <span class="dot red"></span>
            <span class="dot yellow"></span>
            <span class="dot green"></span>
          </div>
          <div class="modal-title-info">
            <span class="modal-email mono" @click="currentPlusLogItem?.email && copyText(currentPlusLogItem.email, '邮箱已复制')" title="点击复制邮箱">{{ currentPlusLogItem?.email }}</span>
            <span class="modal-run-tag">
              Plus 检测日志
            </span>
          </div>
        </div>
      </template>

      <div class="modal-terminal-wrap">
        <div ref="plusModalLogBoxRef" class="modal-terminal-body" @scroll="onPlusLogScroll">
          <div
            v-for="(line, idx) in plusLogLines"
            :key="idx"
            class="terminal-line"
            :class="getLogClass(line)"
          >
            <span v-if="formatTerminalLine(line).time" class="terminal-time mono">{{ formatTerminalLine(line).time }}</span>
            <span class="terminal-text">{{ formatTerminalLine(line).text }}</span>
          </div>
          <div v-if="!plusLogLines.length" class="terminal-empty">
            {{ plusLogLoading ? '正在加载日志...' : '暂无详细日志' }}
          </div>
        </div>
        <transition name="fade">
          <button
            v-if="plusUserScrolledUp"
            class="terminal-scroll-bottom-pill"
            title="检测到您正在查看历史日志，点击快速回到底部"
            @click="scrollToBottomPlusLog"
          >
            <el-icon><ArrowDown /></el-icon>
            <span>回到底部</span>
          </button>
        </transition>
      </div>

      <template #footer>
        <div class="modal-footer">
          <span class="log-count-tip">共 {{ plusLogLines.length }} 行日志</span>
          <div class="modal-footer-btns">
            <el-button size="small" @click="copyText(plusLogLines.join('\n'))">
              <el-icon><CopyDocument /></el-icon>复制全部日志
            </el-button>
            <el-button size="small" type="primary" @click="plusLogModalVisible = false">
              关闭
            </el-button>
          </div>
        </div>
      </template>
    </el-dialog>

    <!-- ──────────────── OAICS 资格检测控制台弹窗 ──────────────── -->
    <el-dialog
      v-model="oaVisible" width="880px" top="5vh"
      class="oa-custom-dialog plus-dialog"
      :close-on-click-modal="false" @closed="closeOA"
    >
      <template #header>
        <div class="oa-header">
          <div class="oa-header-title">
            <span class="oa-title-badge">OAICS</span>
            <span class="oa-title-text">资格检测控制台</span>
            <el-tag size="small" type="info" round effect="plain">{{ selected.length }} 个账号</el-tag>
          </div>
          <div class="oa-header-extra">
            <el-button size="small" text @click="oaConfigCollapsed = !oaConfigCollapsed">
              <el-icon><Setting /></el-icon>{{ oaConfigCollapsed ? '展开参数配置' : '收起参数配置' }}
            </el-button>
          </div>
        </div>
      </template>

      <div class="oa-dialog-container">
        <!-- 配置卡片 (默认折叠收起) -->
        <el-collapse-transition>
          <div v-show="!oaConfigCollapsed" class="oa-config-card">
            <el-form label-position="top" :disabled="oaRunning" size="small">
              <el-row :gutter="12">
                <el-col :span="11">
                  <el-form-item label="接码/检测代理池 (每行一条，支持 sticky 格式)">
                    <el-input
                      v-model="oaForm.proxies" type="textarea" :rows="3" class="mono oa-proxy-input"
                      placeholder="socks5h://user-region-JP-sid-xxx@host:port&#10;user:pass-BR-session-5m@host:port"
                    />
                    <div class="oa-proxy-actions">
                      <el-button size="small" text type="primary" @click="loadProxyListToOA">
                        载入代理池 ({{ proxyList.length }})
                      </el-button>
                      <el-button size="small" text @click="oaForm.proxies = ''">清空</el-button>
                    </div>
                  </el-form-item>
                </el-col>
                <el-col :span="13">
                  <el-row :gutter="8">
                    <el-col :span="8">
                      <el-form-item label="并发数">
                        <el-input-number v-model="oaForm.workers" :min="1" :max="20" style="width: 100%" />
                      </el-form-item>
                    </el-col>
                    <el-col :span="8">
                      <el-form-item label="每号轮数">
                        <el-input-number v-model="oaForm.rounds" :min="1" :max="20" style="width: 100%" />
                      </el-form-item>
                    </el-col>
                    <el-col :span="8">
                      <el-form-item label="超时(秒)">
                        <el-input-number v-model="oaForm.timeout" :min="5" :max="120" style="width: 100%" />
                      </el-form-item>
                    </el-col>
                    <el-col :span="8">
                      <el-form-item label="出口国家">
                        <el-input v-model="oaForm.proxyCountry" class="mono" />
                      </el-form-item>
                    </el-col>
                    <el-col :span="8">
                      <el-form-item label="账单国家">
                        <el-input v-model="oaForm.billingCountry" class="mono" />
                      </el-form-item>
                    </el-col>
                    <el-col :span="8">
                      <el-form-item label="币种">
                        <el-input v-model="oaForm.currency" class="mono" />
                      </el-form-item>
                    </el-col>
                  </el-row>
                  <div class="oa-options-row">
                    <el-checkbox v-model="oaForm.skipProxyCheck">跳过出口校验 (更快)</el-checkbox>
                    <el-checkbox v-model="oaForm.withPromo">带促销 (1个月免费)</el-checkbox>
                  </div>
                </el-col>
              </el-row>
            </el-form>
          </div>
        </el-collapse-transition>

        <!-- KPI 栏目 -->
        <div class="oa-kpi-bar">
          <div class="oa-kpi-item">
            <div class="kpi-label">总体进度</div>
            <div class="kpi-val">{{ oaStats.done }} / {{ oaStats.total }}</div>
          </div>
          <div class="oa-kpi-item kpi-hit">
            <div class="kpi-label">OAICS 命中</div>
            <div class="kpi-val highlight">{{ oaStats.hit }}</div>
          </div>
          <div class="oa-kpi-item">
            <div class="kpi-label">普通 CS</div>
            <div class="kpi-val">{{ oaStats.cs }}</div>
          </div>
          <div class="oa-kpi-item" :class="{ 'kpi-warn': oaStats.err > 0 }">
            <div class="kpi-label">出错/无AT</div>
            <div class="kpi-val">{{ oaStats.err }}</div>
          </div>
          <div class="oa-progress-wrap">
            <el-progress
              :percentage="oaStats.percent"
              :status="oaStats.done === oaStats.total && oaStats.total > 0 ? 'success' : ''"
              :stroke-width="8"
              striped
              :striped-flow="oaRunning"
            />
          </div>
        </div>

        <!-- 核心全宽表格 (每个账号一行，无右侧流水) -->
        <div class="plus-table-box">
          <el-table :data="oaRows" size="small" stripe height="340" class="macos-table" :highlight-current-row="false">
            <el-table-column prop="email" label="账号邮箱" min-width="240" show-overflow-tooltip>
              <template #default="{ row }">
                <button
                  class="macos-tag-btn copy-btn"
                  title="点击复制邮箱"
                  @click="copyText(row.email)"
                >
                  <span class="mono">{{ row.email }}</span>
                  <el-icon class="copy-ico"><CopyDocument /></el-icon>
                </button>
              </template>
            </el-table-column>

            <el-table-column label="检测状态" width="130" align="center">
              <template #default="{ row }">
                <div v-if="row.status === 'running'" class="running-pill">
                  <span class="pulse-dot"></span> 探测中...
                </div>
                <el-tag v-else-if="row.status === 'done'" type="success" size="small" effect="light">已完成</el-tag>
                <el-tag v-else-if="row.status === 'cancelled'" type="info" size="small">已取消</el-tag>
                <el-tag v-else type="info" size="small" effect="plain">排队中</el-tag>
              </template>
            </el-table-column>

            <el-table-column label="OA 资格结果" min-width="180">
              <template #default="{ row }">
                <template v-if="row.result">
                  <el-tag
                    :type="(OA_STATE_META[row.result.state] || {}).type || 'info'"
                    size="small"
                    :effect="row.result.state === 'OAICS' ? 'dark' : 'light'"
                  >
                    {{ (OA_STATE_META[row.result.state] || { label: row.result.state }).label }}
                  </el-tag>
                  <span v-if="row.result.session_id_masked" class="hint mono" style="margin-left: 6px; font-size: 11px">
                    {{ row.result.session_id_masked }}
                  </span>
                  <el-tooltip v-if="row.result.error" :content="row.result.error" placement="top">
                    <span class="hint error-hint" style="margin-left: 6px; color: var(--el-color-danger); cursor: help">⚠</span>
                  </el-tooltip>
                </template>
                <span v-else class="hint">—</span>
              </template>
            </el-table-column>

            <el-table-column label="耗时" width="85" align="right">
              <template #default="{ row }">
                <span class="mono" style="font-size: 11.5px">
                  {{ row.result && row.result.elapsed_ms ? row.result.elapsed_ms + 'ms' : (row.status === 'running' ? '计时中' : '—') }}
                </span>
              </template>
            </el-table-column>

            <el-table-column label="操作" width="85" align="center" fixed="right">
              <template #default="{ row }">
                <el-button size="small" text type="primary" @click="openOaItemLog(row)">
                  <el-icon><Document /></el-icon>日志
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </div>

      <template #footer>
        <div class="oa-dialog-footer">
          <div class="footer-tip">
            <span v-if="oaRunning" class="running-indicator">
              <span class="pulse-dot"></span> 正在并发检测 (Workers: {{ oaForm.workers }})...
            </span>
            <span v-else-if="oaTaskId" class="finished-indicator">
              检测完毕，结果已自动保存至数据库
            </span>
          </div>
          <div class="footer-btns">
            <el-button @click="closeOA">关闭</el-button>
            <el-button v-if="oaRunning" type="danger" plain @click="stopOA">
              <el-icon><SwitchButton /></el-icon>停止检测
            </el-button>
            <el-button v-else type="primary" class="start-gradient-btn" :loading="oaRunning" @click="startOA">
              <el-icon><VideoPlay /></el-icon>{{ oaTaskId ? '重新检测' : '开始检测' }}
            </el-button>
          </div>
        </div>
      </template>
    </el-dialog>

    <!-- ──────────────── 单账号 OA 详细日志终端弹窗 ──────────────── -->
    <el-dialog
      v-model="oaLogModalVisible"
      width="780px"
      top="8vh"
      class="macos-terminal-dialog"
      :close-on-click-modal="false"
    >
      <template #header>
        <div class="modal-header">
          <div class="window-dots">
            <span class="dot red"></span>
            <span class="dot yellow"></span>
            <span class="dot green"></span>
          </div>
          <div class="modal-title-info">
            <span class="modal-email mono" @click="currentOaLogItem?.email && copyText(currentOaLogItem.email, '邮箱已复制')" title="点击复制邮箱">{{ currentOaLogItem?.email }}</span>
            <span class="modal-run-tag">
              OAICS 检测日志
            </span>
          </div>
        </div>
      </template>

      <div class="modal-terminal-wrap">
        <div ref="oaModalLogBoxRef" class="modal-terminal-body" @scroll="onOaLogScroll">
          <div
            v-for="(line, idx) in oaLogLines"
            :key="idx"
            class="terminal-line"
            :class="getLogClass(line)"
          >
            <span v-if="formatTerminalLine(line).time" class="terminal-time mono">{{ formatTerminalLine(line).time }}</span>
            <span class="terminal-text">{{ formatTerminalLine(line).text }}</span>
          </div>
          <div v-if="!oaLogLines.length" class="terminal-empty">
            {{ oaLogLoading ? '正在加载日志...' : '暂无详细日志' }}
          </div>
        </div>
        <transition name="fade">
          <button
            v-if="oaUserScrolledUp"
            class="terminal-scroll-bottom-pill"
            title="检测到您正在查看历史日志，点击快速回到底部"
            @click="scrollToBottomOaLog"
          >
            <el-icon><ArrowDown /></el-icon>
            <span>回到底部</span>
          </button>
        </transition>
      </div>

      <template #footer>
        <div class="modal-footer">
          <span class="log-count-tip">共 {{ oaLogLines.length }} 行日志</span>
          <div class="modal-footer-btns">
            <el-button size="small" @click="copyText(oaLogLines.join('\n'))">
              <el-icon><CopyDocument /></el-icon>复制全部日志
            </el-button>
            <el-button size="small" type="primary" @click="oaLogModalVisible = false">
              关闭
            </el-button>
          </div>
        </div>
      </template>
    </el-dialog>

    <!-- ──────────────── OAuth 导出与凭证生成控制台弹窗 ──────────────── -->
    <el-dialog
      v-model="oauthVisible" width="900px" top="3vh"
      class="oa-custom-dialog plus-dialog oauth-dialog oauth-modern-modal"
      append-to-body
      :lock-scroll="false"
      :close-on-click-modal="false" @opened="layoutOAuthTable" @closed="closeOAuthExport"
    >
      <template #header>
        <div class="oa-header">
          <div class="oa-header-title">
            <span class="oa-title-badge">CODEX OAUTH</span>
            <span class="oa-title-text">Codex OAuth 导出与智能接码授权</span>
            <span class="oa-target-pill">{{ oauthTargetEmails.length }} 个账号</span>
          </div>
          <div class="oa-header-extra">
            <button class="oa-config-toggle-btn" @click="oauthConfigCollapsed = !oauthConfigCollapsed">
              <el-icon><Setting /></el-icon>
              <span>{{ oauthConfigCollapsed ? '展开参数配置' : '收起配置' }}</span>
            </button>
          </div>
        </div>
      </template>

      <div class="oa-dialog-container">
        <!-- 参数配置卡片 (Tab 选项卡折叠卡片) -->
        <el-collapse-transition>
          <div v-show="!oauthConfigCollapsed" class="oa-config-card">
            <!-- 手机号接码策略全局快捷切换卡片 (Hero Strategy Selector) -->
            <div class="oa-strategy-hero-card" :class="'is-' + oauthForm.smsStrategy + '-mode'">
              <div class="strategy-left-control">
                <span class="strategy-label">手机号策略:</span>
                <el-radio-group
                  v-model="oauthForm.smsStrategy"
                  size="small"
                  class="strategy-radio-group"
                  :disabled="oauthRunning"
                  @change="onOAuthStrategyChange"
                >
                  <el-radio-button value="skip">
                    <span class="strategy-btn-content">⏩ 跳过</span>
                  </el-radio-button>
                  <el-radio-button
                    v-for="p in smsProviders"
                    :key="p.kind"
                    :value="p.kind"
                  >
                    <span class="strategy-btn-content">{{ p.display_name }}</span>
                  </el-radio-button>
                </el-radio-group>
              </div>
              <div class="strategy-right-meta">
                <div v-if="oauthForm.smsStrategy === 'skip'" class="strategy-tip text-emerald">
                  <span class="strategy-dot emerald"></span>
                  <span>遇到手机号风控（add-phone）安全跳过并标记「需接码」，零费用消耗</span>
                </div>
                <div v-else class="strategy-tip" :class="oauthSmsMeta?.uses_cdk_pool ? 'text-amber' : 'text-blue'">
                  <span class="strategy-dot" :class="oauthSmsMeta?.uses_cdk_pool ? 'amber' : 'blue'"></span>
                  <span>{{ oauthSmsMeta?.description || '遇到手机号验证时自动调用所选接码渠道租号收码' }}</span>
                </div>
              </div>
            </div>

            <el-tabs v-model="oauthActiveTab" class="oa-config-tabs">
              <!-- Tab 1: 网络与代理 -->
              <el-tab-pane label="🌐 网络代理 & 并发" name="network">
                <el-form label-position="top" :disabled="oauthRunning" size="small" class="oa-tab-form">
                  <el-row :gutter="12">
                    <el-col :xs="24" :sm="12" :md="8">
                      <el-form-item label="网络代理设置">
                        <el-select
                          v-model="oauthForm.proxy" filterable clearable allow-create default-first-option
                          :reserve-keyword="false" placeholder="选择或手动输入代理" style="width: 100%"
                        >
                          <el-option
                            v-if="proxyList.length"
                            label="🌐 全局代理池轮询 (自动多Worker分配)"
                            value="__POOL__"
                          />
                          <el-option v-for="p in proxyList" :key="p" :label="p" :value="p" />
                        </el-select>
                      </el-form-item>
                    </el-col>
                    <el-col :xs="24" :sm="12" :md="8">
                      <el-form-item label="代理目标国家">
                        <el-select
                          v-model="oauthForm.proxyCountry" filterable allow-create
                          placeholder="选择目标国家" style="width: 100%"
                        >
                          <el-option
                            v-for="c in COUNTRY_OPTIONS" :key="c.value"
                            :label="c.label" :value="c.value"
                          />
                        </el-select>
                      </el-form-item>
                    </el-col>
                    <el-col :xs="12" :sm="6" :md="4">
                      <el-form-item label="并发 Worker">
                        <el-input-number v-model="oauthForm.workers" :min="1" :max="20" style="width: 100%" />
                      </el-form-item>
                    </el-col>
                    <el-col :xs="12" :sm="6" :md="4">
                      <el-form-item label="单请求超时 (秒)">
                        <el-input-number v-model="oauthForm.timeout" :min="10" :max="120" style="width: 100%" />
                      </el-form-item>
                    </el-col>
                  </el-row>
                </el-form>
              </el-tab-pane>

              <!-- Tab 2: 短信接码设置 -->
              <el-tab-pane
                :label="oauthForm.smsStrategy === 'skip' ? '📱 手机号接码策略' : ('📱 ' + (oauthSmsMeta?.display_name || '接码') + ' 参数')"
                name="sms"
              >
                <el-form label-position="top" :disabled="oauthRunning" size="small" class="oa-tab-form">
                  <!-- A. CDK 卡密模式专属配置 -->
                  <div v-if="oauthSmsMeta?.uses_cdk_pool" class="oa-cdk-config-panel">
                    <div class="cdk-pool-mini-bar" :class="{ 'is-empty': oauthCdkStats.available === 0 }">
                      <div class="pool-bar-left">
                        <el-icon class="pool-bar-icon text-amber"><Ticket /></el-icon>
                        <span class="pool-bar-label">当前 CDK 号池状态：</span>
                        <el-tag size="small" :type="oauthCdkStats.available > 0 ? 'success' : 'danger'" effect="dark">
                          就绪可用 {{ oauthCdkStats.available }} 张
                        </el-tag>
                        <span class="pool-bar-sub">（号池总收纳 {{ oauthCdkStats.total }} 张 · 累计接码 {{ oauthCdkStats.total_success_codes }} 次）</span>
                      </div>
                      <div class="pool-bar-right">
                        <router-link to="/sms" target="_blank" class="pool-link-btn">
                          前往管理 CDK 号池 →
                        </router-link>
                      </div>
                    </div>

                    <el-alert
                      v-if="oauthCdkStats.available === 0 && !oauthForm.smsApiKey"
                      type="warning"
                      show-icon
                      :closable="false"
                      style="margin-bottom: 10px;"
                      title="⚠️ 当前号池中可用卡密为 0 张！请前往【接码设置】批量导入新卡密，或在下方输入临时卡密。"
                    />

                    <el-row :gutter="12">
                      <el-col :xs="24" :sm="14">
                        <el-form-item label="临时卡密兑换码 (留空自动走号池调度)">
                          <el-input
                            v-model="oauthForm.smsApiKey"
                            placeholder="留空自动从 CDK 号池智能提取可用卡密 (推荐)，亦可填临时卡密"
                            clearable
                            :prefix-icon="Ticket"
                          />
                        </el-form-item>
                      </el-col>
                      <el-col :xs="12" :sm="5">
                        <el-form-item>
                          <template #label>
                            <span>最多换号次数</span>
                            <el-tooltip content="本轮绑手机最多换几个号，按你填的次数跑满。被 OpenAI 拒绝也会继续换到次数用完。" placement="top">
                              <el-icon class="info-ico" style="margin-left: 3px;"><QuestionFilled /></el-icon>
                            </el-tooltip>
                          </template>
                          <el-input-number v-model="oauthForm.smsMaxAttempts" :min="1" :max="10" style="width: 100%" />
                        </el-form-item>
                      </el-col>
                      <el-col :xs="12" :sm="5">
                        <el-form-item label="收码等待超时 (秒)">
                          <template #label>
                            <span>收码等待超时 (秒)</span>
                            <el-tooltip :content="oauthSmsMeta?.timeout_hint || 'CDK 推荐 30~35 秒。超过 45 秒容易导致 OpenAI 授权会话过期。'" placement="top">
                              <el-icon class="info-ico" style="margin-left: 3px;"><QuestionFilled /></el-icon>
                            </el-tooltip>
                          </template>
                          <el-input-number v-model="oauthForm.smsTimeout" :min="15" :max="90" :step="5" style="width: 100%" />
                        </el-form-item>
                      </el-col>
                    </el-row>

                    <div class="oa-cdk-feature-tips">
                      <div class="feature-tip-item">
                        <span class="tip-title">🇬🇧 英国 OpenAI 专属号</span>
                        <span class="tip-desc">由平台自动分配英国 44 线路 OpenAI 专属号，无需手动选择国家与金额。</span>
                      </div>
                      <div class="feature-tip-item">
                        <span class="tip-title">🔄 被风控自动换新号</span>
                        <span class="tip-desc">遇 OpenAI 提示被注册或拒绝，自动调用 change-number 更换新号码 (免费换号20次)。</span>
                      </div>
                      <div class="feature-tip-item">
                        <span class="tip-title">🔁 多次卡支持长期复用</span>
                        <span class="tip-desc">成功接码后只累加次数保持可用，绝不提前作废，支持多个账号循环利用。</span>
                      </div>
                    </div>
                  </div>

                  <!-- B. 常规短信平台（国家 / 金额 / 线路） -->
                  <div v-else-if="oauthSmsMeta?.uses_country">
                    <el-row :gutter="12">
                      <el-col :xs="24" :sm="12" :md="8">
                        <el-form-item label="接码国家 (可搜索)">
                          <el-select
                            v-model="oauthForm.smsCountry"
                            filterable
                            :allow-create="oauthSmsMeta?.country_scheme !== 'iso2'"
                            @visible-change="(open) => open && loadSmsCountries()"
                            default-first-option
                            :loading="smsCountriesLoading"
                            :placeholder="oauthSmsMeta?.country_scheme === 'iso2' ? '搜索国家名，如 斯洛文尼亚' : '搜索国家名或输入ID'"
                            style="width: 100%"
                            @change="onOAuthSmsCountryChange"
                          >
                            <el-option v-for="sc in SMS_COUNTRY_OPTIONS" :key="sc.value" :label="sc.label" :value="sc.value">
                              <div class="country-option-item">
                                <span>{{ sc.label }}</span>
                                <el-tag v-if="sc.safe" size="small" type="success" effect="plain" class="safe-badge">免WhatsApp</el-tag>
                              </div>
                            </el-option>
                          </el-select>
                        </el-form-item>
                      </el-col>
                      <el-col :xs="12" :sm="6" :md="5">
                        <el-form-item :label="oauthSmsMeta?.country_scheme === 'iso2' ? '最高限价 / 点选档位即锁定' : '接码金额要求 (如 0.008 或区间)'">
                          <el-input
                            v-model="oauthForm.smsMaxPrice"
                            :placeholder="oauthSmsMeta?.country_scheme === 'iso2' ? '点下方档位，或手填上限如 0.05' : '如 0.008 或 0.007-0.01'"
                            clearable
                            :prefix-icon="Money"
                          />
                        </el-form-item>
                      </el-col>
                      <el-col v-if="oauthSmsMeta?.uses_provider_ids" :xs="12" :sm="6" :md="5">
                        <el-form-item label="指定供应商 ID (下拉直选)">
                          <el-select
                            v-model="oauthForm.smsProviderIds"
                            filterable
                            allow-create
                            clearable
                            placeholder="选择或输入线路ID"
                            style="width: 100%"
                            @change="(val) => {
                              const found = oauthPriceTiers.find((x) => x.id === val)
                              if (found && found.price_str) oauthForm.smsMaxPrice = found.price_str
                            }"
                          >
                            <el-option
                              v-for="t in oauthPriceTiers"
                              :key="t.id"
                              :label="t.label"
                              :value="t.id"
                            />
                          </el-select>
                        </el-form-item>
                      </el-col>

                      <!-- 号池实时档位直选区：仅 SmsBower / HeroSMS 有供应商线路 -->
                      <el-col v-if="oauthSmsMeta?.uses_price_tiers" :span="24">
                        <div class="oa-tier-chips-block">
                          <span class="oa-tier-title">
                            <el-icon><Discount /></el-icon>
                            {{ oauthSmsMeta?.country_scheme === 'iso2' ? '官网同款价格档位（点选锁定，和 vak-sms 网页一致）' : 'OpenAI 接码档位 dr' }}（{{ oauthForm.smsCountry || '未选国家' }} · 点击锁定）
                            <el-button
                              link
                              type="primary"
                              size="small"
                              :loading="oauthPriceTiersLoading"
                              @click="loadOAuthPriceTiers"
                            >刷新</el-button>
                          </span>
                          <div v-if="oauthPriceTiersLoading" class="oa-tier-empty">正在拉取该国实时价格与库存...</div>
                          <div v-else-if="oauthPriceTiers.length" class="oa-tier-chips">
                            <div
                              v-for="t in oauthPriceTiers"
                              :key="t.id || t.price_str"
                              class="oa-tier-pill"
                              :class="{ 'is-active': oauthSmsMeta?.uses_provider_ids ? oauthForm.smsProviderIds === t.id : (oauthForm.smsMaxPrice === t.price_str || oauthForm.smsMaxPrice === t.price_key) }"
                              @click="() => { oauthForm.smsMaxPrice = t.price_key || t.price_str; if (oauthSmsMeta?.uses_provider_ids && t.id) oauthForm.smsProviderIds = t.id }"
                            >
                              <span>{{ t.label }}</span>
                              <el-icon v-if="oauthSmsMeta?.uses_provider_ids ? oauthForm.smsProviderIds === t.id : (oauthForm.smsMaxPrice === t.price_str || oauthForm.smsMaxPrice === t.price_key)" class="oa-check-icon">
                                <CircleCheckFilled />
                              </el-icon>
                            </div>
                          </div>
                          <div v-else class="oa-tier-empty">
                            {{ oauthSmsMeta?.country_scheme === 'iso2' ? '该国暂无 openai.com 多档报价，可手填最高限价或换国家。' : '该国暂无 OpenAI（dr）库存。不会展示其它业务号源。可点刷新或换国家。' }}
                          </div>
                        </div>
                      </el-col>

                      <el-col v-if="oauthSmsMeta?.uses_provider_ids" :xs="24" :sm="12" :md="8">
                        <el-form-item label="排除供应商 ID (多选拉黑)">
                          <el-select
                            v-model="oauthForm.smsExceptProviderIds"
                            multiple
                            filterable
                            allow-create
                            clearable
                            collapse-tags
                            collapse-tags-tooltip
                            placeholder="可多选排除，如 3327、1170"
                            style="width: 100%"
                          >
                            <el-option
                              v-for="t in oauthPriceTiers"
                              :key="t.id"
                              :label="t.label"
                              :value="t.id"
                            />
                          </el-select>
                        </el-form-item>
                      </el-col>
                      <el-col :xs="24" :sm="12" :md="8">
                        <el-form-item label="接码 API Key (留空读取全局配置)">
                          <el-input v-model="oauthForm.smsApiKey" type="password" show-password placeholder="留空自动使用系统接码配置" clearable :prefix-icon="Lock" />
                        </el-form-item>
                      </el-col>
                      <el-col :xs="12" :sm="6" :md="4">
                        <el-form-item>
                          <template #label>
                            <span>最多换号次数</span>
                            <el-tooltip content="本轮绑手机最多换几个号，按你填的次数跑满。被 OpenAI 拒绝也会继续换到次数用完。" placement="top">
                              <el-icon class="info-ico" style="margin-left: 3px;"><QuestionFilled /></el-icon>
                            </el-tooltip>
                          </template>
                          <el-input-number v-model="oauthForm.smsMaxAttempts" :min="1" :max="10" style="width: 100%" />
                        </el-form-item>
                      </el-col>
                      <el-col :xs="12" :sm="6" :md="4">
                        <el-form-item>
                          <template #label>
                            <span>收码等待超时 (秒)</span>
                            <el-tooltip :content="oauthSmsMeta?.timeout_hint || '推荐 60~85 秒。超过 90 秒容易导致 OpenAI 授权会话过期。'" placement="top">
                              <el-icon class="info-ico" style="margin-left: 3px;"><QuestionFilled /></el-icon>
                            </el-tooltip>
                          </template>
                          <el-input-number v-model="oauthForm.smsTimeout" :min="20" :max="120" :step="5" style="width: 100%" />
                        </el-form-item>
                      </el-col>

                      <el-col :span="24">
                        <div class="sms-guide-box">
                          <div class="sms-guide-title">
                            <el-icon><InfoFilled /></el-icon> 怎么填才和网页点选一样（规则速查）
                          </div>
                          <div v-if="oauthSmsMeta?.country_scheme === 'iso2'" class="sms-guide-chips">
                            <span class="sms-rule-chip"><b>点档位</b> = 锁定该档（maxPrice+fixedPrice，不会拿到更便宜的）</span>
                            <span class="sms-rule-chip"><b>手填 &lt;=0.05</b> = 只限制最高价，仍可能出更便宜的号</span>
                            <span class="sms-rule-chip"><b>留空</b> = 不限价，平台按默认价出号</span>
                          </div>
                          <div v-else class="sms-guide-chips">
                            <span class="sms-rule-chip"><b>选 0.008</b> = 锁定该档，绝不拿更便宜的 0.007</span>
                            <span class="sms-rule-chip"><b>填 0.007-0.008</b> = 允许两档区间</span>
                            <span class="sms-rule-chip"><b>坏线自动剔除</b> = 线路 BANNED 自动去参数继续按金额租号</span>
                          </div>
                        </div>
                      </el-col>
                    </el-row>
                  </div>

                  <!-- C. 极速跳过模式 -->
                  <div v-else class="oa-skip-mode-panel">
                    <el-icon class="skip-icon"><CircleCheckFilled /></el-icon>
                    <div class="skip-text">
                      当前处于 <b>极速跳过接码模式</b>。OpenAI 遇到需手机号验证（add-phone）时将<b>直接安全跳过</b>并标记为「需接码」，不会产生任何接码扣费。如需自动接码推进，请在上方切换到任一接码渠道。
                    </div>
                  </div>
                </el-form>
              </el-tab-pane>
            </el-tabs>

            <div class="oa-config-footer-row">
              <span v-if="oauthSmsMeta?.country_scheme === 'iso2'" class="oa-config-hint">💡 提示：点档位会按官网方式锁定该价（maxPrice + fixedPrice=true）。手填数字默认也是锁档；只要上限请填 &lt;=0.05。</span>
              <span v-else-if="oauthSmsMeta?.uses_price_tiers" class="oa-config-hint">💡 提示：点选档位即锁定该价格（选 <code>0.008</code> 绝不拿 0.007）。若该档位无货会报 NO_NUMBERS，不会擅自换号。</span>
              <span v-else-if="oauthSmsMeta?.uses_cdk_pool" class="oa-config-hint">💡 提示：卡密留空则自动从 CDK 号池调度。被拒会自动换号，多次卡不会提前作废。</span>
              <span v-else class="oa-config-hint">💡 提示：跳过接码不会产生费用。需要自动推进时，在上方切换到任一接码渠道即可。</span>
              <el-button size="small" class="oa-save-default-btn" @click="saveOAuthFormDefault">
                <el-icon><Check /></el-icon> 保存为默认配置
              </el-button>
            </div>
          </div>
        </el-collapse-transition>

        <div v-if="oauthBatchHint" class="oauth-batch-hint">{{ oauthBatchHint }}</div>

        <!-- KPI 统计看板 (4列等宽均匀排布，顶栏完美对齐) -->
        <div class="plus-kpi-grid oa-kpi-grid">
          <div
            class="plus-kpi-card oa-kpi-card clickable-card"
            :class="{ 'is-filter-active': oauthFilter === 'all' }"
            title="查看全部账号"
            @click="setOAuthFilter('all')"
          >
            <span class="kpi-label">已处理 / 总数</span>
            <span class="kpi-num">{{ oauthStats.done }} / {{ oauthStats.total }}</span>
          </div>
          <div
            class="plus-kpi-card oa-kpi-card clickable-card"
            :class="{ 'is-filter-active': oauthFilter === 'success' }"
            title="只看授权成功"
            @click="setOAuthFilter('success')"
          >
            <span class="kpi-label">OAuth 成功</span>
            <span class="kpi-num text-success">{{ oauthStats.success }}</span>
          </div>
          <div
            class="plus-kpi-card oa-kpi-card clickable-card"
            :class="{ 'card-warn': oauthStats.need_phone > 0, 'is-filter-active': oauthFilter === 'phone' }"
            title="只看需接码"
            @click="setOAuthFilter('phone')"
          >
            <span class="kpi-label">需手机接码</span>
            <span class="kpi-num text-warning">{{ oauthStats.need_phone }}</span>
          </div>
          <div
            class="plus-kpi-card oa-kpi-card clickable-card"
            :class="{ 'card-danger': oauthStats.error > 0, 'is-filter-active': oauthFilter === 'fail' }"
            :title="failedOAuthEmails.length > 0 ? `点击筛选失败账号，可再点「批量重新授权」` : '只看失败'"
            @click="setOAuthFilter('fail')"
          >
            <div style="display: flex; justify-content: space-between; align-items: center">
              <span class="kpi-label">❌ 失败 / 异常</span>
              <span
                v-if="failedOAuthEmails.length > 0 && !oauthRunning"
                class="oa-retry-tag"
                style="cursor: pointer"
                @click.stop="retryOAuthExportRunner()"
              >
                重试全部
              </span>
            </div>
            <span class="kpi-num text-danger">{{ oauthStats.error }}</span>
          </div>
        </div>

        <!-- 进度条 -->
        <div class="oa-progress-wrap">
          <el-progress
            :percentage="oauthStats.percent"
            :status="oauthStats.percent === 100 ? 'success' : ''"
            :stroke-width="6"
            :show-text="false"
          />
        </div>

        <!-- 核心表格：分页 + 状态筛选，避免几百行一起渲染卡顿 -->
        <div class="health-table-filter-bar oauth-table-filter-bar">
          <el-radio-group v-model="oauthFilter" size="small" class="health-filter-radio oa-filter-radio" @change="oauthPage = 1">
            <el-radio-button value="all">全部 ({{ oauthStats.total }})</el-radio-button>
            <el-radio-button value="running">进行中 ({{ oauthStats.running }})</el-radio-button>
            <el-radio-button value="pending">排队 ({{ oauthStats.pending }})</el-radio-button>
            <el-radio-button value="success">成功 ({{ oauthStats.success }})</el-radio-button>
            <el-radio-button value="fail">
              <span :class="{ 'text-danger': oauthStats.error > 0 }">失败 ({{ oauthStats.error }})</span>
            </el-radio-button>
            <el-radio-button value="banned">
              <span :class="{ 'text-danger': oauthStats.banned > 0 }">封号 ({{ oauthStats.banned }})</span>
            </el-radio-button>
            <el-radio-button value="phone">需接码 ({{ oauthStats.need_phone }})</el-radio-button>
          </el-radio-group>
          <div class="health-filter-right">
            <el-input
              v-model="oauthSearch"
              placeholder="过滤邮箱..."
              clearable
              size="small"
              class="health-search-input oa-search-input"
              :prefix-icon="Search"
              @input="oauthPage = 1"
            />
          </div>
        </div>

        <div class="plus-table-wrap oauth-table-wrap">
          <el-table
            ref="oauthTableRef"
            :data="oauthDisplayRows"
            row-key="email"
            size="small"
            stripe
            :max-height="oauthConfigCollapsed ? 380 : 240"
            class="plus-table"
            :highlight-current-row="false"
          >
            <el-table-column prop="email" label="账号" min-width="200" show-overflow-tooltip>
              <template #default="{ row }">
                <div class="oa-email-line" title="点击复制邮箱" @click.stop="copyText(row.email, '已复制')">
                  <span class="provider-avatar-badge" :style="{ background: (row._providerMeta || getEmailProviderMeta(row.email)).bg, color: (row._providerMeta || getEmailProviderMeta(row.email)).color }">
                    {{ (row._providerMeta || getEmailProviderMeta(row.email)).icon }}
                  </span>
                  <span class="oa-email-text mono">{{ row.email }}</span>
                  <el-icon class="oa-copy-ico"><CopyDocument /></el-icon>
                </div>
              </template>
            </el-table-column>

            <el-table-column label="实时进度 / 状态" min-width="210" align="left">
              <template #default="{ row }">
                <span v-if="row.status === 'running'" class="oa-status-badge is-running">
                  <span class="spin-dot"></span> {{ row.step_text || '[1/6] 建立会话...' }}
                </span>
                <span v-else-if="row.status === 'pending'" class="oa-status-badge is-pending">
                  {{ row.step_text || '待处理' }}
                </span>
                <span v-else-if="row.result?.status === 'success'" class="oa-status-badge is-success">
                  ✅ {{ row.result?.label || '成功' }}
                </span>
                <span v-else-if="row.result?.status === 'need_phone'" class="oa-status-badge is-warning">
                  📱 需接码 (已跳过)
                </span>
                <span v-else-if="row.result?.status === 'banned' || row.result?.status === 'deactivated'" class="oa-status-badge is-danger" :title="row.result?.error || ''">
                  🚫 {{ row.result?.label || '封号' }}
                </span>
                <span v-else class="oa-status-badge is-danger" :title="row.result?.error || ''">
                  ❌ {{ row.result?.label || '失败' }}
                </span>
              </template>
            </el-table-column>

            <el-table-column label="Refresh Token" width="130" align="center">
              <template #default="{ row }">
                <span v-if="row.result?.refresh_token_len" class="mono" style="color: var(--el-color-success); font-weight: 600">
                  有 ({{ row.result.refresh_token_len }}c)
                </span>
                <span v-else-if="row.status === 'running'" class="hint">获取中...</span>
                <span v-else class="hint">—</span>
              </template>
            </el-table-column>

            <el-table-column label="耗时" width="85" align="right">
              <template #default="{ row }">
                <ElapsedTimer :status="row.status" :started-at="row.started_at" :elapsed="row.elapsed" />
              </template>
            </el-table-column>

            <el-table-column label="操作" width="180" align="center" fixed="right">
              <template #default="{ row }">
                <div class="oa-row-actions">
                  <button class="oa-micro-btn" @click="openOAuthItemLog(row)">
                    日志
                  </button>
                  <button
                    v-if="row.result && row.result.status !== 'success'"
                    class="oa-micro-btn btn-retry"
                    :disabled="row.status === 'running'"
                    @click="retryOAuthExportRunner([row.email])"
                    title="对此失败账号重新发起授权"
                  >
                    <el-icon><Refresh /></el-icon>重试
                  </button>
                  <button
                    v-if="row.result?.status === 'success' || row.result?.refresh_token_len"
                    class="oa-micro-btn btn-dl"
                    @click="downloadSingleOAuthJson(row.email)"
                  >
                    <el-icon><Download /></el-icon>JSON
                  </button>
                </div>
              </template>
            </el-table-column>
            <template #empty>
              <div class="oauth-table-empty">
                {{ oauthStats.total ? '当前筛选没有账号，可切回「全部」或清空搜索' : '还没有待导出账号' }}
              </div>
            </template>
          </el-table>
        </div>

        <div class="health-pagination-bar">
          <span class="health-page-count-tip text-muted text-xs">
            显示第 {{ oauthFilteredRows.length > 0 ? (oauthPage - 1) * oauthPageSize + 1 : 0 }}
            - {{ Math.min(oauthPage * oauthPageSize, oauthFilteredRows.length) }} 条
            · 过滤 <b>{{ oauthFilteredRows.length }}</b> / 共 {{ oauthStats.total }}
          </span>
          <el-pagination
            v-model:current-page="oauthPage"
            v-model:page-size="oauthPageSize"
            :page-sizes="[30, 50, 100, 200]"
            :total="oauthFilteredRows.length"
            layout="sizes, prev, pager, next"
            size="small"
          />
        </div>
      </div>

      <template #footer>
        <div class="oa-footer">
          <div class="footer-left">
            <button
              class="oa-footer-btn btn-export-cpa"
              :disabled="oauthStats.success === 0 && !oauthTargetEmails.length"
              @click="downloadCpaJson"
            >
              <el-icon><Download /></el-icon>下载 CPA JSON ({{ oauthStats.success || oauthTargetEmails.length }})
            </button>
            <button
              class="oa-footer-btn btn-export-sub2"
              :disabled="oauthStats.success === 0 && !oauthTargetEmails.length"
              @click="downloadSub2Json"
            >
              <el-icon><Download /></el-icon>下载 SUB2 JSON ({{ oauthStats.success || oauthTargetEmails.length }})
            </button>
          </div>
          <div class="footer-right">
            <button
              v-if="failedOAuthEmails.length > 0"
              class="oa-footer-btn btn-warn"
              :disabled="oauthRunning"
              @click="retryOAuthExportRunner()"
              title="一键将所有失败/需接码账号重新加入队列执行授权"
            >
              <el-icon><Refresh /></el-icon>批量重试失败 ({{ failedOAuthEmails.length }})
            </button>
            <button class="oa-footer-btn btn-close" @click="closeOAuthExport">关闭</button>
            <button
              v-if="oauthRunning"
              class="oa-footer-btn btn-stop"
              @click="stopOAuthExportTask"
            >
              <el-icon><SwitchButton /></el-icon>停止任务
            </button>
            <button
              v-else
              class="oa-footer-btn btn-primary"
              :disabled="oauthRunning"
              @click="startOAuthExportTask"
            >
              <el-icon><VideoPlay /></el-icon>{{ oauthTaskId ? '重新执行' : '开始导出' }}
            </button>
          </div>
        </div>
      </template>
    </el-dialog>

    <!-- ──────────────── 授权特征 / 成功率看板 ──────────────── -->
    <el-dialog
      v-model="featVisible"
      width="1080px"
      top="3vh"
      class="oa-custom-dialog plus-dialog feat-dialog"
      modal-class="feat-overlay"
      :close-on-click-modal="false"
      :show-close="true"
      append-to-body
    >
      <template #header>
        <div class="oa-header">
          <div class="oa-header-title">
            <span class="oa-title-badge feat-badge">特征</span>
            <span class="oa-title-text">OAuth 授权特征与成功率</span>
            <el-tag size="small" type="info" round effect="plain">成功失败都记 · 给后续加权选路用</el-tag>
          </div>
          <div class="oa-header-extra">
            <el-button size="small" text :loading="featLoading" @click="loadFeatBoard">
              <el-icon><Refresh /></el-icon>刷新
            </el-button>
          </div>
        </div>
      </template>

      <div class="feat-board" v-loading="featLoading">
        <div class="feat-kpi-row">
          <div v-for="k in featKpis" :key="k.label" class="feat-kpi" :class="'tone-' + k.tone">
            <div class="feat-kpi-label">{{ k.label }}</div>
            <div class="feat-kpi-value">{{ k.value }}</div>
          </div>
        </div>

        <div class="feat-grid">
          <section class="feat-card">
            <div class="feat-card-head">代理国成功率</div>
            <div v-if="featProxyBars.length" class="feat-bars">
              <div v-for="b in featProxyBars" :key="'p-' + b.key" class="feat-bar">
                <div class="feat-bar-meta">
                  <span class="feat-bar-key">{{ b.key }}</span>
                  <span class="feat-bar-num">{{ featPct(b.rate) }} · {{ b.ok }}/{{ b.n }}</span>
                </div>
                <div class="feat-bar-track">
                  <i :style="{ width: featPct(b.rate) }"></i>
                </div>
              </div>
            </div>
            <div v-else class="feat-empty">暂无样本</div>
          </section>

          <section class="feat-card">
            <div class="feat-card-head">指纹成功率</div>
            <div v-if="featFpBars.length" class="feat-bars">
              <div v-for="b in featFpBars" :key="'f-' + b.key" class="feat-bar">
                <div class="feat-bar-meta">
                  <span class="feat-bar-key">{{ b.key }}</span>
                  <span class="feat-bar-num">{{ featPct(b.rate) }} · {{ b.ok }}/{{ b.n }}</span>
                </div>
                <div class="feat-bar-track">
                  <i :style="{ width: featPct(b.rate) }"></i>
                </div>
              </div>
            </div>
            <div v-else class="feat-empty">暂无样本</div>
          </section>

          <section class="feat-card">
            <div class="feat-card-head">接码国家成功率</div>
            <div v-if="featSmsBars.length" class="feat-bars">
              <div v-for="b in featSmsBars" :key="'s-' + b.key" class="feat-bar">
                <div class="feat-bar-meta">
                  <span class="feat-bar-key">{{ b.key }}</span>
                  <span class="feat-bar-num">{{ featPct(b.rate) }} · {{ b.ok }}/{{ b.n }}</span>
                </div>
                <div class="feat-bar-track">
                  <i :style="{ width: featPct(b.rate) }"></i>
                </div>
              </div>
            </div>
            <div v-else class="feat-empty">暂无样本</div>
          </section>

          <section class="feat-card">
            <div class="feat-card-head">登录路径成功率</div>
            <div v-if="featPathBars.length" class="feat-bars">
              <div v-for="b in featPathBars" :key="'l-' + b.key" class="feat-bar">
                <div class="feat-bar-meta">
                  <span class="feat-bar-key">{{ b.key }}</span>
                  <span class="feat-bar-num">{{ featPct(b.rate) }} · {{ b.ok }}/{{ b.n }}</span>
                </div>
                <div class="feat-bar-track">
                  <i :style="{ width: featPct(b.rate) }"></i>
                </div>
              </div>
            </div>
            <div v-else class="feat-empty">暂无样本</div>
          </section>
        </div>

        <section class="feat-recent">
          <div class="feat-recent-head">
            <span>最近 8 次尝试</span>
            <el-radio-group :model-value="featOutcome" size="small" @change="onFeatOutcome">
              <el-radio-button value="">全部</el-radio-button>
              <el-radio-button value="success">成功</el-radio-button>
              <el-radio-button value="error">异常</el-radio-button>
              <el-radio-button value="need_phone">需接码</el-radio-button>
            </el-radio-group>
          </div>
          <div v-if="featRecent.length" class="feat-recent-list">
            <div v-for="r in featRecent" :key="r.id" class="feat-recent-row">
              <span class="feat-chip" :class="'tone-' + featOutcomeMeta(r.outcome).tone">{{ featOutcomeMeta(r.outcome).label }}</span>
              <span class="feat-mail" :title="r.email">{{ r.email }}</span>
              <span class="feat-cell">{{ r.proxy_country || '—' }}</span>
              <span class="feat-cell">{{ r.impersonate || '—' }}</span>
              <span class="feat-cell">{{ r.sms_country || '—' }}{{ r.sms_operator ? ' / ' + r.sms_operator : '' }}</span>
              <span class="feat-cell feat-err">{{ featErrorLabel(r.error_class) }}</span>
              <span class="feat-time">{{ featWhen(r.created_at) }}</span>
            </div>
          </div>
          <div v-else class="feat-empty feat-empty-wide">
            还没有样本。重启后端后跑一次授权，成功失败都会出现在这里。
          </div>
        </section>
      </div>
    </el-dialog>

    <!-- ──────────────── 单账号 OAuth 详细日志弹窗 ──────────────── -->
    <el-dialog
      v-model="oauthLogModalVisible"
      width="780px"
      top="8vh"
      class="macos-terminal-dialog"
      append-to-body
      :close-on-click-modal="false"
    >
      <template #header>
        <div class="modal-header">
          <div class="window-dots">
            <span class="dot red"></span>
            <span class="dot yellow"></span>
            <span class="dot green"></span>
          </div>
          <div class="modal-title-info">
            <span class="modal-email mono" @click="currentOAuthLogItem?.email && copyText(currentOAuthLogItem.email, '邮箱已复制')" title="点击复制邮箱">{{ currentOAuthLogItem?.email }}</span>
            <span class="modal-run-tag">
              OAuth 导出日志
            </span>
          </div>
        </div>
      </template>

      <div class="modal-terminal-wrap">
        <div ref="oauthModalLogBoxRef" class="modal-terminal-body" @scroll="onOAuthLogScroll">
          <div
            v-for="row in oauthLogView"
            :key="row.idx"
            class="terminal-line"
            :class="row.cls"
          >
            <span v-if="row.time" class="terminal-time mono">{{ row.time }}</span>
            <span class="terminal-text">{{ row.text }}</span>
          </div>
          <div v-if="!oauthLogLines.length" class="terminal-empty">
            {{ oauthLogLoading ? '正在加载日志...' : '暂无详细日志' }}
          </div>
        </div>
        <transition name="fade">
          <button
            v-if="oauthUserScrolledUp"
            class="terminal-scroll-bottom-pill"
            title="检测到您正在查看历史日志，点击快速回到底部"
            @click="scrollToBottomOAuthLog"
          >
            <el-icon><ArrowDown /></el-icon>
            <span>回到底部</span>
          </button>
        </transition>
      </div>

      <template #footer>
        <div class="modal-footer">
          <span class="log-count-tip">共 {{ oauthLogLines.length }} 行日志</span>
          <div class="modal-footer-btns">
            <el-button size="small" @click="copyText(oauthLogLines.join('\n'))">
              <el-icon><CopyDocument /></el-icon>复制全部日志
            </el-button>
            <el-button size="small" type="primary" @click="oauthLogModalVisible = false">
              关闭
            </el-button>
          </div>
        </div>
      </template>
    </el-dialog>

    <!-- ──────────────── 账号批量验活控制台弹窗 (紧凑型架构，支持 Token 验活 & 套餐验活双模式) ──────────────── -->
    <el-dialog
      v-model="healthVisible" width="880px" top="5vh"
      class="oa-custom-dialog plus-dialog health-dialog"
      append-to-body
      :lock-scroll="false"
      :close-on-click-modal="false"
      @opened="layoutHealthTable"
      @closed="closeHealthCheck"
    >
      <template #header>
        <div class="oa-header">
          <div class="oa-header-title">
            <span class="oa-title-badge health-badge">HEALTH</span>
            <span class="oa-title-text">账号批量验活任务台</span>
            <el-tag size="small" :type="healthForm.mode === 'token' ? 'primary' : 'success'" round effect="dark">
              {{ healthForm.mode === 'token' ? '🔑 Token 状态验活' : '💎 套餐与试用资格探测' }}
            </el-tag>
            <el-tag size="small" type="info" round effect="plain">{{ healthTargetEmails.length }} 个账号</el-tag>
          </div>
          <div class="oa-header-extra">
            <el-button size="small" text @click="healthConfigCollapsed = !healthConfigCollapsed">
              <el-icon><Setting /></el-icon>{{ healthConfigCollapsed ? '展开参数配置' : '收起参数配置' }}
            </el-button>
          </div>
        </div>
      </template>

      <div class="oa-dialog-container">
        <!-- 参数配置卡片 -->
        <el-collapse-transition>
          <div v-show="!healthConfigCollapsed" class="oa-config-card">
            <el-form label-position="top" :disabled="healthRunning" size="small">
              <el-row :gutter="12">
                <el-col :xs="24" :sm="12" :md="6">
                  <el-form-item label="验活模式选择">
                    <el-select v-model="healthForm.mode" style="width: 100%">
                      <el-option label="🔑 Token 状态验活 (快速)" value="token" />
                      <el-option label="💎 套餐与试用探测 (深度)" value="plan" />
                    </el-select>
                  </el-form-item>
                </el-col>
                <el-col :xs="24" :sm="12" :md="8">
                  <el-form-item label="检测代理 (支持代理池轮询/直连)">
                    <el-select
                      v-model="healthForm.proxy" filterable clearable allow-create default-first-option
                      placeholder="选择或输入代理" style="width: 100%"
                    >
                      <el-option
                        v-if="proxyList.length"
                        label="🌐 全局代理池轮询 (自动多Worker分配)"
                        value="__POOL__"
                      />
                      <el-option v-for="p in proxyList" :key="p" :label="p" :value="p" />
                    </el-select>
                  </el-form-item>
                </el-col>
                <el-col :xs="24" :sm="12" :md="4">
                  <el-form-item label="代理目标国家">
                    <el-select
                      v-model="healthForm.proxyCountry" filterable allow-create
                      placeholder="国家" style="width: 100%"
                    >
                      <el-option
                        v-for="c in COUNTRY_OPTIONS" :key="c.value"
                        :label="c.label" :value="c.value"
                      />
                    </el-select>
                  </el-form-item>
                </el-col>
                <el-col :xs="12" :sm="6" :md="3">
                  <el-form-item label="并发 Worker">
                    <el-input-number v-model="healthForm.workers" :min="1" :max="20" style="width: 100%" />
                  </el-form-item>
                </el-col>
                <el-col :xs="12" :sm="6" :md="3">
                  <el-form-item label="超时(秒)">
                    <el-input-number v-model="healthForm.timeout" :min="5" :max="60" style="width: 100%" />
                  </el-form-item>
                </el-col>
              </el-row>
              <div style="font-size: 11.5px; color: var(--el-text-color-secondary); line-height: 1.5; margin-top: 2px">
                💡 <b>模式说明</b>：<b>Token 状态验活</b> 快速检测 Token 存活、JWT 到期时间与封号排查；<b>套餐与试用探测</b> 深度提取 Plus、Pro 5x/20x、Team、1个月免单活动等订阅状态。
              </div>
            </el-form>
          </div>
        </el-collapse-transition>

        <!-- KPI 统计看板：点卡片即筛选表格 -->
        <div class="plus-kpi-grid">
          <div
            class="plus-kpi-card clickable-card"
            :class="{ 'is-filter-active': healthFilter === 'all' }"
            title="查看全部账号"
            @click="setHealthFilter('all')"
          >
            <span class="kpi-label">已验活 / 总数</span>
            <span class="kpi-num">{{ healthStats.done }} / {{ healthStats.total }}</span>
          </div>
          <div
            v-if="healthForm.mode === 'token'"
            class="plus-kpi-card hit-active clickable-card"
            :class="{ 'is-filter-active': healthFilter === 'token_valid' }"
            title="只看 Token 仍然有效的账号"
            @click="setHealthFilter('token_valid')"
          >
            <span class="kpi-label">✅ Token 正常有效</span>
            <span class="kpi-num text-primary">{{ healthStats.token_valid }}</span>
          </div>
          <div
            v-if="healthForm.mode === 'plan' && healthStats.pro_active > 0"
            class="plus-kpi-card hit-pro clickable-card"
            :class="{ 'is-filter-active': healthFilter === 'pro_active' }"
            title="只看 Pro"
            @click="setHealthFilter('pro_active')"
          >
            <span class="kpi-label">👑 Pro 账号</span>
            <span class="kpi-num text-pro">{{ healthStats.pro_active }}</span>
          </div>
          <div
            v-if="healthForm.mode === 'plan'"
            class="plus-kpi-card hit-active clickable-card"
            :class="{ 'is-filter-active': healthFilter === 'plus_active' }"
            title="只看 Plus 订阅生效"
            @click="setHealthFilter('plus_active')"
          >
            <span class="kpi-label">★ Plus 订阅生效</span>
            <span class="kpi-num text-primary">{{ healthStats.plus_active }}</span>
          </div>
          <div
            v-if="healthForm.mode === 'plan'"
            class="plus-kpi-card hit-promo clickable-card"
            :class="{ 'is-filter-active': healthFilter === 'plus_eligible' }"
            title="只看 Plus 试用"
            @click="setHealthFilter('plus_eligible')"
          >
            <span class="kpi-label">◆ Plus 试用</span>
            <span class="kpi-num text-success">{{ healthStats.plus_eligible }}</span>
          </div>
          <div
            class="plus-kpi-card clickable-card"
            :class="{ 'card-warn': healthStats.dead > 0, 'is-filter-active': healthFilter === 'dead' || healthFilter === 'banned' || healthFilter === 'token_invalid' }"
            title="点这里只看封号和凭证失效，不会混在正常完成里"
            @click="setHealthFilter('dead')"
          >
            <span class="kpi-label">封号 / 凭证失效</span>
            <span class="kpi-num text-danger">{{ healthStats.dead }}</span>
            <span v-if="healthStats.dead > 0" class="kpi-sub-hint">封号 {{ healthStats.banned }} · 失效 {{ healthStats.token_invalid }}</span>
          </div>
          <div
            class="plus-kpi-card clickable-card"
            :class="{ 'card-warn': healthStats.error > 0, 'is-filter-active': healthFilter === 'failed' }"
            title="只看探测异常/失败（网络、429 等），不含封号"
            @click="setHealthFilter('failed')"
          >
            <span class="kpi-label">异常 / 失败</span>
            <span class="kpi-num">{{ healthStats.error }}</span>
          </div>
          <div class="plus-progress-cell">
            <el-progress
              :percentage="healthStats.percent"
              :status="healthStats.done === healthStats.total && healthStats.total > 0 ? 'success' : ''"
              :stroke-width="8"
              striped
              :striped-flow="healthRunning"
            />
          </div>
        </div>

        <!-- 核心表格：验活监控列表 (内置状态过滤与前端高性能分页，杜绝万级账号卡顿) -->
        <div class="health-table-filter-bar">
          <el-radio-group v-model="healthFilter" size="small" class="health-filter-radio" @change="healthPage = 1">
            <el-radio-button label="all">全部 ({{ healthStats.total }})</el-radio-button>
            <el-radio-button label="running">运行中 ({{ healthStats.running }})</el-radio-button>
            <el-radio-button label="dead">
              <span :class="{ 'text-danger': healthStats.dead > 0 }">封号/失效 ({{ healthStats.dead }})</span>
            </el-radio-button>
            <el-radio-button label="failed">
              <span :class="{ 'text-danger': healthStats.error > 0 }">异常/失败 ({{ healthStats.error }})</span>
            </el-radio-button>
            <el-radio-button label="done">正常完成 ({{ healthStats.doneOk }})</el-radio-button>
          </el-radio-group>
          <div class="health-filter-right">
            <el-input
              v-model="healthSearch"
              placeholder="快速过滤邮箱..."
              clearable
              size="small"
              class="health-search-input"
              :prefix-icon="Search"
              @input="healthPage = 1"
            />
          </div>
        </div>

        <div class="plus-table-wrap health-table-wrap">
          <el-table
            ref="healthTableRef"
            :data="healthDisplayRows"
            row-key="email"
            size="small"
            stripe
            :max-height="healthConfigCollapsed ? 360 : 240"
            class="macos-table health-live-table"
            :highlight-current-row="false"
            v-memo="[healthTick, healthFilter, healthPage, healthPageSize, healthSearch, healthRunning]"
          >
            <el-table-column prop="email" label="账号邮箱" min-width="220" show-overflow-tooltip>
              <template #default="{ row }">
                <button
                  class="macos-tag-btn copy-btn"
                  title="点击复制邮箱"
                  @click="copyText(row.email)"
                >
                  <span class="mono">{{ row.email }}</span>
                  <el-icon class="copy-ico"><CopyDocument /></el-icon>
                </button>
              </template>
            </el-table-column>

            <el-table-column label="验活模式" width="110" align="center">
              <template #default="{ row }">
                <el-tag size="small" :type="row.mode === 'token' ? 'primary' : 'success'" effect="plain">
                  {{ row.mode === 'token' ? 'Token 探测' : '套餐探测' }}
                </el-tag>
              </template>
            </el-table-column>

            <el-table-column label="当前状态 / 步骤" min-width="160">
              <template #default="{ row }">
                <span v-if="row.status === 'running'" class="running-step">
                  <el-icon class="is-loading" style="margin-right: 4px"><Loading /></el-icon>
                  {{ row.step_text || '检测中...' }}
                </span>
                <el-tag v-else-if="row.status === 'pending'" size="small" type="info" effect="plain">排队中</el-tag>
                <el-tag
                  v-else-if="row.result"
                  size="small"
                  :type="row.result.status === 'token_valid' ? 'success' : row.result.status === 'plus_active' || row.result.status === 'team_active' ? 'primary' : row.result.status === 'plus_eligible' ? 'success' : row.result.status === 'pro_active' || row.result.status === 'pro_20x' || row.result.status === 'pro_5x' ? 'danger' : isHealthDeadRow(row) || isHealthFailedRow(row) ? 'danger' : 'info'"
                  :title="row.result.error || row.result.label || row.result.status"
                >
                  {{ row.result.label || row.result.status }}
                </el-tag>
                <span v-else class="text-muted">—</span>
              </template>
            </el-table-column>

            <el-table-column label="耗时" width="80" align="center">
              <template #default="{ row }">
                <ElapsedTimer :status="row.status" :started-at="row.started_at" :elapsed="row.elapsed" />
              </template>
            </el-table-column>

            <el-table-column label="操作" width="130" align="center" fixed="right">
              <template #default="{ row }">
                <el-button
                  v-if="isHealthFailedRow(row) && !healthRunning"
                  size="small"
                  text
                  type="warning"
                  @click="retrySingleHealthCheck(row)"
                  title="单独重试此异常账号"
                >
                  <el-icon><Refresh /></el-icon>验活
                </el-button>
                <el-button size="small" text type="primary" :disabled="row.status === 'pending'" @click="openHealthItemLog(row)">
                  <el-icon><Document /></el-icon>日志
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>

        <!-- 高性能极速分页底栏 -->
        <div class="health-pagination-bar">
          <span class="health-page-count-tip text-muted text-xs">
            显示第 {{ healthFilteredRows.length > 0 ? (healthPage - 1) * healthPageSize + 1 : 0 }} - {{ Math.min(healthPage * healthPageSize, healthFilteredRows.length) }} 条 · 过滤共 <b>{{ healthFilteredRows.length }}</b> 条
          </span>
          <el-pagination
            v-model:current-page="healthPage"
            v-model:page-size="healthPageSize"
            :page-sizes="[50, 100, 200, 500]"
            :total="healthFilteredRows.length"
            layout="sizes, prev, pager, next"
            size="small"
          />
        </div>
      </div>

      <template #footer>
        <div class="oa-dialog-footer">
          <div class="footer-tip">
            <span v-if="healthRunning" class="running-indicator">
              <span class="pulse-dot"></span> 正在多 Worker 并发验活 (Workers: {{ healthForm.workers }})...
            </span>
            <span v-else-if="healthTaskId" class="finished-indicator">
              验活已完成，结果已自动更新至数据库
            </span>
          </div>
          <div class="footer-btns">
            <el-button @click="closeHealthCheck">
              {{ healthRunning ? '后台运行' : '关闭' }}
            </el-button>
            <el-button v-if="healthRunning" type="danger" plain @click="stopHealthCheckTask">
              <el-icon><SwitchButton /></el-icon>停止验活
            </el-button>
            <template v-else>
              <el-button
                v-if="failedHealthEmails.length > 0"
                type="warning"
                plain
                @click="retryFailedHealthCheck"
              >
                <el-icon><Refresh /></el-icon>重新验活失败 ({{ failedHealthEmails.length }})
              </el-button>
              <el-button
                type="primary"
                class="start-gradient-btn"
                :loading="healthRunning"
                :disabled="!healthTargetEmails.length"
                @click="startHealthCheckTask"
              >
                <el-icon><VideoPlay /></el-icon>{{ healthTaskId ? '重新验活' : '开始批量验活' }}
              </el-button>
            </template>
          </div>
        </div>
      </template>
    </el-dialog>

    <!-- 单账号专属验活日志弹窗 -->
    <el-dialog
      v-model="healthLogModalVisible"
      width="780px"
      top="8vh"
      class="macos-terminal-dialog"
      :close-on-click-modal="false"
    >
      <template #header>
        <div class="modal-header">
          <div class="window-dots">
            <span class="dot red"></span>
            <span class="dot yellow"></span>
            <span class="dot green"></span>
          </div>
          <div class="modal-title-info">
            <span class="modal-email mono" @click="currentHealthLogItem?.email && copyText(currentHealthLogItem.email, '邮箱已复制')" title="点击复制邮箱">{{ currentHealthLogItem?.email }}</span>
            <span class="modal-run-tag">
              验活详细日志
            </span>
          </div>
        </div>
      </template>

      <div class="modal-terminal-wrap">
        <div ref="healthModalLogBoxRef" class="modal-terminal-body" @scroll="onHealthLogScroll">
          <div
            v-for="(line, idx) in healthLogLines"
            :key="idx"
            class="terminal-line"
            :class="getLogClass(line)"
          >
            <span v-if="formatTerminalLine(line).time" class="terminal-time mono">{{ formatTerminalLine(line).time }}</span>
            <span class="terminal-text">{{ formatTerminalLine(line).text }}</span>
          </div>
          <div v-if="!healthLogLines.length" class="terminal-empty">
            {{ healthLogLoading ? '正在加载日志...' : '暂无详细日志' }}
          </div>
        </div>
        <transition name="fade">
          <button
            v-if="healthUserScrolledUp"
            class="terminal-scroll-bottom-pill"
            title="检测到您正在查看历史日志，点击快速回到底部"
            @click="scrollToBottomHealthLog"
          >
            <el-icon><ArrowDown /></el-icon>
            <span>回到底部</span>
          </button>
        </transition>
      </div>

      <template #footer>
        <div class="modal-footer">
          <span class="log-count-tip">共 {{ healthLogLines.length }} 行日志</span>
          <div class="modal-footer-btns">
            <el-button size="small" @click="copyText(healthLogLines.join('\n'))">
              <el-icon><CopyDocument /></el-icon>复制日志
            </el-button>
            <el-button size="small" type="primary" @click="healthLogModalVisible = false">
              关闭
            </el-button>
          </div>
        </div>
      </template>
    </el-dialog>

    <TokenRefreshStudio
      v-model="refreshVisible"
      :emails="refreshTargetEmails"
      @completed="onTokenRefreshCompleted"
    />

    <!-- ──────────────── 安全加固任务控制台 (批量补密码 & 批量补2FA 任务台) ──────────────── -->
    <el-dialog
      v-model="securityVisible"
      width="920px"
      top="5vh"
      class="oa-custom-dialog plus-dialog sec-dialog"
      :close-on-click-modal="false"
      @closed="closeSecurityTask"
    >
      <template #header>
        <div class="oa-header">
          <div class="oa-header-title">
            <span class="oa-title-badge sec-badge">SECURITY</span>
            <span class="oa-title-text">账号安全加固任务台</span>
            <el-tag size="small" :type="securityAction === 'password' ? 'primary' : 'success'" round effect="dark">
              {{ securityAction === 'password' ? '🔑 批量官方设密/重置' : '🛡️ 批量自适应补绑 2FA' }}
            </el-tag>
            <el-tag size="small" type="info" round effect="plain">{{ securityTargetEmails.length }} 个目标账号</el-tag>
          </div>
          <div class="oa-header-extra">
            <el-button size="small" text @click="securityConfigCollapsed = !securityConfigCollapsed">
              <el-icon><Setting /></el-icon>{{ securityConfigCollapsed ? '展开参数配置' : '收起参数配置' }}
            </el-button>
          </div>
        </div>
      </template>

      <div class="oa-dialog-container">
        <!-- 参数配置卡片 -->
        <el-collapse-transition>
          <div v-show="!securityConfigCollapsed" class="oa-config-card">
            <el-form label-position="top" :disabled="securityRunning" size="small">
              <el-row :gutter="12">
                <el-col :xs="24" :sm="12" :md="5">
                  <el-form-item label="任务模式选择">
                    <el-select v-model="securityAction" style="width: 100%">
                      <el-option label="🔑 官方设密 / 补设登录密码" value="password" />
                      <el-option label="🛡️ 官方自适应补绑 2FA" value="2fa" />
                    </el-select>
                  </el-form-item>
                </el-col>
                <el-col :xs="24" :sm="12" :md="6">
                  <el-form-item label="网络代理 (支持全局代理池/直连)">
                    <el-select
                      v-model="securityForm.proxy"
                      filterable
                      clearable
                      allow-create
                      default-first-option
                      placeholder="选择或输入代理"
                      style="width: 100%"
                    >
                      <el-option
                        v-if="proxyList.length"
                        label="🌐 全局代理池轮询 (自动多Worker分配)"
                        value="__POOL__"
                      />
                      <el-option v-for="p in proxyList" :key="p" :label="p" :value="p" />
                    </el-select>
                  </el-form-item>
                </el-col>
                <el-col :xs="24" :sm="12" :md="5">
                  <el-form-item label="代理目标国家 (自动重写时区与Session)">
                    <el-select
                      v-model="securityForm.proxyCountry"
                      filterable
                      allow-create
                      placeholder="选择目标国家"
                      style="width: 100%"
                    >
                      <el-option
                        v-for="c in COUNTRY_OPTIONS"
                        :key="c.value"
                        :label="c.label"
                        :value="c.value"
                      />
                    </el-select>
                  </el-form-item>
                </el-col>
                <el-col :xs="12" :sm="6" :md="3">
                  <el-form-item label="并发 Worker">
                    <el-input-number v-model="securityForm.workers" :min="1" :max="10" style="width: 100%" />
                  </el-form-item>
                </el-col>
                <el-col :xs="12" :sm="6" :md="2">
                  <el-form-item label="超时 (秒)">
                    <el-input-number v-model="securityForm.timeout" :min="10" :max="180" style="width: 100%" />
                  </el-form-item>
                </el-col>
                <el-col v-if="securityAction === 'password'" :xs="24" :sm="12" :md="3">
                  <el-form-item label="服务端生效">
                    <el-checkbox v-model="securityForm.officialReset" label="官方生效" />
                  </el-form-item>
                </el-col>
              </el-row>
              <div style="font-size: 11.5px; color: var(--el-text-color-secondary); line-height: 1.5; margin-top: 2px">
                💡 <b>运作说明</b>：系统将从选定代理中为每个账号自动重写为<b>【{{ securityForm.proxyCountry || '保持原样' }}】</b>出口并分配独立住宅会话。<b>官方设密</b>将自动向 OpenAI 申请重置验证码并在官方生效；<b>补绑 2FA</b> 将激活 TOTP 并持久化 Secret。
              </div>
            </el-form>
          </div>
        </el-collapse-transition>

        <!-- KPI 统计看板 -->
        <div class="plus-kpi-grid">
          <div class="plus-kpi-card">
            <span class="kpi-label">已处理 / 总数</span>
            <span class="kpi-num">{{ securityStats.done }} / {{ securityStats.total }}</span>
          </div>
          <div class="plus-kpi-card hit-active">
            <span class="kpi-label">✅ 成功生效</span>
            <span class="kpi-num text-primary">{{ securityStats.success }}</span>
          </div>
          <div class="plus-kpi-card hit-fail">
            <span class="kpi-label">❌ 失败 / 异常</span>
            <span class="kpi-num" :class="securityStats.fail > 0 ? 'text-danger' : ''">{{ securityStats.fail }}</span>
          </div>
          <div class="plus-kpi-card">
            <span class="kpi-label">⚡ 任务耗时</span>
            <span class="kpi-num mono">{{ securityElapsed }}s</span>
          </div>
        </div>

        <!-- 进度条 -->
        <div class="plus-progress-row">
          <el-progress
            :percentage="securityStats.percent"
            :stroke-width="6"
            :status="securityStats.percent === 100 ? (securityStats.fail > 0 ? 'warning' : 'success') : ''"
          />
        </div>

        <!-- 操作工具栏 & 筛选 -->
        <div class="plus-actions-toolbar">
          <div class="toolbar-left">
            <el-radio-group v-model="securityFilter" size="small" class="macos-radio-group">
              <el-radio-button value="all">全部 ({{ securityStats.total }})</el-radio-button>
              <el-radio-button value="running">进行中 ({{ securityStats.running }})</el-radio-button>
              <el-radio-button value="success">成功 ({{ securityStats.success }})</el-radio-button>
              <el-radio-button value="failed">失败 ({{ securityStats.fail }})</el-radio-button>
              <el-radio-button value="pending">排队中 ({{ securityStats.pending }})</el-radio-button>
            </el-radio-group>

            <!-- 核心功能：一键重试所有失败账号 -->
            <el-button
              size="small"
              type="danger"
              plain
              :disabled="securityStats.fail === 0 || securityRunning"
              @click="retrySecurityTaskRunner()"
            >
              <el-icon><Refresh /></el-icon>🔄 一键重试失败账号 ({{ securityStats.fail }})
            </el-button>
          </div>

          <div class="toolbar-right">
            <el-input
              v-model="securitySearch"
              placeholder="搜索当前列表邮箱..."
              prefix-icon="Search"
              size="small"
              clearable
              style="width: 190px"
            />
          </div>
        </div>

        <!-- 账号处理表格 -->
        <div class="oa-table-box">
          <el-table
            :data="securityDisplayRows"
            size="small"
            style="width: 100%"
            height="320"
            class="macos-table"
            empty-text="暂无账号数据"
          >
            <el-table-column label="账号邮箱" min-width="190" show-overflow-tooltip>
              <template #default="{ row }">
                <button
                  type="button"
                  class="macos-tag-btn copy-btn"
                  title="点击复制邮箱"
                  @click="copyText(row.email)"
                >
                  <span class="mono">{{ row.email }}</span>
                  <el-icon class="copy-ico"><CopyDocument /></el-icon>
                </button>
              </template>
            </el-table-column>

            <el-table-column label="状态" width="100" align="center">
              <template #default="{ row }">
                <el-tag v-if="row.status === 'running'" size="small" type="primary" effect="dark">
                  <el-icon class="is-loading"><Loading /></el-icon> 执行中
                </el-tag>
                <el-tag v-else-if="row.status === 'success'" size="small" type="success" effect="dark">
                  ✅ 成功
                </el-tag>
                <el-tag v-else-if="isSecurityFailedRow(row)" size="small" type="danger" effect="dark">
                  ❌ 失败
                </el-tag>
                <el-tag v-else-if="row.status === 'skipped'" size="small" type="warning" effect="plain">
                  ⚪ 跳过
                </el-tag>
                <el-tag v-else size="small" type="info" effect="plain">
                  排队中
                </el-tag>
              </template>
            </el-table-column>

            <el-table-column label="步骤与处理结果" min-width="240" show-overflow-tooltip>
              <template #default="{ row }">
                <div v-if="row.result?.password" class="mono text-success link" style="cursor: pointer" @click="copyText(row.result.password, '密码已复制')">
                  🔑 密码: {{ row.result.password }}
                  <el-tag v-if="row.result.official_applied" size="small" type="success" style="margin-left: 4px">官方生效</el-tag>
                </div>
                <div v-else-if="row.result?.totp_secret" class="mono text-success link" style="cursor: pointer" @click="copyText(row.result.totp_secret, '2FA Secret 已复制')">
                  🛡️ Secret: {{ row.result.totp_secret }}
                </div>
                <div v-else-if="row.error" class="text-danger" style="font-size: 11.5px">
                  {{ row.error }}
                </div>
                <div v-else style="font-size: 11.5px; color: var(--el-text-color-secondary)">
                  {{ row.step_text || '—' }}
                </div>
              </template>
            </el-table-column>

            <el-table-column label="耗时" width="85" align="right">
              <template #default="{ row }">
                <span class="mono hint" :style="{ color: row.status === 'running' ? 'var(--el-color-primary)' : '' }">
                  {{ getSecurityRowElapsed(row) }}
                </span>
              </template>
            </el-table-column>

            <el-table-column label="操作" width="130" align="center" fixed="right">
              <template #default="{ row }">
                <div class="row-actions">
                  <el-button size="small" text type="primary" @click="openSecurityItemLog(row)">
                    📜 日志
                  </el-button>
                  <el-button
                    v-if="isSecurityFailedRow(row)"
                    size="small"
                    text
                    type="warning"
                    :disabled="securityRunning"
                    @click="retrySecurityTaskRunner([row.email])"
                  >
                    🔄 重试
                  </el-button>
                </div>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </div>

      <template #footer>
        <div class="oa-dialog-footer">
          <div class="footer-left">
            <el-pagination
              v-model:current-page="securityPage"
              v-model:page-size="securityPageSize"
              :page-sizes="[20, 50, 100, 200]"
              :total="securityFilteredRows.length"
              layout="total, sizes, prev, pager, next"
              size="small"
            />
          </div>
          <div class="footer-right">
            <el-button size="small" @click="closeSecurityTask">关闭窗口</el-button>
            <el-button
              v-if="securityRunning"
              size="small"
              type="danger"
              plain
              @click="stopSecurityTaskRunner"
            >
              <el-icon><SwitchButton /></el-icon>停止任务
            </el-button>
            <el-button
              v-else
              type="primary"
              class="start-gradient-btn"
              :loading="securityRunning"
              :disabled="!securityTargetEmails.length"
              @click="startSecurityTaskRunner"
            >
              <el-icon><VideoPlay /></el-icon>{{ securityTaskId ? '重新执行' : (securityAction === 'password' ? '🚀 启动官方设密' : '🛡️ 启动补绑 2FA') }}
            </el-button>
          </div>
        </div>
      </template>
    </el-dialog>

    <!-- ──────────────── 单账号安全加固详细日志终端弹窗 ──────────────── -->
    <el-dialog
      v-model="securityLogModalVisible"
      width="780px"
      top="8vh"
      class="macos-terminal-dialog"
      :close-on-click-modal="false"
    >
      <template #header>
        <div class="modal-header">
          <div class="window-dots">
            <span class="dot red"></span>
            <span class="dot yellow"></span>
            <span class="dot green"></span>
          </div>
          <div class="modal-title-info">
            <span class="modal-email mono" @click="currentSecurityLogItem?.email && copyText(currentSecurityLogItem.email, '邮箱已复制')" title="点击复制邮箱">{{ currentSecurityLogItem?.email }}</span>
            <span class="modal-run-tag">
              {{ currentSecurityLogItem?.action === 'password' ? '🔑 设密任务日志' : '🛡️ 2FA 绑定日志' }}
            </span>
          </div>
        </div>
      </template>

      <div class="modal-terminal-wrap">
        <div ref="securityModalLogBoxRef" class="modal-terminal-body" @scroll="onSecurityLogScroll">
          <div
            v-for="(line, idx) in securityLogLines"
            :key="idx"
            class="terminal-line"
            :class="getLogClass(line)"
          >
            <span v-if="formatTerminalLine(line).time" class="terminal-time mono">{{ formatTerminalLine(line).time }}</span>
            <span class="terminal-text">{{ formatTerminalLine(line).text }}</span>
          </div>
          <div v-if="!securityLogLines.length" class="terminal-empty">
            {{ securityLogLoading ? '正在加载日志...' : '暂无详细日志' }}
          </div>
        </div>
        <transition name="fade">
          <button
            v-if="securityUserScrolledUp"
            class="terminal-scroll-bottom-pill"
            title="检测到您正在查看历史日志，点击快速回到底部"
            @click="scrollToBottomSecurityLog"
          >
            <el-icon><ArrowDown /></el-icon>
            <span>回到底部</span>
          </button>
        </transition>
      </div>

      <template #footer>
        <div class="modal-footer">
          <span class="log-count-tip">共 {{ securityLogLines.length }} 行日志</span>
          <div class="modal-footer-btns">
            <el-button size="small" @click="copyText(securityLogLines.join('\n'))">
              <el-icon><CopyDocument /></el-icon>复制全部日志
            </el-button>
            <el-button size="small" type="primary" @click="securityLogModalVisible = false">
              关闭
            </el-button>
          </div>
        </div>
      </template>
    </el-dialog>

    <!-- ──────────────── 账号批量保温与保鲜控制台 (Account Warming Daemon) ──────────────── -->
    <el-dialog
      v-model="warmingVisible"
      width="920px"
      top="5vh"
      class="oa-custom-dialog plus-dialog warm-dialog"
      :close-on-click-modal="false"
      @closed="stopWarmingTaskRun"
    >
      <template #header>
        <div class="oa-header">
          <div class="oa-header-title">
            <span class="oa-title-badge warm-badge">WARMING</span>
            <span class="oa-title-text">账号生命周期保温与保鲜台</span>
            <el-tag size="small" type="warning" round effect="dark">
              ⚡ 官方接口活跃交互 · 刷新 Token · 免沉睡防封
            </el-tag>
            <el-tag size="small" type="info" round effect="plain">{{ warmingTargetEmails.length }} 个目标账号</el-tag>
          </div>
          <div class="oa-header-extra">
            <el-button size="small" text @click="warmingConfigCollapsed = !warmingConfigCollapsed">
              <el-icon><Setting /></el-icon>{{ warmingConfigCollapsed ? '展开参数配置' : '收起参数配置' }}
            </el-button>
          </div>
        </div>
      </template>

      <div class="oa-dialog-container">
        <!-- 参数配置卡片 -->
        <el-collapse-transition>
          <div v-show="!warmingConfigCollapsed" class="oa-config-card">
            <el-form label-position="top" :disabled="warmingRunning" size="small">
              <el-row :gutter="12">
                <el-col :xs="24" :sm="12" :md="10">
                  <el-form-item label="网络代理 (支持全局代理池/直连)">
                    <el-select
                      v-model="warmingForm.proxy"
                      filterable
                      clearable
                      allow-create
                      default-first-option
                      placeholder="选择或输入代理"
                      style="width: 100%"
                    >
                      <el-option
                        v-if="proxyList.length"
                        label="🌐 全局代理池轮询 (自动多Worker分配)"
                        value="__POOL__"
                      />
                      <el-option v-for="p in proxyList" :key="p" :label="p" :value="p" />
                    </el-select>
                  </el-form-item>
                </el-col>
                <el-col :xs="24" :sm="12" :md="8">
                  <el-form-item label="代理目标国家 (自动重写时区与Session)">
                    <el-select
                      v-model="warmingForm.proxyCountry"
                      filterable
                      allow-create
                      placeholder="选择目标国家"
                      style="width: 100%"
                    >
                      <el-option
                        v-for="c in COUNTRY_OPTIONS"
                        :key="c.value"
                        :label="c.label"
                        :value="c.value"
                      />
                    </el-select>
                  </el-form-item>
                </el-col>
                <el-col :xs="12" :sm="6" :md="6">
                  <el-form-item label="并发 Worker">
                    <el-input-number v-model="warmingForm.workers" :min="1" :max="20" style="width: 100%" />
                  </el-form-item>
                </el-col>
              </el-row>
              <div style="font-size: 11.5px; color: var(--el-text-color-secondary); line-height: 1.5; margin-top: 2px">
                💡 <b>保温原理</b>：利用 Refresh Token 换取全新 Access Token，并向官方发送轻量模型与用户信息健康探测，模拟真实用户交互，使账号脱离长期静默休眠期，有效预防官方批量收回与沉睡封号。
              </div>
            </el-form>
          </div>
        </el-collapse-transition>

        <!-- KPI 统计看板 -->
        <div class="plus-kpi-grid">
          <div class="plus-kpi-card">
            <span class="kpi-label">已处理 / 总数</span>
            <span class="kpi-num">{{ warmingStats.done }} / {{ warmingStats.total }}</span>
          </div>
          <div class="plus-kpi-card hit-active">
            <span class="kpi-label">✅ 保温成功</span>
            <span class="kpi-num text-primary">{{ warmingStats.success }}</span>
          </div>
          <div class="plus-kpi-card hit-fail">
            <span class="kpi-label">❌ 失败 / 异常</span>
            <span class="kpi-num" :class="warmingStats.fail > 0 ? 'text-danger' : ''">{{ warmingStats.fail }}</span>
          </div>
          <div class="plus-kpi-card">
            <span class="kpi-label">⚡ 任务耗时</span>
            <span class="kpi-num mono">{{ warmingElapsed }}s</span>
          </div>
        </div>

        <!-- 进度条 -->
        <div class="plus-progress-row">
          <el-progress
            :percentage="warmingStats.pct"
            :stroke-width="6"
            :status="warmingStats.pct === 100 ? (warmingStats.fail > 0 ? 'warning' : 'success') : ''"
          />
        </div>

        <!-- 操作工具栏 & 筛选 -->
        <div class="plus-actions-toolbar">
          <div class="toolbar-left">
            <el-radio-group v-model="warmingFilter" size="small" class="macos-radio-group">
              <el-radio-button value="all">全部 ({{ warmingStats.total }})</el-radio-button>
              <el-radio-button value="running">进行中 ({{ warmingStats.running }})</el-radio-button>
              <el-radio-button value="success">成功 ({{ warmingStats.success }})</el-radio-button>
              <el-radio-button value="failed">失败 ({{ warmingStats.fail }})</el-radio-button>
              <el-radio-button value="pending">排队中 ({{ warmingStats.pending }})</el-radio-button>
            </el-radio-group>
          </div>

          <div class="toolbar-right">
            <el-input
              v-model="warmingSearch"
              placeholder="搜索当前列表邮箱..."
              prefix-icon="Search"
              size="small"
              clearable
              style="width: 190px"
            />
          </div>
        </div>

        <!-- 账号处理表格 -->
        <div class="oa-table-box">
          <el-table
            :data="warmingDisplayRows"
            size="small"
            style="width: 100%"
            height="320"
            class="macos-table"
            empty-text="暂无账号数据"
          >
            <el-table-column label="账号邮箱" min-width="190" show-overflow-tooltip>
              <template #default="{ row }">
                <button
                  type="button"
                  class="macos-tag-btn copy-btn"
                  title="点击复制邮箱"
                  @click="copyText(row.email)"
                >
                  <span class="mono">{{ row.email }}</span>
                  <el-icon class="copy-ico"><CopyDocument /></el-icon>
                </button>
              </template>
            </el-table-column>

            <el-table-column label="状态" width="100" align="center">
              <template #default="{ row }">
                <el-tag v-if="row.status === 'running'" size="small" type="primary" effect="dark">
                  <el-icon class="is-loading"><Loading /></el-icon> 保温中
                </el-tag>
                <el-tag v-else-if="row.status === 'success' || row.status === 'done'" size="small" type="success" effect="dark">
                  ✅ 成功
                </el-tag>
                <el-tag v-else-if="row.status === 'failed'" size="small" type="danger" effect="dark">
                  ❌ 失败
                </el-tag>
                <el-tag v-else size="small" type="info" effect="plain">
                  排队中
                </el-tag>
              </template>
            </el-table-column>

            <el-table-column label="保鲜探测与活跃信息" min-width="240" show-overflow-tooltip>
              <template #default="{ row }">
                <div v-if="row.models_count || row.user_name" class="mono text-success" style="font-size: 12px">
                  <span>🟢 模型数: {{ row.models_count || 0 }}</span>
                  <span v-if="row.user_name" style="margin-left: 8px">👤 {{ row.user_name }}</span>
                </div>
                <div v-else-if="row.error" class="text-danger" style="font-size: 11.5px">
                  {{ row.error }}
                </div>
                <div v-else style="font-size: 11.5px; color: var(--el-text-color-secondary)">
                  {{ row.step || '—' }}
                </div>
              </template>
            </el-table-column>

            <el-table-column label="耗时" width="85" align="right">
              <template #default="{ row }">
                <span class="mono hint" :style="{ color: row.status === 'running' ? 'var(--el-color-primary)' : '' }">
                  {{ getWarmingRowElapsed(row) }}
                </span>
              </template>
            </el-table-column>

            <el-table-column label="操作" width="100" align="center" fixed="right">
              <template #default="{ row }">
                <div class="row-actions">
                  <el-button size="small" text type="primary" @click="openWarmingItemLog(row)">
                    📜 日志
                  </el-button>
                </div>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </div>

      <template #footer>
        <div class="oa-dialog-footer">
          <div class="footer-left">
            <el-pagination
              v-model:current-page="warmingPage"
              v-model:page-size="warmingPageSize"
              :page-sizes="[20, 50, 100, 200]"
              :total="warmingFilteredRows.length"
              layout="total, sizes, prev, pager, next"
              size="small"
            />
          </div>
          <div class="footer-right">
            <el-button size="small" @click="warmingVisible = false">关闭窗口</el-button>
            <el-button
              v-if="warmingRunning"
              size="small"
              type="danger"
              plain
              @click="stopWarmingTaskRun"
            >
              <el-icon><SwitchButton /></el-icon>停止任务
            </el-button>
            <el-button
              v-else
              type="primary"
              class="start-gradient-btn"
              :loading="warmingRunning"
              :disabled="!warmingTargetEmails.length"
              @click="startWarmingTaskRun"
            >
              <el-icon><VideoPlay /></el-icon>{{ warmingTaskId ? '重新执行' : '🚀 启动批量保温保鲜' }}
            </el-button>
          </div>
        </div>
      </template>
    </el-dialog>

    <!-- ──────────────── 单账号保温详细日志终端弹窗 ──────────────── -->
    <el-dialog
      v-model="warmingLogModalVisible"
      width="780px"
      top="8vh"
      class="macos-terminal-dialog"
      :close-on-click-modal="false"
    >
      <template #header>
        <div class="modal-header">
          <div class="window-dots">
            <span class="dot red"></span>
            <span class="dot yellow"></span>
            <span class="dot green"></span>
          </div>
          <div class="modal-title-info">
            <span class="modal-email mono" @click="currentWarmingLogItem?.email && copyText(currentWarmingLogItem.email, '邮箱已复制')" title="点击复制邮箱">{{ currentWarmingLogItem?.email }}</span>
            <span class="modal-run-tag">
              ☀️ 账号保鲜活跃日志
            </span>
          </div>
        </div>
      </template>

      <div class="modal-terminal-wrap">
        <div ref="warmingModalLogBoxRef" class="modal-terminal-body" @scroll="onWarmingLogScroll">
          <div
            v-for="(line, idx) in warmingLogLines"
            :key="idx"
            class="terminal-line"
            :class="getLogClass(line)"
          >
            <span v-if="formatTerminalLine(line).time" class="terminal-time mono">{{ formatTerminalLine(line).time }}</span>
            <span class="terminal-text">{{ formatTerminalLine(line).text }}</span>
          </div>
          <div v-if="!warmingLogLines.length" class="terminal-empty">
            {{ warmingLogLoading ? '正在加载日志...' : '暂无详细日志' }}
          </div>
        </div>
        <transition name="fade">
          <button
            v-if="warmingUserScrolledUp"
            class="terminal-scroll-bottom-pill"
            title="检测到您正在查看历史日志，点击快速回到底部"
            @click="scrollToBottomWarmingLog"
          >
            <el-icon><ArrowDown /></el-icon>
            <span>回到底部</span>
          </button>
        </transition>
      </div>

      <template #footer>
        <div class="modal-footer">
          <span class="log-count-tip">共 {{ warmingLogLines.length }} 行日志</span>
          <div class="modal-footer-btns">
            <el-button size="small" @click="copyText(warmingLogLines.join('\n'))">
              <el-icon><CopyDocument /></el-icon>复制全部日志
            </el-button>
            <el-button size="small" type="primary" @click="warmingLogModalVisible = false">
              关闭
            </el-button>
          </div>
        </div>
      </template>
    </el-dialog>

    <!-- ──────────────── 全新批量导出配置与状态留痕弹窗 ──────────────── -->
    <el-dialog
      v-model="exportConfigModalVisible"
      width="640px"
      top="8vh"
      class="macos-custom-dialog export-config-dialog"
      :close-on-click-modal="false"
    >
      <template #header>
        <div class="export-modal-header">
          <div class="window-dots">
            <span class="dot red"></span>
            <span class="dot yellow"></span>
            <span class="dot green"></span>
          </div>
          <div class="export-modal-title">
            <span class="main-title">📦 批量导出数据与留痕归档</span>
            <span class="sub-title">Export Studio · 状态记录与分卷打包</span>
          </div>
        </div>
      </template>

      <div class="export-modal-body">
        <!-- 连环导出成功状态横幅 (若上次已成功导出) -->
        <div v-if="lastExportedInfo" class="export-last-success-banner">
          <div class="banner-left">
            <span class="success-indicator-dot"></span>
            <span class="banner-msg">
              已成功下载: <strong>{{ lastExportedInfo.filename }}</strong> ({{ lastExportedInfo.format }} · {{ lastExportedInfo.count }} 个账号<span v-if="lastExportedInfo.skipped">，跳过 {{ lastExportedInfo.skipped }}</span>)
            </span>
          </div>
          <span class="banner-hint">✨ 弹窗与勾选已保留，可继续切换格式连续导出</span>
        </div>

        <!-- 1. 导出目标格式选择 -->
        <div class="export-field-group">
          <div class="field-title-row">
            <span class="field-label">1. 目标导出格式 (Format)</span>
            <span v-if="exportTargetFmt" class="field-extra-pill">
              {{ exportTargetFmt.mode === 'download' ? '📦 文件直下' : '📄 文本预览/下载' }}
            </span>
          </div>
          <el-select
            v-model="exportTargetFmt"
            value-key="id"
            class="export-select-block"
            placeholder="请选择导出格式"
          >
            <el-option
              v-for="fmt in exportFormats"
              :key="fmt.id"
              :label="fmt.label"
              :value="fmt"
            >
              <div class="fmt-option-row">
                <span class="fmt-opt-label">{{ fmt.label }}</span>
                <span v-if="fmt.note" class="fmt-opt-note">{{ fmt.note }}</span>
              </div>
            </el-option>
          </el-select>
          <div v-if="exportTargetFmt?.note" class="field-desc-tip">
            💡 格式说明：{{ exportTargetFmt.note }}
          </div>
          <div v-if="isPw2faExportFormat" class="field-desc-tip">
            ⚠️ 邮箱----密码----2FA：密码或 2FA 缺任一字段的账号整行不导出，也不会打导出留痕。
          </div>
        </div>

        <!-- 2. 导出范围选择 -->
        <div class="export-field-group">
          <div class="field-title-row">
            <span class="field-label">2. 导出账号范围 (Scope)</span>
            <span class="field-count-badge">共 {{ exportTargetCount }} 个目标账号</span>
          </div>
          <div class="scope-radio-cards">
            <div
              class="scope-card"
              :class="{ 'is-active': exportScope === 'selected', 'is-disabled': !selectedCount }"
              @click="selectedCount && (exportScope = 'selected')"
            >
              <div class="scope-card-left">
                <div class="scope-title">导出当前勾选账号</div>
                <div class="scope-desc">仅导出表格中已勾选的 {{ selectedCount }} 个账号</div>
              </div>
              <div class="scope-badge">{{ selectedCount }} 条</div>
            </div>

            <div
              class="scope-card"
              :class="{ 'is-active': exportScope === 'all' }"
              @click="exportScope = 'all'"
            >
              <div class="scope-card-left">
                <div class="scope-title">导出全库账号 (跨页全量)</div>
                <div class="scope-desc">导出数据库中全部符合当前条件的 {{ total }} 个账号</div>
              </div>
              <div class="scope-badge badge-all">{{ total }} 条</div>
            </div>
          </div>
        </div>

        <!-- 3. 分卷设置 -->
        <div class="export-field-group">
          <div class="field-title-row">
            <span class="field-label">3. 分卷文件设置 (Chunking)</span>
            <span class="field-chunk-hint mono">
              {{ effectiveExportChunk > 0 ? `每 ${effectiveExportChunk} 条一卷 (预计打包 ${estimatedChunksCount} 个文件)` : '单文件不分卷' }}
            </span>
          </div>
          <div class="chunk-buttons-wrap">
            <button
              type="button"
              class="chunk-btn"
              :class="{ 'is-active': !exportCustomChunk && exportChunkSize === 0 }"
              @click="exportCustomChunk = false; exportChunkSize = 0"
            >
              不分卷 (单文件)
            </button>
            <button
              type="button"
              class="chunk-btn"
              :class="{ 'is-active': !exportCustomChunk && exportChunkSize === 50 }"
              @click="exportCustomChunk = false; exportChunkSize = 50"
            >
              50条 / 卷
            </button>
            <button
              type="button"
              class="chunk-btn"
              :class="{ 'is-active': !exportCustomChunk && exportChunkSize === 100 }"
              @click="exportCustomChunk = false; exportChunkSize = 100"
            >
              100条 / 卷
            </button>
            <button
              type="button"
              class="chunk-btn"
              :class="{ 'is-active': !exportCustomChunk && exportChunkSize === 200 }"
              @click="exportCustomChunk = false; exportChunkSize = 200"
            >
              200条 / 卷
            </button>
            <button
              type="button"
              class="chunk-btn"
              :class="{ 'is-active': !exportCustomChunk && exportChunkSize === 500 }"
              @click="exportCustomChunk = false; exportChunkSize = 500"
            >
              500条 / 卷
            </button>
            <button
              type="button"
              class="chunk-btn"
              :class="{ 'is-active': exportCustomChunk }"
              @click="exportCustomChunk = true"
            >
              自定义条数
            </button>
          </div>

          <div v-if="exportCustomChunk" class="custom-chunk-input-row">
            <span class="custom-chunk-lbl">自定义每卷条数：</span>
            <el-input-number
              v-model="exportCustomChunkSize"
              :min="10"
              :max="5000"
              :step="50"
              size="small"
              class="custom-chunk-num"
            />
            <span class="custom-chunk-unit">条/文件 (.zip 压缩包打包下载)</span>
          </div>
        </div>

        <!-- 4. 自定义分割线 / 分隔符设置 (仅对文本格式生效) -->
        <div v-if="isTextDelimiterFormat" class="export-field-group">
          <div class="field-title-row">
            <span class="field-label">4. 字段分隔符设置 (Delimiter)</span>
            <span class="field-delim-hint mono">当前: {{ effectiveExportDelimiter === '\t' ? '\\t (制表符)' : `"${effectiveExportDelimiter}"` }}</span>
          </div>
          <div class="chunk-buttons-wrap delim-buttons-wrap">
            <button
              v-for="pDelim in exportDelimiterPresets"
              :key="pDelim.value"
              type="button"
              class="chunk-btn"
              :class="{ 'is-active': exportDelimiterMode === pDelim.value }"
              @click="exportDelimiterMode = pDelim.value"
            >
              {{ pDelim.label }}
            </button>
          </div>

          <div v-if="exportDelimiterMode === 'custom'" class="custom-chunk-input-row">
            <span class="custom-chunk-lbl">输入自定义分隔符：</span>
            <el-input
              v-model="exportCustomDelimiter"
              placeholder="如 ---- 或 | 或 , 或 :::"
              size="small"
              class="custom-delim-input mono"
              style="width: 180px"
            />
            <span class="custom-chunk-unit">可填任意特殊符号</span>
          </div>

          <div class="delim-preview-banner">
            <span class="preview-tag">实时格式预览：</span>
            <span class="preview-text mono">{{ sampleDelimiterPreview }}</span>
          </div>
        </div>

        <!-- 5. 导出留痕备注 -->
        <div class="export-field-group">
          <div class="field-title-row">
            <span class="field-label">{{ isTextDelimiterFormat ? '5' : '4' }}. 导出备注与留痕记录 (Export Note)</span>
            <span class="field-tag-hint">记录去向 · 顶栏可随时筛选</span>
          </div>
          <el-input
            v-model="exportNoteInput"
            placeholder="可填一句备注方便日后回忆这批号的去向 (如：交付客户老王 · 2026-09-02)"
            clearable
            class="export-note-input mono"
          />
          <div class="preset-notes-bar">
            <span class="preset-note-title">快捷便签：</span>
            <span
              v-for="pNote in exportPresetNotes"
              :key="pNote"
              class="preset-note-chip"
              @click="appendPresetNote(pNote)"
            >
              + {{ pNote }}
            </span>
          </div>
          <div class="field-desc-tip">
            💡 导出后系统将自动为这批账号记录导出时间、格式与备注，可在顶栏「导出状态」中精准筛选或通过搜索框随时查找。
          </div>
        </div>
      </div>

      <template #footer>
        <div class="export-modal-footer">
          <div class="export-summary-text">
            <div class="summary-count-line">
              <span>即将导出 <strong>{{ exportTargetCount }}</strong> 个账号</span>
              <span v-if="effectiveExportChunk > 0" class="sub-summary">（分卷打包为 {{ estimatedChunksCount }} 个文件）</span>
            </div>
            <el-checkbox
              v-model="keepModalAfterExport"
              size="small"
              class="keep-modal-checkbox"
              @change="toggleKeepExportModal"
            >
              导出后保持弹窗（不刷新列表，方便连续导出多种格式）
            </el-checkbox>
          </div>
          <div class="footer-btn-group">
            <el-button @click="exportConfigModalVisible = false">
              {{ lastExportedInfo ? '完成并关闭' : '取消' }}
            </el-button>
            <el-button
              type="primary"
              class="export-submit-btn"
              :loading="exporting"
              :disabled="!exportTargetCount"
              @click="submitExport"
            >
              <el-icon><Download /></el-icon>
              <span>{{ lastExportedInfo ? '继续导出所选格式' : '立即导出并记录留痕' }}</span>
            </el-button>
          </div>
        </div>
      </template>
    </el-dialog>

    <!-- 批量导出文本预览弹窗 -->
    <el-dialog v-model="exportVisible" width="720px" top="8vh" class="macos-custom-dialog">
      <template #header>
        <div style="display: flex; align-items: center; gap: 12px">
          <span style="font-weight: 600">导出 · {{ exportLabel }}</span>
          <el-tag size="small" type="info">共 {{ exportCount }} 行</el-tag>
        </div>
      </template>
      <el-input
        :model-value="exportText" type="textarea" :rows="14" readonly
        class="mono export-area"
      />
      <template #footer>
        <el-button @click="copyText(exportText)">
          <el-icon><CopyDocument /></el-icon>复制全部
        </el-button>
        <el-button type="primary" @click="downloadExport">
          <el-icon><Download /></el-icon>下载 {{ exportFilename }}
        </el-button>
        <el-button
          type="danger" plain
          :loading="deletingExported"
          :disabled="!exportedEmails.length"
          @click="downloadAndDelete"
        >
          <el-icon><Delete /></el-icon>下载并删除这 {{ exportedEmails.length }} 个号
        </el-button>
      </template>
    </el-dialog>

    <!-- 查看凭证弹窗 (macOS 风格与布局) -->
    <el-dialog
      v-model="credVisible"
      width="780px"
      top="6vh"
      class="macos-terminal-dialog macos-cred-dialog"
      :close-on-click-modal="false"
    >
      <template #header>
        <div class="modal-header">
          <div class="window-dots">
            <span class="dot red"></span>
            <span class="dot yellow"></span>
            <span class="dot green"></span>
          </div>
          <div class="modal-title-info">
            <span class="modal-email">{{ credEmail }}</span>
            <el-tag size="small" type="info" effect="plain" class="modal-run-tag">
              凭证总览 ({{ credRows.length }} 项)
            </el-tag>
          </div>
          <div class="modal-header-actions" style="display: flex; gap: 8px;">
            <el-button size="small" type="primary" plain class="macos-copy-all-btn" @click="() => copySessionJson(credEmail)">
              <el-icon><Document /></el-icon>复制 Session JSON
            </el-button>
            <el-button size="small" class="macos-copy-all-btn" @click="copyAllJson">
              <el-icon><CopyDocument /></el-icon>复制全部 JSON
            </el-button>
          </div>
        </div>
      </template>

      <div class="cred-dialog-body">
        <div v-for="r in credRows" :key="r.key" class="cred-item-card">
          <div class="cred-item-header">
            <div class="cred-item-meta">
              <span
                class="cred-type-badge"
                :style="{ backgroundColor: getCredMeta(r.key).bg, color: getCredMeta(r.key).color }"
              >
                {{ getCredMeta(r.key).badge }}
              </span>
              <span class="cred-key-title mono">{{ r.key }}</span>
              <span class="cred-len-pill mono">{{ r.val.length }} chars</span>
            </div>
            <el-button size="small" text type="primary" class="cred-copy-btn" @click="copyText(r.val)">
              <el-icon><CopyDocument /></el-icon>复制
            </el-button>
          </div>
          <div class="cred-item-content mono" @click="copyText(r.val)" title="点击复制内容">
            {{ r.val }}
          </div>
        </div>
        <div v-if="!credRows.length" class="cred-empty-box">
          暂无可用凭证字段
        </div>
      </div>

      <template #footer>
        <div class="modal-footer">
          <span class="log-count-tip">凭证安全保存在本地 SQLite 数据库中</span>
          <div class="modal-footer-btns">
            <el-button size="small" type="primary" plain @click="() => copySessionJson(credEmail)">
              <el-icon><Document /></el-icon>复制 Session JSON
            </el-button>
            <el-button size="small" @click="copyAllJson">
              <el-icon><CopyDocument /></el-icon>复制全部 JSON
            </el-button>
            <el-button size="small" type="primary" @click="credVisible = false">
              关闭
            </el-button>
          </div>
        </div>
      </template>
    </el-dialog>

    <!-- 编辑凭证弹窗 -->
    <el-dialog v-model="editVisible" title="编辑凭证" width="540px" top="10vh" class="macos-custom-dialog">
      <el-alert
        type="warning" :closable="false" show-icon style="margin-bottom: 16px"
        title="仅修改本地记录，不会同步到 OpenAI"
        description="这里改密码不等于改了账号密码。填入的值会被登录流程直接使用。"
      />
      <el-form label-position="top" size="small">
        <el-form-item label="邮箱">
          <el-input :model-value="editEmail" class="mono" disabled />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="editPassword" class="mono" placeholder="留空表示该号无密码" />
        </el-form-item>
        <el-form-item label="2FA Secret">
          <el-input
            v-model="editSecret" class="mono"
            placeholder="base32，支持带空格/小写/otpauth:// 链接，会自动规范化"
          />
          <div class="hint" style="margin-top: 6px; line-height: 1.6">
            服务端取不回此值，覆盖后原 secret 永久丢失。清空则该号按无 2FA 处理。
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="editSaving" @click="saveEdit">保存</el-button>
      </template>
    </el-dialog>

    <!-- ──────────────── 2FA 实时动态验证码 (TOTP) 弹窗 ──────────────── -->
    <el-dialog
      v-model="totpModalVisible"
      title="2FA 实时动态验证码"
      width="460px"
      top="15vh"
      class="macos-custom-dialog"
      :close-on-click-modal="false"
      @closed="closeTotpModal"
    >
      <div v-loading="totpLoading" class="totp-card-container">
        <div class="totp-hero-box">
          <div class="totp-email-tag mono">{{ totpEmail }}</div>
          <div
            class="totp-digits-large mono"
            title="点击复制动态验证码"
            @click="copyText(totpCode, '2FA 动态码已复制')"
          >
            {{ totpCode ? (totpCode.slice(0, 3) + ' ' + totpCode.slice(3)) : '------' }}
          </div>
          <div class="totp-countdown-row">
            <el-progress
              type="circle"
              :percentage="Math.round((totpRemaining / 30) * 100)"
              :width="20"
              :stroke-width="3"
              :show-text="false"
              :color="totpRemaining <= 5 ? '#f87171' : '#5da4b1'"
            />
            <span>动态码 <b>{{ totpRemaining }}</b> 秒后自动刷新</span>
            <el-button size="small" text type="primary" :icon="Refresh" @click="fetchTotpCode">刷新</el-button>
          </div>
        </div>

        <div class="totp-meta-grid">
          <div class="meta-field-row" v-if="totpNextCode">
            <span class="meta-field-label">下一周期备用码:</span>
            <span class="meta-field-val mono">{{ totpNextCode }}</span>
          </div>
          <div class="meta-field-row">
            <span class="meta-field-label">TOTP Secret:</span>
            <div style="display: flex; align-items: center; gap: 6px">
              <span class="meta-field-val mono" style="font-size: 11px; max-width: 190px; overflow: hidden; text-overflow: ellipsis">{{ totpSecret }}</span>
              <el-button size="small" link :icon="CopyDocument" @click="copyText(totpSecret, '2FA Secret 已复制')" />
            </div>
          </div>
        </div>
      </div>
      <template #footer>
        <div style="display: flex; justify-content: space-between; align-items: center; width: 100%">
          <el-button size="small" @click="copyText(totpSecret, '2FA Secret 已复制')">
            <el-icon><CopyDocument /></el-icon>复制 Secret
          </el-button>
          <div style="display: flex; gap: 8px">
            <el-button size="small" type="primary" @click="copyText(totpCode, '2FA 动态码已复制')">
              <el-icon><CopyDocument /></el-icon>复制动态码 ({{ totpCode }})
            </el-button>
            <el-button size="small" @click="totpModalVisible = false">关闭</el-button>
          </div>
        </div>
      </template>
    </el-dialog>

    <!-- ──────────────── 现代化邮箱收件箱与验证码实时工作台 (Mailbox Studio) ──────────────── -->
    <el-dialog
      v-model="mailOtpModalVisible"
      title="邮箱收件箱与实时验证码 · Mailbox Studio"
      width="900px"
      top="6vh"
      class="macos-custom-dialog mailbox-studio-dialog"
      @closed="handleMailOtpModalClosed"
    >
      <div v-loading="mailOtpLoading" class="mailbox-studio-body">
        <!-- 顶部信息与控制操作栏 -->
        <div class="mb-top-bar">
          <div class="mb-account-info">
            <div class="mb-email-chip mono" @click="copyText(mailOtpEmail, '邮箱已复制')" title="点击复制邮箱">
              <el-icon class="email-icon"><Message /></el-icon>
              <span class="email-text">{{ mailOtpEmail }}</span>
              <el-icon class="copy-hint-icon"><CopyDocument /></el-icon>
            </div>
            <div class="mb-tags-group">
              <span class="mb-provider-pill" :class="mailOtpProvider === 'outlook' ? 'pill-ms' : (mailOtpProvider === 'remail' ? 'pill-remail' : 'pill-other')">
                {{ mailOtpProvider === 'outlook' ? 'Outlook 微软邮箱' : (mailOtpProvider === 'remail' ? '🍎 Remail 临时邮箱' : (mailOtpProvider || 'Mailbox')) }}
              </span>
              <span class="mb-protocol-pill" :class="mailOtpProtocol === 'graph' ? 'pill-graph' : (mailOtpProtocol === 'remail_pickup' ? 'pill-remail-pickup' : 'pill-imap')">
                {{ mailOtpProtocol === 'graph' ? '⚡ Graph API 极速直连' : (mailOtpProtocol === 'remail_pickup' ? '🍎 Remail 开放平台 API' : 'IMAP 协议') }}
              </span>
              <span v-if="mailOtpElapsed > 0" class="mb-elapsed-pill mono">
                ⚡ 响应 {{ mailOtpElapsed }}s
              </span>
              <span v-if="mailOtpLastUpdated" class="mb-time-pill mono">
                {{ mailOtpLastUpdated }} 更新
              </span>
            </div>
          </div>

          <div class="mb-actions-group">
            <el-tooltip content="开启后每 3.5 秒自动抓取最新邮件，适合刚发码等待收信的场景" placement="top">
              <div class="mb-polling-toggle" :class="{ 'is-active': mailOtpAutoPolling }">
                <span v-if="mailOtpAutoPolling" class="polling-live-dot"></span>
                <span class="polling-label">自动轮询</span>
                <el-switch
                  v-model="mailOtpAutoPolling"
                  size="small"
                  @change="toggleMailPolling"
                />
              </div>
            </el-tooltip>

            <el-checkbox v-model="mailOtpFilterOnlyOtp" size="small" class="mb-filter-checkbox">
              仅看含验证码
            </el-checkbox>

            <el-button
              size="small"
              type="primary"
              :loading="mailOtpLoading"
              class="mb-refresh-btn"
              @click="() => doFetchMailOtp(true)"
            >
              <el-icon><Refresh /></el-icon>极速检索
            </el-button>
          </div>
        </div>

        <!-- 错误或无凭证提示 -->
        <el-alert
          v-if="mailOtpError"
          type="warning"
          :closable="false"
          show-icon
          class="mb-alert-box"
          :title="mailOtpError"
        />

        <!-- 针对 Outlook/Hotmail 缺少凭证时提供一键补绑自愈卡片 -->
        <div
          v-if="mailOtpError && (mailOtpEmail.includes('@outlook.') || mailOtpEmail.includes('@hotmail.') || mailOtpEmail.includes('@live.') || mailOtpEmail.includes('@msn.'))"
          class="mb-credential-fix-card"
        >
          <div class="mb-credential-title">
            🔑 补充/绑定该账号的微软邮箱凭证 (单行 4 段式或独立密码)
          </div>
          <div style="display: flex; gap: 8px">
            <el-input
              v-model="mailOtpCustomLine"
              class="mono"
              size="small"
              placeholder="邮箱----密码----ClientID----RefreshToken"
            />
            <el-button size="small" type="primary" :loading="mailOtpLoading" @click="() => doFetchMailOtp(true)">
              保存并检索
            </el-button>
          </div>
          <div class="mb-credential-hint">
            💡 录入后将自动保存至号池及账号记录，后续查询与自动改密无需重复输入。
          </div>
        </div>

        <!-- 主视觉区：最新验证码超大 Hero 卡片 -->
        <div v-if="mailOtpCode" class="otp-hero-card">
          <div class="otp-hero-left">
            <div class="otp-hero-subtitle">
              <span class="pulse-emerald-dot"></span>
              <span>最新捕获 6 位验证码</span>
            </div>
            <div class="otp-huge-number-row" @click="copyText(mailOtpCode, '验证码已复制')" title="点击一键复制">
              <div class="otp-digits-display mono">
                <span v-for="(char, cIdx) in mailOtpCode" :key="cIdx" class="otp-char-box">{{ char }}</span>
              </div>
              <el-icon class="otp-copy-icon-hover"><CopyDocument /></el-icon>
            </div>
            <div class="otp-hero-tip">
              点击数字卡片或右侧按钮直接复制 · 适合 OpenAI / ChatGPT / 微软账户验证
            </div>
          </div>
          <div class="otp-hero-right">
            <el-button
              type="success"
              size="large"
              class="otp-copy-hero-btn"
              @click="copyText(mailOtpCode, '验证码已复制')"
            >
              <el-icon><CopyDocument /></el-icon>
              <span>一键复制验证码</span>
            </el-button>
          </div>
        </div>

        <!-- 未检索到验证码时的状态卡片 -->
        <div v-else-if="!mailOtpLoading && !mailOtpError" class="otp-empty-status-card">
          <div class="empty-icon-wrap">
            <el-icon :size="22"><Message /></el-icon>
          </div>
          <div class="empty-text-wrap">
            <div class="empty-title">当前邮件中暂未提取到 6 位纯数字验证码</div>
            <div class="empty-desc">
              如刚触发官方发信，微软邮件投递通常有 5~15 秒延迟。建议开启右上角「自动轮询」或稍后点击「极速检索」。
            </div>
          </div>
        </div>

        <!-- 邮件列表区 (Mail Stream) -->
        <div class="mb-mails-container">
          <div class="mb-section-header">
            <div class="mb-section-title">
              <span>📬 全部邮件流</span>
              <span class="mb-count-badge mono">{{ filteredMailMessages.length }} 封</span>
            </div>
            <div class="mb-section-tools">
              <el-input
                v-model="mailSearchQuery"
                size="small"
                placeholder="搜索邮件主题 / 发件人 / OTP..."
                clearable
                class="mb-search-input"
              >
                <template #prefix>
                  <el-icon><Search /></el-icon>
                </template>
              </el-input>
            </div>
          </div>

          <div v-if="filteredMailMessages.length > 0" class="mb-mails-list">
            <div
              v-for="(m, idx) in filteredMailMessages"
              :key="m.id || ('mail_' + idx)"
              class="mb-mail-card"
              :class="{ 'has-otp': Boolean(m.otp), 'is-expanded': expandedMailIds.has(m.id || ('mail_' + idx)) }"
            >
              <!-- 邮件简要信息主行 (点击折叠/展开) -->
              <div class="mb-mail-main-row" @click="toggleMailExpanded(m.id || ('mail_' + idx))">
                <div class="mb-mail-left-group">
                  <div
                    class="mb-sender-avatar"
                    :style="{ color: getSenderAvatar(m.from).color, background: getSenderAvatar(m.from).bg }"
                  >
                    {{ getSenderAvatar(m.from).text }}
                  </div>
                  <div class="mb-mail-text-block">
                    <div class="mb-mail-top-line">
                      <span class="mb-sender-name">{{ m.from }}</span>
                      <span class="mb-mail-date mono">{{ formatMailDate(m.date) || m.date }}</span>
                    </div>
                    <div class="mb-mail-subject-line">
                      <span class="mb-subject-text">{{ m.subject }}</span>
                    </div>
                    <div v-if="!expandedMailIds.has(m.id || ('mail_' + idx)) && m.snippet" class="mb-mail-snippet-line">
                      {{ m.snippet }}
                    </div>
                  </div>
                </div>

                <div class="mb-mail-right-group">
                  <span
                    v-if="m.otp"
                    class="mb-otp-badge mono"
                    @click.stop="copyText(m.otp, '验证码已复制')"
                    title="点击复制此验证码"
                  >
                    <el-icon><Key /></el-icon>
                    <span>{{ m.otp }}</span>
                  </span>
                  <div class="mb-expand-caret">
                    <el-icon><ArrowDown v-if="expandedMailIds.has(m.id || ('mail_' + idx))" /><ArrowRight v-else /></el-icon>
                  </div>
                </div>
              </div>

              <!-- 展开的正文内容区域 -->
              <div v-if="expandedMailIds.has(m.id || ('mail_' + idx))" class="mb-mail-detail-pane">
                <div class="mb-detail-toolbar">
                  <span class="mb-detail-hint">完整邮件正文与内容解析：</span>
                  <div style="display: flex; gap: 8px">
                    <el-button
                      v-if="m.otp"
                      size="small"
                      type="success"
                      plain
                      @click="copyText(m.otp, '验证码已复制')"
                    >
                      <el-icon><CopyDocument /></el-icon>复制验证码 {{ m.otp }}
                    </el-button>
                    <el-button
                      size="small"
                      @click="copyText(m.content || m.snippet, '邮件正文已复制')"
                    >
                      <el-icon><DocumentCopy /></el-icon>复制全文内容
                    </el-button>
                  </div>
                </div>
                <div class="mb-detail-content-box mono">
                  {{ m.content || m.snippet || '(该邮件无正文内容)' }}
                </div>
              </div>
            </div>
          </div>

          <div v-else class="mb-no-mails-wrap">
            <el-empty description="暂无符合条件的邮件记录" :image-size="60" />
          </div>
        </div>
      </div>

      <template #footer>
        <div class="mb-dialog-footer">
          <div class="mb-footer-left">
            <span class="mono" style="font-size: 11.5px; color: var(--el-text-color-secondary)">
              共 {{ mailOtpMessages.length }} 封邮件 · {{ mailOtpProtocol === 'remail_pickup' ? 'Remail 开放平台直连' : (mailOtpProtocol === 'graph' ? '微软官方 Graph API 毫秒级直连' : '邮件协议直连') }}
            </span>
          </div>
          <div class="mb-footer-right">
            <el-button @click="mailOtpModalVisible = false">关闭</el-button>
            <el-button
              v-if="mailOtpCode"
              type="primary"
              @click="copyText(mailOtpCode, '验证码已复制')"
            >
              <el-icon><CopyDocument /></el-icon>
              <span>复制验证码 ({{ mailOtpCode }})</span>
            </el-button>
          </div>
        </div>
      </template>
    </el-dialog>

    <!-- ──────────────── 快速补设/生成密码弹窗 ──────────────── -->
    <el-dialog
      v-model="repairPwVisible"
      title="设置 / 补设登录密码"
      width="500px"
      top="15vh"
      class="macos-custom-dialog"
    >
      class="macos-custom-dialog"
    >
      <el-alert
        type="info"
        :closable="false"
        show-icon
        style="margin-bottom: 14px"
        title="支持全自动联动 OpenAI 官方重置邮件并在服务端真正生效，或仅修改本地数据库记录。"
      />
      <el-form label-position="top" size="small">
        <el-form-item label="账号邮箱">
          <el-input :model-value="repairPwEmail" class="mono" disabled />
        </el-form-item>
        <el-form-item label="登录密码 (可自定义输入或一键随机生成)">
          <div style="display: flex; gap: 8px; width: 100%">
            <el-input v-model="repairPwVal" class="mono" placeholder="16位强随机密码" />
            <el-button @click="repairPwVal = generateRandomPassword(16)">随机生成</el-button>
          </div>
        </el-form-item>

        <el-form-item>
          <el-checkbox v-model="repairPwOfficial">
            🚀 同步在 OpenAI 官方服务端生效 (全自动申请重置并收信设密)
          </el-checkbox>
          <div style="margin-top: 4px; line-height: 1.5; color: var(--el-text-color-secondary); font-size: 11px;">
            💡 默认勾选：系统将全自动向 OpenAI 申请密码重置邮件、由对应邮箱渠道自动收取链接并提交完成官方生效；若取消勾选则仅修改本地数据库记录。
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="repairPwVisible = false">取消</el-button>
        <el-button type="primary" :loading="repairPwLoading" @click="submitRepairPassword">
          {{ repairPwOfficial ? '确认并在官方全自动设置密码' : '仅保存到本地数据库' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- ──────────────── 快速补绑 2FA 弹窗 ──────────────── -->
    <el-dialog
      v-model="repair2faVisible"
      title="账号快速补绑 2FA (TOTP)"
      width="480px"
      top="15vh"
      class="macos-custom-dialog"
    >
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        style="margin-bottom: 14px"
        title="将直接调用官方 API 进行 2FA 绑定，绑定后生成的 TOTP Secret 将自动存入数据库。"
      />
      <el-form label-position="top" size="small">
        <el-form-item label="账号邮箱">
          <el-input :model-value="repair2faEmail" class="mono" disabled />
        </el-form-item>
        <el-form-item label="Access Token 状态">
          <el-tag v-if="repair2faRow?.at_len" type="success" size="small">
            ✓ 具备 Access Token (长度: {{ repair2faRow.at_len }})
          </el-tag>
          <el-tag v-else type="danger" size="small">
            × 缺少 Access Token (需先刷新/重新获取 Token)
          </el-tag>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="repair2faVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="repair2faLoading"
          :disabled="!repair2faRow?.at_len"
          @click="submitRepair2FA"
        >
          立即绑定 2FA
        </el-button>
      </template>
    </el-dialog>

    <!-- ──────────────── 全渠道提炼模态任务台 (GCash/PIX/PayPal/iDEAL/UPI/Kakao/MoMo/TWINT/BLIK/Hosted 等) ──────────────── -->
    <ExtractTaskModal
      v-model="extractModalVisible"
      :channel="extractModalChannel"
      :emails="extractModalEmails"
      :auto-pay="extractModalAutoPay"
      @finished="load(false)"
    />
  </div>
</template>

<style scoped>
/* ──────────── 3D实体液态水晶玻璃拟态 (Liquid Glass / Crystal Glassmorphism) ──────────── */
.registered-page {
  --glass-bg: rgba(255, 255, 255, 0.65);
  --glass-border: rgba(255, 255, 255, 0.92);
  --glass-border-light: #ffffff;
  --glass-shadow:
    0 20px 48px -10px rgba(100, 116, 139, 0.12),
    0 6px 16px -3px rgba(100, 116, 139, 0.05);
  --glass-inset:
    inset 0 2px 2px 0 #ffffff,
    inset 1px 0 1.5px 0 rgba(255, 255, 255, 0.85),
    inset -1px 0 1.5px 0 rgba(255, 255, 255, 0.55),
    inset 0 -1.5px 2px 0 rgba(148, 163, 184, 0.12);
  --accent-primary: #0284c7;
  --text-main: #0f172a;
  --text-muted: #64748b;

  position: relative;
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 8px 12px;
  overflow: hidden;
  background: transparent !important;
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
}

/* ════════ 背景环境微光光球 (柔和马卡龙漫射光晕) ════════ */
.glass-ambient-sphere {
  position: absolute;
  border-radius: 50%;
  filter: blur(90px);
  pointer-events: none;
  z-index: 0;
  opacity: 0.45;
}
.sphere-1 {
  top: -40px;
  left: 15%;
  width: 420px;
  height: 420px;
  background: radial-gradient(circle, rgba(199, 210, 254, 0.6) 0%, rgba(165, 180, 252, 0.05) 70%);
  animation: floatOrb1 18s ease-in-out infinite alternate;
}
.sphere-2 {
  top: 30%;
  right: 10%;
  width: 460px;
  height: 460px;
  background: radial-gradient(circle, rgba(233, 213, 255, 0.5) 0%, rgba(192, 132, 252, 0.05) 70%);
  animation: floatOrb2 22s ease-in-out infinite alternate-reverse;
}
.sphere-3 {
  bottom: -60px;
  left: 35%;
  width: 480px;
  height: 480px;
  background: radial-gradient(circle, rgba(254, 215, 170, 0.4) 0%, rgba(251, 191, 36, 0.05) 70%);
  animation: floatOrb1 20s ease-in-out infinite alternate;
}
@keyframes floatOrb1 {
  0% { transform: translate(0, 0) scale(1); }
  100% { transform: translate(25px, 15px) scale(1.06); }
}
@keyframes floatOrb2 {
  0% { transform: translate(0, 0) scale(1); }
  100% { transform: translate(-20px, 20px) scale(1.05); }
}

/* ════════ 核心精髓：纯净光学晶体折射高光 (Pure Crystal Optical Flare) ════════ */
.liquid-caustic-flare {
  position: absolute;
  bottom: 0px;
  left: 15%;
  right: 15%;
  height: 1.5px;
  border-radius: 9999px;
  background: linear-gradient(90deg,
    transparent 0%,
    rgba(199, 210, 254, 0.35) 25%,
    rgba(255, 255, 255, 0.85) 50%,
    rgba(199, 210, 254, 0.35) 75%,
    transparent 100%
  );
  filter: blur(0.5px);
  box-shadow: 0 1px 8px rgba(199, 210, 254, 0.3);
  pointer-events: none;
  z-index: 3;
}

/* ════════ 核心精髓：顶部水滴凸透镜反光 (Curved Specular Liquid Sheen) ════════ */
.card-glass-specular {
  position: absolute;
  top: 1px;
  left: 2px;
  right: 2px;
  height: 48%;
  border-radius: 14px 14px 42% 42% / 14px 14px 18px 18px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.92) 0%, rgba(255, 255, 255, 0.28) 55%, transparent 100%);
  pointer-events: none;
  z-index: 2;
}

.macos-window-panel {
  position: relative;
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: rgba(255, 255, 255, 0.65);
  backdrop-filter: blur(28px) saturate(180%);
  -webkit-backdrop-filter: blur(28px) saturate(180%);
  border: 1.5px solid rgba(255, 255, 255, 0.92);
  border-top: 2.2px solid #ffffff;
  border-radius: 18px;
  overflow: hidden;
  box-shadow: var(--glass-shadow), var(--glass-inset);
  z-index: 1;
}

/* ════════════════ 顶部控制中枢 (Linear Command Deck · 厚水晶玻璃基底) ════════════════ */
.linear-command-deck {
  position: relative;
  z-index: 4;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: rgba(255, 255, 255, 0.5);
  backdrop-filter: blur(20px);
  border-bottom: 1px solid rgba(255, 255, 255, 0.7);
}

/* Row 1: Segmented Tabs + Search + Tools */
.command-deck-main {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 8px 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.6);
  background: transparent;
}

/* ──────────── 3D 实体水晶滑轨与胶囊选项卡 (1:1 复刻素材 2ac2e3a9c1fd2371a185add9ac5a345a.jpg 的 Tabs 质感) ──────────── */
.linear-segmented-rail {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 6px;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.45);
  backdrop-filter: blur(18px);
  -webkit-backdrop-filter: blur(18px);
  border: 2px solid rgba(255, 255, 255, 0.92);
  border-top: 2.5px solid #ffffff;
  border-bottom: 2px solid rgba(203, 213, 225, 0.65);
  box-shadow:
    0 8px 20px -4px rgba(15, 23, 42, 0.08),
    inset 0 2px 2.5px #ffffff,
    inset 0 -2px 3px rgba(148, 163, 184, 0.2);
  height: 38px;
  box-sizing: border-box;
  flex-shrink: 1;
  min-width: 0;
  max-width: 100%;
  overflow-x: auto;
  scrollbar-width: none;
}
.linear-segmented-rail::-webkit-scrollbar {
  display: none;
}

.segmented-tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0 11px;
  height: 28px;
  border-radius: 9999px;
  border: 1.2px solid rgba(255, 255, 255, 0.85);
  border-top: 1.5px solid #ffffff;
  background: rgba(255, 255, 255, 0.6);
  color: #334155;
  font-size: 11.5px;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.16s ease;
  white-space: nowrap;
  user-select: none;
  flex-shrink: 0;
  line-height: 1;
  box-shadow: 0 2px 5px rgba(15, 23, 42, 0.04), inset 0 1px 1px #ffffff;
}
.segmented-tab:hover {
  background: #ffffff;
  color: #0284c7;
  border-color: #38bdf8;
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(2, 132, 199, 0.18), inset 0 1.5px 1px #ffffff;
}
.segmented-tab.is-active {
  font-weight: 700;
  position: relative;
  overflow: hidden;
  background: linear-gradient(180deg, rgba(224, 242, 254, 0.96) 0%, rgba(186, 230, 253, 0.9) 50%, rgba(125, 211, 252, 0.82) 100%) !important;
  color: #0369a1 !important;
  border: 1.5px solid rgba(255, 255, 255, 0.98) !important;
  border-top: 2px solid #ffffff !important;
  border-bottom: 1.5px solid rgba(56, 189, 248, 0.65) !important;
  box-shadow:
    0 4px 14px -1px rgba(2, 132, 199, 0.28),
    0 2px 5px rgba(15, 23, 42, 0.04),
    inset 0 1.5px 1.5px #ffffff,
    inset 0 -1.5px 2px rgba(2, 132, 199, 0.15) !important;
  transform: translateY(-1px);
}
.segmented-tab.is-active::before {
  content: '';
  position: absolute;
  top: 1px;
  left: 8%;
  right: 8%;
  height: 42%;
  border-radius: 9999px 9999px 40% 40%;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.92) 0%, rgba(255, 255, 255, 0.25) 80%, transparent 100%);
  pointer-events: none;
}
.segmented-tab.is-active .tab-count-badge {
  background: rgba(255, 255, 255, 0.9);
  color: #0284c7;
  border: 1px solid rgba(255, 255, 255, 0.95);
  box-shadow: 0 1px 3px rgba(2, 132, 199, 0.12), inset 0 1px 1px #ffffff;
  font-weight: 700;
}

/* 激活标签右上角微型取消小叉 */
.tab-close-pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 9px;
  width: 13px;
  height: 13px;
  border-radius: 50%;
  background: rgba(90, 80, 70, 0.15);
  margin-left: 2px;
  opacity: 0.75;
  line-height: 1;
  transition: all 0.15s ease;
  color: #5c6e71;
}
.segmented-tab:hover .tab-close-pill {
  opacity: 1;
  background: rgba(90, 80, 70, 0.25);
  color: #243538;
}

/* 状态小圆点 (统一水墨与天水碧矿物色) */
.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}
.dot-emerald { background: #10b981; box-shadow: 0 0 6px #34d399; }
.dot-cyan { background: #06b6d4; box-shadow: 0 0 6px #22d3ee; }
.dot-amber { background: #f59e0b; box-shadow: 0 0 6px #fbbf24; }
.dot-rose { background: #ef4444; box-shadow: 0 0 6px #f87171; }
.dot-slate { background: #64748b; box-shadow: 0 0 6px #94a3b8; }

/* 3D 水晶微胶囊角标 */
.tab-count-badge {
  font-size: 11px;
  font-family: var(--el-font-family-monospace, monospace);
  padding: 1px 7px;
  height: 17px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 9999px;
  background: rgba(15, 23, 42, 0.06);
  color: #334155;
  font-weight: 700;
  line-height: 1;
}
.tab-count-badge.count-cyan {
  background: rgba(224, 242, 254, 0.9);
  color: #0369a1;
}
.tab-count-badge.count-amber {
  background: rgba(254, 243, 199, 0.9);
  color: #92400e;
}
.tab-count-badge.count-emerald {
  background: rgba(236, 253, 245, 0.9);
  color: #065f46;
}
.tab-count-badge.count-rose {
  background: rgba(254, 242, 242, 0.9);
  color: #991b1b;
}

/* 复合多选筛选指示标签 (3D 水晶胶囊) */
.rail-combo-indicator {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 9px;
  border-radius: 9999px;
  background: linear-gradient(180deg, rgba(238, 248, 255, 0.95) 0%, rgba(224, 242, 254, 0.85) 100%);
  border: 1px solid #38bdf8;
  font-size: 10.5px;
  color: #0284c7;
  font-weight: 700;
  margin-left: 4px;
  white-space: nowrap;
  flex-shrink: 0;
  line-height: 1;
  box-shadow: 0 2px 6px rgba(2, 132, 199, 0.15), inset 0 1px 1px #ffffff;
}
.combo-clear-btn {
  border: none;
  background: transparent;
  color: #0284c7;
  cursor: pointer;
  font-size: 10px;
  padding: 0;
  line-height: 1;
  text-decoration: underline;
  transition: color 0.15s ease;
}
.combo-clear-btn:hover {
  color: #dc2626;
}

.command-deck-search {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

/* ──────────── 3D 实体双层厚水晶搜索框 (1:1 复刻素材 2ac2e3a9c1fd2371a185add9ac5a345a.jpg 的 Search projects...) ──────────── */
.search-glass-chassis {
  position: relative;
  display: inline-flex;
  align-items: center;
  padding: 3px 4px;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.45);
  backdrop-filter: blur(18px);
  -webkit-backdrop-filter: blur(18px);
  border: 2px solid rgba(255, 255, 255, 0.95);
  border-top: 2.5px solid #ffffff;
  border-bottom: 2px solid rgba(203, 213, 225, 0.65);
  box-shadow:
    0 10px 24px -4px rgba(15, 23, 42, 0.08),
    0 3px 6px rgba(15, 23, 42, 0.04),
    inset 0 2px 2.5px #ffffff,
    inset 0 -2px 3px rgba(148, 163, 184, 0.22);
  transition: all 0.18s ease;
}
.search-glass-chassis:hover {
  background: rgba(255, 255, 255, 0.65);
  box-shadow:
    0 12px 28px -4px rgba(2, 132, 199, 0.15),
    0 4px 8px rgba(15, 23, 42, 0.06),
    inset 0 2px 2.5px #ffffff,
    inset 0 -2px 3px rgba(148, 163, 184, 0.25);
}

/* 底边光学棱镜火彩 (对齐素材图左下角金色晶体高光) */
.search-chassis-flare {
  position: absolute;
  bottom: -2.5px;
  left: 20px;
  width: 50px;
  height: 3px;
  border-radius: 9999px;
  background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.95) 30%, rgba(251, 191, 36, 0.85) 60%, transparent 100%);
  filter: blur(0.5px);
  pointer-events: none;
  box-shadow: 0 1px 6px rgba(251, 191, 36, 0.5);
}

.linear-search-input {
  width: 210px;
  transition: width 0.2s ease;
}
@media (max-width: 1400px) {
  .linear-search-input {
    width: 165px;
  }
}
.linear-search-input :deep(.el-input__wrapper) {
  border-radius: 9px !important;
  background: rgba(255, 255, 255, 0.92) !important;
  border: 1px solid rgba(226, 232, 240, 0.85) !important;
  box-shadow: inset 0 1.5px 3px rgba(15, 23, 42, 0.05) !important;
  height: 28px !important;
  padding: 0 8px 0 10px !important;
  transition: all 0.18s ease !important;
}
.linear-search-input :deep(.el-input__wrapper.is-focus) {
  border-color: #38bdf8 !important;
  background: #ffffff !important;
  box-shadow: 0 0 0 2px rgba(56, 189, 248, 0.25), inset 0 1.5px 3px rgba(15, 23, 42, 0.05) !important;
}
.linear-search-input :deep(.el-input__inner) {
  color: #0f172a;
  font-size: 11.5px;
  font-weight: 600;
}
.cmd-k-tag {
  font-size: 10px;
  padding: 1px 5px;
  border-radius: 4px;
  background: rgba(15, 23, 42, 0.06);
  color: #64748b;
  font-family: var(--el-font-family-monospace, monospace);
  font-weight: 700;
}

/* 3D 水晶微圆按钮 (对齐素材 2ac2e3a9c1fd2371a185add9ac5a345a.jpg 的圆形操作按钮) */
.ghost-tool-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  border: 1.2px solid rgba(255, 255, 255, 0.95);
  border-top: 1.6px solid #ffffff;
  border-bottom: 1.2px solid rgba(203, 213, 225, 0.7);
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.95) 0%, rgba(241, 245, 249, 0.8) 100%);
  color: #475569;
  cursor: pointer;
  transition: all 0.16s ease;
  font-size: 14px;
  box-shadow: 0 4px 10px -2px rgba(15, 23, 42, 0.08), inset 0 1px 1px #ffffff;
}
.ghost-tool-btn:hover {
  background: #ffffff;
  border-color: #0284c7;
  color: #0284c7;
  transform: translateY(-1.5px);
  box-shadow: 0 6px 14px -2px rgba(2, 132, 199, 0.25), inset 0 1px 1px #ffffff;
}
.ghost-tool-btn.is-active {
  background: linear-gradient(180deg, rgba(238, 248, 255, 0.98) 0%, rgba(224, 242, 254, 0.85) 100%);
  border-color: #38bdf8;
  color: #0284c7;
  box-shadow: 0 4px 12px rgba(2, 132, 199, 0.2), inset 0 1px 1px #ffffff;
}
.is-spinning {
  animation: spin 1s linear infinite;
}
@keyframes spin {
  100% { transform: rotate(360deg); }
}

/* ════════════════ 全量水平下拉筛选栏 (3D 实体水晶厚玻璃胶囊矩阵，对齐图片 31/32) ════════════════ */
.command-deck-filters-bar {
  padding: 8px 16px;
  background: rgba(255, 255, 255, 0.35);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border-bottom: 1.5px solid rgba(255, 255, 255, 0.85);
  display: flex;
  align-items: center;
  gap: 10px;
}

.filters-wrap-scroll {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 6px 10px;
  flex: 1;
  min-width: 0;
}

@media (max-width: 1100px) {
  .filters-wrap-scroll {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

/* 10 个筛选下拉框全面重塑为 3D 水晶微晶槽 (1:1 复刻素材 2ac2e3a9c1fd2371a185add9ac5a345a.jpg 的 Select 下拉胶囊) */
.filter-item-wrap {
  display: flex;
  align-items: center;
  gap: 5px;
  position: relative;
  background: rgba(255, 255, 255, 0.45);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  border: 2px solid rgba(255, 255, 255, 0.92);
  border-top: 2.2px solid #ffffff;
  border-bottom: 2px solid rgba(203, 213, 225, 0.6);
  border-radius: 12px;
  padding: 3px 6px 3px 10px;
  height: 34px;
  box-sizing: border-box;
  width: 100%;
  min-width: 0;
  box-shadow:
    0 6px 16px -3px rgba(15, 23, 42, 0.06),
    inset 0 1.5px 2px #ffffff,
    inset 0 -1.5px 2px rgba(148, 163, 184, 0.18);
  transition: all 0.16s ease;
}
.filter-item-wrap:hover {
  background: rgba(255, 255, 255, 0.7);
  border-color: rgba(56, 189, 248, 0.65);
  border-top-color: #ffffff;
  transform: translateY(-1px);
  box-shadow:
    0 8px 20px -3px rgba(2, 132, 199, 0.15),
    inset 0 1.5px 2px #ffffff,
    inset 0 -1.5px 2px rgba(148, 163, 184, 0.22);
}
.filter-item-wrap.is-filtered {
  background: rgba(240, 249, 255, 0.85);
  border-color: #38bdf8;
  border-top-color: #7dd3fc;
  box-shadow:
    0 6px 16px -3px rgba(2, 132, 199, 0.2),
    inset 0 1.5px 2px #ffffff,
    inset 0 -1.5px 2px rgba(2, 132, 199, 0.18);
}
.filter-item-wrap.is-filtered .filter-label {
  color: #0284c7;
  font-weight: 700;
}

.filter-label {
  font-size: 11px;
  font-weight: 700;
  color: #475569;
  white-space: nowrap;
  flex-shrink: 0;
  user-select: none;
}

/* 10个下拉框在Grid内自动撑满 100%，不再写死宽度，实现彻底的等宽纵向横向双对齐 */
.acct-select {
  flex: 1;
  min-width: 0;
  width: 100% !important;
}
.acct-select.acct-select-domain,
.acct-select.acct-select-country,
.acct-select.acct-select-sec,
.acct-select.acct-select-plan,
.acct-select.acct-select-oauth,
.acct-select.acct-select-export,
.acct-select.acct-select-health,
.acct-select.acct-select-extract,
.acct-select.acct-select-proxy {
  width: 100% !important;
}

.acct-select :deep(.el-select__wrapper) {
  background: rgba(255, 255, 255, 0.9) !important;
  border-radius: 8px !important;
  border: 1px solid rgba(226, 232, 240, 0.85) !important;
  box-shadow: inset 0 1px 2px rgba(15, 23, 42, 0.04) !important;
  padding: 0 6px !important;
  min-height: 24px !important;
  height: 24px !important;
  font-size: 11px !important;
  font-weight: 600 !important;
  color: #0f172a;
  width: 100%;
}
.acct-select :deep(.el-select__wrapper.is-focused) {
  border-color: #38bdf8 !important;
  background: #ffffff !important;
  box-shadow: 0 0 0 2px rgba(56, 189, 248, 0.2), inset 0 1px 2px rgba(15, 23, 42, 0.04) !important;
}
.acct-select :deep(.el-select__placeholder) {
  color: #64748b !important;
  font-size: 11.5px !important;
  font-weight: 600 !important;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.filter-reset-link-btn {
  height: 64px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1.5px solid rgba(239, 68, 68, 0.4);
  background: linear-gradient(180deg, rgba(254, 242, 242, 0.9) 0%, rgba(254, 226, 226, 0.75) 100%);
  color: #dc2626;
  border-radius: 12px;
  font-size: 11.5px;
  font-weight: 800;
  padding: 0 12px;
  box-sizing: border-box;
  cursor: pointer;
  box-shadow: 0 2px 8px rgba(220, 38, 38, 0.12), inset 0 1px 1px #ffffff;
  transition: all 0.16s ease;
  white-space: nowrap;
  flex-shrink: 0;
}
.filter-reset-link-btn:hover {
  background: #dc2626;
  color: #ffffff;
  border-color: #dc2626;
  transform: translateY(-1px);
  box-shadow: 0 4px 14px rgba(220, 38, 38, 0.35);
}

/* Row 3: Action Ribbon (3D 水晶果冻操作工具带 · 对齐图片 31/32) */
.command-deck-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 16px;
  background: rgba(255, 255, 255, 0.40);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border-bottom: 1.5px solid rgba(255, 255, 255, 0.85);
}
.actions-group-left, .actions-group-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* 操作菜单药丸按钮 (1:1 复刻素材 2ac2e3a9c1fd2371a185add9ac5a345a.jpg 的微晶按钮) */
.action-menu-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0 14px;
  height: 33px;
  box-sizing: border-box;
  border-radius: 9999px;
  border: 1.8px solid rgba(255, 255, 255, 0.95);
  border-top: 2.2px solid #ffffff;
  border-bottom: 1.8px solid rgba(203, 213, 225, 0.65);
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.95) 0%, rgba(241, 245, 249, 0.8) 100%);
  color: #1e293b;
  font-size: 11.5px;
  font-weight: 700;
  cursor: pointer;
  position: relative;
  overflow: hidden;
  box-shadow:
    0 4px 12px -2px rgba(15, 23, 42, 0.08),
    0 2px 4px rgba(15, 23, 42, 0.03),
    inset 0 1.5px 1.5px #ffffff,
    inset 0 -1.5px 2px rgba(148, 163, 184, 0.18);
  transition: all 0.16s ease;
}
.action-menu-btn::before {
  content: '';
  position: absolute;
  top: 1px;
  left: 12%;
  right: 12%;
  height: 40%;
  border-radius: 9999px 9999px 38% 38%;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.85) 0%, rgba(255, 255, 255, 0.1) 80%, transparent 100%);
  pointer-events: none;
}
.action-menu-btn:hover {
  background: #ffffff;
  border-color: rgba(56, 189, 248, 0.7);
  border-top-color: #ffffff;
  color: #0284c7;
  transform: translateY(-1px);
  box-shadow:
    0 8px 20px -3px rgba(2, 132, 199, 0.2),
    inset 0 2px 2px #ffffff,
    inset 0 -1.5px 2px rgba(148, 163, 184, 0.2);
}
.action-menu-btn.has-selected {
  border-color: #38bdf8;
  border-top-color: #7dd3fc;
  color: #0284c7;
  background: rgba(238, 248, 255, 0.9);
  box-shadow: 0 4px 12px rgba(2, 132, 199, 0.22), inset 0 1.5px 1px #ffffff;
}
.action-menu-btn.action-refresh-btn {
  border-color: rgba(255, 255, 255, 0.95);
}
.action-menu-btn.action-refresh-btn:hover {
  border-color: #38bdf8;
}

.linear-chunk-select {
  width: 105px;
}
.linear-chunk-select :deep(.el-input__wrapper) {
  background: rgba(255, 255, 255, 0.65);
  border: 1px solid rgba(255, 255, 255, 0.9);
  border-radius: 9999px;
  box-shadow: inset 0 1px 2px rgba(15, 23, 42, 0.04) !important;
  height: 32px;
  font-size: 11.5px;
  font-weight: 600;
  padding: 0 10px;
}
.linear-chunk-select :deep(.el-input__wrapper.is-focus) {
  border-color: #38bdf8;
  box-shadow: 0 0 0 2px rgba(56, 189, 248, 0.2), inset 0 1px 2px rgba(15, 23, 42, 0.04) !important;
}

/* 一键导出按钮：1:1 复刻素材 2ac2e3a9c1fd2371a185add9ac5a345a.jpg 的 "Search projects..." 蓝宝石 3D 药丸 */
.primary-export-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 0 18px;
  height: 33px;
  box-sizing: border-box;
  border-radius: 9999px;
  border: 1.5px solid rgba(255, 255, 255, 0.95) !important;
  border-top: 2px solid #ffffff !important;
  border-bottom: 1.8px solid rgba(2, 132, 199, 0.6) !important;
  background: linear-gradient(180deg, #38bdf8 0%, #0284c7 50%, #0369a1 100%) !important;
  color: #ffffff !important;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
  position: relative;
  overflow: hidden;
  transition: all 0.18s ease;
  box-shadow:
    0 6px 18px -2px rgba(2, 132, 199, 0.38),
    0 2px 6px rgba(15, 23, 42, 0.05),
    inset 0 1.8px 2px rgba(255, 255, 255, 0.9),
    inset 0 -1.8px 2.5px rgba(0, 0, 0, 0.18) !important;
}
.primary-export-btn::before {
  content: '';
  position: absolute;
  top: 1px;
  left: 8%;
  right: 8%;
  height: 42%;
  border-radius: 9999px 9999px 40% 40%;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.9) 0%, rgba(255, 255, 255, 0.2) 80%, transparent 100%);
  pointer-events: none;
}
.primary-export-btn:hover:not(:disabled) {
  transform: translateY(-1px) scale(1.02);
  box-shadow:
    0 8px 22px -2px rgba(2, 132, 199, 0.5),
    0 3px 8px rgba(2, 132, 199, 0.25),
    inset 0 2px 2px #ffffff !important;
}

/* ════════════════ 核心工作区 (Workspace Body) ════════════════ */
.registered-workspace-body {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 1fr;
  overflow: hidden;
  transition: grid-template-columns 0.2s ease;
}
.registered-workspace-body.with-details-panel {
  grid-template-columns: minmax(0, 1fr) 360px;
}

.center-table-pane {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  overflow: hidden;
  background: transparent !important;
}

.table-scroll-wrap {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  position: relative;
  display: flex;
  flex-direction: column;
  margin: 8px 14px 6px 14px;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.62);
  backdrop-filter: blur(24px) saturate(180%);
  -webkit-backdrop-filter: blur(24px) saturate(180%);
  border: 1.5px solid rgba(255, 255, 255, 0.92);
  border-top: 2px solid #ffffff;
  box-shadow: 0 16px 40px -8px rgba(100, 116, 139, 0.09), inset 0 1.5px 1.5px #ffffff;
}

.table-scroll-wrap :deep(.el-table) {
  height: 100% !important;
  border-radius: 14px;
  overflow: hidden;
}

/* ════════════════ 右侧全景档案 3D实体水晶抽屉 ════════════════ */
.details-panel-drawer {
  border-left: 1.5px solid rgba(255, 255, 255, 0.9);
  background: rgba(255, 255, 255, 0.75);
  backdrop-filter: blur(28px) saturate(190%);
  -webkit-backdrop-filter: blur(28px) saturate(190%);
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  box-shadow: -10px 0 32px rgba(15, 23, 42, 0.08);
  position: relative;
  z-index: 10;
}

/* 顶部标题栏 */
.details-heading {
  height: 46px;
  min-height: 46px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 14px;
  border-bottom: 1.5px solid rgba(255, 255, 255, 0.85);
  background: rgba(255, 255, 255, 0.55);
  backdrop-filter: blur(16px);
  flex-shrink: 0;
}
.details-head-title {
  display: flex;
  align-items: center;
  gap: 8px;
}
.dossier-pulse-beacon {
  width: 7px;
  height: 7px;
  border-radius: 999px;
  background: #0284c7;
  box-shadow: 0 0 8px #38bdf8;
  animation: beaconPulse 2s ease-in-out infinite;
}
@keyframes beaconPulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.45; transform: scale(0.85); }
}
.head-text-meta {
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.head-text-meta .title-main {
  font-size: 12.5px;
  font-weight: 700;
  color: #0f172a;
  letter-spacing: 0.3px;
  line-height: 1.2;
}
.head-text-meta .title-sub {
  font-size: 8.5px;
  font-weight: 600;
  color: #64748b;
  letter-spacing: 0.8px;
  line-height: 1;
}

.details-head-actions {
  display: flex;
  align-items: center;
  gap: 6px;
}
.dossier-tool-btn {
  border: 1.2px solid rgba(255, 255, 255, 0.95);
  border-top: 1.6px solid #ffffff;
  background: rgba(255, 255, 255, 0.85);
  color: #475569;
  cursor: pointer;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  transition: all 0.16s ease;
  box-shadow: 0 2px 6px rgba(15, 23, 42, 0.05), inset 0 1px 1px #ffffff;
}
.dossier-tool-btn:hover {
  color: #0284c7;
  background: #ffffff;
  border-color: #0284c7;
  transform: translateY(-1px);
  box-shadow: 0 4px 10px rgba(2, 132, 199, 0.2);
}
.dossier-tool-btn.close-btn:hover {
  color: #dc2626;
  background: #ffffff;
  border-color: #dc2626;
  box-shadow: 0 4px 10px rgba(220, 38, 38, 0.2);
}

/* 主内容滚动容器 */
.details-content {
  flex: 1;
  min-height: 0;
  padding: 12px 14px;
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: rgba(90, 80, 70, 0.2) transparent;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.details-content::-webkit-scrollbar {
  width: 4px;
}
.details-content::-webkit-scrollbar-thumb {
  background: rgba(90, 80, 70, 0.2);
  border-radius: 4px;
}

/* 统一档案卡片基类 (3D 水晶卡片，对齐素材 2ac2e3a9c1fd2371a185add9ac5a345a.jpg 的 Card) */
.dossier-card {
  border-radius: 12px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.92) 0%, rgba(248, 250, 252, 0.75) 100%);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  border: 1.5px solid rgba(255, 255, 255, 0.95);
  border-top: 2px solid #ffffff;
  box-shadow: 0 8px 24px -4px rgba(15, 23, 42, 0.08), inset 0 2px 2px #ffffff, inset 0 -2px 3px rgba(148, 163, 184, 0.15);
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  transition: all 0.18s cubic-bezier(0.16, 1, 0.3, 1);
}
.dossier-card:hover {
  border-color: rgba(56, 189, 248, 0.5);
  box-shadow: 0 12px 28px -4px rgba(2, 132, 199, 0.18), inset 0 2px 2px #ffffff;
}

/* 1. 账号核心身份卡片 (Profile Hero) */
.profile-hero-card {
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.04) 0%, rgba(18, 23, 34, 0.8) 100%);
  border-color: rgba(255, 255, 255, 0.08);
}
.profile-hero-top {
  display: flex;
  align-items: center;
  gap: 10px;
}
.provider-avatar-frame {
  width: 36px;
  height: 36px;
  border-radius: 9px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  flex-shrink: 0;
  border: 1px solid #ded5c6;
  box-shadow: 0 1px 3px rgba(70, 60, 50, 0.08);
}
.profile-meta-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.profile-email-row {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
}
.profile-email {
  font-size: 12.5px;
  font-weight: 700;
  color: #243538;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  letter-spacing: -0.2px;
  transition: color 0.15s ease;
}
.profile-email-row:hover .profile-email {
  color: #5da4b1;
}
.copy-hint-icon {
  font-size: 12px;
  color: #718487;
  opacity: 0;
  transition: all 0.15s ease;
}
.profile-email-row:hover .copy-hint-icon {
  opacity: 1;
  color: #5da4b1;
}
.profile-tags-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.dossier-tag {
  font-size: 10.5px;
  line-height: 1.2;
  padding: 1px 6px;
  border-radius: 4px;
  font-weight: 500;
}
.tag-country {
  background: #edf6f8;
  color: #28646e;
  border: 1px solid #bce2e7;
}
.tag-time {
  background: #f1ece2;
  color: #5c6e71;
  border: 1px solid #ddd5c7;
}
.tag-emerald {
  background: #edf6f8;
  color: #28646e;
  border: 1px solid #bce2e7;
}
.tag-cyan {
  background: #edf6f8;
  color: #28646e;
  border: 1px solid #bce2e7;
}

/* 卡片标题栏通用 */
.card-title-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 2px;
}
.title-left {
  display: flex;
  align-items: center;
  gap: 6px;
}
.title-text {
  font-size: 11.5px;
  font-weight: 600;
  color: #243538;
}
.title-code {
  font-size: 9px;
  font-weight: 600;
  color: #718487;
  letter-spacing: 0.6px;
}
.section-icon {
  width: 18px;
  height: 18px;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
}
.cred-icon {
  background: #fcf4e6;
  color: #8c5c16;
}
.net-icon {
  background: #edf6f8;
  color: #28646e;
}
.shield-icon {
  background: #edf6f8;
  color: #28646e;
}

/* 2. 实时 2FA 动态口令认证器 (Live TOTP) */
.totp-auth-card {
  background: #ffffff;
  border-color: rgba(93, 164, 177, 0.35);
  box-shadow: 0 2px 8px rgba(93, 164, 177, 0.12);
}
.live-dot-radar {
  width: 6px;
  height: 6px;
  border-radius: 999px;
  background: #5da4b1;
  box-shadow: 0 0 6px #5da4b1;
  animation: beaconPulse 1.5s ease-in-out infinite;
}
.totp-auth-card .title-text {
  color: #243538;
}
.totp-timer-chip {
  padding: 1px 7px;
  border-radius: 999px;
  font-size: 10px;
  font-weight: 700;
  display: flex;
  align-items: center;
  transition: all 0.2s ease;
}
.totp-timer-chip.urgency-normal {
  background: #edf6f8;
  color: #28646e;
  border: 1px solid #bce2e7;
}
.totp-timer-chip.urgency-warning {
  background: #fcf4e6;
  color: #8c5c16;
  border: 1px solid #eed8b5;
}
.totp-timer-chip.urgency-critical {
  background: #fdf0ee;
  color: #9b3730;
  border: 1px solid #f1c8c4;
  animation: beaconPulse 0.8s ease-in-out infinite;
}

/* 动态码内嵌屏幕显示槽 */
.totp-display-screen {
  background: #edf6f8;
  border: 1px solid #bce2e7;
  box-shadow: inset 0 1px 3px rgba(93, 164, 177, 0.15);
  border-radius: 7px;
  padding: 8px 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  cursor: pointer;
  transition: all 0.15s ease;
  user-select: none;
}
.totp-display-screen:hover {
  border-color: #5da4b1;
  background: #e1f1f3;
}
.totp-code-val {
  display: flex;
  align-items: center;
  gap: 8px;
}
.code-seg {
  font-size: 24px;
  font-weight: 800;
  letter-spacing: 2px;
  color: #20555e;
  line-height: 1;
}
.code-dot {
  font-size: 22px;
  line-height: 1;
  color: #5da4b1;
  font-weight: 700;
  user-select: none;
  margin: 0 2px;
}
.totp-action-hover {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: #28646e;
  background: #dbeef2;
  border: 1px solid #bce2e7;
  padding: 2px 8px;
  border-radius: 4px;
  opacity: 0.85;
  transition: all 0.15s ease;
}
.totp-display-screen:hover .totp-action-hover {
  opacity: 1;
  background: #cfe8ed;
}

.totp-progress-track {
  height: 3px;
  border-radius: 999px;
  background: #ded5c6;
  overflow: hidden;
}
.totp-progress-bar {
  height: 100%;
  border-radius: 999px;
  transition: width 1s linear;
}
.totp-progress-bar.urgency-normal {
  background: #5da4b1;
}
.totp-progress-bar.urgency-warning {
  background: #d49432;
}
.totp-progress-bar.urgency-critical {
  background: #c7564d;
}

.totp-next-hint {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 10px;
  color: #718487;
  padding-top: 1px;
}
.totp-next-hint .next-val {
  color: #28646e;
  font-weight: 600;
}

/* 未绑定 2FA 引导卡片 */
.totp-empty-card {
  background: #fcf5e8;
  border-color: #eed4a8;
}
.empty-card-inner {
  display: flex;
  align-items: center;
  gap: 10px;
}
.empty-shield-icon {
  width: 28px;
  height: 28px;
  border-radius: 6px;
  background: #faedd6;
  color: #8c5c16;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  flex-shrink: 0;
}
.empty-totp-info {
  flex: 1;
  min-width: 0;
}
.empty-totp-info .empty-title {
  font-size: 11.5px;
  font-weight: 600;
  color: #243538;
}
.empty-totp-info .empty-desc {
  font-size: 10px;
  color: #718487;
  margin-top: 1px;
}
.btn-repair {
  flex-shrink: 0;
  font-size: 10.5px;
  padding: 3px 8px;
  border-radius: 5px;
  border: 1px solid #eed4a8;
  background: #faedd6;
  color: #8c5c16;
  cursor: pointer;
  transition: all 0.15s ease;
  font-weight: 600;
}
.btn-repair:hover {
  background: #f5e4c6;
  border-color: #d49432;
  color: #734808;
}

/* 卡片行插槽通用 */
.card-rows-wrap {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.dossier-row-slot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 4px 6px;
  border-radius: 6px;
  background: #faf6ee;
  transition: background 0.15s ease;
}
.dossier-row-slot:hover {
  background: #f2ece0;
}
.slot-label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: #65777a;
  flex-shrink: 0;
}
.slot-badge {
  font-size: 9.5px;
  padding: 1px 5px;
  border-radius: 3px;
  font-weight: 600;
  line-height: 1.2;
}
.slot-badge.badge-success {
  background: #edf6f8;
  color: #28646e;
  border: 1px solid #bce2e7;
}
.slot-badge.badge-warn {
  background: #fcf4e6;
  color: #8c5c16;
  border: 1px solid #eed8b5;
}
.slot-badge.badge-danger {
  background: #fdf0ee;
  color: #9b3730;
  border: 1px solid #f1c8c4;
}

.slot-value-box {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  max-width: 65%;
  justify-content: flex-end;
}
.secret-text {
  font-size: 11.5px;
  color: #243538;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.truncate-text {
  max-width: 140px;
}
.slot-icon-btn {
  width: 20px;
  height: 20px;
  border-radius: 4px;
  border: 1px solid #ded5c6;
  background: #ffffff;
  color: #5c6e71;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  font-size: 11px;
  transition: all 0.15s ease;
  flex-shrink: 0;
}
.slot-icon-btn:hover {
  background: rgba(255, 255, 255, 0.1);
  color: #fff;
  border-color: rgba(255, 255, 255, 0.18);
}
.slot-text-action-btn {
  font-size: 10px;
  font-weight: 600;
  padding: 2px 7px;
  border-radius: 4px;
  border: 1px solid rgba(56, 189, 248, 0.3);
  background: rgba(56, 189, 248, 0.1);
  color: #38bdf8;
  cursor: pointer;
  transition: all 0.15s ease;
}
.slot-text-action-btn:hover {
  background: rgba(56, 189, 248, 0.2);
  border-color: rgba(56, 189, 248, 0.5);
  color: #7dd3fc;
}
.slot-text-action-btn.btn-rose {
  border-color: rgba(244, 63, 94, 0.3);
  background: rgba(244, 63, 94, 0.1);
  color: #fb7185;
}
.slot-text-action-btn.btn-rose:hover {
  background: rgba(244, 63, 94, 0.2);
  border-color: rgba(244, 63, 94, 0.5);
}

.note-edit-cell {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: #cbd5e1;
  cursor: pointer;
  padding: 1px 5px;
  border-radius: 4px;
  transition: all 0.15s ease;
}
.note-edit-cell:hover {
  background: rgba(255, 255, 255, 0.08);
  color: #38bdf8;
}
.note-pen-icon {
  font-size: 11px;
  color: #64748b;
}
.note-edit-cell:hover .note-pen-icon {
  color: #38bdf8;
}
.note-content {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 140px;
}

/* 5. 安全态势与健康评分卡片 (Health Diagnostic) */
.health-diagnostic-card {
  background: rgba(18, 23, 34, 0.55);
}
.health-grade-chip {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 1px 7px;
  border-radius: 999px;
  font-size: 10px;
  font-weight: 700;
}
.health-grade-chip.grade-success {
  background: rgba(93, 164, 177, 0.12);
  color: #5da4b1;
  border: 1px solid rgba(93, 164, 177, 0.3);
}
.health-grade-chip.grade-info {
  background: rgba(14, 165, 233, 0.12);
  color: #38bdf8;
  border: 1px solid rgba(14, 165, 233, 0.3);
}
.health-grade-chip.grade-warn {
  background: rgba(245, 158, 11, 0.12);
  color: #fbbf24;
  border: 1px solid rgba(245, 158, 11, 0.3);
}
.health-tri-meter {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 5px;
  padding: 2px 0;
}
.meter-bar {
  height: 20px;
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.06);
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s ease;
}
.meter-bar .meter-label {
  font-size: 9.5px;
  font-weight: 600;
  color: #475569;
  letter-spacing: 0.5px;
}
.meter-bar.is-active {
  background: rgba(93, 164, 177, 0.14);
  border-color: rgba(93, 164, 177, 0.35);
}
.meter-bar.is-active .meter-label {
  color: #5da4b1;
}

.health-tips-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.health-tip-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 10.5px;
  padding: 3px 6px;
  border-radius: 4px;
  line-height: 1.3;
}
.health-tip-item .tip-dot {
  width: 4px;
  height: 4px;
  border-radius: 999px;
  flex-shrink: 0;
}
.health-tip-item.tip-success {
  background: rgba(93, 164, 177, 0.08);
  color: #5da4b1;
}
.health-tip-item.tip-success .tip-dot {
  background: #5da4b1;
}
.health-tip-item.tip-info {
  background: rgba(14, 165, 233, 0.08);
  color: #38bdf8;
}
.health-tip-item.tip-info .tip-dot {
  background: #38bdf8;
}
.health-tip-item.tip-warning {
  background: rgba(245, 158, 11, 0.08);
  color: #fbbf24;
}
.health-tip-item.tip-warning .tip-dot {
  background: #fbbf24;
}

/* 空状态 (Empty State) */
.details-empty-state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 30px 24px;
  text-align: center;
}
.empty-radar-wrap {
  position: relative;
  width: 72px;
  height: 72px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 14px;
}
.radar-circle {
  position: absolute;
  border-radius: 999px;
  border: 1px solid rgba(93, 164, 177, 0.15);
}
.radar-circle.circle-1 { width: 32px; height: 32px; }
.radar-circle.circle-2 { width: 52px; height: 52px; border-color: rgba(93, 164, 177, 0.1); }
.radar-circle.circle-3 { width: 72px; height: 72px; border-color: rgba(93, 164, 177, 0.05); }
.empty-radar-icon {
  font-size: 24px;
  color: #5da4b1;
  opacity: 0.65;
}
.details-empty-state .empty-title {
  font-size: 12.5px;
  font-weight: 600;
  color: #cbd5e1;
  margin-bottom: 4px;
}
.details-empty-state .empty-desc {
  font-size: 11px;
  color: #64748b;
  line-height: 1.5;
}

/* 底部多维操作底栏 (Footer Actions) */
.dossier-footer-actions {
  padding: 10px 12px;
  border-top: 1px solid rgba(255, 255, 255, 0.07);
  background: rgba(8, 11, 16, 0.85);
  backdrop-filter: blur(16px);
  display: flex;
  flex-direction: column;
  gap: 7px;
  flex-shrink: 0;
}
.footer-btn {
  height: 30px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  font-size: 11.5px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
  user-select: none;
}
.footer-btn.btn-primary {
  background: linear-gradient(180deg, #5da4b1 0%, #059669 100%);
  color: #ffffff;
  border: 1px solid #5da4b1;
  box-shadow: 0 2px 8px rgba(93, 164, 177, 0.35);
}
.footer-btn.btn-primary:hover {
  background: linear-gradient(180deg, #5da4b1 0%, #5da4b1 100%);
  box-shadow: 0 4px 14px rgba(93, 164, 177, 0.5);
  transform: translateY(-1px);
}
.footer-sub-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
}
.footer-btn.btn-secondary {
  height: 27px;
  border: 1px solid rgba(255, 255, 255, 0.09);
  background: rgba(255, 255, 255, 0.04);
  color: #cbd5e1;
  font-size: 11px;
}
.footer-btn.btn-secondary:hover {
  background: rgba(255, 255, 255, 0.09);
  border-color: rgba(255, 255, 255, 0.18);
  color: #ffffff;
}
.footer-btn.btn-mail:hover {
  color: #38bdf8;
  border-color: rgba(56, 189, 248, 0.35);
  background: rgba(56, 189, 248, 0.08);
}

.table-footer-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 46px;
  min-height: 46px;
  padding: 0 16px;
  border-top: 1.5px solid rgba(255, 255, 255, 0.9);
  background: rgba(255, 255, 255, 0.65);
  backdrop-filter: blur(24px) saturate(180%);
  -webkit-backdrop-filter: blur(24px) saturate(180%);
  box-shadow: 0 -4px 16px rgba(15, 23, 42, 0.04);
  flex-shrink: 0;
  z-index: 10;
}
.footer-left-info {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11.5px;
  color: #64748b;
  font-weight: 500;
}
.selected-badge {
  color: #0284c7;
  font-weight: 700;
}
.selected-badge b {
  color: #0284c7;
  font-weight: 800;
}
.total-badge {
  color: #64748b;
  font-weight: 600;
}
.footer-pagination-right {
  display: flex;
  align-items: center;
  gap: 10px;
}

/* 3D 水晶胶囊自定义页码输入框 */
.page-size-custom {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: #0369a1;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.95) 0%, rgba(241, 245, 249, 0.75) 100%);
  border: 1.2px solid rgba(255, 255, 255, 0.95);
  border-top: 1.6px solid #ffffff;
  padding: 2px 10px;
  border-radius: 9999px;
  box-shadow: 0 4px 12px -2px rgba(15, 23, 42, 0.06), inset 0 1.5px 1px #ffffff;
  white-space: nowrap;
}
.page-size-custom-label {
  font-weight: 700;
  color: #0f172a;
}
.page-size-custom-unit {
  color: #64748b;
  font-weight: 600;
}
.page-size-custom-input {
  width: 96px;
}
.page-size-custom-input :deep(.el-input-number__decrease),
.page-size-custom-input :deep(.el-input-number__increase) {
  width: 18px;
  border-color: rgba(203, 213, 225, 0.6) !important;
  color: #475569 !important;
  background: rgba(255, 255, 255, 0.7) !important;
}
.page-size-custom-input :deep(.el-input__wrapper) {
  padding-left: 6px;
  padding-right: 22px;
  height: 24px;
  background: rgba(255, 255, 255, 0.9) !important;
  border: 1px solid rgba(203, 213, 225, 0.7) !important;
  box-shadow: inset 0 1px 2px rgba(15, 23, 42, 0.05) !important;
  border-radius: 9999px !important;
}
.page-size-custom-input :deep(.el-input__inner) {
  font-size: 11px;
  height: 22px;
  text-align: center;
  font-family: var(--el-font-family-monospace, monospace);
  color: #0f172a;
  font-weight: 700;
}

/* 3D 水晶透镜分页控件 */
.octopus-pagination :deep(.el-pagination__total) {
  font-size: 11.5px;
  color: #64748b;
  font-weight: 600;
  margin-right: 8px;
}
.octopus-pagination :deep(.el-pagination__sizes) {
  margin-right: 8px;
}
.octopus-pagination :deep(.el-select .el-input__wrapper) {
  background: rgba(255, 255, 255, 0.9) !important;
  border: 1.2px solid rgba(255, 255, 255, 0.95) !important;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04) !important;
  height: 26px;
  border-radius: 9999px !important;
}
.octopus-pagination :deep(.btn-prev),
.octopus-pagination :deep(.btn-next),
.octopus-pagination :deep(.el-pager li) {
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.95) 0%, rgba(241, 245, 249, 0.8) 100%) !important;
  border: 1.2px solid rgba(255, 255, 255, 0.95) !important;
  border-top: 1.6px solid #ffffff !important;
  color: #334155 !important;
  min-width: 26px;
  height: 26px;
  line-height: 24px;
  border-radius: 7px;
  font-size: 11px;
  font-weight: 700;
  margin: 0 2px;
  box-shadow: 0 4px 10px -2px rgba(15, 23, 42, 0.08), inset 0 1px 1px #ffffff !important;
  transition: all 0.16s ease !important;
}
.octopus-pagination :deep(.el-pager li:hover),
.octopus-pagination :deep(.btn-prev:hover),
.octopus-pagination :deep(.btn-next:hover) {
  background: #ffffff !important;
  border-color: #0284c7 !important;
  color: #0284c7 !important;
  transform: translateY(-1px);
  box-shadow: 0 6px 14px -2px rgba(2, 132, 199, 0.25) !important;
}
.octopus-pagination :deep(.el-pager li.is-active) {
  background: linear-gradient(180deg, #38bdf8 0%, #0284c7 100%) !important;
  border-color: #38bdf8 !important;
  color: #ffffff !important;
  font-weight: 800 !important;
  box-shadow: 0 6px 16px -2px rgba(2, 132, 199, 0.45), inset 0 1.5px 1px rgba(255, 255, 255, 0.8) !important;
}
.octopus-pagination :deep(.el-pagination__jump) {
  font-size: 11.5px;
  color: #64748b;
  margin-left: 8px;
}
.octopus-pagination :deep(.el-pagination__editor.el-input .el-input__wrapper) {
  background: rgba(255, 255, 255, 0.9) !important;
  border: 1px solid rgba(203, 213, 225, 0.8) !important;
  box-shadow: inset 0 1px 2px rgba(15, 23, 42, 0.05) !important;
  height: 24px;
  border-radius: 6px !important;
}

.action-group-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.selection-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 8px;
  border-radius: 6px;
  background: #edf6f8;
  border: 1px solid #bce2e7;
  font-size: 11.5px;
  color: #28646e;
}
.selection-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #5da4b1;
  box-shadow: 0 0 4px #5da4b1;
}
.clear-sel-btn {
  border: none;
  background: transparent;
  color: #28646e;
  font-size: 11px;
  cursor: pointer;
  padding: 0 2px;
  opacity: 0.8;
}
.clear-sel-btn:hover {
  opacity: 1;
}

.action-dropdown-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  height: 28px;
  border-radius: 6px;
  border: 1px solid #ded5c6;
  background: #ffffff;
  color: #35474a;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}
.action-dropdown-btn:hover {
  background: #fbf8f2;
  border-color: #5da4b1;
  color: #28646e;
}
.action-dropdown-btn.is-active {
  background: #edf6f8;
  border-color: #5da4b1;
  color: #28646e;
}
.action-dropdown-btn .arrow-ico {
  font-size: 10px;
  opacity: 0.6;
}

.action-filter-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  height: 28px;
  border-radius: 6px;
  border: 1px solid #ded5c6;
  background: #ffffff;
  color: #5c6e71;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}
.action-filter-btn:hover {
  background: #fbf8f2;
  border-color: #cfc4b2;
  color: #243538;
}
.action-filter-btn.has-active {
  background: #fcf4e6;
  border-color: #eed4a8;
  color: #8c5c16;
}
.filter-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 10px;
  font-weight: 700;
  width: 15px;
  height: 15px;
  border-radius: 50%;
  background: #d49432;
  color: #ffffff;
}

.action-group-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.linear-chunk-select {
  width: 100px;
}
.linear-chunk-select :deep(.el-select__wrapper) {
  border-radius: 6px;
  font-size: 11.5px;
  height: 28px;
  background: #ffffff;
  border: 1px solid #ded5c6;
}

.linear-export-main-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 12px;
  height: 28px;
  border-radius: 6px;
  border: none;
  background: #5da4b1;
  color: #ffffff;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
  box-shadow: 0 2px 6px rgba(93, 164, 177, 0.3);
}
.linear-export-main-btn:hover {
  background: #488793;
  box-shadow: 0 3px 10px rgba(93, 164, 177, 0.4);
}
.linear-export-main-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.linear-reset-filter-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  height: 28px;
  border-radius: 6px;
  border: 1px solid #f1c8c4;
  background: #fdf0ee;
  color: #9b3730;
  font-size: 11px;
  cursor: pointer;
}
.linear-reset-filter-btn:hover {
  background: #fae4e2;
  border-color: #ebaba5;
}

/* 高级多维筛选 Popover */
.advanced-filters-body {
  padding: 4px;
}
.adv-filter-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
  padding-bottom: 6px;
  border-bottom: 1px solid #ded5c6;
  font-size: 12.5px;
  font-weight: 600;
  color: #243538;
}
.adv-filter-grid {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.adv-filter-field {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.adv-label {
  font-size: 11px;
  color: #65777a;
}

/* ──────────── 资产与权益聚合列 (Status & Entitlements) ──────────── */
.status-cell-wrap {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  flex-wrap: wrap;
}

.status-micro-pill {
  display: inline-flex;
  align-items: center;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 10.5px;
  font-weight: 600;
  line-height: 1.2;
  cursor: default;
  transition: all 0.15s ease;
}

.status-micro-pill.success {
  background: #edf6f8;
  color: #28646e;
  border: 1px solid #bce2e7;
}
.status-micro-pill.primary {
  background: #edf6f8;
  color: #28646e;
  border: 1px solid #bce2e7;
}
.status-micro-pill.warning {
  background: #fcf4e6;
  color: #8c5c16;
  border: 1px solid #eed8b5;
}
.status-micro-pill.danger {
  background: #fdf0ee;
  color: #9b3730;
  border: 1px solid #f1c8c4;
}
.status-micro-pill.info {
  background: #f1ece2;
  color: #6e7f82;
  border: 1px solid #ddd5c7;
}
.status-micro-pill[title] {
  cursor: pointer;
}
.status-micro-pill:hover {
  transform: translateY(-1px);
}

.status-fresh-hint {
  font-size: 11px;
  color: #7a8b8e;
}

/* 表格 Hover 指示线 */
.macos-table :deep(.el-table__row) {
  transition: background-color 0.15s ease, transform 0.1s ease;
  position: relative;
}
.macos-table :deep(.el-table__row:hover) {
  background-color: #edf5f6 !important;
}

/* ──────────── 邮箱列全新现代化卡片 (高信息密度双行) ──────────── */
.email-cell-modern {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 2px 0;
}

.email-primary-line {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.provider-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 6px;
  font-size: 11px;
  flex-shrink: 0;
  user-select: none;
}

.email-text-wrap {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
}

.email-address {
  font-size: 12.5px;
  font-weight: 600;
  color: #243538;
  letter-spacing: -0.01em;
  transition: color 0.15s ease;
}
.email-text-wrap:hover .email-address {
  color: #5da4b1;
  text-decoration: underline;
}

.email-copy-ico {
  font-size: 12px;
  color: #76888b;
  opacity: 0;
  transition: all 0.15s ease;
}
.email-text-wrap:hover .email-copy-ico {
  opacity: 1;
  color: #5da4b1;
}

.email-meta-subline {
  display: flex;
  align-items: center;
  gap: 6px;
  padding-left: 30px;
  flex-wrap: wrap;
}

.export-mark-chip {
  display: inline-flex;
  align-items: center;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 500;
  background: #edf6f8;
  color: #28646e;
  border: 1px solid #bce2e7;
  cursor: pointer;
  transition: all 0.15s ease;
}
.export-mark-chip:hover {
  background: #dbeef2;
  border-color: #5da4b1;
}

.fresh-pure-chip {
  display: inline-flex;
  align-items: center;
  padding: 1px 5px;
  border-radius: 4px;
  font-size: 10px;
  color: #6e7f82;
  background: #f1ece2;
  border: 1px solid #ddd5c7;
}

.reg-ip-chip {
  display: inline-flex;
  align-items: center;
  padding: 1px 5px;
  border-radius: 4px;
  font-size: 10px;
  color: #4b5d60;
  background: #f1ece2;
  border: 1px solid #ddd5c7;
  cursor: pointer;
  transition: all 0.15s ease;
}
.reg-ip-chip:hover {
  color: #28646e;
  border-color: #5da4b1;
  background: #edf6f8;
}

/* ──────────── 出口国家与城市 ──────────── */
.geo-cell-modern {
  display: inline-flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
}
.geo-badge-modern {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: 5px;
  background: #edf6f8;
  border: 1px solid #bce2e7;
  font-size: 11.5px;
  font-weight: 500;
  color: #28646e;
}
.geo-badge-modern.is-hot {
  background: #edf6f8;
  border-color: #5da4b1;
  color: #28646e;
}
.geo-city-text {
  font-size: 10px;
  color: #718487;
}

/* ──────────── 密码 / 2FA 防护列 ──────────── */
.sec-col-wrapper {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  flex-wrap: wrap;
}
.sec-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 7px;
  border-radius: 5px;
  font-size: 11px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}
.sec-badge:hover {
  transform: translateY(-1px);
}
.sec-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
}
.sec-pwd-ok {
  background: #edf6f8;
  color: #28646e;
  border: 1px solid #bce2e7;
}
.sec-pwd-ok .sec-dot { background: #5da4b1; }
.sec-pwd-no {
  background: #fcf4e6;
  color: #8c5c16;
  border: 1px solid #eed8b5;
}
.sec-pwd-no .sec-dot { background: #d49432; }
.sec-2fa-ok {
  background: #edf6f8;
  color: #28646e;
  border: 1px solid #bce2e7;
}
.sec-2fa-ok .sec-dot { background: #5da4b1; }
.sec-2fa-no {
  background: #fcf4e6;
  color: #8c5c16;
  border: 1px solid #eed8b5;
}
.sec-2fa-no .sec-dot { background: #d49432; }

/* ──────────── 注册与时间线 ──────────── */
.time-cell-modern {
  display: inline-flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
}
.time-primary {
  font-size: 12px;
  color: #243538;
  font-weight: 500;
}
.time-ago-sub {
  font-size: 10px;
  color: #718487;
}

/* ──────────── 操作列按钮 ──────────── */
.row-actions-modern {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.row-act-btn {
  height: 24px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  background: #ffffff;
  border: 1px solid #ded5c6;
  color: #35474a;
  transition: all 0.15s ease;
}
.row-act-btn:hover {
  background: #f4ede1;
  border-color: #cfc4b2;
  color: #243538;
}
.row-act-btn.btn-totp-act {
  color: #28646e;
  border-color: #bce2e7;
  background: #edf6f8;
}
.row-act-btn.btn-totp-act:hover {
  background: #dbeef2;
  border-color: #5da4b1;
}
.row-act-btn.btn-otp-act {
  color: #28646e;
  border-color: #bce2e7;
  background: #edf6f8;
}
.row-act-btn.btn-otp-act:hover {
  background: #dbeef2;
  border-color: #5da4b1;
}

.registered-page :deep(.el-button--primary:not(.is-link):not(.is-text)),
.registered-page :deep(.el-button--primary:not(.is-link):not(.is-text):hover:not(:disabled)) {
  background: var(--el-color-primary) !important;
  box-shadow: none !important;
  transform: none !important;
}
.registered-page :deep(.el-button--success:not(.is-link):not(.is-text)),
.registered-page :deep(.el-button--success:not(.is-link):not(.is-text):hover:not(:disabled)) {
  background: var(--el-color-success) !important;
  box-shadow: none !important;
}
.registered-page :deep(.el-button--danger:not(.is-link):not(.is-text)),
.registered-page :deep(.el-button--danger:not(.is-link):not(.is-text):hover:not(:disabled)) {
  background: var(--el-color-danger) !important;
  box-shadow: none !important;
}
.registered-page :deep(.el-button--warning:not(.is-link):not(.is-text)),
.registered-page :deep(.el-button--warning:not(.is-link):not(.is-text):hover:not(:disabled)) {
  background: var(--el-color-warning) !important;
  box-shadow: none !important;
}

.toolbar-left, .toolbar-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.extract-dropdown-menu {
  min-width: 175px;
}
.dropdown-group-title {
  padding: 4px 12px;
  font-size: 11px;
  font-weight: 700;
  color: var(--el-text-color-secondary);
}
.divider-title {
  margin-top: 6px;
  border-top: 1px solid var(--el-border-color-lighter);
  padding-top: 6px;
}

.page-title-badge {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-right: 4px;
}
.page-title-badge .dot-live {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--el-color-primary);
}
.page-title-badge .title {
  font-size: 14px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}
.page-title-badge .badge-total {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  background: var(--el-fill-color-light);
  padding: 1px 6px;
  border-radius: 10px;
}

.macos-btn {
  border-radius: 6px;
  font-size: 12px;
  padding: 6px 10px;
}

.macos-input.search-input {
  width: 175px;
}
.macos-input.search-input :deep(.el-input__wrapper) {
  border-radius: 6px;
}

.macos-select.filter-select {
  width: 126px;
}
.macos-select.filter-select.plan-filter {
  width: 130px;
}
.macos-select.filter-select.sec-filter {
  width: 122px;
}
.macos-select.filter-select.extract-filter {
  width: 120px;
}
.macos-select.filter-select.oauth-filter {
  width: 130px;
}
.macos-select.filter-select.domain-filter {
  width: 155px;
}
.macos-select.proxy-select {
  width: 165px;
}

.clear-filter-btn {
  font-size: 11.5px;
  padding: 4px 8px;
  height: 28px;
  border-radius: 6px;
  font-weight: 600;
  transition: all 0.2s ease;
}
.clear-filter-btn:hover {
  transform: translateY(-1px);
}

/* 按钮组 */
.macos-btn-group {
  display: inline-flex;
  background: var(--el-fill-color-light);
  padding: 2px;
  border-radius: 6px;
  border: 1px solid var(--el-border-color-lighter);
}
.macos-btn-group :deep(.el-button) {
  margin: 0;
  border: none;
  background: transparent;
  padding: 5px 10px;
  height: 26px;
  font-size: 12px;
  border-radius: 4px;
}
.macos-btn-group :deep(.el-button:hover:not(:disabled)) {
  background: var(--el-bg-color);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
}
.macos-btn-group.highlight-group :deep(.el-button:hover:not(:disabled)) {
  color: var(--el-color-primary);
}
.macos-btn-group.danger-group :deep(.el-button:hover:not(:disabled)) {
  color: var(--el-color-danger);
}

.oa-action-btn {
  border-radius: 6px;
  font-size: 12px;
  font-weight: 600;
  background: #059669;
  border: none;
  box-shadow: none;
}
.oa-action-btn:hover:not(:disabled) {
  background: #047857;
}

.copy-at-action-btn {
  background: var(--el-color-primary) !important;
  box-shadow: none !important;
}
.copy-at-action-btn:hover:not(:disabled) {
  background: var(--el-color-primary-dark-2) !important;
}

/* ──────────── 中间表格区域 ──────────── */
.macos-table-container {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

:deep(.macos-table) {
  font-size: 12.5px;
}
:deep(.macos-table th.el-table__cell) {
  background: var(--el-fill-color-lighter);
  color: var(--el-text-color-secondary);
  font-weight: 600;
  font-size: 12px;
}
:deep(.macos-table .el-table__row) {
  transition: all 0.15s ease;
  position: relative;
}
:deep(.macos-table .el-table__row:hover > td) {
  background: rgba(255, 255, 255, 0.035) !important;
}
:deep(.macos-table .el-table__row:hover td:first-child) {
  box-shadow: inset 3px 0 0 0 var(--el-color-primary);
}

/* 邮箱复制胶囊 */
.macos-tag-btn.copy-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  padding: 1px 6px;
  border-radius: 4px;
  color: var(--el-text-color-primary);
  cursor: pointer;
  outline: none;
  font-size: 12px;
  transition: all 0.15s ease;
  max-width: 100%;
}
.macos-tag-btn.copy-btn:hover {
  background: var(--el-color-primary-light-9);
  border-color: var(--el-color-primary-light-7);
  color: var(--el-color-primary);
}
.copy-btn .copy-ico {
  font-size: 11px;
  opacity: 0.5;
  transition: opacity 0.15s;
}
.copy-btn:hover .copy-ico { opacity: 1; }

.geo-badge {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 11px;
}
.geo-badge.geo-hot {
  border-color: rgba(93, 164, 177, 0.4);
  background: rgba(93, 164, 177, 0.08);
}
.geo-badge.geo-hot .geo-country {
  color: #5da4b1;
}
.geo-country { font-weight: 600; color: var(--el-color-primary); }
.geo-city { color: var(--el-text-color-regular); }

.macos-tag-btn.ip-btn {
  font-family: var(--el-font-family-monospace, monospace);
  font-size: 11.5px;
}

.macos-tag {
  font-size: 11.5px;
  border-radius: 4px;
}

.token-len-cell {
  font-size: 12px;
}
.token-len-cell.link {
  color: var(--el-color-primary);
  cursor: pointer;
  padding: 2px 6px;
  border-radius: 4px;
  background: var(--el-color-primary-light-9);
  transition: all 0.15s ease;
  display: inline-flex;
  align-items: center;
  gap: 3px;
}
.token-len-cell.link:hover {
  background: var(--el-color-primary-light-8);
  color: var(--el-color-primary-dark-2);
  transform: translateY(-1px);
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.08);
}
.token-len-cell.st-cell {
  color: var(--el-color-success);
  background: var(--el-color-success-light-9);
}
.token-len-cell.st-cell:hover {
  background: var(--el-color-success-light-8);
  color: var(--el-color-success-dark-2);
}
.token-len-cell.rt-cell {
  color: var(--el-color-warning);
  background: var(--el-color-warning-light-9);
}
.token-len-cell.rt-cell:hover {
  background: var(--el-color-warning-light-8);
  color: var(--el-color-warning-dark-2);
}
.cell-copy-ico {
  font-size: 11px;
  opacity: 0.6;
}
.token-len-cell.link:hover .cell-copy-ico {
  opacity: 1;
}

.mono-date {
  font-family: var(--el-font-family-monospace, monospace);
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.row-actions {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 2px;
}
.row-actions :deep(.el-button) {
  padding: 4px 6px;
  font-size: 12px;
}

/* ──────────── 底部状态与分页栏 ──────────── */
.macos-footer-bar {
  padding: 8px 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-top: 1px solid var(--el-border-color-lighter);
  background: var(--el-fill-color-blank);
  flex-shrink: 0;
}
.footer-status-left {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.selected-badge {
  color: var(--el-color-primary);
  font-weight: 500;
}

/* ──────────── 3D实体液态水晶 OAuth 控制台弹窗样式 (Liquid Glass) ──────────── */
:deep(.oa-custom-dialog) {
  border-radius: 18px !important;
  overflow: hidden !important;
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.92) 0%, rgba(255, 255, 255, 0.72) 50%, rgba(248, 250, 252, 0.88) 100%) !important;
  backdrop-filter: blur(32px) saturate(210%) !important;
  -webkit-backdrop-filter: blur(32px) saturate(210%) !important;
  border: 1.8px solid rgba(255, 255, 255, 0.95) !important;
  border-top: 2.5px solid #ffffff !important;
  box-shadow: 0 32px 72px -12px rgba(15, 23, 42, 0.22), 0 10px 24px rgba(15, 23, 42, 0.06), inset 0 2.5px 2px #ffffff !important;
}
:deep(.oa-custom-dialog .el-dialog__header) {
  padding: 14px 20px 12px !important;
  margin-right: 0 !important;
  border-bottom: 1px solid rgba(255, 255, 255, 0.75) !important;
  background: rgba(255, 255, 255, 0.45) !important;
  backdrop-filter: blur(16px) !important;
}
:deep(.oa-custom-dialog .el-dialog__body) {
  padding: 14px 20px !important;
  background: transparent !important;
}
:deep(.oa-custom-dialog .el-dialog__footer) {
  padding: 12px 20px !important;
  border-top: 1px solid rgba(255, 255, 255, 0.75) !important;
  background: rgba(255, 255, 255, 0.5) !important;
  backdrop-filter: blur(16px) !important;
}

.oa-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
}
.oa-header-title {
  display: flex;
  align-items: center;
  gap: 8px;
}
.oa-title-badge {
  background: linear-gradient(180deg, #38bdf8 0%, #0284c7 100%);
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  padding: 2.5px 8px;
  border-radius: 9999px;
  letter-spacing: 0.5px;
  box-shadow: 0 2px 6px rgba(2, 132, 199, 0.35), inset 0 1px 1px rgba(255, 255, 255, 0.8);
}
.oa-title-badge.plus-badge {
  background: linear-gradient(180deg, #38bdf8 0%, #0284c7 100%);
}
.oa-title-badge.health-badge {
  background: linear-gradient(180deg, #34d399 0%, #059669 100%);
}
.oa-title-badge.sec-badge {
  background: linear-gradient(180deg, #c084fc 0%, #7c3aed 100%);
}
.health-action-btn {
  background: linear-gradient(180deg, #38bdf8 0%, #0284c7 100%) !important;
  border: 1.5px solid rgba(255, 255, 255, 0.95) !important;
  border-radius: 9999px !important;
  color: #fff !important;
  font-weight: 700;
  box-shadow: 0 4px 10px rgba(2, 132, 199, 0.3) !important;
}
.feat-action-btn {
  background: linear-gradient(180deg, #38bdf8 0%, #0284c7 100%) !important;
  border: 1.5px solid rgba(255, 255, 255, 0.95) !important;
  border-radius: 9999px !important;
  color: #fff !important;
  font-weight: 700;
  box-shadow: 0 4px 10px rgba(2, 132, 199, 0.3) !important;
}
.oa-title-badge.feat-badge {
  background: linear-gradient(180deg, #38bdf8 0%, #0284c7 100%);
}
.oa-title-text {
  font-size: 14.5px;
  font-weight: 800;
  color: #0f172a;
  letter-spacing: -0.01em;
}
.oa-target-pill {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 9px;
  border-radius: 9999px;
  font-size: 11px;
  font-weight: 700;
  background: rgba(2, 132, 199, 0.12);
  color: #0369a1;
  border: 1px solid rgba(2, 132, 199, 0.28);
  box-shadow: inset 0 1px 1px rgba(255, 255, 255, 0.8);
}
.oa-config-toggle-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 28px;
  padding: 0 11px;
  border-radius: 9999px;
  font-size: 11.5px;
  font-weight: 700;
  border: 1.2px solid rgba(255, 255, 255, 0.95);
  background: rgba(255, 255, 255, 0.85);
  color: #334155;
  cursor: pointer;
  box-shadow: 0 2px 6px rgba(15, 23, 42, 0.04);
  transition: all 0.16s ease;
}
.oa-config-toggle-btn:hover {
  background: #ffffff;
  border-color: #0284c7;
  color: #0284c7;
  transform: translateY(-1px);
}

.oa-dialog-container {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

/* 配置卡片 */
.oa-config-card {
  background: rgba(255, 255, 255, 0.72);
  backdrop-filter: blur(20px);
  border: 1.5px solid rgba(255, 255, 255, 0.95);
  border-top: 2px solid #ffffff;
  border-radius: 14px;
  padding: 12px;
  box-shadow: 0 6px 18px rgba(15, 23, 42, 0.05), inset 0 1.5px 1px #ffffff;
}
.oa-proxy-input {
  font-size: 11px;
}
.oa-proxy-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 4px;
}
.plus-config-desc {
  font-size: 11.5px;
  color: var(--el-text-color-secondary);
  line-height: 1.5;
  margin-top: 8px;
}

/* Plus KPI 看板 */
.plus-kpi-grid {
  display: grid;
  grid-template-columns: repeat(5, 1fr) 2fr;
  gap: 8px;
  align-items: center;
}
.plus-kpi-card {
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  padding: 6px 10px;
  display: flex;
  flex-direction: column;
}
.plus-kpi-card .kpi-label {
  font-size: 10.5px;
  color: var(--el-text-color-secondary);
  margin-bottom: 2px;
}
.plus-kpi-card .kpi-num {
  font-size: 15px;
  font-weight: 700;
  font-family: var(--el-font-family-monospace, monospace);
  line-height: 1.1;
}
.plus-kpi-card .kpi-sub-hint {
  margin-top: 2px;
  font-size: 10px;
  color: var(--el-color-danger);
  line-height: 1.2;
}
.plus-kpi-card.hit-active {
  border-color: var(--el-color-primary-light-5);
  background: var(--el-color-primary-light-9);
}
.plus-kpi-card.hit-pro {
  border-color: rgba(244, 63, 94, 0.45);
  background: rgba(244, 63, 94, 0.1);
}
.plus-kpi-card.hit-team {
  border-color: rgba(2, 132, 199, 0.45);
  background: rgba(2, 132, 199, 0.08);
}
.text-pro {
  color: #f43f5e;
  font-weight: 700;
}
.text-team {
  color: #0284c7;
  font-weight: 700;
}
.plus-kpi-card.hit-promo {
  border-color: rgba(93, 164, 177, 0.4);
  background: rgba(93, 164, 177, 0.08);
}
.plus-kpi-card.card-warn {
  border-color: rgba(239, 68, 68, 0.4);
  background: rgba(239, 68, 68, 0.08);
}
.plus-progress-cell {
  padding-left: 6px;
}

/* OA KPI 栏目 */
.oa-kpi-bar {
  display: grid;
  grid-template-columns: repeat(4, 1fr) 2fr;
  gap: 8px;
  align-items: center;
}
.oa-kpi-item {
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  padding: 6px 10px;
  display: flex;
  flex-direction: column;
}
.oa-kpi-item .kpi-label {
  font-size: 10.5px;
  color: var(--el-text-color-secondary);
  margin-bottom: 2px;
}
.oa-kpi-item .kpi-val {
  font-size: 15px;
  font-weight: 700;
  font-family: var(--el-font-family-monospace, monospace);
}
.oa-kpi-item.kpi-hit {
  border-color: rgba(93, 164, 177, 0.4);
  background: rgba(93, 164, 177, 0.08);
}
.oa-kpi-item.kpi-hit .highlight { color: #5da4b1; }
.oa-kpi-item.kpi-warn {
  border-color: rgba(239, 68, 68, 0.4);
  background: rgba(239, 68, 68, 0.08);
}

/* 核心双栏监控 */
.oa-monitor-split {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  height: 380px;
}
.oa-table-box {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  overflow: hidden;
  height: 100%;
}
.oa-terminal-box {
  background: #141418;
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.oa-terminal-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  background: #1e1e24;
  border-bottom: 1px solid #2a2a34;
}
.terminal-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.terminal-dot.red { background: #ff5f56; }
.terminal-dot.yellow { background: #ffbd2e; }
.terminal-dot.green { background: #27c93f; }
.terminal-title {
  font-size: 11px;
  color: #94a3b8;
  flex: 1;
  margin-left: 4px;
}
.terminal-clear-btn {
  padding: 0;
  height: auto;
  font-size: 11px;
  color: #94a3b8;
}

.oa-terminal-body {
  flex: 1;
  padding: 10px 12px;
  overflow-y: auto;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, Courier, monospace;
  font-size: 11.5px;
  line-height: 1.55;
  color: #d1d5db;
  word-break: break-all;
  white-space: pre-wrap;
}

.terminal-line { margin-bottom: 2px; }
.terminal-line.log-hit { color: #4ade80; font-weight: 500; }
.terminal-line.log-miss { color: #94a3b8; }
.terminal-line.log-err { color: #f87171; }
.terminal-line.log-task { color: #60a5fa; }
.terminal-empty {
  color: #64748b;
  font-style: italic;
  padding: 30px 0;
  text-align: center;
}

.pulse-dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #f59e0b;
  animation: pulse-ring 1.3s infinite;
  flex-shrink: 0;
}
.running-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: #d97706;
}

.oa-dialog-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.footer-tip {
  font-size: 12px;
}
.running-indicator {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #d97706;
  font-weight: 500;
}
.finished-indicator {
  color: #5da4b1;
}

.start-gradient-btn {
  background: #059669;
  border: none;
}
.start-gradient-btn:hover:not(:disabled) {
  background: #047857;
}

.plus-table-box {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  overflow: hidden;
  height: 340px;
}

.plus-table-box.health-table-box {
  height: 360px;
}

:deep(.plus-dialog) {
  border-radius: 12px;
  overflow: hidden;
}

/* ──────────── 单账号详细日志终端弹窗 (与授权界面 oa-custom-dialog 玻璃拟态 100% 深度对齐) ──────────── */
:deep(.macos-terminal-dialog) {
  border-radius: 20px !important;
  overflow: hidden !important;
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.94) 0%, rgba(255, 255, 255, 0.78) 50%, rgba(248, 250, 252, 0.9) 100%) !important;
  backdrop-filter: blur(32px) saturate(210%) !important;
  -webkit-backdrop-filter: blur(32px) saturate(210%) !important;
  border: 2px solid rgba(255, 255, 255, 0.95) !important;
  border-top: 2.5px solid #ffffff !important;
  box-shadow: 0 32px 72px -12px rgba(15, 23, 42, 0.22), 0 12px 28px rgba(15, 23, 42, 0.08), inset 0 2.5px 2px #ffffff !important;
}
:deep(.macos-terminal-dialog .el-dialog__header) {
  padding: 16px 22px 14px !important;
  margin-right: 0 !important;
  border-bottom: 1.5px solid rgba(255, 255, 255, 0.85) !important;
  background: rgba(255, 255, 255, 0.5) !important;
  backdrop-filter: blur(16px) !important;
  -webkit-backdrop-filter: blur(16px) !important;
}
:deep(.macos-terminal-dialog .el-dialog__body) {
  padding: 16px 22px !important;
  background: transparent !important;
}
:deep(.macos-terminal-dialog .el-dialog__footer) {
  padding: 14px 22px !important;
  border-top: 1.5px solid rgba(255, 255, 255, 0.85) !important;
  background: rgba(255, 255, 255, 0.55) !important;
  backdrop-filter: blur(16px) !important;
  -webkit-backdrop-filter: blur(16px) !important;
}

.modal-header {
  display: flex;
  align-items: center;
  gap: 14px;
  width: 100%;
}
.window-dots {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 3.5px 8px;
  border-radius: 9999px;
  background: rgba(255, 255, 255, 0.65);
  border: 1.2px solid rgba(255, 255, 255, 0.95);
  border-top: 1.5px solid #ffffff;
  box-shadow: 0 2px 6px rgba(15, 23, 42, 0.04), inset 0 1px 1.5px #ffffff;
}
.dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  position: relative;
  box-shadow:
    inset 0 1.2px 1.5px rgba(255, 255, 255, 0.9),
    inset 0 -1.2px 1.8px rgba(0, 0, 0, 0.25),
    0 1px 3px rgba(0, 0, 0, 0.12);
}
.dot.red { background: radial-gradient(circle at 35% 30%, #ff857d 0%, #ff5f56 70%, #d83a30 100%); }
.dot.yellow { background: radial-gradient(circle at 35% 30%, #ffe07a 0%, #ffbd2e 70%, #d99b1a 100%); }
.dot.green { background: radial-gradient(circle at 35% 30%, #68f07e 0%, #27c93f 70%, #1da331 100%); }

.modal-title-info {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
}
.modal-email {
  font-size: 13px;
  font-weight: 700;
  color: #0f172a;
  font-family: var(--el-font-family-monospace, monospace);
  letter-spacing: -0.01em;
  padding: 3px 10px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.6);
  border: 1px solid rgba(226, 232, 240, 0.85);
  transition: all 0.15s ease;
  cursor: pointer;
}
.modal-email:hover {
  color: #0284c7;
  background: rgba(224, 242, 254, 0.7);
  border-color: rgba(56, 189, 248, 0.6);
}
.modal-run-tag {
  display: inline-flex;
  align-items: center;
  height: 24px;
  padding: 0 12px;
  border-radius: 9999px;
  font-size: 11px;
  font-weight: 700;
  background: linear-gradient(180deg, rgba(236, 253, 245, 0.95) 0%, rgba(209, 250, 229, 0.85) 100%);
  border: 1.2px solid rgba(255, 255, 255, 0.95);
  border-top: 1.6px solid #ffffff;
  border-bottom: 1.2px solid rgba(167, 243, 208, 0.9);
  color: #065f46;
  box-shadow: 0 2px 6px rgba(16, 185, 129, 0.18), inset 0 1.2px 1.5px #ffffff;
  position: relative;
  overflow: hidden;
}

/* 终端座舱：浅色清透微晶冰釉槽，与授权界面的浅色卡片/表格完美统一 */
.modal-terminal-wrap {
  position: relative;
  border-radius: 14px;
  overflow: hidden;
  border: 1.5px solid rgba(255, 255, 255, 0.95);
  border-top: 2px solid #ffffff;
  border-bottom: 1.5px solid rgba(203, 213, 225, 0.65);
  background: rgba(255, 255, 255, 0.62) !important;
  backdrop-filter: blur(24px) saturate(180%) !important;
  -webkit-backdrop-filter: blur(24px) saturate(180%) !important;
  box-shadow:
    inset 0 2px 5px rgba(15, 23, 42, 0.04),
    inset 0 -1.5px 2px #ffffff,
    0 8px 24px -4px rgba(15, 23, 42, 0.05);
}
.modal-terminal-body {
  height: 390px;
  background: transparent !important;
  padding: 16px 20px;
  overflow-y: auto;
  font-family: "JetBrains Mono", "SF Mono", Consolas, "Liberation Mono", Menlo, monospace !important;
  font-size: 12.5px;
  line-height: 1.8;
  color: #334155;
  word-break: break-all;
  white-space: pre-wrap;
}

.modal-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
}
.log-count-tip {
  display: inline-flex;
  align-items: center;
  height: 28px;
  padding: 0 12px;
  border-radius: 9999px;
  background: rgba(255, 255, 255, 0.75);
  border: 1.2px solid rgba(226, 232, 240, 0.9);
  border-top: 1.5px solid #ffffff;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.03), inset 0 1px 1px #ffffff;
  font-size: 12px;
  font-weight: 600;
  color: #475569;
  font-family: var(--el-font-family-monospace, monospace);
}
.modal-footer-btns {
  display: flex;
  align-items: center;
  gap: 10px;
}

/* ──────────── macOS 凭证弹窗精致卡片风格 ──────────── */
.cred-dialog-body {
  max-height: 58vh;
  overflow-y: auto;
  padding: 14px 18px;
  background: #141418;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.cred-item-card {
  background: #1a1a22;
  border: 1px solid #282834;
  border-radius: 8px;
  padding: 8px 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  transition: border-color 0.15s ease, background 0.15s ease;
}
.cred-item-card:hover {
  border-color: #3e3e50;
  background: #1c1c26;
}

.cred-item-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.cred-item-meta {
  display: flex;
  align-items: center;
  gap: 8px;
}

.cred-type-badge {
  font-size: 10px;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: 4px;
  letter-spacing: 0.5px;
  text-transform: uppercase;
}

.cred-key-title {
  font-size: 12px;
  font-weight: 600;
  color: #f1f5f9;
}

.cred-len-pill {
  font-size: 10px;
  color: #94a3b8;
  background: #242430;
  padding: 1px 6px;
  border-radius: 10px;
}

.cred-copy-btn {
  padding: 2px 6px;
  height: 20px;
  font-size: 11px;
}

.cred-item-content {
  background: #0f0f13;
  border: 1px solid #22222c;
  border-radius: 6px;
  padding: 7px 10px;
  font-size: 11.5px;
  line-height: 1.45;
  color: #cbd5e1;
  word-break: break-all;
  white-space: pre-wrap;
  max-height: 110px;
  overflow-y: auto;
  cursor: pointer;
  transition: all 0.15s ease;
}
.cred-item-content:hover {
  border-color: #3b82f6;
  background: #111118;
}

.cred-empty-box {
  text-align: center;
  padding: 30px 0;
  color: #64748b;
  font-size: 12px;
}

.macos-copy-all-btn {
  border-radius: 5px;
  font-size: 11px;
  padding: 2px 8px;
  height: 24px;
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.12);
  color: #e2e8f0;
}
.macos-copy-all-btn:hover {
  background: rgba(255, 255, 255, 0.15);
  border-color: rgba(255, 255, 255, 0.25);
  color: #fff;
}

.cred-scroll-wrap {
  max-height: 60vh;
  overflow-y: auto;
}

.extract-link-cell {
  display: flex;
  align-items: center;
  gap: 6px;
}

.refresh-token-action-btn {
  background: #0284c7 !important;
  border-color: #0284c7 !important;
  color: #fff !important;
}
.refresh-token-action-btn:hover {
  background: #0369a1 !important;
}

.extract-pill-tag {
  font-size: 10px;
  font-weight: 700;
  padding: 0 4px;
}

.extract-url-text {
  font-size: 11px;
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ──────── 验活弹窗高性能工具栏与分页底栏 ──────── */
.health-table-filter-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
}

.health-search-input {
  width: 180px;
}

.health-pagination-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 6px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
}

.oauth-batch-hint {
  font-size: 12px;
  color: var(--app-text-secondary);
  background: var(--c-tianshuibi-soft);
  border: 1px solid var(--c-tianshuibi-border);
  border-radius: 8px;
  padding: 6px 10px;
  line-height: 1.45;
}

.plus-kpi-card.clickable-card {
  cursor: pointer;
}
.plus-kpi-card.clickable-card:hover {
  box-shadow: 0 0 0 1px var(--c-tianshuibi-border);
}
.plus-kpi-card.is-filter-active {
  box-shadow: 0 0 0 1px var(--brand);
  background: var(--c-tianshuibi-soft);
}

/* ──────────── OAuth 控制台专用样式规范 (3D Liquid Glass 液态水晶拟态) ──────────── */
.oa-kpi-grid {
  display: grid !important;
  grid-template-columns: repeat(4, 1fr) !important;
  gap: 10px !important;
  width: 100% !important;
  margin-bottom: 8px !important;
}
.oa-kpi-card {
  position: relative !important;
  background: #ffffff !important;
  border: 1px solid #e5e7eb !important;
  border-radius: 12px !important;
  padding: 8px 14px !important;
  display: flex !important;
  flex-direction: column !important;
  justify-content: center !important;
  height: 64px !important;
  box-sizing: border-box !important;
  cursor: pointer !important;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04) !important;
  overflow: hidden !important;
}
.oa-kpi-card:hover {
  border-color: #38bdf8 !important;
  box-shadow: 0 0 0 1px rgba(56, 189, 248, 0.25) !important;
  background: #ffffff !important;
}
.oa-kpi-card.is-filter-active {
  border-color: #38bdf8 !important;
  box-shadow: 0 0 0 2px rgba(56, 189, 248, 0.35), 0 10px 24px -2px rgba(2, 132, 199, 0.25) !important;
}
.oa-kpi-card .kpi-label {
  position: relative;
  z-index: 4;
  font-size: 11px !important;
  color: var(--text-muted) !important;
  font-weight: 700 !important;
  margin-bottom: 2px !important;
  white-space: nowrap !important;
}
.oa-kpi-card .kpi-num {
  position: relative;
  z-index: 4;
  font-size: 19px !important;
  font-weight: 800 !important;
  font-family: var(--el-font-family-monospace, monospace) !important;
  color: var(--text-main) !important;
  line-height: 1.1 !important;
  letter-spacing: -0.02em;
}
.oa-kpi-card .text-success {
  color: #059669 !important;
}
.oa-kpi-card .text-warning {
  color: #d97706 !important;
}
.oa-kpi-card .text-danger {
  color: #dc2626 !important;
}
.oa-retry-tag {
  font-size: 10px;
  padding: 1.5px 7px;
  border-radius: 9999px;
  background: rgba(239, 68, 68, 0.12);
  color: #dc2626;
  border: 1px solid rgba(239, 68, 68, 0.35);
  font-weight: 700;
  cursor: pointer;
  line-height: 1.2;
  transition: all 0.16s ease;
}
.oa-retry-tag:hover {
  background: #dc2626;
  color: #ffffff;
}

.oauth-table-filter-bar {
  display: flex !important;
  align-items: center !important;
  justify-content: space-between !important;
  gap: 8px !important;
  margin: 6px 0 8px !important;
}
:deep(.oa-filter-radio .el-radio-button__inner) {
  height: 28px !important;
  line-height: 26px !important;
  padding: 0 11px !important;
  font-size: 11.5px !important;
  font-weight: 700 !important;
  border: 1.2px solid rgba(255, 255, 255, 0.95) !important;
  background: rgba(255, 255, 255, 0.75) !important;
  backdrop-filter: blur(12px);
  color: #475569 !important;
  border-radius: 9999px !important;
  margin-right: 4px;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04), inset 0 1px 1px #ffffff !important;
  transition: all 0.16s ease !important;
}
:deep(.oa-filter-radio .el-radio-button__original-radio:checked + .el-radio-button__inner) {
  background: linear-gradient(180deg, #38bdf8 0%, #0284c7 100%) !important;
  border-color: rgba(255, 255, 255, 0.95) !important;
  color: #ffffff !important;
  font-weight: 800 !important;
  box-shadow: 0 4px 12px rgba(2, 132, 199, 0.4), inset 0 1.5px 1px rgba(255, 255, 255, 0.9) !important;
}

.oa-search-input {
  width: 190px !important;
}
:deep(.oa-search-input .el-input__wrapper) {
  height: 28px !important;
  border-radius: 9999px !important;
  background: rgba(255, 255, 255, 0.85) !important;
  border: 1.2px solid rgba(255, 255, 255, 0.95) !important;
  box-shadow: inset 0 1.5px 3px rgba(15, 23, 42, 0.06) !important;
}
:deep(.oa-search-input .el-input__wrapper.is-focus) {
  background: #ffffff !important;
  border-color: #38bdf8 !important;
  box-shadow: 0 0 0 2px rgba(56, 189, 248, 0.3), inset 0 1.5px 3px rgba(15, 23, 42, 0.06) !important;
}

.health-table-wrap,
.oauth-table-wrap {
  border-radius: 14px;
  overflow: hidden;
  border: 1px solid var(--app-border, #e5e7eb);
  background: #ffffff;
  box-shadow: inset 0 1px 2px rgba(15, 23, 42, 0.04);
}
.health-table-wrap :deep(.el-table),
.oauth-table-wrap :deep(.el-table) {
  background: #ffffff !important;
}
.health-table-wrap :deep(.el-table__inner-wrapper),
.health-table-wrap :deep(.el-table__body-wrapper),
.health-table-wrap :deep(.el-table__header-wrapper),
.oauth-table-wrap :deep(.el-table__inner-wrapper),
.oauth-table-wrap :deep(.el-table__body-wrapper),
.oauth-table-wrap :deep(.el-table__header-wrapper) {
  background: #ffffff !important;
}
.health-table-wrap :deep(.el-table__header th.el-table__cell),
.oauth-table-wrap :deep(.el-table__header th.el-table__cell) {
  background: #f8fafc !important;
  color: var(--text-main) !important;
  font-size: 11.5px !important;
  font-weight: 800 !important;
  padding: 8px !important;
  border-bottom: 1px solid #e5e7eb !important;
}
.health-table-wrap :deep(.el-table__row td.el-table__cell),
.oauth-table-wrap :deep(.el-table__row td.el-table__cell) {
  padding: 6px 8px !important;
  border-bottom: 1px solid #f1f5f9 !important;
  background: #ffffff !important;
}
.health-table-wrap :deep(.el-table--striped .el-table__body tr.el-table__row--striped td.el-table__cell),
.oauth-table-wrap :deep(.el-table--striped .el-table__body tr.el-table__row--striped td.el-table__cell) {
  background: #f8fafc !important;
}
.health-table-wrap :deep(.el-table__row:hover > td.el-table__cell),
.oauth-table-wrap :deep(.el-table__row:hover > td.el-table__cell) {
  background: #eff6ff !important;
}

.oa-email-line {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  max-width: 100%;
}
.oa-email-text {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-main);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 165px;
  transition: color 0.15s ease;
}
.oa-email-line:hover .oa-email-text {
  color: #0284c7;
  text-decoration: underline;
}
.oa-copy-ico {
  font-size: 11px;
  color: #94a3b8;
  opacity: 0;
  transition: opacity 0.15s ease;
}
.oa-email-line:hover .oa-copy-ico {
  opacity: 1;
}

.oa-status-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2.5px 8px;
  border-radius: 9999px;
  font-size: 11px;
  font-weight: 700;
  line-height: 1.3;
}
.oa-status-badge.is-pending {
  background: rgba(255, 255, 255, 0.65);
  color: #64748b;
  border: 1px solid rgba(255, 255, 255, 0.9);
}
.oa-status-badge.is-running {
  background: rgba(2, 132, 199, 0.12);
  color: #0284c7;
  border: 1px solid rgba(2, 132, 199, 0.3);
}
.oa-status-badge.is-success {
  background: rgba(16, 185, 129, 0.12);
  color: #059669;
  border: 1px solid rgba(16, 185, 129, 0.3);
}
.oa-status-badge.is-warning {
  background: rgba(245, 158, 11, 0.12);
  color: #d97706;
  border: 1px solid rgba(245, 158, 11, 0.3);
}
.oa-status-badge.is-danger {
  background: rgba(239, 68, 68, 0.12);
  color: #dc2626;
  border: 1px solid rgba(239, 68, 68, 0.3);
}

.oa-row-actions {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  width: 100%;
}
.oa-micro-btn {
  height: 24px;
  padding: 0 9px;
  border-radius: 9999px;
  font-size: 11px;
  font-weight: 700;
  border: 1.2px solid rgba(255, 255, 255, 0.95);
  background: rgba(255, 255, 255, 0.85);
  color: #334155;
  cursor: pointer;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04);
  transition: all 0.16s ease;
  display: inline-flex;
  align-items: center;
  gap: 3px;
}
.oa-micro-btn:hover {
  background: #ffffff;
  border-color: #0284c7;
  color: #0284c7;
  transform: translateY(-1px);
}
.oa-micro-btn.btn-retry {
  color: #d97706;
}
.oa-micro-btn.btn-retry:hover {
  background: #d97706;
  border-color: #d97706;
  color: #ffffff;
}
.oa-micro-btn.btn-dl {
  color: #0284c7;
}
.oa-micro-btn.btn-dl:hover {
  background: #0284c7;
  border-color: #0284c7;
  color: #ffffff;
}

/* 3D 水晶果冻底部操作按钮 (对齐图片 31/32/33 的果冻药丸) */
.oa-footer-btn {
  height: 32px;
  padding: 0 14px;
  border-radius: 9999px;
  font-size: 12px;
  font-weight: 800;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  cursor: pointer;
  transition: all 0.18s cubic-bezier(0.16, 1, 0.3, 1);
  box-sizing: border-box;
  outline: none;
}
.oa-footer-btn.btn-export-cpa {
  background: linear-gradient(180deg, rgba(56, 189, 248, 0.22) 0%, rgba(2, 132, 199, 0.15) 100%);
  border: 1.5px solid rgba(56, 189, 248, 0.45);
  color: #0284c7;
  box-shadow: 0 4px 10px rgba(2, 132, 199, 0.15), inset 0 1px 1px #ffffff;
}
.oa-footer-btn.btn-export-cpa:hover:not(:disabled) {
  transform: translateY(-1px);
  background: #0284c7;
  border-color: #0284c7;
  color: #ffffff;
  box-shadow: 0 8px 18px -2px rgba(2, 132, 199, 0.4);
}
.oa-footer-btn.btn-export-sub2 {
  background: linear-gradient(180deg, rgba(52, 211, 153, 0.22) 0%, rgba(5, 150, 105, 0.15) 100%);
  border: 1.5px solid rgba(52, 211, 153, 0.45);
  color: #059669;
  box-shadow: 0 4px 10px rgba(5, 150, 105, 0.15), inset 0 1px 1px #ffffff;
}
.oa-footer-btn.btn-export-sub2:hover:not(:disabled) {
  transform: translateY(-1px);
  background: #059669;
  border-color: #059669;
  color: #ffffff;
  box-shadow: 0 8px 18px -2px rgba(5, 150, 105, 0.4);
}
.oa-footer-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
  box-shadow: none;
}
.oa-footer-btn.btn-close {
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.95) 0%, rgba(241, 245, 249, 0.7) 100%);
  border: 1.5px solid rgba(255, 255, 255, 0.95);
  color: #475569;
  box-shadow: 0 2px 6px rgba(15, 23, 42, 0.04), inset 0 1.5px 1px #ffffff;
}
.oa-footer-btn.btn-close:hover {
  background: #ffffff;
  border-color: #0284c7;
  color: #0284c7;
  transform: translateY(-1px);
}
.oa-footer-btn.btn-warn {
  background: linear-gradient(180deg, #fb923c 0%, #ea580c 100%);
  border: 1.8px solid rgba(255, 255, 255, 0.95);
  color: #ffffff;
  box-shadow: 0 8px 20px -2px rgba(234, 88, 12, 0.45), inset 0 1.5px 1px rgba(255, 255, 255, 0.9);
}
.oa-footer-btn.btn-warn:hover:not(:disabled) {
  transform: translateY(-2px);
  box-shadow: 0 12px 26px -2px rgba(234, 88, 12, 0.6);
}
.oa-footer-btn.btn-stop {
  background: linear-gradient(180deg, #f87171 0%, #dc2626 100%);
  border: 1.8px solid rgba(255, 255, 255, 0.95);
  color: #ffffff;
  box-shadow: 0 8px 20px -2px rgba(220, 38, 38, 0.45), inset 0 1.5px 1px rgba(255, 255, 255, 0.9);
}
.oa-footer-btn.btn-stop:hover {
  transform: translateY(-2px);
  box-shadow: 0 12px 26px -2px rgba(220, 38, 38, 0.6);
}
.oa-footer-btn.btn-primary {
  background: linear-gradient(180deg, #38bdf8 0%, #0284c7 100%);
  border: 1.8px solid rgba(255, 255, 255, 0.95);
  color: #ffffff;
  box-shadow:
    0 10px 24px -2px rgba(2, 132, 199, 0.48),
    0 3px 8px rgba(15, 23, 42, 0.08),
    inset 0 2px 2px rgba(255, 255, 255, 0.9),
    inset 0 -2px 3px rgba(0, 0, 0, 0.2);
}
.oa-footer-btn.btn-primary:hover:not(:disabled) {
  transform: translateY(-2px) scale(1.02);
  box-shadow:
    0 14px 30px -2px rgba(2, 132, 199, 0.6),
    inset 0 2px 2px rgba(255, 255, 255, 0.95);
}

.oauth-table-empty {
  padding: 18px 8px;
  font-size: 12px;
  color: var(--app-text-secondary);
}

/* ──────────── 密码 / 2FA 药丸徽章 (Apple HIG 磨砂半透明质感) ──────────── */
.sec-col-wrapper {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
  padding: 2px 0;
}

.sec-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 10.5px;
  line-height: 1.1;
  padding: 2px 7px;
  border-radius: 9999px;
  font-weight: 600;
  letter-spacing: 0.2px;
  cursor: pointer;
  user-select: none;
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  transition: all 0.18s cubic-bezier(0.4, 0, 0.2, 1);
}

.sec-badge:hover {
  transform: translateY(-1px);
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.25);
}

/* 密码 - 已设置：柔和 Apple Green 暗调半透绿 */
.sec-pwd-ok {
  background: rgba(52, 199, 89, 0.12);
  color: #5da4b1;
  border: 1px solid rgba(93, 164, 177, 0.3);
}
.sec-pwd-ok:hover {
  background: rgba(52, 199, 89, 0.22);
  border-color: rgba(93, 164, 177, 0.5);
}

/* 密码 - 未设置：中性低对比度半透灰 */
.sec-pwd-no {
  background: rgba(148, 163, 184, 0.08);
  color: #64748b;
  border: 1px solid rgba(148, 163, 184, 0.18);
}
.sec-pwd-no:hover {
  background: rgba(239, 68, 68, 0.12);
  color: #f87171;
  border-color: rgba(239, 68, 68, 0.35);
}

/* 2FA - 已绑定：质感 Teal/Cyan 蓝绿半透明 */
.sec-2fa-ok {
  background: rgba(93, 164, 177, 0.15);
  color: #5da4b1;
  border: 1px solid rgba(93, 164, 177, 0.35);
  font-weight: 700;
}
.sec-2fa-ok:hover {
  background: rgba(93, 164, 177, 0.25);
  border-color: rgba(93, 164, 177, 0.55);
}

/* 2FA - 未绑定：中性半透灰 */
.sec-2fa-no {
  background: rgba(148, 163, 184, 0.08);
  color: #64748b;
  border: 1px solid rgba(148, 163, 184, 0.18);
}
.sec-2fa-no:hover {
  background: rgba(245, 158, 11, 0.12);
  color: #fbbf24;
  border-color: rgba(245, 158, 11, 0.35);
}

.sec-action-btn {
  background: rgba(52, 199, 89, 0.12) !important;
  border-color: rgba(93, 164, 177, 0.3) !important;
  color: #5da4b1 !important;
}
.sec-action-btn:hover {
  background: rgba(52, 199, 89, 0.22) !important;
  border-color: rgba(93, 164, 177, 0.5) !important;
}

/* ──────────── 2FA 实时动态码弹窗样式 ──────────── */
.totp-card-container {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 4px 0;
}

.totp-hero-box {
  background: #141418;
  border-radius: 12px;
  padding: 22px 18px;
  text-align: center;
  color: #fff;
  border: 1px solid rgba(255, 255, 255, 0.08);
}

.totp-email-tag {
  font-size: 11.5px;
  color: #94a3b8;
  margin-bottom: 6px;
}

.totp-digits-large {
  font-family: 'SF Mono', Monaco, Menlo, Consolas, monospace;
  font-size: 36px;
  font-weight: 800;
  letter-spacing: 5px;
  color: #5da4b1;
  margin-bottom: 8px;
  cursor: pointer;
  transition: transform 0.15s ease;
}

.totp-digits-large:hover {
  transform: scale(1.03);
}

.totp-countdown-row {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  font-size: 12px;
  color: #94a3b8;
}

.totp-meta-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 8px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--app-border);
  border-radius: 8px;
  padding: 10px 14px;
}

.meta-field-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
}

.meta-field-label {
  color: var(--el-text-color-secondary);
}

.meta-field-val {
  font-family: monospace;
  font-weight: 600;
  color: var(--app-title);
}

/* ──────────── 现代化邮箱收件箱与验证码工作台 (Mailbox Studio) 主流顶流设计 ──────────── */
.mailbox-studio-dialog .el-dialog__header {
  padding: 16px 20px 12px 20px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}

.mailbox-studio-dialog .el-dialog__body {
  padding: 16px 20px 18px 20px;
}

.mailbox-studio-dialog .el-dialog__footer {
  padding: 12px 20px 16px 20px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
}

.mailbox-studio-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

/* 顶部信息与控制栏 */
.mb-top-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  padding: 10px 14px;
  background: rgba(255, 255, 255, 0.025);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 10px;
}

.mb-account-info {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.mb-email-chip {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-size: 13px;
  font-weight: 700;
  color: var(--app-title);
  background: rgba(255, 255, 255, 0.05);
  padding: 3px 10px;
  border-radius: 6px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  cursor: pointer;
  user-select: none;
  transition: all 0.15s ease;
}

.mb-email-chip:hover {
  background: rgba(0, 122, 255, 0.12);
  border-color: rgba(0, 122, 255, 0.35);
  color: #38bdf8;
}

.email-icon {
  font-size: 13px;
  color: #38bdf8;
}

.copy-hint-icon {
  font-size: 12px;
  opacity: 0.6;
}

.mb-tags-group {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.mb-provider-pill,
.mb-protocol-pill,
.mb-elapsed-pill,
.mb-time-pill {
  font-size: 11px;
  padding: 2px 7px;
  border-radius: 4px;
  display: inline-flex;
  align-items: center;
  font-weight: 600;
}

.pill-ms {
  background: rgba(2, 132, 199, 0.12);
  color: #38bdf8;
  border: 1px solid rgba(2, 132, 199, 0.28);
}

.pill-remail {
  background: rgba(93, 164, 177, 0.15);
  color: #5da4b1;
  border: 1px solid rgba(93, 164, 177, 0.35);
}

.pill-other {
  background: rgba(148, 163, 184, 0.1);
  color: #94a3b8;
  border: 1px solid rgba(148, 163, 184, 0.2);
}

.pill-graph {
  background: rgba(93, 164, 177, 0.12);
  color: #5da4b1;
  border: 1px solid rgba(93, 164, 177, 0.3);
}

.pill-remail-pickup {
  background: rgba(168, 85, 247, 0.15);
  color: #c084fc;
  border: 1px solid rgba(168, 85, 247, 0.35);
}

.pill-imap {
  background: rgba(245, 158, 11, 0.12);
  color: #fbbf24;
  border: 1px solid rgba(245, 158, 11, 0.3);
}

.mb-elapsed-pill {
  color: #5da4b1;
  background: rgba(93, 164, 177, 0.08);
  border: 1px solid rgba(93, 164, 177, 0.2);
}

.mb-time-pill {
  color: var(--el-text-color-secondary);
  font-weight: 400;
}

.mb-actions-group {
  display: flex;
  align-items: center;
  gap: 12px;
}

.mb-polling-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: rgba(255, 255, 255, 0.04);
  padding: 2px 8px;
  border-radius: 6px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  transition: all 0.15s ease;
}

.mb-polling-toggle.is-active {
  background: rgba(93, 164, 177, 0.08);
  border-color: rgba(93, 164, 177, 0.35);
}

.polling-label {
  font-size: 11.5px;
  color: var(--app-title);
  user-select: none;
}

.polling-live-dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #5da4b1;
  box-shadow: 0 0 0 0 rgba(93, 164, 177, 0.7);
  animation: pulse-ring 1.6s infinite;
}

.mb-filter-checkbox {
  margin: 0 !important;
}

.mb-refresh-btn {
  font-weight: 600;
}

/* 凭证缺失补录卡片 */
.mb-credential-fix-card {
  background: rgba(245, 158, 11, 0.06);
  border: 1px dashed rgba(245, 158, 11, 0.35);
  border-radius: 8px;
  padding: 10px 14px;
}

.mb-credential-title {
  font-size: 12px;
  font-weight: 700;
  color: #fbbf24;
  margin-bottom: 5px;
}

.mb-credential-hint {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;
}

/* 最新验证码 Hero 卡片 (Linear / 极客分块设计) */
.otp-hero-card {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: linear-gradient(135deg, rgba(93, 164, 177, 0.1) 0%, rgba(6, 78, 59, 0.16) 100%);
  border: 1px solid rgba(93, 164, 177, 0.38);
  box-shadow: 0 4px 18px rgba(93, 164, 177, 0.06);
  border-radius: 12px;
  padding: 14px 18px;
  gap: 16px;
}

.otp-hero-left {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.otp-hero-subtitle {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11.5px;
  font-weight: 600;
  color: #5da4b1;
}

.pulse-emerald-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #5da4b1;
  box-shadow: 0 0 8px #5da4b1;
}

.otp-huge-number-row {
  display: inline-flex;
  align-items: center;
  gap: 12px;
  cursor: pointer;
  user-select: all;
  transition: transform 0.15s ease;
}

.otp-huge-number-row:hover {
  transform: scale(1.015);
}

.otp-digits-display {
  display: flex;
  align-items: center;
  gap: 6px;
}

.otp-char-box {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 44px;
  background: rgba(93, 164, 177, 0.12);
  border: 1px solid rgba(93, 164, 177, 0.35);
  border-radius: 6px;
  font-size: 28px;
  font-weight: 800;
  color: #5da4b1;
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.15);
}

.otp-copy-icon-hover {
  font-size: 16px;
  color: #5da4b1;
  opacity: 0.7;
}

.otp-huge-number-row:hover .otp-copy-icon-hover {
  opacity: 1;
}

.otp-hero-tip {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.otp-copy-hero-btn {
  font-weight: 700;
  font-size: 13.5px;
  padding: 10px 18px;
  background: linear-gradient(135deg, #5da4b1 0%, #059669 100%) !important;
  border: none !important;
  box-shadow: 0 4px 12px rgba(93, 164, 177, 0.3);
}

.otp-copy-hero-btn:hover {
  background: linear-gradient(135deg, #5da4b1 0%, #5da4b1 100%) !important;
  box-shadow: 0 4px 16px rgba(93, 164, 177, 0.45);
}

/* 未检索到验证码空状态卡片 */
.otp-empty-status-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  background: rgba(255, 255, 255, 0.025);
  border: 1px solid rgba(255, 255, 255, 0.07);
  border-radius: 10px;
}

.empty-icon-wrap {
  width: 36px;
  height: 36px;
  border-radius: 8px;
  background: rgba(0, 122, 255, 0.1);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #38bdf8;
  flex-shrink: 0;
}

.empty-text-wrap {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.empty-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--app-title);
}

.empty-desc {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  line-height: 1.4;
}

/* 邮件列表容器与流式布局 (彻底根治 flex-shrink 挤压 bug) */
.mb-mails-container {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.mb-section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 2px;
}

.mb-section-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 700;
  color: var(--app-title);
}

.mb-count-badge {
  font-size: 11px;
  color: #38bdf8;
  background: rgba(2, 132, 199, 0.12);
  padding: 1px 6px;
  border-radius: 10px;
  border: 1px solid rgba(2, 132, 199, 0.25);
}

.mb-search-input {
  width: 220px;
}

.mb-mails-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 380px;
  overflow-y: auto;
  padding-right: 4px;
  overscroll-behavior: contain;
}

/* 独立卡片：绝对不允许被 flexbox 挤压 */
.mb-mail-card {
  flex-shrink: 0 !important;
  min-height: 56px;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.07);
  border-radius: 8px;
  transition: all 0.18s cubic-bezier(0.4, 0, 0.2, 1);
  overflow: hidden;
  box-sizing: border-box;
}

.mb-mail-card:hover {
  background: rgba(255, 255, 255, 0.055);
  border-color: rgba(255, 255, 255, 0.16);
}

.mb-mail-card.has-otp {
  border-left: 3.5px solid #5da4b1;
}

.mb-mail-card.is-expanded {
  background: rgba(255, 255, 255, 0.045);
  border-color: rgba(0, 122, 255, 0.35);
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.2);
}

.mb-mail-main-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 14px;
  cursor: pointer;
  user-select: none;
  gap: 12px;
}

.mb-mail-left-group {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
  min-width: 0;
}

.mb-sender-avatar {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 10.5px;
  font-weight: 800;
  letter-spacing: 0.5px;
  flex-shrink: 0;
}

.mb-mail-text-block {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
  min-width: 0;
}

.mb-mail-top-line {
  display: flex;
  align-items: center;
  gap: 8px;
}

.mb-sender-name {
  font-size: 11px;
  font-weight: 600;
  color: #38bdf8;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mb-mail-date {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.mb-mail-subject-line {
  display: flex;
  align-items: center;
}

.mb-subject-text {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--app-title);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.mb-mail-snippet-line {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  line-height: 1.3;
}

.mb-mail-right-group {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

.mb-otp-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11.5px;
  font-weight: 800;
  color: #5da4b1;
  background: rgba(93, 164, 177, 0.15);
  border: 1px solid rgba(93, 164, 177, 0.4);
  padding: 2px 8px;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.12s ease;
}

.mb-otp-badge:hover {
  background: rgba(93, 164, 177, 0.28);
  border-color: rgba(93, 164, 177, 0.7);
  transform: scale(1.04);
}

.mb-expand-caret {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  display: flex;
  align-items: center;
}

/* 邮件详情展开面板 */
.mb-mail-detail-pane {
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  padding: 10px 14px 12px 14px;
  background: rgba(0, 0, 0, 0.25);
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.mb-detail-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.mb-detail-hint {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.mb-detail-content-box {
  background: #090a0f;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 6px;
  padding: 10px 12px;
  font-size: 11.5px;
  line-height: 1.55;
  color: #cbd5e1;
  max-height: 200px;
  overflow-y: auto;
  word-break: break-all;
  white-space: pre-wrap;
  user-select: text;
}

.mb-no-mails-wrap {
  padding: 24px 0;
}

.mb-dialog-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
}

.mb-footer-right {
  display: flex;
  gap: 8px;
}

/* ──────────── 现代化 OAuth 接码与凭证生成控制台样式 ──────────── */
.oa-target-pill {
  font-size: 11px;
  font-weight: 600;
  color: #28646e;
  background: #edf6f8;
  border: 1px solid #bce2e7;
  padding: 1px 8px;
  border-radius: 12px;
}

.oa-config-toggle-btn {
  font-size: 11.5px;
  font-weight: 500;
  border-radius: 6px;
}

.oa-strategy-hero-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  border-radius: 8px;
  margin-bottom: 12px;
  flex-wrap: wrap;
  gap: 10px;
  border: 1px solid var(--el-border-color-lighter);
  background: var(--el-bg-color-overlay, #ffffff);
  transition: all 0.2s ease;
}

.oa-strategy-hero-card.is-skip-mode {
  background: rgba(93, 164, 177, 0.05);
  border-color: rgba(93, 164, 177, 0.25);
}

.oa-strategy-hero-card.is-cdk-mode,
.oa-strategy-hero-card.is-cdk_sms-mode {
  background: rgba(245, 158, 11, 0.07);
  border-color: rgba(245, 158, 11, 0.35);
}

.oa-strategy-hero-card.is-api-mode,
.oa-strategy-hero-card.is-sms-mode,
.oa-strategy-hero-card.is-smsbower-mode,
.oa-strategy-hero-card.is-herosms-mode {
  background: rgba(0, 122, 255, 0.05);
  border-color: rgba(0, 122, 255, 0.25);
}

.strategy-left-control {
  display: flex;
  align-items: center;
  gap: 10px;
}
.strategy-radio-group {
  display: inline-flex;
  flex-wrap: wrap;
}
.strategy-label {
  font-weight: 700;
  font-size: 12.5px;
  color: var(--app-title);
}
.strategy-btn-content {
  font-weight: 600;
  font-size: 12px;
}

.strategy-right-meta {
  font-size: 12px;
  line-height: 1.4;
}
.strategy-tip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: 500;
}
.strategy-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
}
.strategy-dot.emerald { background: #5da4b1; }
.strategy-dot.amber { background: #d49432; }
.strategy-dot.blue { background: #5da4b1; }

.text-emerald { color: #28646e; }
.text-amber { color: #8c5c16; }
.text-blue { color: #28646e; }

.oa-tab-form {
  margin-top: 8px;
}

/* 实时号池档位选择器 */
.oa-tier-chips-block {
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  padding: 8px 10px;
  margin-bottom: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.oa-tier-title {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--app-title);
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 5px;
}
.oa-tier-empty {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  padding: 4px 0 2px;
}
.oa-tier-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.oa-tier-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  background: var(--el-bg-color-overlay, #ffffff);
  border: 1px solid var(--el-border-color-lighter);
  color: var(--el-text-color-primary);
  cursor: pointer;
  user-select: none;
  transition: all 0.15s ease;
}
.oa-tier-pill:hover {
  border-color: var(--el-color-primary);
  color: var(--el-color-primary);
}
.oa-tier-pill.is-active {
  background: #5da4b1;
  color: #fff;
  border-color: #5da4b1;
  font-weight: 600;
}
.oa-check-icon {
  font-size: 12px;
}

.sms-guide-box {
  margin-top: 4px;
  padding: 8px 12px;
  border-radius: 6px;
  background: rgba(14, 165, 233, 0.05);
  border: 1px solid rgba(14, 165, 233, 0.2);
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.sms-guide-title {
  font-weight: 700;
  color: var(--app-title);
  font-size: 12px;
  display: flex;
  align-items: center;
  gap: 5px;
}
.sms-guide-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.sms-rule-chip {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 4px;
  background: var(--el-bg-color-overlay, #ffffff);
  border: 1px solid rgba(14, 165, 233, 0.18);
  color: var(--el-text-color-regular);
}

.oa-skip-mode-panel {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 16px;
  background: rgba(93, 164, 177, 0.06);
  border: 1px solid rgba(93, 164, 177, 0.2);
  border-radius: 6px;
  color: var(--el-text-color-regular);
  font-size: 12px;
  line-height: 1.55;
}
.skip-icon {
  font-size: 20px;
  color: #5da4b1;
  flex-shrink: 0;
}
.skip-text {
  flex: 1;
}

.oa-config-footer-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed var(--el-border-color-lighter);
  font-size: 11.5px;
}
.oa-config-hint {
  color: var(--el-text-color-secondary);
}
.oa-save-default-btn {
  font-size: 11.5px;
  font-weight: 500;
}

/* CDK 模式专属面板 */
.oa-cdk-config-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.cdk-pool-mini-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  border-radius: 6px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  font-size: 12px;
  flex-wrap: wrap;
  gap: 8px;
}

.cdk-pool-mini-bar.is-empty {
  border-color: rgba(239, 68, 68, 0.35);
  background: rgba(239, 68, 68, 0.06);
}

.pool-bar-left {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.pool-bar-icon {
  font-size: 15px;
}

.pool-bar-label {
  font-weight: 600;
  color: var(--app-title);
}

.pool-bar-sub {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.pool-link-btn {
  font-size: 11px;
  color: var(--el-color-primary);
  text-decoration: none;
  font-weight: 500;
}

.pool-link-btn:hover {
  text-decoration: underline;
}

.oa-cdk-feature-tips {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  margin-top: 4px;
}

@media (max-width: 768px) {
  .oa-cdk-feature-tips {
    grid-template-columns: 1fr;
  }
}

.feature-tip-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 6px 10px;
  border-radius: 6px;
  background: var(--el-fill-color-lighter);
  border: 1px solid var(--el-border-color-extra-light);
}

.tip-title {
  font-size: 11px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.tip-desc {
  font-size: 10.5px;
  color: var(--el-text-color-secondary);
  line-height: 1.35;
}

/* 底部操作工具栏 (横向两端对齐，彻底消除堆叠错位) */
.oa-footer {
  display: flex !important;
  align-items: center !important;
  justify-content: space-between !important;
  width: 100% !important;
  gap: 12px !important;
  box-sizing: border-box !important;
}

.oa-footer .footer-left {
  display: flex !important;
  align-items: center !important;
  gap: 8px !important;
  flex-shrink: 0 !important;
}

.oa-footer .footer-right {
  display: flex !important;
  align-items: center !important;
  justify-content: flex-end !important;
  gap: 8px !important;
  flex-wrap: wrap !important;
}

:deep(.feat-dialog) {
  margin: 3vh auto 0 !important;
  max-height: 94vh;
  overflow: hidden !important;
}
:deep(.feat-dialog .el-dialog__header) {
  padding: 12px 18px;
}
:deep(.feat-dialog .el-dialog__body) {
  padding: 12px 16px 16px;
  overflow: hidden !important;
  max-height: calc(94vh - 56px);
}
:deep(.feat-dialog .el-dialog__footer) {
  display: none;
}
.feat-board {
  display: flex;
  flex-direction: column;
  gap: 10px;
  height: min(calc(94vh - 72px), 680px);
  overflow: hidden;
}
.feat-kpi-row {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 8px;
  flex-shrink: 0;
}
.feat-kpi {
  border-radius: 10px;
  padding: 10px 12px;
  border: 1px solid var(--el-border-color-lighter);
  background: var(--el-fill-color-light);
  min-width: 0;
}
.feat-kpi.tone-ok {
  background: rgba(93, 164, 177, 0.1);
  border-color: rgba(93, 164, 177, 0.28);
}
.feat-kpi.tone-bad {
  background: rgba(239, 68, 68, 0.1);
  border-color: rgba(239, 68, 68, 0.24);
}
.feat-kpi.tone-warn {
  background: rgba(245, 158, 11, 0.12);
  border-color: rgba(245, 158, 11, 0.28);
}
.feat-kpi-label {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}
.feat-kpi-value {
  margin-top: 2px;
  font-size: 22px;
  font-weight: 800;
  letter-spacing: -0.4px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  line-height: 1.15;
}
.feat-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  grid-template-rows: 1fr 1fr;
  gap: 8px;
  flex: 1;
  min-height: 0;
}
.feat-card,
.feat-recent {
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  border-radius: 10px;
  border: 1px solid var(--el-border-color-lighter);
  background: var(--el-bg-color);
  padding: 10px 12px;
}
.feat-card {
  display: flex;
  flex-direction: column;
}
.feat-card-head,
.feat-recent-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12.5px;
  font-weight: 700;
  margin-bottom: 8px;
  flex-shrink: 0;
}
.feat-bars {
  display: flex;
  flex-direction: column;
  gap: 7px;
  overflow: hidden;
}
.feat-bar-meta {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 11.5px;
}
.feat-bar-key {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 600;
}
.feat-bar-num {
  color: var(--el-text-color-secondary);
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
}
.feat-bar-track {
  height: 6px;
  border-radius: 99px;
  background: var(--el-fill-color);
  overflow: hidden;
}
.feat-bar-track i {
  display: block;
  height: 100%;
  border-radius: 99px;
  background: #5856d6;
}
.feat-empty {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  padding: 18px 4px;
}
.feat-empty-wide {
  text-align: center;
  padding-top: 28px;
}
.feat-recent {
  flex-shrink: 0;
  height: 232px;
}
.feat-recent-list {
  display: grid;
  grid-template-rows: repeat(8, 1fr);
  height: calc(100% - 28px);
  overflow: hidden;
}
.feat-recent-row {
  display: grid;
  grid-template-columns: 58px minmax(0, 1.6fr) 52px minmax(0, 1.1fr) minmax(0, 1fr) 88px 86px;
  gap: 8px;
  align-items: center;
  font-size: 12px;
  min-width: 0;
  overflow: hidden;
}
.feat-chip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 20px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
}
.feat-chip.tone-ok { color: #059669; background: rgba(93, 164, 177, 0.14); }
.feat-chip.tone-bad { color: #dc2626; background: rgba(239, 68, 68, 0.14); }
.feat-chip.tone-warn { color: #d97706; background: rgba(245, 158, 11, 0.16); }
.feat-chip.tone-mute { color: var(--el-text-color-secondary); background: var(--el-fill-color); }
.feat-mail,
.feat-cell,
.feat-err,
.feat-time {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.feat-mail { font-weight: 600; }
.feat-cell, .feat-err, .feat-time { color: var(--el-text-color-secondary); }
.feat-time { text-align: right; font-variant-numeric: tabular-nums; }

/* ──────────── 全格式导出留痕徽章与 Tooltip 样式 ──────────── */
.export-mark-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 700;
  cursor: pointer;
  user-select: none;
  transition: all 0.15s ease;
  font-family: var(--el-font-family-monospace, monospace);
  line-height: 1.2;
}
.export-mark-badge:hover {
  transform: scale(1.08);
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.2);
}
.export-mark-badge.badge-at {
  color: #5da4b1;
  background: rgba(93, 164, 177, 0.12);
  border: 1px solid rgba(93, 164, 177, 0.35);
}
.export-mark-badge.badge-sub2 {
  color: #06b6d4;
  background: rgba(6, 182, 212, 0.12);
  border: 1px solid rgba(6, 182, 212, 0.35);
}
.export-mark-badge.badge-cpa {
  color: #f59e0b;
  background: rgba(245, 158, 11, 0.12);
  border: 1px solid rgba(245, 158, 11, 0.35);
}
.export-mark-badge.badge-session {
  color: #8b5cf6;
  background: rgba(139, 92, 246, 0.12);
  border: 1px solid rgba(139, 92, 246, 0.35);
}
.export-mark-badge.badge-pwd2fa {
  color: #3b82f6;
  background: rgba(59, 130, 246, 0.12);
  border: 1px solid rgba(59, 130, 246, 0.35);
}

.export-tooltip-box {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 4px 2px;
  font-size: 11.5px;
  line-height: 1.45;
  color: #e2e8f0;
}
.export-tooltip-title {
  font-weight: 700;
  color: #38bdf8;
  border-bottom: 1px solid rgba(255, 255, 255, 0.12);
  padding-bottom: 3px;
  margin-bottom: 2px;
}
.export-tooltip-item {
  display: flex;
  gap: 6px;
}
.export-tooltip-item .lbl {
  color: #94a3b8;
  flex-shrink: 0;
}
.export-tooltip-item .val {
  color: #f8fafc;
  word-break: break-all;
}
.export-tooltip-tip {
  font-size: 10.5px;
  color: #fbbf24;
  margin-top: 4px;
  border-top: 1px dashed rgba(255, 255, 255, 0.1);
  padding-top: 3px;
}

/* ──────────── 全新导出配置弹窗 (Export Studio) ──────────── */
.export-modal-header {
  display: flex;
  align-items: center;
  gap: 12px;
}
.export-modal-title {
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.export-modal-title .main-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--app-title);
}
.export-modal-title .sub-title {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.export-modal-body {
  padding: 4px 6px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.export-field-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.field-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.field-label {
  font-size: 12.5px;
  font-weight: 700;
  color: var(--app-title);
}
.field-extra-pill {
  font-size: 10.5px;
  font-weight: 600;
  color: var(--el-color-primary);
  background: rgba(0, 122, 255, 0.09);
  padding: 1px 8px;
  border-radius: 10px;
}
.field-count-badge {
  font-size: 11px;
  font-weight: 600;
  color: #5da4b1;
  background: rgba(93, 164, 177, 0.1);
  padding: 1px 8px;
  border-radius: 10px;
}
.field-chunk-hint {
  font-size: 11.5px;
  color: var(--el-color-warning);
  font-weight: 600;
}
.field-tag-hint {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}
.field-desc-tip {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  line-height: 1.4;
}

.export-select-block {
  width: 100%;
}
.fmt-option-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  gap: 10px;
}
.fmt-opt-label {
  font-weight: 600;
  font-size: 12.5px;
}
.fmt-opt-note {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

/* 范围单选卡片 */
.scope-radio-cards {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.scope-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  border-radius: 8px;
  border: 1px solid var(--el-border-color-lighter);
  background: var(--el-fill-color-light);
  cursor: pointer;
  transition: all 0.18s ease;
  user-select: none;
}
.scope-card:hover:not(.is-disabled) {
  border-color: var(--el-color-primary);
  background: rgba(0, 122, 255, 0.04);
}
.scope-card.is-active {
  border-color: var(--el-color-primary);
  background: rgba(0, 122, 255, 0.08);
  box-shadow: 0 0 0 1px var(--el-color-primary) inset;
}
.scope-card.is-disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.scope-card-left {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.scope-card .scope-title {
  font-size: 12px;
  font-weight: 700;
  color: var(--app-title);
}
.scope-card .scope-desc {
  font-size: 10.5px;
  color: var(--el-text-color-secondary);
}
.scope-badge {
  font-size: 11px;
  font-weight: 700;
  color: var(--el-color-primary);
  background: rgba(0, 122, 255, 0.12);
  padding: 2px 8px;
  border-radius: 6px;
  font-family: var(--el-font-family-monospace, monospace);
}
.scope-badge.badge-all {
  color: #8b5cf6;
  background: rgba(139, 92, 246, 0.12);
}

/* 分卷按钮组 */
.chunk-buttons-wrap {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.chunk-btn {
  padding: 6px 12px;
  border-radius: 6px;
  font-size: 11.5px;
  font-weight: 500;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  color: var(--el-text-color-primary);
  cursor: pointer;
  user-select: none;
  transition: all 0.15s ease;
}
.chunk-btn:hover {
  border-color: var(--el-color-primary);
  color: var(--el-color-primary);
}
.chunk-btn.is-active {
  background: #5da4b1;
  color: #fff;
  border-color: #5da4b1;
  font-weight: 700;
  box-shadow: 0 2px 8px rgba(93, 164, 177, 0.3);
}

.custom-chunk-input-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
  padding: 8px 12px;
  background: rgba(93, 164, 177, 0.08);
  border: 1px dashed rgba(93, 164, 177, 0.35);
  border-radius: 6px;
}
.custom-chunk-lbl {
  font-size: 11.5px;
  color: var(--el-text-color-primary);
}
.custom-chunk-num {
  width: 130px;
}
.custom-chunk-unit {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

/* 导出备注与快捷标签 */
.export-note-input {
  font-size: 12px;
}
.preset-notes-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 2px;
}
.preset-note-title {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}
.preset-note-chip {
  font-size: 11px;
  color: var(--el-color-primary);
  background: rgba(0, 122, 255, 0.07);
  border: 1px solid rgba(0, 122, 255, 0.2);
  padding: 1px 8px;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.12s ease;
  user-select: none;
}
.preset-note-chip:hover {
  background: rgba(0, 122, 255, 0.16);
  border-color: var(--el-color-primary);
  transform: translateY(-1px);
}

.export-modal-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
}
.export-summary-text {
  font-size: 12px;
  color: var(--el-text-color-primary);
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.summary-count-line {
  display: flex;
  align-items: center;
}
.keep-modal-checkbox {
  font-size: 11px !important;
  height: 20px !important;
}
.keep-modal-checkbox :deep(.el-checkbox__label) {
  font-size: 11px !important;
  color: #94a3b8 !important;
}
.export-summary-text strong {
  color: var(--el-color-primary);
  font-size: 14px;
  font-family: var(--el-font-family-monospace, monospace);
}
.export-summary-text .sub-summary {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  margin-left: 4px;
}
.export-submit-btn {
  font-weight: 700;
  padding: 8px 16px;
}

/* 连环导出成功状态横幅 */
.export-last-success-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 7px;
  background: rgba(93, 164, 177, 0.12);
  border: 1px solid rgba(93, 164, 177, 0.35);
  margin-bottom: 2px;
}
.export-last-success-banner .banner-left {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11.5px;
  color: #5da4b1;
}
.export-last-success-banner .success-indicator-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #5da4b1;
  box-shadow: 0 0 8px #5da4b1;
  flex-shrink: 0;
}
.export-last-success-banner .banner-hint {
  font-size: 10.5px;
  color: #6ee7b7;
  font-weight: 500;
}

/* 字段分隔符设置与实时预览横幅 */
.field-delim-hint {
  font-size: 11.5px;
  color: #38bdf8;
  font-weight: 600;
}
.delim-buttons-wrap {
  margin-bottom: 4px;
}
.custom-delim-input :deep(.el-input__wrapper) {
  font-weight: 700;
  color: #38bdf8;
}
.delim-preview-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 6px;
  background: #edf6f8;
  border: 1px solid #bce2e7;
  margin-top: 4px;
}
.delim-preview-banner .preview-tag {
  font-size: 11px;
  font-weight: 700;
  color: #28646e;
  flex-shrink: 0;
}
.delim-preview-banner .preview-text {
  font-size: 12px;
  font-weight: 600;
  color: #243538;
  word-break: break-all;
}

/* ──────────── 顶栏工具：快捷键徽章与按钮 ──────────── */
.search-key-badge {
  font-size: 10px;
  font-weight: 700;
  color: var(--el-text-color-secondary);
  background: var(--el-fill-color);
  border: 1px solid var(--el-border-color-lighter);
  padding: 0 4px;
  border-radius: 4px;
  line-height: 1.4;
  user-select: none;
}
.acct-tool-btn {
  font-size: 11.5px;
  font-weight: 600;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border-radius: 6px;
}

/* 自定义列显示 Popover */
.col-settings-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 4px 2px;
}
.col-settings-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
  font-weight: 700;
  color: var(--app-title);
  border-bottom: 1px solid var(--el-border-color-lighter);
  padding-bottom: 6px;
}
.col-checkbox-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.col-checkbox-list :deep(.el-checkbox) {
  margin-right: 0;
  height: 26px;
  font-size: 12px;
}

/* ──────────── 表格行高密度切换 Popover 样式 ──────────── */
.density-menu-popover .el-dropdown-menu__item {
  display: flex !important;
  align-items: center !important;
  justify-content: space-between !important;
  min-width: 175px !important;
  padding: 7px 12px !important;
  font-size: 12px !important;
  gap: 12px !important;
  transition: all 0.15s ease !important;
}
.density-menu-popover .el-dropdown-menu__item.is-density-active {
  color: #5da4b1 !important;
  background: rgba(93, 164, 177, 0.12) !important;
  font-weight: 600 !important;
}
.density-menu-popover .density-item-content {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.density-menu-popover .density-check-mark {
  color: #5da4b1;
  font-weight: 700;
  font-size: 13px;
  margin-left: auto;
}

/* ──────────── 底部毛玻璃极客悬浮批量操作栏 (Dynamic Island Dock) ──────────── */
.floating-action-bar {
  position: fixed;
  bottom: 24px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 1500;
  pointer-events: none;
  max-width: 95vw;
}

.floating-bar-pill {
  pointer-events: auto;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  border-radius: 9999px;
  background: rgba(255, 255, 255, 0.96);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border: 1px solid rgba(93, 164, 177, 0.35);
  box-shadow: 0 12px 36px rgba(25, 65, 72, 0.14), 0 2px 8px rgba(25, 65, 72, 0.06);
  white-space: nowrap;
  user-select: none;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
}
.floating-bar-pill:hover {
  box-shadow: 0 16px 44px rgba(25, 65, 72, 0.18), 0 0 0 1.5px rgba(93, 164, 177, 0.45);
}

.floating-counter-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 9px;
  border-radius: 9999px;
  background: #edf6f8;
  border: 1px solid rgba(93, 164, 177, 0.35);
  font-size: 11.5px;
  color: #21474e;
  white-space: nowrap;
  flex-shrink: 0;
}
.pulse-counter-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #5da4b1;
  box-shadow: 0 0 8px #5da4b1;
  animation: pulse-counter 1.5s infinite;
  flex-shrink: 0;
}
@keyframes pulse-counter {
  0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(93, 164, 177, 0.6); }
  70% { transform: scale(1); box-shadow: 0 0 0 5px rgba(93, 164, 177, 0); }
  100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(93, 164, 177, 0); }
}
.counter-text {
  white-space: nowrap;
  font-weight: 500;
}
.counter-text strong {
  font-family: var(--el-font-family-monospace, monospace);
  font-weight: 700;
  color: #1a454d;
}

.floating-divider {
  width: 1px;
  height: 16px;
  background: rgba(93, 164, 177, 0.25);
  flex-shrink: 0;
}

.floating-actions-group {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  flex-shrink: 0;
}

.dock-btn-primary {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 0 12px;
  height: 28px;
  border-radius: 9999px;
  border: 1px solid #488793;
  background: #5da4b1;
  color: #ffffff;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
  flex-shrink: 0;
  transition: all 0.15s ease;
  box-shadow: 0 2px 6px rgba(93, 164, 177, 0.25);
}
.dock-btn-primary:hover {
  background: #488793;
  box-shadow: 0 3px 10px rgba(93, 164, 177, 0.35);
  transform: translateY(-1px);
}

.dock-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 0 9px;
  height: 28px;
  border-radius: 9999px;
  border: 1px solid rgba(93, 164, 177, 0.25);
  background: #ffffff;
  color: #28474c;
  font-size: 11.5px;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
  flex-shrink: 0;
  transition: all 0.15s ease;
}
.dock-btn:hover {
  background: #edf6f8;
  border-color: #5da4b1;
  color: #1a454d;
  transform: translateY(-1px);
}
.dock-btn .ico-blue { color: #5da4b1; }
.dock-btn .ico-emerald { color: #488793; }
.dock-btn .ico-amber { color: #528994; }
.dock-btn .ico-purple { color: #5da4b1; }
.dock-btn .ico-warm { color: #528994; }
.dock-btn .ico-gray { color: #728c90; }

.dock-btn-danger {
  color: #c7564d !important;
  border-color: rgba(199, 86, 77, 0.25) !important;
  background: #fdf2f1 !important;
}
.dock-btn-danger:hover {
  background: #fae4e2 !important;
  border-color: #c7564d !important;
  color: #9b3730 !important;
}

.dock-close-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  border: none;
  background: rgba(93, 164, 177, 0.12);
  color: #5c7478;
  cursor: pointer;
  flex-shrink: 0;
  transition: all 0.15s ease;
  font-size: 11px;
}
.dock-close-btn:hover {
  background: rgba(93, 164, 177, 0.22);
  color: #1a3c42;
}

.warm-col-cell {
  display: inline-flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
}
.warm-dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  margin-right: 4px;
  background: #5da4b1;
}
.warm-dot.failed {
  background: #ef4444;
}
.warm-dot.running, .warm-dot.pending {
  background: #f59e0b;
}
.warm-cnt-badge {
  font-size: 10px;
  font-weight: 700;
  margin-left: 2px;
  opacity: 0.85;
}
.warm-date-sub {
  font-size: 10px;
  color: var(--el-text-color-secondary);
}
.floating-btn.btn-copy {
  background: rgba(255, 255, 255, 0.08);
  color: #e2e8f0;
  border: 1px solid rgba(255, 255, 255, 0.16);
}
.floating-btn.btn-delete {
  font-weight: 700;
}

.floating-close-btn {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  border: 1px solid rgba(255, 255, 255, 0.12);
  background: rgba(255, 255, 255, 0.08);
  color: #94a3b8;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.15s ease;
  font-size: 13px;
}
.floating-close-btn:hover {
  background: rgba(239, 68, 68, 0.2);
  border-color: rgba(239, 68, 68, 0.5);
  color: #f87171;
  transform: scale(1.1);
}

/* 浮动栏进出动画 */
.floating-bar-slide-enter-active,
.floating-bar-slide-leave-active {
  transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
}
.floating-bar-slide-enter-from,
.floating-bar-slide-leave-to {
  opacity: 0;
  transform: translate(-50%, 20px) scale(0.95);
}
</style>

<style>
.feat-overlay {
  overflow: hidden !important;
}
.feat-overlay .el-overlay-dialog {
  overflow: hidden !important;
}

/* ════════════════ 3D实体液态水晶 Element Plus 全局表格与弹窗系统 (Liquid Glass System) ════════════════ */
:root {
  --el-table-border-color: rgba(255, 255, 255, 0.75);
  --el-table-header-bg-color: rgba(255, 255, 255, 0.88);
  --el-table-row-hover-bg-color: rgba(255, 255, 255, 0.95);
  --el-table-current-row-bg-color: rgba(224, 242, 254, 0.78);
}

/* ──────────── Table Container & Grid (3D 通透水晶厚玻璃拟态卡片，对齐素材 c8b5b50986689a684f7f898b714d8a5a.jpg) ──────────── */
.octopus-table-container {
  flex: 1;
  min-height: 0;
  position: relative;
  background: transparent;
}

.octopus-table-grid {
  background: transparent !important;
  font-size: 12px;
}

/* 3D 水晶磨砂质感表头与统一居中对齐 */
.octopus-table-grid .el-table__header th.el-table__cell {
  background: rgba(248, 250, 252, 0.75) !important;
  color: #334155 !important;
  font-size: 11.5px !important;
  font-weight: 700 !important;
  letter-spacing: 0.3px;
  padding: 8px 8px !important;
  border-bottom: 1px solid rgba(226, 232, 240, 0.65) !important;
  border-right: none !important;
}

.octopus-table-grid .el-table__header th.el-table__cell .cell {
  color: #334155 !important;
  font-weight: 700 !important;
}

/* 行设计与微弱交替 (半透明柔和磨砂底) */
.octopus-table-grid .el-table__row td.el-table__cell {
  padding: 6px 8px !important;
  background: rgba(255, 255, 255, 0.45) !important;
  border-bottom: 1px solid rgba(241, 245, 249, 0.6) !important;
  border-right: none !important;
  color: #334155 !important;
  transition: background 0.16s ease !important;
}

.octopus-table-grid .el-table__row--striped td.el-table__cell {
  background: rgba(248, 250, 252, 0.35) !important;
}

/* 悬浮行高光激活 (柔和天蓝微光) */
.octopus-table-grid .el-table__row:hover > td.el-table__cell {
  background: rgba(240, 249, 255, 0.85) !important;
}
.octopus-table-grid .el-table__row:hover > td.el-table__cell:first-child {
  box-shadow: inset 3.5px 0 0 #38bdf8 !important;
}

/* 选中聚焦行 (清澈海蓝微光) */
.octopus-table-grid .is-focused-row > td.el-table__cell {
  background: rgba(224, 242, 254, 0.75) !important;
}
.octopus-table-grid .is-focused-row > td.el-table__cell:first-child {
  box-shadow: inset 3.5px 0 0 #0284c7 !important;
}

/* 固定列：高不透明度磨砂，防止滚动透字错位同时保持轻盈 */
.octopus-table-grid .el-table__fixed,
.octopus-table-grid .el-table__fixed-right,
.octopus-table-grid .el-table__fixed-right-patch,
.octopus-table-grid .el-table__fixed-body-wrapper {
  background: rgba(255, 255, 255, 0.94) !important;
  backdrop-filter: blur(16px);
  box-shadow: -4px 0 16px rgba(15, 23, 42, 0.05) !important;
}

.octopus-table-grid .el-table__fixed-right th.el-table__cell,
.octopus-table-grid .el-table__fixed th.el-table__cell,
.octopus-table-grid .el-table__fixed-right-patch {
  background: rgba(248, 250, 252, 0.94) !important;
  color: #334155 !important;
  font-weight: 700 !important;
  border-bottom: 1px solid rgba(226, 232, 240, 0.65) !important;
}
.octopus-table-grid .el-table__fixed th.el-table__cell .cell,
.octopus-table-grid .el-table__fixed-right th.el-table__cell .cell {
  color: #334155 !important;
  font-weight: 700 !important;
}

.octopus-table-grid .el-table__fixed-right td.el-table__cell,
.octopus-table-grid .el-table__fixed td.el-table__cell {
  background: rgba(255, 255, 255, 0.94) !important;
  color: #334155 !important;
}

.octopus-table-grid .el-table__fixed-right tr:hover td.el-table__cell,
.octopus-table-grid .el-table__fixed tr:hover td.el-table__cell {
  background: rgba(241, 245, 249, 0.95) !important;
}

/* 3D 水晶多选框 (对齐素材 2ac2e3a9c1fd2371a185add9ac5a345a.jpg 中的复选框) */
.octopus-table-grid .el-checkbox__inner {
  border-radius: 6px !important;
  background: rgba(255, 255, 255, 0.9) !important;
  border: 1.5px solid rgba(203, 213, 225, 0.9) !important;
  box-shadow: inset 0 1px 1px #ffffff, 0 2px 5px rgba(15, 23, 42, 0.06) !important;
  transition: all 0.16s ease !important;
}
.octopus-table-grid .el-checkbox__inner:hover {
  border-color: #0284c7 !important;
  box-shadow: 0 0 0 2px rgba(2, 132, 199, 0.2), inset 0 1px 1px #ffffff !important;
}
.octopus-table-grid .el-checkbox__input.is-checked .el-checkbox__inner,
.octopus-table-grid .el-checkbox__input.is-indeterminate .el-checkbox__inner {
  background: linear-gradient(180deg, #38bdf8 0%, #0284c7 100%) !important;
  border-color: #0284c7 !important;
  box-shadow: 0 2px 6px rgba(2, 132, 199, 0.35), inset 0 1px 1px rgba(255, 255, 255, 0.8) !important;
}

/* 密度控制规范 */
.octopus-table-grid.density-compact .el-table__header th.el-table__cell,
.octopus-table-grid.density-compact .el-table__fixed th.el-table__cell,
.octopus-table-grid.density-compact .el-table__fixed-right th.el-table__cell {
  padding: 6px 8px !important;
}
.octopus-table-grid.density-compact .el-table__row td.el-table__cell,
.octopus-table-grid.density-compact .el-table__fixed td.el-table__cell,
.octopus-table-grid.density-compact .el-table__fixed-right td.el-table__cell {
  padding: 3.5px 8px !important;
}
.octopus-table-grid.density-compact .account-cell-container {
  gap: 1.5px;
}
.octopus-table-grid.density-compact .account-main-line {
  gap: 4px;
}
.octopus-table-grid.density-compact .email-text {
  font-size: 11.5px;
}
.octopus-table-grid.density-compact .provider-avatar-badge {
  width: 16px;
  height: 16px;
  font-size: 9px;
}
.octopus-table-grid.density-compact .meta-badge-pill {
  height: 17px;
  font-size: 10px;
  padding: 0 4px;
}
.octopus-table-grid.density-compact .sec-badge {
  padding: 1px 5px;
  font-size: 9.5px;
}
.octopus-table-grid.density-compact .octopus-row-btn {
  height: 21px;
  padding: 0 7px;
  font-size: 10px;
}
.octopus-table-grid.density-compact .octopus-row-btn.btn-more {
  width: 21px;
  height: 21px;
}

.octopus-table-grid.density-default .el-table__header th.el-table__cell,
.octopus-table-grid.density-default .el-table__fixed th.el-table__cell,
.octopus-table-grid.density-default .el-table__fixed-right th.el-table__cell {
  padding: 9px 8px !important;
}
.octopus-table-grid.density-default .el-table__row td.el-table__cell,
.octopus-table-grid.density-default .el-table__fixed td.el-table__cell,
.octopus-table-grid.density-default .el-table__fixed-right td.el-table__cell {
  padding: 6px 8px !important;
}

.octopus-table-grid.density-relaxed .el-table__header th.el-table__cell,
.octopus-table-grid.density-relaxed .el-table__fixed th.el-table__cell,
.octopus-table-grid.density-relaxed .el-table__fixed-right th.el-table__cell {
  padding: 12px 8px !important;
}
.octopus-table-grid.density-relaxed .el-table__row td.el-table__cell,
.octopus-table-grid.density-relaxed .el-table__fixed td.el-table__cell,
.octopus-table-grid.density-relaxed .el-table__fixed-right td.el-table__cell {
  padding: 9px 8px !important;
}
.octopus-table-grid.density-relaxed .account-cell-container {
  gap: 4px;
}
.octopus-table-grid.density-relaxed .account-main-line {
  gap: 7px;
}
.octopus-table-grid.density-relaxed .email-text {
  font-size: 13px;
}
.octopus-table-grid.density-relaxed .provider-avatar-badge {
  width: 20px;
  height: 20px;
  font-size: 11px;
}
.octopus-table-grid.density-relaxed .meta-badge-pill {
  height: 21px;
  font-size: 11px;
  padding: 0 7px;
}
.octopus-table-grid.density-relaxed .sec-badge {
  padding: 2.5px 8px;
  font-size: 11px;
}
.octopus-table-grid.density-relaxed .octopus-row-btn {
  height: 26px;
  padding: 0 11px;
  font-size: 11.5px;
}
.octopus-table-grid.density-relaxed .octopus-row-btn.btn-more {
  width: 26px;
  height: 26px;
}

/* ──────────── 1. 账号与网络出口规范化 (严格左对齐) ──────────── */
.account-cell-container {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  justify-content: center;
  gap: 3px;
  width: 100%;
  padding-left: 4px;
}
.account-main-line {
  display: inline-flex;
  align-items: center;
  justify-content: flex-start;
  gap: 6px;
  max-width: 100%;
}
.provider-avatar-badge {
  width: 18px;
  height: 18px;
  border-radius: 4px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 10px;
  flex-shrink: 0;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.1);
}
.email-text {
  font-size: 12.5px;
  font-weight: 600;
  color: #0f172a;
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 195px;
  transition: color 0.15s ease;
  letter-spacing: -0.01em;
}
.email-text:hover {
  color: #0284c7;
  text-decoration: underline;
}
.email-copy-btn {
  font-size: 11px;
  color: #94a3b8;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s ease;
}
.account-main-line:hover .email-copy-btn {
  opacity: 1;
}

.account-meta-line {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  flex-wrap: wrap;
  max-width: 100%;
}
.meta-badge-pill {
  display: inline-flex;
  align-items: center;
  height: 19px;
  padding: 0 6px;
  border-radius: 9999px;
  font-size: 10.5px;
  border: 1px solid rgba(255, 255, 255, 0.9);
  background: rgba(255, 255, 255, 0.85);
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04), inset 0 1px 1px #ffffff;
  color: #475569;
  white-space: nowrap;
  transition: all 0.15s ease;
}
.country-pill {
  color: #0369a1;
  background: rgba(224, 242, 254, 0.75);
  border-color: rgba(186, 230, 253, 0.8);
  font-weight: 600;
  cursor: pointer;
}
.country-pill:hover {
  border-color: #38bdf8;
  color: #0284c7;
  background: #ffffff;
}
.ip-pill {
  color: #475569;
  cursor: pointer;
}
.ip-pill:hover {
  color: #0284c7;
  border-color: rgba(56, 189, 248, 0.8);
  background: #ffffff;
}
.pickup-pill {
  color: #0284c7;
  border-color: rgba(56, 189, 248, 0.6);
  background: rgba(240, 249, 255, 0.85);
  cursor: pointer;
}
.pickup-pill:hover {
  background: #0284c7;
  color: #ffffff;
}

/* ──────────── 2. 密码 / 2FA 3D 水晶微胶囊徽章 ──────────── */
.sec-col-wrapper {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  width: 100%;
}
.sec-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 9px;
  height: 22px;
  border-radius: 9999px;
  font-size: 10.5px;
  line-height: 1.1;
  font-weight: 800;
  letter-spacing: 0.2px;
  cursor: pointer;
  user-select: none;
  position: relative;
  overflow: hidden;
  transition: all 0.16s ease;
  white-space: nowrap;
  border: 1.2px solid rgba(255, 255, 255, 0.95);
  border-top: 1.8px solid #ffffff;
  border-bottom: 1.2px solid rgba(5, 150, 105, 0.4);
}
.sec-badge::before {
  content: '';
  position: absolute;
  top: 1px;
  left: 10%;
  right: 10%;
  height: 40%;
  border-radius: 9999px 9999px 38% 38%;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.85) 0%, rgba(255, 255, 255, 0.15) 75%, transparent 100%);
  pointer-events: none;
}
.sec-badge:hover {
  transform: translateY(-1px);
}

/* 密码✓ 3D 薄荷/海盐绿液态果冻 (1:1 复刻素材 3c96dc7ad762c523c88e687794b9c39a.jpg 的微晶胶囊) */
.sec-pwd-ok {
  background: linear-gradient(180deg, rgba(236, 253, 245, 0.96) 0%, rgba(209, 250, 229, 0.9) 50%, rgba(167, 243, 208, 0.78) 100%) !important;
  color: #065f46 !important;
  border-color: rgba(255, 255, 255, 0.95) !important;
  border-top-color: #ffffff !important;
  border-bottom-color: rgba(167, 243, 208, 0.85) !important;
  box-shadow: 0 2px 6px rgba(16, 185, 129, 0.22), inset 0 1.2px 1.5px #ffffff, inset 0 -1px 1.5px rgba(5, 150, 105, 0.12) !important;
}
.sec-pwd-ok:hover {
  background: linear-gradient(180deg, rgba(236, 253, 245, 1) 0%, rgba(209, 250, 229, 0.95) 100%) !important;
  box-shadow: 0 4px 10px rgba(16, 185, 129, 0.35), inset 0 1.5px 1.5px #ffffff !important;
}

/* 密码× 3D 珊瑚红微晶 */
.sec-pwd-no {
  background: linear-gradient(180deg, rgba(254, 242, 242, 0.96) 0%, rgba(254, 226, 226, 0.9) 50%, rgba(254, 205, 211, 0.78) 100%) !important;
  color: #991b1b !important;
  border-color: rgba(255, 255, 255, 0.95) !important;
  border-top-color: #ffffff !important;
  border-bottom-color: rgba(254, 205, 211, 0.85) !important;
  box-shadow: 0 2px 6px rgba(239, 68, 68, 0.18), inset 0 1.2px 1.5px #ffffff, inset 0 -1px 1.5px rgba(220, 38, 38, 0.1) !important;
}
.sec-pwd-no:hover {
  background: linear-gradient(180deg, rgba(254, 242, 242, 1) 0%, rgba(254, 226, 226, 0.95) 100%) !important;
  box-shadow: 0 4px 10px rgba(239, 68, 68, 0.3), inset 0 1.5px 1.5px #ffffff !important;
}

/* 2FA✓ 3D 薄荷/海盐绿液态果冻 (1:1 复刻素材 3c96dc7ad762c523c88e687794b9c39a.jpg 的微晶胶囊) */
.sec-2fa-ok {
  background: linear-gradient(180deg, rgba(236, 253, 245, 0.96) 0%, rgba(209, 250, 229, 0.9) 50%, rgba(167, 243, 208, 0.78) 100%) !important;
  color: #065f46 !important;
  border-color: rgba(255, 255, 255, 0.95) !important;
  border-top-color: #ffffff !important;
  border-bottom-color: rgba(167, 243, 208, 0.85) !important;
  box-shadow: 0 2px 6px rgba(16, 185, 129, 0.22), inset 0 1.2px 1.5px #ffffff, inset 0 -1px 1.5px rgba(5, 150, 105, 0.12) !important;
}
.sec-2fa-ok:hover {
  background: linear-gradient(180deg, rgba(236, 253, 245, 1) 0%, rgba(209, 250, 229, 0.95) 100%) !important;
  box-shadow: 0 4px 10px rgba(16, 185, 129, 0.35), inset 0 1.5px 1.5px #ffffff !important;
}

/* 2FA× 3D 珊瑚红微晶 */
.sec-2fa-no {
  background: linear-gradient(180deg, rgba(254, 242, 242, 0.96) 0%, rgba(254, 226, 226, 0.9) 50%, rgba(254, 205, 211, 0.78) 100%) !important;
  color: #991b1b !important;
  border-color: rgba(255, 255, 255, 0.95) !important;
  border-top-color: #ffffff !important;
  border-bottom-color: rgba(254, 205, 211, 0.85) !important;
  box-shadow: 0 2px 6px rgba(239, 68, 68, 0.18), inset 0 1.2px 1.5px #ffffff, inset 0 -1px 1.5px rgba(220, 38, 38, 0.1) !important;
}
.sec-2fa-no:hover {
  background: linear-gradient(180deg, rgba(254, 242, 242, 1) 0%, rgba(254, 226, 226, 0.95) 100%) !important;
  box-shadow: 0 4px 10px rgba(239, 68, 68, 0.3), inset 0 1.5px 1.5px #ffffff !important;
}

/* ──────────── 3. Token 凭据状态规范 (3D 水晶微胶囊，对齐素材 2ac2e3a9c1fd2371a185add9ac5a345a.jpg) ──────────── */
.token-cell-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 3px;
  width: 100%;
}
.token-main-line {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  padding: 2px 9px;
  border-radius: 9999px;
  background: linear-gradient(180deg, rgba(236, 253, 245, 0.95) 0%, rgba(209, 250, 229, 0.88) 100%);
  border: 1.2px solid rgba(52, 211, 153, 0.65);
  border-top: 1.6px solid #ffffff;
  border-bottom: 1.2px solid rgba(16, 185, 129, 0.35);
  box-shadow: 0 2px 6px rgba(16, 185, 129, 0.18), inset 0 1px 1px #ffffff;
  transition: all 0.16s ease;
}
.token-main-line:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 10px rgba(16, 185, 129, 0.28);
}
.token-main-text.is-ok {
  color: #065f46 !important;
  font-weight: 700;
}
.token-main-text.is-refreshable {
  color: #dc2626 !important;
}
.token-main-text:hover {
  text-decoration: underline;
}

.token-sub-line {
  font-size: 10px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  color: #64748b;
  padding: 1px 7px;
  border-radius: 9999px;
  background: rgba(241, 245, 249, 0.75);
  border: 1px solid rgba(255, 255, 255, 0.95);
  box-shadow: inset 0 1px 1px #ffffff;
}
.token-sub-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  flex-shrink: 0;
}
.token-sub-dot.sub-dot-active {
  background: #10b981;
  box-shadow: 0 0 5px #34d399;
}
.token-sub-dot.sub-dot-none {
  background: #cbd5e1;
}
.rt-active-text {
  color: #0284c7;
  font-weight: 700;
  cursor: pointer;
}
.rt-active-text:hover {
  text-decoration: underline;
}
.rt-none-text {
  color: #94a3b8;
  cursor: pointer;
}
.rt-none-text:hover {
  color: #0284c7;
  text-decoration: underline;
}

/* ──────────── 4. 3D 水晶 AT 有效期微芯片 (1:1 复刻素材 2ac2e3a9c1fd2371a185add9ac5a345a.jpg 的晶体胶囊) ──────────── */
.at-expiry-modern-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 23px;
  padding: 0 10px;
  border-radius: 9999px;
  font-size: 10.5px;
  font-family: var(--el-font-family-monospace, monospace);
  font-weight: 600;
  cursor: pointer;
  position: relative;
  overflow: hidden;
  transition: all 0.16s ease;
  white-space: nowrap;
  user-select: none;
  box-sizing: border-box;
  border: 1.2px solid rgba(255, 255, 255, 0.95);
  border-top: 1.8px solid #ffffff;
  border-bottom: 1.2px solid rgba(203, 213, 225, 0.6);
  box-shadow: 0 2px 6px rgba(15, 23, 42, 0.04), inset 0 1.2px 1.2px #ffffff;
}
.at-expiry-modern-badge::before {
  content: '';
  position: absolute;
  top: 1px;
  left: 8%;
  right: 8%;
  height: 42%;
  border-radius: 9999px 9999px 40% 40%;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.92) 0%, rgba(255, 255, 255, 0.2) 75%, transparent 100%);
  pointer-events: none;
}
.at-expiry-modern-badge.at-exp-ok {
  background: linear-gradient(180deg, rgba(236, 253, 245, 0.96) 0%, rgba(209, 250, 229, 0.88) 100%) !important;
  border-color: rgba(167, 243, 208, 0.85) !important;
  border-top-color: #ffffff !important;
  color: #065f46 !important;
  box-shadow: 0 2px 8px -1px rgba(16, 185, 129, 0.22), inset 0 1.2px 1.5px #ffffff, inset 0 -1px 1.5px rgba(5, 150, 105, 0.1) !important;
}
.at-expiry-modern-badge.at-exp-ok .expiry-status-dot {
  background: #10b981;
  box-shadow: 0 0 5px #34d399;
}
.at-expiry-modern-badge.at-exp-ok:hover {
  background: linear-gradient(180deg, rgba(236, 253, 245, 1) 0%, rgba(209, 250, 229, 0.95) 100%) !important;
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(16, 185, 129, 0.35), inset 0 1.5px 1.5px #ffffff !important;
}

.at-expiry-modern-badge.at-exp-warn {
  background: linear-gradient(180deg, rgba(255, 251, 235, 0.96) 0%, rgba(254, 243, 199, 0.88) 100%) !important;
  border-color: rgba(253, 230, 138, 0.85) !important;
  border-top-color: #ffffff !important;
  color: #92400e !important;
  box-shadow: 0 2px 8px -1px rgba(245, 158, 11, 0.22), inset 0 1.2px 1.5px #ffffff, inset 0 -1px 1.5px rgba(217, 119, 6, 0.1) !important;
}
.at-expiry-modern-badge.at-exp-warn .expiry-status-dot {
  background: #f59e0b;
  box-shadow: 0 0 5px #fbbf24;
}
.at-expiry-modern-badge.at-exp-warn:hover {
  background: linear-gradient(180deg, rgba(255, 251, 235, 1) 0%, rgba(254, 243, 199, 0.95) 100%) !important;
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(245, 158, 11, 0.35), inset 0 1.5px 1.5px #ffffff !important;
}

.at-expiry-modern-badge.at-exp-dead {
  background: linear-gradient(180deg, rgba(254, 242, 242, 0.96) 0%, rgba(254, 226, 226, 0.88) 100%) !important;
  border-color: rgba(254, 205, 211, 0.85) !important;
  border-top-color: #ffffff !important;
  color: #991b1b !important;
  box-shadow: 0 2px 8px -1px rgba(239, 68, 68, 0.2), inset 0 1.2px 1.5px #ffffff, inset 0 -1px 1.5px rgba(185, 28, 28, 0.1) !important;
}
.at-expiry-modern-badge.at-exp-dead .expiry-status-dot {
  background: #ef4444;
  box-shadow: 0 0 5px #f87171;
}
.at-expiry-modern-badge.at-exp-dead:hover {
  background: linear-gradient(180deg, rgba(254, 242, 242, 1) 0%, rgba(254, 226, 226, 0.95) 100%) !important;
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(239, 68, 68, 0.32), inset 0 1.5px 1.5px #ffffff !important;
}

.at-expiry-modern-badge.at-exp-none,
.at-expiry-modern-badge.at-exp-unknown {
  background: rgba(241, 245, 249, 0.85);
  border: 1.2px solid rgba(203, 213, 225, 0.7);
  color: #64748b;
}
.at-expiry-modern-badge.at-exp-none .expiry-status-dot,
.at-expiry-modern-badge.at-exp-unknown .expiry-status-dot {
  background: #94a3b8;
}

.expiry-status-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  flex-shrink: 0;
}
.expiry-val-text {
  letter-spacing: -0.01em;
}

.at-exp-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border-radius: 9999px;
  padding: 0 8px;
  height: 22px;
  font-size: 11px;
  font-family: var(--el-font-family-monospace, monospace);
  cursor: pointer;
  white-space: nowrap;
  font-weight: 600;
}
.dossier-tag.at-exp-ok { color: #065f46; background: rgba(236, 253, 245, 0.9); }
.dossier-tag.at-exp-warn { color: #92400e; background: rgba(254, 243, 199, 0.9); }
.dossier-tag.at-exp-dead { color: #991b1b; background: rgba(254, 242, 242, 0.9); }
.dossier-tag.at-exp-none,
.dossier-tag.at-exp-unknown { color: #64748b; background: rgba(241, 245, 249, 0.9); }

/* ──────────── 5. 套餐与特权订阅 (3D 水晶微晶药丸，对齐素材 2ac2e3a9c1fd2371a185add9ac5a345a.jpg) ──────────── */
.cell-entitlements-block {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  flex-wrap: wrap;
  width: 100%;
}
.entitlement-badge {
  display: inline-flex;
  align-items: center;
  padding: 0 10px;
  height: 23px;
  border-radius: 9999px;
  font-size: 11px;
  font-weight: 800;
  cursor: pointer;
  position: relative;
  overflow: hidden;
  transition: all 0.16s ease;
  border: 1.2px solid rgba(255, 255, 255, 0.95);
  border-top: 1.8px solid #ffffff;
  border-bottom: 1.2px solid rgba(203, 213, 225, 0.6);
  box-shadow: 0 3px 8px -1px rgba(15, 23, 42, 0.08), inset 0 1.2px 1.2px #ffffff, inset 0 -1.2px 1.5px rgba(0, 0, 0, 0.06);
}
.entitlement-badge::before {
  content: '';
  position: absolute;
  top: 1px;
  left: 10%;
  right: 10%;
  height: 40%;
  border-radius: 9999px 9999px 38% 38%;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.85) 0%, rgba(255, 255, 255, 0.15) 75%, transparent 100%);
  pointer-events: none;
}
.entitlement-badge:hover {
  transform: translateY(-1px);
}
.entitlement-badge.success {
  background: linear-gradient(180deg, rgba(236, 253, 245, 0.98) 0%, rgba(209, 250, 229, 0.92) 100%) !important;
  color: #065f46 !important;
  border-color: rgba(52, 211, 153, 0.7) !important;
  border-top-color: #ffffff !important;
  box-shadow: 0 3px 8px rgba(16, 185, 129, 0.25), inset 0 1.5px 1.5px #ffffff, inset 0 -1.2px 1.8px rgba(5, 150, 105, 0.15) !important;
}
.entitlement-badge.success:hover {
  box-shadow: 0 5px 12px rgba(16, 185, 129, 0.38), inset 0 1.5px 1.5px #ffffff !important;
}
.entitlement-badge.primary,
.entitlement-badge.cyan {
  background: linear-gradient(180deg, rgba(238, 248, 255, 0.98) 0%, rgba(224, 242, 254, 0.92) 100%) !important;
  color: #0284c7 !important;
  border-color: rgba(56, 189, 248, 0.7) !important;
  border-top-color: #ffffff !important;
  box-shadow: 0 3px 8px rgba(2, 132, 199, 0.25), inset 0 1.5px 1.5px #ffffff, inset 0 -1.2px 1.8px rgba(2, 132, 199, 0.15) !important;
}
.entitlement-badge.primary:hover,
.entitlement-badge.cyan:hover {
  box-shadow: 0 5px 12px rgba(2, 132, 199, 0.38), inset 0 1.5px 1.5px #ffffff !important;
}
.entitlement-badge.warning {
  background: linear-gradient(180deg, rgba(254, 243, 199, 0.98) 0%, rgba(253, 230, 138, 0.92) 100%) !important;
  color: #92400e !important;
  border-color: rgba(245, 158, 11, 0.7) !important;
  border-top-color: #ffffff !important;
  box-shadow: 0 3px 8px rgba(245, 158, 11, 0.25), inset 0 1.5px 1.5px #ffffff, inset 0 -1.2px 1.8px rgba(217, 119, 6, 0.15) !important;
}
.entitlement-badge.danger {
  background: linear-gradient(180deg, rgba(254, 242, 242, 0.98) 0%, rgba(254, 226, 226, 0.92) 100%) !important;
  color: #991b1b !important;
  border-color: rgba(239, 68, 68, 0.7) !important;
  border-top-color: #ffffff !important;
  box-shadow: 0 3px 8px rgba(239, 68, 68, 0.25), inset 0 1.5px 1.5px #ffffff, inset 0 -1.2px 1.8px rgba(185, 28, 28, 0.15) !important;
}
.free-plain-text {
  font-size: 10.5px;
  color: #475569;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-weight: 700;
  padding: 2px 9px;
  border-radius: 9999px;
  background: rgba(241, 245, 249, 0.75);
  border: 1px solid rgba(255, 255, 255, 0.95);
  border-top: 1.4px solid #ffffff;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04), inset 0 1px 1px #ffffff;
}
.free-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #94a3b8;
  display: inline-block;
}

/* ──────────── 6. 导出留痕与备注 ──────────── */
.cell-export-block {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  width: 100%;
}
.export-status-line {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  font-size: 11.5px;
  cursor: pointer;
}
.export-fresh-text {
  color: #0284c7;
  font-weight: 700;
}
.export-tag-label {
  color: #0369a1;
  font-weight: 600;
}
.export-date-mono {
  font-size: 10px;
  color: #64748b;
}

.export-note-text {
  font-size: 10.5px;
  color: #64748b;
  padding-left: 0;
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 140px;
  text-align: center;
}
.export-note-text:hover {
  color: #0284c7;
  text-decoration: underline;
}

/* ──────────── 7. 注册时间 ──────────── */
.cell-time-block {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 1px;
  width: 100%;
  text-align: center;
}
.time-main-mono {
  font-size: 11.5px;
  color: #1e293b;
  font-weight: 600;
}
.time-relative-text {
  font-size: 10px;
  color: #64748b;
}

/* ──────────── 8. 快捷操作列 3D 实体水晶药丸按键 (严格对齐素材 2ac2e3a9c1fd2371a185add9ac5a345a.jpg 与 3c96dc7ad762c523c88e687794b9c39a.jpg) ──────────── */
.cell-actions-block {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  width: 100%;
  white-space: nowrap;
  flex-wrap: nowrap;
}
.octopus-row-btn {
  height: 26px;
  padding: 0 10px;
  border-radius: 9999px;
  font-size: 11.5px;
  font-weight: 600;
  border: 1.2px solid rgba(255, 255, 255, 0.95);
  border-top: 1.8px solid #ffffff;
  border-bottom: 1.5px solid rgba(203, 213, 225, 0.6);
  cursor: pointer;
  transition: all 0.18s cubic-bezier(0.4, 0, 0.2, 1);
  user-select: none;
  position: relative;
  overflow: hidden;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  white-space: nowrap;
  flex-shrink: 0;
  box-shadow:
    0 3px 8px -1px rgba(15, 23, 42, 0.05),
    inset 0 1.5px 1.5px #ffffff,
    inset 0 -1.2px 1.5px rgba(15, 23, 42, 0.04);
}
.octopus-row-btn::before {
  content: '';
  position: absolute;
  top: 1px;
  left: 8%;
  right: 8%;
  height: 42%;
  border-radius: 9999px 9999px 40% 40%;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.95) 0%, rgba(255, 255, 255, 0.25) 75%, transparent 100%);
  pointer-events: none;
  z-index: 2;
}
.octopus-row-btn:hover {
  transform: translateY(-1px);
}
/* 凭证: 1:1 复刻素材 3c96dc7ad762c523c88e687794b9c39a.jpg 的 "Upgrade plan" / "Text" 微晶白玉药丸 */
.octopus-row-btn.btn-cred {
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.96) 0%, rgba(248, 250, 252, 0.88) 100%) !important;
  color: #1e293b !important;
  border-color: rgba(255, 255, 255, 0.95) !important;
  border-bottom-color: rgba(203, 213, 225, 0.65) !important;
}
.octopus-row-btn.btn-cred:hover {
  background: #ffffff !important;
  color: #0284c7 !important;
  border-color: rgba(56, 189, 248, 0.6) !important;
  border-top-color: #ffffff !important;
  box-shadow: 0 5px 14px rgba(2, 132, 199, 0.18), inset 0 1.5px 1.5px #ffffff !important;
}
/* 2FA: 1:1 复刻素材 3c96dc7ad762c523c88e687794b9c39a.jpg 的 "Primary button" 清透翠玉琉璃 */
.octopus-row-btn.btn-2fa {
  background: linear-gradient(180deg, rgba(236, 253, 245, 0.95) 0%, rgba(209, 250, 229, 0.88) 50%, rgba(167, 243, 208, 0.75) 100%) !important;
  border-color: rgba(255, 255, 255, 0.95) !important;
  border-top-color: #ffffff !important;
  border-bottom-color: rgba(167, 243, 208, 0.85) !important;
  color: #065f46 !important;
  box-shadow:
    0 3px 10px -1px rgba(16, 185, 129, 0.28),
    0 1px 3px rgba(15, 23, 42, 0.03),
    inset 0 1.5px 1.5px #ffffff,
    inset 0 -1.2px 1.8px rgba(5, 150, 105, 0.12) !important;
}
.octopus-row-btn.btn-2fa:hover {
  background: linear-gradient(180deg, rgba(236, 253, 245, 1) 0%, rgba(209, 250, 229, 0.95) 100%) !important;
  box-shadow: 0 6px 16px rgba(16, 185, 129, 0.42), inset 0 1.8px 1.8px #ffffff !important;
}
/* 刷Token: 1:1 复刻素材 3c96dc7ad762c523c88e687794b9c39a.jpg 的 "Create workspace" 冰海蓝宝琉璃 */
.octopus-row-btn.btn-refresh {
  background: linear-gradient(180deg, rgba(240, 249, 255, 0.95) 0%, rgba(224, 242, 254, 0.88) 50%, rgba(186, 230, 253, 0.75) 100%) !important;
  border-color: rgba(255, 255, 255, 0.95) !important;
  border-top-color: #ffffff !important;
  border-bottom-color: rgba(186, 230, 253, 0.85) !important;
  color: #0369a1 !important;
  box-shadow:
    0 3px 10px -1px rgba(2, 132, 199, 0.28),
    0 1px 3px rgba(15, 23, 42, 0.03),
    inset 0 1.5px 1.5px #ffffff,
    inset 0 -1.2px 1.8px rgba(2, 132, 199, 0.12) !important;
}
.octopus-row-btn.btn-refresh:hover {
  background: linear-gradient(180deg, rgba(240, 249, 255, 1) 0%, rgba(224, 242, 254, 0.95) 100%) !important;
  box-shadow: 0 6px 16px rgba(2, 132, 199, 0.42), inset 0 1.8px 1.8px #ffffff !important;
}
/* 查码: 1:1 复刻素材 3c96dc7ad762c523c88e687794b9c39a.jpg 的 "Search button" 蜜糖香槟琉璃 */
.octopus-row-btn.btn-mail {
  background: linear-gradient(180deg, rgba(255, 251, 235, 0.95) 0%, rgba(254, 243, 199, 0.88) 50%, rgba(253, 230, 138, 0.75) 100%) !important;
  border-color: rgba(255, 255, 255, 0.95) !important;
  border-top-color: #ffffff !important;
  border-bottom-color: rgba(253, 230, 138, 0.85) !important;
  color: #92400e !important;
  box-shadow:
    0 3px 10px -1px rgba(245, 158, 11, 0.28),
    0 1px 3px rgba(15, 23, 42, 0.03),
    inset 0 1.5px 1.5px #ffffff,
    inset 0 -1.2px 1.8px rgba(217, 119, 6, 0.12) !important;
}
.octopus-row-btn.btn-mail:hover {
  background: linear-gradient(180deg, rgba(255, 251, 235, 1) 0%, rgba(254, 243, 199, 0.95) 100%) !important;
  box-shadow: 0 6px 16px rgba(245, 158, 11, 0.42), inset 0 1.8px 1.8px #ffffff !important;
}
.octopus-row-btn.btn-more {
  width: 26px;
  height: 26px;
  padding: 0;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.95) 0%, rgba(241, 245, 249, 0.85) 100%) !important;
  color: #64748b !important;
  border: 1.2px solid rgba(255, 255, 255, 0.95) !important;
  border-top: 1.8px solid #ffffff !important;
  box-shadow: 0 2px 6px rgba(15, 23, 42, 0.05), inset 0 1.2px 1.2px #ffffff !important;
}
.octopus-row-btn.btn-more:hover {
  background: #ffffff !important;
  color: #0284c7 !important;
  border-color: #38bdf8 !important;
  box-shadow: 0 4px 10px rgba(2, 132, 199, 0.22) !important;
}

/* ──────────── 9. 状态指示小圆点 (果冻水晶微光) ──────────── */
.pulse-indicator-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  display: inline-block;
  flex-shrink: 0;
}
.dot-emerald {
  background: #10b981 !important;
  box-shadow: 0 0 6px #34d399 !important;
}
.dot-cyan {
  background: #06b6d4 !important;
  box-shadow: 0 0 6px #22d3ee !important;
}
.dot-amber {
  background: #f59e0b !important;
  box-shadow: 0 0 6px #fbbf24 !important;
}
.dot-rose {
  background: #ef4444 !important;
  box-shadow: 0 0 6px #f87171 !important;
}
.dot-slate {
  background: #64748b !important;
  box-shadow: 0 0 6px #94a3b8 !important;
}

/* ──────────── 10. 全局下拉菜单与气泡弹层 3D 水晶拟态 ──────────── */
.extract-dropdown-menu,
.macos-dropdown-menu,
.col-setting-popover,
.density-menu-popover {
  border-radius: 14px !important;
  background: rgba(255, 255, 255, 0.92) !important;
  backdrop-filter: blur(28px) saturate(190%) !important;
  -webkit-backdrop-filter: blur(28px) saturate(190%) !important;
  border: 1.5px solid rgba(255, 255, 255, 0.95) !important;
  border-top: 2px solid #ffffff !important;
  box-shadow: 0 20px 48px -8px rgba(15, 23, 42, 0.18), 0 8px 16px rgba(15, 23, 42, 0.06), inset 0 2px 2px #ffffff !important;
  padding: 6px !important;
}

.extract-dropdown-menu .el-dropdown-menu__item,
.macos-dropdown-menu .el-dropdown-menu__item {
  border-radius: 8px !important;
  font-size: 12px !important;
  font-weight: 600 !important;
  color: #1e293b !important;
  padding: 7px 12px !important;
  transition: all 0.15s ease !important;
}
.extract-dropdown-menu .el-dropdown-menu__item:hover,
.macos-dropdown-menu .el-dropdown-menu__item:hover {
  background: linear-gradient(180deg, rgba(238, 248, 255, 0.95) 0%, rgba(224, 242, 254, 0.8) 100%) !important;
  color: #0284c7 !important;
  box-shadow: inset 0 1px 1px #ffffff !important;
}

/* ──────────── 11. 3D实体液态水晶 OAuth 控制台弹窗 (对齐素材 961356eebdecace364b9e4e43548e143.jpg) ──────────── */
.oa-custom-dialog {
  border-radius: 20px !important;
  overflow: hidden !important;
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.94) 0%, rgba(255, 255, 255, 0.78) 50%, rgba(248, 250, 252, 0.9) 100%) !important;
  backdrop-filter: blur(32px) saturate(210%) !important;
  -webkit-backdrop-filter: blur(32px) saturate(210%) !important;
  border: 2px solid rgba(255, 255, 255, 0.95) !important;
  border-top: 2.5px solid #ffffff !important;
  box-shadow: 0 32px 72px -12px rgba(15, 23, 42, 0.22), 0 12px 28px rgba(15, 23, 42, 0.08), inset 0 2.5px 2px #ffffff !important;
}
.oa-custom-dialog .el-dialog__header {
  padding: 16px 22px 14px !important;
  margin-right: 0 !important;
  border-bottom: 1.5px solid rgba(255, 255, 255, 0.85) !important;
  background: rgba(255, 255, 255, 0.5) !important;
  backdrop-filter: blur(16px) !important;
}
.oa-custom-dialog .el-dialog__body {
  padding: 16px 22px !important;
  background: transparent !important;
}
.oa-custom-dialog .el-dialog__footer {
  padding: 14px 22px !important;
  border-top: 1.5px solid rgba(255, 255, 255, 0.85) !important;
  background: rgba(255, 255, 255, 0.55) !important;
  backdrop-filter: blur(16px) !important;
}
</style>
