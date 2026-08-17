<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
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

const activeTab = ref('auto')

// ── 模式 1：从已提链列表选择 ──
const loading = ref(false)
const rows = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(30)
const selected = ref([])

// ── 模式 2：手动输入 BA 列表 ──
const manualInputText = ref('')

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

// ── 执行与任务监控 ──
const running = ref(false)
const taskId = ref('')
const es = ref(null)
const taskMap = reactive({})

// 全局实时日志流
const liveLogs = ref([])
const liveLogFilter = ref('')
const liveLogAutoScroll = ref(true)
const liveLogTerminalRef = ref(null)
const showLiveConsole = ref(true)

// 待接码快速队列
const pendingOtpList = computed(() => {
  return Object.values(taskMap).filter((item) => item.status === 'awaiting_otp')
})

const taskItems = computed(() => Object.values(taskMap))
const stats = computed(() => {
  const list = taskItems.value
  const tot = list.length
  const success = list.filter((i) => i.status === 'success').length
  const failed = list.filter((i) => i.status === 'error' || i.status === 'cancelled').length
  const active = list.filter((i) => i.status === 'running' || i.status === 'awaiting_otp').length
  const pending = list.filter((i) => i.status === 'pending').length
  const percent = tot > 0 ? Math.round(((success + failed) / tot) * 100) : 0
  return { tot, success, failed, active, pending, percent }
})

// ── 单账号日志弹窗 (支持实时动态追加) ──
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

// ── 加载数据 ──
async function loadData() {
  loading.value = true
  try {
    const res = await listRegistered({
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
      filter: 'extract_success',
    })
    const list = (res.items || []).filter((r) => r.extract_link && r.extract_link.link_url)
    // 为每一行附带独立的手机号编辑字段和行内输入状态
    rows.value = list.map((r) => ({
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

// ── 批量手机号工具 ──
function batchFillSelectedPhones() {
  if (!selected.value.length) {
    ElMessage.warning('请先勾选要填充手机号的账号')
    return
  }
  const phoneVal = form.default_phone.trim()
  if (!phoneVal) {
    ElMessage.warning('请在顶部设置默认手机号')
    return
  }
  selected.value.forEach((r) => {
    r._phone = phoneVal
  })
  ElMessage.success(`已为选中的 ${selected.value.length} 个账号填充手机号: ${phoneVal}`)
}

// ── 启动任务 ──
function handleStart() {
  let payItems = []

  if (activeTab.value === 'auto') {
    const targetRows = selected.value.length ? selected.value : rows.value
    if (!targetRows.length) {
      ElMessage.warning('当前没有可执行协议支付的已提链账号')
      return
    }
    payItems = targetRows.map((r) => ({
      email: r.email,
      ba_token: r.extract_link?.ba_token || r.extract_link?.link_url || '',
      phone: (r._phone || form.default_phone || '').trim(),
    }))
  } else {
    const lines = manualInputText.value.split('\n').map((l) => l.trim()).filter(Boolean)
    if (!lines.length) {
      ElMessage.warning('请输入要代付的 BA Token 列表（每行一个）')
      return
    }
    payItems = lines.map((l) => {
      const parts = l.split('----')
      if (parts.length >= 3) {
        return { email: parts[0].trim(), ba_token: parts[1].trim(), phone: parts[2].trim() }
      }
      if (parts.length === 2) {
        if (parts[1].startsWith('+') || /^\d{8,}$/.test(parts[1])) {
          return { email: '', ba_token: parts[0].trim(), phone: parts[1].trim() }
        }
        return { email: parts[0].trim(), ba_token: parts[1].trim(), phone: form.default_phone.trim() }
      }
      return { email: '', ba_token: l, phone: form.default_phone.trim() }
    })
  }

  // 重置 taskMap
  for (const k in taskMap) delete taskMap[k]
  for (const it of payItems) {
    const key = it.email || it.ba_token
    taskMap[key] = {
      key,
      email: it.email,
      ba_token: it.ba_token,
      phone: it.phone,
      status: 'pending',
      step_text: '排队中...',
      prompt: '',
      started_at: 0,
      elapsed: 0,
      _otpInput: '',
      _submittingOtp: false,
    }
  }

  liveLogs.value = []

  startPayPalPayTask({
    items: payItems,
    country: form.country,
    flow_mode: form.flow_mode,
    workers: form.workers,
  })
    .then((res) => {
      taskId.value = res.task_id
      running.value = true
      ElMessage.success(`PayPal 协议批量支付已启动: 共 ${payItems.length} 个任务, 并发数=${form.workers}`)
      connectStream(res.task_id)
    })
    .catch((e) => {
      ElMessage.error(e.message || '启动支付任务失败')
    })
}

// ── 停止任务 ──
function handleStop() {
  if (!taskId.value) return
  stopPayPalPayTask(taskId.value)
    .then(() => {
      ElMessage.info('已发送停止指令')
    })
    .catch((e) => {
      ElMessage.error(e.message || '停止失败')
    })
}

// ── SSE 实时推流连接 ──
function connectStream(id) {
  if (es.value) {
    es.value.close()
    es.value = null
  }

  es.value = createSSE(paypalPayStreamUrl(id), {
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
              _otpInput: '',
              _submittingOtp: false,
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
        if (k && taskMap[k]) {
          if (data.status !== undefined) taskMap[k].status = data.status
          if (data.step_text !== undefined) taskMap[k].step_text = data.step_text
          if (data.prompt !== undefined) taskMap[k].prompt = data.prompt
          if (data.result !== undefined) taskMap[k].result = data.result
          if (data.started_at !== undefined) taskMap[k].started_at = data.started_at
          if (data.elapsed !== undefined) taskMap[k].elapsed = data.elapsed
        }
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

          // 如果单账号日志弹窗打开，且匹配当前账号，实时追加
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
      running.value = false
      if (es.value) {
        es.value.close()
        es.value = null
      }
      ElMessage.success('PayPal 协议代付任务已全部执行完毕！')
      loadData()
    },
  })
}

// ── 行内提交 2FA 验证码 / 换手机号 (多账号并发独立输入) ──
async function submitInlineOtp(targetItem) {
  const key = targetItem.key || targetItem.email || targetItem.ba_token
  const val = (targetItem._otpInput || '').trim()
  if (!val) {
    ElMessage.warning('请输入 6 位短信验证码或新手机号')
    return
  }

  targetItem._submittingOtp = true
  try {
    await submitPayPalPayInput(taskId.value || 'latest', key, val)
    ElMessage.success(`[${(targetItem.email || key).slice(0, 16)}] 验证码已提交，正在继续授权...`)
    targetItem._otpInput = ''
    targetItem.status = 'running'
    targetItem.step_text = '已提交验证码，正在继续授权...'
    if (taskMap[key]) {
      taskMap[key].status = 'running'
      taskMap[key].step_text = '已提交验证码，正在继续授权...'
      taskMap[key]._otpInput = ''
    }
  } catch (e) {
    ElMessage.error(e.message || '提交验证码失败')
  } finally {
    targetItem._submittingOtp = false
  }
}

// ── 查看单账号详细日志 ──
async function handleViewLog(item) {
  logKey.value = item.key || item.email || item.ba_token
  logVisible.value = true
  logLoading.value = true
  logLines.value = []
  try {
    const res = await getPayPalPayTaskLog(taskId.value || 'latest', logKey.value)
    logLines.value = res.lines || []
    scrollLogModalToBottom()
  } catch (e) {
    logLines.value = [`加载日志失败: ${e.message}`]
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
  loadData()
})

onUnmounted(() => {
  if (es.value) {
    es.value.close()
    es.value = null
  }
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
            <span class="badge-total">{{ activeTab === 'auto' ? rows.length : '手动模式' }}</span>
          </div>

          <el-radio-group v-model="activeTab" size="small">
            <el-radio-button label="auto">已提链账号并发代付</el-radio-button>
            <el-radio-button label="manual">手动批量输入 BA 代付</el-radio-button>
          </el-radio-group>
        </div>

        <div class="toolbar-right">
          <div class="param-inline">
            <span class="param-label">买家资料/国家:</span>
            <el-select
              v-model="form.country"
              filterable
              size="small"
              style="width: 170px"
              @change="handleCountryChange"
            >
              <el-option
                v-for="c in COUNTRY_OPTIONS"
                :key="c.value"
                :label="c.label"
                :value="c.value"
              />
            </el-select>

            <span class="param-label">默认手机号:</span>
            <el-input
              v-model="form.default_phone"
              size="small"
              placeholder="+66..."
              style="width: 135px"
              clearable
            />

            <span class="param-label">模式:</span>
            <el-select v-model="form.flow_mode" size="small" style="width: 140px">
              <el-option label="🛡️ 身份提升 (推荐)" value="elevation" />
              <el-option label="⚡ 标准协议版" value="standard" />
            </el-select>

            <span class="param-label">Worker 并发数:</span>
            <el-input-number v-model="form.workers" :min="1" :max="10" size="small" style="width: 75px" />
          </div>

          <el-button
            v-if="!running"
            type="primary"
            size="small"
            class="start-pay-btn"
            @click="handleStart"
          >
            <el-icon><VideoPlay /></el-icon>启动多并发代付
          </el-button>
          <el-button
            v-else
            type="danger"
            size="small"
            @click="handleStop"
          >
            <el-icon><SwitchButton /></el-icon>停止任务
          </el-button>
        </div>
      </div>

      <!-- KPI 统计卡片与待接码聚合条 -->
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
              <span class="dock-email">{{ item.email || item.key }}</span>
              <span class="dock-phone">({{ item.phone || '默认号码' }})</span>
            </div>
            <div class="dock-item-right">
              <el-input
                v-model="item._otpInput"
                size="small"
                placeholder="输入 6 位验证码或新手机号"
                class="mono dock-input"
                @keyup.enter="submitInlineOtp(item)"
              />
              <el-button
                size="small"
                type="warning"
                :loading="item._submittingOtp"
                @click="submitInlineOtp(item)"
              >
                确认提交
              </el-button>
            </div>
          </div>
        </div>
      </div>

      <!-- 主体区域 -->
      <div class="content-body">
        <!-- 模式 1：从已提链列表勾选并发执行 -->
        <div v-show="activeTab === 'auto'" class="auto-tab-view">
          <div class="table-subtoolbar">
            <div class="subtoolbar-left">
              <el-button size="small" @click="loadData">
                <el-icon><Refresh /></el-icon>刷新提链列表
              </el-button>
              <el-button size="small" type="primary" plain @click="batchFillSelectedPhones">
                <el-icon><Iphone /></el-icon>一键为勾选账号填充当前默认手机号
              </el-button>
              <span class="subtoolbar-tip">提示：支持多账号并发，每个账号可在表格内填写专属手机号，并在接码时行内独立填码</span>
            </div>
            <div class="subtoolbar-right">
              <span class="subtoolbar-count">已勾选 {{ selected.length }} / 共 {{ total }} 个</span>
            </div>
          </div>

          <el-table
            v-loading="loading"
            :data="rows"
            height="100%"
            size="small"
            stripe
            class="macos-table"
            @selection-change="(v) => (selected = v)"
          >
            <el-table-column type="selection" width="40" align="center" />
            <el-table-column label="账号邮箱" min-width="190" show-overflow-tooltip>
              <template #default="{ row }">
                <span class="mono email-text" @click="copyText(row.email)">{{ row.email }}</span>
              </template>
            </el-table-column>

            <el-table-column label="绑卡手机号 (支持每行独立自定义)" width="180">
              <template #default="{ row }">
                <el-input
                  v-model="row._phone"
                  size="small"
                  placeholder="+66..."
                  class="mono phone-input"
                />
              </template>
            </el-table-column>

            <el-table-column label="BA Token / 0元链接" min-width="220" show-overflow-tooltip>
              <template #default="{ row }">
                <span class="mono ba-text">{{ row.extract_link?.ba_token || row.extract_link?.link_url }}</span>
              </template>
            </el-table-column>

            <el-table-column label="执行状态 / 2FA 行内接码交互" min-width="270">
              <template #default="{ row }">
                <div v-if="taskMap[row.email]" class="inline-otp-cell">
                  <!-- 正常运行或完成状态 -->
                  <div v-if="taskMap[row.email].status !== 'awaiting_otp'" class="status-badge-wrap">
                    <el-tag
                      size="small"
                      :type="taskMap[row.email].status === 'success' ? 'success' : taskMap[row.email].status === 'error' ? 'danger' : taskMap[row.email].status === 'running' ? 'primary' : 'info'"
                    >
                      {{ taskMap[row.email].step_text || taskMap[row.email].status }}
                    </el-tag>
                    <span v-if="taskMap[row.email].elapsed" class="elapsed-tag">{{ taskMap[row.email].elapsed }}s</span>
                  </div>

                  <!-- 行内 2FA 接码交互输入框 (多账号并发独立输入) -->
                  <div v-else class="inline-otp-action-box">
                    <el-tag size="small" type="warning" effect="dark" class="pulse-tag">
                      ⚡ 待输入短信验证码
                    </el-tag>
                    <el-input
                      v-model="row._otpInput"
                      size="small"
                      placeholder="输入6位验证码/换号"
                      class="mono inline-otp-input"
                      @keyup.enter="submitInlineOtp(row)"
                    />
                    <el-button
                      size="small"
                      type="warning"
                      :loading="row._submittingOtp"
                      @click="submitInlineOtp(row)"
                    >
                      提交
                    </el-button>
                  </div>
                </div>
                <el-tag v-else size="small" type="info" effect="plain">就绪中</el-tag>
              </template>
            </el-table-column>

            <el-table-column label="操作" width="80" align="center">
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

        <!-- 模式 2：手动批量输入 -->
        <div v-show="activeTab === 'manual'" class="manual-tab-view">
          <div class="manual-left">
            <span class="input-title">输入 BA Token / 链接列表（支持多列格式，每行一条）：</span>
            <el-input
              v-model="manualInputText"
              type="textarea"
              :rows="18"
              class="mono manual-textarea"
              placeholder="支持以下格式（每行一个）：&#10;1. 邮箱----BA链接/Token----手机号 (如 user@outlook.com----BA-XXX----+66812345678)&#10;2. BA链接/Token----手机号 (如 BA-XXX----+66812345678)&#10;3. 邮箱----BA链接/Token (使用顶部默认手机号)&#10;4. 纯 BA Token 或链接 (如 https://www.paypal.com/agreements/approve?ba_token=BA-XXX)"
            />
          </div>
          <div class="manual-right">
            <span class="input-title">多 Worker 并发执行监控与行内接码：</span>
            <el-table :data="taskItems" height="100%" size="small" stripe class="macos-table">
              <el-table-column label="BA / 邮箱" min-width="150" show-overflow-tooltip>
                <template #default="{ row }">
                  <span class="mono">{{ row.email || row.ba_token }}</span>
                </template>
              </el-table-column>
              <el-table-column label="手机号" width="120">
                <template #default="{ row }">
                  <span class="mono text-muted">{{ row.phone || form.default_phone }}</span>
                </template>
              </el-table-column>
              <el-table-column label="状态 / 行内 2FA 验证" min-width="220">
                <template #default="{ row }">
                  <div v-if="row.status !== 'awaiting_otp'" class="status-cell">
                    <el-tag
                      size="small"
                      :type="row.status === 'success' ? 'success' : row.status === 'error' ? 'danger' : row.status === 'running' ? 'primary' : 'info'"
                    >
                      {{ row.step_text || row.status }}
                    </el-tag>
                  </div>
                  <div v-else class="inline-otp-action-box">
                    <el-input
                      v-model="row._otpInput"
                      size="small"
                      placeholder="6位验证码/换手机号"
                      class="mono inline-otp-input"
                      @keyup.enter="submitInlineOtp(row)"
                    />
                    <el-button
                      size="small"
                      type="warning"
                      :loading="row._submittingOtp"
                      @click="submitInlineOtp(row)"
                    >
                      提交
                    </el-button>
                  </div>
                </template>
              </el-table-column>
              <el-table-column label="日志" width="70" align="center">
                <template #default="{ row }">
                  <el-button size="small" link type="primary" @click="handleViewLog(row)">查看</el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </div>
      </div>

      <!-- 底部全量实时协议流控制台 (Live Stream Console) -->
      <div v-if="showLiveConsole" class="live-console-wrap">
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
            {{ running ? '协议代付执行中，正在等待实时日志推流...' : '暂无实时日志，启动任务后在此查看实时流' }}
          </div>
        </div>
      </div>
    </div>

    <!-- 单任务专属实时日志弹窗 (支持 SSE 实时动态追加) -->
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
              PayPal 专属协议日志 (实时追踪)
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
.page-title-badge .badge-total {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  background: var(--el-fill-color-light);
  padding: 1px 6px;
  border-radius: 10px;
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

/* 待接码快速悬浮坞 */
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

.auto-tab-view {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.table-subtoolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 12px;
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

.manual-tab-view {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  padding: 10px;
}

.manual-left, .manual-right {
  display: flex;
  flex-direction: column;
  min-height: 0;
  gap: 6px;
}

.input-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-regular);
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

/* 行内 2FA 接码交互小卡片 */
.inline-otp-cell {
  display: flex;
  align-items: center;
  gap: 6px;
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

.inline-otp-action-box {
  display: flex;
  align-items: center;
  gap: 6px;
  background: rgba(245, 158, 11, 0.1);
  padding: 2px 6px;
  border-radius: 4px;
  border: 1px dashed #f59e0b;
}
.inline-otp-input {
  width: 140px;
}
.pulse-tag {
  animation: pulse-anim 1.5s infinite;
}

/* 底部实时日志终端 */
.live-console-wrap {
  height: 160px;
  min-height: 120px;
  max-height: 240px;
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
