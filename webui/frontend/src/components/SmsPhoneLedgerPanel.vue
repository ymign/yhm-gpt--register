<script setup>
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Notebook, Refresh } from '@element-plus/icons-vue'
import { listSmsPhoneLedger } from '@/api/register'
import { copyText, fmtTime } from '@/api/request'
import { formatCountry } from '@/stores/form'
import StatusDot from '@/components/StatusDot.vue'

const props = defineProps({
  compact: { type: Boolean, default: false },
  autoRefresh: { type: Boolean, default: true },
})

const loading = ref(false)
const rows = ref([])
const total = ref(0)
const counts = ref({ all: 0, rejected: 0, used: 0 })
const outcome = ref('')
const page = ref(1)
const pageSize = ref(50)
let timer = 0

const OUTCOME_META = {
  rejected_openai: { type: 'danger', text: '拒号' },
  rejected: { type: 'danger', text: '拒号' },
  already_in_use: { type: 'danger', text: '拒号·占用' },
  used_success: { type: 'success', text: '已用' },
  success: { type: 'success', text: '已用' },
  skipped_unsubmitted: { type: 'info', text: '未提交跳过' },
  timeout_no_sms: { type: 'warning', text: '超时无码' },
  cancelled: { type: 'info', text: '已取消' },
  session_expired: { type: 'warning', text: '会话过期' },
}

const pills = computed(() => [
  { key: '', label: '全部', value: counts.value.all || 0, type: 'info' },
  { key: 'rejected', label: '拒号', value: counts.value.rejected || 0, type: 'danger' },
  { key: 'used', label: '已用', value: counts.value.used || 0, type: 'success' },
])

function outcomeMeta(st) {
  return OUTCOME_META[String(st || '').toLowerCase()] || { type: 'info', text: st || '—' }
}

function countryLabel(code) {
  const raw = String(code || '').trim()
  if (!raw) return '—'
  return formatCountry(raw) || raw.toUpperCase()
}

async function load(silent = false) {
  if (!silent) loading.value = true
  try {
    const res = await listSmsPhoneLedger({
      outcome: outcome.value,
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
    })
    rows.value = res.items || []
    total.value = Number(res.total || 0)
    counts.value = {
      all: Number(res.all || 0),
      rejected: Number(res.rejected || 0),
      used: Number(res.used || 0),
    }
  } catch (e) {
    if (!silent) ElMessage.error(e.message || '加载台账失败')
  } finally {
    if (!silent) loading.value = false
  }
}

function setOutcome(v) {
  outcome.value = v
  page.value = 1
  load()
}

function stopTimer() {
  if (timer) window.clearInterval(timer)
  timer = 0
}
function startTimer() {
  if (!props.autoRefresh || timer) return
  timer = window.setInterval(() => load(true), 5000)
}

watch(pageSize, () => {
  page.value = 1
  load()
})
watch(page, () => load(true))

onMounted(() => {
  load()
  startTimer()
})
onActivated(() => {
  load(true)
  startTimer()
})
onDeactivated(stopTimer)
onUnmounted(stopTimer)

defineExpose({ load })
</script>

<template>
  <div class="ledger-panel" :class="{ 'is-compact': compact }">
    <div v-if="!compact" class="ledger-head">
      <div class="head-left">
        <div class="window-dot-group">
          <span class="dot red"></span>
          <span class="dot yellow"></span>
          <span class="dot green"></span>
        </div>
        <el-icon class="title-ico"><Notebook /></el-icon>
        <span class="panel-title">接码号码台账</span>
        <span class="badge-total">{{ counts.all }} 条有效记录</span>
      </div>
      <el-button class="macos-btn" size="small" @click="load()">
        <el-icon><Refresh /></el-icon>刷新
      </el-button>
    </div>
    <div v-else class="ledger-head compact-head">
      <span class="compact-lead">只跳过拒号 · 已用号还能再接码</span>
      <el-button text size="small" @click="load()">
        <el-icon><Refresh /></el-icon>刷新
      </el-button>
    </div>

    <div class="kpi-row">
      <button
        v-for="p in pills"
        :key="p.key"
        class="kpi-pill"
        :class="[`is-${p.type}`, { 'is-on': outcome === p.key }]"
        @click="setOutcome(p.key)"
      >
        <em>{{ p.label }}</em>{{ p.value }}
      </button>
    </div>

    <div class="filter-row">
      <el-radio-group :model-value="outcome" size="small" @change="setOutcome">
        <el-radio-button value="">全部</el-radio-button>
        <el-radio-button value="rejected">拒号</el-radio-button>
        <el-radio-button value="used">已用</el-radio-button>
      </el-radio-group>
      <span class="filter-hint">号码打码展示。拒号不打 OpenAI，已用号不拉黑。</span>
    </div>

    <div class="table-wrap">
      <el-table v-loading="loading" :data="rows" size="small" stripe height="100%" class="macos-table">
        <el-table-column label="国家" width="128">
          <template #default="{ row }">
            <span class="country-cell">{{ countryLabel(row.country) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="打码号码" min-width="150">
          <template #default="{ row }">
            <span class="mono phone" @click="copyText(row.phone_masked, '已复制打码号码')">{{ row.phone_masked || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="结果" width="120" align="center">
          <template #default="{ row }">
            <StatusDot :type="outcomeMeta(row.outcome).type" :text="outcomeMeta(row.outcome).text" />
          </template>
        </el-table-column>
        <el-table-column label="时间" width="168" align="center">
          <template #default="{ row }">
            <span class="mono-date">{{ fmtTime(row.created_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="原因" min-width="160" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="reason">{{ row.reject_reason || '—' }}</span>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="还没有落库的接码号码。租号并提交后会出现在这里。" :image-size="56" />
        </template>
      </el-table>
    </div>

    <div class="pager">
      <span class="pager-tip">共 {{ total }} 条</span>
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :page-sizes="[30, 50, 100, 200]"
        :total="total"
        layout="sizes, prev, pager, next"
        size="small"
      />
    </div>
  </div>
</template>

<style scoped>
.ledger-panel {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: var(--app-window-bg);
  border: 1px solid var(--app-border);
  border-radius: 12px;
  overflow: hidden;
}
.ledger-panel.is-compact {
  border: 0;
  border-radius: 0;
  background: transparent;
}
.ledger-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--app-border);
}
.head-left {
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
.badge-total { font-size: 12px; color: var(--el-text-color-secondary); }
.compact-head { padding: 0 0 8px; border-bottom: 0; }
.compact-lead { font-size: 12px; color: var(--el-text-color-secondary); }
.kpi-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 10px 14px 0;
}
.is-compact .kpi-row { padding: 0 0 8px; }
.kpi-pill {
  font-size: 12px;
  padding: 4px 10px;
  border-radius: 999px;
  background: var(--el-fill-color);
  border: 1px solid var(--app-border);
  cursor: pointer;
  color: inherit;
}
.kpi-pill em {
  font-style: normal;
  margin-right: 6px;
  color: var(--el-text-color-secondary);
}
.kpi-pill.is-success { color: #10b981; }
.kpi-pill.is-danger { color: #ef4444; }
.kpi-pill.is-on {
  border-color: var(--el-color-primary);
  box-shadow: 0 0 0 1px var(--el-color-primary-light-7);
}
.filter-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 14px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.is-compact .filter-row { padding: 0 0 8px; }
.filter-hint { opacity: 0.85; }
.table-wrap {
  flex: 1;
  min-height: 0;
  padding: 0 10px 8px;
}
.is-compact .table-wrap { padding: 0; height: 420px; min-height: 360px; }
.phone {
  cursor: pointer;
  letter-spacing: 0.3px;
}
.phone:hover { color: var(--el-color-primary); }
.mono, .mono-date { font-variant-numeric: tabular-nums; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.reason { color: var(--el-text-color-secondary); font-size: 12px; }
.pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 6px 14px 10px;
}
.is-compact .pager { padding: 8px 0 0; }
.pager-tip { font-size: 12px; color: var(--el-text-color-secondary); }
</style>
