<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
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
  CreditCard,
  ArrowDown,
} from '@element-plus/icons-vue'
import {
  startNativeExtractTask,
  stopNativeExtractTask,
  retryNativeExtractTask,
  nativeExtractStreamUrl,
  getNativeExtractTaskLog,
  submitNativeExtractTaskInput,
} from '@/api/extract'
import { copyText, createSSE } from '@/api/request'
import { useProxyStore } from '@/stores/proxy'

const router = useRouter()
const proxyStore = useProxyStore()

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  channel: { type: String, default: 'paypal' },
  emails: { type: Array, default: () => [] },
  autoPay: { type: Boolean, default: false },
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
    defaultExit: 'DE',
    defaultBilling: 'DE',
    defaultCurrency: 'EUR',
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
  { value: 'BR', label: 'BR · 巴西 (Plus试用高爆 ★★★★★)', currency: 'BRL' },
  { value: 'DE', label: 'DE · 德国 (PayPal高爆 ★★★★★)', currency: 'EUR' },
  { value: 'US', label: 'US · 美国 (标准通用 ★★★)', currency: 'USD' },
  { value: 'NL', label: 'NL · 荷兰 (欧洲推荐 ★★★★)', currency: 'EUR' },
  { value: 'FR', label: 'FR · 法国 (欧洲推荐 ★★★★)', currency: 'EUR' },
  { value: 'GB', label: 'GB · 英国 (欧洲推荐 ★★★★)', currency: 'GBP' },
  { value: 'JP', label: 'JP · 日本 (亚太推荐 ★★★★)', currency: 'JPY' },
  { value: 'PH', label: 'PH · 菲律宾 (GCash推荐 ★★★★)', currency: 'PHP' },
  { value: 'IN', label: 'IN · 印度 (UPI推荐 ★★★★)', currency: 'INR' },
  { value: 'VN', label: 'VN · 越南 (MoMo推荐 ★★★★)', currency: 'VND' },
  { value: 'CH', label: 'CH · 瑞士 (TWINT推荐 ★★★★)', currency: 'CHF' },
  { value: 'PL', label: 'PL · 波兰 (BLIK推荐 ★★★★)', currency: 'PLN' },
  { value: 'KR', label: 'KR · 韩国 (Kakao推荐 ★★★★)', currency: 'KRW' },
  { value: 'TH', label: 'TH · 泰国 (接码注册)', currency: 'THB' },
  { value: 'TR', label: 'TR · 土耳其 (USD结算)', currency: 'USD' },
  { value: 'ID', label: 'ID · 印尼', currency: 'IDR' },
  { value: 'MY', label: 'MY · 马来西亚', currency: 'MYR' },
  { value: 'SG', label: 'SG · 新加坡', currency: 'SGD' },
  { value: 'TW', label: 'TW · 中国台湾', currency: 'TWD' },
  { value: 'HK', label: 'HK · 中国香港', currency: 'HKD' },
]

const CURRENCY_OPTIONS = [
  { value: 'USD', label: 'USD · 美元 (官方全区通用)' },
  { value: 'EUR', label: 'EUR · 欧元 (欧洲地区)' },
  { value: 'BRL', label: 'BRL · 巴西雷亚尔 (0元高爆)' },
  { value: 'GBP', label: 'GBP · 英镑' },
  { value: 'JPY', label: 'JPY · 日元' },
  { value: 'THB', label: 'THB · 泰铢' },
  { value: 'PHP', label: 'PHP · 菲律宾比索' },
  { value: 'INR', label: 'INR · 印度卢比' },
  { value: 'KRW', label: 'KRW · 韩元' },
  { value: 'VND', label: 'VND · 越南盾' },
  { value: 'CHF', label: 'CHF · 瑞士法郎' },
  { value: 'PLN', label: 'PLN · 波兰兹罗提' },
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
  allow_fallback: false,
  auto_pay: false,
  pay_phone: '+66812345678',
  pay_flow_mode: 'elevation',
})

// ── 任务运行状态 ──
const running = ref(false)
const taskId = ref('')
const es = ref(null)
const taskMap = reactive({})
const taskItems = computed(() => Object.values(taskMap))

// 等待 2FA 短信验证码的账号列表
const awaitingOtpItems = computed(() => {
  return taskItems.value.filter((i) => i.status === 'awaiting_otp')
})

// 统计卡片指标
const selectedCount = computed(() => props.emails.length)
const inProgressCount = computed(() => taskItems.value.filter((i) => i.status === 'running' || i.status === 'awaiting_otp').length)
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
  form.allow_fallback = false
  if (props.channel === 'paypal') {
    form.auto_pay = props.autoPay || false
    form.pay_phone = '+66812345678'
    form.pay_flow_mode = 'elevation'
  }

  // 初始化 taskMap
  for (const k of Object.keys(taskMap)) delete taskMap[k]
  for (const em of props.emails) {
    taskMap[em] = {
      email: em,
      phone: form.pay_phone || '',
      status: 'pending',
      step_text: '待启动',
      link_url: '',
      prompt: '',
      is_paid: false,
      otpInput: '',
      submittingOtp: false,
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

// 批量粘贴手机号对话框
const pastePhoneVisible = ref(false)
const pastePhoneText = ref('')

function openPastePhoneDialog() {
  pastePhoneText.value = ''
  pastePhoneVisible.value = true
}

function handleConfirmPastePhone() {
  const lines = pastePhoneText.value
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean)
  if (!lines.length) {
    ElMessage.warning('请先粘贴手机号内容')
    return
  }

  const emailsList = props.emails || []
  let filledCount = 0

  let isKeyValue = false
  lines.forEach((line) => {
    let parts = []
    if (line.includes('----')) parts = line.split('----')
    else if (line.includes('\t')) parts = line.split('\t')
    else if (line.includes(':') && line.includes('@')) parts = line.split(':')
    else if (line.includes(' ') && line.includes('@')) parts = line.split(/\s+/)

    if (parts.length >= 2) {
      const em = parts[0].trim().toLowerCase()
      const ph = parts[1].trim()
      if (taskMap[em]) {
        taskMap[em].phone = ph
        filledCount++
        isKeyValue = true
      }
    }
  })

  if (!isKeyValue) {
    emailsList.forEach((em, idx) => {
      if (idx < lines.length && taskMap[em]) {
        taskMap[em].phone = lines[idx]
        filledCount++
      }
    })
  }

  ElMessage.success(`已为 ${filledCount} 个账号批量填入专属手机号`)
  pastePhoneVisible.value = false
}

function handleApplyDefaultPhoneToAll() {
  const ph = (form.pay_phone || '').trim()
  if (!ph) {
    ElMessage.warning('请先填写默认手机号')
    return
  }
  let count = 0
  for (const em of props.emails) {
    if (taskMap[em]) {
      taskMap[em].phone = ph
      count++
    }
  }
  ElMessage.success(`已将手机号 ${ph} 一键同步至所有 ${count} 个账号`)
}

function onExitCountryChange(c) {
  form.billing_country = c
  const match = COUNTRY_OPTIONS.find((item) => item.value === c)
  if (match && match.currency) {
    form.currency = match.currency
  }
}

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

  const accountPhones = {}
  for (const em of props.emails) {
    const ph = taskMap[em]?.phone || form.pay_phone || ''
    if (ph) accountPhones[em] = ph.trim()
    taskMap[em] = {
      email: em,
      phone: ph,
      status: 'pending',
      step_text: '排队中...',
      link_url: '',
      prompt: '',
      is_paid: false,
      otpInput: '',
      submittingOtp: false,
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
      auto_pay: props.channel === 'paypal' ? form.auto_pay : false,
      pay_phone: form.pay_phone,
      account_phones: accountPhones,
      pay_flow_mode: form.pay_flow_mode,
    })
    taskId.value = res.task_id
    running.value = true
    const modeDesc = (props.channel === 'paypal' && form.auto_pay) ? '【PayPal 提炼+代付一条龙(同IP)】' : `【${currentMeta.value.name}】`
    ElMessage.success(`${modeDesc}已启动: 共 ${props.emails.length} 个账号`)
    connectStream(res.task_id)
  } catch (e) {
    ElMessage.error(e.message || '启动提炼任务失败')
  }
}

// ── 行内 2FA 验证码输入与提交 (一条龙代付交互) ──
async function handleInlineOtpSubmit(row) {
  const email = (row.email || '').trim()
  const code = (row.otpInput || '').trim()
  if (!code) {
    ElMessage.warning('请输入 6 位短信验证码或新手机号')
    return
  }
  row.submittingOtp = true
  try {
    await submitNativeExtractTaskInput(taskId.value || 'latest', email, code)
    ElMessage.success(`[${email}] 验证码已提交，正在继续执行协议代付...`)
    row.otpInput = ''
    row.status = 'running'
    row.step_text = '已收到验证码，正在继续授权...'
    if (taskMap[email]) {
      taskMap[email].status = 'running'
      taskMap[email].step_text = '已收到验证码，正在继续授权...'
      taskMap[email].prompt = ''
    }
  } catch (e) {
    ElMessage.error(e.message || '提交验证码失败')
  } finally {
    row.submittingOtp = false
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

// ── 一键重试所有失败项 ──
async function handleRetryFailed() {
  if (!taskId.value) {
    ElMessage.warning('当前无任务实例，请直接点击开始提炼')
    return
  }
  const failedEmails = taskItems.value
    .filter((i) => i.status === 'error' || i.status === 'cancelled')
    .map((i) => i.email)

  if (!failedEmails.length) {
    ElMessage.info('当前没有失败或已停止的账号')
    return
  }

  try {
    const res = await retryNativeExtractTask(taskId.value, { emails: failedEmails })
    running.value = true
    for (const em of failedEmails) {
      if (taskMap[em]) {
        taskMap[em].status = 'pending'
        taskMap[em].step_text = '排队重试中...'
        taskMap[em].result = null
      }
    }
    ElMessage.success(`已开始重试 ${failedEmails.length} 个失败账号`)
    connectStream(taskId.value)
  } catch (e) {
    ElMessage.error(e.message || '重试失败')
  }
}

// ── 单个账号重试 ──
async function handleRetrySingle(email) {
  if (!taskId.value) {
    ElMessage.warning('当前无任务实例，请直接点击开始提炼')
    return
  }
  try {
    await retryNativeExtractTask(taskId.value, { emails: [email] })
    running.value = true
    if (taskMap[email]) {
      taskMap[email].status = 'pending'
      taskMap[email].step_text = '排队重试中...'
      taskMap[email].result = null
    }
    ElMessage.success(`已开始重试账号: ${email}`)
    connectStream(taskId.value)
  } catch (e) {
    ElMessage.error(e.message || '重试失败')
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
              prompt: it.prompt || '',
              is_paid: it.is_paid || false,
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
          if (data.prompt !== undefined) taskMap[em].prompt = data.prompt
          if (data.is_paid !== undefined) taskMap[em].is_paid = data.is_paid
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

// ── 复制成功提炼结果 (支持携带邮箱或仅纯链接) ──
function handleCopySuccessLinks(mode = 'email_link') {
  const items = taskItems.value.filter((i) => i.status === 'success' && i.link_url)
  if (!items.length) {
    ElMessage.warning('暂无提链成功的记录')
    return
  }
  if (mode === 'pure_link') {
    const text = items.map((i) => i.link_url).join('\n')
    copyText(text, `已复制 ${items.length} 条纯提链链接`)
  } else {
    const text = items.map((i) => `${i.email}----${i.link_url}`).join('\n')
    copyText(text, `已复制 ${items.length} 条「邮箱----提链链接」`)
  }
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

function goToPayPalPay() {
  handleClose()
  router.push('/paypal-pay')
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
            <el-select
              v-model="form.exit_country"
              filterable
              size="small"
              style="width: 145px"
              @change="onExitCountryChange"
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

          <el-tooltip content="若当前账单国家未命中0元试用优惠，将自动切换为BR(巴西)高爆0元区通道提链" placement="top">
            <el-checkbox v-model="form.allow_fallback" size="small" class="fallback-check">
              允许账单回退
            </el-checkbox>
          </el-tooltip>
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

      <!-- 一条龙代付配置卡片 (仅 PayPal 渠道可用) -->
      <div v-if="channel === 'paypal'" class="pipeline-switch-card">
        <div class="pipeline-header">
          <el-checkbox v-model="form.auto_pay" class="pipeline-checkbox">
            <span class="pipeline-title">⚡ 开启 PayPal 提链 + 协议代付「一条龙」全链路 (100% 同 IP 同环境)</span>
          </el-checkbox>
          <div class="pipeline-header-actions">
            <el-button v-if="form.auto_pay" size="small" type="primary" plain :icon="Document" @click="openPastePhoneDialog">
              📋 批量粘贴/分配手机号
            </el-button>
            <el-tag size="small" type="success" effect="light" class="pipeline-badge">
              无缝接力开通 · 避免跨IP风控
            </el-tag>
          </div>
        </div>
        <div v-if="form.auto_pay" class="pipeline-config-row">
          <div class="pipeline-field">
            <span class="pipeline-field-label">默认手机号:</span>
            <el-input
              v-model="form.pay_phone"
              placeholder="如 +905301847167 / +55... (自动对齐签约国)"
              size="small"
              style="width: 220px"
            />
            <el-button size="small" link type="primary" @click="handleApplyDefaultPhoneToAll">
              应用到表格所有账号
            </el-button>
          </div>
          <div class="pipeline-field">
            <span class="pipeline-field-label">协议模式:</span>
            <el-select v-model="form.pay_flow_mode" size="small" style="width: 140px">
              <el-option label="身份提升 (2FA接码)" value="elevation" />
              <el-option label="标准原版" value="standard" />
            </el-select>
          </div>
          <div class="pipeline-desc text-xs">
            💡 提示：提链成功后直接沿用同一代理 Session 执行代付；各账号可在下方表格直接录入不同手机号，收到 2FA 短信可实时行内输入验证码！
          </div>
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
          <span>{{ running ? '正在执行提炼与代付流水线中...' : doneTotal > 0 ? '提炼任务完成' : '等待开始...' }}</span>
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

      <!-- 🚨 2FA 短信验证码输入浮动横幅 (当触发 2FA 短信时即刻高亮展示) -->
      <div v-if="awaitingOtpItems.length > 0" class="otp-floating-banner">
        <div class="otp-banner-header">
          <span class="otp-banner-title">🔑 正在等待短信验证码 ({{ awaitingOtpItems.length }} 个账号):</span>
          <span class="otp-banner-subtitle text-xs">手机收到 6 位验证码后，直接在下方输入并点击提交或按回车</span>
        </div>
        <div class="otp-banner-list">
          <div v-for="item in awaitingOtpItems" :key="item.email" class="otp-banner-item">
            <span class="otp-email mono">{{ item.email }}</span>
            <el-input
              v-model="item.otpInput"
              size="small"
              placeholder="输入 6 位 2FA 码"
              class="mono otp-banner-input"
              style="width: 160px"
              @keyup.enter="handleInlineOtpSubmit(item)"
            />
            <el-button
              size="small"
              type="warning"
              :loading="item.submittingOtp"
              @click="handleInlineOtpSubmit(item)"
            >
              提交验证码
            </el-button>
            <span v-if="item.prompt" class="text-xs text-warning mono otp-prompt-text">{{ item.prompt }}</span>
          </div>
        </div>
      </div>

      <!-- 账号表格 -->
      <div class="table-container">
        <el-table
          :data="taskItems"
          size="small"
          height="220px"
          row-key="email"
          stripe
          class="extract-modal-table"
        >
          <el-table-column type="index" label="#" width="40" align="center" />

          <el-table-column label="邮箱" min-width="170" show-overflow-tooltip>
            <template #default="{ row }">
              <span class="mono email-text" @click="copyText(row.email, '已复制邮箱')">{{ row.email }}</span>
            </template>
          </el-table-column>

          <!-- 一条龙模式下支持每个账号单独编辑不同手机号 -->
          <el-table-column v-if="channel === 'paypal' && form.auto_pay" label="代付手机号" width="165">
            <template #default="{ row }">
              <el-input
                v-model="row.phone"
                size="small"
                placeholder="如 +90530..."
                class="mono phone-inline-input"
                :disabled="running"
              />
            </template>
          </el-table-column>

          <el-table-column label="状态" width="175" align="center">
            <template #default="{ row }">
              <el-tag
                v-if="row.status === 'awaiting_otp'"
                size="small"
                type="warning"
                effect="dark"
                class="status-tag status-awaiting"
              >
                🔑 待输入 2FA 验证码
              </el-tag>
              <el-tag
                v-else-if="row.is_paid || (row.result && row.result.is_paid)"
                size="small"
                type="success"
                effect="dark"
                class="status-tag status-paid"
              >
                🎉 Plus 已生效
              </el-tag>
              <el-tag
                v-else-if="row.result && row.result.state"
                size="small"
                :type="row.result.state === 'OAICS' ? 'success' : row.result.state === 'CS' ? 'warning' : row.result.state === 'OAIC' ? 'primary' : row.status === 'error' ? 'danger' : 'info'"
                effect="light"
                class="status-tag"
              >
                {{ row.result.label || row.step_text || row.status }}
              </el-tag>
              <el-tag
                v-else
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
              <!-- 当处于等待 2FA 验证码时，直接呈现行内输入与提交 -->
              <div v-if="row.status === 'awaiting_otp'" class="inline-otp-wrapper">
                <el-input
                  v-model="row.otpInput"
                  size="small"
                  placeholder="输入 6 位验证码"
                  class="mono inline-otp-input"
                  style="width: 130px"
                  @keyup.enter="handleInlineOtpSubmit(row)"
                />
                <el-button
                  size="small"
                  type="warning"
                  :loading="row.submittingOtp"
                  @click="handleInlineOtpSubmit(row)"
                >
                  提交 2FA
                </el-button>
                <span v-if="row.prompt" class="inline-otp-prompt text-xs mono">
                  {{ row.prompt }}
                </span>
              </div>
              <div v-else-if="row.link_url" class="link-cell">
                <el-link
                  :href="row.link_url"
                  target="_blank"
                  type="primary"
                  :underline="false"
                  class="mono link-text"
                >
                  {{ row.link_url }}
                </el-link>
                <el-tooltip content="点击复制：邮箱----提链链接" placement="top">
                  <el-button
                    size="small"
                    link
                    :icon="CopyDocument"
                    @click="copyText(`${row.email}----${row.link_url}`, '已复制：邮箱----链接')"
                  />
                </el-tooltip>
              </div>
              <span v-else-if="row.result?.error" class="text-danger text-xs mono">
                {{ row.result.error }}
              </span>
              <span v-else class="text-muted text-xs">-</span>
            </template>
          </el-table-column>

          <el-table-column label="操作" width="130" align="center">
            <template #default="{ row }">
              <div class="row-actions">
                <el-button size="small" link type="primary" @click="handleViewLog(row.email)">
                  日志
                </el-button>
                <el-button
                  v-if="(row.status === 'error' || row.status === 'cancelled') && !running"
                  size="small"
                  link
                  type="warning"
                  :icon="Refresh"
                  @click="handleRetrySingle(row.email)"
                >
                  重试
                </el-button>
              </div>
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
            v-if="errorCount > 0 && !running"
            size="small"
            type="warning"
            :icon="Refresh"
            @click="handleRetryFailed"
          >
            一键重试失败 ({{ errorCount }})
          </el-button>
          <el-dropdown
            split-button
            size="small"
            type="default"
            :disabled="successCount === 0"
            @click="handleCopySuccessLinks('email_link')"
            @command="handleCopySuccessLinks"
          >
            <el-icon><CopyDocument /></el-icon>
            复制成功 (邮箱----链接)
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="email_link">
                  📋 复制：邮箱----提链链接 (默认)
                </el-dropdown-item>
                <el-dropdown-item command="pure_link">
                  🔗 复制：仅纯链接
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <el-button size="small" :icon="Download" @click="handleExportTxt">
            导出 TXT
          </el-button>
          <el-button size="small" :icon="Download" @click="handleExportJson">
            导出 JSON
          </el-button>
          <el-button
            v-if="props.channel === 'paypal' && successCount > 0"
            size="small"
            type="success"
            :icon="CreditCard"
            @click="goToPayPalPay"
          >
            一键去协议代付 ({{ successCount }})
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

    <!-- 批量粘贴手机号弹窗 -->
    <el-dialog
      v-model="pastePhoneVisible"
      title="📋 批量分配/粘贴代付手机号"
      width="520px"
      append-to-body
    >
      <div style="margin-bottom: 8px; font-size: 12px; color: var(--el-text-color-secondary); line-height: 1.5;">
        每行输入一个手机号（自动按顺序匹配已选账号），或按 <code>邮箱----手机号</code> 格式精准匹配：
      </div>
      <el-input
        v-model="pastePhoneText"
        type="textarea"
        :rows="7"
        placeholder="+905301847167&#10;+905301847168&#10;或&#10;user1@outlook.com----+905301847167&#10;user2@outlook.com----+905301847168"
        class="mono"
      />
      <template #footer>
        <el-button size="small" @click="pastePhoneVisible = false">取消</el-button>
        <el-button size="small" type="primary" @click="handleConfirmPastePhone">
          确认填入
        </el-button>
      </template>
    </el-dialog>
  </el-dialog>
</template>

<style scoped>
.extract-modal-dialog :deep(.el-dialog) {
  max-height: 94vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.extract-modal-dialog :deep(.el-dialog__header) {
  padding: 12px 18px 8px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  margin-right: 0;
}

.extract-modal-dialog :deep(.el-dialog__body) {
  padding: 10px 18px;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}

.extract-modal-dialog :deep(.el-dialog__footer) {
  padding: 8px 18px 12px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.main-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--el-text-color-primary);
}

.sub-title {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  margin-top: 1px;
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
  border-radius: 6px;
  padding: 6px 10px;
  margin-bottom: 8px;
  flex-wrap: wrap;
  gap: 6px;
}

.param-inputs {
  display: flex;
  align-items: center;
  gap: 8px;
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
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.fallback-check {
  margin-left: 2px;
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

.pipeline-switch-card {
  background: linear-gradient(135deg, rgba(25, 135, 84, 0.05) 0%, rgba(13, 110, 253, 0.05) 100%);
  border: 1px dashed rgba(25, 135, 84, 0.35);
  border-radius: 6px;
  padding: 6px 10px;
  margin-bottom: 8px;
}

.pipeline-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.pipeline-header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.pipeline-title {
  font-weight: 600;
  font-size: 12px;
  color: #198754;
}

.pipeline-config-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px dashed rgba(25, 135, 84, 0.2);
}

.pipeline-field {
  display: flex;
  align-items: center;
  gap: 6px;
}

.pipeline-field-label {
  font-size: 11px;
  color: var(--el-text-color-regular);
  font-weight: 500;
}

.pipeline-desc {
  color: var(--el-text-color-secondary);
  width: 100%;
}

.stat-cards {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 6px;
  margin-bottom: 8px;
}

.stat-card {
  background: var(--el-bg-color-overlay);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  padding: 4px 6px;
  text-align: center;
}

.stat-label {
  font-size: 10px;
  color: var(--el-text-color-secondary);
}

.stat-num {
  font-size: 16px;
  font-weight: 700;
  font-family: ui-monospace, monospace;
  margin-top: 1px;
}

.text-default { color: var(--el-text-color-primary); }
.text-warning { color: #e6a23c; }
.text-success { color: #67c23a; }
.text-danger  { color: #f56c6c; }
.text-muted   { color: #909399; }
.text-primary { color: #409eff; }

.progress-section {
  margin-bottom: 8px;
}

.progress-status-tip {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: var(--el-text-color-secondary);
  margin-bottom: 3px;
}

.otp-floating-banner {
  background: #fffbe6;
  border: 1px solid #ffe58f;
  border-left: 4px solid #faad14;
  border-radius: 6px;
  padding: 8px 12px;
  margin-bottom: 8px;
  box-shadow: 0 2px 8px rgba(250, 173, 20, 0.15);
}

.otp-banner-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.otp-banner-title {
  font-weight: 700;
  color: #d48806;
  font-size: 12px;
}

.otp-banner-subtitle {
  color: #8c6b1f;
}

.otp-banner-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.otp-banner-item {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  background: #ffffff;
  padding: 4px 8px;
  border-radius: 4px;
  border: 1px solid #ffe58f;
}

.otp-email {
  font-weight: 600;
  font-size: 11px;
  color: #333;
}

.otp-prompt-text {
  color: #d48806;
}

.table-container {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  overflow: hidden;
  margin-bottom: 2px;
}

.email-text {
  cursor: pointer;
}
.email-text:hover {
  color: var(--el-color-primary);
  text-decoration: underline;
}

.phone-inline-input {
  font-size: 11px;
}

.link-cell {
  display: flex;
  align-items: center;
  gap: 4px;
}

.link-text {
  font-size: 11px;
  max-width: 250px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.status-awaiting {
  animation: pulse-orange 1.6s infinite ease-in-out;
}

.status-paid {
  font-weight: 700;
}

@keyframes pulse-orange {
  0% { opacity: 0.85; transform: scale(0.98); }
  50% { opacity: 1; transform: scale(1.02); }
  100% { opacity: 0.85; transform: scale(0.98); }
}

.inline-otp-wrapper {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.inline-otp-prompt {
  color: #e6a23c;
  width: 100%;
}

.row-actions {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
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
  gap: 6px;
  flex-wrap: wrap;
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
