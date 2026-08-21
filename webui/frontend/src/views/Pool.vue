<script setup>
import { computed, onActivated, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { ElMessage, ElMessageBox, ElNotification, ElLoading } from 'element-plus'
import {
  Refresh,
  Delete,
  CopyDocument,
  VideoPlay,
  RefreshRight,
  Files,
  ArrowDown,
} from '@element-plus/icons-vue'
import {
  listAccounts,
  deleteAccount,
  bulkDeleteAccounts,
  resetFailed,
  resetAccount,
  bulkResetAccounts,
  releaseStale,
  cleanRegisteredFromPool,
} from '@/api/accounts'
import { getMailProviders } from '@/api/settings'
import { copyText } from '@/api/request'
import { useStatsStore } from '@/stores/stats'
import { useRuntimeStore } from '@/stores/runtime'
import StatusDot from '@/components/StatusDot.vue'

const router = useRouter()
const statsStore = useStatsStore()
const runtime = useRuntimeStore()
const { dataVersion } = storeToRefs(runtime)

const PAGE_SIZE = 20
const rows = ref([])
const total = ref(0)
const page = ref(1)
const statusFilter = ref('')
const kindFilter = ref('')
const bulkStatus = ref('')
const selected = ref([])
const loading = ref(false)
const providers = ref([])
const byKind = ref({})

const STATUS_TYPE = { available: 'success', in_use: 'warning', done: 'primary', failed: 'danger' }

const kindOptions = computed(() =>
  providers.value.filter((p) => p.pooled).map((p) => ({
    kind: p.kind,
    label: p.display_name,
    count: byKind.value[p.kind]?.total || 0,
  })),
)

function kindLabel(k) {
  return providers.value.find((p) => p.kind === k)?.display_name || k || 'outlook'
}

async function loadProviders() {
  try {
    providers.value = (await getMailProviders()).providers || []
  } catch (_) {}
}

async function load(resetPage) {
  if (resetPage) page.value = 1
  loading.value = true
  try {
    const { items, total: t, by_kind } = await listAccounts({
      status: statusFilter.value,
      kind: kindFilter.value,
      limit: PAGE_SIZE,
      offset: (page.value - 1) * PAGE_SIZE,
    })
    rows.value = items || []
    total.value = t || 0
    byKind.value = by_kind || {}
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

function afterMutate() {
  load()
  statsStore.refresh()
}

async function confirm(msg, title = '确认') {
  try {
    await ElMessageBox.confirm(msg, title, {
      type: 'warning',
      confirmButtonText: '确定',
      cancelButtonText: '取消',
    })
    return true
  } catch (_) {
    return false
  }
}

async function resetFailedAll() {
  if (!(await confirm('把所有 failed 状态的号重置为 available？'))) return
  try {
    const r = await resetFailed()
    ElMessage.success(`已重置 ${r.reset} 个账号`)
    afterMutate()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

async function releaseStaleAll() {
  try {
    const r = await releaseStale()
    ElMessage.success(`已释放 ${r.released} 个卡死账号`)
    afterMutate()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

const cleaningRegistered = ref(false)

async function cleanRegisteredAll() {
  const ok = await confirm(
    '系统将比对本地「已注册结果库」，自动从待注册号池中移除已成功注册的账号（并自动将 4 段邮箱取件凭证同步备份到已注册库，确保 2FA 与收信凭证不丢失）。\n\n确定开始全库对账清理？',
    '号池对账与清理已注册老号',
  )
  if (!ok) return

  cleaningRegistered.value = true
  const loadingInstance = ElLoading.service({
    lock: true,
    text: '正在比对号池与已注册库，执行深度对账清理...',
    background: 'rgba(0, 0, 0, 0.7)',
  })

  try {
    const r = await cleanRegisteredFromPool('delete')
    if (r.cleaned > 0) {
      ElNotification({
        title: '清理完成',
        message: `成功比对全库，已自动从待注册号池中移除 ${r.cleaned} 个已注册老号（邮箱凭证已同步备份）。`,
        type: 'success',
        duration: 5000,
      })
    } else {
      ElNotification({
        title: '号池非常纯净',
        message: '全库比对完成，当前待注册号池中没有与已注册库重叠的老号（重叠数：0）。',
        type: 'info',
        duration: 4000,
      })
    }
    afterMutate()
  } catch (e) {
    ElNotification({
      title: '清理失败',
      message: e.message || '请求失败，请检查后端服务是否正常',
      type: 'error',
      duration: 5000,
    })
  } finally {
    loadingInstance.close()
    cleaningRegistered.value = false
  }
}

async function resetSelected() {
  const emails = selected.value.map((r) => r.email)
  if (!emails.length) return
  if (!(await confirm(`确定重置选中的 ${emails.length} 个账号为 available 状态？`))) return
  try {
    const r = await bulkResetAccounts(emails)
    ElMessage.success(`已重置 ${r.reset} 个账号`)
    afterMutate()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

async function deleteSelected() {
  const emails = selected.value.map((r) => r.email)
  if (!emails.length) return
  if (!(await confirm(`确定删除选中的 ${emails.length} 个账号？(不可恢复)`))) return
  try {
    const r = await bulkDeleteAccounts({ emails })
    ElMessage.success(`已删除 ${r.deleted} 个账号`)
    afterMutate()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

async function bulkDeleteByStatus(status) {
  if (!status) return
  const tip = status === 'all'
    ? '这会清空号池里所有邮箱账号（含未注册的），确定？'
    : `确定删除号池中全部 ${status} 状态的账号？`
  if (!(await confirm(tip))) return
  try {
    const r = await bulkDeleteAccounts({ status })
    ElMessage.success(`已删除 ${r.deleted} 个 ${status} 账号`)
    afterMutate()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

function useAccount(email) {
  router.push({ path: '/register', query: { email } })
}

async function resetOne(email) {
  if (!(await confirm(`重置 ${email} 为 available？`))) return
  try {
    await resetAccount(email)
    ElMessage.success('已重置')
    afterMutate()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

async function deleteOne(email) {
  if (!(await confirm(`删除 ${email}？`))) return
  try {
    await deleteAccount(email)
    ElMessage.success('已删除')
    afterMutate()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

watch(page, () => load())
watch(dataVersion, () => load())
onActivated(() => load())
loadProviders()
</script>

<template>
  <div class="pool-page">
    <div class="macos-window-panel">
      <!-- 顶部 macOS 工具栏 -->
      <div class="macos-toolbar">
        <div class="toolbar-left">
          <div class="page-title-badge">
            <span class="dot-live"></span>
            <span class="title">邮箱号池</span>
            <span class="badge-total">{{ total }} 个</span>
          </div>

          <el-button class="macos-btn" @click="load(false)">
            <el-icon><Refresh /></el-icon>刷新
          </el-button>

          <el-select
            v-model="statusFilter"
            placeholder="状态筛选"
            class="macos-select filter-select"
            @change="load(true)"
          >
            <el-option label="全部状态" value="" />
            <el-option label="可用 (available)" value="available" />
            <el-option label="运行中 (in_use)" value="in_use" />
            <el-option label="已完成 (done)" value="done" />
            <el-option label="失败 (failed)" value="failed" />
          </el-select>

          <el-select
            v-if="kindOptions.length > 1"
            v-model="kindFilter"
            placeholder="全部来源"
            class="macos-select kind-select"
            @change="load(true)"
          >
            <el-option label="全部来源" value="" />
            <el-option
              v-for="o in kindOptions"
              :key="o.kind"
              :label="`${o.label} (${o.count})`"
              :value="o.kind"
            />
          </el-select>

          <div class="macos-btn-group">
            <el-button size="small" @click="resetFailedAll">重试 failed</el-button>
            <el-button size="small" @click="releaseStaleAll">释放卡死号</el-button>
            <el-button size="small" type="warning" plain :loading="cleaningRegistered" @click="cleanRegisteredAll">清理已注册老号</el-button>
          </div>
        </div>

        <div class="toolbar-right">
          <el-button
            type="primary"
            plain
            size="small"
            class="macos-btn"
            :disabled="!selected.length"
            @click="resetSelected"
          >
            重置选中 ({{ selected.length }})
          </el-button>

          <el-dropdown trigger="click" @command="bulkDeleteByStatus">
            <el-button size="small" type="danger" plain class="macos-btn">
              <el-icon><Delete /></el-icon>批量删除
              <el-icon class="el-icon--right"><ArrowDown /></el-icon>
            </el-button>
            <template #dropdown>
              <el-dropdown-menu class="macos-dropdown-menu">
                <el-dropdown-item command="failed">删除全部 failed 账号</el-dropdown-item>
                <el-dropdown-item command="done">删除全部 done 账号</el-dropdown-item>
                <el-dropdown-item command="available">删除全部 available 账号</el-dropdown-item>
                <el-dropdown-item command="in_use">删除全部 in_use 账号</el-dropdown-item>
                <el-dropdown-item divided command="all" style="color: var(--el-color-danger)">
                  ⚠️ 清空全部号池（不可逆）
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>

          <el-button
            v-if="selected.length"
            size="small"
            type="danger"
            @click="deleteSelected"
          >
            删除选中 ({{ selected.length }})
          </el-button>
        </div>
      </div>

      <!-- 中间主体表格区域 (100% 高度无滚动溢出) -->
      <div class="macos-table-container">
        <el-skeleton v-if="loading && !rows.length" :rows="8" animated style="padding: 16px" />
        <el-table
          v-else
          v-loading="loading"
          :data="rows"
          height="100%"
          size="small"
          stripe
          class="macos-table"
          @selection-change="(v) => (selected = v)"
        >
          <el-table-column type="selection" width="42" align="center" />

          <el-table-column prop="email" label="邮箱地址" min-width="220" show-overflow-tooltip>
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

          <el-table-column v-if="kindOptions.length > 1" label="邮箱来源" width="130" align="center">
            <template #default="{ row }">
              <el-tag size="small" type="info" effect="plain" class="macos-tag">
                {{ kindLabel(row.kind) }}
              </el-tag>
            </template>
          </el-table-column>

          <el-table-column label="状态" width="120" align="center">
            <template #default="{ row }">
              <StatusDot :type="STATUS_TYPE[row.status] || 'info'" :text="row.status" />
            </template>
          </el-table-column>

          <el-table-column prop="fail_reason" label="失败原因 / 备注" min-width="190" show-overflow-tooltip>
            <template #default="{ row }">
              <span v-if="row.fail_reason" class="hint" style="color: var(--el-color-danger)">
                {{ row.fail_reason }}
              </span>
              <span v-else class="hint">—</span>
            </template>
          </el-table-column>

          <el-table-column label="操作" width="180" fixed="right" align="center">
            <template #default="{ row }">
              <div class="row-actions">
                <el-button size="small" text type="primary" @click="useAccount(row.email)">
                  <el-icon><VideoPlay /></el-icon>使用
                </el-button>
                <el-button
                  v-if="row.status === 'done' || row.status === 'failed'"
                  size="small"
                  text
                  @click="resetOne(row.email)"
                >
                  重置
                </el-button>
                <el-button size="small" text type="danger" @click="deleteOne(row.email)">
                  删除
                </el-button>
              </div>
            </template>
          </el-table-column>

          <template #empty>
            <el-empty description="号池暂无数据，前往「导入邮箱」添加账号" :image-size="60" />
          </template>
        </el-table>
      </div>

      <!-- 底部固定 macOS 状态与分页栏 -->
      <div class="macos-footer-bar">
        <div class="footer-status-left">
          <span v-if="selected.length" class="selected-badge">已勾选 <b>{{ selected.length }}</b> 项</span>
          <span v-else class="total-badge">当前页共 {{ rows.length }} 条记录</span>
        </div>

        <div class="footer-pagination-right">
          <el-pagination
            v-model:current-page="page"
            :page-size="PAGE_SIZE"
            :total="total"
            layout="total, prev, pager, next, jumper"
            size="small"
            background
            class="macos-pagination"
          />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.pool-page {
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

.macos-toolbar {
  padding: 10px 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  background: var(--el-fill-color-blank);
  border-bottom: 1px solid var(--app-border);
  flex-shrink: 0;
}

.toolbar-left, .toolbar-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
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
  background: var(--el-color-primary);
}
.page-title-badge .title {
  font-size: 14px;
  font-weight: 600;
  color: var(--app-title);
}
.page-title-badge .badge-total {
  font-size: 11px;
  color: var(--app-text-secondary);
  background: var(--el-fill-color-light);
  padding: 1px 6px;
  border-radius: 10px;
}

.macos-btn {
  border-radius: 6px;
  font-size: 12px;
  padding: 6px 10px;
}

.macos-select.filter-select {
  width: 135px;
}
.macos-select.kind-select {
  width: 160px;
}

.macos-btn-group {
  display: inline-flex;
  background: var(--el-fill-color-light);
  padding: 2px;
  border-radius: 6px;
  border: 1px solid var(--app-border);
}
.macos-btn-group :deep(.el-button) {
  margin: 0;
  border: none;
  background: transparent;
  padding: 5px 10px;
  height: 26px;
  font-size: 12px;
  border-radius: 4px;
}
.macos-btn-group :deep(.el-button:hover:not(:disabled)) {
  background: var(--app-card-bg);
  box-shadow: var(--app-shadow-sm);
}

.macos-table-container {
  flex: 1;
  min-height: 0;
  overflow: hidden;
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

.row-actions {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
}

.macos-footer-bar {
  padding: 8px 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-top: 1px solid var(--app-border);
  background: var(--el-fill-color-blank);
  flex-shrink: 0;
}
.footer-status-left {
  font-size: 12px;
  color: var(--app-text-secondary);
}
.selected-badge {
  color: var(--el-color-primary);
  font-weight: 500;
}
</style>
