<script setup>
import { computed, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Connection,
  VideoPlay,
  CopyDocument,
  Delete,
  Plus,
  Refresh,
  DocumentAdd,
} from '@element-plus/icons-vue'
import { useProxyStore, isValidProxy, proxyScheme } from '@/stores/proxy'
import { testProxies } from '@/api/proxy'
import { copyText } from '@/api/request'

const proxyStore = useProxyStore()
const { list, count } = storeToRefs(proxyStore)

const draft = ref('')
const testResults = ref({})
const testingAll = ref(false)

const rows = computed(() =>
  list.value.map((p, i) => ({
    index: i + 1,
    proxy: p,
    valid: isValidProxy(p),
    result: testResults.value[p] || null,
  })),
)
const invalidCount = computed(() => rows.value.filter((r) => !r.valid).length)

async function runTest(targets) {
  if (!targets.length) return
  for (const p of targets) testResults.value[p] = { status: 'testing' }
  try {
    const { results } = await testProxies(targets)
    for (const [proxy, res] of Object.entries(results)) {
      testResults.value[proxy] = { status: res.ok ? 'ok' : 'fail', ...res }
    }
  } catch (e) {
    for (const p of targets) testResults.value[p] = { status: 'fail', error: e.message }
    ElMessage.error('测试失败: ' + e.message)
  }
}

async function testOne(proxy) {
  await runTest([proxy])
}

async function testAll() {
  if (!count.value) return
  testingAll.value = true
  try {
    await runTest([...list.value])
  } finally {
    testingAll.value = false
  }
}

function save() {
  if (!draft.value.trim()) {
    ElMessage.warning('请先粘贴代理')
    return
  }
  const r = proxyStore.setFromText(draft.value)
  draft.value = ''
  ElMessage.success(`已保存 ${r.kept} 个代理${r.duplicated ? `（去重 ${r.duplicated} 个）` : ''}`)
}

function append() {
  if (!draft.value.trim()) {
    ElMessage.warning('请先粘贴代理')
    return
  }
  const r = proxyStore.append(draft.value)
  draft.value = ''
  ElMessage.success(`已追加 ${r.added} 个新代理`)
}

async function clearAll() {
  if (!count.value) return
  try {
    await ElMessageBox.confirm(`确定清空全部 ${count.value} 个代理？`, '确认', {
      type: 'warning',
      confirmButtonText: '确定',
      cancelButtonText: '取消',
    })
    proxyStore.clear()
    ElMessage.success('已清空')
  } catch (_) {}
}

function editInDraft() {
  draft.value = proxyStore.text
  ElMessage.info('已把当前代理池载入编辑框，改完点「覆盖保存」')
}
</script>

<template>
  <div class="proxypool-page">
    <div class="macos-window-panel">
      <!-- 窗口标题栏 -->
      <div class="macos-panel-header">
        <div class="header-left">
          <div class="window-dot-group">
            <span class="dot red"></span>
            <span class="dot yellow"></span>
            <span class="dot green"></span>
          </div>
          <span class="panel-title">全局代理池管理 · Proxy Cluster</span>
        </div>
        <div class="header-right">
          <span class="header-badge">共 {{ count }} 个代理节点</span>
          <span v-if="invalidCount" class="header-badge badge-err">{{ invalidCount }} 个异常</span>
        </div>
      </div>

      <!-- 主体双栏布局 -->
      <div class="macos-split-container">
        <!-- 左栏：代理快速录入/编辑器 -->
        <div class="form-pane">
          <div class="pane-inner">
            <div class="pane-section-title">
              <el-icon><DocumentAdd /></el-icon>批量录入代理
            </div>

            <div class="hint-card">
              <p class="hint-text">
                每行一条：<span class="mono">[协议://][user:pass@]host:port</span><br />
                支持 SOCKS5 / HTTP / HTTPS 格式。系统自动完成去重与规范化校验。
              </p>
            </div>

            <el-input
              v-model="draft"
              type="textarea"
              :rows="12"
              class="mono proxy-textarea"
              placeholder="socks5://127.0.0.1:7890&#10;socks5://user:pass@1.2.3.4:1080&#10;http://5.6.7.8:8080"
            />

            <div class="editor-action-bar">
              <el-button type="primary" class="macos-btn" @click="save">
                覆盖保存
              </el-button>
              <el-button class="macos-btn" @click="append">
                <el-icon><Plus /></el-icon>追加合并
              </el-button>
              <el-button class="macos-btn" @click="editInDraft">
                载入当前池
              </el-button>
            </div>
          </div>
        </div>

        <!-- 右栏：代理池列表与连通性测试表格 -->
        <div class="table-pane">
          <div class="table-toolbar">
            <div class="toolbar-info">
              <span class="toolbar-title">节点列表 ({{ count }})</span>
            </div>
            <div class="toolbar-actions">
              <el-button
                size="small"
                type="primary"
                plain
                :loading="testingAll"
                :disabled="!count"
                @click="testAll"
              >
                <el-icon><Connection /></el-icon>测试全部连通性
              </el-button>
              <el-button
                size="small"
                :disabled="!count"
                @click="copyText(proxyStore.text, '代理池已复制')"
              >
                <el-icon><CopyDocument /></el-icon>复制全部
              </el-button>
              <el-button
                size="small"
                type="danger"
                plain
                :disabled="!count"
                @click="clearAll"
              >
                <el-icon><Delete /></el-icon>清空
              </el-button>
            </div>
          </div>

          <div class="table-content-box">
            <el-table :data="rows" size="small" stripe height="100%" class="macos-table">
              <el-table-column prop="index" label="#" width="48" align="center" />
              <el-table-column prop="proxy" label="代理地址" min-width="210" show-overflow-tooltip>
                <template #default="{ row }">
                  <span class="mono" style="font-size: 11.5px">{{ row.proxy }}</span>
                </template>
              </el-table-column>
              <el-table-column label="协议" width="90" align="center">
                <template #default="{ row }">
                  <el-tag size="small" effect="plain" class="macos-tag">
                    {{ proxyScheme(row.proxy) }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="连通性 / 延迟" min-width="150">
                <template #default="{ row }">
                  <span v-if="!row.result" class="hint">未测试</span>
                  <el-tag v-else-if="row.result.status === 'testing'" type="warning" size="small">
                    测试中...
                  </el-tag>
                  <div v-else-if="row.result.status === 'ok'" class="latency-cell">
                    <el-tag type="success" size="small">
                      {{ row.result.latency_ms }}ms
                    </el-tag>
                    <span v-if="row.result.ip" class="mono ip-text">{{ row.result.ip }}</span>
                  </div>
                  <el-tooltip v-else :content="row.result.error || '连接失败'" placement="top">
                    <el-tag type="danger" size="small">连接失败</el-tag>
                  </el-tooltip>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="120" align="center" fixed="right">
                <template #default="{ row }">
                  <el-button
                    size="small"
                    text
                    type="primary"
                    :loading="row.result && row.result.status === 'testing'"
                    @click="testOne(row.proxy)"
                  >
                    测试
                  </el-button>
                  <el-button
                    size="small"
                    text
                    type="danger"
                    @click="proxyStore.remove(row.proxy)"
                  >
                    删除
                  </el-button>
                </template>
              </el-table-column>
              <template #empty>
                <el-empty description="代理池暂无节点，在左侧粘贴代理保存" :image-size="60" />
              </template>
            </el-table>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.proxypool-page {
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
.header-badge.badge-err {
  color: var(--apple-red);
  background: rgba(255, 59, 48, 0.1);
}

.macos-split-container {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 420px 1fr;
  overflow: hidden;
}

.form-pane {
  border-right: 1px solid var(--app-border);
  overflow-y: auto;
  background: var(--app-card-bg);
}
.pane-inner {
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.pane-section-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 700;
  color: var(--app-title);
}

.hint-card {
  background: var(--el-fill-color-light);
  border: 1px solid var(--app-border);
  border-radius: 8px;
  padding: 10px 12px;
}
.hint-text {
  margin: 0;
  font-size: 11px;
  color: var(--el-text-color-secondary);
  line-height: 1.5;
}

.proxy-textarea {
  font-size: 11.5px;
}

.editor-action-bar {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.table-pane {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--app-window-bg);
}

.table-toolbar {
  padding: 10px 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--app-border);
  background: var(--el-fill-color-blank);
  flex-shrink: 0;
}
.toolbar-title {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--app-title);
}
.toolbar-actions {
  display: flex;
  gap: 6px;
}

.table-content-box {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.latency-cell {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.ip-text {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

@media (max-width: 900px) {
  .macos-split-container {
    grid-template-columns: 1fr;
  }
}
</style>
