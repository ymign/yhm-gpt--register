<script setup>
import { computed, nextTick, onMounted, onUnmounted, onActivated, onDeactivated, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { ElMessage } from 'element-plus'
import {
  VideoPlay,
  VideoPause,
  RefreshRight,
  SwitchButton,
  QuestionFilled,
  CopyDocument,
  Document,
  Check,
  Close,
  Warning,
  Lightning,
  Setting,
  Refresh,
  ArrowUp,
  ArrowDown,
} from '@element-plus/icons-vue'
import {
  autoStart,
  autoPause,
  autoResume,
  autoStop,
  autoStatus as getAutoStatus,
  getRunLog,
  getPowSlots,
  savePowSlots,
} from '@/api/register'
import { copyText, fmtTime } from '@/api/request'
import { useFormStore, proxyText, COUNTRY_OPTIONS, formatCountry } from '@/stores/form'
import { useProxyStore } from '@/stores/proxy'
import { useRuntimeStore } from '@/stores/runtime'

const router = useRouter()
const { form } = storeToRefs(useFormStore())
const proxyStore = useProxyStore()
const { count: proxyCount } = storeToRefs(proxyStore)
const runtime = useRuntimeStore()
const { autoStatus } = storeToRefs(runtime)

// 状态机
const st = computed(() => autoStatus.value.state || 'stopped')
const canStart = computed(() => st.value === 'stopped')
const canPause = computed(() => st.value === 'running')
const canResume = computed(() => st.value === 'paused')
const canStop = computed(() => st.value !== 'stopped')

const stateLabel = computed(() => ({
  stopped: '未运行', running: '正在运行', paused: '已暂停',
}[st.value] || st.value))

const stateBadgeClass = computed(() => ({
  stopped: 'state-stopped', running: 'state-running', paused: 'state-paused',
}[st.value] || 'state-stopped'))

const successRate = computed(() => {
  const ok = autoStatus.value.registered_ok || 0
  const fail = autoStatus.value.registered_fail || 0
  const total = ok + fail
  if (total === 0) return '—'
  return `${Math.round((ok / total) * 100)}%`
})

// ──────────── 1. 智能风控预警与自动熔断退避监控 ────────────
const riskWarning = computed(() => {
  return autoStatus.value.risk_warning || {
    active: false,
    consecutive_409: 0,
    frozen_proxies: 0,
    backoff_seconds_left: 0,
    reason: '',
  }
})

// ──────────── 2. 实时出号速率 (CPM) ────────────
const velocity = computed(() => {
  return autoStatus.value.velocity || {
    cpm: 0,
    cpm_5m: 0,
    projected_hourly: 0,
    success_rate: 0,
    proxies_used: 0,
  }
})

// ──────────── 3. 参数配置收起与展开（记忆到 localStorage） ────────────
const configCollapsed = ref(localStorage.getItem('autoloop_cfg_collapsed') === 'true')

function toggleConfigCollapsed() {
  configCollapsed.value = !configCollapsed.value
  try {
    localStorage.setItem('autoloop_cfg_collapsed', String(configCollapsed.value))
  } catch (_) {}
}

const configSummary = computed(() => {
  const srcMap = {
    remail: '🍎 Remail 自动购号',
    cf_temp: '⚡ CF 临时邮箱',
    outlook: '📦 微软 Outlook',
    icloud_relay: '✉️ iCloud 邮箱',
  }
  const src = srcMap[form.value.autoMailSource] || '🍎 Remail'
  const conc = form.value.autoConcurrency || 1
  const ctry = form.value.autoProxyCountry ? formatCountry(form.value.autoProxyCountry) : '🌐 随机出口'
  const sec = []
  if (form.value.autoWantPassword) sec.push('🔑自动设密')
  if (form.value.autoWant2fa) sec.push('🛡️自动2FA')
  const secStr = sec.join(' + ') || '免密'
  return `${src} · ⚡ ${conc} Workers · 🧮 ${powSlots.value} 算力槽位 · ${ctry} · ${secStr}`
})

// ──────────── 4. 现代化全链路流水线五阶段定义 ────────────
const PIPELINE_STEPS = [
  { index: 1, key: 'sentinel', label: 'PoW', fullLabel: '⚡ PoW 0ms预算', icon: '⚡' },
  { index: 2, key: 'otp', label: '取码', fullLabel: '📨 微软取OTP', icon: '📨' },
  { index: 3, key: 'password', label: '设密', fullLabel: '🔐 官方设密', icon: '🔐' },
  { index: 4, key: '2fa', label: '2FA', fullLabel: '🛡️ 2FA激活', icon: '🛡️' },
  { index: 5, key: 'database', label: '入库', fullLabel: '💾 资产入库', icon: '💾' },
]

function getTaskStepIndex(row) {
  if (!row) return 1
  if (row.status === 'done') return 5
  const phase = row.phase || ''
  if (phase === 'sentinel' || phase === 'pow' || phase === 'auth_url' || phase === 'oauth_init' || phase === 'network' || phase === 'starting') return 1
  if (phase === 'otp_sent' || phase === 'otp_verify') return 2
  if (phase === 'register_pw' || phase === 'password' || phase === 'official_password') return 3
  if (phase === 'binding_2fa' || phase === '2fa_done') return 4
  if (phase === 'creating' || phase === 'done') return 5
  if (row.percent) {
    return Math.min(5, Math.max(1, Math.ceil(row.percent / 20)))
  }
  return 1
}

function getEmailIcon(email) {
  if (!email) return '✉️'
  if (email.includes('placeholder')) return '🍎'
  const lower = email.toLowerCase()
  if (lower.includes('outlook') || lower.includes('hotmail') || lower.includes('live')) return '📦'
  if (lower.includes('icloud')) return '🍎'
  if (lower.includes('gmail')) return '🇬'
  return '⚡'
}

function isPlaceholder(email) {
  return !email || email.includes('placeholder')
}

function getTaskCountry(row) {
  if (row.reg_country && row.reg_country.trim()) return row.reg_country.trim()
  if (row.target_country && row.target_country.trim()) return row.target_country.trim()
  if (form.value.autoProxyCountry && form.value.autoProxyCountry.trim()) return form.value.autoProxyCountry.trim()
  if (row.proxy) {
    const m = row.proxy.match(/[-_]([A-Za-z]{2})[-_]/) || row.proxy.match(/([a-zA-Z]{2})\.cliproxy/i)
    if (m && m[1] && m[1].length === 2) return m[1].toUpperCase()
  }
  return ''
}

// 账号任务流水列表
const taskList = computed(() => {
  return Array.isArray(autoStatus.value.tasks) ? autoStatus.value.tasks : []
})

// ──────────── 状态筛选与分页控制 ────────────
const filterStatus = ref('all') // 'all' | 'running' | 'done' | 'failed'
const currentPage = ref(1)
const pageSize = ref(30)

const runningCount = computed(() => taskList.value.filter((t) => t.status === 'running').length)
const doneCount = computed(() => taskList.value.filter((t) => t.status === 'done').length)
const failedCount = computed(() => taskList.value.filter((t) => t.status === 'failed').length)

const filteredTasks = computed(() => {
  if (filterStatus.value === 'running') {
    return taskList.value.filter((t) => t.status === 'running')
  }
  if (filterStatus.value === 'done') {
    return taskList.value.filter((t) => t.status === 'done')
  }
  if (filterStatus.value === 'failed') {
    return taskList.value.filter((t) => t.status === 'failed')
  }
  return taskList.value
})

const paginatedTasks = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return filteredTasks.value.slice(start, start + pageSize.value)
})

watch(filterStatus, () => {
  currentPage.value = 1
})

// ──────────── 本地实时走秒与状态同步 ────────────
const nowTs = ref(Math.floor(Date.now() / 1000))
let tickerTimer = null
let statusPollTimer = null

function formatClock(ts) {
  if (!ts) return '—'
  const d = new Date(typeof ts === 'number' ? (ts < 1e11 ? ts * 1000 : ts) : ts)
  if (isNaN(d.getTime())) return '—'
  const pad = (n) => String(n).padStart(2, '0')
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

function formatDuration(sec) {
  if (sec == null || sec <= 0) return '0秒'
  const s = Math.floor(sec)
  if (s < 60) return `${s}秒`
  const m = Math.floor(s / 60)
  const rem = s % 60
  if (m < 60) return `${m}分${rem}秒`
  const h = Math.floor(m / 60)
  const remM = m % 60
  return `${h}时${remM}分${rem}秒`
}

function maskProxy(p) {
  if (!p) return ''
  try {
    const s = String(p)
    if (s.includes('@')) {
      const parts = s.split('@')
      return `...${parts[1] || ''}`
    }
    return s.slice(0, 20)
  } catch (_) {
    return ''
  }
}

const batchStartedAt = computed(() => autoStatus.value.started_at || null)
const batchFinishedAt = computed(() => autoStatus.value.finished_at || null)

const batchElapsedSec = computed(() => {
  if (st.value === 'running' || st.value === 'paused') {
    if (batchStartedAt.value) {
      return Math.max(0, nowTs.value - batchStartedAt.value)
    }
  }
  if (autoStatus.value.elapsed) {
    return Math.round(autoStatus.value.elapsed)
  }
  if (batchFinishedAt.value && batchStartedAt.value) {
    return Math.max(0, Math.round(batchFinishedAt.value - batchStartedAt.value))
  }
  return 0
})

const batchAvgSpeed = computed(() => {
  const total = (autoStatus.value.registered_ok || 0) + (autoStatus.value.registered_fail || 0)
  if (total === 0 || batchElapsedSec.value <= 0) return '—'
  const avg = (batchElapsedSec.value / total).toFixed(1)
  return `${avg}s / 账号`
})

function formatElapsed(row) {
  if (!row) return '—'
  if (row.status === 'running') {
    if (!row.started_at) return '计时中'
    const secs = Math.max(0, Math.floor(nowTs.value - row.started_at))
    return `${secs}s`
  }
  if (row.elapsed !== undefined && row.elapsed !== null) {
    return `${row.elapsed}s`
  }
  return '—'
}

async function syncAutoStatus() {
  try {
    const res = await getAutoStatus()
    if (res && res.ok) {
      autoStatus.value = res
    }
  } catch (_) {}
}

// ──────────── 单账号独立日志弹窗 ────────────
const logModalVisible = ref(false)
const logModalLoading = ref(false)
const currentLogTask = ref(null)
const logLines = ref([])
let logPollTimer = null

async function openTaskLog(task) {
  currentLogTask.value = task
  logLines.value = []
  logModalVisible.value = true
  await fetchTaskLog(task.run_id)

  if (task.status === 'running') {
    startLogPolling(task.run_id)
  }
}

async function fetchTaskLog(runId) {
  if (!runId) return
  logModalLoading.value = true
  try {
    const res = await getRunLog(runId)
    logLines.value = res.lines || (res.text ? res.text.split('\n') : [])
    await nextTick()
    scrollLogModalToBottom()
  } catch (e) {
    logLines.value = ['读取日志失败: ' + (e.response?.data?.detail || e.message)]
  } finally {
    logModalLoading.value = false
  }
}

function startLogPolling(runId) {
  stopLogPolling()
  logPollTimer = setInterval(async () => {
    if (!logModalVisible.value || !currentLogTask.value) {
      stopLogPolling()
      return
    }
    try {
      const res = await getRunLog(runId)
      logLines.value = res.lines || (res.text ? res.text.split('\n') : [])
      await nextTick()
      scrollLogModalToBottom()
    } catch (_) {}

    const latest = taskList.value.find((t) => t.run_id === runId)
    if (latest && latest.status !== 'running') {
      currentLogTask.value = latest
      stopLogPolling()
    }
  }, 1500)
}

function stopLogPolling() {
  if (logPollTimer) {
    clearInterval(logPollTimer)
    logPollTimer = null
  }
}

function closeTaskLog() {
  stopLogPolling()
  logModalVisible.value = false
  currentLogTask.value = null
}

function scrollLogModalToBottom() {
  const el = document.getElementById('task-log-terminal')
  if (el) {
    el.scrollTop = el.scrollHeight
  }
}

function getLogLineClass(line) {
  if (!line) return ''
  const t = line.toLowerCase()
  if (t.includes('error') || t.includes('失败') || t.includes('fail') || t.includes('exception')) return 'line-err'
  if (t.includes('成功') || t.includes('完成') || t.includes('ok') || t.includes('2fa 绑定成功')) return 'line-ok'
  if (t.includes('warn') || t.includes('警告') || t.includes('timeout')) return 'line-warn'
  if (t.includes('[register]') || t.includes('phase=')) return 'line-info'
  return ''
}

// 控制动作
async function start() {
  try {
    await autoStart({
      mail_source: form.value.autoMailSource || form.value.mailSource || 'cf_temp',
      proxy: proxyText(form.value),
      proxy_pool: proxyStore.text,
      proxy_country: form.value.autoProxyCountry || '',
      concurrency: parseInt(form.value.autoConcurrency, 10) || 1,
      otp_timeout: parseInt(form.value.otpTimeout, 10) || 10,
      want_access_token: true,
      want_session_token: true,
      want_refresh_token: form.value.autoWantRefreshToken || false,
      cool_down_seconds: parseFloat(form.value.autoCoolDown) || 0,
      target_count: parseInt(form.value.autoTargetCount, 10) || 0,
      circuit_break_threshold: form.value.autoCircuitBreak !== undefined ? parseInt(form.value.autoCircuitBreak, 10) : 3,
      want_2fa: form.value.autoWant2fa,
      want_password: form.value.autoWantPassword,
    })
    ElMessage.success('🚀 AutoLoop 2.0 全自动跑号引擎已启动！')
    syncAutoStatus()
  } catch (e) {
    ElMessage.error('启动失败: ' + e.message)
  }
}

async function call(fn, name) {
  try {
    await fn()
    ElMessage.success(name + ' 成功')
    syncAutoStatus()
  } catch (e) {
    ElMessage.error(name + ' 失败: ' + e.message)
  }
}

// ──────────── PoW 算力槽位 ────────────
const powSlots = ref(6)
const powSlotsLoading = ref(false)

async function loadPowSlots() {
  try {
    const r = await getPowSlots()
    powSlots.value = r.slots || 6
  } catch (_) {}
}

async function onPowSlotsChange(val) {
  if (!val || powSlotsLoading.value) return
  powSlotsLoading.value = true
  try {
    const r = await savePowSlots(val)
    powSlots.value = r.slots
    ElMessage.success(`PoW 算力槽位已设为 ${r.slots}（已保存）`)
  } catch (e) {
    ElMessage.error('PoW 槽位保存失败: ' + e.message)
    await loadPowSlots()
  } finally {
    powSlotsLoading.value = false
  }
}

watch(
  () => form.value.autoMailSource,
  (src) => {
    if (src === 'remail') {
      form.value.autoWantPassword = true
      form.value.autoWant2fa = true
    }
  },
  { immediate: true },
)

function startTimers() {
  stopTimers()
  tickerTimer = setInterval(() => {
    nowTs.value = Math.floor(Date.now() / 1000)
  }, 1000)

  statusPollTimer = setInterval(() => {
    if (st.value === 'running' || st.value === 'paused') {
      syncAutoStatus()
    }
  }, 2500)
}

function stopTimers() {
  if (tickerTimer) {
    clearInterval(tickerTimer)
    tickerTimer = null
  }
  if (statusPollTimer) {
    clearInterval(statusPollTimer)
    statusPollTimer = null
  }
}

onMounted(() => {
  syncAutoStatus()
  loadPowSlots()
})

onActivated(() => {
  startTimers()
  syncAutoStatus()
  loadPowSlots()
})

onDeactivated(() => {
  stopTimers()
  stopLogPolling()
})

onUnmounted(() => {
  stopTimers()
  stopLogPolling()
})
</script>

<template>
  <div class="autoloop-page">
    <!-- ════════════ 1. 智能风控预警与自动熔断退避提示横幅 ════════════ -->
    <el-collapse-transition>
      <div v-if="riskWarning.active || st === 'paused'" class="risk-defense-banner">
        <div class="risk-banner-left">
          <div class="risk-icon-pulse">
            <el-icon><Warning /></el-icon>
          </div>
          <div class="risk-info">
            <div class="risk-title-row">
              <span class="risk-title">🛡️ 智能风控自适应防御已触发</span>
              <el-tag size="small" type="danger" effect="dark" class="risk-tag">
                {{ riskWarning.consecutive_409 > 0 ? `连续 ${riskWarning.consecutive_409} 次 IP 频控` : '自适应降速保护' }}
              </el-tag>
            </div>
            <div class="risk-desc">
              系统检测到出口 IP 频控 (409 Conflict) 或 Cloudflare 质询拦截，已自动将 <b>{{ riskWarning.frozen_proxies }}</b> 个异常代理隔离进 15 分钟冷冻期，防止浪费号源与代理积分。
            </div>
          </div>
        </div>
        <div class="risk-banner-right">
          <div v-if="riskWarning.backoff_seconds_left > 0" class="cooldown-pill">
            <span class="cooldown-dot"></span>
            <span>退避冷却: <strong>{{ riskWarning.backoff_seconds_left }}s</strong></span>
          </div>
          <el-button size="small" type="warning" plain @click="router.push('/proxy')">
            查看代理池
          </el-button>
          <el-button v-if="canResume" size="small" type="primary" @click="call(autoResume, '恢复')">
            立即恢复运行
          </el-button>
        </div>
      </div>
    </el-collapse-transition>

    <!-- ════════════ 2. 顶部精炼 KPI 指标大屏 ════════════ -->
    <div class="kpi-grid">
      <!-- 运行状态卡片 -->
      <div class="kpi-card" :class="stateBadgeClass">
        <div class="kpi-icon-dot">
          <span class="live-pulse"></span>
        </div>
        <div class="kpi-info">
          <span class="kpi-title">运行状态</span>
          <span class="kpi-num status-text">{{ stateLabel }}</span>
        </div>
      </div>

      <!-- 成功出号 -->
      <div class="kpi-card hit-card">
        <div class="kpi-info">
          <span class="kpi-title">成功出号</span>
          <div class="kpi-num-row">
            <span class="kpi-num text-success">{{ autoStatus.registered_ok || 0 }}</span>
            <span v-if="autoStatus.target_count" class="kpi-sub">/ 目标 {{ autoStatus.target_count }}</span>
          </div>
        </div>
      </div>

      <!-- 注册失败 -->
      <div class="kpi-card err-card">
        <div class="kpi-info">
          <span class="kpi-title">注册失败</span>
          <span class="kpi-num text-danger">{{ autoStatus.registered_fail || 0 }}</span>
        </div>
      </div>

      <!-- 出号速率 CPM -->
      <div class="kpi-card">
        <div class="kpi-info">
          <div class="kpi-title-row">
            <span class="kpi-title">实时出号速率</span>
            <el-icon class="cpm-icon"><Lightning /></el-icon>
          </div>
          <div class="kpi-num-row">
            <span class="kpi-num text-emerald">{{ velocity.cpm }}</span>
            <span class="kpi-sub">个/分 (时产 ~{{ velocity.projected_hourly }})</span>
          </div>
        </div>
      </div>

      <!-- 成功率 -->
      <div class="kpi-card">
        <div class="kpi-info">
          <span class="kpi-title">出号成功率</span>
          <span class="kpi-num">{{ successRate }}</span>
        </div>
      </div>

      <!-- 批次耗时 -->
      <div class="kpi-card timing-card" :class="{ 'timing-card-running': st === 'running' }">
        <div class="kpi-info">
          <div class="timing-kpi-header">
            <span class="kpi-title">批次耗时</span>
            <span v-if="st === 'running'" class="pulse-dot-live"></span>
          </div>
          <div class="kpi-num-row">
            <span class="kpi-num" :class="{ 'text-primary': st === 'running', 'text-success': st === 'stopped' && (autoStatus.registered_ok || 0) > 0 }">
              {{ formatDuration(batchElapsedSec) }}
            </span>
          </div>
          <div class="timing-sub-row">
            <span class="timing-sub-time">🕒 {{ formatClock(batchStartedAt) }}</span>
            <span class="timing-sub-arrow">→</span>
            <span class="timing-sub-time">
              <span v-if="st === 'running'" class="text-running-sub">🟢 运行中</span>
              <span v-else>🏁 {{ formatClock(batchFinishedAt) }}</span>
            </span>
          </div>
        </div>
      </div>
    </div>

    <!-- ════════════ 3. 全自动批量参数调度卡片 (一键折叠/展开，极简优雅) ════════════ -->
    <div class="macos-panel config-panel">
      <div class="panel-header" @click="toggleConfigCollapsed">
        <div class="panel-header-left">
          <span class="macos-pill-tag">CONFIG</span>
          <span class="title">全自动批量参数调度</span>

          <!-- 折叠时的精简摘要胶囊 -->
          <div v-if="configCollapsed" class="config-summary-chip">
            <span class="summary-dot"></span>
            <span class="summary-text">{{ configSummary }}</span>
          </div>

          <!-- 展开时的时序胶囊 -->
          <div v-else-if="batchStartedAt" class="header-timing-pill" :class="{ 'header-timing-running': st === 'running' }">
            <span class="pill-dot" :class="{ 'pulse': st === 'running' }"></span>
            <span>🕒 开始: {{ formatClock(batchStartedAt) }}</span>
            <span class="pill-sep">|</span>
            <span v-if="st === 'running'">⏱️ 耗时: <strong>{{ formatDuration(batchElapsedSec) }}</strong></span>
            <span v-else>🏁 结束: {{ formatClock(batchFinishedAt) }} (总耗时 {{ formatDuration(batchElapsedSec) }})</span>
            <span v-if="batchAvgSpeed !== '—'" class="pill-sep">|</span>
            <span v-if="batchAvgSpeed !== '—'">⚡ 均速: {{ batchAvgSpeed }}</span>
          </div>
        </div>

        <div class="control-actions" @click.stop>
          <el-button
            type="primary" class="start-btn" :disabled="!canStart"
            @click="start"
          >
            <el-icon><VideoPlay /></el-icon>开始自动运行
          </el-button>
          <div class="action-btn-group">
            <el-button size="small" :disabled="!canPause" @click="call(autoPause, '暂停')">
              <el-icon><VideoPause /></el-icon>暂停
            </el-button>
            <el-button size="small" :disabled="!canResume" @click="call(autoResume, '恢复')">
              <el-icon><RefreshRight /></el-icon>恢复
            </el-button>
            <el-button size="small" type="danger" plain :disabled="!canStop" @click="call(autoStop, '停止')">
              <el-icon><SwitchButton /></el-icon>停止任务
            </el-button>
          </div>

          <button
            type="button"
            class="config-toggle-btn"
            :title="configCollapsed ? '点击展开参数配置' : '点击收起参数配置'"
            @click="toggleConfigCollapsed"
          >
            <el-icon :class="{ 'is-rotated': !configCollapsed }"><ArrowDown /></el-icon>
            <span>{{ configCollapsed ? '展开参数配置' : '收起配置' }}</span>
          </button>
        </div>
      </div>

      <el-collapse-transition>
        <div v-show="!configCollapsed" class="panel-body">
          <el-form size="small" label-position="top">
            <!-- 邮箱渠道选择 -->
            <el-row :gutter="12" class="config-row-source">
              <el-col :span="24">
                <el-form-item label="接码邮箱渠道 (选择并发注册使用的邮箱来源)">
                  <div class="mail-source-selector-row">
                    <el-radio-group v-model="form.autoMailSource" class="macos-radio-group">
                      <el-radio-button value="remail">🍎 Remail 自动购号</el-radio-button>
                      <el-radio-button value="cf_temp">⚡ CF 临时邮箱 (动态造号)</el-radio-button>
                      <el-radio-button value="outlook">📦 微软 Outlook (号池)</el-radio-button>
                      <el-radio-button value="icloud_relay">✉️ iCloud 邮箱 (中转)</el-radio-button>
                    </el-radio-group>
                    <span class="mail-source-badge-tip">
                      <span v-if="form.autoMailSource === 'remail'" class="text-remail" style="color: #10b981">🍎 Remail 自动购号：每次并发注册按需购买全新邮箱，支持微软/iCloud等多后缀</span>
                      <span v-else-if="form.autoMailSource === 'cf_temp'" class="text-cf">⚡ 无需号池：Worker 动态无限生成地址并发注册，推荐</span>
                      <span v-else-if="form.autoMailSource === 'outlook'" class="text-outlook">📦 微软号池并发：自动从号池领取可用账号，池空自动等待</span>
                      <span v-else-if="form.autoMailSource === 'icloud_relay'" class="text-ic">✉️ iCloud 号池并发：自动从号池领取带中转链接的账号</span>
                    </span>
                  </div>
                </el-form-item>
              </el-col>
            </el-row>

            <el-row :gutter="12" class="config-row">
              <el-col :xs="12" :sm="6" :md="3">
                <el-form-item label="并发数 (Workers)">
                  <el-input-number v-model="form.autoConcurrency" :min="1" :max="50" class="macos-num-input" />
                </el-form-item>
              </el-col>
              <el-col :xs="12" :sm="6" :md="3">
                <el-form-item>
                  <template #label>
                    <span>PoW 算力槽位</span>
                    <el-tooltip content="同时解算 sentinel PoW 的 node 进程数上限。网络并发再高，PoW 碰撞也会在这里排队，保护 CPU 不被打满降频。" placement="top">
                      <el-icon class="info-ico" style="margin-left: 3px;"><QuestionFilled /></el-icon>
                    </el-tooltip>
                  </template>
                  <el-input-number
                    v-model="powSlots"
                    :min="1"
                    :max="16"
                    class="macos-num-input"
                    :loading="powSlotsLoading"
                    @change="onPowSlotsChange"
                  />
                </el-form-item>
              </el-col>
              <el-col :xs="12" :sm="6" :md="2">
                <el-form-item label="冷却 (秒)">
                  <el-input-number v-model="form.autoCoolDown" :min="0" :max="120" class="macos-num-input" />
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="12" :md="5">
                <el-form-item label="代理目标国家 (自动重写时区)">
                  <el-select
                    v-model="form.autoProxyCountry" filterable allow-create
                    placeholder="选择或输入国家代码" class="macos-country-select"
                  >
                    <el-option
                      v-for="c in COUNTRY_OPTIONS" :key="c.value"
                      :label="c.label" :value="c.value"
                    />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :xs="12" :sm="6" :md="3">
                <el-form-item label="目标数量 (0=不限)">
                  <el-input-number v-model="form.autoTargetCount" :min="0" :max="100000" class="macos-num-input" />
                </el-form-item>
              </el-col>
              <el-col :xs="12" :sm="6" :md="3">
                <el-form-item>
                  <template #label>
                    <span>失败暂停 (次)</span>
                    <el-tooltip content="连续网络/环境错误达到该次数时自动暂停保护（填 0 代表关闭自动暂停，抗网络波动持续重试）" placement="top">
                      <el-icon class="info-ico" style="margin-left: 3px;"><QuestionFilled /></el-icon>
                    </el-tooltip>
                  </template>
                  <el-input-number v-model="form.autoCircuitBreak" :min="0" :max="100" class="macos-num-input" placeholder="0=关闭" />
                </el-form-item>
              </el-col>
              <el-col :xs="12" :sm="6" :md="2">
                <el-form-item label="OTP 超时">
                  <el-input-number v-model="form.otpTimeout" :min="10" :max="600" class="macos-num-input" />
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="12" :md="6">
                <el-form-item label="自动化附加功能">
                  <div class="feature-switches" :class="{ 'remail-active-features': form.autoMailSource === 'remail' }">
                    <div class="switch-item">
                      <el-switch v-model="form.autoWantPassword" size="small" />
                      <span class="switch-label">自动设密</span>
                    </div>
                    <div class="switch-item">
                      <el-switch v-model="form.autoWant2fa" size="small" />
                      <span class="switch-label">自动绑2FA</span>
                    </div>
                  </div>
                </el-form-item>
              </el-col>
            </el-row>
          </el-form>
        </div>
      </el-collapse-transition>
    </div>

    <!-- ════════════ 4. 核心主区域：现代化极客流水监控表格列表 ════════════ -->
    <div class="macos-panel table-panel">
      <div class="table-panel-header">
        <div class="header-left">
          <div class="panel-header-title">
            <span class="dot-live"></span>
            <span class="title">账号注册实时流水监控列表</span>
            <span class="badge-total">{{ taskList.length }} 个任务</span>
          </div>

          <!-- 状态筛选胶囊 -->
          <div class="filter-capsules">
            <button
              class="filter-pill"
              :class="{ active: filterStatus === 'all' }"
              @click="filterStatus = 'all'"
            >
              全部 <span class="pill-cnt">{{ taskList.length }}</span>
            </button>
            <button
              class="filter-pill pill-running"
              :class="{ active: filterStatus === 'running' }"
              @click="filterStatus = 'running'"
            >
              <span class="dot-pill dot-running"></span>
              进行中 <span class="pill-cnt">{{ runningCount }}</span>
            </button>
            <button
              class="filter-pill pill-done"
              :class="{ active: filterStatus === 'done' }"
              @click="filterStatus = 'done'"
            >
              <span class="dot-pill dot-done"></span>
              成功 <span class="pill-cnt">{{ doneCount }}</span>
            </button>
            <button
              class="filter-pill pill-failed"
              :class="{ active: filterStatus === 'failed' }"
              @click="filterStatus = 'failed'"
            >
              <span class="dot-pill dot-failed"></span>
              失败 <span class="pill-cnt">{{ failedCount }}</span>
            </button>
          </div>
        </div>

        <div v-if="autoStatus.last_message" class="last-msg-hint">
          {{ autoStatus.last_message }}
        </div>
      </div>

      <div class="table-container">
        <el-table
          :data="paginatedTasks"
          row-key="run_id"
          height="100%"
          size="small"
          stripe
          class="modern-stepper-table"
          :highlight-current-row="false"
        >
          <!-- 账号邮箱 -->
          <el-table-column prop="email" label="账号邮箱" min-width="230" show-overflow-tooltip>
            <template #default="{ row }">
              <div class="email-modern-cell">
                <span class="email-brand-icon">{{ getEmailIcon(row.email) }}</span>
                <span
                  v-if="isPlaceholder(row.email)"
                  class="placeholder-shimmer-tag"
                >
                  <span class="shimmer-pulse"></span>
                  <span>Remail 自动购号中...</span>
                </span>
                <span v-else class="email-text-mono" :title="row.email">
                  {{ row.email }}
                </span>
                <button
                  v-if="!isPlaceholder(row.email)"
                  type="button"
                  class="modern-copy-btn"
                  title="点击复制邮箱"
                  @click.stop="copyText(row.email, '邮箱已复制')"
                >
                  <el-icon><CopyDocument /></el-icon>
                </button>
              </div>
            </template>
          </el-table-column>

          <!-- 执行 Worker & 出口国家 -->
          <el-table-column label="执行 Worker / 出口" width="160" align="center" show-overflow-tooltip>
            <template #default="{ row }">
              <div class="worker-meta-cell">
                <span class="worker-pill-badge" :class="{ 'is-active': row.status === 'running' }">
                  <span class="worker-pulse-dot" :class="{ 'live': row.status === 'running' }"></span>
                  <span>Worker #{{ row.worker_id !== undefined ? row.worker_id + 1 : 1 }}</span>
                </span>
                <el-tooltip v-if="getTaskCountry(row)" :content="`出口节点: ${row.proxy || '默认代理'}`" placement="top">
                  <span
                    class="geo-flag-pill"
                    :class="{ 'geo-hot': ['JP', 'BR', 'VN', 'DE', 'GB', 'PL', 'ES', 'AR', 'TH'].includes(getTaskCountry(row)?.toUpperCase()) }"
                  >
                    {{ formatCountry(getTaskCountry(row)) }}
                  </span>
                </el-tooltip>
                <span v-else class="geo-flag-pill geo-default">🌐 跟随代理</span>
              </div>
            </template>
          </el-table-column>

          <!-- 注册全链路五阶段微流水线 (Connected Stepper Pipeline) -->
          <el-table-column label="注册阶段与全链路流水线" min-width="360">
            <template #default="{ row }">
              <div class="stepper-pipeline-container">
                <!-- 五阶段连线 Stepper 节点条 -->
                <div class="stepper-track-row">
                  <template v-for="(stItem, sIdx) in PIPELINE_STEPS" :key="stItem.index">
                    <!-- 步骤节点 -->
                    <div
                      class="stepper-node"
                      :class="{
                        'is-done': row.status === 'done' || getTaskStepIndex(row) > stItem.index,
                        'is-active': row.status === 'running' && getTaskStepIndex(row) === stItem.index,
                        'is-failed': row.status === 'failed' && getTaskStepIndex(row) === stItem.index,
                        'is-pending': row.status !== 'done' && getTaskStepIndex(row) < stItem.index,
                      }"
                      :title="stItem.fullLabel"
                    >
                      <span class="stepper-node-dot">
                        <span v-if="row.status === 'done' || getTaskStepIndex(row) > stItem.index" class="node-check">✓</span>
                        <span v-else-if="row.status === 'running' && getTaskStepIndex(row) === stItem.index" class="node-pulse"></span>
                        <span v-else-if="row.status === 'failed' && getTaskStepIndex(row) === stItem.index" class="node-err">✕</span>
                        <span v-else class="node-num">{{ stItem.index }}</span>
                      </span>
                      <span class="stepper-node-label">{{ stItem.label }}</span>
                    </div>

                    <!-- 连接线 -->
                    <div
                      v-if="sIdx < PIPELINE_STEPS.length - 1"
                      class="stepper-connector"
                      :class="{
                        'is-done': row.status === 'done' || getTaskStepIndex(row) > stItem.index + 1,
                        'is-active': row.status === 'running' && getTaskStepIndex(row) > stItem.index,
                      }"
                    ></div>
                  </template>
                </div>

                <!-- 步骤描述与微进度条 -->
                <div class="stepper-meta-row">
                  <div v-if="row.status === 'running'" class="running-status-box">
                    <span class="pulse-beacon"></span>
                    <span class="status-msg-running">{{ row.phase_text || '正在处理中...' }}</span>
                    <span v-if="row.percent" class="status-pct mono">{{ row.percent }}%</span>
                  </div>
                  <div v-else-if="row.status === 'done'" class="done-status-box">
                    <span class="done-tag">🎉 注册完成并成功入库 (100%)</span>
                  </div>
                  <div v-else-if="row.status === 'failed'" class="failed-status-box" :title="row.error">
                    <span class="fail-tag">❌ {{ row.error || row.phase_text || '注册失败' }}</span>
                  </div>
                  <div v-else class="pending-status-box">
                    <span class="pending-tag">⏳ 等待 Worker 领取</span>
                  </div>
                </div>
              </div>
            </template>
          </el-table-column>

          <!-- 单号耗时 -->
          <el-table-column label="单号耗时" width="95" align="right">
            <template #default="{ row }">
              <span
                class="mono duration-cell"
                :class="{
                  'duration-running': row.status === 'running',
                  'duration-done': row.status === 'done',
                  'duration-fail': row.status === 'failed',
                }"
              >
                {{ formatElapsed(row) }}
              </span>
            </template>
          </el-table-column>

          <!-- 启动时间 -->
          <el-table-column label="启动时间" width="105" align="center">
            <template #default="{ row }">
              <span class="mono time-cell">{{ formatClock(row.started_at) }}</span>
            </template>
          </el-table-column>

          <!-- 操作 -->
          <el-table-column label="操作" width="75" fixed="right" align="center">
            <template #default="{ row }">
              <button
                type="button"
                class="modern-log-btn"
                title="查看该账号注册终端日志"
                @click="openTaskLog(row)"
              >
                <el-icon><Document /></el-icon>日志
              </button>
            </template>
          </el-table-column>

          <template #empty>
            <el-empty description="点击上方「开始自动运行」启动全自动注册任务" :image-size="60" />
          </template>
        </el-table>
      </div>

      <!-- 紧凑底部分页栏 -->
      <div v-if="filteredTasks.length > 0" class="table-pagination-bar">
        <div class="page-tip">
          显示第 {{ (currentPage - 1) * pageSize + 1 }} ~ {{ Math.min(currentPage * pageSize, filteredTasks.length) }} 条，共 {{ filteredTasks.length }} 条
        </div>
        <el-pagination
          v-model:current-page="currentPage"
          v-model:page-size="pageSize"
          :page-sizes="[20, 30, 50, 100]"
          :total="filteredTasks.length"
          layout="sizes, prev, pager, next"
          size="small"
          background
        />
      </div>
    </div>

    <!-- ──────────────── 单账号详细注册日志弹窗 (macOS Terminal) ──────────────── -->
    <el-dialog
      v-model="logModalVisible"
      width="820px"
      top="6vh"
      class="macos-terminal-dialog"
      :close-on-click-modal="false"
      @closed="closeTaskLog"
    >
      <template #header>
        <div class="modal-header">
          <div class="window-dots">
            <span class="dot red"></span>
            <span class="dot yellow"></span>
            <span class="dot green"></span>
          </div>
          <div class="modal-title-info">
            <span class="modal-email">{{ currentLogTask?.email }}</span>
            <el-tag size="small" type="info" effect="plain" class="modal-run-tag">
              run: {{ currentLogTask?.run_id }}
            </el-tag>
            <span v-if="currentLogTask?.status === 'running'" class="running-pill">
              <span class="pulse-dot"></span> 实时追踪中
            </span>
          </div>
        </div>
      </template>

      <div class="modal-terminal-wrap">
        <div id="task-log-terminal" class="modal-terminal-body">
          <div
            v-for="(line, idx) in logLines"
            :key="idx"
            class="terminal-line"
            :class="getLogLineClass(line)"
          >
            {{ line }}
          </div>
          <div v-if="!logLines.length" class="terminal-empty">
            {{ logModalLoading ? '正在加载日志...' : '暂无日志输出' }}
          </div>
        </div>
      </div>

      <template #footer>
        <div class="modal-footer">
          <span class="log-count-tip">共 {{ logLines.length }} 行日志</span>
          <div class="modal-footer-btns">
            <el-button size="small" @click="copyText(logLines.join('\n'))">
              <el-icon><CopyDocument /></el-icon>复制全部日志
            </el-button>
            <el-button size="small" type="primary" @click="closeTaskLog">
              关闭
            </el-button>
          </div>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
/* ──────────── 整体单屏自适应布局（绝无外层滚动条） ──────────── */
.autoloop-page {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  overflow: hidden;
}

/* ──────────── 1. 智能风控预警与自动熔断退避横幅 ──────────── */
.risk-defense-banner {
  background: linear-gradient(135deg, rgba(239, 68, 68, 0.12) 0%, rgba(245, 158, 11, 0.12) 100%);
  border: 1px solid rgba(239, 68, 68, 0.35);
  border-radius: 10px;
  padding: 8px 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 10px;
  box-shadow: 0 4px 16px rgba(239, 68, 68, 0.08);
  flex-shrink: 0;
}

.risk-banner-left {
  display: flex;
  align-items: center;
  gap: 10px;
}

.risk-icon-pulse {
  width: 26px;
  height: 26px;
  border-radius: 50%;
  background: rgba(239, 68, 68, 0.2);
  color: #ef4444;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 15px;
  animation: pulse-ring 1.8s infinite ease-in-out;
}

.risk-info {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.risk-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.risk-title {
  font-size: 12px;
  font-weight: 700;
  color: #ef4444;
}

.risk-desc {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  line-height: 1.3;
}

.risk-banner-right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.cooldown-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11.5px;
  color: #f59e0b;
  background: rgba(245, 158, 11, 0.15);
  border: 1px solid rgba(245, 158, 11, 0.4);
  padding: 2px 8px;
  border-radius: 12px;
}

.cooldown-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #f59e0b;
}

/* ──────────── 2. 顶部 KPI 矩阵 ──────────── */
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 8px;
  flex-shrink: 0;
}

@media (max-width: 1200px) {
  .kpi-grid {
    grid-template-columns: repeat(3, 1fr);
  }
}

.kpi-card {
  background: var(--app-window-bg);
  border: 1px solid var(--app-border);
  border-radius: 10px;
  padding: 8px 12px;
  display: flex;
  align-items: center;
  gap: 8px;
  box-shadow: var(--app-shadow-sm);
  transition: all 0.2s ease;
}

.kpi-card:hover {
  transform: translateY(-1px);
  box-shadow: var(--app-shadow);
}

.kpi-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
  min-width: 0;
}

.kpi-title-row {
  display: flex;
  align-items: center;
  gap: 4px;
}

.cpm-icon {
  color: #10b981;
  font-size: 12px;
}

.kpi-title {
  font-size: 10.5px;
  color: var(--app-text-secondary);
  font-weight: 500;
  white-space: nowrap;
}

.kpi-num {
  font-size: 16px;
  font-weight: 700;
  color: var(--app-title);
  font-family: var(--font-mono, monospace);
  line-height: 1.1;
}

.kpi-num-row {
  display: flex;
  align-items: baseline;
  gap: 4px;
}

.kpi-sub {
  font-size: 10.5px;
  color: var(--app-text-secondary);
  white-space: nowrap;
}

.kpi-icon-dot {
  display: flex;
  align-items: center;
  justify-content: center;
}

.live-pulse {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #94a3b8;
}

.state-running .live-pulse {
  background: #10b981;
  box-shadow: 0 0 10px #10b981;
  animation: pulse-ring 1.5s infinite;
}

.state-running .status-text {
  color: #10b981;
}

.state-paused .live-pulse {
  background: #f59e0b;
  box-shadow: 0 0 8px #f59e0b;
}

.state-paused .status-text {
  color: #f59e0b;
}

.state-stopped .status-text {
  color: var(--app-text-secondary);
}

.hit-card {
  border-color: rgba(16, 185, 129, 0.35);
  background: rgba(16, 185, 129, 0.05);
}

.err-card {
  border-color: rgba(239, 68, 68, 0.3);
  background: rgba(239, 68, 68, 0.04);
}

.timing-card-running {
  border-color: rgba(0, 122, 255, 0.35);
  background: rgba(0, 122, 255, 0.04);
}

.timing-kpi-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.pulse-dot-live {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #007aff;
  box-shadow: 0 0 6px #007aff;
}

.timing-sub-row {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 9px;
  color: var(--app-text-secondary);
  margin-top: 1px;
}

.timing-sub-time {
  font-family: var(--font-mono, monospace);
}

.timing-sub-arrow {
  opacity: 0.5;
}

.text-running-sub {
  color: #10b981;
  font-weight: 600;
}

/* ──────────── 3. 参数配置卡片 (极简优雅可折叠) ──────────── */
.macos-panel {
  background: var(--app-window-bg);
  border: 1px solid var(--app-border);
  border-radius: 10px;
  box-shadow: var(--app-shadow-sm);
}

.config-panel {
  padding: 8px 12px;
  flex-shrink: 0;
  transition: all 0.25s ease;
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  user-select: none;
}

.panel-header-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.macos-pill-tag {
  font-size: 9.5px;
  font-weight: 700;
  color: #10b981;
  background: rgba(16, 185, 129, 0.1);
  border: 1px solid rgba(16, 185, 129, 0.3);
  padding: 1px 5px;
  border-radius: 4px;
}

.panel-header-left .title {
  font-size: 12.5px;
  font-weight: 700;
  color: var(--app-title);
}

.config-summary-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--el-text-color-secondary);
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  padding: 2px 10px;
  border-radius: 12px;
}

.summary-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #10b981;
}

.header-timing-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 10.5px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--app-border);
  padding: 1px 7px;
  border-radius: 10px;
  color: var(--el-text-color-secondary);
}

.header-timing-pill.header-timing-running {
  background: rgba(16, 185, 129, 0.08);
  border-color: rgba(16, 185, 129, 0.3);
  color: #10b981;
}

.pill-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #94a3b8;
}

.pill-dot.pulse {
  background: #10b981;
  box-shadow: 0 0 6px #10b981;
}

.control-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.start-btn {
  font-weight: 700;
  border-radius: 6px;
}

.action-btn-group {
  display: flex;
  gap: 4px;
}

.config-toggle-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: var(--el-color-primary);
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-color-primary-light-7);
  padding: 3px 8px;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.config-toggle-btn:hover {
  background: var(--el-color-primary-light-9);
}

.config-toggle-btn .is-rotated {
  transform: rotate(180deg);
}

.config-panel .panel-body {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.mail-source-selector-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}

.mail-source-badge-tip {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.feature-switches {
  display: flex;
  align-items: center;
  gap: 12px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--app-border);
  padding: 3px 8px;
  border-radius: 6px;
}

.switch-item {
  display: flex;
  align-items: center;
  gap: 4px;
}

.switch-label {
  font-size: 11px;
  color: var(--app-title);
}

/* ──────────── 4. 实时流水表格（现代化流线型设计） ──────────── */
.table-panel {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.table-panel-header {
  padding: 8px 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--app-border);
  background: var(--el-fill-color-light);
  flex-shrink: 0;
}

.table-panel-header .header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}

.dot-live {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #10b981;
  box-shadow: 0 0 8px #10b981;
}

.badge-total {
  font-size: 11px;
  color: var(--app-text-secondary);
}

.filter-capsules {
  display: flex;
  gap: 4px;
}

.filter-pill {
  border: 1px solid var(--app-border);
  background: var(--app-window-bg);
  color: var(--app-text-secondary);
  border-radius: 12px;
  padding: 2px 8px;
  font-size: 10.5px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 4px;
  transition: all 0.15s ease;
}

.filter-pill:hover {
  background: var(--el-fill-color);
}

.filter-pill.active {
  background: var(--el-color-primary);
  color: #fff;
  border-color: var(--el-color-primary);
}

.dot-pill {
  width: 5px;
  height: 5px;
  border-radius: 50%;
}
.dot-running { background: #007aff; }
.dot-done { background: #10b981; }
.dot-failed { background: #ef4444; }

.last-msg-hint {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.table-container {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.table-pagination-bar {
  padding: 6px 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-top: 1px solid var(--app-border);
  background: var(--el-fill-color-light);
  flex-shrink: 0;
}

.page-tip {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

/* ──────────── 现代化表格单元格与 Connected Stepper Pipeline ──────────── */
.modern-stepper-table :deep(.el-table__row) {
  transition: all 0.15s ease;
}
.modern-stepper-table :deep(.el-table__row:hover) {
  background-color: var(--el-table-row-hover-bg-color) !important;
}

.email-modern-cell {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
}

.email-brand-icon {
  font-size: 13px;
  flex-shrink: 0;
}

.email-text-mono {
  font-family: var(--font-mono, monospace);
  font-weight: 600;
  color: var(--app-title);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.placeholder-shimmer-tag {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  color: #10b981;
  background: rgba(16, 185, 129, 0.1);
  border: 1px solid rgba(16, 185, 129, 0.25);
  padding: 1px 6px;
  border-radius: 4px;
}

.shimmer-pulse {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #10b981;
  animation: pulse-ring 1.2s infinite;
}

.modern-copy-btn {
  background: none;
  border: none;
  cursor: pointer;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  padding: 0 2px;
  opacity: 0.5;
  transition: all 0.15s ease;
}

.modern-copy-btn:hover {
  opacity: 1;
  color: var(--el-color-primary);
  transform: scale(1.1);
}

.worker-meta-cell {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
}

.worker-pill-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 10px;
  font-weight: 700;
  font-family: var(--font-mono, monospace);
  color: var(--app-title);
  background: var(--el-fill-color-light);
  border: 1px solid var(--app-border);
  padding: 1px 5px;
  border-radius: 4px;
}

.worker-pill-badge.is-active {
  border-color: rgba(16, 185, 129, 0.4);
  background: rgba(16, 185, 129, 0.08);
}

.worker-pulse-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #94a3b8;
}

.worker-pulse-dot.live {
  background: #10b981;
  box-shadow: 0 0 6px #10b981;
  animation: pulse-ring 1.5s infinite;
}

.geo-flag-pill {
  font-size: 10px;
  color: var(--el-color-primary);
  background: var(--el-fill-color-light);
  border: 1px solid var(--app-border);
  padding: 0 4px;
  border-radius: 3px;
  font-weight: 600;
}

.geo-flag-pill.geo-hot {
  color: #f59e0b;
  border-color: rgba(245, 158, 11, 0.3);
  background: rgba(245, 158, 11, 0.08);
}

.geo-default {
  color: var(--el-text-color-secondary);
}

/* ──────────── Connected Stepper Pipeline ──────────── */
.stepper-pipeline-container {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.stepper-track-row {
  display: flex;
  align-items: center;
  gap: 2px;
}

.stepper-node {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 9.5px;
  padding: 1px 4px;
  border-radius: 3px;
  background: var(--el-fill-color-light);
  border: 1px solid transparent;
  color: var(--el-text-color-secondary);
  transition: all 0.2s ease;
}

.stepper-node-dot {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  font-size: 8.5px;
  font-weight: 700;
  background: rgba(255, 255, 255, 0.08);
}

.stepper-node-label {
  font-size: 9px;
  font-weight: 600;
}

.stepper-node.is-done {
  background: rgba(16, 185, 129, 0.12);
  border-color: rgba(16, 185, 129, 0.35);
  color: #10b981;
}
.stepper-node.is-done .stepper-node-dot {
  background: #10b981;
  color: #ffffff;
}

.stepper-node.is-active {
  background: rgba(0, 122, 255, 0.16);
  border-color: #007aff;
  color: #007aff;
  font-weight: 700;
  box-shadow: 0 0 8px rgba(0, 122, 255, 0.3);
}
.stepper-node.is-active .stepper-node-dot {
  background: #007aff;
  color: #ffffff;
}
.node-pulse {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: #ffffff;
  animation: pulse-ring 1s infinite;
}

.stepper-node.is-failed {
  background: rgba(239, 68, 68, 0.12);
  border-color: rgba(239, 68, 68, 0.35);
  color: #ef4444;
}
.stepper-node.is-failed .stepper-node-dot {
  background: #ef4444;
  color: #ffffff;
}

.stepper-node.is-pending {
  opacity: 0.38;
}

.stepper-connector {
  flex: 1;
  height: 2px;
  background: var(--el-fill-color);
  border-radius: 2px;
  min-width: 6px;
  max-width: 14px;
}
.stepper-connector.is-done {
  background: #10b981;
}
.stepper-connector.is-active {
  background: #007aff;
}

.stepper-meta-row {
  display: flex;
  align-items: center;
  font-size: 10.5px;
}

.running-status-box {
  display: flex;
  align-items: center;
  gap: 5px;
  color: var(--el-color-primary);
}

.pulse-beacon {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #007aff;
  animation: pulse-ring 1.2s infinite;
}

.status-msg-running {
  font-size: 10.5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.status-pct {
  font-size: 10px;
  font-weight: 700;
  color: #007aff;
}

.done-status-box {
  color: #10b981;
  font-size: 10.5px;
  font-weight: 600;
}

.failed-status-box {
  color: #ef4444;
  font-size: 10.5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pending-status-box {
  color: var(--el-text-color-secondary);
  font-size: 10px;
}

/* 耗时与时间列 */
.duration-cell {
  font-size: 11.5px;
  font-weight: 700;
}
.duration-running {
  color: #007aff;
}
.duration-done {
  color: #10b981;
}
.duration-fail {
  color: #ef4444;
}

.time-cell {
  font-size: 10.5px;
  color: var(--el-text-color-secondary);
}

.modern-log-btn {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 10.5px;
  font-weight: 600;
  color: var(--el-color-primary);
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-color-primary-light-7);
  padding: 1px 5px;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.modern-log-btn:hover {
  background: var(--el-color-primary-light-9);
}

/* ──────────── 终端弹窗 ──────────── */
.macos-terminal-dialog :deep(.el-dialog__body) {
  padding: 0;
}

.modal-header {
  display: flex;
  align-items: center;
  gap: 12px;
}

.window-dots {
  display: flex;
  gap: 6px;
}

.window-dots .dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
}
.dot.red { background: #ff5f56; }
.dot.yellow { background: #ffbd2e; }
.dot.green { background: #27c93f; }

.modal-title-info {
  display: flex;
  align-items: center;
  gap: 8px;
}

.modal-email {
  font-size: 13px;
  font-weight: 700;
  color: var(--app-title);
  font-family: var(--font-mono, monospace);
}

.modal-terminal-wrap {
  background: #0d1117;
  padding: 12px;
}

.modal-terminal-body {
  height: 380px;
  overflow-y: auto;
  font-family: var(--font-mono, monospace);
  font-size: 11.5px;
  line-height: 1.6;
  color: #e6edf3;
}

.terminal-line {
  white-space: pre-wrap;
  word-break: break-all;
}

.line-err { color: #f85149; }
.line-ok { color: #3fb950; }
.line-warn { color: #d29922; }
.line-info { color: #58a6ff; }

.modal-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.log-count-tip {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.modal-footer-btns {
  display: flex;
  gap: 8px;
}

/* 动画定义 */
@keyframes pulse-ring {
  0% { transform: scale(0.85); opacity: 0.6; }
  50% { transform: scale(1.15); opacity: 1; }
  100% { transform: scale(0.85); opacity: 0.6; }
}
</style>
