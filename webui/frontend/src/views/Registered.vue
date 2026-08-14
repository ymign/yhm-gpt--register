<script setup>
import { computed, nextTick, onActivated, reactive, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  listRegistered, getRegistered, deleteRegistered,
  bulkDeleteRegistered, bulkDeleteAccounts, checkPlus,
  listExportFormats, exportRegistered, updateCredentials,
  startOACheck, stopOACheck, oaCheckStreamUrl,
} from '@/api/register'
import { copyText, fmtTime, createSSE } from '@/api/request'
import { useFormStore, proxyText } from '@/stores/form'
import { useProxyStore } from '@/stores/proxy'
import { useRuntimeStore } from '@/stores/runtime'
import StatusDot from '@/components/StatusDot.vue'

const { form } = storeToRefs(useFormStore())
// 检测用的代理必须能从代理池里挑：以前这页只在代码里读 form.proxy，页面上
// 连个输入框都没有，主人在代理池换了密码，这里还在用 localStorage 里的旧值，
// 结果是 curl:(97) 代理鉴权被拒 → 静默降级直连 → 拿真实 IP 打 chatgpt.com。
const { list: proxyList } = storeToRefs(useProxyStore())
const runtime = useRuntimeStore()
// dataVersion 要走 storeToRefs 才保持响应（watch 用）；bumpData 是 action，直接从
// store 实例上取 —— storeToRefs 只转 state/getter，把 action 解构出来会丢 this。
const { dataVersion } = storeToRefs(runtime)

const PAGE_SIZE = 20
const rows = ref([])
const total = ref(0)
const page = ref(1)
const filter = ref('all')
const selected = ref([])
const loading = ref(false)
const checking = ref(false)
const checkResult = ref('')

const PLUS_TYPE = {
  plus_eligible: 'success', plus_active: 'primary', free: 'warning',
  // token_invalid（401 且响应体没有封号措辞）仍与 banned 分开显示——判据不同，
  // 不能混成一个。但配色从橙改红：AT 未到期却 401 = 被吊销，实测多半就是封号，
  // 橙色（=号还在）会让主人以为重新登录就能救回来。
  token_invalid: 'danger',
  banned: 'danger', error: 'danger',
}
function plusOf(row) { return row.plus_check || null }

// ──────────── OAICS 资格检测 ────────────
// 对勾选的账号批量打 OpenAI checkout，判断 session id 是 oaics_（可卖资格）还是 cs_。
// 代理池支持 sticky 代理（user:pass-CC-session-ttl@host:port 格式），轮换会话。
const oaVisible = ref(false)
const oaRunning = ref(false)
const oaTaskId = ref('')
const oaEs = ref(null)
// 弹窗配置
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

function guessProxyCountry(text) {
  if (!text) return ''
  const m = text.match(/(?:-region-|-country-|_country-)([a-zA-Z]{2})/i) || text.match(/-([a-zA-Z]{2})-\d+-\d+/i)
  if (m && m[1]) return m[1].toUpperCase()
  return ''
}

function loadProxyListToOA() {
  oaForm.proxies = proxyList.value.join('\n')
  const g = guessProxyCountry(oaForm.proxies)
  if (g) oaForm.proxyCountry = g
}
// 进度：email -> { status: 'pending'|'running'|'done', result: {...} }
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
  if (line.includes('HIT') || line.includes('oaics_')) return 'log-hit'
  if (line.includes('MISS') || line.includes('state=CS')) return 'log-miss'
  if (line.includes('err=') || line.includes('ERROR') || line.includes('失败')) return 'log-err'
  if (line.includes('[task]')) return 'log-task'
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
  } catch (e) {
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
        } catch (_) { /* ignore */ }
      },
      progress: (ev) => {
        try {
          const msg = JSON.parse(ev.data)
          if (msg.email) {
            oaItems.value[msg.email] = { status: msg.status, result: msg.result || null }
            const c = oaCount()
            oaSummary.value = `正在检测：已完成 ${c.done}/${c.total} (命中 ${c.hit} 个 OAICS)`
          }
        } catch (_) { /* ignore */ }
      },
      log: (ev) => {
        try {
          const msg = JSON.parse(ev.data)
          if (msg.line) {
            oaLogs.value.push(msg.line)
            if (oaLogs.value.length > 500) oaLogs.value.splice(0, oaLogs.value.length - 500)
            nextTick(scrollOaLog)
          }
        } catch (_) { /* ignore */ }
      },
      end: () => {
        const c = oaCount()
        oaSummary.value = `检测完成！共 ${c.total} 个账号，完成 ${c.done} 个，命中 ${c.hit} 个 OAICS`
        oaRunning.value = false
        if (oaEs.value) {
          oaEs.value.close()
          oaEs.value = null
        }
        load(false) // 刷新表格里的 OA资格 列
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

async function load(resetPage) {
  if (resetPage) page.value = 1
  loading.value = true
  try {
    const { items, total: t } = await listRegistered({
      limit: PAGE_SIZE, offset: (page.value - 1) * PAGE_SIZE, filter: filter.value,
    })
    rows.value = items
    total.value = t
  } catch (e) { ElMessage.error(e.message) }
  finally { loading.value = false }
}

function collectEmails(mode) {
  if (mode === 'selected') return selected.value.map((r) => r.email)
  if (mode === 'unchecked') return rows.value.filter((r) => !plusOf(r)).map((r) => r.email)
  return rows.value.map((r) => r.email) // all（当前页）
}

async function doCheck(mode) {
  const emails = collectEmails(mode)
  if (!emails.length) { ElMessage.info('当前页没有可检测的号'); return }
  checking.value = true
  checkResult.value = `检查中... (${emails.length} 个)`
  try {
    const { results, note } = await checkPlus(emails, proxyText(form.value))
    let plus = 0, free = 0, banned = 0, failed = 0, badToken = 0
    for (const [email, info] of Object.entries(results)) {
      const row = rows.value.find((r) => r.email === email)
      if (row) row.plus_check = info
      if (info.status === 'plus_eligible' || info.status === 'plus_active') plus++
      else if (info.status === 'banned') banned++
      else if (info.status === 'free') free++
      else if (info.status === 'token_invalid') badToken++
      else if (info.status === 'error') failed++
    }
    // failed / note 不入库，只是这一次的现场说明：
    // 以前网络/代理挂了这里只会显示「0 可用Plus, 0 Free, 0 封号」，看不出是没检测成。
    // badToken 从 2026-08-10 起是**会入库**的结论，措辞也跟着改：
    // AT 没过期却 401 = 被吊销，大概率就是封号，不该再说得像只是要重新登录。
    const parts = [`完成: ${plus} 可用Plus, ${free} Free, ${banned} 封号`]
    if (badToken) parts.push(`${badToken} 个凭证失效（AT 被吊销，多半已封）`)
    if (failed) parts.push(`${failed} 个没检测成`)
    if (note) parts.push(note)
    checkResult.value = parts.join(' · ')
  } catch (e) {
    checkResult.value = ''
    ElMessage.error('检查失败: ' + e.message)
  } finally { checking.value = false }
}

// customClass 里的 pre-line 让消息里的 \n 真的换行。
// 不用 dangerouslyUseHTMLString：消息里会拼邮箱、文件名这些数据，走 HTML 等于开 XSS 口子。
async function confirm(msg) {
  try {
    await ElMessageBox.confirm(msg, '确认', {
      type: 'warning', confirmButtonText: '确定', cancelButtonText: '取消',
      customClass: 'confirm-multiline',
    })
    return true
  }
  catch (_) { return false }
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
// 格式清单来自后端 export_formats.py，下拉菜单是 v-for 出来的：
// 以后加格式只改后端那一个文件，这里一行都不用动。
const exportFormats = ref([])
const exporting = ref(false)
const exportVisible = ref(false)
const exportText = ref('')
const exportCount = ref(0)
const exportFilename = ref('')
const exportLabel = ref('')
// 这一批导出的到底是哪些号 —— 「下载并删除」照着它删，来自后端 r.emails。
// 为什么要后端给、为什么在导出那一刻就存下来：
//   · 「导出全部」是跨页的，前端手里只有当前页 20 行，自己凑必漏；
//   · 弹窗开着的时候主人可能改勾选、翻页，后台自动跑号还会插进新号进来，
//     那时再去读 selected/表格，删的就不是刚下载的那批了。
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
  // 没勾选 = 导出全部（跨页，不只当前页）
  const payload = emails.length ? { format: fmt.id, emails } : { format: fmt.id, all: true }
  exporting.value = true
  try {
    const r = await exportRegistered(payload)
    exportedEmails.value = (r.emails || []).filter(Boolean)
    // download 模式（CPA zip / SUB2API json）：不弹预览，直接落盘
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

// ──────────── 下载并删除 ────────────
// 主人的原话：「不然分不清楚越堆越多」。导出的 txt 里邮箱/密码/2FA/取件url 都齐了，
// 这两张表就没有留存价值了，一起清掉。
//
// ⚠️ 顺序**必须**是「先下载、再确认、最后删」：
//    删库是不可恢复的，而浏览器下载可能被拦（弹窗拦截 / 用户点了取消 / 磁盘满）。
//    先把文件落盘再问，主人是在**手里已经有 txt** 的前提下点的确认。
//    确认框里再报一遍将要删的两张表各多少条，删完之前还有最后一次反悔机会。
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
    // 两张表分别删。先删注册结果：它是主人真正在看的那张表，
    // 万一号池那边报错（比如这批号根本不是号池导入的、压根没有对应行），
    // 至少结果表已经清干净了，不会出现"删了一半还看得见"。
    const r1 = await bulkDeleteRegistered({ emails })
    let poolDeleted = 0
    try {
      const r2 = await bulkDeleteAccounts({ emails })
      poolDeleted = r2.deleted || 0
    } catch (e) {
      // 号池删失败不算整体失败：凭证已经清掉了，主人该知道的是号池还剩着
      ElMessage.warning('注册结果已删，但邮箱列表删除失败: ' + e.message)
    }
    ElMessage.success(`已删除：注册结果 ${r1.deleted} 条 / 邮箱列表 ${poolDeleted} 条`)
    exportVisible.value = false
    exportedEmails.value = []
    selected.value = []
    load(true)          // 回第一页：这一批没了，停在旧页码多半是空页
    runtime.bumpData()  // 通知「邮箱列表」那一页也刷新，否则主人切过去还看得到已删的号
  } catch (e) {
    ElMessage.error('删除失败: ' + e.message)
  } finally {
    deletingExported.value = false
  }
}

// 凭证弹窗
const credVisible = ref(false)
const credEmail = ref('')
const credData = ref(null)
// totp_secret 放最前：它是唯一「服务端取不回」的字段，弹窗一打开就要能看到
const CRED_KEYS = ['totp_secret', 'totp_factor_id', 'access_token', 'session_token', 'refresh_token', 'id_token', 'device_id', 'csrf_token', 'cookie_header', 'password']
const credRows = computed(() => {
  if (!credData.value) return []
  return CRED_KEYS.filter((k) => credData.value[k]).map((k) => ({ key: k, val: credData.value[k] }))
})
async function viewCred(email) {
  try {
    const { data } = await getRegistered(email)
    credData.value = data
    credEmail.value = email
    credVisible.value = true
  } catch (e) { ElMessage.error('加载凭证失败: ' + e.message) }
}
async function copyCell(email, field) {
  try {
    const { data } = await getRegistered(email)
    const val = data[field] || ''
    if (!val) { ElMessage.warning(`${field} 为空`); return }
    await copyText(val)
  } catch (e) { ElMessage.error('加载凭证失败: ' + e.message) }
}
function copyAllJson() {
  if (credData.value) copyText(JSON.stringify(credData.value, null, 2))
}

// ── 手动编辑凭证 ──
// 只改本地库，不同步 OpenAI。改完的值会被登录流程直接用上
// （registrar 的 account_callback 走 db.get_registered，不区分数据来源）。
const editVisible = ref(false)
const editSaving = ref(false)
const editEmail = ref('')
const editPassword = ref('')
const editSecret = ref('')
// 打开弹窗时的原值，用来判断哪些字段真被改过（没改的不传，后端就不碰）
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
  // 只把真正改动过的字段传给后端 —— 没动的字段不传，后端就不会碰它
  if (pw !== editOrigPassword.value) payload.password = pw
  if (sec !== editOrigSecret.value) payload.totp_secret = sec
  if (payload.password === undefined && payload.totp_secret === undefined) {
    ElMessage.info('没有改动')
    editVisible.value = false
    return
  }
  // secret 是唯一「服务端取不回」的凭证：覆盖掉原值 = 该号 2FA 永久锁死。
  // 只在「原本就有 secret」且「确实要改」时拦一道，新填不打扰。
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
    // 后端 400 会带具体原因（如「TOTP secret 含非法字符」），原样透出
    ElMessage.error('保存失败: ' + (e.response?.data?.detail || e.message))
  } finally { editSaving.value = false }
}

watch(page, () => load())
watch(dataVersion, () => load())
onActivated(() => load())
</script>
<template>
  <div class="page">
    <el-card shadow="never">
      <template #header><span class="section-title" style="margin: 0">注册结果</span></template>

      <el-space wrap style="margin-bottom: 12px">
        <el-button @click="load(false)"><el-icon><Refresh /></el-icon>刷新</el-button>
        <el-select v-model="filter" style="width: 130px" @change="load(true)">
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
          style="width: 260px"
        >
          <el-option v-for="p in proxyList" :key="p" :label="p" :value="p" />
        </el-select>
        <el-button :loading="checking" @click="doCheck('unchecked')">检查未检测</el-button>
        <el-button :loading="checking" @click="doCheck('all')">重新检查</el-button>
        <el-button :loading="checking" :disabled="!selected.length" @click="doCheck('selected')">
          检测选中 ({{ selected.length }})
        </el-button>
        <el-button type="primary" :disabled="!selected.length" @click="openOA">
          资格检测 ({{ selected.length }})
        </el-button>
        <el-divider direction="vertical" />
        <el-dropdown trigger="click" @command="doExport" @visible-change="(v) => v && loadExportFormats()">
          <el-button :loading="exporting">
            <el-icon><Download /></el-icon>{{ exportBtnText }}
            <el-icon class="el-icon--right"><ArrowDown /></el-icon>
          </el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item v-for="f in exportFormats" :key="f.id" :command="f" :divided="f.mode === 'download' && f.id === 'cpa'">
                {{ f.label }}
                <span v-if="f.note" class="hint" style="margin-left: 6px">{{ f.note }}</span>
              </el-dropdown-item>
              <el-dropdown-item v-if="!exportFormats.length" disabled>加载中...</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <el-divider direction="vertical" />
        <el-button type="danger" plain :disabled="!selected.length" @click="deleteSelected">
          删除选中 ({{ selected.length }})
        </el-button>
        <el-button type="danger" plain @click="deleteAll">清空全部</el-button>
        <span class="hint">{{ checkResult }}</span>
      </el-space>

      <el-skeleton v-if="loading && !rows.length" :rows="6" animated style="padding: 8px 0" />
      <el-table
        v-else
        v-loading="loading" :data="rows" size="small" stripe
        @selection-change="(v) => (selected = v)"
      >
        <el-table-column type="selection" width="44" />
        <el-table-column prop="email" label="邮箱" min-width="200" show-overflow-tooltip />
        <!-- 密码直接明文列出：随机 16 位，是登录账号的必需品，
             藏进「查看凭证」弹窗每次都要多点两下。列表接口本来就在返回它。
             图标放在文字**后面**：放前面会把值整体右推 27px（见 .cell-copy 注释）。 -->
        <el-table-column label="密码" min-width="170">
          <template #default="{ row }">
            <el-button
              v-if="row.password" size="small" text type="primary"
              class="cell-copy mono" @click="copyText(row.password)"
            >
              {{ row.password }}<el-icon class="ico"><CopyDocument /></el-icon>
            </el-button>
            <span v-else class="hint">—</span>
          </template>
        </el-table-column>
        <!-- 2FA secret 同样明文列出：它是唯一「服务端取不回」的凭证，
             丢了这个号就永久锁死，必须一眼看见、一点就能复制。
             min-width 必须装得下 32 位 base32：.cell 带 overflow:hidden，
             宽度不够会**无声截断**，肉眼核对时看到的是残缺值。实测需 ~250px。 -->
        <el-table-column label="2FA" min-width="260">
          <template #default="{ row }">
            <el-button
              v-if="row.totp_secret" size="small" text type="warning"
              class="cell-copy mono" @click="copyText(row.totp_secret)"
            >
              {{ row.totp_secret }}<el-icon class="ico"><CopyDocument /></el-icon>
            </el-button>
            <span v-else class="hint">—</span>
          </template>
        </el-table-column>
        <el-table-column label="Plus状态" width="120">
          <template #default="{ row }">
            <StatusDot v-if="plusOf(row)" :type="PLUS_TYPE[plusOf(row).status] || 'info'" :text="plusOf(row).label" />
            <span v-else class="hint">—</span>
          </template>
        </el-table-column>
        <el-table-column label="OA资格" width="120">
          <template #default="{ row }">
            <el-tooltip
              v-if="row.oa_check && oaMeta(row)"
              :content="row.oa_check.error || `${row.oa_check.state} · ${row.oa_check.elapsed_ms || 0}ms · ${row.oa_check.session_id_masked || '无 sid'}`"
              placement="top"
            >
              <el-tag :type="oaMeta(row).type" size="small" effect="light">{{ oaMeta(row).label }}</el-tag>
            </el-tooltip>
            <span v-else class="hint">—</span>
          </template>
        </el-table-column>
        <el-table-column label="access" width="100" align="center">
          <template #default="{ row }">
            <el-button v-if="row.at_len > 0" size="small" text type="primary" @click="copyCell(row.email, 'access_token')">
              <el-icon><CopyDocument /></el-icon>{{ row.at_len }}
            </el-button>
            <span v-else class="hint">—</span>
          </template>
        </el-table-column>
        <el-table-column label="session" width="100" align="center">
          <template #default="{ row }">
            <el-button v-if="row.st_len > 0" size="small" text type="primary" @click="copyCell(row.email, 'session_token')">
              <el-icon><CopyDocument /></el-icon>{{ row.st_len }}
            </el-button>
            <span v-else class="hint">—</span>
          </template>
        </el-table-column>
        <el-table-column label="refresh" width="100" align="center">
          <template #default="{ row }">
            <el-button v-if="row.rt_len > 0" size="small" text type="primary" @click="copyCell(row.email, 'refresh_token')">
              <el-icon><CopyDocument /></el-icon>{{ row.rt_len }}
            </el-button>
            <span v-else class="hint">—</span>
          </template>
        </el-table-column>
        <el-table-column label="时间" width="160">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button size="small" text @click="viewCred(row.email)">查看凭证</el-button>
            <el-button size="small" text type="warning" @click="openEdit(row)">编辑</el-button>
            <el-button size="small" text type="danger" @click="deleteOne(row.email)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无注册结果，去「单次注册」或「全自动批量」跑号" :image-size="70" />
        </template>
      </el-table>
      <div style="display: flex; justify-content: center; margin-top: 14px">
        <el-pagination
          v-model:current-page="page" :page-size="PAGE_SIZE" :total="total"
          layout="prev, pager, next, total" background
        />
      </div>

      <el-dialog v-model="exportVisible" width="720px" top="8vh">
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
          <!-- 危险动作放最右、danger 色，和左边的纯下载拉开距离，避免手滑。
               先下载文件、再弹二次确认，确认框里会报清楚要删哪两张表各多少条。 -->
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

      <el-dialog v-model="credVisible" :title="credEmail" width="760px" top="6vh">
        <template #header>
          <div style="display: flex; align-items: center; gap: 12px">
            <span class="mono" style="font-weight: 600">{{ credEmail }}</span>
            <el-button size="small" @click="copyAllJson">复制全部 JSON</el-button>
          </div>
        </template>
        <div v-for="r in credRows" :key="r.key" style="margin-bottom: 12px">
          <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 4px">
            <span class="mono" style="font-weight: 600; color: var(--dango-pink-dark)">{{ r.key }}</span>
            <el-tag size="small" type="info">len={{ r.val.length }}</el-tag>
            <el-button size="small" @click="copyText(r.val)">复制</el-button>
          </div>
          <el-input :model-value="r.val" type="textarea" :rows="2" readonly class="mono" />
        </div>
        <el-empty v-if="!credRows.length" description="无凭证字段" />
      </el-dialog>

      <!-- 手动编辑凭证：把外部已知的密码/2FA 补进来，或修正记录错误 -->
      <el-dialog v-model="editVisible" title="编辑凭证" width="560px" top="10vh">
        <el-alert
          type="warning" :closable="false" show-icon style="margin-bottom: 16px"
          title="仅修改本地记录，不会同步到 OpenAI"
          description="这里改密码不等于改了账号密码。填入的值会被登录流程直接使用。"
        />
        <el-form label-position="top">
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

      <!-- OAICS 资格检测：紧凑弹窗 + 左右分栏实时看板 -->
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
          <!-- 配置区域（开始后可折叠收起） -->
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

          <!-- 运行状态看板（任务开始后显示） -->
          <template v-if="oaTaskId">
            <!-- 统计指标行 KPI Cards + Progress Bar -->
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

            <!-- 核心监控分栏：左侧账号列表 + 右侧实时日志终端 -->
            <div class="oa-monitor-split">
              <!-- 左侧：账号状态明细表格 -->
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

              <!-- 右侧：黑色终端风格日志 -->
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
    </el-card>
  </div>
</template>

<style scoped>
/* 表格里「点一下就复制」的明文单元格（密码 / 2FA secret）。
   :deep 是必需的：.el-button 由 Element Plus 渲染，scoped 的属性选择器打不到它。

   为什么要重置 padding —— Element Plus 有两个长得很像的类：
     .el-button--text  （旧版 type="text"）  padding 左右为 0
     .el-button.is-text（新版 text 属性）    继承 --small 的 5px 11px
   我们用的是后者，于是 11px padding + 12px 图标 + 4px 间隙 = 值被整体右推 27px，
   同列的表头和空值「—」都贴着 cell 左沿，一眼就看出错位。 */
:deep(.el-button.cell-copy.el-button--small) {
  padding: 0 6px 0 0;
  height: 20px;
  font-size: 12px;
}
/* 图标默认透明但**保留占位**：用 opacity 而不是 display:none，
   否则 hover 时图标撑开宽度会把文字挤得左右抖。 */
:deep(.cell-copy .ico) {
  margin-left: 5px;
  opacity: 0;
  transition: opacity 0.12s;
}
:deep(.cell-copy:hover .ico) { opacity: 0.65; }

/* ──────────── 资格检测控制台精致样式 ──────────── */
:deep(.oa-custom-dialog) {
  border-radius: 12px;
  overflow: hidden;
}
:deep(.oa-custom-dialog .el-dialog__header) {
  padding: 14px 20px 10px;
  margin-right: 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
}
:deep(.oa-custom-dialog .el-dialog__body) {
  padding: 12px 20px;
}
:deep(.oa-custom-dialog .el-dialog__footer) {
  padding: 10px 20px 14px;
  border-top: 1px solid var(--el-border-color-lighter);
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
  font-weight: 700;
  font-size: 12px;
  padding: 2px 7px;
  border-radius: 4px;
  letter-spacing: 0.5px;
}
.oa-title-text {
  font-size: 15px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.oa-dialog-container {
  height: 520px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  overflow: hidden;
}
.oa-config-card {
  padding: 10px 14px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  flex-shrink: 0;
}
.oa-proxy-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 2px;
}
.oa-options-row {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-top: 4px;
}
.oa-kpi-bar {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 8px 14px;
  background: var(--el-fill-color-lighter);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  flex-shrink: 0;
}
.oa-kpi-item {
  display: flex;
  flex-direction: column;
  min-width: 65px;
}
.oa-kpi-item .kpi-label {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}
.oa-kpi-item .kpi-val {
  font-size: 15px;
  font-weight: 700;
  font-family: var(--el-font-family-monospace, monospace);
  color: var(--el-text-color-primary);
}
.oa-kpi-item.kpi-hit .kpi-val {
  color: #10b981;
}
.oa-kpi-item.kpi-warn .kpi-val {
  color: var(--el-color-danger);
}
.oa-progress-wrap {
  flex: 1;
  margin-left: 10px;
}
.oa-monitor-split {
  flex: 1;
  display: flex;
  gap: 12px;
  min-height: 0;
  overflow: hidden;
}
.oa-table-box {
  flex: 1.15;
  height: 100%;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  overflow: hidden;
}
.oa-terminal-box {
  flex: 0.85;
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #141418;
  border: 1px solid #272730;
  border-radius: 8px;
  overflow: hidden;
}
.oa-terminal-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  background: #1e1e24;
  border-bottom: 1px solid #2a2a34;
  flex-shrink: 0;
}
.terminal-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
}
.terminal-dot.red { background: #ff5f56; }
.terminal-dot.yellow { background: #ffbd2e; }
.terminal-dot.green { background: #27c93f; }
.terminal-title {
  font-size: 11px;
  color: #94a3b8;
  margin-left: 4px;
  flex: 1;
}
.terminal-clear-btn {
  font-size: 11px;
  color: #94a3b8;
  padding: 0 4px;
  height: 20px;
}
.oa-terminal-body {
  flex: 1;
  padding: 8px 10px;
  overflow-y: auto;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, Courier, monospace;
  font-size: 11.5px;
  line-height: 1.55;
  color: #d1d5db;
  word-break: break-all;
  white-space: pre-wrap;
}
.terminal-line.log-hit { color: #4ade80; font-weight: 600; }
.terminal-line.log-miss { color: #9ca3af; }
.terminal-line.log-err { color: #f87171; }
.terminal-line.log-task { color: #60a5fa; }
.terminal-empty {
  color: #64748b;
  text-align: center;
  margin-top: 40px;
  font-size: 12px;
}
.oa-dialog-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.footer-tip {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.running-indicator {
  display: flex;
  align-items: center;
  color: var(--el-color-primary);
  font-weight: 500;
}
.pulse-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--el-color-primary);
  margin-right: 6px;
  animation: oa-pulse 1.4s infinite;
}
@keyframes oa-pulse {
  0% { transform: scale(0.85); opacity: 0.6; }
  50% { transform: scale(1.3); opacity: 1; }
  100% { transform: scale(0.85); opacity: 0.6; }
}
</style>

<!-- 非 scoped：ElMessageBox 是挂到 body 上的，不在本组件的 scope 属性范围内，
     scoped 样式打不到它。只作用在自家 customClass 上，不会污染别处的确认框。 -->
<style>
.confirm-multiline .el-message-box__message { white-space: pre-line; }
</style>
