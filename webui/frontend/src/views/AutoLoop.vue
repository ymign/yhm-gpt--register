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

const configChips = computed(() => {
  const srcMap = {
    remail: 'Remail 自动购号',
    cf_temp: 'CF 临时邮箱',
    outlook: '微软 Outlook',
    icloud_relay: 'iCloud 邮箱',
  }
  const src = srcMap[form.value.autoMailSource] || 'Remail 自动购号'
  const conc = `${form.value.autoConcurrency || 1} Workers`
  const pow = `${powSlots.value} 算力槽`
  const ctry = form.value.autoProxyCountry ? formatCountry(form.value.autoProxyCountry) : '随机出口'
  const sec = []
  if (form.value.autoWantPassword) sec.push('自动设密')
  if (form.value.autoWant2fa) sec.push('自动2FA')
  const secStr = sec.length ? sec.join(' + ') : '免密'
  return [src, conc, pow, ctry, secStr]
})

const configSummary = computed(() => {
  return configChips.value.join(' · ')
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
  const t = String(line).toLowerCase()
  if (
    t.includes('traceback') || t.includes('exception') || t.includes('error') ||
    t.includes('失败') || t.includes('fail') || t.includes('curl:') ||
    t.includes('异常') || t.includes('closed') || t.includes('errno')
  ) return 'log-err'
  if (t.includes('成功') || t.includes('完成') || t.includes('ok') || t.includes('2fa 绑定成功')) return 'log-hit'
  if (t.includes('warn') || t.includes('警告') || t.includes('timeout') || t.includes('超时')) return 'log-warn'
  if (t.includes('[register]') || t.includes('phase=')) return 'log-step'
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
    <!-- 背景流体微光环境光球 (Ambient Glass Glow Orbs) -->
    <div class="glass-ambient-sphere sphere-1"></div>
    <div class="glass-ambient-sphere sphere-2"></div>
    <div class="glass-ambient-sphere sphere-3"></div>

    <!-- ════════════ 1. 智能风控预警与自动熔断退避提示横幅 ════════════ -->
    <el-collapse-transition>
      <div v-if="riskWarning.active || st === 'paused'" class="risk-defense-banner">
        <div class="card-glass-specular"></div>
        <div class="liquid-caustic-flare"></div>
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
        <div class="card-glass-specular"></div>
        <div class="liquid-caustic-flare"></div>
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
        <div class="card-glass-specular"></div>
        <div class="liquid-caustic-flare"></div>
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
        <div class="card-glass-specular"></div>
        <div class="liquid-caustic-flare"></div>
        <div class="kpi-info">
          <span class="kpi-title">注册失败</span>
          <span class="kpi-num text-danger">{{ autoStatus.registered_fail || 0 }}</span>
        </div>
      </div>

      <!-- 出号速率 CPM -->
      <div class="kpi-card">
        <div class="card-glass-specular"></div>
        <div class="liquid-caustic-flare"></div>
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
        <div class="card-glass-specular"></div>
        <div class="liquid-caustic-flare"></div>
        <div class="kpi-info">
          <span class="kpi-title">出号成功率</span>
          <span class="kpi-num">{{ successRate }}</span>
        </div>
      </div>

      <!-- 批次耗时 -->
      <div class="kpi-card timing-card" :class="{ 'timing-card-running': st === 'running' }">
        <div class="card-glass-specular"></div>
        <div class="liquid-caustic-flare"></div>
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
            <span class="timing-sub-time mono">{{ formatClock(batchStartedAt) }}</span>
            <span class="timing-sub-arrow">→</span>
            <span class="timing-sub-time mono">
              <span v-if="st === 'running'" class="text-running-sub">运行中</span>
              <span v-else>{{ formatClock(batchFinishedAt) }}</span>
            </span>
          </div>
        </div>
      </div>
    </div>

    <!-- ════════════ 3. 全自动批量参数调度卡片 (一键折叠/展开，极简优雅) ════════════ -->
    <div class="macos-panel config-panel">
      <div class="card-glass-specular"></div>
      <div class="liquid-caustic-flare"></div>
      <div class="panel-header" @click="toggleConfigCollapsed">
        <div class="panel-header-left">
          <span class="autoloop-pill-tag">CONFIG</span>
          <span class="title">全自动批量参数调度</span>

          <!-- 折叠时的现代微规格芯片组 -->
          <div v-if="configCollapsed" class="config-summary-chips">
            <span v-for="(chip, cIdx) in configChips" :key="cIdx" class="config-chip-pill">
              {{ chip }}
            </span>
          </div>

          <!-- 展开时的时序微芯片 -->
          <div v-else-if="batchStartedAt" class="header-timing-pill" :class="{ 'header-timing-running': st === 'running' }">
            <span class="pill-dot" :class="{ 'pulse': st === 'running' }"></span>
            <span class="mono">开始: {{ formatClock(batchStartedAt) }}</span>
            <span class="pill-sep">·</span>
            <span v-if="st === 'running'">耗时: <strong class="mono">{{ formatDuration(batchElapsedSec) }}</strong></span>
            <span v-else>结束: <span class="mono">{{ formatClock(batchFinishedAt) }}</span> (总耗时 <span class="mono">{{ formatDuration(batchElapsedSec) }}</span>)</span>
            <span v-if="batchAvgSpeed !== '—'" class="pill-sep">·</span>
            <span v-if="batchAvgSpeed !== '—'">均速: <span class="mono">{{ batchAvgSpeed }}</span></span>
          </div>
        </div>

        <div class="control-actions" @click.stop>
          <button
            type="button"
            class="autoloop-btn btn-primary"
            :disabled="!canStart"
            @click="start"
          >
            <el-icon><VideoPlay /></el-icon>
            <span>开始自动运行</span>
          </button>
          <div class="action-btn-group">
            <button
              type="button"
              class="autoloop-btn btn-sub"
              :disabled="!canPause"
              @click="call(autoPause, '暂停')"
            >
              <el-icon><VideoPause /></el-icon>
              <span>暂停</span>
            </button>
            <button
              type="button"
              class="autoloop-btn btn-sub"
              :disabled="!canResume"
              @click="call(autoResume, '恢复')"
            >
              <el-icon><RefreshRight /></el-icon>
              <span>恢复</span>
            </button>
            <button
              type="button"
              class="autoloop-btn btn-danger"
              :disabled="!canStop"
              @click="call(autoStop, '停止')"
            >
              <el-icon><SwitchButton /></el-icon>
              <span>停止任务</span>
            </button>
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
                      <span v-if="form.autoMailSource === 'remail'" class="text-remail" style="color: #28646e; font-weight: 500;">🍎 Remail 自动购号：每次并发注册按需购买全新邮箱，支持微软/iCloud等多后缀</span>
                      <span v-else-if="form.autoMailSource === 'cf_temp'" class="text-cf" style="color: #28646e; font-weight: 500;">⚡ 无需号池：Worker 动态无限生成地址并发注册，推荐</span>
                      <span v-else-if="form.autoMailSource === 'outlook'" class="text-outlook" style="color: #28646e; font-weight: 500;">📦 微软号池并发：自动从号池领取可用账号，池空自动等待</span>
                      <span v-else-if="form.autoMailSource === 'icloud_relay'" class="text-ic" style="color: #28646e; font-weight: 500;">✉️ iCloud 号池并发：自动从号池领取带中转链接的账号</span>
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
                    <el-tooltip content="同时解算 Sentinel PoW 的 node 进程数上限（默认 6）。启动日志里的「预计算池缓冲水位=3」是另一件事，已经停用，不会拿别人的指纹 token。网络并发再高，PoW 也会在这里排队。" placement="top">
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
                <el-form-item>
                  <template #label>
                    <span>目标数量 (0=不限)</span>
                    <el-tooltip content="只表示要成功几个号。填 1 时即使并发开 15，也只会真正拉起 1 个 Worker；失败后由同一 Worker 重试，不会再刷「目标已锁定，退出」。" placement="top">
                      <el-icon class="info-ico" style="margin-left: 3px;"><QuestionFilled /></el-icon>
                    </el-tooltip>
                  </template>
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
      <div class="card-glass-specular"></div>
      <div class="liquid-caustic-flare"></div>
      <div class="table-panel-header">
        <div class="header-left">
          <div class="panel-header-title">
            <span class="dot-live"></span>
            <span class="title">账号注册流水监控</span>
            <span class="badge-total">{{ taskList.length }} 个任务</span>
          </div>

          <!-- 现代状态筛选胶囊组 -->
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
          <span class="last-msg-dot"></span>
          <span>{{ autoStatus.last_message }}</span>
        </div>
      </div>

      <div class="table-container">
        <el-table
          :data="paginatedTasks"
          row-key="run_id"
          height="100%"
          size="small"
          stripe
          class="modern-stepper-table auto-stepper-table"
          :highlight-current-row="false"
        >
          <!-- 账号邮箱 -->
          <el-table-column prop="email" label="账号邮箱" min-width="240" show-overflow-tooltip>
            <template #default="{ row }">
              <div class="email-modern-cell">
                <span class="auto-email-dot"></span>
                <span
                  v-if="isPlaceholder(row.email)"
                  class="placeholder-shimmer-tag"
                >
                  <span class="shimmer-pulse"></span>
                  <span>Remail 自动购号中...</span>
                </span>
                <span v-else class="email-text-mono mono" :title="row.email">
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
                <span v-else class="geo-flag-pill geo-default">跟随代理</span>
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

                <!-- 步骤描述与微状态（彻底移除 🎉 礼花） -->
                <div class="stepper-meta-row">
                  <div v-if="row.status === 'running'" class="running-status-box">
                    <span class="pulse-beacon"></span>
                    <span class="status-msg-running">{{ row.phase_text || '正在处理中...' }}</span>
                    <span v-if="row.percent" class="status-pct mono">{{ row.percent }}%</span>
                  </div>
                  <div v-else-if="row.status === 'done'" class="done-status-box">
                    <span class="done-tag">全流程完成 · 成功入库 (100%)</span>
                  </div>
                  <div v-else-if="row.status === 'failed'" class="failed-status-box" :title="row.error">
                    <span class="fail-tag">✕ {{ row.error || row.phase_text || '注册失败' }}</span>
                  </div>
                  <div v-else class="pending-status-box">
                    <span class="pending-tag">等待 Worker 调度</span>
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
          <el-table-column label="操作" width="80" fixed="right" align="center">
            <template #default="{ row }">
              <button
                type="button"
                class="modern-log-btn auto-micro-btn"
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
      width="880px"
      top="6vh"
      class="macos-terminal-dialog"
      append-to-body
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
/* ──────────── 3D实体液态水晶玻璃拟态 (Liquid Glass / Crystal Glassmorphism) ──────────── */
.autoloop-page {
  --glass-bg: linear-gradient(135deg, rgba(255, 255, 255, 0.84) 0%, rgba(255, 255, 255, 0.54) 50%, rgba(248, 250, 252, 0.7) 100%);
  --glass-border: rgba(255, 255, 255, 0.92);
  --glass-border-light: #ffffff;
  --glass-shadow:
    0 22px 48px -10px rgba(15, 23, 42, 0.08),
    0 8px 18px -4px rgba(15, 23, 42, 0.04);
  --glass-inset:
    inset 0 2px 2px 0 #ffffff,
    inset 1.5px 0 2px 0 rgba(255, 255, 255, 0.85),
    inset -1.5px 0 2px 0 rgba(255, 255, 255, 0.55),
    inset 0 -2px 3px 0 rgba(148, 163, 184, 0.18);
  --accent-primary: #0284c7;
  --accent-hover: #0369a1;
  --text-main: #0f172a;
  --text-muted: #475569;
  --text-sub: #64748b;

  position: relative;
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 14px;
  overflow: hidden;
  /* 摄影棚哑光台面 + 极细透气正交网格底纹 (如图片 29/31/32) */
  background-color: #f6f8fb;
  background-image:
    radial-gradient(ellipse at 50% -12%, rgba(255, 255, 255, 0.98) 0%, transparent 65%),
    radial-gradient(ellipse at 88% 18%, rgba(56, 189, 248, 0.12) 0%, transparent 45%),
    radial-gradient(ellipse at 12% 82%, rgba(249, 115, 22, 0.08) 0%, transparent 45%),
    linear-gradient(to right, rgba(203, 213, 225, 0.32) 1px, transparent 1px),
    linear-gradient(to bottom, rgba(203, 213, 225, 0.32) 1px, transparent 1px);
  background-size: 100% 100%, 100% 100%, 100% 100%, 32px 32px, 32px 32px;
  background-attachment: fixed;
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
  color: var(--text-main);
}

/* ════════ 背景环境微光光球 (柔和摄影棚漫射光晕) ════════ */
.glass-ambient-sphere {
  position: absolute;
  border-radius: 50%;
  filter: blur(75px);
  pointer-events: none;
  z-index: 0;
  opacity: 0.65;
}
.sphere-1 {
  top: -40px;
  left: 15%;
  width: 420px;
  height: 420px;
  background: radial-gradient(circle, rgba(56, 189, 248, 0.55) 0%, rgba(14, 165, 233, 0.05) 70%);
  animation: floatOrb1 15s ease-in-out infinite alternate;
}
.sphere-2 {
  top: 30%;
  right: 10%;
  width: 460px;
  height: 460px;
  background: radial-gradient(circle, rgba(168, 85, 247, 0.45) 0%, rgba(99, 102, 241, 0.05) 70%);
  animation: floatOrb2 19s ease-in-out infinite alternate-reverse;
}
.sphere-3 {
  bottom: -60px;
  left: 35%;
  width: 480px;
  height: 480px;
  background: radial-gradient(circle, rgba(16, 185, 129, 0.4) 0%, rgba(5, 150, 105, 0.05) 70%);
  animation: floatOrb1 17s ease-in-out infinite alternate;
}
@keyframes floatOrb1 {
  0% { transform: translate(0, 0) scale(1); }
  100% { transform: translate(25px, 15px) scale(1.06); }
}
@keyframes floatOrb2 {
  0% { transform: translate(0, 0) scale(1); }
  100% { transform: translate(-20px, 20px) scale(1.05); }
}

/* ════════ 核心精髓：彩虹棱镜色散高光 (Prismatic Caustic Dispersion Flare) ════════ */
.liquid-caustic-flare {
  position: absolute;
  bottom: 0px;
  left: 12%;
  right: 12%;
  height: 2px;
  border-radius: 9999px;
  background: linear-gradient(90deg,
    transparent 0%,
    rgba(249, 115, 22, 0.65) 15%,
    rgba(251, 191, 36, 0.85) 32%,
    rgba(255, 255, 255, 1) 50%,
    rgba(56, 189, 248, 0.9) 66%,
    rgba(168, 85, 247, 0.7) 84%,
    transparent 100%
  );
  filter: blur(0.5px);
  box-shadow: 0 3px 12px rgba(251, 191, 36, 0.42), 0 4px 14px rgba(56, 189, 248, 0.38);
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

/* ──────────── 1. 智能风控预警与自动熔断退避厚水晶横幅 ──────────── */
.risk-defense-banner {
  position: relative;
  background: linear-gradient(135deg, rgba(254, 242, 242, 0.9) 0%, rgba(255, 247, 237, 0.75) 100%);
  backdrop-filter: blur(24px) saturate(200%);
  -webkit-backdrop-filter: blur(24px) saturate(200%);
  border: 1.5px solid rgba(239, 68, 68, 0.4);
  border-top: 2px solid #ffffff;
  border-radius: 14px;
  padding: 8px 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 10px;
  box-shadow: 0 10px 24px -4px rgba(239, 68, 68, 0.15), var(--glass-inset);
  flex-shrink: 0;
  overflow: hidden;
  z-index: 4;
}

.risk-banner-left {
  position: relative;
  z-index: 4;
  display: flex;
  align-items: center;
  gap: 10px;
}

.risk-icon-pulse {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: linear-gradient(180deg, #f87171 0%, #ef4444 100%);
  border: 1.5px solid rgba(255, 255, 255, 0.95);
  color: #ffffff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 15px;
  box-shadow: 0 4px 12px rgba(239, 68, 68, 0.4), inset 0 1.5px 1.5px #ffffff;
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
  font-size: 12.5px;
  font-weight: 800;
  color: #b91c1c;
}

.risk-tag {
  border-radius: 9999px !important;
  font-weight: 700;
}

.risk-desc {
  font-size: 11px;
  color: #7f1d1d;
  line-height: 1.35;
}

.risk-banner-right {
  position: relative;
  z-index: 4;
  display: flex;
  align-items: center;
  gap: 10px;
}

.cooldown-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11.5px;
  font-weight: 700;
  color: #c2410c;
  background: rgba(251, 146, 60, 0.2);
  border: 1px solid rgba(251, 146, 60, 0.45);
  padding: 2px 10px;
  border-radius: 9999px;
  box-shadow: inset 0 1px 1px #ffffff;
}

.cooldown-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #ea580c;
  box-shadow: 0 0 6px #ea580c;
}

/* ──────────── 2. 顶部 KPI 矩阵 (6 大厚水晶指标卡) ──────────── */
.kpi-grid {
  position: relative;
  z-index: 4;
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
  position: relative;
  background: var(--glass-bg);
  backdrop-filter: blur(24px) saturate(200%);
  -webkit-backdrop-filter: blur(24px) saturate(200%);
  border: 1.5px solid var(--glass-border);
  border-top: 2px solid var(--glass-border-light);
  border-radius: 14px;
  padding: 8px 12px;
  min-height: 60px;
  display: flex;
  align-items: center;
  gap: 10px;
  box-shadow: var(--glass-shadow), var(--glass-inset);
  overflow: hidden;
  transition: all 0.24s cubic-bezier(0.16, 1, 0.3, 1);
  cursor: default;
}

.kpi-card:hover {
  transform: translateY(-3px) scale(1.015);
  border-color: rgba(56, 189, 248, 0.6);
  box-shadow:
    0 16px 36px -6px rgba(2, 132, 199, 0.18),
    0 6px 14px rgba(15, 23, 42, 0.05),
    var(--glass-inset);
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.95) 0%, rgba(255, 255, 255, 0.68) 100%);
}

.kpi-info {
  position: relative;
  z-index: 4;
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
  color: #0284c7;
  font-size: 13px;
}

.kpi-title {
  font-size: 11px;
  color: var(--text-muted);
  font-weight: 700;
  white-space: nowrap;
}

.kpi-num {
  font-size: 19px;
  font-weight: 800;
  color: var(--text-main);
  font-family: var(--el-font-family-monospace, monospace);
  line-height: 1.2;
  letter-spacing: -0.02em;
}

.kpi-num-row {
  display: flex;
  align-items: baseline;
  gap: 4px;
}

.kpi-sub {
  font-size: 10.5px;
  color: #64748b;
  font-weight: 600;
  white-space: nowrap;
}

.text-success { color: #059669 !important; }
.text-danger { color: #dc2626 !important; }
.text-emerald { color: #0284c7 !important; }
.text-primary { color: #0284c7 !important; }

.kpi-icon-dot {
  position: relative;
  z-index: 4;
  display: flex;
  align-items: center;
  justify-content: center;
}

.live-pulse {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: #94a3b8;
}

.state-running .live-pulse {
  background: #10b981;
  box-shadow: 0 0 10px #10b981;
  animation: pulse-ring 1.5s infinite;
}

.state-running .status-text {
  color: #047857;
}

.state-paused .live-pulse {
  background: #f59e0b;
  box-shadow: 0 0 8px #f59e0b;
}

.state-paused .status-text {
  color: #b45309;
}

.state-stopped .status-text {
  color: #64748b;
}

.timing-kpi-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.pulse-dot-live {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #10b981;
  box-shadow: 0 0 8px #10b981;
}

.timing-sub-row {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 10px;
  color: #64748b;
  margin-top: 2px;
  font-weight: 500;
}

.timing-sub-time {
  font-family: var(--el-font-family-monospace, monospace);
}

.timing-sub-arrow {
  opacity: 0.5;
}

.text-running-sub {
  color: #0284c7;
  font-weight: 700;
}

/* ──────────── 3. 参数配置厚水晶卡片 ──────────── */
.macos-panel {
  position: relative;
  background: var(--glass-bg);
  backdrop-filter: blur(28px) saturate(200%);
  -webkit-backdrop-filter: blur(28px) saturate(200%);
  border: 1.5px solid var(--glass-border);
  border-top: 2px solid var(--glass-border-light);
  border-radius: 16px;
  box-shadow: var(--glass-shadow), var(--glass-inset);
  overflow: hidden;
}

.config-panel {
  padding: 8px 14px;
  flex-shrink: 0;
  transition: all 0.25s ease;
  z-index: 4;
}

.panel-header {
  position: relative;
  z-index: 4;
  display: flex;
  align-items: center;
  justify-content: space-between;
  user-select: none;
}

.panel-header-left {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.autoloop-pill-tag {
  font-size: 9.5px;
  font-weight: 800;
  color: #ffffff;
  background: linear-gradient(180deg, #38bdf8 0%, #0284c7 100%);
  padding: 2px 7px;
  border-radius: 9999px;
  letter-spacing: 0.5px;
  font-family: var(--el-font-family-monospace, monospace);
  box-shadow: 0 2px 6px rgba(2, 132, 199, 0.35);
}

.panel-header-left .title {
  font-size: 13.5px;
  font-weight: 800;
  color: var(--text-main);
  letter-spacing: -0.01em;
}

/* 现代微规格芯片组 */
.config-summary-chips {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.config-chip-pill {
  display: inline-flex;
  align-items: center;
  font-size: 10.5px;
  font-weight: 700;
  color: #0369a1;
  background: rgba(2, 132, 199, 0.1);
  border: 1px solid rgba(2, 132, 199, 0.25);
  padding: 1.5px 8px;
  border-radius: 9999px;
  white-space: nowrap;
  box-shadow: inset 0 1px 1px rgba(255, 255, 255, 0.8);
}

.header-timing-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  background: rgba(255, 255, 255, 0.65);
  border: 1px solid rgba(255, 255, 255, 0.9);
  padding: 2px 10px;
  border-radius: 9999px;
  color: var(--text-muted);
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04);
}

.header-timing-pill.header-timing-running {
  background: rgba(2, 132, 199, 0.12);
  border-color: rgba(2, 132, 199, 0.3);
  color: #0284c7;
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

.pill-sep {
  opacity: 0.4;
}

.control-actions {
  position: relative;
  z-index: 4;
  display: flex;
  align-items: center;
  gap: 8px;
}

/* 现代化 3D 水晶操作按钮 (对齐图片 31/32/33 的 Create Workspace / Start project 按钮) */
.autoloop-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 32px;
  padding: 0 14px;
  border-radius: 9999px;
  font-size: 11.5px;
  font-weight: 800;
  cursor: pointer;
  transition: all 0.18s cubic-bezier(0.16, 1, 0.3, 1);
  user-select: none;
  outline: none;
}

/* 启动按钮：绿光果冻水晶药丸 */
.autoloop-btn.btn-primary {
  background: linear-gradient(180deg, #34d399 0%, #059669 100%);
  border: 1.8px solid rgba(255, 255, 255, 0.95);
  color: #ffffff;
  box-shadow:
    0 10px 24px -2px rgba(5, 150, 105, 0.45),
    0 3px 8px rgba(15, 23, 42, 0.08),
    inset 0 2px 2px rgba(255, 255, 255, 0.9),
    inset 0 -2px 3px rgba(0, 0, 0, 0.2);
}
.autoloop-btn.btn-primary:hover:not(:disabled) {
  transform: translateY(-2px) scale(1.02);
  box-shadow:
    0 14px 30px -2px rgba(5, 150, 105, 0.6),
    0 4px 10px rgba(15, 23, 42, 0.1),
    inset 0 2px 2px rgba(255, 255, 255, 0.95);
}
.autoloop-btn.btn-primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  box-shadow: none;
}

/* 次级按钮：高透水晶药丸 */
.autoloop-btn.btn-sub {
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.95) 0%, rgba(241, 245, 249, 0.7) 100%);
  border: 1.5px solid rgba(255, 255, 255, 0.95);
  color: #334155;
  box-shadow: 0 4px 10px rgba(15, 23, 42, 0.05), inset 0 1.5px 1px #ffffff;
}
.autoloop-btn.btn-sub:hover:not(:disabled) {
  background: #ffffff;
  border-color: #0284c7;
  color: #0284c7;
  transform: translateY(-2px);
  box-shadow: 0 8px 18px -2px rgba(2, 132, 199, 0.25), inset 0 1.5px 1px #ffffff;
}
.autoloop-btn.btn-sub:disabled {
  opacity: 0.45;
  cursor: not-allowed;
  box-shadow: none;
}

/* 停止按钮：红宝石果冻水晶药丸 */
.autoloop-btn.btn-danger {
  background: linear-gradient(180deg, #f87171 0%, #dc2626 100%);
  border: 1.8px solid rgba(255, 255, 255, 0.95);
  color: #ffffff;
  box-shadow:
    0 10px 24px -2px rgba(220, 38, 38, 0.45),
    0 3px 8px rgba(15, 23, 42, 0.08),
    inset 0 2px 2px rgba(255, 255, 255, 0.9),
    inset 0 -2px 3px rgba(0, 0, 0, 0.2);
}
.autoloop-btn.btn-danger:hover:not(:disabled) {
  transform: translateY(-2px) scale(1.02);
  box-shadow:
    0 14px 30px -2px rgba(220, 38, 38, 0.6),
    0 4px 10px rgba(15, 23, 42, 0.1),
    inset 0 2px 2px rgba(255, 255, 255, 0.95);
}
.autoloop-btn.btn-danger:disabled {
  opacity: 0.4;
  cursor: not-allowed;
  box-shadow: none;
}

.action-btn-group {
  display: flex;
  gap: 6px;
}

.config-toggle-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 32px;
  font-size: 11.5px;
  font-weight: 700;
  color: #334155;
  background: rgba(255, 255, 255, 0.75);
  border: 1.5px solid rgba(255, 255, 255, 0.95);
  padding: 0 12px;
  border-radius: 9999px;
  cursor: pointer;
  box-shadow: 0 2px 6px rgba(15, 23, 42, 0.04), inset 0 1px 1px #ffffff;
  transition: all 0.18s ease;
}

.config-toggle-btn:hover {
  background: #ffffff;
  border-color: #0284c7;
  color: #0284c7;
  transform: translateY(-1px);
}

.config-toggle-btn .is-rotated {
  transform: rotate(180deg);
}

.config-panel .panel-body {
  position: relative;
  z-index: 4;
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid rgba(255, 255, 255, 0.6);
}

.mail-source-selector-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}

.mail-source-badge-tip {
  font-size: 11px;
  color: #64748b;
  font-weight: 500;
}

/* 自动化附加功能水晶药丸开关 */
.feature-switches {
  display: flex;
  align-items: center;
  gap: 12px;
  background: rgba(255, 255, 255, 0.65);
  border: 1.5px solid rgba(255, 255, 255, 0.9);
  padding: 4px 12px;
  border-radius: 9999px;
  box-shadow: inset 0 1px 2px rgba(15, 23, 42, 0.04);
}

.switch-item {
  display: flex;
  align-items: center;
  gap: 6px;
}

.switch-label {
  font-size: 11px;
  color: var(--text-main);
  font-weight: 700;
}

/* ──────────── 4. 实时流水表格（厚水晶面板） ──────────── */
.table-panel {
  position: relative;
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  z-index: 4;
}

.table-panel-header {
  position: relative;
  z-index: 4;
  padding: 8px 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid rgba(255, 255, 255, 0.6);
  flex-shrink: 0;
}

.table-panel-header .header-left {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.panel-header-title {
  display: flex;
  align-items: center;
  gap: 7px;
}

.panel-header-title .title {
  font-size: 13.5px;
  font-weight: 800;
  color: var(--text-main);
  letter-spacing: -0.01em;
}

.dot-live {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #10b981;
  box-shadow: 0 0 8px #10b981;
}

.badge-total {
  font-size: 11px;
  font-weight: 700;
  color: #0369a1;
  background: rgba(2, 132, 199, 0.1);
  border: 1px solid rgba(2, 132, 199, 0.25);
  padding: 1.5px 8px;
  border-radius: 9999px;
}

/* 筛选胶囊组 (对齐图片 31/32 的 Crystal Filter Pills) */
.filter-capsules {
  display: flex;
  gap: 6px;
}

.filter-pill {
  border: 1.5px solid rgba(255, 255, 255, 0.9);
  background: rgba(255, 255, 255, 0.7);
  color: #475569;
  border-radius: 9999px;
  padding: 2.5px 11px;
  font-size: 11px;
  font-weight: 700;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 5px;
  box-shadow: 0 2px 6px rgba(15, 23, 42, 0.04), inset 0 1px 1px #ffffff;
  transition: all 0.18s cubic-bezier(0.16, 1, 0.3, 1);
}

.filter-pill:hover {
  background: #ffffff;
  color: #0284c7;
  transform: translateY(-1px);
}

.filter-pill.active {
  border-color: rgba(255, 255, 255, 0.95);
  color: #ffffff;
}

.filter-pill.active:not(.pill-running):not(.pill-done):not(.pill-failed) {
  background: linear-gradient(180deg, #38bdf8 0%, #0284c7 100%);
  box-shadow: 0 6px 16px -2px rgba(2, 132, 199, 0.45), inset 0 1.5px 1px rgba(255, 255, 255, 0.9);
}

.filter-pill.pill-running.active {
  background: linear-gradient(180deg, #38bdf8 0%, #0284c7 100%);
  box-shadow: 0 6px 16px -2px rgba(2, 132, 199, 0.45), inset 0 1.5px 1px rgba(255, 255, 255, 0.9);
}

.filter-pill.pill-done.active {
  background: linear-gradient(180deg, #34d399 0%, #059669 100%);
  box-shadow: 0 6px 16px -2px rgba(5, 150, 105, 0.45), inset 0 1.5px 1px rgba(255, 255, 255, 0.9);
}

.filter-pill.pill-failed.active {
  background: linear-gradient(180deg, #f87171 0%, #dc2626 100%);
  box-shadow: 0 6px 16px -2px rgba(220, 38, 38, 0.45), inset 0 1.5px 1px rgba(255, 255, 255, 0.9);
}

.filter-pill.active .pill-cnt {
  color: #ffffff;
  opacity: 0.95;
}

.pill-cnt {
  font-family: var(--el-font-family-monospace, monospace);
  font-weight: 800;
  font-size: 10.5px;
  color: #334155;
}

.dot-pill {
  width: 5px;
  height: 5px;
  border-radius: 50%;
}
.dot-running { background: #0284c7; }
.dot-done { background: #10b981; }
.dot-failed { background: #ef4444; }

.last-msg-hint {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  color: #475569;
  background: rgba(255, 255, 255, 0.7);
  border: 1px solid rgba(255, 255, 255, 0.9);
  padding: 2px 10px;
  border-radius: 9999px;
  font-weight: 500;
}

.last-msg-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #0284c7;
}

.table-container {
  position: relative;
  z-index: 4;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.table-pagination-bar {
  position: relative;
  z-index: 4;
  padding: 6px 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-top: 1px solid rgba(255, 255, 255, 0.6);
  flex-shrink: 0;
}

.page-tip {
  font-size: 11px;
  color: var(--text-muted);
  font-weight: 500;
}

/* ──────────── 水晶玻璃表格样式 ──────────── */
.auto-stepper-table {
  background: transparent !important;
}

.auto-stepper-table :deep(.el-table__inner-wrapper) {
  background: transparent !important;
}

.auto-stepper-table :deep(tr) {
  background: transparent !important;
}

.auto-stepper-table :deep(th.el-table__cell) {
  background: rgba(255, 255, 255, 0.7) !important;
  backdrop-filter: blur(16px);
  color: var(--text-main) !important;
  font-weight: 800;
  font-size: 11.5px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.85) !important;
}

.auto-stepper-table :deep(td.el-table__cell) {
  background: rgba(255, 255, 255, 0.35);
  border-bottom: 1px solid rgba(255, 255, 255, 0.5) !important;
}

.auto-stepper-table :deep(.el-table__row:hover td.el-table__cell) {
  background: rgba(255, 255, 255, 0.7) !important;
}

.email-modern-cell {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  cursor: pointer;
  padding: 2px 6px;
  border-radius: 6px;
  transition: all 0.14s ease;
}
.email-modern-cell:hover {
  background: rgba(255, 255, 255, 0.85);
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.05);
}
.email-modern-cell:hover .modern-copy-btn {
  opacity: 1;
  color: #0284c7;
}

.auto-email-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #0284c7;
  flex-shrink: 0;
}

.email-text-mono {
  font-family: var(--el-font-family-monospace, monospace);
  font-weight: 600;
  color: var(--text-main);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.placeholder-shimmer-tag {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 10.5px;
  font-weight: 700;
  color: #0369a1;
  background: rgba(2, 132, 199, 0.12);
  border: 1px solid rgba(2, 132, 199, 0.3);
  padding: 1px 7px;
  border-radius: 9999px;
}

.shimmer-pulse {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #0284c7;
  animation: pulse-ring 1.2s infinite;
}

.modern-copy-btn {
  background: none;
  border: none;
  cursor: pointer;
  color: #94a3b8;
  font-size: 12px;
  padding: 0 2px;
  opacity: 0.3;
  transition: all 0.15s ease;
}

.modern-copy-btn:hover {
  opacity: 1;
  color: #0284c7;
}

/* Worker & 出口列 */
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
  font-family: var(--el-font-family-monospace, monospace);
  color: #334155;
  background: rgba(255, 255, 255, 0.75);
  border: 1px solid rgba(255, 255, 255, 0.95);
  padding: 1px 7px;
  border-radius: 4px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.03);
}

.worker-pill-badge.is-active {
  border-color: rgba(2, 132, 199, 0.4);
  background: rgba(2, 132, 199, 0.12);
  color: #0284c7;
}

.worker-pulse-dot {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: #94a3b8;
}

.worker-pulse-dot.live {
  background: #10b981;
  box-shadow: 0 0 6px #10b981;
  animation: pulse-ring 1.5s infinite;
}

.geo-flag-pill {
  font-size: 9.5px;
  color: #475569;
  background: rgba(255, 255, 255, 0.65);
  border: 1px solid rgba(255, 255, 255, 0.85);
  padding: 0 6px;
  border-radius: 3px;
  font-weight: 600;
}

.geo-flag-pill.geo-hot {
  color: #0284c7;
  border-color: rgba(2, 132, 199, 0.35);
  background: rgba(2, 132, 199, 0.1);
}

.geo-default {
  color: #94a3b8;
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
  gap: 3px;
}

.stepper-node {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 10px;
  padding: 1.5px 6px;
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.65);
  border: 1px solid rgba(255, 255, 255, 0.85);
  color: #64748b;
  transition: all 0.2s ease;
}

.stepper-node-dot {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 11px;
  height: 11px;
  border-radius: 50%;
  font-size: 8px;
  font-weight: 800;
  background: rgba(148, 163, 184, 0.3);
  color: #475569;
}

.stepper-node-label {
  font-size: 10px;
  font-weight: 600;
}

.stepper-node.is-done {
  background: rgba(16, 185, 129, 0.12);
  border-color: rgba(16, 185, 129, 0.35);
  color: #047857;
}
.stepper-node.is-done .stepper-node-dot {
  background: #10b981;
  color: #ffffff;
}

.stepper-node.is-active {
  background: rgba(2, 132, 199, 0.14);
  border-color: #0284c7;
  color: #0284c7;
  font-weight: 700;
  box-shadow: 0 0 8px rgba(2, 132, 199, 0.3);
}
.stepper-node.is-active .stepper-node-dot {
  background: #0284c7;
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
  color: #dc2626;
}
.stepper-node.is-failed .stepper-node-dot {
  background: #ef4444;
  color: #ffffff;
}

.stepper-node.is-pending {
  opacity: 0.45;
}

.stepper-connector {
  flex: 1;
  height: 2px;
  background: rgba(203, 213, 225, 0.5);
  border-radius: 2px;
  min-width: 6px;
  max-width: 14px;
}
.stepper-connector.is-done {
  background: #10b981;
}
.stepper-connector.is-active {
  background: #0284c7;
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
  color: #0284c7;
  font-weight: 600;
}

.pulse-beacon {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #0284c7;
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
  font-weight: 800;
  color: #0284c7;
}

.done-status-box {
  display: inline-flex;
  align-items: center;
}
.done-tag {
  color: #047857;
  font-size: 10.5px;
  font-weight: 700;
  background: rgba(16, 185, 129, 0.12);
  border: 1px solid rgba(16, 185, 129, 0.3);
  padding: 1px 7px;
  border-radius: 9999px;
}

.failed-status-box {
  display: inline-flex;
  align-items: center;
}
.fail-tag {
  color: #dc2626;
  font-size: 10.5px;
  font-weight: 700;
  background: rgba(239, 68, 68, 0.12);
  border: 1px solid rgba(239, 68, 68, 0.3);
  padding: 1px 7px;
  border-radius: 9999px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pending-status-box {
  color: #94a3b8;
  font-size: 10.5px;
}
.pending-tag {
  background: rgba(255, 255, 255, 0.6);
  padding: 1px 6px;
  border-radius: 3px;
}

/* 耗时与时间列 */
.duration-cell {
  font-size: 11.5px;
  font-weight: 700;
  font-family: var(--el-font-family-monospace, monospace);
}
.duration-running {
  color: #0284c7;
}
.duration-done {
  color: #059669;
}
.duration-fail {
  color: #dc2626;
}

.time-cell {
  font-size: 11px;
  color: var(--text-muted);
  font-family: var(--el-font-family-monospace, monospace);
  font-weight: 600;
}

/* 日志按钮：水晶微药丸按钮 */
.auto-micro-btn {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 11px;
  font-weight: 700;
  color: #334155;
  background: rgba(255, 255, 255, 0.8);
  border: 1.2px solid rgba(255, 255, 255, 0.95);
  padding: 2.5px 8px;
  border-radius: 9999px;
  cursor: pointer;
  box-shadow: 0 2px 5px rgba(15, 23, 42, 0.04);
  transition: all 0.16s ease;
}

.auto-micro-btn:hover {
  background: #ffffff;
  border-color: #0284c7;
  color: #0284c7;
  transform: translateY(-1px);
  box-shadow: 0 4px 10px rgba(2, 132, 199, 0.2);
}

/* 动画定义 */
@keyframes pulse-ring {
  0% { transform: scale(0.85); opacity: 0.6; }
  50% { transform: scale(1.15); opacity: 1; }
  100% { transform: scale(0.85); opacity: 0.6; }
}

/* ──────────── 3D 水晶拟态表单控件系统性调优 ──────────── */
:deep(.macos-radio-group .el-radio-button__inner) {
  border-color: rgba(255, 255, 255, 0.9) !important;
  color: #334155 !important;
  font-size: 11.5px !important;
  font-weight: 700 !important;
  padding: 5px 12px !important;
  background: rgba(255, 255, 255, 0.75) !important;
  backdrop-filter: blur(12px);
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04), inset 0 1px 1px #ffffff !important;
  transition: all 0.16s ease !important;
}
:deep(.macos-radio-group .el-radio-button__original-radio:checked + .el-radio-button__inner) {
  background: linear-gradient(180deg, #38bdf8 0%, #0284c7 100%) !important;
  border-color: rgba(255, 255, 255, 0.95) !important;
  color: #ffffff !important;
  font-weight: 800 !important;
  box-shadow: 0 4px 14px rgba(2, 132, 199, 0.4), inset 0 1.5px 1px rgba(255, 255, 255, 0.9) !important;
}

:deep(.macos-num-input) {
  width: 100% !important;
}
:deep(.macos-num-input .el-input__wrapper) {
  background: rgba(255, 255, 255, 0.85) !important;
  border: 1.2px solid rgba(255, 255, 255, 0.95) !important;
  box-shadow: inset 0 1.5px 3px rgba(15, 23, 42, 0.06) !important;
  border-radius: 8px !important;
  padding-left: 30px !important;
  padding-right: 30px !important;
  height: 30px !important;
  transition: all 0.18s ease !important;
}
:deep(.macos-num-input.is-focus .el-input__wrapper),
:deep(.macos-num-input .el-input__wrapper.is-focus) {
  border-color: #38bdf8 !important;
  background: #ffffff !important;
  box-shadow: 0 0 0 2px rgba(56, 189, 248, 0.3), inset 0 1.5px 3px rgba(15, 23, 42, 0.06) !important;
}
:deep(.macos-num-input .el-input-number__decrease),
:deep(.macos-num-input .el-input-number__increase) {
  background: rgba(255, 255, 255, 0.6) !important;
  border-color: rgba(203, 213, 225, 0.6) !important;
  color: #475569 !important;
  width: 26px !important;
  height: 28px !important;
  border-radius: 6px !important;
  transition: all 0.15s ease !important;
}
:deep(.macos-num-input .el-input-number__decrease:hover),
:deep(.macos-num-input .el-input-number__increase:hover) {
  background: #ffffff !important;
  color: #0284c7 !important;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.08);
}
:deep(.macos-num-input .el-input__inner) {
  font-family: var(--el-font-family-monospace, monospace) !important;
  font-size: 13px !important;
  font-weight: 700 !important;
  color: #0f172a !important;
  text-align: center !important;
}

:deep(.macos-country-select .el-select__wrapper) {
  background: rgba(255, 255, 255, 0.85) !important;
  border: 1.2px solid rgba(255, 255, 255, 0.95) !important;
  box-shadow: inset 0 1.5px 3px rgba(15, 23, 42, 0.06) !important;
  border-radius: 8px !important;
  height: 30px !important;
}

:deep(.el-form-item__label) {
  font-size: 11.5px !important;
  font-weight: 700 !important;
  color: #334155 !important;
  margin-bottom: 4px !important;
  line-height: 1.3 !important;
}
:deep(.el-form-item) {
  margin-bottom: 8px !important;
}
</style>
