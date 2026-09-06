<script setup>
import { computed, nextTick, onUnmounted, reactive, ref, shallowRef, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { ElMessage } from 'element-plus'
import {
  CopyDocument,
  Document,
  Download,
  Loading,
  Search,
  Setting,
  SwitchButton,
  VideoPlay,
} from '@element-plus/icons-vue'
import {
  startTokenRefresh,
  stopTokenRefresh,
  tokenRefreshStreamUrl,
  getTokenRefreshLog,
  downloadTokenRefreshExport,
} from '@/api/register'
import { getSmsAllCountries, getSmsProviders } from '@/api/settings'
import { copyText, createSSE } from '@/api/request'
import { COUNTRY_OPTIONS } from '@/stores/form'
import { useProxyStore } from '@/stores/proxy'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  emails: { type: Array, default: () => [] },
})
const emit = defineEmits(['update:modelValue', 'completed'])

const { list: proxyList } = storeToRefs(useProxyStore())

const running = ref(false)
const taskId = ref('')
const targetEmails = ref([])
const activeTab = ref('network')
const configCollapsed = ref(true)
const page = ref(1)
const pageSize = ref(50)
const filter = ref('all')
const search = ref('')
const clock = ref(0)

const TOKEN_REFRESH_FORM_KEY = 'gpt_token_refresh_form_v1'
const FORM_DEFAULTS = {
  proxy: '',
  proxyCountry: 'US',
  workers: 10,
  timeout: 45,
  forceFullLogin: false,
  smsEnabled: false,
  smsProvider: 'smsbower',
  smsApiKey: '',
  smsCountry: '52',
  smsMaxPrice: '',
  smsMaxAttempts: 3,
  smsTimeout: 80,
}

function loadSavedForm() {
  let saved = {}
  try {
    saved = JSON.parse(localStorage.getItem(TOKEN_REFRESH_FORM_KEY) || '{}')
  } catch (_) {
    saved = {}
  }
  if (!saved || typeof saved !== 'object') saved = {}
  const workers = Number(saved.workers)
  const timeout = Number(saved.timeout)
  const smsMaxAttempts = Number(saved.smsMaxAttempts)
  const smsTimeout = Number(saved.smsTimeout)
  return {
    ...FORM_DEFAULTS,
    ...saved,
    proxy: saved.proxy == null ? FORM_DEFAULTS.proxy : String(saved.proxy),
    proxyCountry: Object.prototype.hasOwnProperty.call(saved, 'proxyCountry')
      ? String(saved.proxyCountry ?? '')
      : FORM_DEFAULTS.proxyCountry,
    workers: Number.isFinite(workers) && workers >= 1 ? Math.min(20, Math.round(workers)) : FORM_DEFAULTS.workers,
    timeout: Number.isFinite(timeout) && timeout >= 10 ? Math.min(120, Math.round(timeout)) : FORM_DEFAULTS.timeout,
    forceFullLogin: Boolean(saved.forceFullLogin),
    smsEnabled: Boolean(saved.smsEnabled),
    smsProvider: saved.smsProvider || FORM_DEFAULTS.smsProvider,
    smsApiKey: saved.smsApiKey == null ? '' : String(saved.smsApiKey),
    smsCountry: saved.smsCountry == null ? FORM_DEFAULTS.smsCountry : String(saved.smsCountry),
    smsMaxPrice: saved.smsMaxPrice == null ? '' : String(saved.smsMaxPrice),
    smsMaxAttempts: Number.isFinite(smsMaxAttempts) && smsMaxAttempts >= 1
      ? Math.min(10, Math.round(smsMaxAttempts))
      : FORM_DEFAULTS.smsMaxAttempts,
    smsTimeout: Number.isFinite(smsTimeout) && smsTimeout >= 20
      ? Math.min(300, Math.round(smsTimeout))
      : FORM_DEFAULTS.smsTimeout,
  }
}

function persistForm(payload = form) {
  try {
    localStorage.setItem(TOKEN_REFRESH_FORM_KEY, JSON.stringify({ ...payload }))
  } catch (_) {}
}

const form = reactive(loadSavedForm())
persistForm()
watch(form, persistForm, { deep: true })

function saveFormAsDefault() {
  persistForm()
  ElMessage.success('已保存当前设置，下次打开 Token 刷新工作台会沿用并发、国家和代理')
}

const items = shallowRef(Object.create(null))
const tick = ref(0)
const logModalVisible = ref(false)
const currentLogItem = ref(null)
const logLines = ref([])
const logLoading = ref(false)
const logBoxRef = ref(null)
const smsProviders = ref([])
const smsAllCountries = ref([])
const smsCountriesLoading = ref(false)

let es = null
let liveTimer = null
let patchTimer = 0
let logPollTimer = null
let pendingPatches = Object.create(null)
const PATCH_FLUSH_MS = 180

const smsCountryOptions = computed(() => {
  const rest = (smsAllCountries.value || []).map((c) => ({
    value: String(c.id),
    label: c.name ? `${c.name} (${c.id})` : String(c.id),
  }))
  return [{ value: 'AUTO', label: '🌐 智能多国自动轮换' }, ...rest]
})

function bump() {
  tick.value++
}

function makePendingItem(email) {
  return {
    email,
    status: 'pending',
    step_text: '排队中...',
    result: null,
    elapsed: 0,
    started_at: 0,
  }
}

function initFromEmails(emails, { force = false } = {}) {
  const list = (emails || []).map((e) => String(e || '').trim().toLowerCase()).filter(Boolean)
  const same =
    !force &&
    list.length === targetEmails.value.length &&
    list.every((e, i) => e === targetEmails.value[i])
  if (running.value) return
  if (same && taskId.value && Object.keys(items.value).length) return

  targetEmails.value = list
  taskId.value = ''
  configCollapsed.value = true
  page.value = 1
  filter.value = 'all'
  search.value = ''
  const map = Object.create(null)
  for (const em of list) map[em] = makePendingItem(em)
  items.value = map
  bump()
}

function startLiveTimer() {
  stopLiveTimer()
  clock.value = Date.now()
  liveTimer = setInterval(() => {
    if (!running.value) {
      stopLiveTimer()
      return
    }
    clock.value = Date.now()
  }, 1000)
}

function stopLiveTimer() {
  if (liveTimer) {
    clearInterval(liveTimer)
    liveTimer = null
  }
}

function flushPatches() {
  patchTimer = 0
  const patches = pendingPatches
  pendingPatches = Object.create(null)
  const map = items.value
  let changed = false
  for (const em in patches) {
    const up = patches[em]
    const cur = map[em]
    if (cur) Object.assign(cur, up)
    else map[em] = { ...makePendingItem(em), ...up }
    changed = true
  }
  if (changed) bump()
}

function queuePatch(email, patch) {
  const prev = pendingPatches[email]
  pendingPatches[email] = prev ? Object.assign(prev, patch) : patch
  if (!patchTimer) patchTimer = window.setTimeout(flushPatches, PATCH_FLUSH_MS)
}

function displayElapsed(row) {
  clock.value
  if (row.status === 'running' && row.started_at) {
    return Math.max(0, Date.now() / 1000 - row.started_at).toFixed(1)
  }
  if (row.elapsed) return String(row.elapsed)
  return ''
}

const stats = computed(() => {
  tick.value
  const map = items.value
  const tot = targetEmails.value.length || Object.keys(map).length
  let done = 0
  let runningN = 0
  let pending = 0
  let rt_fast_ok = 0
  let full_login_ok = 0
  let success = 0
  let need_phone = 0
  let error = 0
  for (const em in map) {
    const i = map[em]
    const st = i.status
    const rs = i.result && i.result.status
    const method = i.result && i.result.method
    if (st === 'running') runningN++
    else if (st === 'pending') pending++
    else if (st === 'done') {
      done++
      if (rs === 'success') {
        success++
        if (method === 'rt_fast') rt_fast_ok++
        else full_login_ok++
      } else if (rs === 'need_phone') need_phone++
      else if (rs !== 'cancelled') error++
    }
  }
  return {
    total: tot,
    done,
    running: runningN,
    pending,
    rt_fast_ok,
    full_login_ok,
    success,
    need_phone,
    error,
    percent: tot > 0 ? Math.round((done / tot) * 100) : 0,
  }
})

const filteredRows = computed(() => {
  tick.value
  const kw = search.value.trim().toLowerCase()
  const f = filter.value
  const map = items.value
  const out = []
  const source = targetEmails.value.length ? targetEmails.value : Object.keys(map)
  for (const em of source) {
    const item = map[em]
    if (!item) continue
    if (kw && !em.toLowerCase().includes(kw)) continue
    const st = item.status
    const rs = item.result && item.result.status
    if (f === 'running' && st !== 'running') continue
    else if (f === 'pending' && st !== 'pending') continue
    else if (f === 'success' && rs !== 'success') continue
    else if (f === 'phone' && rs !== 'need_phone') continue
    else if (f === 'fail' && !(st === 'done' && rs !== 'success' && rs !== 'need_phone' && rs !== 'cancelled')) continue
    out.push(item)
  }
  return out
})

const displayRows = computed(() => {
  const rows = filteredRows.value
  const start = (page.value - 1) * pageSize.value
  return rows.slice(start, start + pageSize.value)
})

const emptyHint = computed(() => {
  if (!stats.value.total) return '还没有待刷新账号'
  if (filter.value === 'running' && stats.value.running === 0) {
    return stats.value.pending > 0 ? '还没有账号开始执行，可切到「排队」或「全部」查看' : '当前没有进行中的账号'
  }
  if (search.value.trim()) return '没有匹配该邮箱的记录'
  return '当前筛选没有结果'
})

const batchHint = computed(() => {
  const n = stats.value.total
  if (n < 80) return ''
  return `大批量 ${n} 个账号：表格已分页（每页 ${pageSize.value} 条），建议先看「进行中」。关闭弹窗不会中断任务。`
})

function rowClassName({ row }) {
  if (row.status === 'running') return 'refresh-row-running'
  if (row.result?.status === 'success') return 'refresh-row-ok'
  if (row.result?.status === 'need_phone') return 'refresh-row-phone'
  if (row.result?.status === 'cancelled') return ''
  if (row.status === 'done') return 'refresh-row-fail'
  return ''
}

function closeStudio() {
  emit('update:modelValue', false)
}

function onDialogClosed() {
  if (running.value) {
    ElMessage.info('Token 刷新任务在后台继续运行，可随时重新打开查看进度')
  } else if (es) {
    closeEventSource()
  }
}

function markLeftoverCancelled() {
  const map = items.value
  let changed = false
  for (const em in map) {
    const it = map[em]
    if (it.status === 'pending' || it.status === 'running') {
      it.status = 'done'
      it.result = { status: 'cancelled', label: '已取消' }
      it.step_text = '已取消'
      changed = true
    }
  }
  if (changed) bump()
}

async function stopTask() {
  if (!taskId.value) {
    running.value = false
    stopLiveTimer()
    return
  }
  try {
    await stopTokenRefresh(taskId.value)
    ElMessage.success('已发送停止指令')
  } catch (_) {
    ElMessage.info('任务已停止')
  } finally {
    running.value = false
  }
}

function closeEventSource() {
  if (es) {
    try { es.close() } catch (_) {}
    es = null
  }
}

async function startTask() {
  const emails = targetEmails.value
  if (!emails.length) {
    ElMessage.warning('没有待刷新的账号列表')
    return
  }

  closeEventSource()
  running.value = true
  configCollapsed.value = true
  page.value = 1
  startLiveTimer()

  const map = Object.create(null)
  for (const em of emails) map[em] = makePendingItem(em)
  items.value = map
  bump()

  let proxiesParam = ''
  let proxyParam = ''
  if (form.proxy === '__POOL__') {
    proxiesParam = proxyList.value.join('\n')
  } else {
    proxyParam = (form.proxy || '').trim()
  }

  try {
    const res = await startTokenRefresh({
      emails,
      proxies: proxiesParam,
      proxy: proxyParam,
      proxy_country: form.proxyCountry || '',
      workers: form.workers || 10,
      timeout: form.timeout || 45,
      force_full_login: Boolean(form.forceFullLogin),
      sms_enabled: Boolean(form.smsEnabled),
      sms_provider: form.smsProvider || 'smsbower',
      sms_api_key: form.smsApiKey || '',
      sms_country: form.smsCountry || '52',
      sms_max_price: String(form.smsMaxPrice || ''),
      sms_max_attempts: Number(form.smsMaxAttempts) || 3,
      sms_timeout: Number(form.smsTimeout) || 80,
    })
    const id = res.taskId || res.task_id
    if (!id) throw new Error('未获取到任务 ID')
    taskId.value = id

    es = createSSE(tokenRefreshStreamUrl(id), {
      init: () => {},
      progress: (ev) => {
        try {
          const msg = JSON.parse(ev.data)
          if (!msg.email) return
          const patch = {}
          if (msg.status !== undefined) patch.status = msg.status
          if (msg.step_text !== undefined) patch.step_text = msg.step_text
          if (msg.result !== undefined) patch.result = msg.result
          if (msg.started_at !== undefined) patch.started_at = msg.started_at
          if (msg.elapsed !== undefined) patch.elapsed = msg.elapsed
          queuePatch(msg.email, patch)
        } catch (_) {}
      },
      end: () => {
        if (patchTimer) {
          clearTimeout(patchTimer)
          flushPatches()
        }
        stopLiveTimer()
        running.value = false
        closeEventSource()
        markLeftoverCancelled()
        ElMessage.success('Token 刷新任务已全部执行完成！凭证已同步写入数据库')
        emit('completed')
      },
    }, () => {
      stopLiveTimer()
      if (!running.value) closeEventSource()
    })
  } catch (e) {
    stopLiveTimer()
    running.value = false
    configCollapsed.value = false
    ElMessage.error('启动 Token 刷新失败: ' + (e.response?.data?.detail || e.message))
  }
}

function scrollLog() {
  nextTick(() => {
    if (logBoxRef.value) logBoxRef.value.scrollTop = logBoxRef.value.scrollHeight
  })
}

async function fetchLogLines() {
  if (!taskId.value || !currentLogItem.value) return
  try {
    const res = await getTokenRefreshLog(taskId.value, currentLogItem.value.email)
    logLines.value = res.lines || []
    scrollLog()
  } catch (e) {
    logLines.value = ['读取日志失败: ' + (e.response?.data?.detail || e.message)]
  }
}

function stopLogPoll() {
  if (logPollTimer) {
    clearInterval(logPollTimer)
    logPollTimer = null
  }
}

async function openItemLog(row) {
  currentLogItem.value = row
  logLines.value = []
  logModalVisible.value = true
  logLoading.value = true
  try {
    await fetchLogLines()
  } finally {
    logLoading.value = false
  }
  stopLogPoll()
  if (running.value) {
    logPollTimer = setInterval(fetchLogLines, 1200)
  }
}

watch(logModalVisible, (v) => {
  if (!v) stopLogPoll()
})

function logClass(line) {
  if (!line) return ''
  if (line.includes('✅') || line.includes('成功')) return 'log-hit'
  if (line.includes('❌') || line.includes('失败') || line.includes('封号')) return 'log-err'
  if (line.includes('⚠️')) return 'log-miss'
  return ''
}

async function downloadExport(format = 'txt') {
  if (!taskId.value) {
    ElMessage.warning('暂无当前任务 ID')
    return
  }
  try {
    const res = await downloadTokenRefreshExport(taskId.value, format)
    const mime = format === 'txt' ? 'text/plain;charset=utf-8' : 'application/json'
    const ext = format === 'txt' ? 'txt' : 'json'
    const blob = new Blob([res.data || res], { type: mime })
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `tokens_${format}_${taskId.value}.${ext}`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    window.URL.revokeObjectURL(url)
    ElMessage.success(`已下载 ${format.toUpperCase()} 格式凭证`)
  } catch (e) {
    ElMessage.error('下载导出凭证失败: ' + e.message)
  }
}

async function ensureSmsMeta() {
  if (!smsProviders.value.length) {
    try {
      const res = await getSmsProviders()
      smsProviders.value = res.providers || []
    } catch (_) {
      smsProviders.value = [
        { kind: 'smsbower', display_name: 'SmsBower' },
        { kind: 'herosms', display_name: 'HeroSMS' },
        { kind: 'cdk_sms', display_name: 'CDK' },
      ]
    }
  }
  smsCountriesLoading.value = true
  try {
    const r = await getSmsAllCountries(form.smsProvider || 'smsbower')
    smsAllCountries.value = r.countries || []
  } catch (_) {
    /* 允许手动输入国家 ID */
  } finally {
    smsCountriesLoading.value = false
  }
}

watch(
  () => [props.modelValue, props.emails],
  () => {
    if (!props.modelValue) return
    ensureSmsMeta()
    initFromEmails(props.emails || [])
  },
)

watch(
  () => form.smsProvider,
  () => {
    if (props.modelValue && form.smsEnabled) ensureSmsMeta()
  },
)

onUnmounted(() => {
  stopLiveTimer()
  stopLogPoll()
  if (patchTimer) clearTimeout(patchTimer)
  closeEventSource()
})
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    width="980px"
    top="3vh"
    class="oa-custom-dialog plus-dialog health-dialog refresh-dialog"
    :close-on-click-modal="false"
    @update:model-value="emit('update:modelValue', $event)"
    @closed="onDialogClosed"
  >
    <template #header>
      <div class="oa-header">
        <div class="oa-header-title">
          <span class="oa-title-badge health-badge">TOKEN</span>
          <span class="oa-title-text">Token 智能双模刷新与重获工作台</span>
          <el-tag size="small" type="primary" round effect="dark">RT极速置换 / Full OAuth重登</el-tag>
          <el-tag size="small" type="info" round effect="plain">{{ targetEmails.length }} 个账号</el-tag>
          <el-tag v-if="running" size="small" type="warning" round effect="plain">
            进行中 {{ stats.running }} · 排队 {{ stats.pending }}
          </el-tag>
        </div>
        <div class="oa-header-extra">
          <el-button size="small" text @click="configCollapsed = !configCollapsed">
            <el-icon><Setting /></el-icon>{{ configCollapsed ? '展开参数配置' : '收起参数配置' }}
          </el-button>
        </div>
      </div>
    </template>

    <div class="oa-dialog-container">
      <el-collapse-transition>
        <div v-show="!configCollapsed" class="oa-config-card" style="padding: 10px 14px 12px">
          <el-tabs v-model="activeTab" class="oa-config-tabs">
            <el-tab-pane label="🌐 网络代理 & 刷新模式" name="network">
              <el-form label-position="top" :disabled="running" size="small" style="margin-top: 6px">
                <el-row :gutter="12">
                  <el-col :xs="24" :sm="12" :md="8">
                    <el-form-item label="检测/登录代理 (支持代理池轮询/直连)">
                      <el-select
                        v-model="form.proxy" filterable clearable allow-create default-first-option
                        placeholder="选择或输入代理" style="width: 100%"
                      >
                        <el-option
                          v-if="proxyList.length"
                          label="🌐 全局代理池轮询 (自动多Worker分配)"
                          value="__POOL__"
                        />
                        <el-option v-for="p in proxyList" :key="p" :label="p" :value="p" />
                      </el-select>
                    </el-form-item>
                  </el-col>
                  <el-col :xs="24" :sm="12" :md="6">
                    <el-form-item label="代理目标国家">
                      <el-select v-model="form.proxyCountry" filterable allow-create placeholder="国家" style="width: 100%">
                        <el-option v-for="c in COUNTRY_OPTIONS" :key="c.value" :label="c.label" :value="c.value" />
                      </el-select>
                    </el-form-item>
                  </el-col>
                  <el-col :xs="12" :sm="6" :md="5">
                    <el-form-item label="并发 Worker">
                      <el-input-number v-model="form.workers" :min="1" :max="20" style="width: 100%" />
                    </el-form-item>
                  </el-col>
                  <el-col :xs="12" :sm="6" :md="5">
                    <el-form-item label="超时(秒)">
                      <el-input-number v-model="form.timeout" :min="10" :max="120" style="width: 100%" />
                    </el-form-item>
                  </el-col>
                </el-row>
                <el-row :gutter="12">
                  <el-col :span="24">
                    <el-checkbox v-model="form.forceFullLogin">
                      强制走完整 OAuth 重新登录流程（跳过 RT 快速换取，直接打 OpenAI 登录端点获取全新全套凭证）
                    </el-checkbox>
                  </el-col>
                </el-row>
                <div class="refresh-config-actions">
                  <el-button size="small" type="primary" plain :disabled="running" @click="saveFormAsDefault">
                    保存当前设置
                  </el-button>
                  <span class="plus-config-desc" style="margin: 0">
                    改完会自动记住；也可点保存。下次打开沿用并发、国家和代理。
                  </span>
                </div>
                <div class="plus-config-desc">
                  💡 <b>智能机制</b>：有历史 Refresh Token 的账号优先 <b>200ms 极速置换</b>；失效或无 RT 账号自动触发 <b>Full OAuth 登录重获</b> 并自动写回数据库。
                </div>
              </el-form>
            </el-tab-pane>

            <el-tab-pane label="📱 手机号风控接码设置 (可选)" name="sms">
              <el-form label-position="top" :disabled="running" size="small" style="margin-top: 6px">
                <el-row :gutter="12">
                  <el-col :span="24">
                    <el-checkbox v-model="form.smsEnabled">
                      启用 SMS 自动接码解封（遇到 OpenAI 要求绑定手机号时自动调用接码平台）
                    </el-checkbox>
                  </el-col>
                </el-row>
                <el-row v-if="form.smsEnabled" :gutter="12" style="margin-top: 6px">
                  <el-col :xs="24" :sm="8">
                    <el-form-item label="接码平台">
                      <el-select v-model="form.smsProvider" style="width: 100%">
                        <el-option
                          v-for="p in smsProviders"
                          :key="p.kind"
                          :label="p.display_name"
                          :value="p.kind"
                        />
                      </el-select>
                    </el-form-item>
                  </el-col>
                  <el-col :xs="24" :sm="10">
                    <el-form-item label="API Key">
                      <el-input v-model="form.smsApiKey" placeholder="平台 API 密钥" clearable />
                    </el-form-item>
                  </el-col>
                  <el-col :xs="24" :sm="6">
                    <el-form-item label="接码国家">
                      <el-select
                        v-model="form.smsCountry"
                        filterable
                        allow-create
                        default-first-option
                        :loading="smsCountriesLoading"
                        placeholder="搜索国家名或输入国家ID"
                        style="width: 100%"
                      >
                        <el-option v-for="sc in smsCountryOptions" :key="sc.value" :label="sc.label" :value="sc.value" />
                      </el-select>
                    </el-form-item>
                  </el-col>
                </el-row>
              </el-form>
            </el-tab-pane>
          </el-tabs>
        </div>
      </el-collapse-transition>

      <div v-if="batchHint" class="refresh-batch-hint">{{ batchHint }}</div>

      <div class="plus-kpi-grid">
        <div class="plus-kpi-card">
          <span class="kpi-label">已处理 / 总数</span>
          <span class="kpi-num">{{ stats.done }} / {{ stats.total }}</span>
        </div>
        <div class="plus-kpi-card hit-active">
          <span class="kpi-label">⚡ RT极速置换成功</span>
          <span class="kpi-num text-primary">{{ stats.rt_fast_ok }}</span>
        </div>
        <div class="plus-kpi-card hit-promo">
          <span class="kpi-label">🔑 Full OAuth 重登成功</span>
          <span class="kpi-num text-success">{{ stats.full_login_ok }}</span>
        </div>
        <div class="plus-kpi-card" :class="{ 'card-warn': stats.need_phone > 0 }">
          <span class="kpi-label">需要手机号</span>
          <span class="kpi-num" :class="stats.need_phone > 0 ? 'text-warning' : ''">{{ stats.need_phone }}</span>
        </div>
        <div class="plus-kpi-card" :class="{ 'card-warn': stats.error > 0 }">
          <span class="kpi-label">失败 / 异常</span>
          <span class="kpi-num text-danger">{{ stats.error }}</span>
        </div>
        <div class="plus-progress-cell">
          <el-progress
            :percentage="stats.percent"
            :status="stats.done === stats.total && stats.total > 0 ? 'success' : ''"
            :stroke-width="8"
            striped
            :striped-flow="running"
          />
        </div>
      </div>

      <div class="health-table-filter-bar">
        <el-radio-group v-model="filter" size="small" class="health-filter-radio" @change="page = 1">
          <el-radio-button label="all">全部 ({{ stats.total }})</el-radio-button>
          <el-radio-button label="running">进行中 ({{ stats.running }})</el-radio-button>
          <el-radio-button label="pending">排队 ({{ stats.pending }})</el-radio-button>
          <el-radio-button label="success">成功 ({{ stats.success }})</el-radio-button>
          <el-radio-button label="fail">
            <span :class="{ 'text-danger': stats.error > 0 }">失败 ({{ stats.error }})</span>
          </el-radio-button>
          <el-radio-button label="phone">需手机 ({{ stats.need_phone }})</el-radio-button>
        </el-radio-group>
        <div class="health-filter-right">
          <el-input
            v-model="search"
            placeholder="快速过滤邮箱..."
            clearable
            size="small"
            class="health-search-input"
            :prefix-icon="Search"
            @input="page = 1"
          />
        </div>
      </div>

      <div class="plus-table-box health-table-box">
        <el-table
          :data="displayRows"
          size="small"
          stripe
          height="300"
          class="macos-table"
          row-key="email"
          :row-class-name="rowClassName"
          :highlight-current-row="false"
        >
          <el-table-column prop="email" label="账号邮箱" min-width="210" show-overflow-tooltip>
            <template #default="{ row }">
              <button class="macos-tag-btn copy-btn" title="点击复制邮箱" @click="copyText(row.email)">
                <span class="mono">{{ row.email }}</span>
                <el-icon class="copy-ico"><CopyDocument /></el-icon>
              </button>
            </template>
          </el-table-column>

          <el-table-column label="刷新模式" width="130" align="center">
            <template #default="{ row }">
              <span v-if="row.result?.method === 'rt_fast'" class="mode-pill mode-rt">⚡ RT 极速</span>
              <span v-else-if="row.result?.method === 'st_fast'" class="mode-pill mode-st">⚡ Session</span>
              <span v-else-if="row.result?.method === 'full_login' || row.result?.method === 'full_oauth'" class="mode-pill mode-oauth">🔑 OAuth</span>
              <span v-else-if="row.status === 'running'" class="mono text-primary text-xs">执行中...</span>
              <span v-else class="text-muted">—</span>
            </template>
          </el-table-column>

          <el-table-column label="当前状态 / 步骤" min-width="170" show-overflow-tooltip>
            <template #default="{ row }">
              <span v-if="row.status === 'running'" class="running-step">
                <el-icon class="is-loading" style="margin-right: 4px"><Loading /></el-icon>
                {{ row.step_text || '正在刷新...' }}
              </span>
              <span v-else-if="row.status === 'pending'" class="status-pill status-pending">排队中</span>
              <span
                v-else-if="row.result"
                class="status-pill"
                :class="{
                  'status-ok': row.result.status === 'success',
                  'status-phone': row.result.status === 'need_phone',
                  'status-fail': row.result.status !== 'success' && row.result.status !== 'need_phone',
                }"
              >
                {{ row.result.label || row.result.status }}
              </span>
              <span v-else class="text-muted">—</span>
            </template>
          </el-table-column>

          <el-table-column label="耗时" width="80" align="center">
            <template #default="{ row }">
              <span v-if="displayElapsed(row)" class="mono text-muted">{{ displayElapsed(row) }}s</span>
              <span v-else-if="row.status === 'running'" class="mono text-primary">...</span>
              <span v-else class="text-muted">—</span>
            </template>
          </el-table-column>

          <el-table-column label="操作" width="85" align="center" fixed="right">
            <template #default="{ row }">
              <el-button size="small" text type="primary" :disabled="row.status === 'pending'" @click="openItemLog(row)">
                <el-icon><Document /></el-icon>日志
              </el-button>
            </template>
          </el-table-column>

          <template #empty>
            <div class="refresh-empty">{{ emptyHint }}</div>
          </template>
        </el-table>
      </div>

      <div class="health-pagination-bar">
        <span class="health-page-count-tip text-muted text-xs">
          显示第 {{ filteredRows.length > 0 ? (page - 1) * pageSize + 1 : 0 }}
          - {{ Math.min(page * pageSize, filteredRows.length) }} 条
          · 过滤 <b>{{ filteredRows.length }}</b> / 共 {{ stats.total }}
        </span>
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :page-sizes="[30, 50, 100, 200]"
          :total="filteredRows.length"
          layout="sizes, prev, pager, next"
          size="small"
        />
      </div>
    </div>

    <template #footer>
      <div class="oa-footer">
        <div class="footer-left">
          <el-button type="primary" plain size="small" :disabled="stats.success === 0" @click="downloadExport('txt')">
            <el-icon><Download /></el-icon>下载 TXT 凭证 ({{ stats.success }})
          </el-button>
          <el-button type="primary" size="small" :disabled="stats.success === 0" @click="downloadExport('cpa')">
            <el-icon><Download /></el-icon>下载 CPA JSON
          </el-button>
          <el-button type="success" size="small" :disabled="stats.success === 0" @click="downloadExport('sub2api')">
            <el-icon><Download /></el-icon>下载 Sub2API JSON
          </el-button>
        </div>
        <div class="footer-right">
          <el-button size="small" @click="closeStudio">
            {{ running ? '后台运行' : '关闭' }}
          </el-button>
          <el-button v-if="running" size="small" type="danger" plain @click="stopTask">
            <el-icon><SwitchButton /></el-icon>停止任务
          </el-button>
          <el-button
            v-else
            type="primary"
            class="start-gradient-btn"
            :disabled="!targetEmails.length"
            @click="startTask"
          >
            <el-icon><VideoPlay /></el-icon>{{ taskId ? '重新刷新' : '开始刷新/重获' }}
          </el-button>
        </div>
      </div>
    </template>
  </el-dialog>

  <el-dialog
    v-model="logModalVisible"
    width="780px"
    top="8vh"
    class="macos-terminal-dialog"
    :close-on-click-modal="false"
  >
    <template #header>
      <div class="modal-header">
        <div class="window-dots">
          <span class="dot red"></span>
          <span class="dot yellow"></span>
          <span class="dot green"></span>
        </div>
        <div class="modal-title-info">
          <span class="modal-email">{{ currentLogItem?.email }}</span>
          <el-tag size="small" type="primary" effect="plain" class="modal-run-tag">
            Token 刷新/重登日志
          </el-tag>
        </div>
      </div>
    </template>

    <div class="modal-terminal-wrap">
      <div ref="logBoxRef" class="modal-terminal-body">
        <div v-for="(line, idx) in logLines" :key="idx" class="terminal-line" :class="logClass(line)">
          {{ line }}
        </div>
        <div v-if="!logLines.length" class="terminal-empty">
          {{ logLoading ? '正在加载日志...' : '暂无详细日志' }}
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
          <el-button size="small" type="primary" @click="logModalVisible = false">关闭</el-button>
        </div>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
.oa-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.oa-header-title {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.oa-title-badge {
  background: #0284c7;
  color: #fff;
  font-size: 11px;
  font-weight: 700;
  padding: 1px 7px;
  border-radius: 4px;
  letter-spacing: 0.5px;
}
.oa-title-text {
  font-size: 14px;
  font-weight: 600;
}
.oa-dialog-container {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.oa-config-card {
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 12px;
}
.plus-config-desc {
  font-size: 11.5px;
  color: var(--el-text-color-secondary);
  line-height: 1.5;
  margin-top: 4px;
}
.refresh-config-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin: 8px 0 4px;
}
.plus-kpi-grid {
  display: grid;
  grid-template-columns: repeat(5, 1fr) 2fr;
  gap: 8px;
  align-items: center;
}
.plus-kpi-card {
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  padding: 6px 10px;
  display: flex;
  flex-direction: column;
}
.plus-kpi-card .kpi-label {
  font-size: 10.5px;
  color: var(--el-text-color-secondary);
  margin-bottom: 2px;
}
.plus-kpi-card .kpi-num {
  font-size: 15px;
  font-weight: 700;
  font-family: var(--el-font-family-monospace, monospace);
  line-height: 1.1;
}
.plus-kpi-card.hit-active {
  border-color: var(--el-color-primary-light-5);
  background: var(--el-color-primary-light-9);
}
.plus-kpi-card.hit-promo {
  border-color: rgba(16, 185, 129, 0.4);
  background: rgba(16, 185, 129, 0.08);
}
.plus-kpi-card.card-warn {
  border-color: rgba(239, 68, 68, 0.4);
  background: rgba(239, 68, 68, 0.08);
}
.plus-progress-cell { padding-left: 6px; }
.health-table-filter-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
}
.health-search-input { width: 180px; }
.health-pagination-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 6px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
}
.plus-table-box {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  overflow: hidden;
  height: 300px;
}
.start-gradient-btn {
  background: #059669;
  border: none;
}
.start-gradient-btn:hover:not(:disabled) { background: #047857; }
.oa-footer {
  display: flex !important;
  align-items: center !important;
  justify-content: space-between !important;
  width: 100% !important;
  gap: 12px !important;
}
.oa-footer .footer-left,
.oa-footer .footer-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
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
  max-width: 100%;
}
.macos-tag-btn.copy-btn:hover {
  background: var(--el-color-primary-light-9);
  border-color: var(--el-color-primary-light-7);
  color: var(--el-color-primary);
}
.copy-btn .copy-ico { font-size: 11px; opacity: 0.5; }
.running-step {
  display: inline-flex;
  align-items: center;
  color: var(--el-color-primary);
  font-size: 12px;
}
.mode-pill, .status-pill {
  display: inline-flex;
  align-items: center;
  font-size: 11px;
  line-height: 1.2;
  padding: 2px 7px;
  border-radius: 999px;
  font-weight: 600;
}
.mode-rt { background: rgba(37, 99, 235, 0.12); color: #3b82f6; }
.mode-st { background: rgba(14, 165, 233, 0.12); color: #0ea5e9; }
.mode-oauth { background: rgba(16, 185, 129, 0.12); color: #10b981; }
.status-pending { background: var(--el-fill-color); color: var(--el-text-color-secondary); }
.status-ok { background: rgba(16, 185, 129, 0.12); color: #10b981; }
.status-warn { background: rgba(245, 158, 11, 0.14); color: #d97706; }
.status-phone { background: rgba(245, 158, 11, 0.14); color: #d97706; }
.status-fail { background: rgba(239, 68, 68, 0.12); color: #ef4444; }
.refresh-batch-hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  background: var(--el-color-primary-light-9);
  border: 1px solid var(--el-color-primary-light-7);
  border-radius: 6px;
  padding: 6px 10px;
  line-height: 1.45;
}
.refresh-empty {
  padding: 24px 12px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
:deep(.refresh-row-running) { background: var(--el-color-primary-light-9) !important; }
:deep(.refresh-row-warn) { background: rgba(245, 158, 11, 0.08) !important; }
:deep(.oa-custom-dialog) { border-radius: 12px; overflow: hidden; }
:deep(.oa-custom-dialog .el-dialog__header) {
  padding: 12px 18px;
  margin-right: 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
  background: var(--el-fill-color-light);
}
:deep(.oa-custom-dialog .el-dialog__body) { padding: 14px 18px; }
:deep(.oa-custom-dialog .el-dialog__footer) {
  padding: 10px 18px;
  border-top: 1px solid var(--el-border-color-lighter);
  background: var(--el-fill-color-light);
}
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
:deep(.macos-terminal-dialog .el-dialog__body) { padding: 0; }
:deep(.macos-terminal-dialog .el-dialog__footer) {
  padding: 10px 16px;
  background: #1e1e24;
  border-top: 1px solid #2a2a34;
}
.modal-header { display: flex; align-items: center; gap: 12px; }
.window-dots { display: flex; align-items: center; gap: 6px; }
.dot { width: 9px; height: 9px; border-radius: 50%; }
.dot.red { background: #ff5f56; }
.dot.yellow { background: #ffbd2e; }
.dot.green { background: #27c93f; }
.modal-title-info { display: flex; align-items: center; gap: 8px; flex: 1; }
.modal-email {
  font-size: 13px;
  font-weight: 600;
  color: #f1f5f9;
  font-family: var(--el-font-family-monospace, monospace);
}
.modal-terminal-wrap { height: 400px; display: flex; flex-direction: column; }
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
.terminal-empty { color: #64748b; }
.log-hit { color: #34d399; }
.log-miss { color: #fbbf24; }
.log-err { color: #f87171; }
.modal-footer { display: flex; align-items: center; justify-content: space-between; }
.log-count-tip { font-size: 11px; color: #94a3b8; }
.modal-footer-btns { display: flex; gap: 8px; }
.text-xs { font-size: 11px; }
</style>
