<script setup>
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  FolderOpened,
  Delete,
  DataAnalysis,
  Download,
  Upload,
  Right,
  DocumentCopy,
  Check,
  Warning,
  CopyDocument,
} from '@element-plus/icons-vue'
import {
  analyzeCredentialDump,
  importCredentialDump,
  exportCredentialDump,
} from '@/api/register'
import { useStatsStore } from '@/stores/stats'
import { useRuntimeStore } from '@/stores/runtime'
import { copyText } from '@/api/request'

const router = useRouter()
const statsStore = useStatsStore()
const runtime = useRuntimeStore()

const text = ref('')
const strategy = ref('smart_merge')
const fileInput = ref(null)
const analyzing = ref(false)
const importing = ref(false)
const exporting = ref(false)
const analysis = ref(null)
const importSummary = ref(null)
const errors = ref([])
const elapsedSeconds = ref(0)
let timerId = null
let autoAnalyzeTimer = null

const exportFmt = ref('sub2api_json')
const delimiterMode = ref('----')
const customDelimiter = ref('----')
const exportVisible = ref(false)
const exportText = ref('')
const exportFilename = ref('')
const exportLabel = ref('')

const exportFormats = [
  {
    id: 'sub2api_json',
    title: 'Sub2API JSON',
    desc: '标准账号导入包：access_token + refresh_token',
    mode: 'download',
    filename: 'sub2api_accounts.json',
  },
  {
    id: 'email_pw_2fa',
    title: '账号----密码----2FA',
    desc: '一行一条，分隔符可改。没有 2FA 的号分隔符仍保留',
    mode: 'text',
    filename: '账号密码2FA.txt',
  },
  {
    id: 'email_pw_2fa_relay',
    title: '账号----密码----2FA----取件url',
    desc: '额外带 Remail 取件链接，等同收件权限',
    mode: 'text',
    filename: '账号密码2FA取件url.txt',
  },
]

const delimiterPresets = [
  { label: '----', value: '----' },
  { label: '---', value: '---' },
  { label: '|', value: '|' },
  { label: ',', value: ',' },
  { label: '自定义', value: 'custom' },
]

const isTextExport = computed(() => exportFmt.value !== 'sub2api_json')
const effectiveDelimiter = computed(() => {
  if (!isTextExport.value) return '----'
  if (delimiterMode.value === 'custom') return customDelimiter.value || '----'
  return delimiterMode.value || '----'
})

const recordHint = computed(() => {
  const n = analysis.value?.valid_count
  if (n) return `${n.toLocaleString()} 个有效账号`
  const lines = text.value.split('\n').filter((l) => l.trim() && !l.trim().startsWith('#')).length
  return lines ? `${lines.toLocaleString()} 行待解析` : ''
})

const samplePreview = computed(() => {
  const d = effectiveDelimiter.value === '\t' ? ' ⇥ ' : effectiveDelimiter.value
  if (exportFmt.value === 'email_pw_2fa_relay') {
    return `user@outlook.com${d}Password${d}JBSWY3DPEHPK3PXP${d}https://remail.../pickup?...`
  }
  if (exportFmt.value === 'email_pw_2fa') {
    return `user@outlook.com${d}Password${d}JBSWY3DPEHPK3PXP`
  }
  return '{ "exported_at": "...", "accounts": [ { "name": "user@outlook.com", ... } ] }'
})

function triggerFileInput() {
  fileInput.value?.click()
}

function handleFilesSelected(e) {
  const files = Array.from(e.target.files || [])
  if (!files.length) return
  Promise.all(
    files.map(
      (file) =>
        new Promise((resolve) => {
          const reader = new FileReader()
          reader.onload = (evt) => resolve(String(evt.target?.result || ''))
          reader.onerror = () => resolve('')
          reader.readAsText(file)
        }),
    ),
  ).then((chunks) => {
    const joined = chunks.filter(Boolean).join('\n')
    text.value = text.value.trim() ? `${text.value.trim()}\n${joined}` : joined
    importSummary.value = null
    ElMessage.success(`已读取 ${files.length} 个文件`)
    runAnalysis()
  })
  e.target.value = ''
}

function clearText() {
  text.value = ''
  analysis.value = null
  importSummary.value = null
  errors.value = []
}

async function runAnalysis() {
  const raw = text.value.trim()
  if (!raw) {
    analysis.value = null
    errors.value = []
    return
  }
  analyzing.value = true
  try {
    const res = await analyzeCredentialDump(raw)
    analysis.value = res
    errors.value = res.errors || []
  } catch (e) {
    ElMessage.error(e.message || '解析失败')
  } finally {
    analyzing.value = false
  }
}

watch(
  () => text.value,
  (val) => {
    importSummary.value = null
    if (autoAnalyzeTimer) clearTimeout(autoAnalyzeTimer)
    if (!val.trim()) {
      analysis.value = null
      errors.value = []
      return
    }
    autoAnalyzeTimer = setTimeout(() => runAnalysis(), 600)
  },
)

function startTimer() {
  elapsedSeconds.value = 0
  const start = performance.now()
  timerId = setInterval(() => {
    elapsedSeconds.value = +((performance.now() - start) / 1000).toFixed(1)
  }, 100)
  return start
}

function stopTimer(start) {
  clearInterval(timerId)
  elapsedSeconds.value = +((performance.now() - start) / 1000).toFixed(2)
}

async function doImport() {
  if (!text.value.trim()) {
    ElMessage.warning('请先粘贴发货内容，或选择发货文件')
    return
  }
  importing.value = true
  importSummary.value = null
  const start = startTimer()
  try {
    const r = await importCredentialDump(text.value.trim(), strategy.value)
    stopTimer(start)
    importSummary.value = r
    ElMessage.success(`已写入账号管理：新增 ${r.inserted} · 更新 ${r.updated}`)
    statsStore.refresh()
    runtime.bumpData()
    runAnalysis()
  } catch (e) {
    stopTimer(start)
    ElMessage.error(e.message || '入库失败')
  } finally {
    importing.value = false
  }
}

function b64ToBytes(b64) {
  const bin = atob(b64 || '')
  const bytes = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
  return bytes
}

function saveBlob(data, filename, mime) {
  const blob = data instanceof Blob ? data : new Blob([data], { type: mime || 'application/octet-stream' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

async function doExport() {
  if (!text.value.trim()) {
    ElMessage.warning('请先粘贴发货内容，或选择发货文件')
    return
  }
  exporting.value = true
  try {
    const r = await exportCredentialDump({
      text: text.value.trim(),
      format: exportFmt.value,
      delimiter: effectiveDelimiter.value,
    })
    if (r.mode === 'download') {
      saveBlob(b64ToBytes(r.b64), r.filename, r.mime)
      ElMessage.success(`已下载 ${r.filename}（${r.count} 个账号）`)
      return
    }
    exportText.value = r.text || ''
    exportFilename.value = r.filename || 'export.txt'
    exportLabel.value = r.label || '导出'
    exportVisible.value = true
    ElMessage.success(`已生成 ${r.count} 行`)
  } catch (e) {
    ElMessage.error(e.message || '导出失败')
  } finally {
    exporting.value = false
  }
}

function downloadExportText() {
  saveBlob(exportText.value, exportFilename.value, 'text/plain;charset=utf-8')
}

function gotoRegistered() {
  router.push('/registered')
}
</script>

<template>
  <div class="ship-page">
    <div class="macos-window-panel">
      <div class="macos-panel-header">
        <div class="header-left">
          <div class="window-dot-group">
            <span class="dot red"></span>
            <span class="dot yellow"></span>
            <span class="dot green"></span>
          </div>
          <div class="header-title-box">
            <span class="panel-title">发货导入 · 成品账号入库与格式转换</span>
            <span class="panel-sub-title">接码free NDJSON / JSON 数组 / 粘贴文本 · 直接出 Sub2 或账密 2FA</span>
          </div>
        </div>
        <div class="header-right">
          <span class="header-badge">无需先入库也可导出</span>
        </div>
      </div>

      <div class="ship-body">
        <div class="import-top-card">
          <div class="form-row-top">
            <div class="form-row-left">
              <div class="field-item">
                <span class="field-label">入库策略：</span>
                <el-select v-model="strategy" style="width: 280px">
                  <el-option label="智能合并（新号入库 / 老号补凭证）" value="smart_merge" />
                  <el-option label="仅导入全新号（库内已有则跳过）" value="skip_duplicates" />
                  <el-option label="强制覆盖（用发货内容替换库内凭证）" value="overwrite" />
                </el-select>
              </div>
            </div>
            <div class="form-row-right">
              <input
                ref="fileInput"
                type="file"
                accept=".txt,.json,.jsonl,.ndjson,.log,.csv"
                multiple
                style="display: none"
                @change="handleFilesSelected"
              />
              <el-button size="small" @click="triggerFileInput">
                <el-icon><FolderOpened /></el-icon> 选择发货文件（可多选）
              </el-button>
              <el-button
                v-if="text"
                size="small"
                type="primary"
                plain
                :loading="analyzing"
                @click="runAnalysis"
              >
                <el-icon><DataAnalysis /></el-icon> 重新解析
              </el-button>
              <el-button v-if="text" size="small" text type="danger" @click="clearText">
                <el-icon><Delete /></el-icon> 清空
              </el-button>
            </div>
          </div>

          <div class="hint-banner">
            <span class="hint-tag">兼容格式</span>
            <span class="hint-desc">
              一行一个 JSON（卖家发货原样）· JSON 数组 · Sub2API 包 ·
              <code>账号----密码----2FA</code> · 多文件自动拼接
            </span>
          </div>
        </div>

        <div class="editor-section">
          <div class="editor-header">
            <div class="editor-title">
              <span class="title-text">粘贴发货内容，或从文件读入</span>
              <span v-if="recordHint" class="count-chip">{{ recordHint }}</span>
            </div>
          </div>
          <el-input
            v-model="text"
            type="textarea"
            :rows="8"
            class="mono import-textarea"
            placeholder="把卖家发货文件整份贴进来，例如：&#10;{&quot;email&quot;:&quot;a@outlook.com&quot;,&quot;password&quot;:&quot;...&quot;,&quot;totp_secret&quot;:&quot;...&quot;,&quot;access_token&quot;:&quot;...&quot;}&#10;{&quot;email&quot;:&quot;b@outlook.com&quot;,...}&#10;&#10;也可以直接贴：&#10;a@outlook.com----密码----2FA密钥"
          />
        </div>

        <el-collapse-transition>
          <div v-if="analysis && analysis.valid_count > 0" class="analysis-hud-panel">
            <div class="hud-header">
              <div class="hud-title-wrap">
                <span class="hud-badge">SHIPMENT PARSE</span>
                <span class="hud-title">发货透视</span>
              </div>
              <span class="hud-count mono">{{ analysis.valid_count }} 个去重账号</span>
            </div>

            <div class="hud-kpi-grid">
              <div class="hud-kpi-card">
                <div class="kpi-label">有效账号</div>
                <div class="kpi-main-val">{{ analysis.valid_count }} <span class="kpi-sub">/ {{ analysis.total_objects }}</span></div>
                <div class="kpi-note">批次内重复 {{ analysis.internal_dup_count }}</div>
              </div>
              <div class="hud-kpi-card highlight">
                <div class="kpi-label">全新待入库</div>
                <div class="kpi-main-val text-brand">+{{ analysis.brand_new_count }}</div>
                <div class="kpi-note">库内已有 {{ analysis.registered_dup_count }}</div>
              </div>
              <div class="hud-kpi-card">
                <div class="kpi-label">密码 / 2FA</div>
                <div class="kpi-main-val">{{ analysis.with_password }} <span class="kpi-sub">/ {{ analysis.with_2fa }}</span></div>
                <div class="kpi-note">有密码 / 有 TOTP</div>
              </div>
              <div class="hud-kpi-card">
                <div class="kpi-label">AT / RT</div>
                <div class="kpi-main-val">{{ analysis.with_at }} <span class="kpi-sub">/ {{ analysis.with_rt }}</span></div>
                <div class="kpi-note">取件链接 {{ analysis.with_pickup }}</div>
              </div>
            </div>

            <div class="preview-table-box">
              <div class="preview-table-title">采样预览（前 {{ analysis.preview_rows?.length || 0 }} 条，Token 已打码）</div>
              <el-table :data="analysis.preview_rows" size="small" stripe height="220" class="macos-table">
                <el-table-column prop="line" label="行" width="56" align="center">
                  <template #default="{ row }"><span class="mono hint">#{{ row.line }}</span></template>
                </el-table-column>
                <el-table-column prop="email" label="邮箱" min-width="200" show-overflow-tooltip>
                  <template #default="{ row }">
                    <div class="email-preview-cell">
                      <span class="mono">{{ row.email }}</span>
                      <el-icon class="copy-ico" @click.stop="copyText(row.email)"><DocumentCopy /></el-icon>
                    </div>
                  </template>
                </el-table-column>
                <el-table-column label="密码" width="110" align="center">
                  <template #default="{ row }">
                    <span v-if="row.has_password" class="mono text-success">{{ row.password_masked }}</span>
                    <span v-else class="hint">无</span>
                  </template>
                </el-table-column>
                <el-table-column label="2FA" width="70" align="center">
                  <template #default="{ row }">
                    <el-tag v-if="row.has_2fa" size="small" type="success" effect="plain">有</el-tag>
                    <span v-else class="hint">—</span>
                  </template>
                </el-table-column>
                <el-table-column label="AT" width="90" align="center">
                  <template #default="{ row }">
                    <el-tag v-if="row.at_len" size="small" effect="plain" class="mono">{{ row.at_len }}位</el-tag>
                    <span v-else class="hint">—</span>
                  </template>
                </el-table-column>
                <el-table-column label="RT" width="90" align="center">
                  <template #default="{ row }">
                    <el-tag v-if="row.rt_len" size="small" type="primary" effect="plain" class="mono">{{ row.rt_len }}位</el-tag>
                    <span v-else class="hint">—</span>
                  </template>
                </el-table-column>
                <el-table-column label="取件" width="70" align="center">
                  <template #default="{ row }">
                    <el-tag v-if="row.has_pickup" size="small" effect="plain">有</el-tag>
                    <span v-else class="hint">—</span>
                  </template>
                </el-table-column>
                <el-table-column label="库内" width="110" align="center">
                  <template #default="{ row }">
                    <el-tag
                      size="small"
                      :type="row.db_status === 'brand_new' ? 'success' : (row.db_status === 'registered' ? 'warning' : 'info')"
                      effect="light"
                    >
                      {{ row.db_label }}
                    </el-tag>
                  </template>
                </el-table-column>
              </el-table>
            </div>
          </div>
        </el-collapse-transition>

        <el-collapse-transition>
          <div v-if="errors.length" class="error-feedback-card">
            <div class="error-card-header">
              <div class="err-title-wrap">
                <el-icon class="err-icon"><Warning /></el-icon>
                <span class="err-title">{{ errors.length }} 行无法识别</span>
              </div>
              <el-button size="small" text @click="errors = []">关闭</el-button>
            </div>
            <ul class="err-list mono">
              <li v-for="e in errors" :key="e.line">
                <span class="err-line-tag">Line {{ e.line }}</span>
                <span class="err-msg">{{ e.error }}</span>
              </li>
            </ul>
          </div>
        </el-collapse-transition>

        <div class="export-studio">
          <div class="studio-title">导出格式（参考账号管理导出）</div>
          <div class="fmt-cards">
            <button
              v-for="fmt in exportFormats"
              :key="fmt.id"
              type="button"
              class="fmt-card"
              :class="{ 'is-active': exportFmt === fmt.id }"
              @click="exportFmt = fmt.id"
            >
              <div class="fmt-card-title">{{ fmt.title }}</div>
              <div class="fmt-card-desc">{{ fmt.desc }}</div>
            </button>
          </div>

          <div v-if="isTextExport" class="delim-row">
            <span class="field-label">分隔符</span>
            <button
              v-for="p in delimiterPresets"
              :key="p.value"
              type="button"
              class="chunk-btn"
              :class="{ 'is-active': delimiterMode === p.value }"
              @click="delimiterMode = p.value"
            >
              {{ p.label }}
            </button>
            <el-input
              v-if="delimiterMode === 'custom'"
              v-model="customDelimiter"
              size="small"
              class="mono"
              style="width: 140px"
              placeholder="自定义分隔符"
            />
          </div>
          <div class="delim-preview">
            <span class="preview-tag">预览</span>
            <span class="preview-text mono">{{ samplePreview }}</span>
          </div>
        </div>

        <div class="import-footer-actions">
          <div class="footer-left">
            <el-button type="primary" :loading="importing" @click="doImport">
              <el-icon><Upload /></el-icon>
              {{ importing ? `正在入库 (${elapsedSeconds}s)` : '写入账号管理' }}
            </el-button>
            <el-button type="success" plain :loading="exporting" @click="doExport">
              <el-icon><Download /></el-icon>
              {{ exporting ? '正在导出…' : '按所选格式导出' }}
            </el-button>
            <span v-if="recordHint" class="count-pill">{{ recordHint }}</span>
          </div>
        </div>

        <el-collapse-transition>
          <div v-if="importSummary" class="import-success-card">
            <div class="success-header">
              <div class="success-title">
                <el-icon class="icon-success"><Check /></el-icon>
                <span>入库完成 · {{ importSummary.cost_seconds }}s</span>
              </div>
              <el-button size="small" type="primary" plain @click="gotoRegistered">
                前往账号管理 <el-icon><Right /></el-icon>
              </el-button>
            </div>
            <div class="kpi-grid">
              <div class="kpi-item">
                <span class="kpi-tag">解析</span>
                <span class="kpi-val">{{ importSummary.parsed }}</span>
              </div>
              <div class="kpi-item highlight-insert">
                <span class="kpi-tag">新增</span>
                <span class="kpi-val text-success">+{{ importSummary.inserted }}</span>
              </div>
              <div class="kpi-item">
                <span class="kpi-tag">更新</span>
                <span class="kpi-val">{{ importSummary.updated }}</span>
              </div>
              <div class="kpi-item">
                <span class="kpi-tag">跳过</span>
                <span class="kpi-val">{{ importSummary.skipped }}</span>
              </div>
            </div>
          </div>
        </el-collapse-transition>
      </div>
    </div>

    <el-dialog v-model="exportVisible" width="720px" top="8vh" class="macos-custom-dialog">
      <template #header>
        <div class="export-dialog-head">
          <span>导出 · {{ exportLabel }}</span>
          <span class="mono hint">{{ exportFilename }}</span>
        </div>
      </template>
      <el-input v-model="exportText" type="textarea" :rows="16" class="mono" />
      <template #footer>
        <el-button @click="copyText(exportText)">
          <el-icon><CopyDocument /></el-icon> 复制
        </el-button>
        <el-button type="primary" @click="downloadExportText">
          <el-icon><Download /></el-icon> 下载 {{ exportFilename }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.ship-page {
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
  box-shadow: var(--app-shadow-sm);
  overflow: hidden;
}
.macos-panel-header {
  padding: 12px 18px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--app-border);
  background: var(--el-fill-color-light);
  flex-shrink: 0;
}
.header-left { display: flex; align-items: center; gap: 12px; }
.window-dot-group { display: flex; gap: 6px; }
.dot { width: 10px; height: 10px; border-radius: 50%; }
.dot.red { background: #ff5f56; }
.dot.yellow { background: #ffbd2e; }
.dot.green { background: #27c93f; }
.header-title-box { display: flex; flex-direction: column; gap: 1px; }
.panel-title { font-size: 13.5px; font-weight: 700; color: var(--app-title); }
.panel-sub-title { font-size: 11px; color: var(--el-text-color-secondary); }
.header-badge {
  font-size: 11px;
  color: var(--app-text-secondary);
  background: var(--el-fill-color);
  padding: 2px 8px;
  border-radius: 10px;
  border: 1px solid var(--app-border);
}
.ship-body {
  flex: 1;
  min-height: 0;
  padding: 16px 20px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.import-top-card {
  background: var(--el-fill-color-light);
  border: 1px solid var(--app-border);
  border-radius: 10px;
  padding: 12px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.form-row-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
}
.form-row-left, .form-row-right { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.field-item { display: flex; align-items: center; gap: 8px; }
.field-label { font-size: 12.5px; font-weight: 600; color: var(--app-title); }
.hint-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--app-window-bg);
  border: 1px solid var(--app-border);
  border-radius: 6px;
  padding: 8px 12px;
  font-size: 11.5px;
  line-height: 1.5;
}
.hint-tag { font-weight: 700; color: var(--el-color-primary); white-space: nowrap; }
.hint-desc { color: var(--el-text-color-secondary); }
.editor-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
.editor-title { display: flex; align-items: center; gap: 8px; }
.title-text { font-size: 12.5px; font-weight: 600; color: var(--app-title); }
.count-chip {
  font-size: 11px;
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  padding: 1px 6px;
  border-radius: 4px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
.import-textarea :deep(.el-textarea__inner) {
  font-size: 12px;
  line-height: 1.6;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
.analysis-hud-panel {
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 12px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.hud-header { display: flex; align-items: center; justify-content: space-between; }
.hud-title-wrap { display: flex; align-items: center; gap: 8px; }
.hud-badge {
  font-size: 10px;
  letter-spacing: 0.08em;
  font-weight: 700;
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  padding: 2px 6px;
  border-radius: 4px;
}
.hud-title { font-size: 13px; font-weight: 700; color: var(--app-title); }
.hud-count { font-size: 12px; color: var(--app-text-secondary); }
.hud-kpi-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}
.hud-kpi-card {
  background: var(--app-window-bg);
  border: 1px solid var(--app-border);
  border-radius: 10px;
  padding: 10px 12px;
}
.hud-kpi-card.highlight { border-color: var(--c-tianshuibi-border); background: var(--c-tianshuibi-soft); }
.kpi-label { font-size: 11px; color: var(--app-text-secondary); }
.kpi-main-val { font-size: 22px; font-weight: 700; color: var(--app-title); margin-top: 4px; }
.kpi-sub { font-size: 13px; font-weight: 500; color: var(--app-text-secondary); }
.kpi-note { font-size: 11px; color: var(--app-text-secondary); margin-top: 2px; }
.text-brand { color: var(--brand); }
.text-success { color: var(--el-color-success); }
.preview-table-box { display: flex; flex-direction: column; gap: 6px; }
.preview-table-title { font-size: 12px; color: var(--app-text-secondary); }
.email-preview-cell { display: flex; align-items: center; gap: 6px; }
.copy-ico { cursor: pointer; color: var(--app-text-secondary); }
.copy-ico:hover { color: var(--brand); }
.hint { color: var(--el-text-color-placeholder); }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.export-studio {
  border: 1px solid var(--app-border);
  border-radius: 12px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  background: var(--el-fill-color-light);
}
.studio-title { font-size: 12.5px; font-weight: 700; color: var(--app-title); }
.fmt-cards { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
.fmt-card {
  text-align: left;
  border: 1px solid var(--app-border);
  background: var(--app-window-bg);
  border-radius: 10px;
  padding: 12px;
  cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.fmt-card:hover { border-color: var(--c-tianshuibi-border); }
.fmt-card.is-active {
  border-color: var(--brand);
  box-shadow: 0 0 0 1px var(--brand);
  background: var(--c-tianshuibi-soft);
}
.fmt-card-title { font-size: 13px; font-weight: 700; color: var(--app-title); }
.fmt-card-desc { font-size: 11px; color: var(--app-text-secondary); margin-top: 4px; line-height: 1.45; }
.delim-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.chunk-btn {
  border: 1px solid var(--app-border);
  background: var(--app-window-bg);
  border-radius: 8px;
  padding: 4px 10px;
  font-size: 12px;
  cursor: pointer;
  color: var(--app-text-regular);
}
.chunk-btn.is-active {
  border-color: var(--brand);
  color: var(--brand);
  background: var(--c-tianshuibi-soft);
}
.delim-preview {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11.5px;
  background: var(--app-window-bg);
  border-radius: 6px;
  padding: 6px 10px;
  border: 1px dashed var(--app-border);
}
.preview-tag { color: var(--brand); font-weight: 700; }
.preview-text { color: var(--app-text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.import-footer-actions { display: flex; align-items: center; justify-content: space-between; }
.footer-left { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.count-pill {
  font-size: 12px;
  color: var(--app-text-secondary);
  background: var(--el-fill-color);
  padding: 4px 8px;
  border-radius: 8px;
}
.import-success-card {
  border: 1px solid var(--c-tianshuibi-border);
  background: var(--c-tianshuibi-soft);
  border-radius: 12px;
  padding: 14px 16px;
}
.success-header { display: flex; align-items: center; justify-content: space-between; }
.success-title { display: flex; align-items: center; gap: 8px; font-weight: 700; color: var(--app-title); }
.icon-success { color: var(--brand); }
.kpi-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-top: 12px; }
.kpi-item {
  background: var(--app-window-bg);
  border-radius: 8px;
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.kpi-tag { font-size: 11px; color: var(--app-text-secondary); }
.kpi-val { font-size: 18px; font-weight: 700; }
.error-feedback-card {
  border: 1px solid rgba(199, 86, 77, 0.25);
  background: rgba(199, 86, 77, 0.06);
  border-radius: 10px;
  padding: 10px 12px;
}
.error-card-header { display: flex; align-items: center; justify-content: space-between; }
.err-title-wrap { display: flex; align-items: center; gap: 6px; }
.err-icon { color: var(--el-color-danger); }
.err-title { font-size: 12.5px; font-weight: 700; }
.err-list { margin: 8px 0 0; padding-left: 0; list-style: none; max-height: 140px; overflow: auto; }
.err-list li { font-size: 12px; padding: 3px 0; }
.err-line-tag { color: var(--el-color-danger); margin-right: 8px; }
.export-dialog-head { display: flex; align-items: center; gap: 10px; font-weight: 600; }

@media (max-width: 960px) {
  .hud-kpi-grid, .fmt-cards, .kpi-grid { grid-template-columns: 1fr 1fr; }
}
</style>
