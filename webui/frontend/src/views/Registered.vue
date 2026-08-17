<script setup>
import { computed, nextTick, onActivated, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
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
  Loading,
} from '@element-plus/icons-vue'
import {
  listRegistered,
  listRegisteredEmails,
  getRegistered,
  deleteRegistered,
  bulkDeleteRegistered,
  cleanInvalidRegistered,
  bulkDeleteAccounts,
  listExportFormats,
  exportRegistered,
  updateCredentials,
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
  stopOAuthExport,
  oauthExportStreamUrl,
  getOAuthExportLog,
  downloadOAuthExportCpa,
  downloadOAuthExportSub2,
  startTokenRefresh,
  stopTokenRefresh,
  tokenRefreshStreamUrl,
  getTokenRefreshLog,
  downloadTokenRefreshExport,
} from '@/api/register'
import { saveSmsConfig } from '@/api/settings'
import { copyText, fmtTime, createSSE } from '@/api/request'
import { useFormStore, proxyText, COUNTRY_OPTIONS, formatCountry } from '@/stores/form'
import { useProxyStore } from '@/stores/proxy'
import { useRuntimeStore } from '@/stores/runtime'
import StatusDot from '@/components/StatusDot.vue'
import ExtractTaskModal from '@/components/ExtractTaskModal.vue'

const { form } = storeToRefs(useFormStore())
// 检测用的代理必须能从代理池里挑
const { list: proxyList } = storeToRefs(useProxyStore())
const runtime = useRuntimeStore()
const { dataVersion } = storeToRefs(runtime)

// ════════════════════════ 全渠道提炼模态任务台 ════════════════════════
const extractModalVisible = ref(false)
const extractModalChannel = ref('paypal')
const extractModalEmails = ref([])

async function openExtractChannel(channelKey) {
  extractModalChannel.value = channelKey
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
      const res = await listRegisteredEmails(filter.value)
      extractModalEmails.value = res.emails || []
      extractModalVisible.value = true
    } catch (_) {}
  }
}

// 分页与数据
const rows = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const filter = ref('all')
const searchKeyword = ref('')
const selected = ref([])
const loading = ref(false)
let searchTimer = null

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
  error: 'danger',
}
function plusOf(row) {
  if (!row || !row.plus_check) return null
  const p = row.plus_check
  let label = p.label || p.status || ''
  if (p.status === 'plus_eligible' || label === '可领Plus试用' || label === '🎁 可领Plus试用') {
    label = 'Plus试用'
  }
  return { ...p, label }
}

const OAUTH_STATUS_META = {
  success:    { type: 'success', label: '✅ 成功', effect: 'light' },
  need_phone: { type: 'warning', label: '📱 需接码', effect: 'light' },
  failed:     { type: 'danger',  label: '❌ 失败', effect: 'light' },
  error:      { type: 'danger',  label: '❌ 失败', effect: 'light' },
}

function oauthMeta(row) {
  const st = (row.oauth_status || row.oauth_export?.status || '').toLowerCase().trim()
  if (!st) return null
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

const plusRows = computed(() =>
  Object.values(plusItems.value).map((item) => ({ ...item })),
)

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

function scrollPlusLog() {
  const box = document.getElementById('plus-log-box')
  if (box) box.scrollTop = box.scrollHeight
}

async function openPlusItemLog(row) {
  currentPlusLogItem.value = row
  plusLogLines.value = []
  plusLogModalVisible.value = true
  plusLogLoading.value = true

  try {
    if (plusTaskId.value) {
      const res = await getPlusCheckLog(plusTaskId.value, row.email)
      plusLogLines.value = res.lines || []
    } else {
      plusLogLines.value = row.logs || ['暂无日志']
    }
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
const healthItems = ref({})
const healthLogs = ref([])
const healthForm = reactive({
  mode: 'token', // 'token' (Token 状态验活) | 'plan' (套餐与试用资格探测)
  proxy: '__POOL__',
  proxyCountry: 'BR',
  workers: 5,
  timeout: 20,
})

// 弹窗内单账号日志终端
const healthLogModalVisible = ref(false)
const currentHealthLogItem = ref(null)
const healthLogLines = ref([])
const healthLogLoading = ref(false)

// ── 验活高性能分页、状态筛选与批量节流更新 (防万号卡死) ──
const healthPage = ref(1)
const healthPageSize = ref(50)
const healthFilter = ref('all') // 'all' | 'running' | 'failed' | 'done' | 'pending'
const healthSearch = ref('')

const healthFilteredRows = computed(() => {
  const list = Object.values(healthItems.value)
  const kw = healthSearch.value.trim().toLowerCase()
  const f = healthFilter.value
  return list.filter((item) => {
    if (kw && !item.email.toLowerCase().includes(kw)) return false
    if (f === 'all') return true
    if (f === 'running') return item.status === 'running'
    if (f === 'pending') return item.status === 'pending'
    if (f === 'failed') return isHealthFailedRow(item)
    if (f === 'done') return item.status === 'done' && !isHealthFailedRow(item)
    return true
  })
})

const healthDisplayRows = computed(() => {
  const rows = healthFilteredRows.value
  const start = (healthPage.value - 1) * healthPageSize.value
  return rows.slice(start, start + healthPageSize.value)
})

const healthRows = computed(() =>
  Object.values(healthItems.value).map((item) => ({ ...item })),
)

let healthUpdateTimer = null
let healthPendingUpdates = {}
let healthPendingLogs = []

function flushHealthUpdates() {
  if (healthUpdateTimer) {
    cancelAnimationFrame(healthUpdateTimer)
    healthUpdateTimer = null
  }
  if (Object.keys(healthPendingUpdates).length > 0) {
    const copy = { ...healthItems.value }
    for (const [em, up] of Object.entries(healthPendingUpdates)) {
      if (!copy[em]) {
        copy[em] = { email: em, mode: healthForm.mode, status: 'pending', step_text: '排队中...', result: null, elapsed: 0 }
      }
      Object.assign(copy[em], up)
    }
    healthItems.value = copy
    healthPendingUpdates = {}
  }
  if (healthPendingLogs.length > 0) {
    healthLogs.value.push(...healthPendingLogs)
    if (healthLogs.value.length > 200) {
      healthLogs.value = healthLogs.value.slice(-200)
    }
    healthPendingLogs = []
  }
}

function scheduleHealthUpdate() {
  if (healthUpdateTimer) return
  healthUpdateTimer = requestAnimationFrame(() => {
    healthUpdateTimer = null
    flushHealthUpdates()
  })
}

const healthStats = computed(() => {
  const items = Object.values(healthItems.value)
  const tot = items.length || healthTargetEmails.value.length || 0
  const done = items.filter((i) => i.status === 'done').length
  const running = items.filter((i) => i.status === 'running').length
  const pending = items.filter((i) => i.status === 'pending').length
  const token_valid = items.filter((i) => i.result && i.result.status === 'token_valid').length
  const plus_active = items.filter((i) => i.result && i.result.status === 'plus_active').length
  const plus_eligible = items.filter((i) => i.result && i.result.status === 'plus_eligible').length
  const pro_active = items.filter((i) => i.result && (i.result.status === 'pro_active' || i.result.status === 'pro_20x' || i.result.status === 'pro_5x' || i.result.status === 'pro_eligible')).length
  const team_active = items.filter((i) => i.result && i.result.status === 'team_active').length
  const free = items.filter((i) => i.result && i.result.status === 'free').length
  const banned = items.filter((i) => i.result && i.result.status === 'banned').length
  const token_invalid = items.filter((i) => i.result && i.result.status === 'token_invalid').length
  const error = items.filter((i) => isHealthFailedRow(i)).length
  const percent = tot > 0 ? Math.round((done / tot) * 100) : 0
  return {
    total: tot, done, running, pending,
    token_valid, plus_active, plus_eligible, pro_active, team_active, free, banned, token_invalid, error,
    percent,
  }
})

async function openHealthCheck(scope = 'selected', mode = 'token') {
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
  healthForm.mode = mode
  healthPage.value = 1
  healthFilter.value = 'all'
  healthSearch.value = ''

  if (!healthRunning.value) {
    healthTaskId.value = ''
    healthLogs.value = []
    healthConfigCollapsed.value = true
    const initMap = Object.create(null)
    for (const em of emails) {
      initMap[em] = { email: em, mode, status: 'pending', step_text: '排队中...', result: null, elapsed: 0 }
    }
    healthItems.value = initMap
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
  if (healthRunning.value) {
    ElMessage.info('验活任务在后台继续运行，可随时重新打开查看进度')
  }
  if (healthEs.value && !healthRunning.value) {
    healthEs.value.close()
    healthEs.value = null
  }
  healthVisible.value = false
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
  healthLogs.value = []
  healthPage.value = 1
  healthFilter.value = 'all'
  healthSearch.value = ''
  healthConfigCollapsed.value = true

  const initMap = Object.create(null)
  for (const em of emails) {
    initMap[em] = { email: em, mode: healthForm.mode, status: 'pending', step_text: '排队中...', result: null, elapsed: 0 }
  }
  healthItems.value = initMap

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
      workers: healthForm.workers || 5,
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
            healthItems.value = snap.items
          }
        } catch (_) {}
      },
      progress: (ev) => {
        try {
          const msg = JSON.parse(ev.data)
          if (msg.email) {
            if (!healthPendingUpdates[msg.email]) healthPendingUpdates[msg.email] = {}
            if (msg.status !== undefined) healthPendingUpdates[msg.email].status = msg.status
            if (msg.step_text !== undefined) healthPendingUpdates[msg.email].step_text = msg.step_text
            if (msg.result !== undefined) healthPendingUpdates[msg.email].result = msg.result
            if (msg.elapsed !== undefined) healthPendingUpdates[msg.email].elapsed = msg.elapsed
            scheduleHealthUpdate()
          }
        } catch (_) {}
      },
      log: (ev) => {
        try {
          const msg = JSON.parse(ev.data)
          if (msg.line) {
            healthPendingLogs.push(msg.line)
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
        ElMessage.success('批量验活任务已全部执行完成！')
        load(false)
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

function scrollHealthLog() {
  const box = document.getElementById('health-log-box')
  if (box) box.scrollTop = box.scrollHeight
}

async function openHealthItemLog(row) {
  currentHealthLogItem.value = row
  healthLogLines.value = []
  healthLogModalVisible.value = true
  healthLogLoading.value = true

  try {
    if (healthTaskId.value) {
      const res = await getHealthCheckLog(healthTaskId.value, row.email)
      healthLogLines.value = res.lines || []
    } else {
      healthLogLines.value = row.logs || ['暂无日志']
    }
  } catch (e) {
    healthLogLines.value = ['读取日志失败: ' + (e.response?.data?.detail || e.message)]
  } finally {
    healthLogLoading.value = false
  }
}

// ── 验活异常重试相关计算与操作 (支持批量与单行重试) ──
const failedHealthEmails = computed(() => {
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
  healthItems.value = newMap
  startHealthCheckTask()
}

function retrySingleHealthCheck(row) {
  healthTargetEmails.value = [row.email]
  healthItems.value = {
    ...healthItems.value,
    [row.email]: { email: row.email, mode: healthForm.mode, status: 'pending', step_text: '准备重试...', result: null, elapsed: 0 },
  }
  startHealthCheckTask()
}

// ════════════════════════ Token 刷新与重登工作台 (Token Refresh Studio) ════════════════════════
const refreshVisible = ref(false)
const refreshRunning = ref(false)
const refreshTaskId = ref('')
const refreshEs = ref(null)
const refreshTargetEmails = ref([])
const refreshActiveTab = ref('network')
const refreshConfigCollapsed = ref(true)

const refreshForm = reactive({
  proxy: '',
  proxyCountry: 'JP',
  workers: 5,
  timeout: 45,
  forceFullLogin: false,
  smsEnabled: false,
  smsProvider: 'smsbower',
  smsApiKey: '',
  smsCountry: '52',
  smsMaxPrice: '',
  smsMaxAttempts: 3,
  smsTimeout: 80,
})

const refreshItems = ref({})
const refreshLogs = ref([])
const refreshLogModalVisible = ref(false)
const currentRefreshLogItem = ref(null)
const refreshLogLines = ref([])
const refreshLogLoading = ref(false)

const refreshRows = computed(() =>
  Object.values(refreshItems.value).map((item) => ({ ...item })),
)

const refreshStats = computed(() => {
  const items = Object.values(refreshItems.value)
  const tot = items.length || refreshTargetEmails.value.length || 0
  const done = items.filter((i) => i.status === 'done').length
  const running = items.filter((i) => i.status === 'running').length
  const pending = items.filter((i) => i.status === 'pending').length
  const rt_fast_ok = items.filter((i) => i.result && i.result.status === 'success' && i.result.method === 'rt_fast').length
  const full_login_ok = items.filter((i) => i.result && i.result.status === 'success' && i.result.method !== 'rt_fast').length
  const success = items.filter((i) => i.result && i.result.status === 'success').length
  const need_phone = items.filter((i) => i.result && i.result.status === 'need_phone').length
  const error = items.filter((i) => i.status === 'done' && (!i.result || (i.result.status !== 'success' && i.result.status !== 'need_phone'))).length
  const percent = tot > 0 ? Math.round((done / tot) * 100) : 0
  return {
    total: tot, done, running, pending,
    rt_fast_ok, full_login_ok, success, need_phone, error,
    percent,
  }
})

async function openTokenRefresh(scope = 'selected') {
  let emails = []
  if (scope === 'selected') {
    if (!selected.value.length) {
      ElMessage.warning('请先在表格中勾选要刷新 Token 的账号')
      return
    }
    emails = selected.value.map((r) => r.email)
  } else if (scope === 'no_token') {
    loading.value = true
    try {
      const res = await listRegisteredEmails('no_at')
      emails = res.emails || []
    } catch (e) {
      ElMessage.error('获取无Token账号失败: ' + e.message)
      return
    } finally {
      loading.value = false
    }
    if (!emails.length) {
      ElMessage.info('当前没有缺少 Token 的账号')
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

  if (!refreshRunning.value) {
    refreshTaskId.value = ''
    refreshLogs.value = []
    refreshConfigCollapsed.value = true
    const initMap = {}
    for (const em of emails) {
      initMap[em] = { email: em, status: 'pending', step_text: '排队中...', result: null, elapsed: 0 }
    }
    refreshItems.value = initMap
  }

  refreshVisible.value = true
}

function handleRefreshCommand(cmd) {
  if (cmd === 'refresh_selected') openTokenRefresh('selected')
  else if (cmd === 'refresh_no_token') openTokenRefresh('no_token')
  else if (cmd === 'refresh_all') openTokenRefresh('all')
}

function closeTokenRefresh() {
  if (refreshRunning.value) {
    ElMessage.info('Token 刷新任务在后台继续运行，可随时重新打开查看进度')
  }
  if (refreshEs.value && !refreshRunning.value) {
    refreshEs.value.close()
    refreshEs.value = null
  }
  refreshVisible.value = false
}

async function stopTokenRefreshTask() {
  if (!refreshTaskId.value) {
    refreshRunning.value = false
    return
  }
  try {
    await stopTokenRefresh(refreshTaskId.value)
    ElMessage.success('已发送停止指令')
  } catch (_) {
    ElMessage.info('任务已停止')
  } finally {
    refreshRunning.value = false
  }
}

async function startTokenRefreshTask() {
  const emails = refreshTargetEmails.value
  if (!emails.length) {
    ElMessage.warning('没有待刷新的账号列表')
    return
  }

  if (refreshEs.value) {
    refreshEs.value.close()
    refreshEs.value = null
  }

  refreshRunning.value = true
  refreshLogs.value = []
  refreshConfigCollapsed.value = true

  const initMap = {}
  for (const em of emails) {
    initMap[em] = { email: em, status: 'pending', step_text: '排队中...', result: null, elapsed: 0 }
  }
  refreshItems.value = initMap

  let proxiesParam = ''
  let proxyParam = ''
  if (refreshForm.proxy === '__POOL__') {
    proxiesParam = proxyList.value.join('\n')
  } else {
    proxyParam = (refreshForm.proxy || '').trim()
  }

  try {
    const res = await startTokenRefresh({
      emails,
      proxies: proxiesParam,
      proxy: proxyParam,
      proxy_country: refreshForm.proxyCountry || '',
      workers: refreshForm.workers || 5,
      timeout: refreshForm.timeout || 45,
      force_full_login: Boolean(refreshForm.forceFullLogin),
      sms_enabled: Boolean(refreshForm.smsEnabled),
      sms_provider: refreshForm.smsProvider || 'smsbower',
      sms_api_key: refreshForm.smsApiKey || '',
      sms_country: refreshForm.smsCountry || '52',
      sms_max_price: String(refreshForm.smsMaxPrice || ''),
      sms_max_attempts: Number(refreshForm.smsMaxAttempts) || 3,
      sms_timeout: Number(refreshForm.smsTimeout) || 80,
    })
    const taskId = res.taskId || res.task_id
    if (!taskId) throw new Error('未获取到任务 ID')
    refreshTaskId.value = taskId

    refreshEs.value = createSSE(tokenRefreshStreamUrl(taskId), {
      init: (ev) => {
        try {
          const snap = JSON.parse(ev.data)
          if (snap.items) refreshItems.value = snap.items
        } catch (_) {}
      },
      progress: (ev) => {
        try {
          const msg = JSON.parse(ev.data)
          if (msg.email) {
            if (!refreshItems.value[msg.email]) {
              refreshItems.value[msg.email] = { email: msg.email }
            }
            if (msg.status !== undefined) refreshItems.value[msg.email].status = msg.status
            if (msg.step_text !== undefined) refreshItems.value[msg.email].step_text = msg.step_text
            if (msg.result !== undefined) refreshItems.value[msg.email].result = msg.result
            if (msg.elapsed !== undefined) refreshItems.value[msg.email].elapsed = msg.elapsed
          }
        } catch (_) {}
      },
      log: (ev) => {
        try {
          const msg = JSON.parse(ev.data)
          if (msg.line) {
            refreshLogs.value.push(msg.line)
            if (refreshLogs.value.length > 500) refreshLogs.value.splice(0, refreshLogs.value.length - 500)
            nextTick(scrollRefreshLog)
          }
        } catch (_) {}
      },
      end: () => {
        refreshRunning.value = false
        if (refreshEs.value) {
          refreshEs.value.close()
          refreshEs.value = null
        }
        ElMessage.success('Token 刷新任务已全部执行完成！凭证已同步写入数据库')
        load(false)
      },
    }, () => {
      if (!refreshRunning.value && refreshEs.value) {
        refreshEs.value.close()
        refreshEs.value = null
      }
    })
  } catch (e) {
    refreshRunning.value = false
    refreshConfigCollapsed.value = false
    ElMessage.error('启动 Token 刷新失败: ' + (e.response?.data?.detail || e.message))
  }
}

function scrollRefreshLog() {
  const box = document.getElementById('refresh-log-box')
  if (box) box.scrollTop = box.scrollHeight
}

async function openRefreshItemLog(row) {
  currentRefreshLogItem.value = row
  refreshLogLines.value = []
  refreshLogModalVisible.value = true
  refreshLogLoading.value = true

  try {
    if (refreshTaskId.value) {
      const res = await getTokenRefreshLog(refreshTaskId.value, row.email)
      refreshLogLines.value = res.lines || []
    } else {
      refreshLogLines.value = row.logs || ['暂无日志']
    }
  } catch (e) {
    refreshLogLines.value = ['读取日志失败: ' + (e.response?.data?.detail || e.message)]
  } finally {
    refreshLogLoading.value = false
  }
}

async function downloadTokenRefresh(format = 'txt') {
  if (!refreshTaskId.value) {
    ElMessage.warning('暂无当前任务 ID')
    return
  }
  try {
    const res = await downloadTokenRefreshExport(refreshTaskId.value, format)
    const mime = format === 'txt' ? 'text/plain;charset=utf-8' : 'application/json'
    const ext = format === 'txt' ? 'txt' : 'json'
    const blob = new Blob([res.data || res], { type: mime })
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `tokens_${format}_${refreshTaskId.value}.${ext}`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    window.URL.revokeObjectURL(url)
    ElMessage.success(`已下载 ${format.toUpperCase()} 格式凭证`)
  } catch (e) {
    ElMessage.error('下载导出凭证失败: ' + e.message)
  }
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
  oaLogModalVisible.value = true
  oaLogLoading.value = true

  try {
    if (oaTaskId.value) {
      const res = await getOACheckLog(oaTaskId.value, row.email)
      oaLogLines.value = res.lines || []
    } else {
      oaLogLines.value = row.logs || ['暂无日志']
    }
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

function getLogClass(line) {
  if (!line) return ''
  if (line.includes('HIT') || line.includes('oaics_') || line.includes('★') || line.includes('◆')) return 'log-hit'
  if (line.includes('MISS') || line.includes('state=CS') || line.includes('Free')) return 'log-miss'
  if (line.includes('err=') || line.includes('ERROR') || line.includes('失败') || line.includes('封号') || line.includes('失效')) return 'log-err'
  if (line.includes('[task]') || line.includes('HTTP')) return 'log-task'
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
const oauthItems = ref({})
const oauthLogs = ref([])

const OAUTH_FORM_KEY = 'gpt_oauth_export_form_v2'
let savedOAuth = {}
try { savedOAuth = JSON.parse(localStorage.getItem(OAUTH_FORM_KEY) || '{}') } catch (_) {}

const oauthForm = reactive({
  proxy: savedOAuth.proxy || '__POOL__',
  proxyCountry: savedOAuth.proxyCountry || 'RANDOM_HOT',
  workers: savedOAuth.workers || 5,
  timeout: savedOAuth.timeout || 45,
  smsEnabled: !!savedOAuth.smsEnabled,
  smsProvider: savedOAuth.smsProvider || 'smsbower',
  smsApiKey: savedOAuth.smsApiKey || '',
  smsCountry: savedOAuth.smsCountry || '52',
  smsMaxPrice: savedOAuth.smsMaxPrice || '',
  smsMaxAttempts: savedOAuth.smsMaxAttempts || 3,
  smsTimeout: savedOAuth.smsTimeout || 80,
})

watch(oauthForm, (v) => {
  try { localStorage.setItem(OAUTH_FORM_KEY, JSON.stringify(v)) } catch (_) {}
}, { deep: true })

const oauthActiveTab = ref('network')
const oauthNowTime = ref(Date.now())
let oauthLiveTimer = null

onMounted(() => {
  oauthLiveTimer = setInterval(() => {
    if (oauthRunning.value) {
      oauthNowTime.value = Date.now()
    }
  }, 1000)
})

onUnmounted(() => {
  if (oauthLiveTimer) clearInterval(oauthLiveTimer)
})

function getOAuthRowElapsed(row) {
  if (row.elapsed) return row.elapsed + 's'
  if (row.status === 'running' && row.started_at) {
    const sec = Math.max(0, Math.floor((oauthNowTime.value / 1000) - row.started_at))
    return sec + 's'
  }
  return '—'
}

function saveOAuthFormDefault() {
  try {
    localStorage.setItem(OAUTH_FORM_KEY, JSON.stringify(oauthForm))
    if (oauthForm.smsApiKey && oauthForm.smsApiKey !== '***') {
      saveSmsConfig({
        sms_enabled: oauthForm.smsEnabled ? '1' : '0',
        sms_provider: oauthForm.smsProvider || 'smsbower',
        sms_api_key: oauthForm.smsApiKey,
        sms_country: String(oauthForm.smsCountry || '52').trim(),
        sms_max_price: String(oauthForm.smsMaxPrice || '').trim(),
        sms_max_phone_attempts: String(oauthForm.smsMaxAttempts || '3'),
        sms_per_phone_timeout: String(oauthForm.smsTimeout || '80'),
      }).catch(() => {})
    }
    ElMessage.success('OAuth 参数配置已成功保存为默认！')
  } catch (e) {
    ElMessage.error('保存配置失败: ' + e.message)
  }
}

const SMS_COUNTRY_OPTIONS = [
  { value: '52', label: '52 · 泰国 (推荐 ★★★★★ 免WhatsApp极高成功率)' },
  { value: 'AUTO', label: '🌐 智能多国自动轮换 (泰国52/印尼6/越南10/巴西73/波兰15)' },
  { value: '6', label: '6 · 印度尼西亚 (东南亚高库存)' },
  { value: '10', label: '10 · 越南 (东南亚低价)' },
  { value: '73', label: '73 · 巴西 (拉美高爆)' },
  { value: '15', label: '15 · 波兰 (欧洲高品质)' },
  { value: '16', label: '16 · 英国 (欧洲高品质)' },
  { value: '12', label: '12 · 美国虚拟号' },
  { value: '187', label: '187 · 美国实体号' },
]

const oauthLogModalVisible = ref(false)
const currentOAuthLogItem = ref(null)
const oauthLogLines = ref([])
const oauthLogLoading = ref(false)

const oauthRows = computed(() =>
  Object.values(oauthItems.value).map((item) => ({ ...item })),
)

const oauthStats = computed(() => {
  const items = Object.values(oauthItems.value)
  const tot = items.length || oauthTargetEmails.value.length || 0
  const done = items.filter((i) => i.status === 'done').length
  const running = items.filter((i) => i.status === 'running').length
  const pending = items.filter((i) => i.status === 'pending').length
  const success = items.filter((i) => i.result && i.result.status === 'success').length
  const need_phone = items.filter((i) => i.result && i.result.status === 'need_phone').length
  const error = items.filter((i) => i.result && i.result.status !== 'success' && i.result.status !== 'need_phone').length
  const percent = tot > 0 ? Math.round((done / tot) * 100) : 0
  return {
    total: tot, done, running, pending,
    success, need_phone, error, percent,
  }
})

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
    oauthLogs.value = []
    oauthConfigCollapsed.value = true
    const initMap = {}
    for (const em of emails) {
      initMap[em] = { email: em, status: 'pending', result: null, elapsed: 0 }
    }
    oauthItems.value = initMap
  }

  oauthVisible.value = true
}

function closeOAuthExport() {
  if (oauthRunning.value) {
    ElMessage.info('OAuth 导出任务在后台继续运行，可随时重新打开查看进度')
  }
  if (oauthEs.value && !oauthRunning.value) {
    oauthEs.value.close()
    oauthEs.value = null
  }
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

  oauthRunning.value = true
  oauthLogs.value = []
  oauthConfigCollapsed.value = true

  const initMap = {}
  for (const em of emails) {
    initMap[em] = { email: em, status: 'pending', result: null, elapsed: 0 }
  }
  oauthItems.value = initMap

  let proxiesParam = ''
  let proxyParam = ''
  if (oauthForm.proxy === '__POOL__') {
    proxiesParam = proxyList.value.join('\n')
  } else {
    proxyParam = (oauthForm.proxy || '').trim()
  }

  try {
    const res = await startOAuthExport({
      emails,
      proxies: proxiesParam,
      proxy: proxyParam,
      proxy_country: oauthForm.proxyCountry || '',
      workers: oauthForm.workers || 5,
      timeout: oauthForm.timeout || 45,
      sms_enabled: !!oauthForm.smsEnabled,
      sms_provider: oauthForm.smsProvider || 'smsbower',
      sms_api_key: oauthForm.smsApiKey || '',
      sms_country: String(oauthForm.smsCountry || '52').trim(),
      sms_max_price: String(oauthForm.smsMaxPrice || '').trim(),
      sms_max_attempts: Number(oauthForm.smsMaxAttempts) || 3,
      sms_timeout: Number(oauthForm.smsTimeout) || 80,
    })
    const taskId = res.taskId || res.task_id
    if (!taskId) throw new Error('未获取到任务 ID')
    oauthTaskId.value = taskId

    oauthEs.value = createSSE(oauthExportStreamUrl(taskId), {
      init: (ev) => {
        try {
          const snap = JSON.parse(ev.data)
          if (snap.items) oauthItems.value = snap.items
        } catch (_) {}
      },
      progress: (ev) => {
        try {
          const msg = JSON.parse(ev.data)
          if (msg.email) {
            if (!oauthItems.value[msg.email]) {
              oauthItems.value[msg.email] = { email: msg.email }
            }
            if (msg.status !== undefined) oauthItems.value[msg.email].status = msg.status
            if (msg.step !== undefined) oauthItems.value[msg.email].step = msg.step
            if (msg.step_text !== undefined) oauthItems.value[msg.email].step_text = msg.step_text
            if (msg.result !== undefined) oauthItems.value[msg.email].result = msg.result
            if (msg.elapsed !== undefined) oauthItems.value[msg.email].elapsed = msg.elapsed
          }
        } catch (_) {}
      },
      log: (ev) => {
        try {
          const msg = JSON.parse(ev.data)
          if (msg.line) {
            oauthLogs.value.push(msg.line)
            if (oauthLogs.value.length > 500) oauthLogs.value.splice(0, oauthLogs.value.length - 500)
            nextTick(scrollOAuthLog)
          }
        } catch (_) {}
      },
      end: () => {
        oauthRunning.value = false
        if (oauthEs.value) {
          oauthEs.value.close()
          oauthEs.value = null
        }
        ElMessage.success('OAuth 导出任务已全部完成！')
        load(false)
      },
    }, () => {
      if (!oauthRunning.value && oauthEs.value) {
        oauthEs.value.close()
        oauthEs.value = null
      }
    })
  } catch (e) {
    oauthRunning.value = false
    oauthConfigCollapsed.value = false
    ElMessage.error('启动 OAuth 导出失败: ' + (e.response?.data?.detail || e.message))
  }
}

function scrollOAuthLog() {
  const box = document.getElementById('oauth-log-box')
  if (box) box.scrollTop = box.scrollHeight
}

async function openOAuthItemLog(row) {
  currentOAuthLogItem.value = row
  oauthLogLines.value = []
  oauthLogModalVisible.value = true
  oauthLogLoading.value = true

  try {
    if (oauthTaskId.value) {
      const res = await getOAuthExportLog(oauthTaskId.value, row.email)
      oauthLogLines.value = res.lines || []
    } else {
      oauthLogLines.value = row.logs || ['暂无日志']
    }
  } catch (e) {
    oauthLogLines.value = ['读取日志失败: ' + (e.response?.data?.detail || e.message)]
  } finally {
    oauthLogLoading.value = false
  }
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

// ════════════════════════ 数据加载与分页 ════════════════════════
async function load(resetPage = false) {
  if (resetPage) page.value = 1
  loading.value = true
  try {
    const { items, total: t } = await listRegistered({
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
      filter: filter.value,
      search: searchKeyword.value.trim(),
    })
    rows.value = items || []
    total.value = t || 0
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

function handleSizeChange(val) {
  pageSize.value = val
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
  try { await deleteRegistered(email); ElMessage.success('已删除'); load() }
  catch (e) { ElMessage.error(e.message) }
}

async function deleteSelected() {
  const emails = selected.value.map((r) => r.email)
  if (!emails.length) return
  if (!(await confirm(`确定删除选中的 ${emails.length} 条凭证？(不可恢复)`))) return
  try { const r = await bulkDeleteRegistered({ emails }); ElMessage.success(`已删除 ${r.deleted} 条`); load() }
  catch (e) { ElMessage.error(e.message) }
}

async function cleanInvalid() {
  if (!(await confirm('将自动清理所有没有有效 Token 凭证（AT/ST/RT 全为空）的未完成废号，确定？'))) return
  try {
    const r = await cleanInvalidRegistered()
    ElMessage.success(`已清理 ${r.deleted} 个无凭证空号`)
    load()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

async function deleteAll() {
  if (!(await confirm('这会清空注册结果表里的所有凭证！邮箱列表不受影响，确定？'))) return
  if (!(await confirm('再次确认：真的要删除全部凭证吗？此操作不可恢复！'))) return
  try { const r = await bulkDeleteRegistered({ all: true }); ElMessage.success(`已清空 ${r.deleted} 条`); load() }
  catch (e) { ElMessage.error(e.message) }
}

// ──────────── 批量导出 ────────────
const exportFormats = ref([])
const exporting = ref(false)
const exportVisible = ref(false)
const exportText = ref('')
const exportCount = ref(0)
const exportFilename = ref('')
const exportLabel = ref('')
const exportedEmails = ref([])
const deletingExported = ref(false)

const exportBtnText = computed(() =>
  selected.value.length ? `导出选中 (${selected.value.length})` : '导出全部',
)

async function loadExportFormats() {
  if (exportFormats.value.length) return
  try {
    const { formats } = await listExportFormats()
    exportFormats.value = formats || []
  } catch (e) { ElMessage.error('加载导出格式失败: ' + e.message) }
}

async function doExport(fmt) {
  const emails = selected.value.map((r) => r.email)
  const payload = emails.length ? { format: fmt.id, emails } : { format: fmt.id, all: true }
  exporting.value = true
  try {
    const r = await exportRegistered(payload)
    exportedEmails.value = (r.emails || []).filter(Boolean)
    if (r.mode === 'download') {
      saveBlob(b64ToBytes(r.b64), r.filename, r.mime)
      ElMessage.success(`已下载 ${r.filename}（${r.count} 个号）`)
      return
    }
    exportText.value = r.text || ''
    exportCount.value = r.count || 0
    exportFilename.value = r.filename || 'export.txt'
    exportLabel.value = r.label || fmt.label
    exportVisible.value = true
  } catch (e) { ElMessage.error('导出失败: ' + e.message) }
  finally { exporting.value = false }
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
  saveBlob(exportText.value, exportFilename.value, 'text/plain;charset=utf-8')
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
    `删掉后只剩刚下载的 txt 这一份，不可恢复。确定？`,
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
  const ext = credData.value.extract_link || credData.value.extra?.extract_link
  if (ext && ext.link_url) {
    items.unshift({ key: 'extract_link', val: ext.link_url })
  }
  return items
})

const CRED_META_DICT = {
  extract_link:   { badge: 'Extract', bg: 'rgba(16, 185, 129, 0.2)', color: '#34d399' },
  totp_secret:    { badge: '2FA', bg: 'rgba(16, 185, 129, 0.2)', color: '#34d399' },
  totp_factor_id: { badge: '2FA', bg: 'rgba(16, 185, 129, 0.15)', color: '#6ee7b7' },
  access_token:   { badge: 'OAuth', bg: 'rgba(59, 130, 246, 0.2)', color: '#60a5fa' },
  session_token:  { badge: 'Session', bg: 'rgba(168, 85, 247, 0.2)', color: '#c084fc' },
  refresh_token:  { badge: 'OAuth', bg: 'rgba(236, 72, 153, 0.2)', color: '#f472b6' },
  id_token:       { badge: 'Token', bg: 'rgba(100, 116, 139, 0.2)', color: '#94a3b8' },
  device_id:      { badge: 'Device', bg: 'rgba(100, 116, 139, 0.2)', color: '#94a3b8' },
  csrf_token:     { badge: 'Security', bg: 'rgba(100, 116, 139, 0.2)', color: '#94a3b8' },
  cookie_header:  { badge: 'Cookie', bg: 'rgba(245, 158, 11, 0.2)', color: '#fbbf24' },
  password:       { badge: 'Auth', bg: 'rgba(6, 182, 212, 0.2)', color: '#22d3ee' },
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

watch(dataVersion, () => load())
onActivated(() => load())

onUnmounted(() => {
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
})
</script>

<template>
  <div class="registered-page">
    <div class="macos-window-panel">
      <!-- 顶部 macOS 风格工具栏 -->
      <div class="macos-toolbar">
        <div class="toolbar-left">
          <div class="page-title-badge">
            <span class="dot-live"></span>
            <span class="title">注册结果</span>
            <span class="badge-total">{{ total }} 条</span>
          </div>

          <el-button class="macos-btn" @click="load(false)">
            <el-icon><Refresh /></el-icon>刷新
          </el-button>

          <el-input
            v-model="searchKeyword"
            placeholder="搜索邮箱..."
            clearable
            size="small"
            class="macos-input search-input"
            :prefix-icon="Search"
            @input="onSearchInput"
            @clear="load(true)"
            @keyup.enter="load(true)"
          />

          <el-select v-model="filter" class="macos-select filter-select" @change="load(true)">
            <el-option label="全部" value="all" />
            <el-option label="👑 Pro 账号 (含20x/5x)" value="pro" />
            <el-option label="💎 Team 团队号" value="team" />
            <el-option label="★ Plus / 试用" value="plus" />
            <el-option label="🎁 Plus试用" value="extract_eligible" />
            <el-option label="⚗️ 提链成功" value="extract_success" />
            <el-option label="❌ 提链失败" value="extract_failed" />
            <el-option label="Free 普通号" value="free" />
            <el-option label="有 RT" value="has_rt" />
            <el-option label="无 RT" value="no_rt" />
            <el-option label="未检测" value="unchecked" />
            <el-option label="已封号" value="banned" />
            <el-option label="凭证失效" value="token_invalid" />
            <el-option label="OA未检" value="oa_unchecked" />
            <el-option label="OA命中" value="oa_hit" />
            <el-option label="OA未中" value="oa_miss" />
            <el-option label="OAuth 成功" value="oauth_success" />
            <el-option label="需接码 (已跳过)" value="oauth_need_phone" />
            <el-option label="OAuth 失败" value="oauth_failed" />
            <el-option label="OAuth 未检" value="oauth_unchecked" />
          </el-select>

          <el-select
            v-model="form.proxy" filterable clearable allow-create default-first-option
            :reserve-keyword="false" placeholder="检测代理（留空直连）"
            class="macos-select proxy-select"
          >
            <el-option v-for="p in proxyList" :key="p" :label="p" :value="p" />
          </el-select>
        </div>

        <div class="toolbar-right">
          <!-- 核心功能：账号批量验活 (Token 验活 & 套餐/试用资格探测) -->
          <el-dropdown trigger="click" @command="handleHealthCheckCommand">
            <el-button type="primary" class="oa-action-btn health-action-btn">
              <el-icon><Compass /></el-icon>⚡ 批量验活 ({{ selected.length }})
              <el-icon class="el-icon--right"><ArrowDown /></el-icon>
            </el-button>
            <template #dropdown>
              <el-dropdown-menu class="extract-dropdown-menu">
                <div class="dropdown-group-title">🔑 Token 状态验活</div>
                <el-dropdown-item command="token_selected" :disabled="!selected.length">
                  验活选中账号 Token ({{ selected.length }})
                </el-dropdown-item>
                <el-dropdown-item command="token_unchecked">验活未检账号 Token</el-dropdown-item>
                <el-dropdown-item command="token_all">全量重验所有账号 Token</el-dropdown-item>

                <div class="dropdown-group-title divider-title">💎 套餐与试用资格探测</div>
                <el-dropdown-item command="plan_selected" :disabled="!selected.length">
                  探测选中账号套餐 ({{ selected.length }})
                </el-dropdown-item>
                <el-dropdown-item command="plan_unchecked">探测未检账号套餐</el-dropdown-item>
                <el-dropdown-item command="plan_all">全量重测所有账号套餐</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>

          <!-- 核心功能：全渠道提炼与资格检测下拉菜单 -->
          <el-dropdown trigger="click" @command="openExtractChannel">
            <el-button type="warning" class="oa-action-btn extract-action-btn">
              <el-icon><Link /></el-icon>⚗️ 提炼 ({{ selected.length }})
              <el-icon class="el-icon--right"><ArrowDown /></el-icon>
            </el-button>
            <template #dropdown>
              <el-dropdown-menu class="extract-dropdown-menu">
                <div class="dropdown-group-title">资格检测</div>
                <el-dropdown-item command="gcash_check">批量 GCash 检测</el-dropdown-item>
                <el-dropdown-item command="oaics_check">批量 OAICS 检测</el-dropdown-item>
                <el-dropdown-item command="plus_check">批量 Plus 状态检测</el-dropdown-item>

                <div class="dropdown-group-title divider-title">提链 / 出码</div>
                <el-dropdown-item command="gcash">批量 GCash 提链</el-dropdown-item>
                <el-dropdown-item command="pix">批量 PIX 出码</el-dropdown-item>
                <el-dropdown-item command="paypal">批量 PayPal 提链</el-dropdown-item>
                <el-dropdown-item command="ideal">批量 iDEAL 提链</el-dropdown-item>
                <el-dropdown-item command="upi">批量 UPI 提链</el-dropdown-item>
                <el-dropdown-item command="kakao">批量 Kakao 提链</el-dropdown-item>
                <el-dropdown-item command="momo">批量 MoMo 提链</el-dropdown-item>
                <el-dropdown-item command="twint">批量 TWINT 提链</el-dropdown-item>
                <el-dropdown-item command="blik">批量 BLIK 提链</el-dropdown-item>
                <el-dropdown-item command="hosted">批量 Hosted 提链</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>

          <!-- 核心功能：OAuth 导出 -->
          <el-button
            type="success" class="oa-action-btn oauth-action-btn" :disabled="!selected.length"
            @click="openOAuthExport('selected')"
          >
            <el-icon><Refresh /></el-icon>OAuth 导出 ({{ selected.length }})
          </el-button>

          <!-- 核心功能：Token 重新获取与刷新 -->
          <el-dropdown trigger="click" @command="handleRefreshCommand">
            <el-button type="info" class="oa-action-btn refresh-token-action-btn">
              <el-icon><Refresh /></el-icon>🔄 刷新/重获Token ({{ selected.length }})
              <el-icon class="el-icon--right"><ArrowDown /></el-icon>
            </el-button>
            <template #dropdown>
              <el-dropdown-menu class="extract-dropdown-menu">
                <div class="dropdown-group-title">🔑 Token 智能双模刷新与重登</div>
                <el-dropdown-item command="refresh_selected" :disabled="!selected.length">
                  刷新选中账号 Token ({{ selected.length }})
                </el-dropdown-item>
                <el-dropdown-item command="refresh_no_token">
                  重新获取无 Token 账号凭证
                </el-dropdown-item>
                <el-dropdown-item command="refresh_all">
                  全量重新获取/刷新所有账号
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>

          <!-- 导出下拉 -->
          <el-dropdown trigger="click" @command="doExport" @visible-change="(v) => v && loadExportFormats()">
            <el-button class="macos-btn" :loading="exporting">
              <el-icon><Download /></el-icon>{{ exportBtnText }}
              <el-icon class="el-icon--right"><ArrowDown /></el-icon>
            </el-button>
            <template #dropdown>
              <el-dropdown-menu class="macos-dropdown-menu">
                <el-dropdown-item v-for="f in exportFormats" :key="f.id" :command="f" :divided="f.mode === 'download' && f.id === 'cpa'">
                  {{ f.label }}
                  <span v-if="f.note" class="hint" style="margin-left: 6px">{{ f.note }}</span>
                </el-dropdown-item>
                <el-dropdown-item v-if="!exportFormats.length" disabled>加载中...</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>

          <!-- 删除操作 -->
          <div class="macos-btn-group danger-group">
            <el-button size="small" type="danger" plain :disabled="!selected.length" @click="deleteSelected">
              删除 ({{ selected.length }})
            </el-button>
            <el-button size="small" type="warning" plain @click="cleanInvalid">清理空号</el-button>
            <el-button size="small" type="danger" plain @click="deleteAll">清空</el-button>
          </div>
        </div>
      </div>

      <!-- 中间主体表格区域：height="100%" 自适应弹性伸缩，绝无外部整页滚动条 -->
      <div class="macos-table-container">
        <el-skeleton v-if="loading && !rows.length" :rows="8" animated style="padding: 16px" />
        <el-table
          v-else
          v-loading="loading"
          :data="rows"
          height="100%"
          size="small"
          stripe
          class="macos-table"
          @selection-change="(v) => (selected = v)"
        >
          <el-table-column type="selection" width="40" align="center" />

          <el-table-column prop="email" label="邮箱" min-width="190" show-overflow-tooltip>
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

          <!-- 出口国家 (展示国旗 + 中文 + 代码) -->
          <el-table-column label="出口国家" width="135" align="center" show-overflow-tooltip>
            <template #default="{ row }">
              <span
                v-if="row.reg_country"
                class="geo-badge"
                :class="{ 'geo-hot': ['JP', 'BR', 'VN', 'DE', 'GB', 'PL', 'ES', 'AR', 'TH'].includes(row.reg_country?.toUpperCase()) }"
              >
                <span class="geo-country">{{ formatCountry(row.reg_country) }}</span>
              </span>
              <span v-else class="hint">—</span>
            </template>
          </el-table-column>

          <el-table-column label="Plus状态" width="130" align="center">
            <template #default="{ row }">
              <el-tag
                v-if="plusOf(row)"
                :type="PLUS_TYPE[plusOf(row).status] || 'info'"
                size="small"
                effect="light"
                class="macos-tag"
              >
                <StatusDot :status="plusOf(row).status" />
                {{ plusOf(row).label }}
              </el-tag>
              <span v-else class="hint">—</span>
            </template>
          </el-table-column>

          <el-table-column label="OA资格" width="120" align="center">
            <template #default="{ row }">
              <el-tag
                v-if="oaMeta(row)"
                :type="oaMeta(row).type"
                size="small"
                :effect="oaMeta(row).type === 'success' ? 'dark' : 'light'"
                class="macos-tag"
              >
                {{ oaMeta(row).label }}
              </el-tag>
              <span v-else class="hint">—</span>
            </template>
          </el-table-column>

          <el-table-column label="OAuth授权" width="125" align="center">
            <template #default="{ row }">
              <el-tag
                v-if="oauthMeta(row)"
                :type="oauthMeta(row).type"
                size="small"
                :effect="oauthMeta(row).effect || 'light'"
                class="macos-tag"
              >
                {{ oauthMeta(row).label }}
              </el-tag>
              <span v-else class="hint">—</span>
            </template>
          </el-table-column>

          <!-- 提链结果 -->
          <el-table-column label="提链结果" min-width="160" show-overflow-tooltip>
            <template #default="{ row }">
              <div v-if="row.extract_link?.link_url" class="extract-link-cell">
                <el-tag size="small" type="success" effect="light" class="extract-pill-tag">
                  {{ (row.extract_link.channel || row.extract_link.link_type || '提链').toUpperCase() }}
                </el-tag>
                <el-link
                  :href="row.extract_link.link_url"
                  target="_blank"
                  type="primary"
                  :underline="false"
                  class="mono extract-url-text"
                >
                  {{ row.extract_link.link_url }}
                </el-link>
                <el-button
                  size="small"
                  link
                  :icon="CopyDocument"
                  @click="copyText(row.extract_link.link_url, '提链链接已复制')"
                />
              </div>
              <el-tag
                v-else-if="row.extract_link?.status === 'failed'"
                size="small"
                type="danger"
                effect="plain"
                :title="row.extract_link.error"
              >
                失败: {{ (row.extract_link.error || '提链失败').slice(0, 8) }}
              </el-tag>
              <span v-else class="hint">—</span>
            </template>
          </el-table-column>

          <el-table-column label="AT" width="75" align="center">
            <template #default="{ row }">
              <span v-if="row.at_len" class="mono token-len-cell link" title="点击复制 AT" @click="copyCell(row.email, 'access_token')">
                {{ row.at_len }}
              </span>
              <span v-else class="hint">—</span>
            </template>
          </el-table-column>

          <el-table-column label="ST" width="75" align="center">
            <template #default="{ row }">
              <span v-if="row.st_len" class="mono token-len-cell link" title="点击复制 ST" @click="copyCell(row.email, 'session_token')">
                {{ row.st_len }}
              </span>
              <span v-else class="hint">—</span>
            </template>
          </el-table-column>

          <el-table-column label="RT" width="75" align="center">
            <template #default="{ row }">
              <span v-if="row.rt_len" class="mono token-len-cell link" title="点击复制 RT" @click="copyCell(row.email, 'refresh_token')">
                {{ row.rt_len }}
              </span>
              <span v-else class="hint">—</span>
            </template>
          </el-table-column>

          <el-table-column label="注册时间" width="135" align="center">
            <template #default="{ row }">
              <span class="mono-date">{{ fmtTime(row.created_at) }}</span>
            </template>
          </el-table-column>

          <el-table-column label="操作" width="170" fixed="right" align="center">
            <template #default="{ row }">
              <div class="row-actions">
                <el-button size="small" text @click="viewCred(row.email)">凭证</el-button>
                <el-button size="small" text type="primary" @click="openEdit(row)">编辑</el-button>
                <el-button size="small" text type="danger" @click="deleteOne(row.email)">删除</el-button>
              </div>
            </template>
          </el-table-column>

          <template #empty>
            <el-empty description="暂无注册结果，去「单次注册」或「全自动批量」跑号" :image-size="60" />
          </template>
        </el-table>
      </div>

      <!-- 底部固定 macOS 风格状态与全能分页栏 (10, 20, 30, 50, 100) -->
      <div class="macos-footer-bar">
        <div class="footer-status-left">
          <span v-if="selected.length" class="selected-badge">已勾选 <b>{{ selected.length }}</b> 项</span>
          <span v-else class="total-badge">当前页共 {{ rows.length }} 条记录</span>
        </div>

        <div class="footer-pagination-right">
          <el-pagination
            v-model:current-page="page"
            v-model:page-size="pageSize"
            :page-sizes="[10, 20, 30, 50, 100]"
            :total="total"
            layout="total, sizes, prev, pager, next, jumper"
            size="small"
            background
            class="macos-pagination"
            @size-change="handleSizeChange"
            @current-change="handleCurrentChange"
          />
        </div>
      </div>
    </div>

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

        <!-- KPI 统计看板 -->
        <div class="plus-kpi-grid">
          <div class="plus-kpi-card">
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
          <div class="plus-kpi-card hit-active">
            <span class="kpi-label">★ Plus 生效中</span>
            <span class="kpi-num text-primary">{{ plusStats.plus_active }}</span>
          </div>
          <div class="plus-kpi-card hit-promo">
            <span class="kpi-label">◆ Plus 试用</span>
            <span class="kpi-num text-success">{{ plusStats.plus_eligible }}</span>
          </div>
          <div class="plus-kpi-card">
            <span class="kpi-label">Free 普通号</span>
            <span class="kpi-num">{{ plusStats.free }}</span>
          </div>
          <div class="plus-kpi-card" :class="{ 'card-warn': plusStats.banned > 0 || plusStats.token_invalid > 0 }">
            <span class="kpi-label">封号 / 凭证失效</span>
            <span class="kpi-num text-danger">{{ plusStats.banned + plusStats.token_invalid }}</span>
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
            <span class="modal-email">{{ currentPlusLogItem?.email }}</span>
            <el-tag size="small" type="info" effect="plain" class="modal-run-tag">
              Plus 检测日志
            </el-tag>
          </div>
        </div>
      </template>

      <div class="modal-terminal-wrap">
        <div class="modal-terminal-body">
          <div
            v-for="(line, idx) in plusLogLines"
            :key="idx"
            class="terminal-line"
            :class="getLogClass(line)"
          >
            {{ line }}
          </div>
          <div v-if="!plusLogLines.length" class="terminal-empty">
            {{ plusLogLoading ? '正在加载日志...' : '暂无详细日志' }}
          </div>
        </div>
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
            <span class="modal-email">{{ currentOaLogItem?.email }}</span>
            <el-tag size="small" type="info" effect="plain" class="modal-run-tag">
              OAICS 检测日志
            </el-tag>
          </div>
        </div>
      </template>

      <div class="modal-terminal-wrap">
        <div class="modal-terminal-body">
          <div
            v-for="(line, idx) in oaLogLines"
            :key="idx"
            class="terminal-line"
            :class="getLogClass(line)"
          >
            {{ line }}
          </div>
          <div v-if="!oaLogLines.length" class="terminal-empty">
            {{ oaLogLoading ? '正在加载日志...' : '暂无详细日志' }}
          </div>
        </div>
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

    <!-- ──────────────── OAuth 导出与凭证生成控制台弹窗 (紧凑型设计) ──────────────── -->
    <el-dialog
      v-model="oauthVisible" width="880px" top="2vh"
      class="oa-custom-dialog plus-dialog oauth-dialog oauth-compact-modal"
      :close-on-click-modal="false" @closed="closeOAuthExport"
    >
      <template #header>
        <div class="oa-header">
          <div class="oa-header-title">
            <span class="oa-title-badge" style="background: linear-gradient(135deg, #10b981 0%, #059669 100%)">OAUTH</span>
            <span class="oa-title-text">Codex OAuth 重跑导出与凭证生成</span>
            <el-tag size="small" type="info" round effect="plain">{{ oauthTargetEmails.length }} 个账号</el-tag>
          </div>
          <div class="oa-header-extra">
            <el-button size="small" text @click="oauthConfigCollapsed = !oauthConfigCollapsed">
              <el-icon><Setting /></el-icon>{{ oauthConfigCollapsed ? '展开参数配置' : '收起参数配置' }}
            </el-button>
          </div>
        </div>
      </template>

      <div class="oa-dialog-container">
        <!-- 参数配置卡片 (Tab 选项卡折叠卡片) -->
        <el-collapse-transition>
          <div v-show="!oauthConfigCollapsed" class="oa-config-card" style="padding: 10px 14px 12px">
            <el-tabs v-model="oauthActiveTab" class="oa-config-tabs">
              <!-- Tab 1: 网络与代理 -->
              <el-tab-pane label="🌐 网络代理 & 并发设置" name="network">
                <el-form label-position="top" :disabled="oauthRunning" size="small" style="margin-top: 6px">
                  <el-row :gutter="12">
                    <el-col :xs="24" :sm="12" :md="8">
                      <el-form-item label="网络代理（支持下拉选择/代理池轮询/手动输入/直连）">
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
                      <el-form-item label="代理目标国家（自动重写代理与请求特征）">
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
                      <el-form-item label="并发 Worker 数">
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
              <el-tab-pane label="📱 手机号短信接码 (SmsBower)" name="sms">
                <el-form label-position="top" :disabled="oauthRunning" size="small" style="margin-top: 6px">
                  <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; flex-wrap: wrap; gap: 6px">
                    <el-checkbox v-model="oauthForm.smsEnabled" style="font-weight: 600">
                      启用自动短信接码 (遇到 add-phone 自动收码推进)
                    </el-checkbox>
                    <span style="font-size: 11.5px; color: var(--el-color-success)">
                      🛡️ 自动退款保障：未接码成功的手机号均会自动向平台取消退款
                    </span>
                  </div>

                  <div v-show="oauthForm.smsEnabled">
                    <el-row :gutter="12">
                      <el-col :xs="24" :sm="12" :md="6">
                        <el-form-item label="接码服务平台">
                          <el-select v-model="oauthForm.smsProvider" style="width: 100%">
                            <el-option label="SmsBower (smsbower.page)" value="smsbower" />
                            <el-option label="HeroSMS (hero-sms.com)" value="herosms" />
                          </el-select>
                        </el-form-item>
                      </el-col>
                      <el-col :xs="24" :sm="12" :md="8">
                        <el-form-item label="接码国家 (建议 52 泰国免 WhatsApp)">
                          <el-select v-model="oauthForm.smsCountry" filterable allow-create placeholder="选择或输入国家ID" style="width: 100%">
                            <el-option v-for="sc in SMS_COUNTRY_OPTIONS" :key="sc.value" :label="sc.label" :value="sc.value" />
                          </el-select>
                        </el-form-item>
                      </el-col>
                      <el-col :xs="12" :sm="6" :md="5">
                        <el-form-item label="最高金额限制 (留空不限，建议留空或≥0.2)">
                          <el-input v-model="oauthForm.smsMaxPrice" placeholder="留空不限 / 如 0.25" clearable />
                        </el-form-item>
                      </el-col>
                      <el-col :xs="12" :sm="6" :md="5">
                        <el-form-item label="最多换号尝试次数">
                          <el-input-number v-model="oauthForm.smsMaxAttempts" :min="1" :max="10" style="width: 100%" />
                        </el-form-item>
                      </el-col>
                      <el-col :xs="24" :sm="16" :md="16">
                        <el-form-item label="接码平台 API Key (留空自动使用全局「接码配置」)">
                          <el-input v-model="oauthForm.smsApiKey" type="password" show-password placeholder="留空自动读取系统接码配置" clearable />
                        </el-form-item>
                      </el-col>
                      <el-col :xs="12" :sm="8" :md="8">
                        <el-form-item label="单号收码等待超时 (秒)">
                          <el-input-number v-model="oauthForm.smsTimeout" :min="20" :max="300" :step="10" style="width: 100%" />
                        </el-form-item>
                      </el-col>
                    </el-row>
                  </div>
                  <div v-show="!oauthForm.smsEnabled" style="padding: 10px 0; color: var(--el-text-color-secondary); font-size: 12px">
                    当前未开启接码。遇到手机号验证（add-phone）将<b>直接安全跳过</b>，不会产生任何扣费。
                  </div>
                </el-form>
              </el-tab-pane>
            </el-tabs>

            <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 6px; padding-top: 8px; border-top: 1px dashed var(--el-border-color-lighter); font-size: 11.5px; color: var(--el-text-color-secondary)">
              <span>💡 提示：若设置最高金额（如 0.03），若该档位缺货可能报 NO_NUMBERS，建议留空或设为 0.25+。</span>
              <el-button size="small" type="primary" plain @click="saveOAuthFormDefault">
                <el-icon><Check /></el-icon> 保存为默认配置
              </el-button>
            </div>
          </div>
        </el-collapse-transition>

        <!-- KPI 统计看板 -->
        <div class="plus-kpi-grid">
          <div class="plus-kpi-card">
            <span class="kpi-label">已处理 / 总数</span>
            <span class="kpi-num">{{ oauthStats.done }} / {{ oauthStats.total }}</span>
          </div>
          <div class="plus-kpi-card hit-active">
            <span class="kpi-label">✅ OAuth 成功</span>
            <span class="kpi-num text-success">{{ oauthStats.success }}</span>
          </div>
          <div class="plus-kpi-card card-warn" :class="{ 'card-warn': oauthStats.need_phone > 0 }">
            <span class="kpi-label">📱 需手机接码 (已跳过)</span>
            <span class="kpi-num text-warning">{{ oauthStats.need_phone }}</span>
          </div>
          <div class="plus-kpi-card" :class="{ 'card-danger': oauthStats.error > 0 }">
            <span class="kpi-label">❌ 失败 / 异常</span>
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

        <!-- 核心表格：每个账号一行实时状态 -->
        <div class="plus-table-wrap">
          <el-table
            :data="oauthRows"
            size="small"
            stripe
            :height="oauthConfigCollapsed ? '280px' : '170px'"
            class="plus-table"
          >
            <el-table-column prop="email" label="账号" min-width="190" show-overflow-tooltip />

            <el-table-column label="实时进度 / 状态" min-width="210" align="left">
              <template #default="{ row }">
                <el-tag v-if="row.status === 'running'" size="small" type="primary" effect="light">
                  <span class="spin-dot"></span> {{ row.step_text || '[1/6] 建立会话...' }}
                </el-tag>
                <el-tag v-else-if="row.status === 'pending'" size="small" type="info" effect="plain">
                  待处理
                </el-tag>
                <el-tag v-else-if="row.result?.status === 'success'" size="small" type="success" effect="light">
                  ✅ {{ row.result?.label || '成功' }}
                </el-tag>
                <el-tag v-else-if="row.result?.status === 'need_phone'" size="small" type="warning" effect="light">
                  📱 需接码(已跳过)
                </el-tag>
                <el-tag v-else size="small" type="danger" effect="light" :title="row.result?.error || ''">
                  ❌ {{ row.result?.label || '失败' }}
                </el-tag>
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
                <span class="mono hint" :style="{ color: row.status === 'running' ? 'var(--el-color-primary)' : '' }">
                  {{ getOAuthRowElapsed(row) }}
                </span>
              </template>
            </el-table-column>

            <el-table-column label="日志" width="80" align="center">
              <template #default="{ row }">
                <el-button size="small" text type="primary" @click="openOAuthItemLog(row)">
                  查看
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </div>

      <template #footer>
        <div class="oa-footer">
          <div class="footer-left" style="display: flex; gap: 8px">
            <el-button
              type="primary" plain size="small"
              :disabled="oauthStats.success === 0"
              @click="downloadCpaJson"
            >
              <el-icon><Download /></el-icon>下载 CPA JSON ({{ oauthStats.success }})
            </el-button>
            <el-button
              type="success" size="small"
              :disabled="oauthStats.success === 0"
              @click="downloadSub2Json"
            >
              <el-icon><Download /></el-icon>下载 SUB2 JSON ({{ oauthStats.success }})
            </el-button>
          </div>
          <div class="footer-right">
            <el-button size="small" @click="closeOAuthExport">关闭</el-button>
            <el-button
              v-if="oauthRunning"
              size="small" type="danger" plain
              @click="stopOAuthExportTask"
            >
              <el-icon><SwitchButton /></el-icon>停止任务
            </el-button>
            <el-button
              v-else
              type="primary" class="start-gradient-btn"
              :loading="oauthRunning"
              @click="startOAuthExportTask"
            >
              <el-icon><VideoPlay /></el-icon>{{ oauthTaskId ? '重新执行' : '开始导出' }}
            </el-button>
          </div>
        </div>
      </template>
    </el-dialog>

    <!-- ──────────────── 单账号 OAuth 详细日志弹窗 ──────────────── -->
    <el-dialog
      v-model="oauthLogModalVisible"
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
            <span class="modal-email">{{ currentOAuthLogItem?.email }}</span>
            <el-tag size="small" type="success" effect="plain" class="modal-run-tag">
              OAuth 导出日志
            </el-tag>
          </div>
        </div>
      </template>

      <div class="modal-terminal-wrap">
        <div class="modal-terminal-body">
          <div
            v-for="(line, idx) in oauthLogLines"
            :key="idx"
            class="terminal-line"
            :class="getLogClass(line)"
          >
            {{ line }}
          </div>
          <div v-if="!oauthLogLines.length" class="terminal-empty">
            {{ oauthLogLoading ? '正在加载日志...' : '暂无详细日志' }}
          </div>
        </div>
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
      :close-on-click-modal="false" @closed="closeHealthCheck"
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
                    <el-input-number v-model="healthForm.workers" :min="1" :max="10" style="width: 100%" />
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

        <!-- KPI 统计看板 -->
        <div class="plus-kpi-grid">
          <div class="plus-kpi-card">
            <span class="kpi-label">已验活 / 总数</span>
            <span class="kpi-num">{{ healthStats.done }} / {{ healthStats.total }}</span>
          </div>
          <div v-if="healthForm.mode === 'token'" class="plus-kpi-card hit-active">
            <span class="kpi-label">✅ Token 正常有效</span>
            <span class="kpi-num text-primary">{{ healthStats.token_valid }}</span>
          </div>
          <div v-if="healthForm.mode === 'plan' && healthStats.pro_active > 0" class="plus-kpi-card hit-pro">
            <span class="kpi-label">👑 Pro 账号</span>
            <span class="kpi-num text-pro">{{ healthStats.pro_active }}</span>
          </div>
          <div v-if="healthForm.mode === 'plan'" class="plus-kpi-card hit-active">
            <span class="kpi-label">★ Plus 订阅生效</span>
            <span class="kpi-num text-primary">{{ healthStats.plus_active }}</span>
          </div>
          <div v-if="healthForm.mode === 'plan'" class="plus-kpi-card hit-promo">
            <span class="kpi-label">◆ Plus 试用</span>
            <span class="kpi-num text-success">{{ healthStats.plus_eligible }}</span>
          </div>
          <div class="plus-kpi-card" :class="{ 'card-warn': healthStats.banned > 0 || healthStats.token_invalid > 0 }">
            <span class="kpi-label">封号 / 凭证失效</span>
            <span class="kpi-num text-danger">{{ healthStats.banned + healthStats.token_invalid }}</span>
          </div>
          <div class="plus-kpi-card">
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
            <el-radio-button label="failed">
              <span :class="{ 'text-danger': healthStats.error > 0 }">异常/失败 ({{ healthStats.error }})</span>
            </el-radio-button>
            <el-radio-button label="done">正常完成 ({{ Math.max(0, healthStats.done - healthStats.error) }})</el-radio-button>
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

        <div class="plus-table-box health-table-box">
          <el-table :data="healthDisplayRows" size="small" stripe height="330" class="macos-table" :highlight-current-row="false">
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
                  :type="row.result.status === 'token_valid' ? 'success' : row.result.status === 'plus_active' || row.result.status === 'team_active' ? 'primary' : row.result.status === 'plus_eligible' ? 'success' : row.result.status === 'pro_active' || row.result.status === 'pro_20x' || row.result.status === 'pro_5x' ? 'danger' : row.result.status === 'banned' || row.result.status === 'token_invalid' ? 'danger' : 'info'"
                >
                  {{ row.result.label || row.result.status }}
                </el-tag>
                <span v-else class="text-muted">—</span>
              </template>
            </el-table-column>

            <el-table-column label="耗时" width="80" align="center">
              <template #default="{ row }">
                <span v-if="row.elapsed" class="mono text-muted">{{ row.elapsed }}s</span>
                <span v-else-if="row.status === 'running'" class="mono text-primary">...</span>
                <span v-else class="text-muted">—</span>
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
            <span class="modal-email">{{ currentHealthLogItem?.email }}</span>
            <el-tag size="small" type="success" effect="plain" class="modal-run-tag">
              验活详细日志
            </el-tag>
          </div>
        </div>
      </template>

      <div class="modal-terminal-wrap">
        <div class="modal-terminal-body">
          <div v-for="(line, idx) in healthLogLines" :key="idx" class="terminal-line">
            {{ line }}
          </div>
          <div v-if="!healthLogLines.length" class="terminal-empty">
            {{ healthLogLoading ? '正在加载日志...' : '暂无详细日志' }}
          </div>
        </div>
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

    <!-- ──────────────── Token 重新获取与刷新控制台弹窗 (Token Refresh Studio) ──────────────── -->
    <el-dialog
      v-model="refreshVisible" width="880px" top="3vh"
      class="oa-custom-dialog plus-dialog health-dialog refresh-dialog"
      :close-on-click-modal="false" @closed="closeTokenRefresh"
    >
      <template #header>
        <div class="oa-header">
          <div class="oa-header-title">
            <span class="oa-title-badge" style="background: linear-gradient(135deg, #0ea5e9 0%, #0284c7 100%)">TOKEN</span>
            <span class="oa-title-text">Token 智能双模刷新与重获工作台</span>
            <el-tag size="small" type="primary" round effect="dark">RT极速置换 / Full OAuth重登</el-tag>
            <el-tag size="small" type="info" round effect="plain">{{ refreshTargetEmails.length }} 个账号</el-tag>
          </div>
          <div class="oa-header-extra">
            <el-button size="small" text @click="refreshConfigCollapsed = !refreshConfigCollapsed">
              <el-icon><Setting /></el-icon>{{ refreshConfigCollapsed ? '展开参数配置' : '收起参数配置' }}
            </el-button>
          </div>
        </div>
      </template>

      <div class="oa-dialog-container">
        <!-- 参数配置卡片 -->
        <el-collapse-transition>
          <div v-show="!refreshConfigCollapsed" class="oa-config-card" style="padding: 10px 14px 12px">
            <el-tabs v-model="refreshActiveTab" class="oa-config-tabs">
              <!-- Tab 1: 基础与网络 -->
              <el-tab-pane label="🌐 网络代理 & 刷新模式" name="network">
                <el-form label-position="top" :disabled="refreshRunning" size="small" style="margin-top: 6px">
                  <el-row :gutter="12">
                    <el-col :xs="24" :sm="12" :md="8">
                      <el-form-item label="检测/登录代理 (支持代理池轮询/直连)">
                        <el-select
                          v-model="refreshForm.proxy" filterable clearable allow-create default-first-option
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
                    <el-col :xs="24" :sm="12" :md="6">
                      <el-form-item label="代理目标国家">
                        <el-select
                          v-model="refreshForm.proxyCountry" filterable allow-create
                          placeholder="国家" style="width: 100%"
                        >
                          <el-option
                            v-for="c in COUNTRY_OPTIONS" :key="c.value"
                            :label="c.label" :value="c.value"
                          />
                        </el-select>
                      </el-form-item>
                    </el-col>
                    <el-col :xs="12" :sm="6" :md="5">
                      <el-form-item label="并发 Worker">
                        <el-input-number v-model="refreshForm.workers" :min="1" :max="20" style="width: 100%" />
                      </el-form-item>
                    </el-col>
                    <el-col :xs="12" :sm="6" :md="5">
                      <el-form-item label="超时(秒)">
                        <el-input-number v-model="refreshForm.timeout" :min="10" :max="120" style="width: 100%" />
                      </el-form-item>
                    </el-col>
                  </el-row>
                  <el-row :gutter="12">
                    <el-col :span="24">
                      <el-checkbox v-model="refreshForm.forceFullLogin">
                        强制走完整 OAuth 重新登录流程（跳过 RT 快速换取，直接打 OpenAI 登录端点获取全新全套凭证）
                      </el-checkbox>
                    </el-col>
                  </el-row>
                  <div style="font-size: 11.5px; color: var(--el-text-color-secondary); line-height: 1.5; margin-top: 4px">
                    💡 <b>智能机制</b>：有历史 Refresh Token 的账号优先 <b>200ms 极速置换</b>；失效或无 RT 账号自动触发 <b>Full OAuth 登录重获</b> 并自动写回数据库。
                  </div>
                </el-form>
              </el-tab-pane>

              <!-- Tab 2: 短信接码设置 (针对需要手机号验证的账号) -->
              <el-tab-pane label="📱 手机号风控接码设置 (可选)" name="sms">
                <el-form label-position="top" :disabled="refreshRunning" size="small" style="margin-top: 6px">
                  <el-row :gutter="12">
                    <el-col :span="24">
                      <el-checkbox v-model="refreshForm.smsEnabled">
                        启用 SMS 自动接码解封（遇到 OpenAI 要求绑定手机号时自动调用接码平台）
                      </el-checkbox>
                    </el-col>
                  </el-row>
                  <el-row v-if="refreshForm.smsEnabled" :gutter="12" style="margin-top: 6px">
                    <el-col :xs="24" :sm="8">
                      <el-form-item label="接码平台">
                        <el-select v-model="refreshForm.smsProvider" style="width: 100%">
                          <el-option label="SMSBower (高成功率推荐)" value="smsbower" />
                          <el-option label="SMS-Activate" value="smsactivate" />
                          <el-option label="HeroSMS" value="herosms" />
                          <el-option label="DaisySMS" value="daisysms" />
                        </el-select>
                      </el-form-item>
                    </el-col>
                    <el-col :xs="24" :sm="10">
                      <el-form-item label="API Key">
                        <el-input v-model="refreshForm.smsApiKey" placeholder="平台 API 密钥" clearable />
                      </el-form-item>
                    </el-col>
                    <el-col :xs="24" :sm="6">
                      <el-form-item label="国家代码">
                        <el-input v-model="refreshForm.smsCountry" placeholder="如 52(泰国), 6(印尼)" />
                      </el-form-item>
                    </el-col>
                  </el-row>
                </el-form>
              </el-tab-pane>
            </el-tabs>
          </div>
        </el-collapse-transition>

        <!-- KPI 统计看板 -->
        <div class="plus-kpi-grid">
          <div class="plus-kpi-card">
            <span class="kpi-label">已处理 / 总数</span>
            <span class="kpi-num">{{ refreshStats.done }} / {{ refreshStats.total }}</span>
          </div>
          <div class="plus-kpi-card hit-active">
            <span class="kpi-label">⚡ RT极速置换成功</span>
            <span class="kpi-num text-primary">{{ refreshStats.rt_fast_ok }}</span>
          </div>
          <div class="plus-kpi-card hit-promo">
            <span class="kpi-label">🔑 Full OAuth 重登成功</span>
            <span class="kpi-num text-success">{{ refreshStats.full_login_ok }}</span>
          </div>
          <div class="plus-kpi-card" :class="{ 'card-warn': refreshStats.need_phone > 0 }">
            <span class="kpi-label">需要手机号</span>
            <span class="kpi-num" :class="refreshStats.need_phone > 0 ? 'text-warning' : ''">{{ refreshStats.need_phone }}</span>
          </div>
          <div class="plus-kpi-card" :class="{ 'card-warn': refreshStats.error > 0 }">
            <span class="kpi-label">失败 / 异常</span>
            <span class="kpi-num text-danger">{{ refreshStats.error }}</span>
          </div>
          <div class="plus-progress-cell">
            <el-progress
              :percentage="refreshStats.percent"
              :status="refreshStats.done === refreshStats.total && refreshStats.total > 0 ? 'success' : ''"
              :stroke-width="8"
              striped
              :striped-flow="refreshRunning"
            />
          </div>
        </div>

        <!-- 核心表格：账号 Token 刷新监控列表 -->
        <div class="plus-table-box health-table-box">
          <el-table :data="refreshRows" size="small" stripe height="340" class="macos-table" :highlight-current-row="false">
            <el-table-column prop="email" label="账号邮箱" min-width="210" show-overflow-tooltip>
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

            <el-table-column label="刷新模式" width="130" align="center">
              <template #default="{ row }">
                <el-tag v-if="row.result?.method === 'rt_fast'" size="small" type="primary" effect="plain">⚡ RT 极速置换</el-tag>
                <el-tag v-else-if="row.result?.method === 'full_oauth'" size="small" type="success" effect="plain">🔑 OAuth 重登</el-tag>
                <span v-else-if="row.status === 'running'" class="mono text-primary text-xs">执行中...</span>
                <span v-else class="text-muted">—</span>
              </template>
            </el-table-column>

            <el-table-column label="当前状态 / 步骤" min-width="170" show-overflow-tooltip>
              <template #default="{ row }">
                <span v-if="row.status === 'running'" class="running-step">
                  <el-icon class="is-loading" style="margin-right: 4px"><Loading /></el-icon>
                  {{ row.step_text || '正在刷新...' }}
                </span>
                <el-tag v-else-if="row.status === 'pending'" size="small" type="info" effect="plain">排队中</el-tag>
                <el-tag
                  v-else-if="row.result"
                  size="small"
                  :type="row.result.status === 'success' ? 'success' : row.result.status === 'need_phone' ? 'warning' : 'danger'"
                >
                  {{ row.result.label || row.result.status }}
                </el-tag>
                <span v-else class="text-muted">—</span>
              </template>
            </el-table-column>

            <el-table-column label="耗时" width="80" align="center">
              <template #default="{ row }">
                <span v-if="row.elapsed" class="mono text-muted">{{ row.elapsed }}s</span>
                <span v-else-if="row.status === 'running'" class="mono text-primary">...</span>
                <span v-else class="text-muted">—</span>
              </template>
            </el-table-column>

            <el-table-column label="操作" width="85" align="center" fixed="right">
              <template #default="{ row }">
                <el-button size="small" text type="primary" :disabled="row.status === 'pending'" @click="openRefreshItemLog(row)">
                  <el-icon><Document /></el-icon>日志
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </div>

      <template #footer>
        <div class="oa-footer">
          <div class="footer-left" style="display: flex; gap: 8px">
            <el-button
              type="primary" plain size="small"
              :disabled="refreshStats.success === 0"
              @click="downloadTokenRefresh('txt')"
            >
              <el-icon><Download /></el-icon>下载 TXT 凭证 ({{ refreshStats.success }})
            </el-button>
            <el-button
              type="primary" size="small"
              :disabled="refreshStats.success === 0"
              @click="downloadTokenRefresh('cpa')"
            >
              <el-icon><Download /></el-icon>下载 CPA JSON
            </el-button>
            <el-button
              type="success" size="small"
              :disabled="refreshStats.success === 0"
              @click="downloadTokenRefresh('sub2api')"
            >
              <el-icon><Download /></el-icon>下载 Sub2API JSON
            </el-button>
          </div>
          <div class="footer-right">
            <el-button size="small" @click="closeTokenRefresh">
              {{ refreshRunning ? '后台运行' : '关闭' }}
            </el-button>
            <el-button
              v-if="refreshRunning"
              size="small" type="danger" plain
              @click="stopTokenRefreshTask"
            >
              <el-icon><SwitchButton /></el-icon>停止任务
            </el-button>
            <el-button
              v-else
              type="primary" class="start-gradient-btn"
              :loading="refreshRunning"
              :disabled="!refreshTargetEmails.length"
              @click="startTokenRefreshTask"
            >
              <el-icon><VideoPlay /></el-icon>{{ refreshTaskId ? '重新刷新' : '开始刷新/重获' }}
            </el-button>
          </div>
        </div>
      </template>
    </el-dialog>

    <!-- ──────────────── 单账号 Token 刷新详细日志终端弹窗 ──────────────── -->
    <el-dialog
      v-model="refreshLogModalVisible"
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
            <span class="modal-email">{{ currentRefreshLogItem?.email }}</span>
            <el-tag size="small" type="primary" effect="plain" class="modal-run-tag">
              Token 刷新/重登日志
            </el-tag>
          </div>
        </div>
      </template>

      <div class="modal-terminal-wrap">
        <div class="modal-terminal-body">
          <div
            v-for="(line, idx) in refreshLogLines"
            :key="idx"
            class="terminal-line"
            :class="getLogClass(line)"
          >
            {{ line }}
          </div>
          <div v-if="!refreshLogLines.length" class="terminal-empty">
            {{ refreshLogLoading ? '正在加载日志...' : '暂无详细日志' }}
          </div>
        </div>
      </div>

      <template #footer>
        <div class="modal-footer">
          <span class="log-count-tip">共 {{ refreshLogLines.length }} 行日志</span>
          <div class="modal-footer-btns">
            <el-button size="small" @click="copyText(refreshLogLines.join('\n'))">
              <el-icon><CopyDocument /></el-icon>复制全部日志
            </el-button>
            <el-button size="small" type="primary" @click="refreshLogModalVisible = false">
              关闭
            </el-button>
          </div>
        </div>
      </template>
    </el-dialog>

    <!-- 批量导出弹窗 -->
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
          <div class="modal-header-actions">
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

    <!-- ──────────────── 全渠道提炼模态任务台 (GCash/PIX/PayPal/iDEAL/UPI/Kakao/MoMo/TWINT/BLIK/Hosted 等) ──────────────── -->
    <ExtractTaskModal
      v-model="extractModalVisible"
      :channel="extractModalChannel"
      :emails="extractModalEmails"
      @finished="load(false)"
    />
  </div>
</template>

<style scoped>
/* ──────────── 页面整体布局：100% 高度 + 绝无外层滚动条 ──────────── */
.registered-page {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* macOS 风格主面板卡片 */
.macos-window-panel {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-light);
  border-radius: 10px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.04);
  overflow: hidden;
}

/* ──────────── macOS 风格顶部工具栏 ──────────── */
.macos-toolbar {
  padding: 10px 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  background: var(--el-fill-color-blank);
  border-bottom: 1px solid var(--el-border-color-lighter);
  flex-shrink: 0;
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
  width: 115px;
}
.macos-select.proxy-select {
  width: 175px;
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
  background: linear-gradient(135deg, #10b981, #059669);
  border: none;
  box-shadow: 0 2px 6px rgba(16, 185, 129, 0.25);
}
.oa-action-btn:hover:not(:disabled) {
  background: linear-gradient(135deg, #059669, #047857);
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
  border-color: rgba(16, 185, 129, 0.4);
  background: rgba(16, 185, 129, 0.08);
}
.geo-badge.geo-hot .geo-country {
  color: #10b981;
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
}
.token-len-cell.link:hover {
  text-decoration: underline;
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

/* ──────────── OAICS / Plus 控制台弹窗样式 ──────────── */
:deep(.oa-custom-dialog) {
  border-radius: 12px;
  overflow: hidden;
}
:deep(.oa-custom-dialog .el-dialog__header) {
  padding: 12px 18px;
  margin-right: 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
  background: var(--el-fill-color-light);
}
:deep(.oa-custom-dialog .el-dialog__body) {
  padding: 14px 18px;
}
:deep(.oa-custom-dialog .el-dialog__footer) {
  padding: 10px 18px;
  border-top: 1px solid var(--el-border-color-lighter);
  background: var(--el-fill-color-light);
}

.oa-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.oa-header-title {
  display: flex;
  align-items: center;
  gap: 8px;
}
.oa-title-badge {
  background: linear-gradient(135deg, #10b981, #059669);
  color: #fff;
  font-size: 11px;
  font-weight: 700;
  padding: 1px 7px;
  border-radius: 4px;
  letter-spacing: 0.5px;
}
.oa-title-badge.plus-badge {
  background: linear-gradient(135deg, #3b82f6, #1d4ed8);
}
.oa-title-badge.health-badge {
  background: linear-gradient(135deg, #0ea5e9, #0284c7);
}
.health-action-btn {
  background: linear-gradient(135deg, #0ea5e9, #0284c7) !important;
  border: none !important;
  color: #fff !important;
  font-weight: 600;
}
.oa-title-text {
  font-size: 14px;
  font-weight: 600;
}

.oa-dialog-container {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* 配置卡片 */
.oa-config-card {
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 12px;
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
.plus-kpi-card.hit-active {
  border-color: var(--el-color-primary-light-5);
  background: var(--el-color-primary-light-9);
}
.plus-kpi-card.hit-pro {
  border-color: rgba(244, 63, 94, 0.45);
  background: linear-gradient(135deg, rgba(244, 63, 94, 0.12), rgba(245, 158, 11, 0.08));
}
.plus-kpi-card.hit-team {
  border-color: rgba(99, 102, 241, 0.45);
  background: rgba(99, 102, 241, 0.08);
}
.text-pro {
  color: #f43f5e;
  font-weight: 700;
}
.text-team {
  color: #6366f1;
  font-weight: 700;
}
.plus-kpi-card.hit-promo {
  border-color: rgba(16, 185, 129, 0.4);
  background: rgba(16, 185, 129, 0.08);
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
  border-color: rgba(16, 185, 129, 0.4);
  background: rgba(16, 185, 129, 0.08);
}
.oa-kpi-item.kpi-hit .highlight { color: #10b981; }
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
  color: #10b981;
}

.start-gradient-btn {
  background: linear-gradient(135deg, #10b981, #059669);
  border: none;
}
.start-gradient-btn:hover:not(:disabled) {
  background: linear-gradient(135deg, #059669, #047857);
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

/* ──────────── 单账号详细日志终端弹窗 ──────────── */
:deep(.macos-terminal-dialog) {
  border-radius: 12px;
  overflow: hidden;
  background: #141418;
}
:deep(.macos-terminal-dialog .el-dialog__header) {
  padding: 10px 16px;
  margin-right: 0;
  background: #1e1e24;
  border-bottom: 1px solid #2a2a34;
}
:deep(.macos-terminal-dialog .el-dialog__body) {
  padding: 0;
}
:deep(.macos-terminal-dialog .el-dialog__footer) {
  padding: 10px 16px;
  background: #1e1e24;
  border-top: 1px solid #2a2a34;
}

.modal-header {
  display: flex;
  align-items: center;
  gap: 12px;
}
.window-dots {
  display: flex;
  align-items: center;
  gap: 6px;
}
.dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
}
.dot.red { background: #ff5f56; }
.dot.yellow { background: #ffbd2e; }
.dot.green { background: #27c93f; }

.modal-title-info {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
}
.modal-email {
  font-size: 13px;
  font-weight: 600;
  color: #f1f5f9;
  font-family: var(--el-font-family-monospace, monospace);
}
.modal-run-tag {
  font-size: 10.5px;
}

.modal-terminal-wrap {
  height: 400px;
  display: flex;
  flex-direction: column;
}
.modal-terminal-body {
  flex: 1;
  padding: 12px 16px;
  overflow-y: auto;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, Courier, monospace;
  font-size: 12px;
  line-height: 1.6;
  color: #d1d5db;
  word-break: break-all;
  white-space: pre-wrap;
  background: #141418;
}

.modal-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.log-count-tip {
  font-size: 11px;
  color: #94a3b8;
}
.modal-footer-btns {
  display: flex;
  gap: 8px;
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
  background: linear-gradient(135deg, #0ea5e9, #0284c7) !important;
  border-color: #0284c7 !important;
  color: #fff !important;
}
.refresh-token-action-btn:hover {
  background: linear-gradient(135deg, #0284c7, #0369a1) !important;
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
</style>
