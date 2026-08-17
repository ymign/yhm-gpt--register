<script setup>
import { computed, onActivated, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Upload, DocumentAdd, Refresh, Select, CircleClose } from '@element-plus/icons-vue'
import { importAccounts } from '@/api/accounts'
import { getMailProviders } from '@/api/settings'
import { useStatsStore } from '@/stores/stats'
import { useRuntimeStore } from '@/stores/runtime'

const statsStore = useStatsStore()
const runtime = useRuntimeStore()

const providers = ref([])
const kind = ref('')
const text = ref('')
const loading = ref(false)
const result = ref('')
const errors = ref([])

const current = computed(
  () => providers.value.find((p) => p.kind === kind.value) || null,
)

const lineCount = computed(
  () => text.value.split('\n').filter((l) => l.trim() && !l.trim().startsWith('#')).length,
)

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

async function doImport() {
  if (!text.value.trim()) {
    ElMessage.warning('请输入要导入的账号数据')
    return
  }
  if (!kind.value) {
    ElMessage.warning('请先选择邮箱来源')
    return
  }
  loading.value = true
  result.value = ''
  errors.value = []
  try {
    const r = await importAccounts(text.value.trim(), kind.value)
    result.value = `解析 ${r.parsed} 行，新增 ${r.inserted}，更新 ${r.updated}，跳过 ${r.skipped}`
    ElMessage.success('导入成功')
    text.value = ''
    statsStore.refresh()
    runtime.bumpData()
  } catch (e) {
    if (e.status === 422 && e.data?.errors?.length) {
      errors.value = e.data.errors
      result.value = `有 ${e.data.errors.length} 行不合法，已全部拒绝`
      ElMessage.error('格式校验未通过，请检查错误提示')
    } else {
      result.value = '导入失败: ' + e.message
      ElMessage.error(e.message)
    }
  } finally {
    loading.value = false
  }
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
          <span class="panel-title">批量导入邮箱号池 · Account Importer</span>
        </div>
        <div class="header-right">
          <span class="header-badge">全量原子导入 (全合法才写入)</span>
        </div>
      </div>

      <div class="import-container">
        <!-- 来源选择与规则提示卡片 -->
        <div class="import-top-card">
          <div class="form-row">
            <span class="field-label">选择邮箱来源协议：</span>
            <el-select v-model="kind" style="width: 260px" placeholder="请选择邮箱类型">
              <el-option
                v-for="p in providers"
                :key="p.kind"
                :label="p.display_name"
                :value="p.kind"
              />
            </el-select>
          </div>

          <div v-if="current" class="hint-banner">
            <div class="hint-title">
              💡 每行一条，标准 {{ current.line_segments }} 段格式（以 <code>----</code> 隔开）：
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
              class="import-submit-btn"
              :loading="loading"
              @click="doImport"
            >
              <el-icon><Upload /></el-icon>开始导入号池
            </el-button>
            <span v-if="lineCount" class="count-pill">待导入 {{ lineCount }} 行</span>
            <span v-if="result" class="result-tip">{{ result }}</span>
          </div>
        </div>

        <!-- 错误详情反馈 -->
        <el-collapse-transition>
          <div v-if="errors.length" class="error-feedback-card">
            <div class="error-card-header">
              <span class="err-title">以下 {{ errors.length }} 行格式不合规，整批已安全拒绝：</span>
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

.form-row {
  display: flex;
  align-items: center;
  gap: 10px;
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
  padding: 8px 0;
}
.footer-left {
  display: flex;
  align-items: center;
  gap: 12px;
}
.import-submit-btn {
  border-radius: 8px;
  font-weight: 600;
}
.count-pill {
  font-size: 11.5px;
  color: var(--app-title);
  background: var(--el-fill-color-light);
  border: 1px solid var(--app-border);
  padding: 3px 8px;
  border-radius: 6px;
  font-weight: 600;
}
.result-tip {
  font-size: 12px;
  color: var(--apple-green);
  font-weight: 500;
}

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
.err-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--apple-red);
}
.err-list {
  margin: 8px 0 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 140px;
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
  color: var(--apple-red);
  padding: 1px 5px;
  border-radius: 4px;
  font-weight: 700;
}
.err-msg {
  color: var(--app-title);
}
</style>
