<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  Key,
  VideoPlay,
  SwitchButton,
  Connection,
  Download,
  CopyDocument,
  Document,
  ArrowDown,
  Loading,
  CircleCheck,
  Warning,
} from '@element-plus/icons-vue'
import {
  previewFix401,
  checkFix401Proxy,
  startFix401,
  stopFix401,
  getFix401Snapshot,
  fix401StreamUrl,
  getFix401Log,
  downloadFix401Cpa,
  downloadFix401Sub2,
} from '@/api/fix401'
import { copyText, createSSE } from '@/api/request'
import ElapsedTimer from '@/components/ElapsedTimer.vue'

const FORM_KEY = 'gpt_fix401_form_v1'
const TASK_KEY = 'gpt_fix401_task'

const form = reactive({
  proxy: '',
  emails: '',
  workers: 2,
  timeout: 45,
  forceFullLogin: false,
})

try {
  const saved = JSON.parse(localStorage.getItem(FORM_KEY) || '{}')
  if (saved && typeof saved === 'object') Object.assign(form, saved)
  if (Number(form.workers) > 5) form.workers = 2
} catch (_) {}

const previewing = ref(false)
const starting = ref(false)
const proxyChecking = ref(false)
const proxyMsg = ref('')
const proxyOk = ref(false)
const proxyCountry = ref('')
const preview = ref(null)
const running = ref(false)
const taskId = ref('')
const items = ref({})
const stats = ref({
  success: 0, rt_fast: 0, oauth: 0, failed: 0, banned: 0, need_phone: 0, missing: 0, rt_expired: 0,
})
const filter = ref('all')
const search = ref('')
const logOpen = ref(false)
const logEmail = ref('')
const logLines = ref([])
const logLoading = ref(false)
let es = null

function persistForm() {
  try {
    localStorage.setItem(FORM_KEY, JSON.stringify({
      proxy: form.proxy,
      workers: form.workers,
      timeout: form.timeout,
      forceFullLogin: form.forceFullLogin,
    }))
  } catch (_) {}
}

function isEmail(s) {
  const v = String(s || '').trim()
  return v.includes('@') && !/\s/.test(v) ? v.toLowerCase() : ''
}

function emailFromJwt(tok) {
  try {
    const parts = String(tok || '').split('.')
    if (parts.length < 2) return ''
    let b64 = parts[1].replace(/-/g, '+').replace(/_/g, '/')
    b64 += '='.repeat((4 - b64.length % 4) % 4)
    const json = JSON.parse(atob(b64))
    return isEmail(json.email) || isEmail((json['https://api.openai.com/profile'] || {}).email)
  } catch (_) {
    return ''
  }
}

function pickEmail(obj) {
  if (!obj || typeof obj !== 'object') return ''
  const cred = obj.credentials || {}
  const extra = obj.extra || {}
  return isEmail(cred.email) || isEmail(extra.email) || isEmail(obj.email) || isEmail(obj.name)
    || emailFromJwt(cred.access_token || cred.actoken || obj.access_token || obj.actoken)
    || emailFromJwt(cred.id_token || obj.id_token)
}

function collectEmails(data, out, seen) {
  if (data == null) return
  if (typeof data === 'string') {
    const t = data.trim()
    if ((t.startsWith('{') || t.startsWith('[')) && t.length > 2) {
      try { collectEmails(JSON.parse(t), out, seen); return } catch (_) {}
    }
    const e = isEmail(t) || emailFromJwt(t)
    if (e && !seen.has(e)) { seen.add(e); out.push(e) }
    return
  }
  if (Array.isArray(data)) { data.forEach((item) => collectEmails(item, out, seen)); return }
  if (typeof data !== 'object') return
  if (Array.isArray(data.accounts)) { collectEmails(data.accounts, out, seen); return }
  const e = pickEmail(data)
  if (e && !seen.has(e)) { seen.add(e); out.push(e); return }
  Object.values(data).forEach((v) => {
    if (v && typeof v === 'object') collectEmails(v, out, seen)
  })
}

function emailsFromText(text) {
  const seen = new Set()
  const out = []
  const raw = String(text || '').trim()
  if (!raw) return out
  try { collectEmails(JSON.parse(raw), out, seen) } catch (_) {}
  raw.split(/\r?\n/).forEach((line) => {
    const t = line.trim()
    if (!t) return
    try { collectEmails(JSON.parse(t), out, seen) }
    catch (_) {
      const e = isEmail(t) || emailFromJwt(t)
      if (e && !seen.has(e)) { seen.add(e); out.push(e) }
    }
  })
  const re = /"email"\s*:\s*"([^"]+@[^"]+)"/gi
  let m
  while ((m = re.exec(raw))) {
    const e = isEmail(m[1])
    if (e && !seen.has(e)) { seen.add(e); out.push(e) }
  }
  return out
}

function parsedEmails() {
  const found = emailsFromText(form.emails)
  if (found.length) return found
  const seen = new Set()
  const out = []
  String(form.emails || '').split(/\r?\n/).forEach((line) => {
    const e = isEmail(line)
    if (e && !seen.has(e)) { seen.add(e); out.push(e) }
  })
  return out
}

const emailCount = computed(() => parsedEmails().length)
const itemList = computed(() => Object.values(items.value))
const doneCount = computed(() => itemList.value.filter((i) => i.status === 'done').length)
const runningCount = computed(() => itemList.value.filter((i) => i.status === 'running').length)
const queuedCount = computed(() => itemList.value.filter((i) => i.status === 'pending').length)
const successCount = computed(() => Number(stats.value.success || 0))
const failEmails = computed(() => itemList.value
  .filter((i) => i.status === 'done' && !['success', 'banned', 'missing'].includes(i.result?.status))
  .map((i) => i.email))

const filteredItems = computed(() => {
  const q = search.value.trim().toLowerCase()
  return itemList.value.filter((it) => {
    if (q && !String(it.email || '').includes(q)) return false
    const st = it.status === 'done' ? (it.result?.status || 'failed') : it.status
    if (filter.value === 'all') return true
    if (filter.value === 'running') return it.status === 'running' || it.status === 'pending'
    return st === filter.value
  })
})

function resultMeta(it) {
  const st = it.status === 'done' ? (it.result?.status || 'failed') : it.status
  const map = {
    pending: { text: '排队', cls: 'is-wait' },
    running: { text: it.step_text || '进行中', cls: 'is-run' },
    success: { text: it.result?.label || '成功', cls: 'is-ok' },
    banned: { text: it.result?.label || '封号', cls: 'is-ban' },
    failed: { text: it.result?.label || '失败', cls: 'is-fail' },
    rt_expired: { text: it.result?.label || 'RT 已失效', cls: 'is-warn' },
    need_phone: { text: '需绑手机', cls: 'is-warn' },
    missing: { text: '号池没有', cls: 'is-wait' },
    cancelled: { text: '已取消', cls: 'is-wait' },
  }
  return map[st] || { text: it.step_text || st, cls: 'is-wait' }
}

function onPasteEmails(ev) {
  const text = (ev.clipboardData && ev.clipboardData.getData('text')) || ''
  if (!/[{[]/.test(text) && !/access_token|refresh_token|"email"\s*:/.test(text)) return
  const found = emailsFromText(text)
  if (!found.length) return
  ev.preventDefault()
  const merged = [...new Set([...parsedEmails(), ...found])]
  form.emails = merged.join('\n')
  ElMessage.success(`已从粘贴内容提取 ${found.length} 个邮箱`)
}

async function onImportFile(ev) {
  const file = ev.target?.files?.[0]
  ev.target.value = ''
  if (!file) return
  try {
    const found = emailsFromText(await file.text())
    if (!found.length) {
      ElMessage.warning('文件里没有找到邮箱')
      return
    }
    const merged = [...new Set([...parsedEmails(), ...found])]
    form.emails = merged.join('\n')
    ElMessage.success(`已导入 ${found.length} 个邮箱，共 ${merged.length} 个`)
  } catch (e) {
    ElMessage.error(e.message || '解析失败')
  }
}

async function checkProxy() {
  proxyMsg.value = ''
  proxyOk.value = false
  const proxy = form.proxy.trim()
  if (!proxy) {
    ElMessage.warning('请先填写代理')
    return
  }
  proxyChecking.value = true
  try {
    const res = await checkFix401Proxy(proxy)
    proxyOk.value = true
    proxyCountry.value = String(res.loc || '').trim().toUpperCase()
    proxyMsg.value = res.loc
      ? `代理可用，出口 ${res.ip}（${res.loc}）`
      : `代理可用，出口 ${res.ip}`
    persistForm()
  } catch (e) {
    proxyMsg.value = e.message || '代理不可用'
  } finally {
    proxyChecking.value = false
  }
}

async function doPreview() {
  const emails = parsedEmails()
  if (!emails.length) {
    ElMessage.warning('请填写邮箱，一行一个，也可粘贴 Sub2 / CPA JSON')
    return
  }
  previewing.value = true
  try {
    const res = await previewFix401({ emails })
    preview.value = res
    if (res.queued === 0) {
      ElMessage.warning(
        res.banned?.length
          ? `号池命中 ${res.found}，但可跑 0（封号 ${res.banned.length}，不在号池 ${res.missing.length}）`
          : '号池里没有这些邮箱',
      )
    } else {
      ElMessage.success(`号池命中 ${res.found}，可解 ${res.queued}，封号跳过 ${res.banned.length}`)
    }
  } catch (e) {
    ElMessage.error(e.message || '识别失败')
  } finally {
    previewing.value = false
  }
}

function applySnapshot(snap) {
  if (!snap) return
  stats.value = { ...stats.value, ...(snap.stats || {}) }
  const next = {}
  for (const it of snap.items || []) {
    next[it.email] = {
      email: it.email,
      status: it.status,
      step_text: it.step_text,
      result: it.result || {},
      elapsed: it.elapsed || 0,
      started_at: it.started_at || 0,
    }
  }
  items.value = next
}

function closeStream() {
  if (es) {
    try { es.close() } catch (_) {}
    es = null
  }
}

function watchTask(id) {
  closeStream()
  taskId.value = id
  running.value = true
  try { sessionStorage.setItem(TASK_KEY, id) } catch (_) {}
  es = createSSE(fix401StreamUrl(id), {
    init: (ev) => {
      try {
        const d = JSON.parse(ev.data)
        if (d.stats) stats.value = { ...stats.value, ...d.stats }
      } catch (_) {}
    },
    progress: (ev) => {
      try {
        const d = JSON.parse(ev.data)
        if (d.stats) stats.value = { ...stats.value, ...d.stats }
        if (!d.email) return
        const cur = items.value[d.email] || { email: d.email }
        items.value = {
          ...items.value,
          [d.email]: {
            ...cur,
            status: d.status || cur.status,
            step_text: d.step_text || cur.step_text,
            result: d.result || cur.result || {},
            elapsed: d.elapsed ?? cur.elapsed,
            started_at: d.started_at || cur.started_at,
          },
        }
      } catch (_) {}
    },
    end: () => {
      running.value = false
      closeStream()
      ElMessage.success('解 401 任务已结束')
      getFix401Snapshot(id).then(applySnapshot).catch(() => {})
    },
  }, () => {
    if (!running.value) closeStream()
  })
}

async function startTask() {
  const emails = parsedEmails()
  if (!emails.length) {
    ElMessage.warning('请填写邮箱')
    return
  }
  persistForm()
  if (!form.proxy.trim()) {
    ElMessage.warning('未填代理：将直连。对方 CPA/Sub 主机 401/502 时请填那台机器的出口代理')
  }
  starting.value = true
  running.value = true
  preview.value = preview.value || null
  try {
    const res = await startFix401({
      emails,
      proxy: form.proxy,
      proxy_country: proxyCountry.value,
      workers: form.workers,
      timeout: form.timeout,
      force_full_login: form.forceFullLogin,
    })
    const id = res.taskId || res.task_id
    if (!id) throw new Error('未拿到任务 ID')
    const snap = await getFix401Snapshot(id)
    applySnapshot(snap)
    if (!res.queued) {
      running.value = false
      ElMessage.warning('没有可跑的号（不在号池或已封号）')
      return
    }
    watchTask(id)
  } catch (e) {
    running.value = false
    ElMessage.error(e.message || '启动失败')
  } finally {
    starting.value = false
  }
}

async function stopTask() {
  if (!taskId.value) return
  try {
    await stopFix401(taskId.value)
    ElMessage.success('已请求停止，进行中的号会跑完当前步')
  } catch (e) {
    ElMessage.error(e.message || '停止失败')
  }
}

function saveBlob(res, filename) {
  const blob = res instanceof Blob ? res : new Blob([res?.data || res])
  const url = window.URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  window.URL.revokeObjectURL(url)
}

async function downloadCpa(layout) {
  if (!taskId.value || !successCount.value) {
    ElMessage.warning('还没有成功的号')
    return
  }
  try {
    const res = await downloadFix401Cpa(taskId.value, layout)
    const name = layout === 'zip'
      ? `cpa-fix401-${taskId.value}.zip`
      : (layout === 'bundle' ? `cpa-fix401-${taskId.value}.json` : `cpa-fix401-${taskId.value}.txt`)
    saveBlob(res, name)
    ElMessage.success(layout === 'txt' ? '已下载整包 TXT JSON 数组' : 'CPA 已下载')
  } catch (e) {
    ElMessage.error(e.message || '下载失败')
  }
}

async function downloadSub2(layout) {
  if (!taskId.value || !successCount.value) {
    ElMessage.warning('还没有成功的号')
    return
  }
  try {
    const res = await downloadFix401Sub2(taskId.value, layout)
    saveBlob(res, layout === 'zip' ? `sub2-fix401-${taskId.value}.zip` : `sub2-fix401-${taskId.value}.json`)
    ElMessage.success('Sub2 已下载')
  } catch (e) {
    ElMessage.error(e.message || '下载失败')
  }
}

async function copyAllTxt() {
  if (!taskId.value || !successCount.value) {
    ElMessage.warning('还没有成功的号')
    return
  }
  try {
    const res = await downloadFix401Cpa(taskId.value, 'txt')
    const blob = res instanceof Blob ? res : new Blob([res])
    const text = await blob.text()
    await copyText(text, `已复制 ${successCount.value} 条 CPA JSON 数组`)
  } catch (e) {
    ElMessage.error(e.message || '复制失败')
  }
}

function copyFails() {
  if (!failEmails.value.length) {
    ElMessage.warning('没有可重试的失败邮箱')
    return
  }
  copyText(failEmails.value.join('\n'), `已复制 ${failEmails.value.length} 个失败邮箱`)
}

async function openLog(email) {
  logEmail.value = email
  logOpen.value = true
  logLines.value = []
  if (!taskId.value) return
  logLoading.value = true
  try {
    const res = await getFix401Log(taskId.value, email)
    logLines.value = res.lines || []
  } catch (e) {
    logLines.value = [e.message || '读取日志失败']
  } finally {
    logLoading.value = false
  }
}

onMounted(async () => {
  const id = sessionStorage.getItem(TASK_KEY) || ''
  if (!id) return
  try {
    const snap = await getFix401Snapshot(id)
    if (!snap) return
    taskId.value = id
    applySnapshot(snap)
    if (snap.running) watchTask(id)
  } catch (_) {
    try { sessionStorage.removeItem(TASK_KEY) } catch (__) {}
  }
})

onUnmounted(() => {
  closeStream()
})
</script>

<template>
  <div class="fix401-page">
    <div class="macos-window-panel">
      <div class="macos-toolbar">
        <div class="toolbar-left">
          <div class="page-title-badge">
            <el-icon class="title-icon"><Key /></el-icon>
            <span class="title">解 401</span>
            <span class="badge-total">重提 RT 给 CPA / Sub 用</span>
          </div>
        </div>
        <div class="toolbar-right">
          <span v-if="taskId" class="task-id">任务 {{ taskId }}</span>
        </div>
      </div>

      <div class="page-body">
        <div class="form-card">
          <p class="hint">
            一行一个邮箱。把对方 CPA / Sub 主机的 HTTP 代理填进来；任务启动时会实测出口 IP，写进每条日志。
            默认只刷 RT，失效不会自动重登。重新登录要单独勾选，机房 IP 走密码+2FA 很容易被官方注销。
          </p>

          <label class="field-label">对方主机代理</label>
          <div class="proxy-row">
            <el-input
              v-model="form.proxy"
              placeholder="http://用户:密码@主机:端口  或  http://主机:端口"
              clearable
              spellcheck="false"
              @change="persistForm"
              @input="proxyOk = false; proxyCountry = ''; proxyMsg = ''"
            />
            <el-button :loading="proxyChecking" @click="checkProxy">
              <el-icon><Connection /></el-icon>验证代理
            </el-button>
          </div>
          <p class="proxy-msg" :class="{ ok: proxyOk, bad: proxyMsg && !proxyOk }">{{ proxyMsg }}</p>

          <div class="opt-row">
            <span class="opt-item">
              并发
              <el-input-number v-model="form.workers" :min="1" :max="5" size="small" @change="persistForm" />
            </span>
            <span class="opt-item">
              超时秒
              <el-input-number v-model="form.timeout" :min="15" :max="120" :step="5" size="small" @change="persistForm" />
            </span>
            <el-checkbox v-model="form.forceFullLogin" @change="persistForm">
              允许重新登录（RT 失效才走密码+2FA，机房 IP 容易封号）
            </el-checkbox>
          </div>

          <label class="field-label">邮箱列表 · {{ emailCount }} 个</label>
          <el-input
            v-model="form.emails"
            type="textarea"
            :autosize="{ minRows: 7, maxRows: 14 }"
            placeholder="name@outlook.com&#10;也可直接粘贴 Sub2 / CPA JSON，会自动抠邮箱"
            spellcheck="false"
            @paste="onPasteEmails"
          />

          <div v-if="preview" class="preview-chips">
            <span>识别 {{ preview.parsed }}</span>
            <span>号池 {{ preview.found }}</span>
            <span class="ok">可跑 {{ preview.queued }}</span>
            <span class="warn">凭证失效 {{ (preview.token_invalid || []).length }}</span>
            <span class="bad">封号跳过 {{ (preview.banned || []).length }}</span>
            <span>不在号池 {{ (preview.missing || []).length }}</span>
          </div>

          <div class="action-row">
            <el-button :loading="previewing" :disabled="running" @click="doPreview">识别号池</el-button>
            <el-button @click="$refs.fileInput.click()">导入 JSON</el-button>
            <input ref="fileInput" type="file" accept=".json,.txt,application/json" hidden @change="onImportFile" />
            <el-button type="primary" :loading="starting" :disabled="running" @click="startTask">
              <el-icon><VideoPlay /></el-icon>开始解 401
            </el-button>
            <el-button type="danger" plain :disabled="!running" @click="stopTask">
              <el-icon><SwitchButton /></el-icon>停止
            </el-button>
          </div>
        </div>

        <div class="stat-row">
          <div class="stat-card">
            <div class="k">已处理</div>
            <div class="v">{{ doneCount }} / {{ itemList.length || emailCount }}</div>
          </div>
          <div class="stat-card ok">
            <div class="k">成功</div>
            <div class="v">{{ stats.success || 0 }}</div>
            <div class="s">RT 刷新 {{ stats.rt_fast || 0 }} · 重登 {{ stats.oauth || 0 }}</div>
          </div>
          <div class="stat-card bad">
            <div class="k">失败</div>
            <div class="v">{{ stats.failed || 0 }}</div>
          </div>
          <div class="stat-card ban">
            <div class="k">封号</div>
            <div class="v">{{ stats.banned || 0 }}</div>
          </div>
          <div class="stat-card warn">
            <div class="k">RT 失效</div>
            <div class="v">{{ stats.rt_expired || 0 }}</div>
          </div>
          <div class="stat-card">
            <div class="k">进行中 / 排队</div>
            <div class="v">{{ runningCount }} / {{ queuedCount }}</div>
          </div>
        </div>

        <div class="table-toolbar">
          <el-radio-group v-model="filter" size="small">
            <el-radio-button label="all">全部 {{ itemList.length }}</el-radio-button>
            <el-radio-button label="running">进行中 {{ runningCount + queuedCount }}</el-radio-button>
            <el-radio-button label="success">成功 {{ stats.success || 0 }}</el-radio-button>
            <el-radio-button label="failed">失败 {{ stats.failed || 0 }}</el-radio-button>
            <el-radio-button label="rt_expired">RT 失效 {{ stats.rt_expired || 0 }}</el-radio-button>
            <el-radio-button label="banned">封号 {{ stats.banned || 0 }}</el-radio-button>
          </el-radio-group>
          <el-input v-model="search" size="small" clearable placeholder="过滤邮箱" class="search-box" />
        </div>

        <div class="table-wrap">
          <el-table :data="filteredItems" height="100%" size="small" stripe row-key="email">
            <el-table-column label="账号" min-width="220" show-overflow-tooltip>
              <template #default="{ row }">
                <span class="mono" @click="copyText(row.email, '已复制邮箱')">{{ row.email }}</span>
              </template>
            </el-table-column>
            <el-table-column label="状态" min-width="200">
              <template #default="{ row }">
                <span class="st" :class="resultMeta(row).cls">
                  <el-icon v-if="row.status === 'running'" class="spin"><Loading /></el-icon>
                  <el-icon v-else-if="row.result?.status === 'success'"><CircleCheck /></el-icon>
                  <el-icon v-else-if="row.result?.status === 'banned'"><Warning /></el-icon>
                  {{ resultMeta(row).text }}
                </span>
                <span v-if="row.result?.error" class="err-snip">{{ row.result.error }}</span>
              </template>
            </el-table-column>
            <el-table-column label="耗时" width="90" align="center">
              <template #default="{ row }">
                <ElapsedTimer :status="row.status" :started-at="row.started_at" :elapsed="row.elapsed" />
              </template>
            </el-table-column>
            <el-table-column label="操作" width="88" align="center">
              <template #default="{ row }">
                <el-button link type="primary" @click="openLog(row.email)">日志</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </div>

      <div class="macos-pagination-bar">
        <span class="total-text">
          成功 {{ stats.success || 0 }} · RT 失效 {{ stats.rt_expired || 0 }} · 封号 {{ stats.banned || 0 }}
        </span>
        <div class="dl-row">
          <el-dropdown :disabled="!successCount" @command="downloadCpa">
            <el-button size="small" type="primary" :disabled="!successCount">
              <el-icon><Download /></el-icon>下载 CPA
              <el-icon class="el-icon--right"><ArrowDown /></el-icon>
            </el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="txt">整包 TXT JSON 数组</el-dropdown-item>
                <el-dropdown-item command="bundle">整包 JSON</el-dropdown-item>
                <el-dropdown-item command="zip">zip 一号一文件</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <el-dropdown :disabled="!successCount" @command="downloadSub2">
            <el-button size="small" :disabled="!successCount">
              <el-icon><Document /></el-icon>下载 Sub2
              <el-icon class="el-icon--right"><ArrowDown /></el-icon>
            </el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="bundle">整包 JSON</el-dropdown-item>
                <el-dropdown-item command="zip">zip 一号一文件</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <el-button size="small" :disabled="!successCount" @click="copyAllTxt">
            <el-icon><CopyDocument /></el-icon>复制全部
          </el-button>
          <el-button size="small" :disabled="!failEmails.length" @click="copyFails">复制失败邮箱</el-button>
        </div>
      </div>
    </div>

    <el-dialog v-model="logOpen" :title="logEmail" width="720px" append-to-body>
      <div v-loading="logLoading" class="log-box">
        <div v-for="(line, i) in logLines" :key="i" class="log-line">{{ line }}</div>
        <div v-if="!logLoading && !logLines.length" class="log-empty">暂无日志</div>
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
.fix401-page {
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
  overflow: hidden;
}
.macos-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  flex-shrink: 0;
}
.toolbar-left, .toolbar-right { display: flex; align-items: center; gap: 10px; }
.page-title-badge {
  display: flex;
  align-items: center;
  gap: 8px;
}
.title-icon { color: var(--el-color-primary); }
.title { font-weight: 700; font-size: 15px; }
.badge-total {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.task-id { font-size: 12px; color: var(--el-text-color-secondary); font-family: ui-monospace, Consolas, monospace; }

.page-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: 12px 14px 0;
}
.form-card {
  flex-shrink: 0;
  background: var(--el-fill-color-blank);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
  padding: 12px 14px 10px;
}
.hint {
  margin: 0 0 10px;
  font-size: 12px;
  line-height: 1.55;
  color: var(--el-text-color-secondary);
}
.field-label {
  display: block;
  font-size: 12px;
  font-weight: 600;
  margin: 8px 0 6px;
}
.proxy-row { display: flex; gap: 8px; }
.proxy-row :deep(.el-input) { flex: 1; }
.proxy-msg { min-height: 1.2em; margin: 6px 0 0; font-size: 12px; }
.proxy-msg.ok { color: var(--el-color-success); }
.proxy-msg.bad { color: var(--el-color-danger); }
.opt-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 16px;
  margin: 10px 0 4px;
  font-size: 13px;
}
.opt-item { display: inline-flex; align-items: center; gap: 8px; }
.preview-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.preview-chips .ok { color: var(--el-color-success); }
.preview-chips .warn { color: var(--el-color-warning); }
.preview-chips .bad { color: var(--el-color-danger); }
.action-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}

.stat-row {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 8px;
  margin: 10px 0;
  flex-shrink: 0;
}
.stat-card {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 8px 10px;
  background: var(--el-fill-color-blank);
}
.stat-card .k { font-size: 11px; color: var(--el-text-color-secondary); }
.stat-card .v { font-size: 18px; font-weight: 700; margin-top: 2px; }
.stat-card .s { font-size: 11px; color: var(--el-text-color-secondary); margin-top: 2px; }
.stat-card.ok .v { color: var(--el-color-success); }
.stat-card.bad .v { color: var(--el-color-danger); }
.stat-card.ban .v { color: var(--el-color-danger); }
.stat-card.warn .v { color: var(--el-color-warning); }

.table-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
  flex-shrink: 0;
}
.search-box { width: 200px; }
.table-wrap {
  flex: 1;
  min-height: 160px;
  overflow: hidden;
}
.mono {
  font-family: ui-monospace, Consolas, monospace;
  cursor: pointer;
  font-size: 12px;
}
.st {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
}
.st.is-ok { color: var(--el-color-success); }
.st.is-fail, .st.is-ban { color: var(--el-color-danger); }
.st.is-warn { color: var(--el-color-warning); }
.st.is-run { color: var(--el-color-primary); }
.st.is-wait { color: var(--el-text-color-secondary); }
.err-snip {
  display: block;
  margin-top: 2px;
  font-size: 11px;
  color: var(--el-text-color-secondary);
  max-width: 520px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

.macos-pagination-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 14px;
  border-top: 1px solid var(--el-border-color-lighter);
  flex-shrink: 0;
}
.total-text { font-size: 12px; color: var(--el-text-color-secondary); }
.dl-row { display: flex; flex-wrap: wrap; gap: 8px; }

.log-box {
  min-height: 240px;
  max-height: 60vh;
  overflow: auto;
  font-family: ui-monospace, Consolas, monospace;
  font-size: 12px;
  line-height: 1.5;
  background: var(--el-fill-color-light);
  border-radius: 8px;
  padding: 10px 12px;
}
.log-line { white-space: pre-wrap; word-break: break-all; }
.log-empty { color: var(--el-text-color-secondary); }

@media (max-width: 1100px) {
  .stat-row { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
</style>
