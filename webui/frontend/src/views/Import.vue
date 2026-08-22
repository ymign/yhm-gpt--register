<script setup>
import { computed, onActivated, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  Upload,
  DocumentAdd,
  Refresh,
  Select,
  CircleClose,
  FolderOpened,
  Check,
  Warning,
  Right,
  Delete,
} from '@element-plus/icons-vue'
import { importAccounts } from '@/api/accounts'
import { getMailProviders } from '@/api/settings'
import { useStatsStore } from '@/stores/stats'
import { useRuntimeStore } from '@/stores/runtime'

const router = useRouter()
const statsStore = useStatsStore()
const runtime = useRuntimeStore()

const providers = ref([])
const kind = ref('')
const text = ref('')
const loading = ref(false)
const importSummary = ref(null)
const errors = ref([])
const fileInput = ref(null)

// 导入进度计时器
const elapsedSeconds = ref(0)
let timerId = null

const current = computed(
  () => providers.value.find((p) => p.kind === kind.value) || null,
)

const recordCount = computed(() => {
  const t = text.value
  if (!t) return 0
  return t.split('\n').filter((l) => l.trim() && !l.trim().startsWith('#')).length
})

const importCountLabel = computed(() => (recordCount.value ? `待导入 ${recordCount.value.toLocaleString()} 条` : ''))

async function loadProviders() {
  try {
    const r = await getMailProviders(true)
    providers.value = r.providers || []
    const cur = r.current
    kind.value = providers.value.some((p) => p.kind === cur)
      ? cur
      : (providers.value[0]?.kind || '')
  } catch (e) {
    ElMessage.error(e.message)
  }
}

onMounted(loadProviders)
onActivated(loadProviders)

function triggerFileInput() {
  fileInput.value?.click()
}

function handleFileSelected(e) {
  const file = e.target.files?.[0]
  if (!file) return
  const reader = new FileReader()
  reader.onload = (evt) => {
    text.value = evt.target?.result || ''
    importSummary.value = null
    errors.value = []
    ElMessage.success(`已从文件加载 ${recordCount.value.toLocaleString()} 条账号数据`)
  }
  reader.readAsText(file)
  e.target.value = '' // reset input
}

function clearText() {
  text.value = ''
  importSummary.value = null
  errors.value = []
}

async function doImport() {
  if (!text.value.trim()) {
    ElMessage.warning('请输入或选择要导入的账号数据')
    return
  }
  if (!kind.value) {
    ElMessage.warning('请先选择邮箱来源协议')
    return
  }

  loading.value = true
  importSummary.value = null
  errors.value = []
  elapsedSeconds.value = 0

  const startTime = performance.now()
  timerId = setInterval(() => {
    elapsedSeconds.value = +((performance.now() - startTime) / 1000).toFixed(1)
  }, 100)

  try {
    const r = await importAccounts(text.value.trim(), kind.value)
    clearInterval(timerId)
    elapsedSeconds.value = +( (performance.now() - startTime) / 1000 ).toFixed(2)

    importSummary.value = {
      parsed: r.parsed || 0,
      inserted: r.inserted || 0,
      updated: r.updated || 0,
      skipped_registered: r.skipped_registered || 0,
      skipped: r.skipped || 0,
      cost_seconds: r.cost_seconds || elapsedSeconds.value,
    }

    ElMessage.success(`成功完成批量导入！共写入/更新 ${((r.inserted || 0) + (r.updated || 0)).toLocaleString()} 条`)
    text.value = ''
    statsStore.refresh()
    runtime.bumpData()
  } catch (e) {
    clearInterval(timerId)
    if (e.status === 422 && e.data?.errors?.length) {
      errors.value = e.data.errors
      ElMessage.error(`格式校验未通过（有 ${e.data.errors.length} 行错误），整批已安全拦截拒绝`)
    } else {
      ElMessage.error('导入失败: ' + (e.message || '网络或服务异常'))
    }
  } finally {
    loading.value = false
  }
}

function gotoPool() {
  router.push('/pool')
}
</script>

<template>
  <div class="import-page">
    <div class="macos-window-panel">
      <!-- 窗口标题栏 -->
      <div class="macos-panel-header">
        <div class="header-left">
          <div class="window-dot-group">
            <span class="dot red"></span>
            <span class="dot yellow"></span>
            <span class="dot green"></span>
          </div>
          <span class="panel-title">批量导入邮箱号池 · Fast Account Importer</span>
        </div>
        <div class="header-right">
          <span class="header-badge">极速事务写入 (万级秒级入库 · 自动防重)</span>
        </div>
      </div>

      <div class="import-container">
        <!-- 来源选择与规则提示卡片 -->
        <div class="import-top-card">
          <div class="form-row-top">
            <div class="form-row-left">
              <span class="field-label">选择邮箱来源协议：</span>
              <el-select v-model="kind" style="width: 240px" placeholder="请选择邮箱类型">
                <el-option
                  v-for="p in providers"
                  :key="p.kind"
                  :label="p.display_name"
                  :value="p.kind"
                />
              </el-select>
            </div>

            <div class="form-row-right">
              <input
                ref="fileInput"
                type="file"
                accept=".txt,.csv,.log"
                style="display: none"
                @change="handleFileSelected"
              />
              <el-button size="small" @click="triggerFileInput">
                <el-icon><FolderOpened /></el-icon> 从本地 .txt 文件导入
              </el-button>
              <el-button v-if="text" size="small" text type="danger" @click="clearText">
                <el-icon><Delete /></el-icon> 清空输入
              </el-button>
            </div>
          </div>

          <div v-if="current" class="hint-banner">
            <div class="hint-title">
              💡 格式规范：标准 {{ current.line_segments }} 段格式（以 <code>----</code> 隔开）：
            </div>
            <code class="hint-code mono">{{ current.import_hint || '' }}</code>
          </div>
        </div>

        <!-- 文本录入区域 -->
        <div class="editor-wrap">
          <el-input
            v-model="text"
            type="textarea"
            :rows="12"
            class="mono import-textarea"
            :placeholder="current?.import_placeholder || '邮箱----密码----辅助邮箱----...'"
          />
        </div>

        <!-- 底部提交与统计栏 -->
        <div class="import-footer-actions">
          <div class="footer-left">
            <el-button
              type="primary"
              size="default"
              class="import-submit-btn"
              :loading="loading"
              @click="doImport"
            >
              <el-icon><Upload /></el-icon>
              {{ loading ? `正在高速入库 (${elapsedSeconds}s)...` : '开始导入号池' }}
            </el-button>
            <span v-if="recordCount" class="count-pill">{{ importCountLabel }}</span>
          </div>

          <div class="footer-right">
            <span v-if="loading" class="loading-hint-pulse">
              <span class="pulse-dot"></span> 内存比对 ➔ 防重去重 ➔ 批量事务写入中...
            </span>
          </div>
        </div>

        <!-- 导入成功结果 KPI 看板 -->
        <el-collapse-transition>
          <div v-if="importSummary" class="import-success-card">
            <div class="success-header">
              <div class="success-title">
                <el-icon class="icon-success"><Check /></el-icon>
                <span>🎉 批量导入完成！耗时 {{ importSummary.cost_seconds }}s</span>
              </div>
              <el-button size="small" type="primary" plain @click="gotoPool">
                前往邮箱号池查看 <el-icon><Right /></el-icon>
              </el-button>
            </div>

            <div class="kpi-grid">
              <div class="kpi-item">
                <span class="kpi-tag">📥 解析总数</span>
                <span class="kpi-val">{{ importSummary.parsed.toLocaleString() }}</span>
              </div>
              <div class="kpi-item highlight-insert">
                <span class="kpi-tag">✨ 全新入库</span>
                <span class="kpi-val text-success">+{{ importSummary.inserted.toLocaleString() }}</span>
              </div>
              <div class="kpi-item">
                <span class="kpi-tag">🔄 凭证更新</span>
                <span class="kpi-val text-primary">{{ importSummary.updated.toLocaleString() }}</span>
              </div>
              <div class="kpi-item">
                <span class="kpi-tag">🛡️ 跳过已注册</span>
                <span class="kpi-val text-warning">{{ importSummary.skipped_registered.toLocaleString() }}</span>
              </div>
              <div class="kpi-item">
                <span class="kpi-tag">⏭️ 完全重复跳过</span>
                <span class="kpi-val text-muted">{{ importSummary.skipped.toLocaleString() }}</span>
              </div>
            </div>
          </div>
        </el-collapse-transition>

        <!-- 错误详情反馈 -->
        <el-collapse-transition>
          <div v-if="errors.length" class="error-feedback-card">
            <div class="error-card-header">
              <div class="err-title-wrap">
                <el-icon class="err-icon"><Warning /></el-icon>
                <span class="err-title">发现 {{ errors.length }} 行格式不合规，整批已安全拒绝入库：</span>
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
      </div>
    </div>
  </div>
</template>

<style scoped>
.import-page {
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
.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}
.window-dot-group {
  display: flex;
  gap: 6px;
}
.dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
}
.dot.red { background: #ff5f56; }
.dot.yellow { background: #ffbd2e; }
.dot.green { background: #27c93f; }

.panel-title {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--app-title);
}
.header-badge {
  font-size: 11px;
  color: var(--app-text-secondary);
  background: var(--el-fill-color);
  padding: 2px 8px;
  border-radius: 10px;
  border: 1px solid var(--app-border);
}

.import-container {
  flex: 1;
  min-height: 0;
  padding: 18px 22px;
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
  gap: 10px;
}
.form-row-left {
  display: flex;
  align-items: center;
  gap: 10px;
}
.form-row-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
.field-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--app-title);
}

.hint-banner {
  background: var(--app-window-bg);
  border: 1px solid var(--app-border);
  border-radius: 6px;
  padding: 8px 12px;
}
.hint-title {
  font-size: 11.5px;
  color: var(--app-text-secondary);
  margin-bottom: 4px;
}
.hint-code {
  font-size: 12px;
  color: var(--el-color-primary);
  font-weight: 600;
}

.editor-wrap {
  flex: 1;
  min-height: 180px;
}
.import-textarea :deep(.el-textarea__inner) {
  height: 100% !important;
  min-height: 220px;
  font-size: 12px;
  line-height: 1.6;
}

.import-footer-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 0;
}
.footer-left {
  display: flex;
  align-items: center;
  gap: 12px;
}
.import-submit-btn {
  border-radius: 8px;
  font-weight: 600;
  padding: 8px 20px;
}
.count-pill {
  font-size: 12px;
  color: var(--app-title);
  background: var(--el-fill-color-light);
  border: 1px solid var(--app-border);
  padding: 4px 10px;
  border-radius: 6px;
  font-weight: 600;
}

.loading-hint-pulse {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--el-color-primary);
  font-weight: 500;
}
.pulse-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--el-color-primary);
  animation: pulse-anim 1.2s infinite ease-in-out;
}
@keyframes pulse-anim {
  0% { transform: scale(0.8); opacity: 0.4; }
  50% { transform: scale(1.3); opacity: 1; }
  100% { transform: scale(0.8); opacity: 0.4; }
}

/* 成功 KPI 看板 */
.import-success-card {
  background: rgba(39, 201, 63, 0.06);
  border: 1px solid rgba(39, 201, 63, 0.28);
  border-radius: 10px;
  padding: 14px 18px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.success-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.success-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13.5px;
  font-weight: 600;
  color: var(--app-title);
}
.icon-success {
  color: var(--el-color-success);
  font-size: 16px;
}

.kpi-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
  gap: 10px;
}
.kpi-item {
  background: var(--app-window-bg);
  border: 1px solid var(--app-border);
  border-radius: 8px;
  padding: 8px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.kpi-item.highlight-insert {
  border-color: rgba(39, 201, 63, 0.4);
  background: rgba(39, 201, 63, 0.08);
}
.kpi-tag {
  font-size: 11px;
  color: var(--app-text-secondary);
}
.kpi-val {
  font-size: 16px;
  font-weight: 700;
  font-family: var(--font-mono, monospace);
  color: var(--app-title);
}
.text-success { color: var(--el-color-success) !important; }
.text-primary { color: var(--el-color-primary) !important; }
.text-warning { color: var(--el-color-warning) !important; }
.text-muted { color: var(--app-text-secondary) !important; }

/* 错误反馈 */
.error-feedback-card {
  background: rgba(255, 59, 48, 0.08);
  border: 1px solid rgba(255, 59, 48, 0.25);
  border-radius: 8px;
  padding: 12px 14px;
}
.error-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.err-title-wrap {
  display: flex;
  align-items: center;
  gap: 6px;
}
.err-icon {
  color: var(--el-color-danger);
  font-size: 15px;
}
.err-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-color-danger);
}
.err-list {
  margin: 8px 0 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 160px;
  overflow-y: auto;
}
.err-list li {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11.5px;
}
.err-line-tag {
  background: rgba(255, 59, 48, 0.15);
  color: var(--el-color-danger);
  padding: 1px 5px;
  border-radius: 4px;
  font-weight: 700;
}
.err-msg {
  color: var(--app-title);
}
</style>
