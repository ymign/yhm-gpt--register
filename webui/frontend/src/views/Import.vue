<script setup>
import { computed, onActivated, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  Upload,
  FolderOpened,
  Check,
  Warning,
  Right,
  Delete,
  Search,
  Refresh,
  Opportunity,
  DocumentCopy,
  CircleCheck,
  CircleClose,
  DataAnalysis,
  Operation,
  InfoFilled,
} from '@element-plus/icons-vue'
import { importAccounts, analyzeImportAccounts } from '@/api/accounts'
import { getMailProviders } from '@/api/settings'
import { useStatsStore } from '@/stores/stats'
import { useRuntimeStore } from '@/stores/runtime'
import { copyText } from '@/api/request'

const router = useRouter()
const statsStore = useStatsStore()
const runtime = useRuntimeStore()

const providers = ref([])
const kind = ref('outlook')
const strategy = ref('smart_merge') // 'smart_merge' | 'skip_duplicates' | 'overwrite'
const text = ref('')
const loading = ref(false)
const analyzing = ref(false)
const importSummary = ref(null)
const errors = ref([])
const fileInput = ref(null)

// 导入进度计时器
const elapsedSeconds = ref(0)
let timerId = null

// 导入前数据透视与去重分析结果
const analysis = ref(null)
let autoAnalyzeTimer = null

const current = computed(
  () => providers.value.find((p) => p.kind === kind.value) || null,
)

const recordCount = computed(() => {
  const t = text.value
  if (!t) return 0
  return t.split('\n').filter((l) => l.trim() && !l.trim().startsWith('#')).length
})

const importCountLabel = computed(() => (recordCount.value ? `待处理 ${recordCount.value.toLocaleString()} 条` : ''))

async function loadProviders() {
  try {
    const r = await getMailProviders(true)
    providers.value = r.providers || []
    const cur = r.current
    kind.value = providers.value.some((p) => p.kind === cur)
      ? cur
      : (providers.value[0]?.kind || 'outlook')
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
    ElMessage.success(`已从文件读取 ${recordCount.value.toLocaleString()} 行数据`)
    runAnalysis()
  }
  reader.readAsText(file)
  e.target.value = '' // reset input
}

function clearText() {
  text.value = ''
  analysis.value = null
  importSummary.value = null
  errors.value = []
}

// ── 导入前格式透视与去重分析 ──
async function runAnalysis() {
  const raw = text.value.trim()
  if (!raw) {
    analysis.value = null
    return
  }
  analyzing.value = true
  try {
    const res = await analyzeImportAccounts(raw, kind.value)
    if (res && res.ok) {
      analysis.value = res
      if (res.invalid_errors?.length) {
        errors.value = res.invalid_errors
      } else {
        errors.value = []
      }
    }
  } catch (e) {
    console.error('analyze error:', e)
  } finally {
    analyzing.value = false
  }
}

// 文本输入变化时防抖自动分析
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
    autoAnalyzeTimer = setTimeout(() => {
      runAnalysis()
    }, 500)
  }
)

watch(
  () => kind.value,
  () => {
    if (text.value.trim()) {
      runAnalysis()
    }
  }
)

// ── 一键过滤并剔除格式异常行 ──
function removeInvalidRows() {
  if (!analysis.value || !analysis.value.invalid_errors?.length) {
    ElMessage.info('没有需要清理的异常行')
    return
  }
  const errLines = new Set(analysis.value.invalid_errors.map((e) => e.line))
  const lines = text.value.split('\n')
  const validLines = []
  let lineCounter = 0
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i]
    if (l.trim() && !l.trim().startsWith('#')) {
      lineCounter += 1
      if (!errLines.has(lineCounter)) {
        validLines.push(l)
      }
    }
  }
  text.value = validLines.join('\n')
  errors.value = []
  ElMessage.success(`已自动剔除 ${errLines.size} 行异常数据，保留 ${validLines.length} 行有效数据`)
}

// ── 执行批量导入 ──
async function doImport() {
  if (!text.value.trim()) {
    ElMessage.warning('请输入或选择要导入的账号数据')
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
    const r = await importAccounts(text.value.trim(), kind.value, strategy.value)
    clearInterval(timerId)
    elapsedSeconds.value = +((performance.now() - startTime) / 1000).toFixed(2)

    importSummary.value = {
      parsed: r.parsed || 0,
      inserted: r.inserted || 0,
      updated: r.updated || 0,
      skipped_registered: r.skipped_registered || 0,
      skipped: r.skipped || 0,
      cost_seconds: r.cost_seconds || elapsedSeconds.value,
    }

    ElMessage.success(`成功完成批量导入！共写入/更新 ${((r.inserted || 0) + (r.updated || 0)).toLocaleString()} 条`)
    statsStore.refresh()
    runtime.bumpData()
    // 导入成功后重新触发分析，展示入库后的最新状态
    runAnalysis()
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
          <div class="header-title-box">
            <span class="panel-title">号池智能导入与批量去重透视 · Fast Importer 2.0</span>
            <span class="panel-sub-title">全分隔符自适应 · 乱序智能嗅探 · 毫秒级内存去重</span>
          </div>
        </div>
        <div class="header-right">
          <span class="header-badge">极速事务写入 (万级秒级入库 · 自动防重)</span>
        </div>
      </div>

      <div class="import-container">
        <!-- ════════════ 1. 顶部控制栏：协议来源、策略选择与文件操作 ════════════ -->
        <div class="import-top-card">
          <div class="form-row-top">
            <div class="form-row-left">
              <div class="field-item">
                <span class="field-label">邮箱来源协议：</span>
                <el-select v-model="kind" style="width: 220px" placeholder="请选择邮箱类型">
                  <el-option
                    v-for="p in providers"
                    :key="p.kind"
                    :label="p.display_name"
                    :value="p.kind"
                  />
                </el-select>
              </div>

              <div class="field-item">
                <span class="field-label">导入去重策略：</span>
                <el-select v-model="strategy" style="width: 260px" placeholder="选择处理策略">
                  <el-option label="🌟 智能合并 (新号入库/老号绑凭证/同号更新)" value="smart_merge" />
                  <el-option label="⏭️ 仅导全新号 (跳过全部库内重复)" value="skip_duplicates" />
                  <el-option label="⚡ 强制覆盖更新 (重置已有号为可用)" value="overwrite" />
                </el-select>
              </div>
            </div>

            <div class="form-row-right">
              <input
                ref="fileInput"
                type="file"
                accept=".txt,.csv,.tsv,.log"
                style="display: none"
                @change="handleFileSelected"
              />
              <el-button size="small" @click="triggerFileInput">
                <el-icon><FolderOpened /></el-icon> 从本地文件读取 (.txt / .csv)
              </el-button>
              <el-button
                v-if="text"
                size="small"
                :loading="analyzing"
                type="primary"
                plain
                @click="runAnalysis"
              >
                <el-icon><DataAnalysis /></el-icon> 重新分析透视
              </el-button>
              <el-button v-if="text" size="small" text type="danger" @click="clearText">
                <el-icon><Delete /></el-icon> 清空
              </el-button>
            </div>
          </div>

          <!-- 智能自适应格式说明条 -->
          <div class="hint-banner">
            <div class="hint-title-row">
              <span class="hint-tag">✨ 智能全格式兼容已就绪</span>
              <span class="hint-desc">支持 <code>----</code>、<code>---</code>、<code>--</code>、逗号 <code>,</code>、制表符 <code>Tab</code>、竖线 <code>|</code>、冒号 <code>:</code> 或空格分隔；支持<b>任意乱序</b>自适应识别；自动剥离首尾序号与引号。</span>
            </div>
          </div>
        </div>

        <!-- ════════════ 2. 文本录入区域 ════════════ -->
        <div class="editor-section">
          <div class="editor-header">
            <div class="editor-title">
              <span class="title-text">账号凭证数据录入 (每行一条)</span>
              <span v-if="recordCount" class="count-chip">{{ recordCount.toLocaleString() }} 行待导入</span>
            </div>
            <div class="editor-actions">
              <el-button
                v-if="errors.length"
                size="small"
                type="warning"
                plain
                @click="removeInvalidRows"
              >
                🧹 一键剔除 {{ errors.length }} 行格式异常
              </el-button>
            </div>
          </div>

          <el-input
            v-model="text"
            type="textarea"
            :rows="9"
            class="mono import-textarea"
            placeholder="支持粘贴任意格式，例如：&#10;test@hotmail.com----密码----Client_ID----Refresh_Token&#10;test@hotmail.com,密码,Client_ID,Refresh_Token&#10;密码----test@hotmail.com----Refresh_Token----Client_ID (乱序自适应)&#10;1. &quot;test@hotmail.com&quot; | &quot;密码&quot; | &quot;Client_ID&quot; | &quot;Refresh_Token&quot; (自动去序号与引号)"
          />
        </div>

        <!-- ════════════ 3. 导入前数据透视与去重分析大屏 (Pre-import Analysis HUD) ════════════ -->
        <el-collapse-transition>
          <div v-if="analysis && analysis.total_lines > 0" class="analysis-hud-panel">
            <div class="hud-header">
              <div class="hud-title-wrap">
                <span class="hud-badge">PRE-IMPORT ANALYSIS</span>
                <span class="hud-title">导入前多维透视与去重分析报告</span>
              </div>
              <div class="hud-rate-wrap">
                <span class="rate-label">综合重复率:</span>
                <span
                  class="rate-val mono"
                  :class="analysis.dup_rate > 30 ? 'rate-high' : 'rate-good'"
                >
                  {{ analysis.dup_rate }}%
                </span>
              </div>
            </div>

            <!-- 4 大核心 KPI 矩阵 -->
            <div class="hud-kpi-grid">
              <!-- KPI 1: 解析与有效 -->
              <div class="hud-kpi-card">
                <div class="kpi-header">
                  <span class="kpi-icon-dot dot-blue"></span>
                  <span class="kpi-label">识别有效行数</span>
                </div>
                <div class="kpi-main-val text-blue">
                  {{ analysis.valid_count }} <span class="kpi-sub-total">/ {{ analysis.total_lines }}</span>
                </div>
                <div class="kpi-footer-note">
                  <span v-if="analysis.invalid_count === 0" class="text-success">✅ 100% 格式合规</span>
                  <span v-else class="text-danger">⚠️ {{ analysis.invalid_count }} 行无法识别</span>
                </div>
              </div>

              <!-- KPI 2: 全新待入库 -->
              <div class="hud-kpi-card highlight-card-brandnew">
                <div class="kpi-header">
                  <span class="kpi-icon-dot dot-emerald"></span>
                  <span class="kpi-label">全新待入库 (Brand New)</span>
                </div>
                <div class="kpi-main-val text-emerald">
                  +{{ analysis.brand_new_count }}
                </div>
                <div class="kpi-footer-note text-emerald">
                  未在号池，也未在已注册库
                </div>
              </div>

              <!-- KPI 3: 批次内自身重复 -->
              <div class="hud-kpi-card">
                <div class="kpi-header">
                  <span class="kpi-icon-dot dot-amber"></span>
                  <span class="kpi-label">批次内重复 (Internal Dup)</span>
                </div>
                <div class="kpi-main-val text-amber">
                  {{ analysis.internal_dup_count }}
                </div>
                <div class="kpi-footer-note text-secondary">
                  已自动去重，保留最新凭证
                </div>
              </div>

              <!-- KPI 4: 库内已存在 / GPT老号 -->
              <div class="hud-kpi-card">
                <div class="kpi-header">
                  <span class="kpi-icon-dot dot-purple"></span>
                  <span class="kpi-label">库内已存量 (DB Existing)</span>
                </div>
                <div class="kpi-main-val text-purple">
                  {{ analysis.pool_dup_count + analysis.registered_dup_count }}
                </div>
                <div class="kpi-footer-note text-secondary">
                  号池 {{ analysis.pool_dup_count }} · GPT老号 {{ analysis.registered_dup_count }}
                </div>
              </div>
            </div>

            <!-- 数据去重分布彩色比例条 -->
            <div class="distribution-track-box">
              <div class="dist-track">
                <!-- 全新号 (绿) -->
                <div
                  v-if="analysis.brand_new_count > 0"
                  class="dist-bar dist-green"
                  :style="{ width: `${(analysis.brand_new_count / analysis.total_lines) * 100}%` }"
                  :title="`全新号: ${analysis.brand_new_count} 条`"
                ></div>
                <!-- 号池重复 (蓝) -->
                <div
                  v-if="analysis.pool_dup_count > 0"
                  class="dist-bar dist-blue"
                  :style="{ width: `${(analysis.pool_dup_count / analysis.total_lines) * 100}%` }"
                  :title="`号池已存: ${analysis.pool_dup_count} 条`"
                ></div>
                <!-- 已注册老号 (紫) -->
                <div
                  v-if="analysis.registered_dup_count > 0"
                  class="dist-bar dist-purple"
                  :style="{ width: `${(analysis.registered_dup_count / analysis.total_lines) * 100}%` }"
                  :title="`GPT已注册老号: ${analysis.registered_dup_count} 条`"
                ></div>
                <!-- 内部重复 (黄) -->
                <div
                  v-if="analysis.internal_dup_count > 0"
                  class="dist-bar dist-amber"
                  :style="{ width: `${(analysis.internal_dup_count / analysis.total_lines) * 100}%` }"
                  :title="`批次内部重复: ${analysis.internal_dup_count} 条`"
                ></div>
                <!-- 格式异常 (红) -->
                <div
                  v-if="analysis.invalid_count > 0"
                  class="dist-bar dist-red"
                  :style="{ width: `${(analysis.invalid_count / analysis.total_lines) * 100}%` }"
                  :title="`格式异常: ${analysis.invalid_count} 条`"
                ></div>
              </div>

              <div class="dist-legend-row">
                <span class="legend-item"><span class="legend-dot bg-green"></span>全新可用 ({{ analysis.brand_new_count }})</span>
                <span class="legend-item"><span class="legend-dot bg-blue"></span>号池已存 ({{ analysis.pool_dup_count }})</span>
                <span class="legend-item"><span class="legend-dot bg-purple"></span>GPT老号 ({{ analysis.registered_dup_count }})</span>
                <span class="legend-item"><span class="legend-dot bg-amber"></span>批次内重复 ({{ analysis.internal_dup_count }})</span>
                <span v-if="analysis.invalid_count" class="legend-item text-danger"><span class="legend-dot bg-red"></span>格式异常 ({{ analysis.invalid_count }})</span>
              </div>
            </div>

            <!-- 数据抽样识别预览表格 (前 30 条) -->
            <div class="preview-table-box">
              <div class="preview-table-title">
                <span>📋 格式识别与状态透视采样预览 (前 {{ analysis.preview_rows?.length || 0 }} 条)</span>
              </div>
              <el-table
                :data="analysis.preview_rows"
                size="small"
                stripe
                height="220"
                class="macos-table"
                empty-text="暂无预览数据"
              >
                <el-table-column prop="line" label="行号" width="60" align="center">
                  <template #default="{ row }">
                    <span class="mono hint">#{{ row.line }}</span>
                  </template>
                </el-table-column>

                <el-table-column prop="email" label="识别邮箱" min-width="190" show-overflow-tooltip>
                  <template #default="{ row }">
                    <div class="email-preview-cell">
                      <span class="mono">{{ row.email }}</span>
                      <el-icon class="copy-ico" @click.stop="copyText(row.email)"><DocumentCopy /></el-icon>
                    </div>
                  </template>
                </el-table-column>

                <el-table-column label="密码" width="100" align="center">
                  <template #default="{ row }">
                    <span v-if="row.password_masked" class="mono text-success">{{ row.password_masked }}</span>
                    <span v-else class="hint">无密码</span>
                  </template>
                </el-table-column>

                <el-table-column label="Client ID" width="130" show-overflow-tooltip>
                  <template #default="{ row }">
                    <span v-if="row.client_id" class="mono hint">{{ row.client_id }}</span>
                    <span v-else class="hint">—</span>
                  </template>
                </el-table-column>

                <el-table-column label="RT 凭证" width="90" align="center">
                  <template #default="{ row }">
                    <el-tag v-if="row.rt_len" size="small" type="primary" effect="plain" class="mono">
                      {{ row.rt_len }}位
                    </el-tag>
                    <span v-else class="hint">—</span>
                  </template>
                </el-table-column>

                <el-table-column label="识别格式与特征" min-width="170" show-overflow-tooltip>
                  <template #default="{ row }">
                    <span class="format-desc-pill">{{ row.detected_format }}</span>
                  </template>
                </el-table-column>

                <el-table-column label="库内状态" width="130" align="center">
                  <template #default="{ row }">
                    <el-tag
                      size="small"
                      :type="row.db_status === 'brand_new' ? 'success' : (row.db_status === 'registered_gpt' ? 'warning' : (row.db_status === 'internal_dup' ? 'info' : 'primary'))"
                      effect="light"
                      class="status-pill"
                    >
                      {{ row.db_label }}
                    </el-tag>
                  </template>
                </el-table-column>
              </el-table>
            </div>
          </div>
        </el-collapse-transition>

        <!-- ════════════ 4. 底部提交与统计栏 ════════════ -->
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
              {{ loading ? `正在高速事务写入 (${elapsedSeconds}s)...` : '确认写入号池数据库' }}
            </el-button>

            <span v-if="recordCount" class="count-pill">{{ importCountLabel }}</span>

            <span class="strategy-badge-tip">
              当前策略：<b>{{ strategy === 'smart_merge' ? '智能合并' : (strategy === 'skip_duplicates' ? '仅全新号' : '覆盖重置') }}</b>
            </span>
          </div>

          <div class="footer-right">
            <span v-if="loading" class="loading-hint-pulse">
              <span class="pulse-dot"></span> 内存比对 ➔ 防重去重 ➔ 批量事务写入中...
            </span>
          </div>
        </div>

        <!-- ════════════ 5. 导入成功结果 KPI 看板 ════════════ -->
        <el-collapse-transition>
          <div v-if="importSummary" class="import-success-card">
            <div class="success-header">
              <div class="success-title">
                <el-icon class="icon-success"><Check /></el-icon>
                <span>🎉 批量入库完成！耗时 {{ importSummary.cost_seconds }}s</span>
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
                <span class="kpi-tag">🛡️ 同步已注册</span>
                <span class="kpi-val text-warning">{{ importSummary.skipped_registered.toLocaleString() }}</span>
              </div>
              <div class="kpi-item">
                <span class="kpi-tag">⏭️ 重复跳过</span>
                <span class="kpi-val text-muted">{{ importSummary.skipped.toLocaleString() }}</span>
              </div>
            </div>
          </div>
        </el-collapse-transition>

        <!-- ════════════ 6. 错误详情反馈 ════════════ -->
        <el-collapse-transition>
          <div v-if="errors.length" class="error-feedback-card">
            <div class="error-card-header">
              <div class="err-title-wrap">
                <el-icon class="err-icon"><Warning /></el-icon>
                <span class="err-title">发现 {{ errors.length }} 行格式异常或未匹配到有效邮箱：</span>
              </div>
              <div class="err-actions">
                <el-button size="small" type="warning" plain @click="removeInvalidRows">
                  一键剔除这些异常行
                </el-button>
                <el-button size="small" text @click="errors = []">关闭</el-button>
              </div>
            </div>
            <ul class="err-list mono">
              <li v-for="e in errors" :key="e.line">
                <span class="err-line-tag">Line {{ e.line }}</span>
                <span class="err-msg">{{ e.error }}</span>
                <span v-if="e.raw" class="err-raw hint">({{ e.raw }})</span>
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

.header-title-box {
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.panel-title {
  font-size: 13.5px;
  font-weight: 700;
  color: var(--app-title);
}
.panel-sub-title {
  font-size: 11px;
  color: var(--el-text-color-secondary);
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
  padding: 16px 20px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

/* ──────────── 顶部控制卡片 ──────────── */
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
.form-row-left {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 16px;
}
.form-row-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.field-item {
  display: flex;
  align-items: center;
  gap: 8px;
}
.field-label {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--app-title);
}

.hint-banner {
  background: var(--app-window-bg);
  border: 1px solid var(--app-border);
  border-radius: 6px;
  padding: 8px 12px;
}
.hint-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11.5px;
  line-height: 1.5;
}
.hint-tag {
  font-weight: 700;
  color: var(--el-color-primary);
  white-space: nowrap;
}
.hint-desc {
  color: var(--el-text-color-secondary);
}

/* ──────────── 编辑器部分 ──────────── */
.editor-section {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.editor-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.editor-title {
  display: flex;
  align-items: center;
  gap: 8px;
}
.title-text {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--app-title);
}
.count-chip {
  font-size: 11px;
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  padding: 1px 6px;
  border-radius: 4px;
  font-family: var(--font-mono, monospace);
}

.import-textarea :deep(.el-textarea__inner) {
  font-size: 12px;
  line-height: 1.6;
  font-family: var(--font-mono, monospace);
  background: var(--el-fill-color-blank);
}

/* ──────────── 导入前去重分析 HUD ──────────── */
.analysis-hud-panel {
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 12px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.hud-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--el-border-color-lighter);
  padding-bottom: 8px;
}
.hud-title-wrap {
  display: flex;
  align-items: center;
  gap: 8px;
}
.hud-badge {
  font-size: 10px;
  font-weight: 700;
  color: #007aff;
  background: rgba(0, 122, 255, 0.1);
  border: 1px solid rgba(0, 122, 255, 0.3);
  padding: 1px 6px;
  border-radius: 4px;
}
.hud-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--app-title);
}

.hud-rate-wrap {
  display: flex;
  align-items: center;
  gap: 6px;
}
.rate-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.rate-val {
  font-size: 14px;
  font-weight: 700;
}
.rate-good {
  color: #10b981;
}
.rate-high {
  color: #f59e0b;
}

.hud-kpi-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
}
.hud-kpi-card {
  background: var(--app-window-bg);
  border: 1px solid var(--app-border);
  border-radius: 8px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.highlight-card-brandnew {
  background: rgba(16, 185, 129, 0.06);
  border-color: rgba(16, 185, 129, 0.35);
}

.kpi-header {
  display: flex;
  align-items: center;
  gap: 6px;
}
.kpi-icon-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}
.dot-blue { background: #007aff; }
.dot-emerald { background: #10b981; }
.dot-amber { background: #f59e0b; }
.dot-purple { background: #8b5cf6; }

.kpi-label {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}
.kpi-main-val {
  font-size: 18px;
  font-weight: 700;
  font-family: var(--font-mono, monospace);
}
.kpi-sub-total {
  font-size: 12px;
  font-weight: 400;
  color: var(--el-text-color-secondary);
}
.kpi-footer-note {
  font-size: 10.5px;
}

/* 进度分布条 */
.distribution-track-box {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.dist-track {
  height: 8px;
  border-radius: 99px;
  background: var(--el-fill-color);
  overflow: hidden;
  display: flex;
}
.dist-bar {
  height: 100%;
  transition: width 0.3s ease;
}
.dist-green { background: #10b981; }
.dist-blue { background: #007aff; }
.dist-purple { background: #8b5cf6; }
.dist-amber { background: #f59e0b; }
.dist-red { background: #ef4444; }

.dist-legend-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 14px;
  font-size: 11px;
  color: var(--el-text-color-secondary);
}
.legend-item {
  display: flex;
  align-items: center;
  gap: 5px;
}
.legend-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
}
.bg-green { background: #10b981; }
.bg-blue { background: #007aff; }
.bg-purple { background: #8b5cf6; }
.bg-amber { background: #f59e0b; }
.bg-red { background: #ef4444; }

/* 预览表格 */
.preview-table-box {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.preview-table-title {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--app-title);
}
.email-preview-cell {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.copy-ico {
  cursor: pointer;
  opacity: 0.6;
}
.copy-ico:hover {
  opacity: 1;
  color: var(--el-color-primary);
}
.format-desc-pill {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

/* ──────────── 底部控制栏 ──────────── */
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
  padding: 8px 22px;
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
.strategy-badge-tip {
  font-size: 11.5px;
  color: var(--el-text-color-secondary);
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
.text-blue { color: #007aff !important; }
.text-emerald { color: #10b981 !important; }
.text-amber { color: #f59e0b !important; }
.text-purple { color: #8b5cf6 !important; }
.text-danger { color: #ef4444 !important; }
.text-secondary { color: var(--el-text-color-secondary) !important; }

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
  font-size: 12.5px;
  font-weight: 600;
  color: #ff3b30;
}
.err-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.err-list {
  margin: 8px 0 0 0;
  padding: 0;
  list-style: none;
  max-height: 140px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.err-list li {
  font-size: 11.5px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.err-line-tag {
  color: #ff3b30;
  background: rgba(255, 59, 48, 0.12);
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 10.5px;
}
.err-msg {
  color: var(--app-title);
}
.err-raw {
  font-size: 10.5px;
}
</style>
