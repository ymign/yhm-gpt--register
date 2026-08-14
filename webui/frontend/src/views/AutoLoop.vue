<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { ElMessage } from 'element-plus'
import { autoStart, autoPause, autoResume, autoStop } from '@/api/register'
import { useFormStore, proxyText } from '@/stores/form'
import { useProxyStore } from '@/stores/proxy'
import { useRuntimeStore } from '@/stores/runtime'

const router = useRouter()
const { form } = storeToRefs(useFormStore())
const proxyStore = useProxyStore()
const { count: proxyCount } = storeToRefs(proxyStore)
const runtime = useRuntimeStore()
const { autoStatus, logs } = storeToRefs(runtime)

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

const workers = computed(() => Array.isArray(autoStatus.value.workers) ? autoStatus.value.workers : [])

const successRate = computed(() => {
  const ok = autoStatus.value.registered_ok || 0
  const fail = autoStatus.value.registered_fail || 0
  const total = ok + fail
  if (total === 0) return '—'
  return `${Math.round((ok / total) * 100)}%`
})

function getLogClass(line) {
  if (!line) return ''
  const t = typeof line === 'string' ? line : line.text || ''
  if (t.includes('完成') || t.includes('成功') || t.includes('2FA 绑定成功')) return 'log-ok'
  if (t.includes('失败') || t.includes('ERROR') || t.includes('Exception')) return 'log-err'
  if (t.includes('警告') || t.includes('WARNING')) return 'log-warn'
  if (t.includes('[register]') || t.includes('[auto]')) return 'log-event'
  return ''
}

async function start() {
  try {
    await autoStart({
      proxy: proxyText(form.value),
      proxy_pool: proxyStore.text,
      concurrency: parseInt(form.value.autoConcurrency, 10) || 1,
      otp_timeout: parseInt(form.value.otpTimeout, 10) || 10,
      want_access_token: true,
      want_session_token: true,
      want_refresh_token: true,
      cool_down_seconds: parseFloat(form.value.autoCoolDown) || 0,
      target_count: parseInt(form.value.autoTargetCount, 10) || 0,
      want_2fa: form.value.autoWant2fa,
    })
    ElMessage.success('全自动批量跑号已启动')
  } catch (e) {
    ElMessage.error('启动失败: ' + e.message)
  }
}

async function call(fn, name) {
  try {
    await fn()
    ElMessage.success(name + ' 成功')
  } catch (e) {
    ElMessage.error(name + ' 失败: ' + e.message)
  }
}
</script>

<template>
  <div class="autoloop-page">
    <!-- 顶部状态看板 + 指标卡片 (macOS 信息密集型) -->
    <div class="kpi-grid">
      <!-- 状态卡片 -->
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

    <!-- 紧凑参数控制面板 (macOS 风格) -->
    <div class="macos-panel config-panel">
      <div class="panel-header">
        <div class="panel-header-title">
          <span class="macos-pill-tag">BATCH</span>
          <span class="title">全自动批量参数调度</span>
        </div>

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
              <el-icon><SwitchButton /></el-icon>停止
            </el-button>
          </div>
        </div>
      </div>

      <div class="panel-body">
        <el-form size="small" label-position="top">
          <el-row :gutter="12" class="config-row">
            <el-col :xs="12" :sm="6" :md="4">
              <el-form-item label="并发数 (Workers)">
                <el-input-number v-model="form.autoConcurrency" :min="1" :max="20" class="macos-num-input" />
              </el-form-item>
            </el-col>
            <el-col :xs="12" :sm="6" :md="4">
              <el-form-item label="冷却间隔 (秒)">
                <el-input-number v-model="form.autoCoolDown" :min="0" :max="120" class="macos-num-input" />
              </el-form-item>
            </el-col>
            <el-col :xs="12" :sm="6" :md="4">
              <el-form-item label="目标数量 (0=不限)">
                <el-input-number v-model="form.autoTargetCount" :min="0" :max="100000" class="macos-num-input" />
              </el-form-item>
            </el-col>
            <el-col :xs="12" :sm="6" :md="4">
              <el-form-item label="OTP 超时 (秒)">
                <el-input-number v-model="form.otpTimeout" :min="10" :max="600" class="macos-num-input" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :sm="12" :md="8">
              <el-form-item label="自动化附加功能">
                <div class="feature-switches">
                  <div class="switch-item">
                    <el-switch v-model="form.autoWant2fa" size="small" />
                    <span class="switch-label">自动绑定 2FA (TOTP)</span>
                    <el-tooltip content="每个账号注册成功后自动绑定 2FA 并将 secret 备份至数据库" placement="top">
                      <el-icon class="info-ico"><QuestionFilled /></el-icon>
                    </el-tooltip>
                  </div>
                </div>
              </el-form-item>
            </el-col>
          </el-row>
        </el-form>

        <!-- 活跃 Workers 实时胶囊矩阵 -->
        <div v-if="workers.length" class="workers-matrix">
          <div class="workers-label">正在活跃运行的 Worker:</div>
          <div class="worker-chips">
            <div v-for="w in workers" :key="w.id" class="worker-chip">
              <span class="chip-pulse"></span>
              <span class="chip-id">Worker #{{ w.id }}</span>
              <span class="chip-email">{{ w.email }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 底部 macOS 终端风格全屏实时日志 (占据剩下全部视口高度) -->
    <div class="macos-panel terminal-panel">
      <div class="terminal-header">
        <div class="window-dots">
          <span class="dot red"></span>
          <span class="dot yellow"></span>
          <span class="dot green"></span>
        </div>
        <span class="terminal-title">全自动注册实时流水日志 ({{ logs.length }} 行)</span>
        <div class="terminal-right-tools">
          <span v-if="autoStatus.last_message" class="last-msg-hint">
            {{ autoStatus.last_message }}
          </span>
          <el-button size="small" text class="clear-btn" @click="runtime.clearLogs">清屏</el-button>
        </div>
      </div>

      <div class="terminal-body">
        <div v-for="l in logs" :key="l.id" class="terminal-line" :class="getLogClass(l)">
          <span class="line-time">{{ l.time || '' }}</span>
          <span class="line-text">{{ typeof l === 'string' ? l : l.text }}</span>
        </div>
        <div v-if="!logs.length" class="terminal-empty">
          <div class="empty-ico"><el-icon :size="24"><Monitor /></el-icon></div>
          <div>等待全自动批量任务启动并输出日志...</div>
        </div>
      </div>
    </div>
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

/* ──────────── 顶部 KPI 指标网格 (苹果卡片质感) ──────────── */
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
  height: 28px;
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

/* Workers 实时胶囊矩阵 */
.workers-matrix {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed var(--el-border-color-lighter);
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.workers-label {
  font-size: 11.5px;
  color: var(--el-text-color-secondary);
}
.worker-chips {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.worker-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 8px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 12px;
  font-size: 11.5px;
}
.chip-pulse {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #10b981;
  animation: pulse-ring 1.2s infinite;
}
.chip-id {
  font-weight: 600;
  color: var(--el-color-primary);
}
.chip-email {
  font-family: var(--el-font-family-monospace, monospace);
  color: var(--el-text-color-regular);
}

/* ──────────── 底部终端窗口 (macOS Terminal) ──────────── */
.terminal-panel {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: #141418;
  border: 1px solid #272730;
  overflow: hidden;
}

.terminal-header {
  padding: 7px 12px;
  background: #1e1e24;
  border-bottom: 1px solid #2a2a34;
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-shrink: 0;
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

.terminal-title {
  font-size: 11.5px;
  color: #94a3b8;
  font-family: var(--el-font-family-monospace, monospace);
}
.terminal-right-tools {
  display: flex;
  align-items: center;
  gap: 12px;
}
.last-msg-hint {
  font-size: 11px;
  color: #64748b;
  max-width: 360px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.clear-btn {
  font-size: 11px;
  color: #94a3b8;
  padding: 0 4px;
  height: 20px;
}

.terminal-body {
  flex: 1;
  min-height: 0;
  padding: 10px 14px;
  overflow-y: auto;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, Courier, monospace;
  font-size: 12px;
  line-height: 1.55;
  color: #d1d5db;
  word-break: break-all;
  white-space: pre-wrap;
}

.terminal-line {
  margin-bottom: 2px;
}
.terminal-line.log-ok { color: #4ade80; font-weight: 500; }
.terminal-line.log-err { color: #f87171; }
.terminal-line.log-warn { color: #fbbf24; }
.terminal-line.log-event { color: #60a5fa; }

.terminal-empty {
  text-align: center;
  color: #64748b;
  margin-top: 60px;
  font-size: 12px;
}
.terminal-empty .empty-ico {
  margin-bottom: 8px;
  opacity: 0.5;
}
</style>
