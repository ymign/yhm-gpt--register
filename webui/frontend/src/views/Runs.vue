<script setup>
import { onActivated, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { ElMessage } from 'element-plus'
import { Refresh, CopyDocument, Document } from '@element-plus/icons-vue'
import { listRuns } from '@/api/register'
import { fmtTime, copyText } from '@/api/request'
import { useRuntimeStore } from '@/stores/runtime'
import StatusDot from '@/components/StatusDot.vue'

const { dataVersion } = storeToRefs(useRuntimeStore())
const rows = ref([])
const loading = ref(false)

const STATUS_TYPE = { done: 'primary', failed: 'danger', running: 'warning' }

async function load() {
  loading.value = true
  try {
    const { items } = await listRuns(100)
    rows.value = items || []
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

watch(dataVersion, () => load())
onActivated(() => load())
</script>

<template>
  <div class="runs-page">
    <div class="macos-window-panel">
      <!-- 窗口标题栏 -->
      <div class="macos-panel-header">
        <div class="header-left">
          <div class="window-dot-group">
            <span class="dot red"></span>
            <span class="dot yellow"></span>
            <span class="dot green"></span>
          </div>
          <span class="panel-title">历史运行记录 · Execution History</span>
          <span class="badge-total">{{ rows.length }} 条记录</span>
        </div>
        <div class="header-right">
          <el-button class="macos-btn" @click="load">
            <el-icon><Refresh /></el-icon>刷新
          </el-button>
        </div>
      </div>

      <!-- 表格内容 -->
      <div class="macos-table-container">
        <el-skeleton v-if="loading && !rows.length" :rows="8" animated style="padding: 16px" />
        <el-table v-else v-loading="loading" :data="rows" size="small" stripe height="100%" class="macos-table">
          <el-table-column prop="run_id" label="Run ID" width="170" align="center">
            <template #default="{ row }">
              <span class="mono run-id-pill" @click="copyText(row.run_id)">{{ row.run_id }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="email" label="执行邮箱" min-width="210" show-overflow-tooltip>
            <template #default="{ row }">
              <button
                class="macos-tag-btn copy-btn"
                title="点击复制邮箱"
                @click="copyText(row.email)"
              >
                <span class="mono">{{ row.email }}</span>
                <el-icon class="copy-ico"><CopyDocument /></el-icon>
              </button>
            </template>
          </el-table-column>
          <el-table-column label="执行状态" width="110" align="center">
            <template #default="{ row }">
              <StatusDot :type="STATUS_TYPE[row.status] || 'info'" :text="row.status" />
            </template>
          </el-table-column>
          <el-table-column label="启动时间" width="165" align="center">
            <template #default="{ row }">
              <span class="mono-date">{{ fmtTime(row.started_at) }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="error" label="异常或拦截原因" min-width="220" show-overflow-tooltip>
            <template #default="{ row }">
              <span v-if="row.error" class="hint" style="color: var(--el-color-danger)">
                {{ row.error }}
              </span>
              <span v-else class="hint">—</span>
            </template>
          </el-table-column>
          <template #empty>
            <el-empty description="暂无历史运行记录" :image-size="60" />
          </template>
        </el-table>
      </div>
    </div>
  </div>
</template>

<style scoped>
.runs-page {
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
.badge-total {
  font-size: 11px;
  color: var(--app-text-secondary);
  background: var(--el-fill-color);
  padding: 1px 6px;
  border-radius: 10px;
}

.macos-btn {
  border-radius: 6px;
  font-size: 12px;
}

.macos-table-container {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.run-id-pill {
  font-size: 11px;
  padding: 2px 6px;
  background: var(--el-fill-color-light);
  border-radius: 4px;
  border: 1px solid var(--app-border);
  cursor: pointer;
}
.run-id-pill:hover {
  color: var(--el-color-primary);
  border-color: var(--el-color-primary-light-7);
}

.macos-tag-btn.copy-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--app-border);
  padding: 1px 6px;
  border-radius: 4px;
  color: var(--app-title);
  cursor: pointer;
  outline: none;
  font-size: 12px;
  transition: all 0.15s ease;
  max-width: 100%;
}
.macos-tag-btn.copy-btn:hover {
  background: var(--el-color-primary-light-9);
  border-color: var(--el-color-primary-light-7);
  color: var(--el-color-primary);
}
.copy-btn .copy-ico {
  font-size: 11px;
  opacity: 0.5;
}
.copy-btn:hover .copy-ico { opacity: 1; }

.mono-date {
  font-family: var(--el-font-family-monospace, monospace);
  font-size: 11px;
  color: var(--app-text-secondary);
}
</style>
