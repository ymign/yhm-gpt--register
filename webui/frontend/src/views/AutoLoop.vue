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
} from '@element-plus/icons-vue'
import {
  autoStart,
  autoPause,
  autoResume,
  autoStop,
  autoStatus as getAutoStatus,
  getRunLog,
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

// 账号任务流水列表（从 autoStatus 中取得）
const taskList = computed(() => {
  return Array.isArray(autoStatus.value.tasks) ? autoStatus.value.tasks : []
})

// ──────────── 状态筛选与分页控制（彻底消除卡顿） ────────────
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

// 筛选变更时自动重置回第 1 页
watch(filterStatus, () => {
  currentPage.value = 1
})

// ──────────── 本地实时走秒与状态同步 ────────────
const nowTs = ref(Math.floor(Date.now() / 1000))
let tickerTimer = null
let statusPollTimer = null

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

  // 如果该任务还在 running，开启轮询实时刷新
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
    // 刷新日志
    try {
      const res = await getRunLog(runId)
      logLines.value = res.lines || (res.text ? res.text.split('\n') : [])
      await nextTick()
      scrollLogModalToBottom()
    } catch (_) {}

    // 如果任务已结束，停止轮询
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
  logLines.value = []
}

function scrollLogModalToBottom() {
  const el = document.getElementById('task-log-terminal')
  if (el) el.scrollTop = el.scrollHeight
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
      want_2fa: form.value.autoWant2fa,
      want_password: form.value.autoWantPassword,
    })
    ElMessage.success('全自动批量跑号已启动')
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

onMounted(() => {
  tickerTimer = setInterval(() => {
    nowTs.value = Math.floor(Date.now() / 1000)
  }, 1000)

  statusPollTimer = setInterval(() => {
    if (st.value === 'running' || st.value === 'paused') {
      syncAutoStatus()
    }
  }, 3000)

  syncAutoStatus()
})

onUnmounted(() => {
  stopLogPolling()
  if (tickerTimer) {
    clearInterval(tickerTimer)
    tickerTimer = null
  }
  if (statusPollTimer) {
    clearInterval(statusPollTimer)
    statusPollTimer = null
  }
})
</script>

<template>
  <div class="autoloop-page">
    <!-- 顶部 KPI 指标网格 (苹果风格卡片) -->
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

      <!-- 成功注册卡片 -->
      <div class="kpi-card hit-card">
        <div class="kpi-info">
          <span class="kpi-title">成功注册</span>
          <div class="kpi-num-row">
            <span class="kpi-num text-success">{{ autoStatus.registered_ok || 0 }}</span>
            <span v-if="autoStatus.target_count" class="kpi-sub">/ 目标 {{ autoStatus.target_count }}</span>
          </div>
        </div>
      </div>

      <!-- 失败卡片 -->
      <div class="kpi-card err-card">
        <div class="kpi-info">
          <span class="kpi-title">注册失败</span>
          <span class="kpi-num text-danger">{{ autoStatus.registered_fail || 0 }}</span>
        </div>
      </div>

      <!-- 成功率卡片 -->
      <div class="kpi-card">
        <div class="kpi-info">
          <span class="kpi-title">成功率</span>
          <span class="kpi-num">{{ successRate }}</span>
        </div>
      </div>

      <!-- 代理池卡片 -->
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

    <!-- 参数控制调度卡片 (macOS 紧凑排布) -->
    <div class="macos-panel config-panel">
      <div class="panel-header">
        <div class="panel-header-title">
          <span class="macos-pill-tag">BATCH</span>
          <span class="title">全自动批量参数调度</span>
        </div>

        <!-- 一体化控制操作按钮组（含停止任务按钮） -->
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

      <div class="panel-body">
        <el-form size="small" label-position="top">
          <el-row :gutter="12" class="config-row">
            <el-col :xs="12" :sm="6" :md="3">
              <el-form-item label="并发数 (Workers)">
                <el-input-number v-model="form.autoConcurrency" :min="1" :max="20" class="macos-num-input" />
              </el-form-item>
            </el-col>
            <el-col :xs="12" :sm="6" :md="3">
              <el-form-item label="冷却间隔 (秒)">
                <el-input-number v-model="form.autoCoolDown" :min="0" :max="120" class="macos-num-input" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :sm="12" :md="5">
              <el-form-item label="代理目标国家 (自动重写代理与时区)">
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
              <el-form-item label="OTP 超时 (秒)">
                <el-input-number v-model="form.otpTimeout" :min="10" :max="600" class="macos-num-input" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :sm="12" :md="7">
              <el-form-item label="自动化附加功能">
                <div class="feature-switches">
                  <div class="switch-item">
                    <el-switch v-model="form.autoWantPassword" size="small" />
                    <span class="switch-label">自动设置密码</span>
                    <el-tooltip content="开启后新注册账号自动设置16位强随机登录密码并落盘保存到数据库" placement="top">
                      <el-icon class="info-ico"><QuestionFilled /></el-icon>
                    </el-tooltip>
                  </div>
                  <div class="switch-item">
                    <el-switch v-model="form.autoWant2fa" size="small" />
                    <span class="switch-label">自动绑定 2FA</span>
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

    <!-- 核心主区域：每个账号一行的实时注册表格列表 (苹果风格 + 内部自适应滚动) -->
    <div class="macos-panel table-panel">
      <div class="table-panel-header">
        <div class="header-left">
          <div class="panel-header-title">
            <span class="dot-live"></span>
            <span class="title">账号注册实时监控列表</span>
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
          <el-table-column label="出口国家" width="135" align="center" show-overflow-tooltip>
            <template #default="{ row }">
              <span
                v-if="row.reg_country"
                class="geo-badge"
                :class="{ 'geo-hot': ['JP', 'BR', 'VN', 'DE', 'GB', 'PL', 'ES', 'AR', 'TH'].includes(row.reg_country?.toUpperCase()) }"
              >
                <span class="geo-country">{{ formatCountry(row.reg_country) }}</span>
              </span>
              <span v-else-if="row.status === 'running'" class="hint">匹配中...</span>
              <span v-else class="hint">—</span>
            </template>
          </el-table-column>

          <!-- 耗时 -->
          <el-table-column label="耗时" width="85" align="right">
            <template #default="{ row }">
              <span class="mono elapsed-badge" :class="{ 'elapsed-running': row.status === 'running' }">
                {{ formatElapsed(row) }}
              </span>
            </template>
          </el-table-column>

          <!-- 启动时间 -->
          <el-table-column label="启动时间" width="140" align="center">
            <template #default="{ row }">
              <span class="mono-date">{{ row.started_at ? fmtTime(row.started_at) : '—' }}</span>
            </template>
          </el-table-column>

          <!-- 操作栏：详细日志 -->
          <el-table-column label="操作" width="100" fixed="right" align="center">
            <template #default="{ row }">
              <el-button
                size="small"
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
  gap: 10px;
  overflow: hidden;
}

/* ──────────── 顶部 KPI 指标网格 ──────────── */
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 10px;
  flex-shrink: 0;
}
@media (max-width: 900px) {
  .kpi-grid {
    grid-template-columns: repeat(3, 1fr);
  }
}

.kpi-card {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  padding: 10px 14px;
  display: flex;
  align-items: center;
  gap: 12px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.03);
  transition: transform 0.15s, border-color 0.15s;
}
.kpi-card:hover {
  border-color: var(--el-border-color);
}
.kpi-card.proxy-card {
  cursor: pointer;
}
.kpi-card.proxy-card:hover {
  background: var(--el-fill-color-light);
}

.kpi-icon-dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: var(--el-color-info-light-3);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.state-running .kpi-icon-dot {
  background: #10b981;
}
.state-paused .kpi-icon-dot {
  background: #f59e0b;
}

.live-pulse {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #fff;
}
.state-running .live-pulse {
  animation: pulse-ring 1.4s cubic-bezier(0.4, 0, 0.6, 1) infinite;
}

@keyframes pulse-ring {
  0% { transform: scale(0.9); opacity: 1; }
  50% { transform: scale(1.6); opacity: 0.4; }
  100% { transform: scale(0.9); opacity: 1; }
}

.kpi-info {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.kpi-title {
  font-size: 11.5px;
  color: var(--el-text-color-secondary);
  margin-bottom: 2px;
}
.kpi-num-row {
  display: flex;
  align-items: baseline;
  gap: 4px;
}
.kpi-num {
  font-size: 18px;
  font-weight: 700;
  font-family: var(--el-font-family-monospace, monospace);
  color: var(--el-text-color-primary);
  line-height: 1.1;
}
.kpi-sub {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}
.text-success { color: #10b981; }
.text-danger { color: var(--el-color-danger); }

/* ──────────── 参数控制面板 (macOS 质感) ──────────── */
.macos-panel {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.03);
  overflow: hidden;
}

.config-panel {
  flex-shrink: 0;
}

.panel-header {
  padding: 8px 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--el-border-color-lighter);
  background: var(--el-fill-color-blank);
}
.panel-header-title {
  display: flex;
  align-items: center;
  gap: 8px;
}
.macos-pill-tag {
  background: linear-gradient(135deg, var(--el-color-primary), #1d4fc4);
  color: #fff;
  font-size: 10px;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: 4px;
  letter-spacing: 0.5px;
}
.panel-header-title .title {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.control-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.start-btn {
  border-radius: 6px;
  font-size: 12px;
  font-weight: 600;
  background: linear-gradient(135deg, #10b981, #059669);
  border: none;
  box-shadow: 0 2px 6px rgba(16, 185, 129, 0.25);
}
.start-btn:hover:not(:disabled) {
  background: linear-gradient(135deg, #059669, #047857);
}

.action-btn-group {
  display: inline-flex;
  background: var(--el-fill-color-light);
  padding: 2px;
  border-radius: 6px;
  border: 1px solid var(--el-border-color-lighter);
}
.action-btn-group :deep(.el-button) {
  margin: 0;
  border: none;
  background: transparent;
  padding: 5px 8px;
  height: 24px;
  font-size: 11.5px;
  border-radius: 4px;
}
.action-btn-group :deep(.el-button:hover:not(:disabled)) {
  background: var(--el-bg-color);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
}

.panel-body {
  padding: 10px 14px;
}
.config-row :deep(.el-form-item) {
  margin-bottom: 0;
}
.config-row :deep(.el-form-item__label) {
  font-size: 11.5px;
  color: var(--el-text-color-secondary);
  padding-bottom: 2px;
}
.macos-num-input {
  width: 100%;
}
:deep(.macos-num-input .el-input__wrapper) {
  border-radius: 6px;
}

.feature-switches {
  display: flex;
  align-items: center;
  gap: 12px;
  height: 28px;
  flex-wrap: wrap;
}
.switch-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--el-text-color-regular);
}
.info-ico {
  color: var(--el-text-color-secondary);
  cursor: help;
  font-size: 13px;
}

/* ──────────── 核心主区域：账号列表表格 ──────────── */
.table-panel {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.table-panel-header {
  padding: 8px 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--el-fill-color-blank);
  border-bottom: 1px solid var(--el-border-color-lighter);
  flex-shrink: 0;
  gap: 12px;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}
.panel-header-title {
  display: flex;
  align-items: center;
  gap: 8px;
}
.dot-live {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--el-color-primary);
}
.badge-total {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  background: var(--el-fill-color-light);
  padding: 1px 6px;
  border-radius: 10px;
}

/* ──────────── 状态筛选胶囊 ──────────── */
.filter-capsules {
  display: inline-flex;
  align-items: center;
  background: var(--el-fill-color-light);
  padding: 2px;
  border-radius: 6px;
  border: 1px solid var(--el-border-color-lighter);
  gap: 2px;
}
.filter-pill {
  border: none;
  background: transparent;
  padding: 2px 8px;
  font-size: 11px;
  border-radius: 4px;
  color: var(--el-text-color-secondary);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  transition: all 0.15s ease;
  line-height: 18px;
}
.filter-pill:hover {
  color: var(--el-text-color-primary);
  background: rgba(0, 0, 0, 0.04);
}
.filter-pill.active {
  background: var(--el-bg-color);
  color: var(--el-text-color-primary);
  font-weight: 600;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
}
.pill-cnt {
  font-size: 10px;
  opacity: 0.8;
  background: var(--el-fill-color);
  padding: 0 4px;
  border-radius: 8px;
}
.filter-pill.active .pill-cnt {
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}
.dot-pill {
  width: 5px;
  height: 5px;
  border-radius: 50%;
}
.dot-running { background: #3b82f6; }
.dot-done { background: #10b981; }
.dot-failed { background: #ef4444; }

.last-msg-hint {
  font-size: 11.5px;
  color: var(--el-text-color-secondary);
  max-width: 400px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ──────────── 底部分页栏 ──────────── */
.table-pagination-bar {
  padding: 6px 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--el-fill-color-blank);
  border-top: 1px solid var(--el-border-color-lighter);
  flex-shrink: 0;
}
.page-tip {
  font-size: 11.5px;
  color: var(--el-text-color-secondary);
}
:deep(.table-pagination-bar .el-pagination) {
  margin: 0;
  font-weight: normal;
}
:deep(.table-pagination-bar .el-pagination button),
:deep(.table-pagination-bar .el-pagination .el-pager li) {
  min-width: 24px;
  height: 24px;
  line-height: 24px;
  font-size: 11.5px;
}

.table-container {
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

.worker-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  padding: 1px 6px;
  border-radius: 10px;
  font-size: 11px;
  color: var(--el-text-color-regular);
}
.worker-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #94a3b8;
}
.worker-dot.pulse-active {
  background: #10b981;
  animation: pulse-ring 1.3s infinite;
}

/* 运行中步骤单元格 */
.running-step-cell {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-width: 220px;
}
.step-label-row {
  display: flex;
  align-items: center;
  gap: 6px;
}
.step-text {
  font-size: 11.5px;
  font-weight: 600;
  color: #d97706;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.step-bar-wrap {
  width: 100%;
  height: 4px;
  background: var(--el-fill-color-darker, #e2e8f0);
  border-radius: 2px;
  overflow: hidden;
}
.step-bar-fill {
  height: 100%;
  background: linear-gradient(90deg, #f59e0b, #10b981);
  border-radius: 2px;
  transition: width 0.3s ease;
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

.status-ico {
  margin-right: 3px;
  font-size: 11px;
}

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

.macos-country-select {
  width: 100%;
}
:deep(.macos-country-select .el-input__wrapper) {
  border-radius: 6px;
}

.macos-tag-btn.ip-btn {
  font-family: var(--el-font-family-monospace, monospace);
  font-size: 11.5px;
}
.elapsed-badge {
  font-size: 11.5px;
  font-family: var(--el-font-family-monospace, monospace);
  color: var(--el-text-color-regular);
}
.elapsed-running {
  color: #10b981;
  font-weight: 700;
}

.mono-date {
  font-family: var(--el-font-family-monospace, monospace);
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.macos-log-btn {
  border-radius: 5px;
  font-size: 11.5px;
  padding: 2px 8px;
  height: 24px;
}

/* ──────────── 单账号独立日志终端弹窗 ──────────── */
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
.running-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: #4ade80;
}

.modal-terminal-wrap {
  height: 460px;
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

.terminal-line {
  margin-bottom: 2px;
}
.terminal-line.line-ok { color: #4ade80; font-weight: 500; }
.terminal-line.line-err { color: #f87171; }
.terminal-line.line-warn { color: #fbbf24; }
.terminal-line.line-info { color: #60a5fa; }
.terminal-empty {
  color: #6b7280;
  font-style: italic;
  padding: 20px 0;
  text-align: center;
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
</style>
