<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  Refresh,
  Download,
  CopyDocument,
  VideoPlay,
  SwitchButton,
  CircleCheck,
  Warning,
  Document,
  Link,
  Close,
} from '@element-plus/icons-vue'
import {
  startNativeExtractTask,
  stopNativeExtractTask,
  nativeExtractStreamUrl,
  getNativeExtractTaskLog,
} from '@/api/extract'
import { copyText, createSSE } from '@/api/request'
import { useProxyStore } from '@/stores/proxy'

const proxyStore = useProxyStore()

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  channel: { type: String, default: 'paypal' },
  emails: { type: Array, default: () => [] },
})

const emit = defineEmits(['update:modelValue', 'finished'])

// ── 渠道预设字典 ──
const CHANNEL_CONFIGS = {
  gcash_check: {
    name: 'GCash 资格检测',
    title: 'GCash 资格检测任务台',
    actionText: '开始检测',
    resultColumn: '检测结论 / 状态',
    defaultExit: 'US',
    defaultBilling: 'PH',
    defaultCurrency: 'PHP',
  },
  oaics_check: {
    name: 'OAICS 资格检测',
    title: 'OAICS 资格检测任务台',
    actionText: '开始检测',
    resultColumn: 'OAICS 状态 / 链接',
    defaultExit: 'US',
    defaultBilling: 'DE',
    defaultCurrency: 'EUR',
  },
  plus_check: {
    name: 'Plus 状态检测',
    title: 'Plus 状态检测任务台',
    actionText: '开始检测',
    resultColumn: 'Plus 计划 / 状态',
    defaultExit: 'US',
    defaultBilling: 'US',
    defaultCurrency: 'USD',
  },
  gcash: {
    name: 'GCash',
    title: 'GCash 提链任务台',
    actionText: '开始提链',
    resultColumn: 'GCash 链接 / 说明',
    defaultExit: 'US',
    defaultBilling: 'PH',
    defaultCurrency: 'PHP',
  },
  pix: {
    name: 'PIX',
    title: 'PIX 出码任务台',
    actionText: '开始出码',
    resultColumn: 'PIX 支付链接 / 二维码说明',
    defaultExit: 'BR',
    defaultBilling: 'BR',
    defaultCurrency: 'BRL',
  },
  paypal: {
    name: 'PayPal',
    title: 'PayPal 提链任务台',
    actionText: '开始提链',
    resultColumn: 'PayPal 链接 / 说明',
    defaultExit: 'TH',
    defaultBilling: 'TH',
    defaultCurrency: 'THB',
  },
  ideal: {
    name: 'iDEAL',
    title: 'iDEAL 提链任务台',
    actionText: '开始提链',
    resultColumn: 'iDEAL 银行跳转 / 授权链接',
    defaultExit: 'NL',
    defaultBilling: 'NL',
    defaultCurrency: 'EUR',
  },
  upi: {
    name: 'UPI',
    title: 'UPI 提链任务台',
    actionText: '开始提链',
    resultColumn: 'UPI 扫码指令链接',
    defaultExit: 'IN',
    defaultBilling: 'IN',
    defaultCurrency: 'INR',
  },
  kakao: {
    name: 'Kakao',
    title: 'Kakao 提链任务台',
    actionText: '开始提链',
    resultColumn: 'Kakao 支付链接',
    defaultExit: 'KR',
    defaultBilling: 'KR',
    defaultCurrency: 'KRW',
  },
  momo: {
    name: 'MoMo',
    title: 'MoMo 提链任务台',
    actionText: '开始提链',
    resultColumn: 'MoMo 支付链接',
    defaultExit: 'VN',
    defaultBilling: 'VN',
    defaultCurrency: 'VND',
  },
  twint: {
    name: 'TWINT',
    title: 'TWINT 提链任务台',
    actionText: '开始提链',
    resultColumn: 'TWINT 扫码支付链接',
    defaultExit: 'CH',
    defaultBilling: 'CH',
    defaultCurrency: 'CHF',
  },
  blik: {
    name: 'BLIK',
    title: 'BLIK 提链任务台',
    actionText: '开始提链',
    resultColumn: 'BLIK 授权链接 / 6位码',
    defaultExit: 'PL',
    defaultBilling: 'PL',
    defaultCurrency: 'PLN',
  },
  hosted: {
    name: 'Hosted',
    title: 'Hosted / Stripe 提链任务台',
    actionText: '开始提链',
    resultColumn: 'Stripe Checkout 托管链接',
    defaultExit: 'US',
    defaultBilling: 'US',
    defaultCurrency: 'USD',
  },
}

// ── 国家 / 币种字典 ──
const COUNTRY_OPTIONS = [
  { value: 'TH', label: 'TH · 泰国 (推荐)', currency: 'THB' },
  { value: 'BR', label: 'BR · 巴西 (推荐)', currency: 'BRL' },
  { value: 'US', label: 'US · 美国', currency: 'USD' },
  { value: 'DE', label: 'DE · 德国', currency: 'EUR' },
  { value: 'NL', label: 'NL · 荷兰', currency: 'EUR' },
  { value: 'PH', label: 'PH · 菲律宾', currency: 'PHP' },
  { value: 'IN', label: 'IN · 印度', currency: 'INR' },
  { value: 'KR', label: 'KR · 韩国', currency: 'KRW' },
  { value: 'VN', label: 'VN · 越南', currency: 'VND' },
  { value: 'CH', label: 'CH · 瑞士', currency: 'CHF' },
  { value: 'PL', label: 'PL · 波兰', currency: 'PLN' },
  { value: 'TR', label: 'TR · 土耳其', currency: 'TRY' },
  { value: 'JP', label: 'JP · 日本', currency: 'JPY' },
  { value: 'GB', label: 'GB · 英国', currency: 'GBP' },
  { value: 'FR', label: 'FR · 法国', currency: 'EUR' },
  { value: 'ID', label: 'ID · 印尼', currency: 'IDR' },
  { value: 'MY', label: 'MY · 马来西亚', currency: 'MYR' },
  { value: 'SG', label: 'SG · 新加坡', currency: 'SGD' },
  { value: 'TW', label: 'TW · 中国台湾', currency: 'TWD' },
  { value: 'HK', label: 'HK · 中国香港', currency: 'HKD' },
]

const CURRENCY_OPTIONS = [
  { value: 'EUR', label: 'EUR · 欧元' },
  { value: 'USD', label: 'USD · 美元' },
  { value: 'THB', label: 'THB · 泰铢' },
  { value: 'BRL', label: 'BRL · 巴西雷亚尔' },
  { value: 'PHP', label: 'PHP · 菲律宾比索' },
  { value: 'INR', label: 'INR · 印度卢比' },
  { value: 'KRW', label: 'KRW · 韩元' },
  { value: 'VND', label: 'VND · 越南盾' },
  { value: 'CHF', label: 'CHF · 瑞士法郎' },
  { value: 'PLN', label: 'PLN · 波兰兹罗提' },
  { value: 'TRY', label: 'TRY · 土耳其里拉' },
  { value: 'JPY', label: 'JPY · 日元' },
  { value: 'GBP', label: 'GBP · 英镑' },
  { value: 'IDR', label: 'IDR · 印尼盾' },
  { value: 'MYR', label: 'MYR · 马来西亚林吉特' },
  { value: 'SGD', label: 'SGD · 新加坡元' },
  { value: 'TWD', label: 'TWD · 新台币' },
  { value: 'HKD', label: 'HKD · 港币' },
]

const currentMeta = computed(() => CHANNEL_CONFIGS[props.channel] || CHANNEL_CONFIGS.paypal)

// ── 参数表单 ──
const form = reactive({
  workers: 2,
  retries: 3,
  exit_country: 'BR',
  billing_country: 'DE',
  currency: 'EUR',
  allow_fallback: true,
})

// ── 任务运行状态 ──
const running = ref(false)
const taskId = ref('')
const es = ref(null)
const taskMap = reactive({})
const taskItems = computed(() => Object.values(taskMap))

// 统计卡片指标
const selectedCount = computed(() => props.emails.length)
const inProgressCount = computed(() => taskItems.value.filter((i) => i.status === 'running').length)
const successCount = computed(() => taskItems.value.filter((i) => i.status === 'success').length)
const errorCount = computed(() => taskItems.value.filter((i) => i.status === 'error').length)
const stoppedCount = computed(() => taskItems.value.filter((i) => i.status === 'cancelled').length)
const pendingCount = computed(() => {
  const done = successCount.value + errorCount.value + stoppedCount.value + inProgressCount.value
  return Math.max(0, selectedCount.value - done)
})
const doneTotal = computed(() => successCount.value + errorCount.value + stoppedCount.value)
const progressPercent = computed(() => {
  if (!selectedCount.value) return 0
  return Math.min(100, Math.round((doneTotal.value / selectedCount.value) * 100))
})

// 实时跳秒定时器
const nowTime = ref(Date.now())
let timer = null

// ── 日志查看弹窗 ──
const logVisible = ref(false)
const logEmail = ref('')
const logLines = ref([])
const logLoading = ref(false)

function initFormFromChannel() {
  const meta = currentMeta.value
  form.exit_country = meta.defaultExit
  form.billing_country = meta.defaultBilling
  form.currency = meta.defaultCurrency
  form.workers = 2
  form.retries = 3
  form.allow_fallback = true

  // 初始化 taskMap
  for (const k of Object.keys(taskMap)) delete taskMap[k]
  for (const em of props.emails) {
    taskMap[em] = {
      email: em,
      status: 'pending',
      step_text: '待启动',
      link_url: '',
      started_at: 0,
      elapsed: 0,
    }
  }
}

watch(
  () => [props.modelValue, props.channel, props.emails],
  ([val]) => {
    if (val) {
      initFormFromChannel()
    } else {
      if (es.value) {
        es.value.close()
        es.value = null
      }
      running.value = false
    }
  },
  { immediate: true },
)

function onBillingCountryChange(c) {
  const match = COUNTRY_OPTIONS.find((item) => item.value === c)
  if (match && match.currency) {
    form.currency = match.currency
  }
}

// ── 启动任务 ──
async function handleStart() {
  if (!props.emails.length) {
    ElMessage.warning('请先勾选要提炼的账号')
    return
  }

  // 重置 taskMap
  for (const em of props.emails) {
    taskMap[em] = {
      email: em,
      status: 'pending',
      step_text: '排队中...',
      link_url: '',
      started_at: 0,
      elapsed: 0,
    }
  }

  try {
    const res = await startNativeExtractTask({
      emails: props.emails,
      channel: props.channel,
      exit_country: form.exit_country,
      billing_country: form.billing_country,
      currency: form.currency,
      workers: form.workers,
      retries: form.retries,
      allow_fallback: form.allow_fallback,
      proxy_pool: proxyStore.text,
    })
    taskId.value = res.task_id
    running.value = true
    ElMessage.success(`【${currentMeta.value.name}】已启动: 共 ${props.emails.length} 个账号`)
    connectStream(res.task_id)
  } catch (e) {
    ElMessage.error(e.message || '启动提炼任务失败')
  }
}

// ── 停止任务 ──
async function handleStop() {
  if (!taskId.value) return
  try {
    await stopNativeExtractTask(taskId.value)
    ElMessage.info('已发送中止请求')
  } catch (e) {
    ElMessage.error(e.message || '中止失败')
  }
}

// ── SSE 连接 (严格匹配 init / progress / end 事件名) ──
function connectStream(id) {
  if (es.value) {
    es.value.close()
    es.value = null
  }

  es.value = createSSE(nativeExtractStreamUrl(id), {
    init: (ev) => {
      try {
        const data = JSON.parse(ev.data)
        if (data.items) {
          for (const [em, it] of Object.entries(data.items)) {
            taskMap[em] = {
              email: em,
              status: it.status,
              step_text: it.step_text || it.status,
              link_url: it.link_url || '',
              started_at: it.started_at || 0,
              elapsed: it.elapsed || 0,
            }
          }
        }
      } catch (_) {}
    },
    progress: (ev) => {
      try {
        const data = JSON.parse(ev.data)
        const em = data.email
        if (em) {
          if (!taskMap[em]) {
            taskMap[em] = { email: em }
          }
          if (data.status !== undefined) taskMap[em].status = data.status
          if (data.step_text !== undefined) taskMap[em].step_text = data.step_text
          if (data.link_url !== undefined) taskMap[em].link_url = data.link_url
          if (data.result !== undefined) taskMap[em].result = data.result
          if (data.started_at !== undefined) taskMap[em].started_at = data.started_at
          if (data.elapsed !== undefined) taskMap[em].elapsed = data.elapsed
        }
      } catch (_) {}
    },
    end: () => {
      running.value = false
      if (es.value) {
        es.value.close()
        es.value = null
      }
      ElMessage.success(`【${currentMeta.value.name}】提炼任务执行完毕！`)
      emit('finished')
    },
  })
}

// ── 查看日志 ──
async function handleViewLog(email) {
  logEmail.value = email
  logVisible.value = true
  logLoading.value = true
  try {
    const res = await getNativeExtractTaskLog(taskId.value || 'latest', email)
    logLines.value = res.lines || []
  } catch (e) {
    logLines.value = [`加载日志失败: ${e.message}`]
  } finally {
    logLoading.value = false
  }
}

// ── 复制所有成功链接 ──
function handleCopySuccessLinks() {
  const links = taskItems.value
    .filter((i) => i.status === 'success' && i.link_url)
    .map((i) => i.link_url)
  if (!links.length) {
    ElMessage.warning('暂无提链成功的 URL')
    return
  }
  copyText(links.join('\n'), `已复制 ${links.length} 条提链 URL`)
}

// ── 导出为 TXT / JSON ──
function handleExportTxt() {
  const lines = taskItems.value
    .filter((i) => i.link_url)
    .map((i) => `${i.email}----${i.link_url}`)
  if (!lines.length) {
    ElMessage.warning('暂无提链结果可导出')
    return
  }
  const blob = new Blob([lines.join('\n')], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${props.channel}_extracted_${taskId.value || 'data'}.txt`
  a.click()
  URL.revokeObjectURL(url)
  ElMessage.success('TXT 导出成功')
}

function handleExportJson() {
  const records = taskItems.value.map((i) => ({
    email: i.email,
    status: i.status,
    channel: props.channel,
    link_url: i.link_url,
    step_text: i.step_text,
    elapsed: i.elapsed,
  }))
  const blob = new Blob([JSON.stringify(records, null, 2)], { type: 'application/json;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${props.channel}_extracted_${taskId.value || 'data'}.json`
  a.click()
  URL.revokeObjectURL(url)
  ElMessage.success('JSON 导出成功')
}

function handleClose() {
  emit('update:modelValue', false)
}

onMounted(() => {
  timer = setInterval(() => {
    nowTime.value = Date.now()
  }, 300)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
  if (es.value) es.value.close()
})
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    width="960px"
    top="3vh"
    :show-close="false"
    append-to-body
    destroy-on-close
    class="extract-modal-dialog"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <!-- 自定义头部 -->
    <template #header>
      <div class="modal-header">
        <div class="header-left">
          <div class="main-title">{{ currentMeta.title }}</div>
          <div class="sub-title">
            已选 <span class="highlight-num">{{ emails.length }}</span> 个账号 ·
            {{ form.exit_country }} 出口 + {{ form.billing_country }} / {{ form.currency }} · OAICS/CS · 0元
          </div>
        </div>
        <el-button size="small" :icon="Close" circle @click="handleClose" />
      </div>
    </template>

    <div class="modal-body">
      <!-- 参数控制行 -->
      <div class="param-bar">
        <div class="param-inputs">
          <span class="param-label">已选 <b>{{ emails.length }}</b> 个账号</span>

          <div class="param-field">
            <span class="field-title">并发</span>
            <el-input-number
              v-model="form.workers"
              :min="1"
              :max="16"
              size="small"
              controls-position="right"
              style="width: 72px"
            />
          </div>

          <div class="param-field">
            <span class="field-title">每号次数</span>
            <el-input-number
              v-model="form.retries"
              :min="1"
              :max="10"
              size="small"
              controls-position="right"
              style="width: 72px"
            />
          </div>

          <div class="param-field">
            <span class="field-title">出口</span>
            <el-select v-model="form.exit_country" filterable size="small" style="width: 145px">
              <el-option
                v-for="opt in COUNTRY_OPTIONS"
                :key="opt.value"
                :label="opt.label"
                :value="opt.value"
              />
            </el-select>
          </div>

          <div class="param-field">
            <span class="field-title">账单</span>
            <el-select
              v-model="form.billing_country"
              filterable
              size="small"
              style="width: 145px"
              @change="onBillingCountryChange"
            >
              <el-option
                v-for="opt in COUNTRY_OPTIONS"
                :key="opt.value"
                :label="opt.label"
                :value="opt.value"
              />
            </el-select>
          </div>

          <div class="param-field">
            <span class="field-title">币种</span>
            <el-select v-model="form.currency" filterable size="small" style="width: 120px">
              <el-option
                v-for="opt in CURRENCY_OPTIONS"
                :key="opt.value"
                :label="opt.label"
                :value="opt.value"
              />
            </el-select>
          </div>

          <el-checkbox v-model="form.allow_fallback" size="small" class="fallback-check">
            允许账单回退
          </el-checkbox>
        </div>

        <div class="param-buttons">
          <el-button size="small" @click="handleClose">取消</el-button>
          <el-button
            size="small"
            type="primary"
            class="start-btn"
            :loading="running"
            :icon="VideoPlay"
            @click="handleStart"
          >
            {{ currentMeta.actionText }}
          </el-button>
        </div>
      </div>

      <!-- 6个指标统计卡片 -->
      <div class="stat-cards">
        <div class="stat-card">
          <div class="stat-label">已选</div>
          <div class="stat-num text-default">{{ selectedCount }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">进行中</div>
          <div class="stat-num text-warning">{{ inProgressCount }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">成功</div>
          <div class="stat-num text-success">{{ successCount }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">失败</div>
          <div class="stat-num text-danger">{{ errorCount }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">已停止</div>
          <div class="stat-num text-muted">{{ stoppedCount }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">待结果</div>
          <div class="stat-num text-primary">{{ pendingCount }}</div>
        </div>
      </div>

      <!-- 进度条 -->
      <div class="progress-section">
        <div class="progress-status-tip">
          <span>{{ running ? '正在极速提链中...' : doneTotal > 0 ? '提炼任务完成' : '等待开始...' }}</span>
          <span class="mono">{{ progressPercent }}%</span>
        </div>
        <el-progress
          :percentage="progressPercent"
          :status="progressPercent === 100 ? 'success' : ''"
          :stroke-width="8"
          :show-text="false"
          striped
          :striped-flow="running"
        />
      </div>

      <!-- 账号表格 -->
      <div class="table-container">
        <el-table
          :data="taskItems"
          size="small"
          height="280px"
          row-key="email"
          stripe
          class="extract-modal-table"
        >
          <el-table-column type="index" label="#" width="45" align="center" />

          <el-table-column label="邮箱" min-width="180" show-overflow-tooltip>
            <template #default="{ row }">
              <span class="mono email-text" @click="copyText(row.email, '已复制邮箱')">{{ row.email }}</span>
            </template>
          </el-table-column>

          <el-table-column label="状态" width="165" align="center">
            <template #default="{ row }">
              <el-tag
                size="small"
                :type="row.status === 'success' ? 'success' : row.status === 'error' ? 'danger' : row.status === 'running' ? 'warning' : 'info'"
                effect="light"
                class="status-tag"
              >
                {{ row.step_text || row.status }}
              </el-tag>
            </template>
          </el-table-column>

          <el-table-column :label="currentMeta.resultColumn" min-width="300" show-overflow-tooltip>
            <template #default="{ row }">
              <div v-if="row.link_url" class="link-cell">
                <el-link
                  :href="row.link_url"
                  target="_blank"
                  type="primary"
                  :underline="false"
                  class="mono link-text"
                >
                  {{ row.link_url }}
                </el-link>
                <el-button
                  size="small"
                  link
                  :icon="CopyDocument"
                  @click="copyText(row.link_url, '链接已复制')"
                />
              </div>
              <span v-else-if="row.result?.error" class="text-danger text-xs mono">
                {{ row.result.error }}
              </span>
              <span v-else class="text-muted text-xs">-</span>
            </template>
          </el-table-column>

          <el-table-column label="操作" width="70" align="center">
            <template #default="{ row }">
              <el-button size="small" link type="primary" @click="handleViewLog(row.email)">
                日志
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </div>

    <!-- 底部操作栏 -->
    <template #footer>
      <div class="modal-footer">
        <div class="footer-left">
          <el-button
            v-if="running"
            size="small"
            type="danger"
            :icon="SwitchButton"
            @click="handleStop"
          >
            批量停止
          </el-button>
          <el-button
            size="small"
            :icon="CopyDocument"
            @click="handleCopySuccessLinks"
          >
            复制全部成功链接
          </el-button>
          <el-button size="small" :icon="Download" @click="handleExportTxt">
            导出 TXT
          </el-button>
          <el-button size="small" :icon="Download" @click="handleExportJson">
            导出 JSON
          </el-button>
        </div>
        <div class="footer-right">
          <el-button size="small" @click="handleClose">关闭</el-button>
        </div>
      </div>
    </template>

    <!-- 单账号日志弹窗 -->
    <el-dialog
      v-model="logVisible"
      :title="`提炼执行日志 · ${logEmail}`"
      width="640px"
      append-to-body
    >
      <div v-loading="logLoading" class="log-container mono">
        <template v-if="logLines && logLines.length">
          <div v-for="(line, idx) in logLines" :key="idx" class="log-line">
            {{ line }}
          </div>
        </template>
        <div v-else class="log-empty">
          暂无实时日志记录
        </div>
      </div>
      <template #footer>
        <el-button size="small" @click="logVisible = false">关闭</el-button>
        <el-button size="small" type="primary" :icon="CopyDocument" @click="copyText(logLines.join('\n'), '日志已复制')">
          复制日志
        </el-button>
      </template>
    </el-dialog>
  </el-dialog>
</template>

<style scoped>
.extract-modal-dialog :deep(.el-dialog__header) {
  padding: 14px 20px 10px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  margin-right: 0;
}

.extract-modal-dialog :deep(.el-dialog__body) {
  padding: 14px 20px;
}

.extract-modal-dialog :deep(.el-dialog__footer) {
  padding: 10px 20px 14px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.main-title {
  font-size: 16px;
  font-weight: 700;
  color: var(--el-text-color-primary);
}

.sub-title {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 2px;
}

.highlight-num {
  font-weight: 700;
  color: var(--el-color-primary);
}

.param-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 8px 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
  gap: 8px;
}

.param-inputs {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.param-label {
  font-size: 12px;
  color: var(--el-text-color-regular);
}

.param-field {
  display: flex;
  align-items: center;
  gap: 4px;
}

.field-title {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.fallback-check {
  margin-left: 4px;
}

.param-buttons {
  display: flex;
  align-items: center;
  gap: 6px;
}

.start-btn {
  background: #198754;
  border-color: #198754;
  font-weight: 600;
}
.start-btn:hover {
  background: #157347;
  border-color: #157347;
}

.stat-cards {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 8px;
  margin-bottom: 12px;
}

.stat-card {
  background: var(--el-bg-color-overlay);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 8px 10px;
  text-align: center;
}

.stat-label {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.stat-num {
  font-size: 18px;
  font-weight: 700;
  font-family: ui-monospace, monospace;
  margin-top: 2px;
}

.text-default { color: var(--el-text-color-primary); }
.text-warning { color: #e6a23c; }
.text-success { color: #67c23a; }
.text-danger  { color: #f56c6c; }
.text-muted   { color: #909399; }
.text-primary { color: #409eff; }

.progress-section {
  margin-bottom: 12px;
}

.progress-status-tip {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: var(--el-text-color-secondary);
  margin-bottom: 4px;
}

.table-container {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  overflow: hidden;
}

.email-text {
  cursor: pointer;
}
.email-text:hover {
  color: var(--el-color-primary);
  text-decoration: underline;
}

.link-cell {
  display: flex;
  align-items: center;
  gap: 4px;
}

.link-text {
  font-size: 11px;
  max-width: 270px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.modal-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
}

.footer-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.log-container {
  background: #1e1e1e;
  color: #d4d4d4;
  padding: 10px;
  border-radius: 6px;
  max-height: 380px;
  overflow-y: auto;
  font-size: 12px;
  line-height: 1.5;
}

.log-line {
  white-space: pre-wrap;
  word-break: break-all;
}

.log-empty {
  text-align: center;
  color: #888;
  padding: 20px 0;
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.text-xs {
  font-size: 11px;
}
</style>
