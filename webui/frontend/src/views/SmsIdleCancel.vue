<script setup>
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Delete, Timer } from '@element-plus/icons-vue'
import { getSmsIdleSweeper, listSmsActivations, cancelSmsActivation, saveSmsIdleSweeper } from '@/api/settings'
import { fmtTime, copyText } from '@/api/request'
import StatusDot from '@/components/StatusDot.vue'

const loading = ref(false)
const cancelling = ref('')
const savingIdle = ref(false)
const rows = ref([])
const total = ref(0)
const counts = ref({ open: 0, verified: 0, cancelled: 0, cancel_failed: 0, total: 0 })
const statusFilter = ref('')
const task = ref({
  running: false,
  idle_sec: 300,
  scan_sec: 20,
  max_tries: 3,
  last_scan_at: 0,
  last_open: 0,
  last_cancelled: 0,
  last_failed: 0,
  last_skipped: 0,
  last_error: '',
  started_at: 0,
  next_scan_at: 0,
  counts: {},
})
const idleSec = ref('300')
let timer = 0

const STATUS_META = {
  open: { type: 'warning', text: '待处理' },
  verified: { type: 'success', text: '已校验' },
  cancelled: { type: 'primary', text: '已取消' },
  cancel_failed: { type: 'danger', text: '取消失败' },
}

const pills = computed(() => [
  { label: '后台任务', value: task.value.running ? '运行中' : '未启动', type: task.value.running ? 'success' : 'info' },
  { label: '超时阈值', value: `${task.value.idle_sec}s`, type: 'info' },
  { label: '扫描间隔', value: `${task.value.scan_sec}s`, type: 'info' },
  { label: '待处理', value: counts.value.open || 0, type: 'warning' },
  { label: '已取消', value: counts.value.cancelled || 0, type: 'primary' },
  { label: '已校验', value: counts.value.verified || 0, type: 'success' },
  { label: '取消失败', value: counts.value.cancel_failed || 0, type: 'danger' },
])

async function load(silent = false) {
  if (!silent) loading.value = true
  try {
    const [sw, list] = await Promise.all([
      getSmsIdleSweeper(),
      listSmsActivations({ status: statusFilter.value, limit: 200 }),
    ])
    task.value = { ...task.value, ...(sw.task || {}) }
    if (task.value.idle_sec) idleSec.value = String(task.value.idle_sec)
    rows.value = list.items || []
    total.value = list.total || 0
    counts.value = { ...counts.value, ...(list.counts || task.value.counts || {}) }
  } catch (e) {
    if (!silent) ElMessage.error(e.message || '加载失败')
  } finally {
    if (!silent) loading.value = false
  }
}

async function saveIdle() {
  const n = Math.max(30, Math.min(3600, parseInt(idleSec.value, 10) || 300))
  idleSec.value = String(n)
  savingIdle.value = true
  try {
    const res = await saveSmsIdleSweeper({ idle_sec: n })
    if (res?.task) task.value = { ...task.value, ...res.task }
    ElMessage.success(`已保存：超过 ${n} 秒未成功则自动取消`)
    await load()
  } catch (e) {
    ElMessage.error(e.message || '保存失败')
  } finally {
    savingIdle.value = false
  }
}

async function cancelRow(row) {
  const key = `${row.provider}:${row.activation_id}`
  try {
    await ElMessageBox.confirm(
      `确认向 ${row.provider} 取消 ${row.phone || row.activation_id} 并申请退款？`,
      '手动取消号码',
      { type: 'warning' },
    )
  } catch {
    return
  }
  cancelling.value = key
  try {
    await cancelSmsActivation({ provider: row.provider, activation_id: row.activation_id })
    ElMessage.success('已提交取消')
    await load()
  } catch (e) {
    ElMessage.error(e.message || '取消失败')
  } finally {
    cancelling.value = ''
  }
}

function ageSec(row) {
  const t = Number(row.rented_at || 0)
  if (!t) return '-'
  return `${Math.max(0, Math.floor(Date.now() / 1000 - t))}s`
}

function stopTimer() {
  if (timer) window.clearInterval(timer)
  timer = 0
}
function startTimer() {
  if (timer) return
  timer = window.setInterval(() => load(true), 5000)
}
onMounted(() => {
  load()
  startTimer()
})
onActivated(() => {
  load()
  startTimer()
})
onDeactivated(stopTimer)
onUnmounted(stopTimer)
</script>

<template>
  <div class="idle-page">
    <div class="macos-window-panel">
      <div class="macos-panel-header">
        <div class="header-left">
          <div class="window-dot-group">
            <span class="dot red"></span>
            <span class="dot yellow"></span>
            <span class="dot green"></span>
          </div>
          <el-icon class="title-ico"><Timer /></el-icon>
          <span class="panel-title">超时退号 · 后台兜底任务</span>
          <span class="badge-total">{{ total }} 条记录</span>
        </div>
        <div class="header-right">
          <el-input
            v-model="idleSec"
            type="number"
            style="width: 140px"
            size="small"
            placeholder="超时秒数"
          />
          <el-button size="small" :loading="savingIdle" @click="saveIdle">保存阈值</el-button>
          <el-button class="macos-btn" @click="load">
            <el-icon><Refresh /></el-icon>刷新
          </el-button>
        </div>
      </div>

      <div class="kpi-row">
        <span v-for="p in pills" :key="p.label" class="kpi-pill" :class="`is-${p.type}`">
          <em>{{ p.label }}</em>{{ p.value }}
        </span>
      </div>
      <div class="scan-line">
        启动于 {{ fmtTime(task.started_at) }}
        · 上次扫描 {{ fmtTime(task.last_scan_at) }}
        · 下次扫描 {{ fmtTime(task.next_scan_at) }}
        · 本轮到期 {{ task.last_open || 0 }}
        · 取消 {{ task.last_cancelled || 0 }}
        · 失败 {{ task.last_failed || 0 }}
        · 跳过 {{ task.last_skipped || 0 }}
        <span v-if="task.last_error" class="scan-err"> · {{ task.last_error }}</span>
      </div>
      <div class="scan-line tip">
        只对租号成功后落库的号码兜底取消。Vak 官网 Active 里从未经过本机租号的号码无法列出，需在网站上手动 Cancel。
      </div>

      <div class="filter-row">
        <el-radio-group v-model="statusFilter" size="small" @change="() => load()">
          <el-radio-button value="">全部</el-radio-button>
          <el-radio-button value="open">待处理</el-radio-button>
          <el-radio-button value="cancelled">已取消</el-radio-button>
          <el-radio-button value="verified">已校验</el-radio-button>
          <el-radio-button value="cancel_failed">取消失败</el-radio-button>
        </el-radio-group>
      </div>

      <div class="macos-table-container">
        <el-table v-loading="loading" :data="rows" size="small" stripe height="100%" class="macos-table">
          <el-table-column label="状态" width="110" align="center">
            <template #default="{ row }">
              <StatusDot
                :type="(STATUS_META[row.status] || {}).type || 'info'"
                :text="(STATUS_META[row.status] || {}).text || row.status"
              />
            </template>
          </el-table-column>
          <el-table-column prop="provider" label="平台" width="100" align="center" />
          <el-table-column prop="phone" label="号码" width="150">
            <template #default="{ row }">
              <span class="mono" @click="copyText(row.phone)">{{ row.phone || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="activation_id" label="激活 ID" width="130">
            <template #default="{ row }">
              <span class="mono" @click="copyText(row.activation_id)">{{ row.activation_id || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="country" label="国家" width="70" align="center" />
          <el-table-column prop="email" label="账号" min-width="160" show-overflow-tooltip />
          <el-table-column prop="source" label="来源" width="80" align="center" />
          <el-table-column label="已闲置" width="80" align="center">
            <template #default="{ row }">{{ ageSec(row) }}</template>
          </el-table-column>
          <el-table-column label="保护至" width="165" align="center">
            <template #default="{ row }">
              <span class="mono-date">{{ fmtTime(row.protect_until) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="租号时间" width="165" align="center">
            <template #default="{ row }">
              <span class="mono-date">{{ fmtTime(row.rented_at) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="取消次数" width="90" align="center">
            <template #default="{ row }">{{ row.cancel_tries || 0 }}/{{ task.max_tries || 3 }}</template>
          </el-table-column>
          <el-table-column prop="last_error" label="备注" min-width="160" show-overflow-tooltip />
          <el-table-column label="操作" width="110" align="center" fixed="right">
            <template #default="{ row }">
              <el-button
                v-if="row.status === 'open' || row.status === 'cancel_failed'"
                type="danger"
                link
                :loading="cancelling === `${row.provider}:${row.activation_id}`"
                @click="cancelRow(row)"
              >
                <el-icon><Delete /></el-icon>取消
              </el-button>
              <span v-else class="hint">—</span>
            </template>
          </el-table-column>
          <template #empty>
            <el-empty description="还没有落库的接码号码。租号成功后会出现在这里。" :image-size="60" />
          </template>
        </el-table>
      </div>
    </div>
  </div>
</template>

<style scoped>
.idle-page {
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
  overflow: hidden;
}
.macos-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--app-border);
}
.header-left, .header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
.window-dot-group { display: flex; gap: 5px; }
.dot { width: 10px; height: 10px; border-radius: 50%; }
.dot.red { background: #ff5f57; }
.dot.yellow { background: #febc2e; }
.dot.green { background: #28c840; }
.panel-title { font-weight: 650; }
.title-ico { color: var(--el-color-primary); }
.badge-total {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.kpi-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 10px 14px 0;
}
.kpi-pill {
  font-size: 12px;
  padding: 4px 10px;
  border-radius: 999px;
  background: var(--el-fill-color);
  border: 1px solid var(--app-border);
}
.kpi-pill em {
  font-style: normal;
  margin-right: 6px;
  color: var(--el-text-color-secondary);
}
.kpi-pill.is-success { color: #10b981; }
.kpi-pill.is-warning { color: #d97706; }
.kpi-pill.is-danger { color: #ef4444; }
.kpi-pill.is-primary { color: var(--el-color-primary); }
.scan-line, .filter-row {
  padding: 8px 14px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.scan-err { color: var(--el-color-danger); }
.scan-line.tip { padding-top: 0; opacity: 0.9; }
.macos-table-container {
  flex: 1;
  min-height: 0;
  padding: 0 8px 8px;
}
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; cursor: pointer; }
.mono-date { font-variant-numeric: tabular-nums; font-size: 12px; }
.hint { color: var(--el-text-color-placeholder); }
</style>
