<script setup>
import { computed, nextTick, onActivated, onUnmounted, reactive, ref, watch } from 'vue'
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
} from '@element-plus/icons-vue'
import {
  listRegistered,
  listRegisteredEmails,
  getRegistered,
  deleteRegistered,
  bulkDeleteRegistered,
  bulkDeleteAccounts,
  listExportFormats,
  exportRegistered,
  updateCredentials,
  startPlusCheck,
  stopPlusCheck,
  plusCheckStreamUrl,
  getPlusCheckLog,
  startOACheck,
  stopOACheck,
  oaCheckStreamUrl,
} from '@/api/register'
import { copyText, fmtTime, createSSE } from '@/api/request'
import { useFormStore, proxyText } from '@/stores/form'
import { useProxyStore } from '@/stores/proxy'
import { useRuntimeStore } from '@/stores/runtime'
import StatusDot from '@/components/StatusDot.vue'

const { form } = storeToRefs(useFormStore())
// 检测用的代理必须能从代理池里挑
const { list: proxyList } = storeToRefs(useProxyStore())
const runtime = useRuntimeStore()
const { dataVersion } = storeToRefs(runtime)

// 分页与数据
const rows = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const filter = ref('all')
const selected = ref([])
const loading = ref(false)

const PLUS_TYPE = {
  plus_eligible: 'success', plus_active: 'primary', free: 'warning',
  token_invalid: 'danger',
  banned: 'danger', error: 'danger',
}
function plusOf(row) { return row.plus_check || null }

// ════════════════════════ Plus 状态检测控制台 ════════════════════════
const plusVisible = ref(false)
const plusRunning = ref(false)
const plusTaskId = ref('')
const plusEs = ref(null)
const plusConfigCollapsed = ref(true) // 默认收起参数配置
const plusTargetEmails = ref([])
const plusItems = ref({})
const plusLogs = ref([])
const plusForm = reactive({
  proxies: '',
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
  const plus_active = items.filter((i) => i.result && i.result.status === 'plus_active').length
  const plus_eligible = items.filter((i) => i.result && i.result.status === 'plus_eligible').length
  const free = items.filter((i) => i.result && i.result.status === 'free').length
  const banned = items.filter((i) => i.result && i.result.status === 'banned').length
  const token_invalid = items.filter((i) => i.result && i.result.status === 'token_invalid').length
  const error = items.filter((i) => i.result && (i.result.status === 'error' || i.result.status === 'no_at' || i.result.status === 'not_found')).length
  const percent = tot > 0 ? Math.round((done / tot) * 100) : 0
  return {
    total: tot, done, running, pending,
    plus_active, plus_eligible, free, banned, token_invalid, error,
    percent,
  }
})

const PLUS_STATE_META = {
  plus_active:   { type: 'primary', label: 'Plus生效中', icon: 'Check' },
  plus_eligible: { type: 'success', label: '可领Plus试用', icon: 'Check' },
  free:          { type: 'warning', label: 'Free', icon: '' },
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
  if (!plusForm.proxies && proxyList.value.length) {
    plusForm.proxies = proxyList.value.join('\n')
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

  try {
    const res = await startPlusCheck({
      emails,
      proxies: plusForm.proxies,
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
const oaConfigCollapsed = ref(false)
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
    oaConfigCollapsed.value = false
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

// ════════════════════════ 数据加载与分页 ════════════════════════
async function load(resetPage = false) {
  if (resetPage) page.value = 1
  loading.value = true
  try {
    const { items, total: t } = await listRegistered({
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
      filter: filter.value,
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
  return CRED_KEYS.filter((k) => credData.value[k]).map((k) => ({ key: k, val: credData.value[k] }))
})

const CRED_META_DICT = {
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

          <el-select v-model="filter" class="macos-select filter-select" @change="load(true)">
            <el-option label="全部" value="all" />
            <el-option label="有 RT" value="has_rt" />
            <el-option label="无 RT" value="no_rt" />
            <el-option label="未检测" value="unchecked" />
            <el-option label="Free" value="free" />
            <el-option label="可领Plus" value="plus" />
            <el-option label="已封号" value="banned" />
            <el-option label="凭证失效" value="token_invalid" />
            <el-option label="OA未检" value="oa_unchecked" />
            <el-option label="OA命中" value="oa_hit" />
            <el-option label="OA未中" value="oa_miss" />
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
          <!-- 升级后的 Plus 检测操作组（全部采用现代化弹窗架构） -->
          <div class="macos-btn-group highlight-group">
            <el-button size="small" @click="openPlusCheck('unchecked')">检查未检</el-button>
            <el-button size="small" @click="openPlusCheck('all')">全重检</el-button>
            <el-button size="small" :disabled="!selected.length" @click="openPlusCheck('selected')">
              检选中 ({{ selected.length }})
            </el-button>
          </div>

          <!-- 核心高亮：资格检测 -->
          <el-button
            type="primary" class="oa-action-btn" :disabled="!selected.length"
            @click="openOA"
          >
            <el-icon><Compass /></el-icon>资格检测 ({{ selected.length }})
          </el-button>

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

          <!-- 出口地区 -->
          <el-table-column label="出口地区" width="140" show-overflow-tooltip>
            <template #default="{ row }">
              <span v-if="row.reg_country || row.reg_city" class="geo-badge">
                <span class="geo-country">{{ row.reg_country || '未知' }}</span>
                <span v-if="row.reg_city" class="geo-city"> · {{ row.reg_city }}</span>
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
                <el-col :span="13">
                  <el-form-item label="检测代理池 (每行一条；留空直连)">
                    <el-input
                      v-model="plusForm.proxies" type="textarea" :rows="3" class="mono oa-proxy-input"
                      placeholder="socks5h://user:pass@host:port&#10;http://user:pass@host:port"
                    />
                    <div class="oa-proxy-actions">
                      <el-button size="small" text type="primary" @click="loadProxyListToPlus">
                        载入全局代理池 ({{ proxyList.length }})
                      </el-button>
                      <el-button size="small" text @click="plusForm.proxies = ''">清空直连</el-button>
                    </div>
                  </el-form-item>
                </el-col>
                <el-col :span="11">
                  <el-row :gutter="12">
                    <el-col :span="12">
                      <el-form-item label="并发 Worker 数">
                        <el-input-number v-model="plusForm.workers" :min="1" :max="20" style="width: 100%" />
                      </el-form-item>
                    </el-col>
                    <el-col :span="12">
                      <el-form-item label="单请求超时 (秒)">
                        <el-input-number v-model="plusForm.timeout" :min="5" :max="60" style="width: 100%" />
                      </el-form-item>
                    </el-col>
                  </el-row>
                  <div class="plus-config-desc">
                    通过并发请求 ChatGPT 官方账号鉴权接口，实时探测每个账号的 Plus 订阅、试用资格、封号状态并保存至数据库。
                  </div>
                </el-col>
              </el-row>
            </el-form>
          </div>
        </el-collapse-transition>

        <!-- KPI 统计看板 -->
        <div class="plus-kpi-grid">
          <div class="plus-kpi-card">
            <span class="kpi-label">已检测 / 总数</span>
            <span class="kpi-num">{{ plusStats.done }} / {{ plusStats.total }}</span>
          </div>
          <div class="plus-kpi-card hit-active">
            <span class="kpi-label">★ Plus 生效中</span>
            <span class="kpi-num text-primary">{{ plusStats.plus_active }}</span>
          </div>
          <div class="plus-kpi-card hit-promo">
            <span class="kpi-label">◆ 可领 Plus 试用</span>
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
      v-model="oaVisible" width="980px" top="3vh"
      class="oa-custom-dialog"
      :close-on-click-modal="false" @closed="closeOA"
    >
      <template #header>
        <div class="oa-header">
          <div class="oa-header-title">
            <span class="oa-title-badge">OAICS</span>
            <span class="oa-title-text">资格检测控制台</span>
            <el-tag size="small" type="info" round effect="plain">{{ selected.length }} 个账号</el-tag>
          </div>
          <div v-if="oaTaskId" class="oa-header-extra">
            <el-button size="small" text @click="oaConfigCollapsed = !oaConfigCollapsed">
              <el-icon><Setting /></el-icon>{{ oaConfigCollapsed ? '展开参数配置' : '收起参数配置' }}
            </el-button>
          </div>
        </div>
      </template>

      <div class="oa-dialog-container">
        <!-- 配置卡片 -->
        <el-collapse-transition>
          <div v-show="!oaTaskId || !oaConfigCollapsed" class="oa-config-card">
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

        <!-- 运行监控面板 -->
        <template v-if="oaTaskId">
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
                :status="oaStats.done === oaStats.total ? 'success' : ''"
                :stroke-width="10"
                striped
                :striped-flow="oaRunning"
              />
            </div>
          </div>

          <!-- 双栏监控 -->
          <div class="oa-monitor-split">
            <!-- 左侧表格 -->
            <div class="oa-table-box">
              <el-table :data="oaRows" size="small" stripe height="100%" :highlight-current-row="false">
                <el-table-column prop="email" label="邮箱" min-width="170" show-overflow-tooltip />
                <el-table-column label="状态" width="75" align="center">
                  <template #default="{ row }">
                    <el-tag v-if="row.status === 'done'" type="success" size="small">完成</el-tag>
                    <el-tag v-else-if="row.status === 'running'" type="warning" size="small" effect="dark">检测中</el-tag>
                    <el-tag v-else-if="row.status === 'cancelled'" type="info" size="small">已取消</el-tag>
                    <el-tag v-else type="info" size="small" effect="plain">排队</el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="结果" min-width="155">
                  <template #default="{ row }">
                    <template v-if="row.result">
                      <el-tag
                        :type="(OA_STATE_META[row.result.state] || {}).type || 'info'"
                        size="small"
                        :effect="row.result.state === 'OAICS' ? 'dark' : 'light'"
                      >
                        {{ (OA_STATE_META[row.result.state] || { label: row.result.state }).label }}
                      </el-tag>
                      <span v-if="row.result.session_id_masked" class="hint mono" style="margin-left: 4px; font-size: 11px">
                        {{ row.result.session_id_masked }}
                      </span>
                      <el-tooltip v-if="row.result.error" :content="row.result.error" placement="top">
                        <span class="hint error-hint" style="margin-left: 4px; color: var(--el-color-danger); cursor: help">⚠</span>
                      </el-tooltip>
                    </template>
                    <span v-else class="hint">—</span>
                  </template>
                </el-table-column>
                <el-table-column label="耗时" width="75" align="right">
                  <template #default="{ row }">
                    <span class="mono" style="font-size: 11px">{{ row.result && row.result.elapsed_ms ? row.result.elapsed_ms + 'ms' : '—' }}</span>
                  </template>
                </el-table-column>
              </el-table>
            </div>

            <!-- 右侧终端 -->
            <div class="oa-terminal-box">
              <div class="oa-terminal-header">
                <span class="terminal-dot red"></span>
                <span class="terminal-dot yellow"></span>
                <span class="terminal-dot green"></span>
                <span class="terminal-title">实时探测日志 ({{ oaLogs.length }} 行)</span>
                <el-button size="small" text class="terminal-clear-btn" @click="oaLogs = []">清屏</el-button>
              </div>
              <div id="oa-log-box" class="oa-terminal-body">
                <div v-for="(log, idx) in oaLogs" :key="idx" class="terminal-line" :class="getLogClass(log)">
                  {{ log }}
                </div>
                <div v-if="!oaLogs.length" class="terminal-empty">等待日志输出...</div>
              </div>
            </div>
          </div>
        </template>
      </div>

      <template #footer>
        <div class="oa-dialog-footer">
          <div class="footer-tip">
            <span v-if="oaRunning" class="running-indicator">
              <span class="pulse-dot"></span> 检测进行中 (并发: {{ oaForm.workers }})...
            </span>
            <span v-else-if="oaTaskId" class="finished-indicator">
              检测完毕，结果已自动保存至数据库
            </span>
          </div>
          <div class="footer-btns">
            <el-button @click="closeOA">关闭</el-button>
            <el-button v-if="oaRunning" type="danger" plain @click="stopOA">
              停止检测
            </el-button>
            <el-button v-else type="primary" :loading="oaRunning" @click="startOA">
              {{ oaTaskId ? '重新检测' : '开始检测' }}
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
</style>
