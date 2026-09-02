<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
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
  Odometer,
  TrendCharts,
  Lightning,
  Opportunity,
  Connection,
  Timer,
  Histogram,
  Cpu,
  Refresh,
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

// ──────────── 2. 实时出号速率 (CPM) 与波形看板 ────────────
const velocity = computed(() => {
  return autoStatus.value.velocity || {
    cpm: 0,
    cpm_5m: 0,
    projected_hourly: 0,
    success_rate: 0,
    proxies_used: 0,
    history: [],
  }
})

// SVG 平滑贝塞尔曲线路径生成
const svgPath = computed(() => {
  const history = velocity.value.history || []
  if (!history.length) return { line: '', area: '', points: [] }
  const data = history.map((item) => (typeof item.cpm === 'number' ? item.cpm : 0))
  const w = 360
  const h = 80
  const pad = 10
  const maxVal = Math.max(...data, 10)
  const minVal = 0

  const pts = data.map((val, idx) => {
    const x = pad + (idx / Math.max(1, data.length - 1)) * (w - 2 * pad)
    const y = h - pad - ((val - minVal) / Math.max(1, maxVal - minVal)) * (h - 2 * pad)
    return { x: Math.round(x * 10) / 10, y: Math.round(y * 10) / 10, val }
  })

  if (pts.length === 1) {
    return {
      line: `M ${pts[0].x} ${pts[0].y} L ${w - pad} ${pts[0].y}`,
      area: `M ${pts[0].x} ${pts[0].y} L ${w - pad} ${pts[0].y} L ${w - pad} ${h} L ${pad} ${h} Z`,
      points: pts,
    }
  }

  let lineD = `M ${pts[0].x} ${pts[0].y}`
  for (let i = 1; i < pts.length; i++) {
    const prev = pts[i - 1]
    const curr = pts[i]
    const cp1x = prev.x + (curr.x - prev.x) / 2
    const cp1y = prev.y
    const cp2x = prev.x + (curr.x - prev.x) / 2
    const cp2y = curr.y
    lineD += ` C ${cp1x} ${cp1y}, ${cp2x} ${cp2y}, ${curr.x} ${curr.y}`
  }

  const areaD = `${lineD} L ${pts[pts.length - 1].x} ${h} L ${pts[0].x} ${h} Z`
  return { line: lineD, area: areaD, points: pts }
})

// ──────────── 3. 多 Worker 多核并发动态舰队监控 (Fleet HUD) ────────────
const PIPELINE_STEPS = [
  { index: 1, key: 'sentinel', label: 'PoW预算', fullLabel: '⚡ PoW 0ms预算', icon: '⚡' },
  { index: 2, key: 'otp', label: '取OTP', fullLabel: '📨 微软取OTP', icon: '📨' },
  { index: 3, key: 'password', label: '设密', fullLabel: '🔐 官方设密', icon: '🔐' },
  { index: 4, key: '2fa', label: '2FA', fullLabel: '🛡️ 2FA激活', icon: '🛡️' },
  { index: 5, key: 'database', label: '入库', fullLabel: '💾 资产入库', icon: '💾' },
]

const fleetList = computed(() => {
  if (Array.isArray(autoStatus.value.fleet) && autoStatus.value.fleet.length) {
    return autoStatus.value.fleet
  }
  if (Array.isArray(autoStatus.value.workers) && autoStatus.value.workers.length) {
    return autoStatus.value.workers
  }
  const conc = autoStatus.value.concurrency || form.value.autoConcurrency || 1
  return Array.from({ length: conc }, (_, i) => ({
    id: i,
    status: st.value === 'running' ? 'cooling' : st.value,
    email: '',
    proxy: '',
    country: form.value.autoProxyCountry || '',
    started_at: 0,
    elapsed: 0,
    phase: 'idle',
    phase_text: st.value === 'running' ? '准备就绪 / 领取下一个号...' : '空闲待命',
    percent: 0,
    step_index: 0,
    cycles: 0,
    last_error: '',
  }))
})

// 账号任务流水列表（从 autoStatus 中取得）
const taskList = computed(() => {
  return Array.isArray(autoStatus.value.tasks) ? autoStatus.value.tasks : []
})

// ──────────── 状态筛选与分页控制 ────────────
const filterStatus = ref('all') // 'all' | 'running' | 'done' | 'failed'
const currentPage = ref(1)
const pageSize = ref(20)

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
  if (!p) return '直连 (无代理)'
  try {
    const s = String(p)
    if (s.includes('@')) {
      const parts = s.split('@')
      const hostPort = parts[1] || ''
      return `...${hostPort}`
    }
    return s.slice(0, 24)
  } catch (_) {
    return '代理就绪'
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

onMounted(() => {
  tickerTimer = setInterval(() => {
    nowTs.value = Math.floor(Date.now() / 1000)
  }, 1000)

  statusPollTimer = setInterval(() => {
    if (st.value === 'running' || st.value === 'paused') {
      syncAutoStatus()
    }
  }, 2000)

  syncAutoStatus()
  loadPowSlots()
})

onUnmounted(() => {
  stopLogPolling()
  if (tickerTimer) clearInterval(tickerTimer)
  if (statusPollTimer) clearInterval(statusPollTimer)
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

    <!-- ════════════ 2. 顶部 KPI 指标大屏与实时速度波形看板 ════════════ -->
    <div class="autoloop-top-matrix">
      <!-- 6 大核心 KPI 矩阵 -->
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

        <!-- 代理池 -->
        <div class="kpi-card proxy-card" @click="router.push('/proxy')">
          <div class="kpi-info">
            <span class="kpi-title">代理池</span>
            <div class="kpi-num-row">
              <span class="kpi-num">{{ proxyCount }}</span>
              <span class="kpi-sub">节点可用 ›</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 实时速度与出号率波形大屏 (Velocity Sparkline HUD) -->
      <div class="velocity-hud-card">
        <div class="velocity-left">
          <div class="velocity-metric-item">
            <div class="metric-title-row">
              <el-icon class="icon-velocity"><Lightning /></el-icon>
              <span class="metric-title">实时出号速率 (CPM)</span>
            </div>
            <div class="metric-main-val">
              <span class="mono text-emerald">{{ velocity.cpm }}</span>
              <span class="unit-text">账号 / 分钟</span>
            </div>
          </div>

          <div class="velocity-metric-sub">
            <div class="sub-kpi">
              <span class="sub-label">📈 预计时产能:</span>
              <span class="sub-val mono text-primary">{{ velocity.projected_hourly }} 个/时</span>
            </div>
            <div class="sub-kpi">
              <span class="sub-label">🌐 代理轮询消耗:</span>
              <span class="sub-val mono text-amber">{{ velocity.proxies_used }} 节点</span>
            </div>
          </div>
        </div>

        <!-- 动态波形平滑曲线图 -->
        <div class="velocity-chart-wrap">
          <div class="chart-header">
            <span class="chart-title">出号速率平滑波形 (Real-time Velocity)</span>
            <span class="chart-tag">5m均速 {{ velocity.cpm_5m }} CPM</span>
          </div>
          <div class="svg-sparkline-box">
            <svg viewBox="0 0 360 80" class="sparkline-svg" preserveAspectRatio="none">
              <defs>
                <linearGradient id="velocityGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stop-color="#10b981" stop-opacity="0.35" />
                  <stop offset="100%" stop-color="#10b981" stop-opacity="0.0" />
                </linearGradient>
              </defs>
              <!-- 填充曲面 -->
              <path v-if="svgPath.area" :d="svgPath.area" fill="url(#velocityGrad)" />
              <!-- 曲线 -->
              <path
                v-if="svgPath.line"
                :d="svgPath.line"
                fill="none"
                stroke="#10b981"
                stroke-width="2.5"
                stroke-linecap="round"
              />
              <!-- 最新点发光点 -->
              <circle
                v-if="svgPath.points?.length"
                :cx="svgPath.points[svgPath.points.length - 1].x"
                :cy="svgPath.points[svgPath.points.length - 1].y"
                r="4"
                fill="#10b981"
                class="live-spark-dot"
              />
            </svg>
          </div>
        </div>
      </div>
    </div>

    <!-- ════════════ 3. 多 Worker 多核并发动态舰队监控大屏 (Fleet HUD) ════════════ -->
    <div class="macos-panel fleet-panel">
      <div class="fleet-panel-header">
        <div class="header-left">
          <span class="fleet-badge">FLEET HUD</span>
          <span class="panel-title">多 Worker 多核并发动态舰队监控大屏</span>
          <span class="fleet-count-tag">{{ fleetList.length }} 个并发 Worker 在线</span>
        </div>
        <div class="header-right">
          <!-- 一体化控制操作按钮组 -->
          <div class="control-actions">
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
          </div>
        </div>
      </div>

      <!-- 动态舰队网格卡片矩阵 -->
      <div class="fleet-grid">
        <div
          v-for="w in fleetList"
          :key="w.id"
          class="worker-hud-card"
          :class="{
            'is-running': w.status === 'running',
            'is-cooling': w.status === 'cooling',
            'is-stopped': w.status === 'stopped' || w.status === 'paused',
            'is-error': Boolean(w.last_error),
          }"
        >
          <!-- Worker 顶部状态条 -->
          <div class="worker-card-header">
            <div class="worker-id-wrap">
              <span class="worker-dot" :class="w.status"></span>
              <span class="worker-name">WORKER #{{ w.id + 1 }}</span>
            </div>
            <div class="worker-geo-wrap">
              <span v-if="w.country" class="worker-country-tag">
                {{ formatCountry(w.country) }}
              </span>
              <span class="worker-proxy-sub">{{ maskProxy(w.proxy) }}</span>
            </div>
          </div>

          <!-- Worker 核心内容：当前账号与耗时 -->
          <div class="worker-card-body">
            <div class="worker-email-row">
              <span v-if="w.email" class="worker-email mono" :title="w.email">
                {{ w.email }}
              </span>
              <span v-else class="worker-email-empty">
                {{ w.status === 'cooling' ? '⏳ 冷却间隙 / 准备领号' : '💤 待机空闲' }}
              </span>
              <button
                v-if="w.email"
                type="button"
                class="copy-btn-mini"
                title="复制邮箱"
                @click="copyText(w.email)"
              >
                <el-icon><CopyDocument /></el-icon>
              </button>
            </div>

            <!-- 耗时与产出 -->
            <div class="worker-meta-row">
              <div class="worker-timer-box">
                <span class="timer-label">单号耗时:</span>
                <span class="timer-val mono text-emerald">{{ w.elapsed > 0 ? `${w.elapsed}s` : '—' }}</span>
              </div>
              <div class="worker-cycles-box">
                <span class="cycles-label">累计产出:</span>
                <span class="cycles-val mono text-primary">{{ w.cycles || 0 }} 个</span>
              </div>
            </div>

            <!-- 五阶段微动画流水线 (5-Stage Step Pipeline) -->
            <div class="pipeline-track-box">
              <div class="pipeline-steps">
                <div
                  v-for="step in PIPELINE_STEPS"
                  :key="step.index"
                  class="step-node"
                  :class="{
                    'step-done': w.step_index > step.index || w.status === 'done',
                    'step-active': w.step_index === step.index && w.status === 'running',
                    'step-pending': w.step_index < step.index,
                  }"
                  :title="step.fullLabel"
                >
                  <span class="step-icon">{{ step.icon }}</span>
                  <span class="step-label">{{ step.label }}</span>
                </div>
              </div>

              <!-- 当前详细进度说明 -->
              <div class="step-current-desc">
                <span v-if="w.status === 'running'" class="pulse-ring-dot"></span>
                <span class="step-desc-text">{{ w.phase_text || '等待执行...' }}</span>
                <span v-if="w.percent" class="step-pct-mono">{{ w.percent }}%</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ════════════ 4. 参数控制调度卡片 (macOS 紧凑排布) ════════════ -->
    <div class="macos-panel config-panel">
      <div class="panel-header">
        <div class="panel-header-title">
          <span class="macos-pill-tag">BATCH</span>
          <span class="title">全自动批量参数调度</span>

          <!-- 顶部批次实时耗时与均速摘要胶囊 -->
          <div v-if="batchStartedAt" class="header-timing-pill" :class="{ 'header-timing-running': st === 'running' }">
            <span class="pill-dot" :class="{ 'pulse': st === 'running' }"></span>
            <span>🕒 开始: {{ formatClock(batchStartedAt) }}</span>
            <span class="pill-sep">|</span>
            <span v-if="st === 'running'">⏱️ 耗时: <strong>{{ formatDuration(batchElapsedSec) }}</strong></span>
            <span v-else>🏁 结束: {{ formatClock(batchFinishedAt) }} (总耗时 {{ formatDuration(batchElapsedSec) }})</span>
            <span v-if="batchAvgSpeed !== '—'" class="pill-sep">|</span>
            <span v-if="batchAvgSpeed !== '—'">⚡ 均速: {{ batchAvgSpeed }}</span>
          </div>
        </div>
      </div>

      <div class="panel-body">
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
                  <el-tooltip content="同时解算 sentinel PoW 的 node 进程数上限。网络并发再高，PoW 碰撞也会在这里排队，保护 CPU 不被打满降频。i5-13500H 建议 4~6；改完立即生效并持久保存，重启不丢。" placement="top">
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
                    <el-tooltip content="开启后新注册账号自动设置16位强随机登录密码并落盘保存到数据库" placement="top">
                      <el-icon class="info-ico"><QuestionFilled /></el-icon>
                    </el-tooltip>
                  </div>
                  <div class="switch-item">
                    <el-switch v-model="form.autoWant2fa" size="small" />
                    <span class="switch-label">自动绑2FA</span>
                    <el-tooltip content="每个账号注册成功后自动绑定 2FA 并将 secret 备份至数据库" placement="top">
                      <el-icon class="info-ico"><QuestionFilled /></el-icon>
                    </el-tooltip>
                  </div>
                </div>
              </el-form-item>
            </el-col>
          </el-row>
        </el-form>
      </div>
    </div>

    <!-- ════════════ 5. 核心主区域：每个账号一行的实时注册表格列表 ════════════ -->
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
          class="macos-table"
          :highlight-current-row="false"
        >
          <!-- 账号邮箱 -->
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

          <!-- Worker 归属 -->
          <el-table-column label="执行 Worker" width="120" align="center">
            <template #default="{ row }">
              <span class="worker-badge">
                <span class="worker-dot" :class="{ 'pulse-active': row.status === 'running' }"></span>
                Worker #{{ row.worker_id !== undefined ? row.worker_id + 1 : 1 }}
              </span>
            </template>
          </el-table-column>

          <!-- 注册进度与步骤 -->
          <el-table-column label="注册进度 / 步骤" min-width="200">
            <template #default="{ row }">
              <div v-if="row.status === 'running'" class="running-step-cell">
                <div class="step-label-row">
                  <span class="pulse-dot"></span>
                  <span class="step-text">{{ row.phase_text || '正在注册...' }}</span>
                </div>
                <div class="step-bar-wrap">
                  <div
                    class="step-bar-fill"
                    :style="{ width: (row.percent || 20) + '%' }"
                  ></div>
                </div>
              </div>
              <el-tag v-else-if="row.status === 'done'" type="success" size="small" effect="light" class="macos-tag">
                <el-icon class="status-ico"><Check /></el-icon>注册完成
              </el-tag>
              <el-tooltip v-else-if="row.status === 'failed'" :content="row.error || '未知错误'" placement="top">
                <el-tag type="danger" size="small" effect="light" class="macos-tag cursor-help">
                  <el-icon class="status-ico"><Close /></el-icon>{{ row.phase_text || '注册失败' }}
                </el-tag>
              </el-tooltip>
              <el-tag v-else type="info" size="small" effect="plain" class="macos-tag">
                排队中
              </el-tag>
            </template>
          </el-table-column>

          <!-- 出口国家 -->
          <el-table-column label="出口国家" width="125" align="center" show-overflow-tooltip>
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

          <!-- 耗时 -->
          <el-table-column label="耗时" width="85" align="right">
            <template #default="{ row }">
              <span class="mono hint" :style="{ color: row.status === 'running' ? 'var(--el-color-primary)' : '' }">
                {{ formatElapsed(row) }}
              </span>
            </template>
          </el-table-column>

          <!-- 启动时间 -->
          <el-table-column label="启动时间" width="135" align="center">
            <template #default="{ row }">
              <span class="mono hint">{{ fmtTime(row.started_at) }}</span>
            </template>
          </el-table-column>

          <!-- 操作 -->
          <el-table-column label="操作" width="85" fixed="right" align="center">
            <template #default="{ row }">
              <el-button
                size="small"
                text
                type="primary"
                plain
                class="macos-log-btn"
                @click="openTaskLog(row)"
              >
                <el-icon><Document /></el-icon>日志
              </el-button>
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
          :page-sizes="[20, 50, 100]"
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
  gap: 12px;
  overflow-y: auto;
  padding-bottom: 16px;
}

/* ──────────── 1. 智能风控预警与自动熔断退避横幅 ──────────── */
.risk-defense-banner {
  background: linear-gradient(135deg, rgba(239, 68, 68, 0.12) 0%, rgba(245, 158, 11, 0.12) 100%);
  border: 1px solid rgba(239, 68, 68, 0.35);
  border-radius: 12px;
  padding: 12px 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  box-shadow: 0 4px 16px rgba(239, 68, 68, 0.08);
}

.risk-banner-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.risk-icon-pulse {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: rgba(239, 68, 68, 0.2);
  color: #ef4444;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  animation: pulse-ring 1.8s infinite ease-in-out;
}

.risk-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.risk-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.risk-title {
  font-size: 13px;
  font-weight: 700;
  color: #ef4444;
}

.risk-desc {
  font-size: 11.5px;
  color: var(--el-text-color-secondary);
  line-height: 1.4;
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
  font-size: 12px;
  color: #f59e0b;
  background: rgba(245, 158, 11, 0.15);
  border: 1px solid rgba(245, 158, 11, 0.4);
  padding: 3px 10px;
  border-radius: 14px;
}

.cooldown-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #f59e0b;
}

/* ──────────── 2. 顶部大屏矩阵 (KPI Cards + Velocity HUD) ──────────── */
.autoloop-top-matrix {
  display: grid;
  grid-template-columns: 1.4fr 1.1fr;
  gap: 12px;
}

@media (max-width: 1200px) {
  .autoloop-top-matrix {
    grid-template-columns: 1fr;
  }
}

.kpi-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
}

.kpi-card {
  background: var(--app-window-bg);
  border: 1px solid var(--app-border);
  border-radius: 10px;
  padding: 10px 14px;
  display: flex;
  align-items: center;
  gap: 10px;
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
}

.kpi-title {
  font-size: 11px;
  color: var(--app-text-secondary);
  font-weight: 500;
}

.kpi-num {
  font-size: 17px;
  font-weight: 700;
  color: var(--app-title);
  font-family: var(--font-mono, monospace);
}

.kpi-num-row {
  display: flex;
  align-items: baseline;
  gap: 4px;
}

.kpi-sub {
  font-size: 11px;
  color: var(--app-text-secondary);
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
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #007aff;
  box-shadow: 0 0 6px #007aff;
}

.timing-sub-row {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 9.5px;
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

.proxy-card {
  cursor: pointer;
}

.proxy-card:hover {
  border-color: var(--el-color-primary-light-5);
}

/* ──────────── 速度与出号率波形卡片 ──────────── */
.velocity-hud-card {
  background: var(--app-window-bg);
  border: 1px solid var(--app-border);
  border-radius: 10px;
  padding: 10px 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  box-shadow: var(--app-shadow-sm);
}

.velocity-left {
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex-shrink: 0;
}

.metric-title-row {
  display: flex;
  align-items: center;
  gap: 5px;
}

.icon-velocity {
  color: #10b981;
  font-size: 14px;
}

.metric-title {
  font-size: 11px;
  color: var(--app-text-secondary);
  font-weight: 600;
}

.metric-main-val {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 22px;
  font-weight: 800;
  line-height: 1.1;
}

.unit-text {
  font-size: 11px;
  font-weight: 400;
  color: var(--el-text-color-secondary);
}

.velocity-metric-sub {
  display: flex;
  flex-direction: column;
  gap: 3px;
  font-size: 11px;
}

.sub-kpi {
  display: flex;
  align-items: center;
  gap: 6px;
}

.sub-label {
  color: var(--el-text-color-secondary);
}

.sub-val {
  font-weight: 700;
}

.velocity-chart-wrap {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.chart-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.chart-title {
  font-size: 10.5px;
  color: var(--el-text-color-secondary);
  font-weight: 600;
}

.chart-tag {
  font-size: 10px;
  color: #10b981;
  font-family: var(--font-mono, monospace);
  font-weight: 700;
}

.svg-sparkline-box {
  width: 100%;
  height: 52px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  overflow: hidden;
}

.sparkline-svg {
  width: 100%;
  height: 100%;
  display: block;
}

.live-spark-dot {
  animation: pulse-ring 1.5s infinite;
}

/* ──────────── 3. 多 Worker 动态舰队监控大屏 (Fleet HUD) ──────────── */
.fleet-panel {
  background: var(--app-window-bg);
  border: 1px solid var(--app-border);
  border-radius: 12px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  box-shadow: var(--app-shadow-sm);
}

.fleet-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--el-border-color-lighter);
  padding-bottom: 10px;
}

.fleet-panel-header .header-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.fleet-badge {
  font-size: 10px;
  font-weight: 700;
  color: #007aff;
  background: rgba(0, 122, 255, 0.1);
  border: 1px solid rgba(0, 122, 255, 0.3);
  padding: 1px 6px;
  border-radius: 4px;
  letter-spacing: 0.5px;
}

.panel-title {
  font-size: 13.5px;
  font-weight: 700;
  color: var(--app-title);
}

.fleet-count-tag {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.fleet-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 10px;
}

.worker-hud-card {
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  transition: all 0.2s ease;
}

.worker-hud-card.is-running {
  border-color: rgba(16, 185, 129, 0.4);
  box-shadow: 0 2px 10px rgba(16, 185, 129, 0.08);
}

.worker-hud-card.is-cooling {
  border-color: rgba(245, 158, 11, 0.35);
}

.worker-hud-card.is-error {
  border-color: rgba(239, 68, 68, 0.45);
  animation: flash-border 2s infinite ease-in-out;
}

.worker-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.worker-id-wrap {
  display: flex;
  align-items: center;
  gap: 6px;
}

.worker-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #94a3b8;
}

.worker-dot.running {
  background: #10b981;
  box-shadow: 0 0 6px #10b981;
}

.worker-dot.cooling {
  background: #f59e0b;
}

.worker-dot.stopped {
  background: #94a3b8;
}

.worker-name {
  font-size: 11.5px;
  font-weight: 700;
  color: var(--app-title);
  font-family: var(--font-mono, monospace);
}

.worker-geo-wrap {
  display: flex;
  align-items: center;
  gap: 6px;
}

.worker-country-tag {
  font-size: 10.5px;
  color: var(--el-color-primary);
  font-weight: 600;
}

.worker-proxy-sub {
  font-size: 10px;
  color: var(--el-text-color-secondary);
  font-family: var(--font-mono, monospace);
}

.worker-card-body {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.worker-email-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--app-window-bg);
  border: 1px solid var(--app-border);
  border-radius: 6px;
  padding: 4px 8px;
}

.worker-email {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--el-color-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.worker-email-empty {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.copy-btn-mini {
  background: none;
  border: none;
  cursor: pointer;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  padding: 0 2px;
}

.copy-btn-mini:hover {
  color: var(--el-color-primary);
}

.worker-meta-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 11px;
}

.worker-timer-box, .worker-cycles-box {
  display: flex;
  align-items: center;
  gap: 4px;
}

.timer-label, .cycles-label {
  color: var(--el-text-color-secondary);
}

.timer-val {
  font-weight: 700;
}

.cycles-val {
  font-weight: 700;
}

/* 五阶段微动画流水线 */
.pipeline-track-box {
  background: var(--app-window-bg);
  border: 1px solid var(--app-border);
  border-radius: 6px;
  padding: 6px 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.pipeline-steps {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 4px;
}

.step-node {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  padding: 3px 2px;
  border-radius: 4px;
  font-size: 9.5px;
  background: var(--el-fill-color-light);
  border: 1px solid transparent;
  transition: all 0.2s ease;
}

.step-node.step-done {
  background: rgba(16, 185, 129, 0.12);
  border-color: rgba(16, 185, 129, 0.3);
  color: #10b981;
}

.step-node.step-active {
  background: rgba(0, 122, 255, 0.16);
  border-color: #007aff;
  color: #007aff;
  animation: pulse-step 1.2s infinite ease-in-out;
}

.step-node.step-pending {
  opacity: 0.45;
}

.step-icon {
  font-size: 11px;
}

.step-label {
  font-size: 9px;
  white-space: nowrap;
  font-weight: 600;
}

.step-current-desc {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 10.5px;
  color: var(--el-text-color-secondary);
  margin-top: 2px;
}

.pulse-ring-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #007aff;
  margin-right: 4px;
}

.step-desc-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}

.step-pct-mono {
  font-family: var(--font-mono, monospace);
  font-weight: 700;
  color: var(--el-color-primary);
}

/* ──────────── 4. 参数配置卡片 ──────────── */
.macos-panel {
  background: var(--app-window-bg);
  border: 1px solid var(--app-border);
  border-radius: 12px;
  box-shadow: var(--app-shadow-sm);
}

.config-panel {
  padding: 12px 16px;
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.panel-header-title {
  display: flex;
  align-items: center;
  gap: 8px;
}

.macos-pill-tag {
  font-size: 10px;
  font-weight: 700;
  color: #10b981;
  background: rgba(16, 185, 129, 0.1);
  border: 1px solid rgba(16, 185, 129, 0.3);
  padding: 1px 6px;
  border-radius: 4px;
}

.panel-header-title .title {
  font-size: 13px;
  font-weight: 700;
  color: var(--app-title);
}

.header-timing-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--app-border);
  padding: 2px 8px;
  border-radius: 12px;
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

.mail-source-selector-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}

.mail-source-badge-tip {
  font-size: 11.5px;
  color: var(--el-text-color-secondary);
}

.feature-switches {
  display: flex;
  align-items: center;
  gap: 14px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--app-border);
  padding: 4px 10px;
  border-radius: 6px;
}

.switch-item {
  display: flex;
  align-items: center;
  gap: 6px;
}

.switch-label {
  font-size: 11.5px;
  color: var(--app-title);
}

/* ──────────── 5. 流水表格 ──────────── */
.table-panel {
  flex: 1;
  min-height: 280px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.table-panel-header {
  padding: 10px 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--app-border);
  background: var(--el-fill-color-light);
}

.table-panel-header .header-left {
  display: flex;
  align-items: center;
  gap: 12px;
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
  font-size: 11px;
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
  padding: 6px 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-top: 1px solid var(--app-border);
  background: var(--el-fill-color-light);
}

.page-tip {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

/* 表格内部单元格 */
.copy-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  cursor: pointer;
}

.worker-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  color: var(--app-title);
  background: var(--el-fill-color-light);
  border: 1px solid var(--app-border);
  padding: 1px 6px;
  border-radius: 4px;
}

.worker-dot.pulse-active {
  background: #10b981;
  animation: pulse-ring 1.5s infinite;
}

.running-step-cell {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.step-label-row {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
}

.pulse-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #007aff;
}

.step-bar-wrap {
  height: 3px;
  background: var(--el-fill-color);
  border-radius: 99px;
  overflow: hidden;
}

.step-bar-fill {
  height: 100%;
  background: #007aff;
  transition: width 0.3s ease;
}

.geo-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--app-border);
  padding: 1px 6px;
  border-radius: 4px;
}

.geo-hot {
  border-color: rgba(245, 158, 11, 0.4);
  background: rgba(245, 158, 11, 0.08);
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

@keyframes pulse-step {
  0% { box-shadow: 0 0 0 rgba(0, 122, 255, 0.4); }
  50% { box-shadow: 0 0 8px rgba(0, 122, 255, 0.8); }
  100% { box-shadow: 0 0 0 rgba(0, 122, 255, 0.4); }
}

@keyframes flash-border {
  0% { border-color: rgba(239, 68, 68, 0.3); }
  50% { border-color: rgba(239, 68, 68, 0.9); box-shadow: 0 0 10px rgba(239, 68, 68, 0.3); }
  100% { border-color: rgba(239, 68, 68, 0.3); }
}
</style>
