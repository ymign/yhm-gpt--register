<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Plus,
  Refresh,
  Download,
  CopyDocument,
  VideoPlay,
  CircleCheck,
  Warning,
  Link,
  CreditCard,
  Setting,
  SwitchButton,
  Close,
  Iphone,
  Key,
  Delete,
  Search,
  Position,
  DocumentAdd,
  Promotion,
  ChatDotSquare,
} from '@element-plus/icons-vue'
import { listRegistered } from '@/api/register'
import { copyText, fmtTime, createSSE } from '@/api/request'
import {
  startPayPalPayTask,
  stopPayPalPayTask,
  paypalPayStreamUrl,
  getPayPalPayTaskLog,
  submitPayPalPayInput,
} from '@/api/paypalPay'

const activeTab = ref('manual_table')

// ── 参数设置 ──
const form = reactive({
  country: 'TH',
  flow_mode: 'elevation',
  workers: 3,
  default_phone: '+66812345678',
})

const COUNTRY_OPTIONS = [
  { value: 'TH', label: 'TH · 泰国 (推荐免税0元)', phone: '+66812345678' },
  { value: 'BR', label: 'BR · 巴西 (推荐0元)', phone: '+55119800133818' },
  { value: 'US', label: 'US · 美国', phone: '+12025550123' },
  { value: 'GB', label: 'GB · 英国', phone: '+447700900123' },
  { value: 'DE', label: 'DE · 德国', phone: '+4915123456789' },
  { value: 'NL', label: 'NL · 荷兰', phone: '+31612345678' },
  { value: 'JP', label: 'JP · 日本', phone: '+819012345678' },
  { value: 'PH', label: 'PH · 菲律宾', phone: '+639171234567' },
  { value: 'ID', label: 'ID · 印尼', phone: '+6281234567890' },
]

function handleCountryChange(val) {
  const c = COUNTRY_OPTIONS.find((item) => item.value === val)
  if (c && c.phone) {
    form.default_phone = c.phone
  }
}

// ──────────────── 模式 1：动态表格录入模式 (用户重点需求) ────────────────
let rowIdCounter = 1
function createNewManualRow(initData = {}) {
  return {
    id: `row_${Date.now()}_${rowIdCounter++}`,
    ba_token: initData.ba_token || '',
    phone: initData.phone || form.default_phone || '+66812345678',
    email: initData.email || '',
    status: 'idle', // idle, running, awaiting_otp, success, error, cancelled
    step_text: '待支付',
    prompt: '',
    otpInput: '',
    submittingOtp: false,
    taskId: '',
    elapsed: 0,
  }
}

const manualRows = ref([createNewManualRow()])
const manualSelected = ref([])

// 新增单行
function handleAddManualRow() {
  manualRows.value.push(createNewManualRow())
}

// 移除单行
function handleRemoveManualRow(index) {
  manualRows.value.splice(index, 1)
  if (manualRows.value.length === 0) {
    manualRows.value.push(createNewManualRow())
  }
}

// 清空全部行
function handleClearManualRows() {
  ElMessageBox.confirm('确定要清空当前所有录入的代付数据吗？', '清空确认', {
    confirmButtonText: '确定清空',
    cancelButtonText: '取消',
    type: 'warning',
  }).then(() => {
    manualRows.value = [createNewManualRow()]
    ElMessage.success('已清空列表')
  })
}

// 批量粘贴导入弹窗
const pasteDialogVisible = ref(false)
const pasteText = ref('')

function openPasteDialog() {
  pasteText.value = ''
  pasteDialogVisible.value = true
}

function handleConfirmPaste() {
  const lines = pasteText.value.split('\n').map((l) => l.trim()).filter(Boolean)
  if (!lines.length) {
    ElMessage.warning('请输入要导入的内容')
    return
  }

  const newItems = lines.map((l) => {
    const parts = l.split('----')
    if (parts.length >= 3) {
      return { email: parts[0].trim(), ba_token: parts[1].trim(), phone: parts[2].trim() }
    }
    if (parts.length === 2) {
      if (parts[1].startsWith('+') || /^\d{8,}$/.test(parts[1])) {
        return { email: '', ba_token: parts[0].trim(), phone: parts[1].trim() }
      }
      return { email: parts[0].trim(), ba_token: parts[1].trim(), phone: form.default_phone }
    }
    return { email: '', ba_token: l, phone: form.default_phone }
  })

  // 如果原本只有一条空行，先清空
  if (manualRows.value.length === 1 && !manualRows.value[0].ba_token.trim()) {
    manualRows.value = []
  }

  for (const item of newItems) {
    manualRows.value.push(createNewManualRow(item))
  }

  pasteDialogVisible.value = false
  ElMessage.success(`成功导入 ${newItems.length} 条数据`)
}

// ──────────────── 模式 2：从已提链列表勾选 ────────────────
const loading = ref(false)
const autoRows = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(30)
const autoSelected = ref([])

async function loadAutoData() {
  loading.value = true
  try {
    const res = await listRegistered({
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
      filter: 'extract_success',
    })
    const list = (res.items || []).filter((r) => r.extract_link && r.extract_link.link_url)
    autoRows.value = list.map((r) => ({
      ...r,
      _phone: r._phone || form.default_phone || '+66812345678',
      _otpInput: '',
      _submittingOtp: false,
    }))
    total.value = res.total || 0
  } catch (e) {
    ElMessage.error(e.message || '加载列表失败')
  } finally {
    loading.value = false
  }
}

function batchFillAutoPhones() {
  if (!autoSelected.value.length) {
    ElMessage.warning('请先勾选要填充手机号的账号')
    return
  }
  const phoneVal = form.default_phone.trim()
  autoSelected.value.forEach((r) => {
    r._phone = phoneVal
  })
  ElMessage.success(`已为选中的 ${autoSelected.value.length} 个账号填充手机号`)
}

// ──────────────── 任务调度与 SSE 流管理 ────────────────
const running = ref(false)
const activeTaskIds = ref(new Set())
const taskMap = reactive({})
const sseStreams = reactive({})

// 全局实时日志流
const liveLogs = ref([])
const liveLogFilter = ref('')
const liveLogAutoScroll = ref(true)
const liveLogTerminalRef = ref(null)

// 待接码列表聚合
const pendingOtpList = computed(() => {
  const list = []
  // 从 manualRows 提取
  manualRows.value.forEach((r) => {
    if (r.status === 'awaiting_otp') {
      list.push({
        type: 'manual',
        row: r,
        key: r.email || r.ba_token || r.id,
        phone: r.phone,
        prompt: r.prompt,
      })
    }
  })
  // 从 autoRows 提取
  autoRows.value.forEach((r) => {
    const t = taskMap[r.email]
    if (t && t.status === 'awaiting_otp') {
      list.push({
        type: 'auto',
        row: r,
        key: r.email,
        phone: r._phone || form.default_phone,
        prompt: t.prompt,
      })
    }
  })
  return list
})

// KPI 统计
const allTaskItems = computed(() => Object.values(taskMap))
const stats = computed(() => {
  const list = allTaskItems.value
  const tot = list.length
  const success = list.filter((i) => i.status === 'success').length
  const failed = list.filter((i) => i.status === 'error' || i.status === 'cancelled').length
  const active = list.filter((i) => i.status === 'running' || i.status === 'awaiting_otp').length
  const pending = list.filter((i) => i.status === 'pending').length
  const percent = tot > 0 ? Math.round(((success + failed) / tot) * 100) : 0
  return { tot, success, failed, active, pending, percent }
})

// ── 单账号日志弹窗 ──
const logVisible = ref(false)
const logLoading = ref(false)
const logKey = ref('')
const logLines = ref([])
const logTerminalBodyRef = ref(null)

function scrollLogModalToBottom() {
  nextTick(() => {
    if (logTerminalBodyRef.value) {
      logTerminalBodyRef.value.scrollTop = logTerminalBodyRef.value.scrollHeight
    }
  })
}

function scrollGlobalTerminalToBottom() {
  if (!liveLogAutoScroll.value) return
  nextTick(() => {
    if (liveLogTerminalRef.value) {
      liveLogTerminalRef.value.scrollTop = liveLogTerminalRef.value.scrollHeight
    }
  })
}

// ──────────────── 执行支付动作 ────────────────

// 单行启动支付 (表格录入模式下点击某一行)
function handleStartSingleRow(row) {
  const token = (row.ba_token || '').trim()
  if (!token) {
    ElMessage.warning('请先输入有效的 0元 BA Token 或授权链接')
    return
  }
  const phone = (row.phone || form.default_phone || '').trim()
  if (!phone) {
    ElMessage.warning('请填写手机号')
    return
  }

  const key = row.email || token
  row.status = 'running'
  row.step_text = '正在启动协议会话...'
  row.prompt = ''
  row.otpInput = ''

  taskMap[key] = {
    key,
    email: row.email,
    ba_token: token,
    phone,
    status: 'running',
    step_text: '正在启动协议会话...',
    prompt: '',
    started_at: Date.now() / 1000,
    elapsed: 0,
  }

  startPayPalPayTask({
    items: [{ email: row.email, ba_token: token, phone }],
    country: form.country,
    flow_mode: form.flow_mode,
    workers: 1,
  })
    .then((res) => {
      row.taskId = res.task_id
      running.value = true
      activeTaskIds.value.add(res.task_id)
      ElMessage.success(`[${token.slice(0, 14)}...] 协议支付任务已启动！`)
      connectTaskStream(res.task_id, [row])
    })
    .catch((e) => {
      row.status = 'error'
      row.step_text = e.message || '启动失败'
      ElMessage.error(e.message || '启动支付任务失败')
    })
}

// 批量启动支付 (当前 Tab 下所有待处理或勾选数据)
function handleStartBatch() {
  let payItems = []
  let targetRowList = []

  if (activeTab.value === 'manual_table') {
    const list = manualSelected.value.length ? manualSelected.value : manualRows.value
    const valid = list.filter((r) => r.ba_token && r.ba_token.trim())
    if (!valid.length) {
      ElMessage.warning('请先在表格中输入至少一条包含 BA Token 的数据')
      return
    }
    payItems = valid.map((r) => ({
      email: r.email ? r.email.trim() : '',
      ba_token: r.ba_token.trim(),
      phone: (r.phone || form.default_phone || '').trim(),
    }))
    targetRowList = valid
  } else {
    const list = autoSelected.value.length ? autoSelected.value : autoRows.value
    if (!list.length) {
      ElMessage.warning('当前没有可执行的已提链账号')
      return
    }
    payItems = list.map((r) => ({
      email: r.email,
      ba_token: r.extract_link?.ba_token || r.extract_link?.link_url || '',
      phone: (r._phone || form.default_phone || '').trim(),
    }))
    targetRowList = list
  }

  for (const r of targetRowList) {
    r.status = 'running'
    r.step_text = '排队准备中...'
    r.prompt = ''
    r.otpInput = ''
    const k = r.email || r.ba_token || r.id
    taskMap[k] = {
      key: k,
      email: r.email || '',
      ba_token: r.ba_token || '',
      phone: r.phone || r._phone || form.default_phone,
      status: 'running',
      step_text: '排队准备中...',
      prompt: '',
      started_at: Date.now() / 1000,
      elapsed: 0,
    }
  }

  startPayPalPayTask({
    items: payItems,
    country: form.country,
    flow_mode: form.flow_mode,
    workers: form.workers,
  })
    .then((res) => {
      running.value = true
      activeTaskIds.value.add(res.task_id)
      ElMessage.success(`批量协议支付已启动: 共 ${payItems.length} 个任务, 并发数=${form.workers}`)
      connectTaskStream(res.task_id, targetRowList)
    })
    .catch((e) => {
      ElMessage.error(e.message || '启动支付任务失败')
    })
}

// 停止全部任务
function handleStopAll() {
  if (!activeTaskIds.value.size) return
  activeTaskIds.value.forEach((tid) => {
    stopPayPalPayTask(tid).catch(() => {})
  })
  activeTaskIds.value.clear()
  running.value = false
  ElMessage.info('已发送停止全部任务指令')
}

// ── SSE 实时流连接与多行状态绑定 ──
function connectTaskStream(taskId, boundRows = []) {
  if (sseStreams[taskId]) {
    sseStreams[taskId].close()
  }

  const es = createSSE(paypalPayStreamUrl(taskId), {
    init: (ev) => {
      try {
        const data = JSON.parse(ev.data)
        if (data.items) {
          for (const [k, it] of Object.entries(data.items)) {
            taskMap[k] = {
              key: k,
              email: it.email || '',
              ba_token: it.ba_token || '',
              phone: it.phone || '',
              status: it.status,
              step_text: it.step_text || it.status,
              prompt: it.prompt || '',
              started_at: it.started_at || 0,
              elapsed: it.elapsed || 0,
            }
          }
        }
        if (data.logs && Array.isArray(data.logs)) {
          liveLogs.value = [...data.logs]
          scrollGlobalTerminalToBottom()
        }
      } catch (_) {}
    },
    progress: (ev) => {
      try {
        const data = JSON.parse(ev.data)
        const k = data.key || data.email

        // 1. 同步到全局 taskMap
        if (k && taskMap[k]) {
          if (data.status !== undefined) taskMap[k].status = data.status
          if (data.step_text !== undefined) taskMap[k].step_text = data.step_text
          if (data.prompt !== undefined) taskMap[k].prompt = data.prompt
          if (data.result !== undefined) taskMap[k].result = data.result
          if (data.started_at !== undefined) taskMap[k].started_at = data.started_at
          if (data.elapsed !== undefined) taskMap[k].elapsed = data.elapsed
        }

        // 2. 同步更新 manualRows 中匹配的行
        manualRows.value.forEach((r) => {
          const matchKey = r.email || r.ba_token
          if (matchKey && (matchKey === k || k.includes(matchKey) || matchKey.includes(k))) {
            if (data.status !== undefined) r.status = data.status
            if (data.step_text !== undefined) r.step_text = data.step_text
            if (data.prompt !== undefined) r.prompt = data.prompt
            if (data.elapsed !== undefined) r.elapsed = data.elapsed
          }
        })

        // 3. 同步更新 autoRows 中匹配的行
        autoRows.value.forEach((r) => {
          if (r.email === k || (r.extract_link?.ba_token && r.extract_link.ba_token === k)) {
            // autoRows 的渲染直接依赖 taskMap[row.email]
          }
        })
      } catch (_) {}
    },
    log: (ev) => {
      try {
        const data = JSON.parse(ev.data)
        const line = data.line || ''
        const key = data.key || ''
        if (line) {
          liveLogs.value.push(line)
          if (liveLogs.value.length > 800) liveLogs.value.shift()
          scrollGlobalTerminalToBottom()

          // 如果单账号日志弹窗当前打开，且匹配该 key，实时追加
          if (logVisible.value && logKey.value) {
            if (!key || logKey.value === key || logKey.value.includes(key) || key.includes(logKey.value)) {
              logLines.value.push(line)
              scrollLogModalToBottom()
            }
          }
        }
      } catch (_) {}
    },
    end: () => {
      activeTaskIds.value.delete(taskId)
      if (activeTaskIds.value.size === 0) {
        running.value = false
        ElMessage.success('PayPal 协议代付任务执行完成！')
      }
      if (activeTab.value === 'auto') {
        loadAutoData()
      }
    },
  })

  sseStreams[taskId] = es
}

// ──────────────── 行内 2FA 验证码输入与确认 ────────────────
async function handleInlineOtpSubmit(row) {
  const key = row.email || row.ba_token || row.id || row.key
  const val = (row.otpInput || row._otpInput || '').trim()
  if (!val) {
    ElMessage.warning('请输入 6 位短信验证码或新手机号')
    return
  }

  row.submittingOtp = true
  row._submittingOtp = true

  const targetTaskId = row.taskId || Array.from(activeTaskIds.value)[0] || 'latest'

  try {
    await submitPayPalPayInput(targetTaskId, key, val)
    ElMessage.success(`[${(row.email || row.ba_token || key).slice(0, 16)}] 验证码已提交，正在继续执行协议...`)
    row.otpInput = ''
    row._otpInput = ''
    row.status = 'running'
    row.step_text = '已收到验证码，正在继续授权...'
    if (taskMap[key]) {
      taskMap[key].status = 'running'
      taskMap[key].step_text = '已收到验证码，正在继续授权...'
      taskMap[key].prompt = ''
    }
  } catch (e) {
    ElMessage.error(e.message || '提交验证码失败')
  } finally {
    row.submittingOtp = false
    row._submittingOtp = false
  }
}

// ── 查看单账号详细实时日志 ──
async function handleViewLog(item) {
  logKey.value = item.email || item.ba_token || item.key || item.id
  logVisible.value = true
  logLoading.value = true
  logLines.value = []

  const targetTaskId = item.taskId || Array.from(activeTaskIds.value)[0] || 'latest'

  try {
    const res = await getPayPalPayTaskLog(targetTaskId, logKey.value)
    logLines.value = res.lines || []
    scrollLogModalToBottom()
  } catch (e) {
    logLines.value = [`加载日志提示: ${e.message}`]
  } finally {
    logLoading.value = false
  }
}

// 日志高亮分类
function getLogLineClass(line) {
  if (line.includes('成功') || line.includes('🎉') || line.includes('completed successfully')) return 'log-success'
  if (line.includes('失败') || line.includes('错误') || line.includes('❌') || line.includes('error') || line.includes('failed')) return 'log-error'
  if (line.includes('阶段3') || line.includes('2FA') || line.includes('验证码') || line.includes('SMS')) return 'log-otp'
  if (line.includes('阶段4') || line.includes('授权') || line.includes('authorize') || line.includes('return URL')) return 'log-auth'
  if (line.includes('阶段1') || line.includes('阶段2') || line.includes('阶段0')) return 'log-phase'
  return 'log-normal'
}

onMounted(() => {
  loadAutoData()
})

onUnmounted(() => {
  Object.values(sseStreams).forEach((s) => s.close())
})
</script>

<template>
  <div class="paypal-pay-page">
    <div class="macos-window-panel">
      <!-- 顶部主工具栏 -->
      <div class="macos-toolbar">
        <div class="toolbar-left">
          <div class="page-title-badge">
            <span class="dot-live"></span>
            <span class="title">PayPal 协议代付工作台</span>
          </div>

          <el-radio-group v-model="activeTab" size="small">
            <el-radio-button label="manual_table">📝 手动表格录入代付 (实时接码)</el-radio-button>
            <el-radio-button label="auto">📂 从已提链列表批量代付</el-radio-button>
          </el-radio-group>
        </div>

        <div class="toolbar-right">
          <div class="param-inline">
            <span class="param-label">国家/买家资料:</span>
            <el-select
              v-model="form.country"
              filterable
              size="small"
              style="width: 175px"
              @change="handleCountryChange"
            >
              <el-option
                v-for="c in COUNTRY_OPTIONS"
                :key="c.value"
                :label="c.label"
                :value="c.value"
              />
            </el-select>

            <span class="param-label">默认号码:</span>
            <el-input
              v-model="form.default_phone"
              size="small"
              placeholder="+66..."
              style="width: 135px"
              clearable
            />

            <span class="param-label">并发数:</span>
            <el-input-number v-model="form.workers" :min="1" :max="10" size="small" style="width: 75px" />
          </div>

          <el-button
            type="primary"
            size="small"
            class="start-pay-btn"
            :loading="running"
            @click="handleStartBatch"
          >
            <el-icon><VideoPlay /></el-icon>批量启动全部代付
          </el-button>
          <el-button
            v-if="running"
            type="danger"
            size="small"
            @click="handleStopAll"
          >
            <el-icon><SwitchButton /></el-icon>停止所有任务
          </el-button>
        </div>
      </div>

      <!-- KPI 统计卡片 -->
      <div class="kpi-banner">
        <div class="kpi-card">
          <span class="kpi-label">总任务数</span>
          <span class="kpi-val">{{ stats.tot }}</span>
        </div>
        <div class="kpi-card text-primary">
          <span class="kpi-label">并发执行中</span>
          <span class="kpi-val">{{ stats.active }}</span>
        </div>
        <div class="kpi-card text-warning" :class="{ 'pulse-badge': pendingOtpList.length > 0 }">
          <span class="kpi-label">⚡ 待输入验证码</span>
          <span class="kpi-val">{{ pendingOtpList.length }}</span>
        </div>
        <div class="kpi-card text-success">
          <span class="kpi-label">🎉 成功开通 Plus</span>
          <span class="kpi-val">{{ stats.success }}</span>
        </div>
        <div class="kpi-card text-danger">
          <span class="kpi-label">失败 / 异常</span>
          <span class="kpi-val">{{ stats.failed }}</span>
        </div>
        <div class="kpi-progress-wrap">
          <el-progress
            :percentage="stats.percent"
            :status="stats.percent === 100 ? 'success' : ''"
            :stroke-width="8"
            striped
            :striped-flow="running"
          />
        </div>
      </div>

      <!-- 待接码并发快捷处理坞 (Pending OTP Dock) -->
      <div v-if="pendingOtpList.length > 0" class="pending-otp-dock">
        <div class="dock-header">
          <span class="dock-title">
            <el-icon class="pulse-icon"><Iphone /></el-icon>
            【待处理 2FA 验证码】当前有 {{ pendingOtpList.length }} 个账号正在等待接码，支持各行独立并发输入：
          </span>
        </div>
        <div class="dock-list">
          <div v-for="item in pendingOtpList" :key="item.key" class="dock-item">
            <div class="dock-item-left">
              <span class="dock-email">{{ item.row.email || item.row.ba_token?.slice(0, 16) || item.key }}</span>
              <span class="dock-phone">号码: {{ item.phone || '默认' }}</span>
            </div>
            <div class="dock-item-right">
              <el-input
                v-model="item.row.otpInput"
                size="small"
                placeholder="输入6位验证码/换新号"
                class="mono dock-input"
                @keyup.enter="handleInlineOtpSubmit(item.row)"
              />
              <el-button
                size="small"
                type="warning"
                :loading="item.row.submittingOtp"
                @click="handleInlineOtpSubmit(item.row)"
              >
                确认提交
              </el-button>
            </div>
          </div>
        </div>
      </div>

      <!-- 主体表格区域 -->
      <div class="content-body">
        <!-- ──────────────── 模式 1：动态表格录入模式 ──────────────── -->
        <div v-show="activeTab === 'manual_table'" class="manual-table-view">
          <div class="table-subtoolbar">
            <div class="subtoolbar-left">
              <el-button type="success" size="small" @click="handleAddManualRow">
                <el-icon><Plus /></el-icon>新增一条数据
              </el-button>
              <el-button size="small" @click="openPasteDialog">
                <el-icon><DocumentAdd /></el-icon>批量粘贴导入
              </el-button>
              <el-button size="small" type="danger" plain @click="handleClearManualRows">
                <el-icon><Delete /></el-icon>清空列表
              </el-button>
              <span class="subtoolbar-tip">
                💡 流程：新增一行 ➔ 填写 0元链接与手机号 ➔ 点击【支付】 ➔ 发送短信后直接在状态列输入验证码 ➔ 确认完成授权！
              </span>
            </div>
            <div class="subtoolbar-right">
              <span class="subtoolbar-count">已录入 {{ manualRows.length }} 行</span>
            </div>
          </div>

          <el-table
            :data="manualRows"
            height="100%"
            size="small"
            stripe
            class="macos-table"
            @selection-change="(v) => (manualSelected = v)"
          >
            <el-table-column type="selection" width="40" align="center" />
            <el-table-column label="#" width="45" align="center">
              <template #default="{ $index }">
                <span class="row-index">{{ $index + 1 }}</span>
              </template>
            </el-table-column>

            <el-table-column label="0元 BA Token / 协议链接 (必填)" min-width="260">
              <template #default="{ row }">
                <el-input
                  v-model="row.ba_token"
                  size="small"
                  placeholder="BA-XXX 或 https://www.paypal.com/agreements/approve?ba_token=BA-XXX"
                  class="mono table-input"
                  clearable
                />
              </template>
            </el-table-column>

            <el-table-column label="绑卡手机号码 (必填)" width="180">
              <template #default="{ row }">
                <el-input
                  v-model="row.phone"
                  size="small"
                  placeholder="+66812345678"
                  class="mono table-input"
                  clearable
                />
              </template>
            </el-table-column>

            <el-table-column label="关联邮箱 (可选)" width="190">
              <template #default="{ row }">
                <el-input
                  v-model="row.email"
                  size="small"
                  placeholder="user@outlook.com"
                  class="mono table-input"
                  clearable
                />
              </template>
            </el-table-column>

            <el-table-column label="实时状态 / 2FA 验证码输入" min-width="260">
              <template #default="{ row }">
                <!-- 1. 待支付初始状态 -->
                <el-tag v-if="row.status === 'idle'" size="small" type="info" effect="plain">
                  待支付
                </el-tag>

                <!-- 2. 等待 2FA 短信验证码状态 (行内交互卡片) -->
                <div v-else-if="row.status === 'awaiting_otp'" class="inline-otp-box">
                  <el-tag size="small" type="warning" effect="dark" class="pulse-tag">
                    ⚡ 待输入验证码
                  </el-tag>
                  <el-input
                    v-model="row.otpInput"
                    size="small"
                    placeholder="输入 6 位验证码"
                    class="mono inline-otp-input"
                    autofocus
                    @keyup.enter="handleInlineOtpSubmit(row)"
                  />
                  <el-button
                    size="small"
                    type="warning"
                    :loading="row.submittingOtp"
                    @click="handleInlineOtpSubmit(row)"
                  >
                    确认
                  </el-button>
                </div>

                <!-- 3. 执行中 / 成功 / 失败 -->
                <div v-else class="status-badge-wrap">
                  <el-tag
                    size="small"
                    :type="row.status === 'success' ? 'success' : row.status === 'error' ? 'danger' : row.status === 'running' ? 'primary' : 'info'"
                  >
                    {{ row.step_text || row.status }}
                  </el-tag>
                  <span v-if="row.elapsed" class="elapsed-tag">{{ row.elapsed }}s</span>
                </div>
              </template>
            </el-table-column>

            <el-table-column label="操作" width="160" align="center" fixed="right">
              <template #default="{ row, $index }">
                <div class="row-actions">
                  <!-- 单行启动支付 -->
                  <el-button
                    v-if="row.status === 'idle' || row.status === 'error'"
                    size="small"
                    type="primary"
                    link
                    @click="handleStartSingleRow(row)"
                  >
                    <el-icon><Promotion /></el-icon>支付
                  </el-button>
                  <el-button
                    v-else-if="row.status === 'running'"
                    size="small"
                    type="primary"
                    link
                    loading
                  >
                    进行中
                  </el-button>
                  <el-button
                    v-else-if="row.status === 'success'"
                    size="small"
                    type="success"
                    link
                    disabled
                  >
                    已完成
                  </el-button>

                  <!-- 查看实时日志 -->
                  <el-button
                    size="small"
                    type="info"
                    link
                    :disabled="row.status === 'idle'"
                    @click="handleViewLog(row)"
                  >
                    日志
                  </el-button>

                  <!-- 移除该行 -->
                  <el-button
                    size="small"
                    type="danger"
                    link
                    @click="handleRemoveManualRow($index)"
                  >
                    删除
                  </el-button>
                </div>
              </template>
            </el-table-column>
          </el-table>
        </div>

        <!-- ──────────────── 模式 2：从已提链列表勾选 ──────────────── -->
        <div v-show="activeTab === 'auto'" class="auto-table-view">
          <div class="table-subtoolbar">
            <div class="subtoolbar-left">
              <el-button size="small" @click="loadAutoData">
                <el-icon><Refresh /></el-icon>刷新提链列表
              </el-button>
              <el-button size="small" type="primary" plain @click="batchFillAutoPhones">
                <el-icon><Iphone /></el-icon>一键为勾选账号填充当前默认手机号
              </el-button>
              <span class="subtoolbar-tip">从系统已提取出 0 元链接的账号中勾选并批量执行协议代付</span>
            </div>
            <div class="subtoolbar-right">
              <span class="subtoolbar-count">已勾选 {{ autoSelected.length }} / 共 {{ total }} 个</span>
            </div>
          </div>

          <el-table
            v-loading="loading"
            :data="autoRows"
            height="100%"
            size="small"
            stripe
            class="macos-table"
            @selection-change="(v) => (autoSelected = v)"
          >
            <el-table-column type="selection" width="40" align="center" />
            <el-table-column label="账号邮箱" min-width="190" show-overflow-tooltip>
              <template #default="{ row }">
                <span class="mono email-text" @click="copyText(row.email)">{{ row.email }}</span>
              </template>
            </el-table-column>

            <el-table-column label="绑卡手机号 (可单行编辑)" width="180">
              <template #default="{ row }">
                <el-input
                  v-model="row._phone"
                  size="small"
                  placeholder="+66..."
                  class="mono phone-input"
                />
              </template>
            </el-table-column>

            <el-table-column label="BA Token / 0元链接" min-width="230" show-overflow-tooltip>
              <template #default="{ row }">
                <span class="mono ba-text">{{ row.extract_link?.ba_token || row.extract_link?.link_url }}</span>
              </template>
            </el-table-column>

            <el-table-column label="执行状态 / 2FA 验证码输入" min-width="260">
              <template #default="{ row }">
                <div v-if="taskMap[row.email]" class="inline-otp-cell">
                  <!-- 行内 2FA 接码交互输入框 -->
                  <div v-if="taskMap[row.email].status === 'awaiting_otp'" class="inline-otp-box">
                    <el-tag size="small" type="warning" effect="dark" class="pulse-tag">
                      ⚡ 待输入验证码
                    </el-tag>
                    <el-input
                      v-model="row._otpInput"
                      size="small"
                      placeholder="输入 6 位验证码"
                      class="mono inline-otp-input"
                      autofocus
                      @keyup.enter="handleInlineOtpSubmit(row)"
                    />
                    <el-button
                      size="small"
                      type="warning"
                      :loading="row._submittingOtp"
                      @click="handleInlineOtpSubmit(row)"
                    >
                      确认
                    </el-button>
                  </div>

                  <!-- 正常状态 -->
                  <div v-else class="status-badge-wrap">
                    <el-tag
                      size="small"
                      :type="taskMap[row.email].status === 'success' ? 'success' : taskMap[row.email].status === 'error' ? 'danger' : taskMap[row.email].status === 'running' ? 'primary' : 'info'"
                    >
                      {{ taskMap[row.email].step_text || taskMap[row.email].status }}
                    </el-tag>
                    <span v-if="taskMap[row.email].elapsed" class="elapsed-tag">{{ taskMap[row.email].elapsed }}s</span>
                  </div>
                </div>
                <el-tag v-else size="small" type="info" effect="plain">待支付</el-tag>
              </template>
            </el-table-column>

            <el-table-column label="操作" width="90" align="center">
              <template #default="{ row }">
                <el-button
                  size="small"
                  link
                  type="primary"
                  :disabled="!taskMap[row.email]"
                  @click="handleViewLog(taskMap[row.email] || { email: row.email })"
                >
                  实时日志
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </div>

      <!-- 底部全量实时协议流控制台 (Live Stream Console) -->
      <div class="live-console-wrap">
        <div class="console-header">
          <div class="console-header-left">
            <span class="console-dot green"></span>
            <span class="console-title">PayPal 协议全量实时流控制台 (Live Stream)</span>
            <span class="console-line-count">共 {{ liveLogs.length }} 条实时事件</span>
          </div>
          <div class="console-header-right">
            <el-input
              v-model="liveLogFilter"
              size="small"
              placeholder="按关键词/邮箱过滤..."
              style="width: 160px"
              clearable
            />
            <el-checkbox v-model="liveLogAutoScroll" size="small" label="自动滚屏" />
            <el-button size="small" text @click="liveLogs = []">
              <el-icon><Delete /></el-icon>清空
            </el-button>
          </div>
        </div>
        <div ref="liveLogTerminalRef" class="console-body">
          <div
            v-for="(line, idx) in liveLogs.filter((l) => !liveLogFilter || l.includes(liveLogFilter))"
            :key="idx"
            class="terminal-line"
            :class="getLogLineClass(line)"
          >
            {{ line }}
          </div>
          <div v-if="!liveLogs.length" class="terminal-empty">
            {{ running ? '协议代付执行中，正在等待实时日志推流...' : '暂无实时日志，点击【支付】后在此查看实时执行流' }}
          </div>
        </div>
      </div>
    </div>

    <!-- ──────────────── 批量粘贴导入弹窗 ──────────────── -->
    <el-dialog
      v-model="pasteDialogVisible"
      title="📋 批量粘贴导入 BA Token"
      width="580px"
      top="15vh"
      :close-on-click-modal="false"
    >
      <div class="paste-dialog-body">
        <p class="paste-tip">
          支持直接粘贴多行数据，系统将自动解析为表格行：<br />
          • 纯 BA Token 或链接：<code>BA-XXX</code> 或 <code>https://www.paypal.com/agreements/approve?ba_token=BA-XXX</code><br />
          • 带手机号：<code>BA-XXX----+66812345678</code><br />
          • 带邮箱与手机号：<code>user@outlook.com----BA-XXX----+66812345678</code>
        </p>
        <el-input
          v-model="pasteText"
          type="textarea"
          :rows="10"
          class="mono paste-textarea"
          placeholder="每行一条，例如：&#10;BA-9AR71182YT9943128----+66802242723&#10;https://www.paypal.com/agreements/approve?ba_token=BA-1YS85615WS776414E"
        />
      </div>
      <template #footer>
        <el-button size="small" @click="pasteDialogVisible = false">取消</el-button>
        <el-button size="small" type="primary" @click="handleConfirmPaste">导入到表格</el-button>
      </template>
    </el-dialog>

    <!-- ──────────────── 单任务专属实时日志弹窗 (支持 SSE 实时动态追加) ──────────────── -->
    <el-dialog
      v-model="logVisible"
      width="820px"
      top="7vh"
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
            <span class="modal-email">{{ logKey }}</span>
            <el-tag size="small" type="success" effect="plain" class="modal-run-tag">
              PayPal 协议日志 (实时追踪)
            </el-tag>
          </div>
        </div>
      </template>

      <div class="modal-terminal-wrap">
        <div ref="logTerminalBodyRef" class="modal-terminal-body">
          <div
            v-for="(line, idx) in logLines"
            :key="idx"
            class="terminal-line"
            :class="getLogLineClass(line)"
          >
            {{ line }}
          </div>
          <div v-if="!logLines.length" class="terminal-empty">
            {{ logLoading ? '正在加载日志...' : '暂无详细日志' }}
          </div>
        </div>
      </div>

      <template #footer>
        <div class="modal-footer">
          <span class="log-count-tip">共 {{ logLines.length }} 行日志 · 实时连接中</span>
          <div class="modal-footer-btns">
            <el-button size="small" @click="copyText(logLines.join('\n'))">
              <el-icon><CopyDocument /></el-icon>复制日志
            </el-button>
            <el-button size="small" type="primary" @click="logVisible = false">
              关闭
            </el-button>
          </div>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.paypal-pay-page {
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
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-light);
  border-radius: 10px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.04);
  overflow: hidden;
}

.macos-toolbar {
  padding: 8px 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  background: var(--el-fill-color-blank);
  border-bottom: 1px solid var(--el-border-color-lighter);
  flex-shrink: 0;
}

.toolbar-left, .toolbar-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.param-inline {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11.5px;
}

.param-label {
  color: var(--el-text-color-secondary);
}

.page-title-badge {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-right: 4px;
}
.page-title-badge .dot-live {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #3b82f6;
}
.page-title-badge .title {
  font-size: 13px;
  font-weight: 700;
  color: var(--el-text-color-primary);
}

.start-pay-btn {
  background: linear-gradient(135deg, #0070ba, #003087);
  border: none;
  font-weight: 600;
}

.kpi-banner {
  display: grid;
  grid-template-columns: repeat(5, 1fr) 2fr;
  gap: 8px;
  padding: 8px 12px;
  background: var(--el-fill-color-light);
  border-bottom: 1px solid var(--el-border-color-lighter);
  flex-shrink: 0;
}

.kpi-card {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  padding: 4px 8px;
  display: flex;
  flex-direction: column;
}
.kpi-card .kpi-label {
  font-size: 10px;
  color: var(--el-text-color-secondary);
}
.kpi-card .kpi-val {
  font-size: 14px;
  font-weight: 700;
  font-family: ui-monospace, monospace;
}

.pulse-badge {
  border-color: #f59e0b;
  background: rgba(245, 158, 11, 0.08);
}

.kpi-progress-wrap {
  display: flex;
  align-items: center;
  padding: 0 8px;
}

/* 待接码悬浮坞 */
.pending-otp-dock {
  background: rgba(245, 158, 11, 0.08);
  border-bottom: 1px solid #f59e0b;
  padding: 6px 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex-shrink: 0;
}
.dock-header {
  display: flex;
  align-items: center;
  font-size: 11.5px;
  font-weight: 600;
  color: #b45309;
}
.pulse-icon {
  animation: pulse-anim 1.5s infinite;
  margin-right: 4px;
  font-size: 14px;
}
@keyframes pulse-anim {
  0% { transform: scale(1); opacity: 1; }
  50% { transform: scale(1.2); opacity: 0.7; }
  100% { transform: scale(1); opacity: 1; }
}

.dock-list {
  display: flex;
  gap: 8px;
  overflow-x: auto;
  padding-bottom: 2px;
}
.dock-item {
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--el-bg-color);
  border: 1px solid #f59e0b;
  border-radius: 6px;
  padding: 4px 8px;
  flex-shrink: 0;
}
.dock-item-left {
  display: flex;
  flex-direction: column;
}
.dock-email {
  font-size: 11px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}
.dock-phone {
  font-size: 10px;
  color: var(--el-text-color-secondary);
}
.dock-item-right {
  display: flex;
  align-items: center;
  gap: 4px;
}
.dock-input {
  width: 150px;
}

.content-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.manual-table-view, .auto-table-view {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.table-subtoolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 12px;
  background: var(--el-fill-color-blank);
  border-bottom: 1px solid var(--el-border-color-lighter);
  flex-shrink: 0;
}
.subtoolbar-left, .subtoolbar-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
.subtoolbar-tip {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}
.subtoolbar-count {
  font-size: 11px;
  color: var(--el-text-color-regular);
}

.row-index {
  font-size: 11px;
  font-family: ui-monospace, monospace;
  color: var(--el-text-color-secondary);
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}
.email-text {
  cursor: pointer;
}
.email-text:hover {
  color: var(--el-color-primary);
  text-decoration: underline;
}
.ba-text {
  font-size: 11px;
  color: #3b82f6;
}
.phone-input {
  font-size: 12px;
}
.table-input {
  font-size: 12px;
}

/* 行内 2FA 接码交互输入框 */
.inline-otp-cell {
  display: flex;
  align-items: center;
}
.inline-otp-box {
  display: flex;
  align-items: center;
  gap: 6px;
  background: rgba(245, 158, 11, 0.1);
  padding: 3px 6px;
  border-radius: 4px;
  border: 1px dashed #f59e0b;
}
.inline-otp-input {
  width: 135px;
}
.pulse-tag {
  animation: pulse-anim 1.5s infinite;
}

.status-badge-wrap {
  display: flex;
  align-items: center;
  gap: 6px;
}
.elapsed-tag {
  font-size: 10px;
  color: var(--el-text-color-secondary);
  font-family: ui-monospace, monospace;
}

.row-actions {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}

/* 底部实时日志终端 */
.live-console-wrap {
  height: 150px;
  min-height: 110px;
  max-height: 220px;
  background: #111116;
  border-top: 1px solid #2d2d38;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
}
.console-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 12px;
  background: #181820;
  border-bottom: 1px solid #262633;
}
.console-header-left, .console-header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
.console-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.console-dot.green { background: #10b981; }
.console-title {
  font-size: 11px;
  font-weight: 600;
  color: #e2e8f0;
}
.console-line-count {
  font-size: 10px;
  color: #64748b;
}

.console-body {
  flex: 1;
  padding: 8px 12px;
  overflow-y: auto;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
  line-height: 1.5;
  color: #cbd5e1;
  background: #111116;
}

/* 终端日志高亮类 */
.terminal-line {
  white-space: pre-wrap;
  word-break: break-all;
  padding: 1px 0;
}
.terminal-line.log-success { color: #34d399; font-weight: 600; }
.terminal-line.log-error { color: #f87171; font-weight: 600; }
.terminal-line.log-otp { color: #fbbf24; }
.terminal-line.log-auth { color: #a78bfa; }
.terminal-line.log-phase { color: #60a5fa; }
.terminal-line.log-normal { color: #94a3b8; }
.terminal-empty {
  color: #475569;
  font-style: italic;
  font-size: 11px;
  text-align: center;
  padding-top: 30px;
}

/* 粘贴导入弹窗 */
.paste-dialog-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.paste-tip {
  font-size: 11.5px;
  color: var(--el-text-color-secondary);
  line-height: 1.6;
}
.paste-tip code {
  color: #3b82f6;
  background: var(--el-fill-color-light);
  padding: 1px 4px;
  border-radius: 4px;
}

/* 弹窗终端样式 */
:deep(.macos-terminal-dialog) {
  border-radius: 12px;
  overflow: hidden;
  background: #141418;
}
:deep(.macos-terminal-dialog .el-dialog__header) {
  padding: 10px 16px;
  background: #1c1c22;
  border-bottom: 1px solid #2d2d38;
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
.window-dots .dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
}
.window-dots .dot.red { background: #ff5f56; }
.window-dots .dot.yellow { background: #ffbd2e; }
.window-dots .dot.green { background: #27c93f; }
.modal-email {
  font-size: 13px;
  font-weight: 600;
  color: #f3f4f6;
  font-family: ui-monospace, monospace;
}
.modal-terminal-body {
  height: 420px;
  padding: 12px 16px;
  overflow-y: auto;
  font-family: ui-monospace, monospace;
  font-size: 12px;
  line-height: 1.6;
  color: #d1d5db;
  background: #141418;
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
</style>
