<script setup>
import { computed, onActivated, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { ElMessage, ElMessageBox, ElNotification, ElLoading } from 'element-plus'
import {
  Refresh,
  Delete,
  CopyDocument,
  VideoPlay,
  RefreshRight,
  Files,
  ArrowDown,
  Setting,
  Download,
} from '@element-plus/icons-vue'
import {
  listAccounts,
  deleteAccount,
  bulkDeleteAccounts,
  resetFailed,
  resetAccount,
  bulkResetAccounts,
  releaseStale,
  archiveFailed,
  unarchiveAccounts,
  cleanRegisteredFromPool,
  startMailboxValidation,
  stopMailboxValidation,
  mailboxValidateStreamUrl,
  exportPoolAccounts,
} from '@/api/accounts'
import { getMailProviders } from '@/api/settings'
import { copyText, createSSE } from '@/api/request'
import { useStatsStore } from '@/stores/stats'
import { useRuntimeStore } from '@/stores/runtime'
import { useProxyStore } from '@/stores/proxy'
import { useFormStore, COUNTRY_OPTIONS } from '@/stores/form'
import StatusDot from '@/components/StatusDot.vue'

const router = useRouter()
const statsStore = useStatsStore()
const runtime = useRuntimeStore()
const { dataVersion } = storeToRefs(runtime)
const { list: proxyList } = storeToRefs(useProxyStore())
const { form: regForm } = storeToRefs(useFormStore())

const PAGE_SIZE = 20
const rows = ref([])
const total = ref(0)
const page = ref(1)
const statusFilter = ref('')
const kindFilter = ref('')
const bulkStatus = ref('')
const selected = ref([])
const loading = ref(false)
const providers = ref([])
const byKind = ref({})

const STATUS_TYPE = { available: 'success', in_use: 'warning', done: 'primary', failed: 'danger', archived: 'info' }
// 状态列中文文案（archived 归档 = 只留存不再使用）
const STATUS_LABEL = { available: '可用', in_use: '运行中', done: '已完成', failed: '失败', archived: '已归档' }

const kindOptions = computed(() =>
  providers.value.filter((p) => p.pooled).map((p) => ({
    kind: p.kind,
    label: p.display_name,
    count: byKind.value[p.kind]?.total || 0,
  })),
)

function kindLabel(k) {
  return providers.value.find((p) => p.kind === k)?.display_name || k || 'outlook'
}

async function loadProviders() {
  try {
    providers.value = (await getMailProviders()).providers || []
  } catch (_) {}
}

async function load(resetPage) {
  if (resetPage) page.value = 1
  loading.value = true
  try {
    const { items, total: t, by_kind } = await listAccounts({
      status: statusFilter.value,
      kind: kindFilter.value,
      limit: PAGE_SIZE,
      offset: (page.value - 1) * PAGE_SIZE,
    })
    rows.value = items || []
    total.value = t || 0
    byKind.value = by_kind || {}
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

function afterMutate() {
  load()
  statsStore.refresh()
}

async function confirm(msg, title = '确认') {
  try {
    await ElMessageBox.confirm(msg, title, {
      type: 'warning',
      confirmButtonText: '确定',
      cancelButtonText: '取消',
    })
    return true
  } catch (_) {
    return false
  }
}

const failedCount = computed(() => statsStore.stats?.failed || 0)
const inUseCount = computed(() => statsStore.stats?.in_use || 0)
const archivedCount = computed(() => statsStore.stats?.archived || 0)
const resettingFailed = ref(false)
const releasingStale = ref(false)
const archivingFailed = ref(false)
const unarchiving = ref(false)

async function resetFailedAll() {
  const count = failedCount.value
  const countMsg = count > 0 ? `全部 ${count} 个` : '全部'
  const ok = await confirm(
    `确定将待注册号池中${countMsg}【失败 (failed)】邮箱一键重置为 available（可用）状态？\n\n重置后将自动清空原失败原因和时间戳，重新进入待注册与验活队列。`,
    '一键重置所有失败号',
  )
  if (!ok) return

  resettingFailed.value = true
  try {
    const r = await resetFailed()
    ElNotification({
      title: '重置成功',
      message: `已成功将 ${r.reset} 个失败邮箱重置为可用 (available) 状态！`,
      type: 'success',
      duration: 4000,
    })
    afterMutate()
  } catch (e) {
    ElNotification({
      title: '重置失败',
      message: e.message || '请求异常',
      type: 'error',
      duration: 4000,
    })
  } finally {
    resettingFailed.value = false
  }
}

async function releaseStaleAll() {
  releasingStale.value = true
  try {
    const r = await releaseStale()
    ElNotification({
      title: '释放完成',
      message: `已成功释放 ${r.released} 个卡在 in_use 状态的账号回 available 状态！`,
      type: 'info',
      duration: 4000,
    })
    afterMutate()
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    releasingStale.value = false
  }
}

async function archiveFailedAll() {
  const count = failedCount.value
  const countMsg = count > 0 ? `全部 ${count} 个` : '全部'
  const ok = await confirm(
    `确定将号池中${countMsg}【失败 (failed)】邮箱一键归档为「已归档 (archived)」状态？\n\n` +
    `归档 = 只留存、不再使用：这些号将退出注册与验活领取队列，但数据与失败原因全部保留，随时可查证。\n` +
    `如需重新启用，可在「重置操作」里一键取消归档（恢复为 failed）。`,
    '一键归档所有失败号',
  )
  if (!ok) return

  archivingFailed.value = true
  try {
    const r = await archiveFailed()
    ElNotification({
      title: '归档完成',
      message: `已成功将 ${r.archived} 个失败邮箱归档 (archived)，不再参与注册与验活。`,
      type: 'success',
      duration: 4000,
    })
    afterMutate()
  } catch (e) {
    ElNotification({
      title: '归档失败',
      message: e.message || '请求异常',
      type: 'error',
      duration: 4000,
    })
  } finally {
    archivingFailed.value = false
  }
}

async function unarchiveAll() {
  const count = archivedCount.value
  if (!count) {
    ElMessage.info('当前没有已归档 (archived) 的邮箱')
    return
  }
  const ok = await confirm(
    `确定将全部 ${count} 个【已归档 (archived)】邮箱退回归档、恢复为 failed 状态？\n\n失败原因原样保留，可再用「一键重置所有失败号」重新入队。`,
    '一键取消归档',
  )
  if (!ok) return

  unarchiving.value = true
  try {
    const r = await unarchiveAccounts()
    ElNotification({
      title: '取消归档完成',
      message: `已将 ${r.unarchived} 个归档邮箱恢复为 failed 状态。`,
      type: 'success',
      duration: 4000,
    })
    afterMutate()
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    unarchiving.value = false
  }
}

function handleResetCommand(cmd) {
  if (cmd === 'selected') {
    resetSelected()
  } else if (cmd === 'failed') {
    resetFailedAll()
  } else if (cmd === 'stale') {
    releaseStaleAll()
  } else if (cmd === 'unarchive') {
    unarchiveAll()
  }
}

// ════════════════ 邮箱号池一键导出 (标准 4 段 txt 格式) ════════════════
const exportVisible = ref(false)
const exportText = ref('')
const exportCount = ref(0)
const exportFilename = ref('')
const exportLabel = ref('')
const exporting = ref(false)

async function doExportPool(mode, emails = null, label = '', reasonLike = '') {
  exporting.value = true
  try {
    let payload = {}
    if (emails && emails.length) {
      payload.emails = emails
    } else if (mode === 'all') {
      payload.all = true
    } else if (mode === 'failed_70000') {
      payload.status = 'failed'
      payload.reason_like = 'AADSTS70000'
    } else if (mode) {
      payload.status = mode
    }
    if (reasonLike) {
      payload.reason_like = reasonLike
    }
    const res = await exportPoolAccounts(payload)
    if (!res.count) {
      ElMessage.warning('未找到符合条件的邮箱数据')
      return
    }
    exportText.value = res.text || ''
    exportCount.value = res.count || 0
    exportFilename.value = res.filename || `mailbox_${mode || 'all'}.txt`
    exportLabel.value = label || (mode === 'failed_70000' ? 'AADSTS70000 授权过期死号' : (mode === 'failed' ? '所有失败死号' : (mode === 'available' ? '所有可用邮箱' : '号池邮箱')))
    exportVisible.value = true
  } catch (e) {
    ElMessage.error('导出失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    exporting.value = false
  }
}

function downloadExportTxt() {
  if (!exportText.value) return
  const blob = new Blob([exportText.value], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = exportFilename.value || 'mailbox_accounts.txt'
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
  ElMessage.success(`已开始下载 ${exportFilename.value}`)
}

function handleExportCommand(cmd) {
  if (cmd === 'selected') {
    const emails = selected.value.map((r) => r.email)
    if (!emails.length) return
    doExportPool(null, emails, `选中的 ${emails.length} 个邮箱`)
  } else if (cmd === 'failed_70000') {
    doExportPool('failed_70000', null, 'AADSTS70000 授权过期/被吊销死号 (卡商退款售后专用)')
  } else if (cmd === 'failed') {
    doExportPool('failed', null, `全部失败/死号 (${failedCount.value} 个)`)
  } else if (cmd === 'available') {
    doExportPool('available', null, '全部可用邮箱 (available)')
  } else if (cmd === 'all') {
    doExportPool('all', null, '全量号池所有邮箱')
  }
}

async function exportDeadMailboxesFromVal() {
  // 优先直接按当前验活出的 failed_70000 或全部失败号导出
  const deadEmails = mbxValFailedRows.value.map((r) => r.email)
  if (deadEmails.length > 0) {
    await doExportPool(null, deadEmails, `验活发现的 ${deadEmails.length} 个失效死号 (含 AADSTS70000)`)
  } else {
    await doExportPool('failed_70000', null, '号池 AADSTS70000 授权过期死号')
  }
}

const cleaningRegistered = ref(false)

// ════════════════ 邮箱号池批量验活 (Mailbox Validation Studio) ════════════════
const mbxValVisible = ref(false)
const mbxValRunning = ref(false)
const mbxValTaskId = ref('')
const mbxValEs = ref(null)
const mbxValConfigCollapsed = ref(true)
const mbxValMode = ref('selected') // selected / available / failed / all
const mbxValTargetEmails = ref([])
const mbxValFailedRows = ref([])
const mbxValLogs = ref([])
const mbxValForm = reactive({
  action: 'mark_failed', // mark_failed (标记为失败) / delete (直接删除)
  workers: 20,
  proxyMode: '__AUTO_DYNAMIC__', // '__AUTO_DYNAMIC__' (动态住宅按账号自动换IP) / '__POOL__' (代理池轮换) / '__DIRECT__' (直连) / '__CUSTOM__' (自定义)
  customProxy: '',
  proxyCountry: '',
})
const mbxValStats = reactive({
  total: 0,
  done: 0,
  valid: 0,
  invalid: 0,
  percent: 0,
})

function updateMbxStats(data) {
  if (data && data.stats) {
    mbxValStats.total = data.stats.total || mbxValStats.total
    mbxValStats.valid = data.stats.valid || 0
    mbxValStats.invalid = data.stats.invalid || 0
    mbxValStats.done = data.done_count || (mbxValStats.valid + mbxValStats.invalid)
    mbxValStats.percent = mbxValStats.total ? Math.round((mbxValStats.done / mbxValStats.total) * 100) : 0
  }
}

function openMbxValidation(mode = 'selected') {
  mbxValMode.value = mode
  mbxValLogs.value = []
  mbxValFailedRows.value = []
  mbxValStats.total = 0
  mbxValStats.done = 0
  mbxValStats.valid = 0
  mbxValStats.invalid = 0
  mbxValStats.percent = 0

  if (mode === 'selected') {
    if (!selected.value.length) {
      ElMessage.warning('请先勾选要检测的邮箱')
      return
    }
    mbxValTargetEmails.value = selected.value.map((r) => r.email)
    mbxValStats.total = mbxValTargetEmails.value.length
  } else {
    mbxValTargetEmails.value = []
  }

  mbxValVisible.value = true
}

async function startMbxValTask() {
  if (mbxValRunning.value) return
  mbxValRunning.value = true
  mbxValLogs.value = []
  mbxValFailedRows.value = []

  let proxyParam = ''
  let proxyPoolParam = ''
  if (mbxValForm.proxyMode === '__AUTO_DYNAMIC__') {
    // 动态住宅代理模式：优先使用注册表单中配置的动态住宅代理，或者代理池中的节点，由后端自动派生新 session_id
    proxyParam = (regForm.value?.proxy || (proxyList.value && proxyList.value[0]) || '').trim()
    if (!proxyParam && proxyList.value?.length) {
      proxyPoolParam = proxyList.value.join('\n')
    }
  } else if (mbxValForm.proxyMode === '__POOL__') {
    proxyPoolParam = (proxyList.value || []).join('\n')
  } else if (mbxValForm.proxyMode === '__CUSTOM__') {
    proxyParam = (mbxValForm.customProxy || '').trim()
  }

  let payload = {
    action: mbxValForm.action,
    workers: mbxValForm.workers,
    proxy: proxyParam,
    proxy_pool: proxyPoolParam,
    proxy_country: mbxValForm.proxyCountry,
  }

  if (mbxValMode.value === 'selected') {
    payload.emails = mbxValTargetEmails.value
  } else if (mbxValMode.value === 'available') {
    payload.status_filter = 'available'
  } else if (mbxValMode.value === 'failed') {
    payload.status_filter = 'failed'
  }

  try {
    const { task_id } = await startMailboxValidation(payload)
    mbxValTaskId.value = task_id

    if (mbxValEs.value) {
      mbxValEs.value.close()
    }

    mbxValEs.value = createSSE(mailboxValidateStreamUrl(task_id), {
      failed_item: (ev) => {
        try {
          const data = JSON.parse(ev.data)
          if (data.email) {
            mbxValFailedRows.value.unshift({
              email: data.email,
              reason: data.reason,
              elapsed: data.elapsed,
            })
            if (mbxValFailedRows.value.length > 200) {
              mbxValFailedRows.value.pop()
            }
          }
          updateMbxStats(data)
        } catch (_) {}
      },
      progress: (ev) => {
        try {
          const data = JSON.parse(ev.data)
          updateMbxStats(data)
        } catch (_) {}
      },
      log: (ev) => {
        try {
          const data = JSON.parse(ev.data)
          if (data.line) {
            mbxValLogs.value.push(data.line)
            if (mbxValLogs.value.length > 200) mbxValLogs.value.splice(0, mbxValLogs.value.length - 200)
          }
        } catch (_) {}
      },
      end: () => {
        mbxValRunning.value = false
        if (mbxValEs.value) {
          mbxValEs.value.close()
          mbxValEs.value = null
        }
        ElNotification({
          title: '邮箱验活完成',
          message: `共检测 ${mbxValStats.total} 个，有效 ${mbxValStats.valid} 个，失效 ${mbxValStats.invalid} 个。`,
          type: mbxValStats.invalid > 0 ? 'warning' : 'success',
          duration: 5000,
        })
        afterMutate()
      },
    }, () => {
      mbxValRunning.value = false
    })
  } catch (e) {
    mbxValRunning.value = false
    ElMessage.error('启动验活失败: ' + e.message)
  }
}

async function stopMbxValTask() {
  if (!mbxValTaskId.value) {
    mbxValRunning.value = false
    return
  }
  try {
    await stopMailboxValidation(mbxValTaskId.value)
    ElMessage.info('已发送停止指令')
  } catch (_) {}
  finally {
    mbxValRunning.value = false
    afterMutate()
  }
}

function closeMbxValModal() {
  if (mbxValRunning.value) {
    ElMessage.info('验活任务在后台继续运行')
  }
  if (!mbxValRunning.value && mbxValEs.value) {
    mbxValEs.value.close()
    mbxValEs.value = null
  }
  mbxValVisible.value = false
}

async function cleanRegisteredAll() {
  const ok = await confirm(
    '系统将比对本地「已注册结果库」，自动从待注册号池中移除已成功注册的账号（并自动将 4 段邮箱取件凭证同步备份到已注册库，确保 2FA 与收信凭证不丢失）。\n\n确定开始全库对账清理？',
    '号池对账与清理已注册老号',
  )
  if (!ok) return

  cleaningRegistered.value = true
  const loadingInstance = ElLoading.service({
    lock: true,
    text: '正在比对号池与已注册库，执行深度对账清理...',
    background: 'rgba(0, 0, 0, 0.7)',
  })

  try {
    const r = await cleanRegisteredFromPool('delete')
    if (r.cleaned > 0) {
      ElNotification({
        title: '清理完成',
        message: `成功比对全库，已自动从待注册号池中移除 ${r.cleaned} 个已注册老号（邮箱凭证已同步备份）。`,
        type: 'success',
        duration: 5000,
      })
    } else {
      ElNotification({
        title: '号池非常纯净',
        message: '全库比对完成，当前待注册号池中没有与已注册库重叠的老号（重叠数：0）。',
        type: 'info',
        duration: 4000,
      })
    }
    afterMutate()
  } catch (e) {
    ElNotification({
      title: '清理失败',
      message: e.message || '请求失败，请检查后端服务是否正常',
      type: 'error',
      duration: 5000,
    })
  } finally {
    loadingInstance.close()
    cleaningRegistered.value = false
  }
}

async function resetSelected() {
  const emails = selected.value.map((r) => r.email)
  if (!emails.length) return
  if (!(await confirm(`确定重置选中的 ${emails.length} 个账号为 available 状态？`))) return
  try {
    const r = await bulkResetAccounts(emails)
    ElMessage.success(`已重置 ${r.reset} 个账号`)
    afterMutate()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

async function deleteSelected() {
  const emails = selected.value.map((r) => r.email)
  if (!emails.length) return
  if (!(await confirm(`确定删除选中的 ${emails.length} 个账号？(不可恢复)`))) return
  try {
    const r = await bulkDeleteAccounts({ emails })
    ElMessage.success(`已删除 ${r.deleted} 个账号`)
    afterMutate()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

async function bulkDeleteByStatus(status) {
  if (!status) return
  const tip = status === 'all'
    ? '这会清空号池里所有邮箱账号（含未注册的），确定？'
    : `确定删除号池中全部 ${status} 状态的账号？`
  if (!(await confirm(tip))) return
  try {
    const r = await bulkDeleteAccounts({ status })
    ElMessage.success(`已删除 ${r.deleted} 个 ${status} 账号`)
    afterMutate()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

function useAccount(email) {
  router.push({ path: '/register', query: { email } })
}

async function resetOne(email) {
  if (!(await confirm(`重置 ${email} 为 available？`))) return
  try {
    await resetAccount(email)
    ElMessage.success('已重置')
    afterMutate()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

async function deleteOne(email) {
  if (!(await confirm(`删除 ${email}？`))) return
  try {
    await deleteAccount(email)
    ElMessage.success('已删除')
    afterMutate()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

watch(page, () => load())
watch(dataVersion, () => load())
onActivated(() => load())
loadProviders()
</script>

<template>
  <div class="pool-page">
    <div class="macos-window-panel">
      <!-- 顶部 macOS 工具栏 -->
      <div class="macos-toolbar">
        <div class="toolbar-left">
          <div class="page-title-badge">
            <span class="dot-live"></span>
            <span class="title">邮箱号池</span>
            <span class="badge-total">{{ total }} 个</span>
          </div>

          <el-button class="macos-btn" @click="load(false)">
            <el-icon><Refresh /></el-icon>刷新
          </el-button>

          <el-select
            v-model="statusFilter"
            placeholder="状态筛选"
            class="macos-select filter-select"
            @change="load(true)"
          >
            <el-option label="全部状态" value="" />
            <el-option label="可用 (available)" value="available" />
            <el-option label="运行中 (in_use)" value="in_use" />
            <el-option label="已完成 (done)" value="done" />
            <el-option label="失败 (failed)" value="failed" />
            <el-option label="📦 已归档 (archived)" value="archived" />
          </el-select>

          <el-select
            v-if="kindOptions.length > 1"
            v-model="kindFilter"
            placeholder="全部来源"
            class="macos-select kind-select"
            @change="load(true)"
          >
            <el-option label="全部来源" value="" />
            <el-option
              v-for="o in kindOptions"
              :key="o.kind"
              :label="`${o.label} (${o.count})`"
              :value="o.kind"
            />
          </el-select>

          <div class="macos-btn-group">
            <el-button
              size="small"
              type="warning"
              plain
              class="macos-btn"
              :loading="resettingFailed"
              @click="resetFailedAll"
            >
              <el-icon><RefreshRight /></el-icon>一键重置所有失败号
              <span v-if="failedCount > 0" style="margin-left: 2px; font-weight: 700">({{ failedCount }})</span>
            </el-button>
            <el-button size="small" :loading="releasingStale" @click="releaseStaleAll">释放卡死号</el-button>
            <el-button
              size="small"
              type="info"
              plain
              class="macos-btn"
              :loading="archivingFailed"
              @click="archiveFailedAll"
            >
              📦 一键归档失败号
              <span v-if="failedCount > 0" style="margin-left: 2px; font-weight: 700">({{ failedCount }})</span>
            </el-button>
            <el-button size="small" type="warning" plain :loading="cleaningRegistered" @click="cleanRegisteredAll">清理已注册老号</el-button>
          </div>

          <el-dropdown trigger="click" @command="openMbxValidation">
            <el-button type="primary" size="small" class="macos-btn">
              🩺 邮箱验活
              <el-icon class="el-icon--right"><ArrowDown /></el-icon>
            </el-button>
            <template #dropdown>
              <el-dropdown-menu class="macos-dropdown-menu">
                <el-dropdown-item command="selected" :disabled="!selected.length">验活选中邮箱 ({{ selected.length }})</el-dropdown-item>
                <el-dropdown-item command="available">验活所有可用邮箱 (available)</el-dropdown-item>
                <el-dropdown-item command="failed">重验所有失败邮箱 (failed)</el-dropdown-item>
                <el-dropdown-item command="all" divided>验活全部号池</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>

        <div class="toolbar-right">
          <!-- 导出号池下拉菜单 -->
          <el-dropdown trigger="click" @command="handleExportCommand">
            <el-button
              size="small"
              class="macos-btn"
              :loading="exporting"
            >
              <el-icon><Download /></el-icon>导出号池
              <el-icon class="el-icon--right"><ArrowDown /></el-icon>
            </el-button>
            <template #dropdown>
              <el-dropdown-menu class="macos-dropdown-menu">
                <el-dropdown-item command="failed_70000" style="color: var(--el-color-danger); font-weight: 600">
                  🔥 一键导出 AADSTS70000 授权过期死号 (卡商售后退款专属 · 4段txt)
                </el-dropdown-item>
                <el-dropdown-item command="failed">
                  ❌ 导出所有失败/死号 ({{ failedCount }} 项 · 4段txt)
                </el-dropdown-item>
                <el-dropdown-item command="selected" :disabled="!selected.length">
                  📋 导出当前选中项 ({{ selected.length }} 项 · 4段txt)
                </el-dropdown-item>
                <el-dropdown-item command="available" divided>
                  ✅ 导出所有可用号 (available · 4段txt)
                </el-dropdown-item>
                <el-dropdown-item command="all">
                  📦 导出全量号池所有数据 (4段txt)
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>

          <el-dropdown trigger="click" @command="handleResetCommand">
            <el-button
              type="primary"
              plain
              size="small"
              class="macos-btn"
            >
              <el-icon><RefreshRight /></el-icon>重置操作
              <span v-if="selected.length" style="margin-left: 4px; font-weight: 700">({{ selected.length }})</span>
              <el-icon class="el-icon--right"><ArrowDown /></el-icon>
            </el-button>
            <template #dropdown>
              <el-dropdown-menu class="macos-dropdown-menu">
                <el-dropdown-item command="selected" :disabled="!selected.length">
                  重置当前选中 ({{ selected.length }} 项)
                </el-dropdown-item>
                <el-dropdown-item command="failed" divided>
                  🔄 一键重置全部失败号 (共 {{ failedCount }} 项)
                </el-dropdown-item>
                <el-dropdown-item command="stale">
                  ⏱️ 一键释放所有卡死号 (in_use ➔ available)
                </el-dropdown-item>
                <el-dropdown-item command="unarchive" :disabled="!archivedCount">
                  📤 一键取消归档 (共 {{ archivedCount }} 项 · archived ➔ failed)
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>

          <el-dropdown trigger="click" @command="bulkDeleteByStatus">
            <el-button size="small" type="danger" plain class="macos-btn">
              <el-icon><Delete /></el-icon>批量删除
              <el-icon class="el-icon--right"><ArrowDown /></el-icon>
            </el-button>
            <template #dropdown>
              <el-dropdown-menu class="macos-dropdown-menu">
                <el-dropdown-item command="failed">删除全部 failed 账号</el-dropdown-item>
                <el-dropdown-item command="done">删除全部 done 账号</el-dropdown-item>
                <el-dropdown-item command="available">删除全部 available 账号</el-dropdown-item>
                <el-dropdown-item command="in_use">删除全部 in_use 账号</el-dropdown-item>
                <el-dropdown-item divided command="all" style="color: var(--el-color-danger)">
                  ⚠️ 清空全部号池（不可逆）
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>

          <el-button
            v-if="selected.length"
            size="small"
            type="danger"
            @click="deleteSelected"
          >
            删除选中 ({{ selected.length }})
          </el-button>
        </div>
      </div>

      <!-- 中间主体表格区域 (100% 高度无滚动溢出) -->
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
          <el-table-column type="selection" width="42" align="center" />

          <el-table-column prop="email" label="邮箱地址" min-width="220" show-overflow-tooltip>
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

          <el-table-column v-if="kindOptions.length > 1" label="邮箱来源" width="130" align="center">
            <template #default="{ row }">
              <el-tag size="small" type="info" effect="plain" class="macos-tag">
                {{ kindLabel(row.kind) }}
              </el-tag>
            </template>
          </el-table-column>

          <el-table-column label="状态" width="120" align="center">
            <template #default="{ row }">
              <StatusDot :type="STATUS_TYPE[row.status] || 'info'" :text="STATUS_LABEL[row.status] || row.status" />
            </template>
          </el-table-column>

          <el-table-column prop="fail_reason" label="失败原因 / 备注" min-width="190" show-overflow-tooltip>
            <template #default="{ row }">
              <span v-if="row.fail_reason" class="hint" style="color: var(--el-color-danger)">
                {{ row.fail_reason }}
              </span>
              <span v-else class="hint">—</span>
            </template>
          </el-table-column>

          <el-table-column label="操作" width="180" fixed="right" align="center">
            <template #default="{ row }">
              <div class="row-actions">
                <el-button size="small" text type="primary" @click="useAccount(row.email)">
                  <el-icon><VideoPlay /></el-icon>使用
                </el-button>
                <el-button
                  v-if="row.status === 'done' || row.status === 'failed'"
                  size="small"
                  text
                  @click="resetOne(row.email)"
                >
                  重置
                </el-button>
                <el-button size="small" text type="danger" @click="deleteOne(row.email)">
                  删除
                </el-button>
              </div>
            </template>
          </el-table-column>

          <template #empty>
            <el-empty description="号池暂无数据，前往「导入邮箱」添加账号" :image-size="60" />
          </template>
        </el-table>
      </div>

      <!-- 底部固定 macOS 状态与分页栏 -->
      <div class="macos-footer-bar">
        <div class="footer-status-left">
          <span v-if="selected.length" class="selected-badge">已勾选 <b>{{ selected.length }}</b> 项</span>
          <span v-else class="total-badge">当前页共 {{ rows.length }} 条记录</span>
        </div>

        <div class="footer-pagination-right">
          <el-pagination
            v-model:current-page="page"
            :page-size="PAGE_SIZE"
            :total="total"
            layout="total, prev, pager, next, jumper"
            size="small"
            background
            class="macos-pagination"
          />
        </div>
      </div>
    </div>

    <!-- ──────────────── 邮箱快速验活控制台弹窗 (Linear & macOS 大气紧凑设计) ──────────────── -->
    <el-dialog
      v-model="mbxValVisible"
      width="880px"
      top="4vh"
      class="oa-custom-dialog plus-dialog mbx-val-dialog"
      :close-on-click-modal="false"
      @closed="closeMbxValModal"
    >
      <template #header>
        <div class="oa-header">
          <div class="window-dots">
            <span class="dot red"></span>
            <span class="dot yellow"></span>
            <span class="dot green"></span>
          </div>
          <div class="oa-header-title">
            <span class="oa-title-badge">MAILBOX</span>
            <span class="oa-title-text">邮箱号池快速验活与死号检测</span>
            <span class="oa-target-pill">
              {{ mbxValMode === 'selected' ? `${mbxValTargetEmails.length} 个选中邮箱` : (mbxValMode === 'available' ? '全部可用号' : (mbxValMode === 'failed' ? '全部失败号' : '全量号池')) }}
            </span>
          </div>
          <div class="oa-header-extra">
            <el-button size="small" class="config-toggle-btn" text @click="mbxValConfigCollapsed = !mbxValConfigCollapsed">
              <el-icon><Setting /></el-icon>{{ mbxValConfigCollapsed ? '展开检测配置' : '收起配置' }}
            </el-button>
          </div>
        </div>
      </template>

      <div class="oa-dialog-container">
        <!-- 检测参数配置卡片 -->
        <el-collapse-transition>
          <div v-show="!mbxValConfigCollapsed" class="oa-config-card">
            <el-form label-position="top" size="small" :disabled="mbxValRunning">
              <el-row :gutter="14">
                <el-col :xs="24" :sm="13" :md="13">
                  <el-form-item label="网络与动态代理通道 (建议开启，彻底避免单 IP 频率限制)">
                    <el-select v-model="mbxValForm.proxyMode" style="width: 100%" placeholder="选择验活网络模式">
                      <el-option value="__AUTO_DYNAMIC__" label="🎲 动态住宅轮换 (一号一IP，自动注入 Session ID)" />
                      <el-option value="__POOL__" :label="`📋 使用系统代理池 (${proxyList.length} 个节点)`" />
                      <el-option value="__DIRECT__" label="🌐 直连本机网络 (速度最快，无代理)" />
                      <el-option value="__CUSTOM__" label="⚙️ 指定自定义单代理 / 动态住宅 URL" />
                    </el-select>
                  </el-form-item>
                </el-col>
                <el-col :xs="12" :sm="6" :md="6">
                  <el-form-item label="出口国家 (动态住宅)">
                    <el-select v-model="mbxValForm.proxyCountry" style="width: 100%" placeholder="自动 / 默认">
                      <el-option
                        v-for="c in COUNTRY_OPTIONS"
                        :key="c.code"
                        :label="c.name"
                        :value="c.code"
                      />
                    </el-select>
                  </el-form-item>
                </el-col>
                <el-col :xs="12" :sm="5" :md="5">
                  <el-form-item label="并发 Worker (并发检测)">
                    <el-input-number v-model="mbxValForm.workers" :min="1" :max="50" style="width: 100%" />
                  </el-form-item>
                </el-col>

                <el-col v-if="mbxValForm.proxyMode === '__CUSTOM__'" :xs="24" :sm="24" :md="24">
                  <el-form-item label="自定义代理地址 (支持 socks5:// 或 http://)">
                    <el-input
                      v-model="mbxValForm.customProxy"
                      placeholder="socks5h://user:pass@host:port 或 http://host:port"
                    />
                  </el-form-item>
                </el-col>

                <el-col :xs="24" :sm="24" :md="24">
                  <el-form-item label="发现失效死号时的动作">
                    <el-radio-group v-model="mbxValForm.action" size="small" class="custom-radio-group">
                      <el-radio-button value="mark_failed">
                        ⚠️ 标记为 failed (记录报错原因，可在号池筛选导出)
                      </el-radio-button>
                      <el-radio-button value="delete">
                        🗑️ 直接从号池彻底删除
                      </el-radio-button>
                    </el-radio-group>
                  </el-form-item>
                </el-col>
              </el-row>
            </el-form>
          </div>
        </el-collapse-transition>

        <!-- KPI 统计看板 (大气卡片，高对比度) -->
        <div class="plus-kpi-grid mbx-kpi-grid">
          <div class="plus-kpi-card">
            <span class="kpi-label">已检测 / 总数</span>
            <div class="kpi-num-row">
              <span class="kpi-num">{{ mbxValStats.done }}</span>
              <span class="kpi-total">/ {{ mbxValStats.total || '—' }}</span>
            </div>
          </div>
          <div class="plus-kpi-card hit-active">
            <span class="kpi-label">✅ OAuth有效 / 正常可用</span>
            <span class="kpi-num text-success">{{ mbxValStats.valid }}</span>
          </div>
          <div class="plus-kpi-card card-danger" :class="{ 'has-dead': mbxValStats.invalid > 0 }">
            <span class="kpi-label">❌ 授权过期 / 失效死号</span>
            <span class="kpi-num text-danger">{{ mbxValStats.invalid }}</span>
          </div>
        </div>

        <!-- 进度条 -->
        <div class="oa-progress-wrap">
          <el-progress
            :percentage="mbxValStats.percent"
            :status="mbxValStats.percent === 100 ? (mbxValStats.invalid > 0 ? 'warning' : 'success') : ''"
            :stroke-width="5"
            :show-text="false"
          />
        </div>

        <!-- 失效死号清单实时表格 (仅展示死号，保障万级检测丝滑流畅) -->
        <div class="plus-table-wrap">
          <div class="mbx-sub-header">
            <div class="sub-title-wrap">
              <span class="sub-title">❌ 失效死号实时清单</span>
              <span v-if="mbxValStats.invalid > 0" class="dead-count-badge">{{ mbxValStats.invalid }} 个死号</span>
            </div>
            <span class="sub-tip">（仅实时展示死号与失败诊断，正常号不占列表 DOM，保持极速流畅）</span>
          </div>
          <el-table
            :data="mbxValFailedRows"
            size="small"
            stripe
            :height="mbxValConfigCollapsed ? '280px' : '180px'"
            class="macos-table mbx-table"
          >
            <el-table-column prop="email" label="失效邮箱账号" min-width="220" show-overflow-tooltip>
              <template #default="{ row }">
                <button
                  type="button"
                  class="macos-tag-btn copy-btn err-copy-btn"
                  title="点击复制邮箱"
                  @click="copyText(row.email)"
                >
                  <span class="mono">{{ row.email }}</span>
                  <el-icon class="copy-ico"><CopyDocument /></el-icon>
                </button>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="110" align="center">
              <template #default>
                <el-tag size="small" type="danger" effect="light" class="dead-tag">❌ 授权失效</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="reason" label="诊断原因 / 报错信息" min-width="260" show-overflow-tooltip>
              <template #default="{ row }">
                <span class="err-reason-text">{{ row.reason }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="elapsed" label="耗时" width="80" align="center">
              <template #default="{ row }">
                <span class="hint mono">{{ row.elapsed ? `${row.elapsed}s` : '—' }}</span>
              </template>
            </el-table-column>
            <template #empty>
              <div class="mbx-empty-state">
                <div class="empty-icon-wrap">🎉</div>
                <div class="empty-title">当前未发现任何失效死号</div>
                <div class="empty-desc">
                  {{ mbxValRunning ? `已快速扫过 ${mbxValStats.valid} 个正常邮箱，正在持续高并发检测中...` : '号池非常纯净可用' }}
                </div>
              </div>
            </template>
          </el-table>
        </div>

        <!-- 底部控制栏 -->
        <div class="oa-dialog-footer">
          <div class="footer-tip">
            <span v-if="mbxValRunning" class="running-indicator">
              <span class="pulse-dot"></span> 正在并发高速验活中 (Worker: {{ mbxValForm.workers }})...
            </span>
            <span v-else-if="mbxValStats.done > 0" class="finished-indicator">
              ✅ 已完成全部检测 (共 {{ mbxValStats.total }} 项)
            </span>
          </div>
          <div class="modal-footer-btns">
            <el-button
              v-if="mbxValStats.invalid > 0"
              size="small"
              type="warning"
              plain
              @click="exportDeadMailboxesFromVal"
            >
              <el-icon><Download /></el-icon> 导出失效死号 ({{ mbxValStats.invalid }} 项)
            </el-button>
            <el-button v-if="mbxValRunning" size="small" @click="closeMbxValModal">
              后台运行
            </el-button>
            <el-button v-if="mbxValRunning" size="small" type="danger" plain @click="stopMbxValTask">
              停止检测
            </el-button>
            <el-button
              v-else
              size="small"
              type="primary"
              class="start-gradient-btn"
              @click="startMbxValTask"
            >
              <el-icon><VideoPlay /></el-icon> 开始快速验活
            </el-button>
            <el-button size="small" @click="mbxValVisible = false">关闭</el-button>
          </div>
        </div>
      </div>
    </el-dialog>

    <!-- ──────────────── 邮箱号池一键导出弹窗 (标准 4 段 txt 格式) ──────────────── -->
    <el-dialog
      v-model="exportVisible"
      width="780px"
      top="8vh"
      class="macos-custom-dialog export-dialog"
      :close-on-click-modal="false"
    >
      <template #header>
        <div style="display: flex; align-items: center; justify-content: space-between; width: 100%; padding-right: 20px">
          <div style="display: flex; align-items: center; gap: 8px">
            <span style="font-weight: 700; font-size: 14px; color: var(--app-title)">📥 导出邮箱号池 · {{ exportLabel }}</span>
            <el-tag size="small" type="primary" effect="plain" class="mono">共 {{ exportCount }} 行</el-tag>
          </div>
          <span style="font-size: 11.5px; color: var(--app-text-secondary)">格式：邮箱----密码----ClientID----RefreshToken</span>
        </div>
      </template>

      <el-input
        :model-value="exportText"
        type="textarea"
        :rows="14"
        readonly
        class="mono export-area"
        placeholder="正在生成导出数据..."
      />

      <template #footer>
        <div style="display: flex; align-items: center; justify-content: space-between">
          <span style="font-size: 11.5px; color: var(--app-text-secondary)">
            标准 4 段格式，可直接用于售后退款/补号、备份或重新批量导入
          </span>
          <div style="display: flex; gap: 8px">
            <el-button size="small" @click="copyText(exportText, '已复制全部邮箱数据')">
              <el-icon><CopyDocument /></el-icon> 复制全部
            </el-button>
            <el-button size="small" type="primary" @click="downloadExportTxt">
              <el-icon><Download /></el-icon> 下载 {{ exportFilename }}
            </el-button>
            <el-button size="small" @click="exportVisible = false">关闭</el-button>
          </div>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.pool-page {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.macos-window-panel {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: var(--app-window-bg);
  border: 1px solid var(--app-border);
  border-radius: 12px;
  box-shadow: var(--app-shadow-sm);
  overflow: hidden;
}

.macos-toolbar {
  padding: 10px 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  background: var(--el-fill-color-blank);
  border-bottom: 1px solid var(--app-border);
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
  color: var(--app-title);
}
.page-title-badge .badge-total {
  font-size: 11px;
  color: var(--app-text-secondary);
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
  width: 135px;
}
.macos-select.kind-select {
  width: 160px;
}

.macos-btn-group {
  display: inline-flex;
  background: var(--el-fill-color-light);
  padding: 2px;
  border-radius: 6px;
  border: 1px solid var(--app-border);
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
  background: var(--app-card-bg);
  box-shadow: var(--app-shadow-sm);
}

.macos-table-container {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.macos-tag-btn.copy-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--app-border);
  padding: 1px 6px;
  border-radius: 4px;
  color: var(--app-title);
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
}
.copy-btn:hover .copy-ico { opacity: 1; }

.row-actions {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
}

.macos-footer-bar {
  padding: 8px 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-top: 1px solid var(--app-border);
  background: var(--el-fill-color-blank);
  flex-shrink: 0;
}
.footer-status-left {
  font-size: 12px;
  color: var(--app-text-secondary);
}
.selected-badge {
  color: var(--el-color-primary);
  font-weight: 500;
}

.mbx-sub-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
  padding: 0 4px;
}
.sub-title-wrap {
  display: flex;
  align-items: center;
  gap: 8px;
}
.mbx-sub-header .sub-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--el-color-danger);
}
.dead-count-badge {
  font-size: 11px;
  font-weight: 700;
  color: #fff;
  background: var(--el-color-danger);
  padding: 1px 7px;
  border-radius: 10px;
}
.mbx-sub-header .sub-tip {
  font-size: 11px;
  color: var(--app-text-secondary);
}

/* ════════════════════════ 验活弹窗高级现代设计 (Linear & macOS Dark/Light) ════════════════════════ */
:deep(.mbx-val-dialog) {
  border-radius: 14px;
  overflow: hidden;
  background: var(--app-window-bg);
  border: 1px solid var(--app-border);
  box-shadow: 0 20px 48px rgba(0, 0, 0, 0.45);
}

:deep(.mbx-val-dialog .el-dialog__header) {
  padding: 12px 18px;
  margin: 0;
  border-bottom: 1px solid var(--app-border);
  background: var(--el-fill-color-light);
}

:deep(.mbx-val-dialog .el-dialog__body) {
  padding: 14px 18px;
}

.oa-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.window-dots {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-right: 12px;
}
.dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
}
.dot.red { background: #ff5f56; }
.dot.yellow { background: #ffbd2e; }
.dot.green { background: #27c93f; }

.oa-header-title {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
}
.oa-title-badge {
  font-size: 10.5px;
  font-weight: 800;
  letter-spacing: 0.5px;
  color: #fff;
  background: linear-gradient(135deg, #007aff, #5856d6);
  padding: 2px 7px;
  border-radius: 5px;
}
.oa-title-text {
  font-size: 14px;
  font-weight: 700;
  color: var(--app-title);
}
.oa-target-pill {
  font-size: 11px;
  font-weight: 600;
  color: var(--app-text-secondary);
  background: var(--el-fill-color);
  border: 1px solid var(--app-border);
  padding: 2px 8px;
  border-radius: 12px;
}

.config-toggle-btn {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--app-title);
  background: var(--el-fill-color);
  border: 1px solid var(--app-border);
  border-radius: 6px;
  padding: 4px 8px;
}

.oa-dialog-container {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* 参数配置卡片 */
.oa-config-card {
  background: var(--el-fill-color-light);
  border: 1px solid var(--app-border);
  border-radius: 10px;
  padding: 12px 16px;
}
.oa-config-card :deep(.el-form-item) {
  margin-bottom: 8px;
}
.oa-config-card :deep(.el-form-item__label) {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--app-text-secondary);
  padding-bottom: 2px;
}

/* KPI 统计卡片网格 */
.plus-kpi-grid.mbx-kpi-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
}
.plus-kpi-card {
  background: var(--el-fill-color-light);
  border: 1px solid var(--app-border);
  border-radius: 8px;
  padding: 10px 14px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  transition: all 0.2s ease;
}
.plus-kpi-card .kpi-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--app-text-secondary);
}
.plus-kpi-card .kpi-num-row {
  display: flex;
  align-items: baseline;
  gap: 4px;
}
.plus-kpi-card .kpi-num {
  font-size: 19px;
  font-weight: 800;
  font-family: var(--font-mono, monospace);
  line-height: 1.1;
  color: var(--app-title);
}
.plus-kpi-card .kpi-total {
  font-size: 12px;
  font-weight: 500;
  color: var(--app-text-secondary);
  font-family: var(--font-mono, monospace);
}
.plus-kpi-card.hit-active {
  background: rgba(39, 201, 63, 0.08);
  border-color: rgba(39, 201, 63, 0.35);
}
.plus-kpi-card.card-danger.has-dead {
  background: rgba(255, 59, 48, 0.1);
  border-color: rgba(255, 59, 48, 0.4);
  box-shadow: 0 0 12px rgba(255, 59, 48, 0.15);
}
.text-success { color: var(--apple-green) !important; }
.text-danger { color: var(--apple-red) !important; }

/* 进度条 */
.oa-progress-wrap {
  margin: -2px 0 2px;
}
.oa-progress-wrap :deep(.el-progress-bar__outer) {
  background: var(--el-fill-color);
  border-radius: 4px;
}
.oa-progress-wrap :deep(.el-progress-bar__inner) {
  border-radius: 4px;
  background: linear-gradient(90deg, #007aff, #34c759);
}

/* 核心表格 */
.plus-table-wrap {
  background: var(--app-window-bg);
  border: 1px solid var(--app-border);
  border-radius: 8px;
  padding: 10px;
}
.mbx-table :deep(.el-table__inner-wrapper) {
  background: transparent;
}
.err-copy-btn {
  background: rgba(255, 59, 48, 0.08) !important;
  border-color: rgba(255, 59, 48, 0.25) !important;
  color: var(--apple-red) !important;
}
.dead-tag {
  font-weight: 700;
  border-radius: 4px;
}
.err-reason-text {
  color: var(--apple-red);
  font-size: 11.5px;
  font-weight: 500;
}

/* 空状态卡片 */
.mbx-empty-state {
  padding: 28px 0;
  text-align: center;
}
.empty-icon-wrap {
  font-size: 26px;
  margin-bottom: 6px;
}
.empty-title {
  font-size: 13.5px;
  font-weight: 700;
  color: var(--apple-green);
}
.empty-desc {
  font-size: 11.5px;
  color: var(--app-text-secondary);
  margin-top: 4px;
}

/* 底部状态与按钮 */
.oa-dialog-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 6px;
}
.running-indicator {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--el-color-primary);
}
.finished-indicator {
  font-size: 12px;
  font-weight: 600;
  color: var(--apple-green);
}
.pulse-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--el-color-primary);
  animation: pulse 1.2s infinite ease-in-out;
}
@keyframes pulse {
  0% { transform: scale(0.8); opacity: 0.4; }
  50% { transform: scale(1.3); opacity: 1; }
  100% { transform: scale(0.8); opacity: 0.4; }
}

.modal-footer-btns {
  display: flex;
  align-items: center;
  gap: 8px;
}
.start-gradient-btn {
  background: linear-gradient(135deg, #007aff, #5856d6) !important;
  border: none !important;
  color: #fff !important;
  font-weight: 700 !important;
  border-radius: 6px;
  box-shadow: 0 2px 8px rgba(0, 122, 255, 0.35);
}
</style>
