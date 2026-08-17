<script setup>
import { computed, onActivated, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Refresh,
  Download,
  CopyDocument,
  VideoPlay,
  CircleCheck,
  Warning,
  Link,
  ArrowDown,
  Connection,
  Document,
  Search,
  Setting,
  Grid,
} from '@element-plus/icons-vue'
import { listRegistered, listRegisteredEmails } from '@/api/register'
import { copyText, fmtTime } from '@/api/request'
import ExtractTaskModal from '@/components/ExtractTaskModal.vue'

// ── 渠道卡片元数据 ──
const CHANNELS = [
  { key: 'paypal', name: 'PayPal', flag: '💳', exit: 'BR 巴西', billing: 'DE 德国 / EUR', desc: 'PayPal 授权与支付提链', hot: true, tag: 'DE/EUR' },
  { key: 'pix', name: 'PIX 出码', flag: '🇧🇷', exit: 'BR 巴西', billing: 'BR 巴西 / BRL', desc: '巴西 PIX 二维码与银行跳转', hot: true, tag: 'BR/BRL' },
  { key: 'gcash', name: 'GCash', flag: '🇵🇭', exit: 'US 美国', billing: 'PH 菲律宾 / PHP', desc: '菲律宾 GCash 短链接直提', tag: 'PH/PHP' },
  { key: 'ideal', name: 'iDEAL', flag: '🇳🇱', exit: 'NL 荷兰', billing: 'NL 荷兰 / EUR', desc: '荷兰及欧洲银行跳转扫码', tag: 'NL/EUR' },
  { key: 'upi', name: 'UPI 扫码', flag: '🇮🇳', exit: 'IN 印度', billing: 'IN 印度 / INR', desc: '印度 UPI 扫码指令链接', tag: 'IN/INR' },
  { key: 'kakao', name: 'Kakao', flag: '🇰🇷', exit: 'KR 韩国', billing: 'KR 韩国 / KRW', desc: '韩国 KakaoPay 支付提链', tag: 'KR/KRW' },
  { key: 'momo', name: 'MoMo', flag: '🇻🇳', exit: 'VN 越南', billing: 'VN 越南 / VND', desc: '越南 MoMo 电子钱包提链', tag: 'VN/VND' },
  { key: 'twint', name: 'TWINT', flag: '🇨🇭', exit: 'CH 瑞士', billing: 'CH 瑞士 / CHF', desc: '瑞士 TWINT 扫码支付提链', tag: 'CH/CHF' },
  { key: 'blik', name: 'BLIK', flag: '🇵🇱', exit: 'PL 波兰', billing: 'PL 波兰 / PLN', desc: '波兰 BLIK 6位码与跳转', tag: 'PL/PLN' },
  { key: 'hosted', name: 'Hosted', flag: '⚡', exit: 'US 美国', billing: 'US 美国 / USD', desc: '标准 0 元 Stripe 托管支付页', tag: 'US/USD' },
]

// ── 账号表格与分页 ──
const rows = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const filter = ref('all')
const selected = ref([])
const loading = ref(false)

// ── 模态任务台 ──
const taskModalVisible = ref(false)
const taskModalChannel = ref('paypal')
const taskModalEmails = ref([])

// 是否展示顶层紧凑渠道横条
const showChannelBar = ref(true)

async function openChannelTask(channelKey) {
  taskModalChannel.value = channelKey
  if (selected.value.length > 0) {
    taskModalEmails.value = selected.value.map((r) => r.email)
    taskModalVisible.value = true
  } else {
    try {
      await ElMessageBox.confirm(
        `当前未手动勾选账号，是否针对当前【${getFilterLabel(filter.value)}】下的所有账号启动【${channelKey.toUpperCase()}】提炼任务台？`,
        '一键全量提炼任务',
        { confirmButtonText: '确定打开', cancelButtonText: '取消', type: 'info' }
      )
      const res = await listRegisteredEmails(filter.value)
      taskModalEmails.value = res.emails || []
      taskModalVisible.value = true
    } catch (_) {}
  }
}

// ── 加载表格 ──
async function loadData() {
  loading.value = true
  try {
    const res = await listRegistered({
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
      filter: filter.value,
    })
    rows.value = (res.items || []).map((row) => {
      let ext = row.extract_link || null
      if (!ext && row.extra_json) {
        try {
          const ex = typeof row.extra_json === 'string' ? JSON.parse(row.extra_json) : row.extra_json
          ext = ex.extract_link || null
        } catch (_) {}
      }
      return {
        ...row,
        extract_link: ext,
      }
    })
    total.value = res.total || 0
  } catch (e) {
    ElMessage.error(e.message || '加载列表失败')
  } finally {
    loading.value = false
  }
}

function handleSelectionChange(val) {
  selected.value = val
}

function getExtractStatus(row) {
  if (row.extract_link) {
    if (row.extract_link.status === 'success') {
      return { type: 'success', label: '0元生效' }
    }
    if (row.extract_link.status === 'failed') return { type: 'danger', label: '提链失败' }
  }
  return { type: 'info', label: '未提链' }
}

function getExtractUrl(row) {
  if (row.extract_link && row.extract_link.link_url) return row.extract_link.link_url
  return ''
}

function getFilterLabel(f) {
  const map = {
    all: '全部账号',
    extract_eligible: 'Plus试用',
    extract_success: '提链成功',
    extract_failed: '提链失败',
    has_rt: '包含 RT 账号',
  }
  return map[f] || f
}

function handleCopyAllCurrentLinks() {
  const list = []
  for (const row of rows.value) {
    const u = getExtractUrl(row)
    if (u) list.push(`${row.email}----${u}`)
  }
  if (!list.length) {
    ElMessage.warning('当前列表暂无可复制的提链 URL')
    return
  }
  copyText(list.join('\n'), `已复制 ${list.length} 条账号提链结果`)
}

onMounted(() => {
  loadData()
})

onActivated(() => {
  loadData()
})
</script>

<template>
  <div class="extract-page">
    <div class="macos-window-panel">
      <!-- 顶部超紧凑 Bento 风格全渠道胶囊滑轨 (高仅 44px) -->
      <div v-show="showChannelBar" class="channel-pill-rail">
        <div class="rail-title">
          <span class="rail-badge">提炼渠道</span>
        </div>
        <div class="pill-scroll-wrap">
          <div
            v-for="ch in CHANNELS"
            :key="ch.key"
            class="channel-pill-btn"
            :class="{ 'hot-pill': ch.hot }"
            @click="openChannelTask(ch.key)"
          >
            <span class="ch-flag">{{ ch.flag }}</span>
            <span class="ch-name">{{ ch.name }}</span>
            <span class="ch-tag">{{ ch.tag }}</span>
            <span v-if="ch.hot" class="ch-hot-dot"></span>
          </div>
        </div>
      </div>

      <!-- macOS 风格主工具栏 (高度自适应紧凑无缝) -->
      <div class="macos-toolbar">
        <div class="toolbar-left">
          <div class="page-title-badge">
            <span class="dot-live"></span>
            <span class="title">Plus 提炼中心</span>
            <span class="badge-total">{{ total }} 账号</span>
          </div>

          <el-button size="small" class="macos-btn" :loading="loading" @click="loadData">
            <el-icon><Refresh /></el-icon>刷新
          </el-button>

          <el-radio-group v-model="filter" size="small" class="filter-radio-group" @change="() => { page = 1; loadData() }">
            <el-radio-button label="all">全部</el-radio-button>
            <el-radio-button label="extract_eligible">🎁 Plus试用</el-radio-button>
            <el-radio-button label="extract_success">✅ 提链成功</el-radio-button>
            <el-radio-button label="extract_failed">❌ 失败</el-radio-button>
          </el-radio-group>
        </div>

        <div class="toolbar-right">
          <!-- ⚗️ 全渠道提炼与资格检测下拉菜单 -->
          <el-dropdown trigger="click" @command="openChannelTask">
            <el-button type="primary" size="small" class="extract-action-btn">
              <el-icon><Link /></el-icon>⚗️ 提炼任务台 ({{ selected.length }})
              <el-icon class="el-icon--right"><ArrowDown /></el-icon>
            </el-button>
            <template #dropdown>
              <el-dropdown-menu class="extract-menu-dropdown">
                <div class="dropdown-group-header">资格检测</div>
                <el-dropdown-item command="gcash_check">批量 GCash 检测</el-dropdown-item>
                <el-dropdown-item command="oaics_check">批量 OAICS 资格检测</el-dropdown-item>
                <el-dropdown-item command="plus_check">批量 Plus 状态检测</el-dropdown-item>

                <div class="dropdown-group-header divider">提链 / 出码</div>
                <el-dropdown-item command="paypal">批量 PayPal 提链 (DE/EU)</el-dropdown-item>
                <el-dropdown-item command="pix">批量 PIX 出码 (BR)</el-dropdown-item>
                <el-dropdown-item command="gcash">批量 GCash 提链 (PH)</el-dropdown-item>
                <el-dropdown-item command="ideal">批量 iDEAL 提链 (NL)</el-dropdown-item>
                <el-dropdown-item command="upi">批量 UPI 扫码 (IN)</el-dropdown-item>
                <el-dropdown-item command="kakao">批量 Kakao 提链 (KR)</el-dropdown-item>
                <el-dropdown-item command="momo">批量 MoMo 提链 (VN)</el-dropdown-item>
                <el-dropdown-item command="twint">批量 TWINT 提链 (CH)</el-dropdown-item>
                <el-dropdown-item command="blik">批量 BLIK 提链 (PL)</el-dropdown-item>
                <el-dropdown-item command="hosted">批量 Hosted / Stripe 提链</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>

          <el-button size="small" class="macos-btn" :icon="CopyDocument" @click="handleCopyAllCurrentLinks">
            复制当前页链接
          </el-button>
        </div>
      </div>

      <!-- 主体表格：100% 高度自适应伸缩，绝无外部整页滚动条 -->
      <div class="macos-table-container">
        <el-table
          v-loading="loading"
          :data="rows"
          height="100%"
          row-key="email"
          size="small"
          stripe
          class="extract-main-table"
          @selection-change="handleSelectionChange"
        >
          <el-table-column type="selection" width="42" align="center" />

          <el-table-column label="账号邮箱" min-width="210" show-overflow-tooltip>
            <template #default="{ row }">
              <span class="mono email-text" @click="copyText(row.email, '已复制邮箱')">{{ row.email }}</span>
            </template>
          </el-table-column>

          <el-table-column label="资格状态" width="120" align="center">
            <template #default="{ row }">
              <el-tag
                v-if="row.plus_check"
                size="small"
                :type="row.plus_check.status === 'plus_eligible' ? 'success' : row.plus_check.status === 'free' ? 'info' : 'danger'"
                effect="light"
              >
                {{ row.plus_check.label || row.plus_check.status }}
              </el-tag>
              <el-tag
                v-else-if="row.oa_check"
                size="small"
                :type="row.oa_check.state === 'OAICS' ? 'success' : 'warning'"
                effect="light"
              >
                {{ row.oa_check.state }}
              </el-tag>
              <span v-else class="text-muted text-xs">未检测</span>
            </template>
          </el-table-column>

          <el-table-column label="提炼渠道" width="100" align="center">
            <template #default="{ row }">
              <el-tag v-if="row.extract_link?.channel || row.extract_link?.link_type" size="small" effect="plain" type="primary">
                {{ (row.extract_link?.channel || row.extract_link?.link_type).toUpperCase() }}
              </el-tag>
              <span v-else class="text-muted text-xs">-</span>
            </template>
          </el-table-column>

          <el-table-column label="提炼状态" width="100" align="center">
            <template #default="{ row }">
              <el-tag size="small" :type="getExtractStatus(row).type" effect="light">
                {{ getExtractStatus(row).label }}
              </el-tag>
            </template>
          </el-table-column>

          <el-table-column label="已生成提取链接 / 支付说明" min-width="340">
            <template #default="{ row }">
              <div v-if="getExtractUrl(row)" class="url-cell">
                <el-link
                  :href="getExtractUrl(row)"
                  target="_blank"
                  type="primary"
                  class="mono url-link"
                  :underline="false"
                >
                  {{ getExtractUrl(row) }}
                </el-link>
                <el-button
                  size="small"
                  link
                  :icon="CopyDocument"
                  @click="copyText(getExtractUrl(row), '提链 URL 已复制')"
                />
              </div>
              <span v-else-if="row.extract_link?.error" class="text-danger text-xs mono">
                {{ row.extract_link.error }}
              </span>
              <span v-else class="text-muted text-xs">-</span>
            </template>
          </el-table-column>

          <el-table-column label="操作" width="100" align="center" fixed="right">
            <template #default="{ row }">
              <el-button
                size="small"
                link
                type="primary"
                @click="openChannelTask('paypal')"
              >
                发起提炼
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <!-- 分页底栏 -->
      <div class="macos-pagination-bar">
        <span class="total-text">共 {{ total }} 条账号记录 · 当前已选 {{ selected.length }} 项</span>
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[20, 50, 100, 200]"
          layout="sizes, prev, pager, next, jumper"
          size="small"
          @current-change="loadData"
          @size-change="() => { page = 1; loadData() }"
        />
      </div>
    </div>

    <!-- 模态提炼任务台 -->
    <ExtractTaskModal
      v-model="taskModalVisible"
      :channel="taskModalChannel"
      :emails="taskModalEmails"
      @finished="loadData"
    />
  </div>
</template>

<style scoped>
/* ──────────── 页面整体布局：100% 高度 + 绝无外层滚动条 ──────────── */
.extract-page {
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

/* ──────────── 顶部超紧凑 Bento 风格全渠道胶囊滑轨 ──────────── */
.channel-pill-rail {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  background: var(--el-fill-color-light);
  border-bottom: 1px solid var(--el-border-color-lighter);
  flex-shrink: 0;
}

.rail-title {
  display: flex;
  align-items: center;
  flex-shrink: 0;
}

.rail-badge {
  font-size: 11px;
  font-weight: 700;
  color: var(--el-text-color-secondary);
  background: var(--el-fill-color-dark);
  padding: 2px 6px;
  border-radius: 4px;
}

.pill-scroll-wrap {
  display: flex;
  align-items: center;
  gap: 6px;
  overflow-x: auto;
  scrollbar-width: none;
  flex: 1;
}
.pill-scroll-wrap::-webkit-scrollbar {
  display: none;
}

.channel-pill-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  border-radius: 6px;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-light);
  font-size: 11px;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.15s ease;
  position: relative;
}

.channel-pill-btn:hover {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  transform: translateY(-1px);
}

.ch-flag {
  font-size: 12px;
}

.ch-name {
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.ch-tag {
  font-size: 10px;
  font-family: ui-monospace, monospace;
  color: var(--el-text-color-secondary);
  background: var(--el-fill-color);
  padding: 0 4px;
  border-radius: 3px;
}

.hot-pill {
  border-color: rgba(239, 68, 68, 0.3);
}

.ch-hot-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #ef4444;
  margin-left: 1px;
}

/* ──────────── macOS 风格主工具栏 ──────────── */
.macos-toolbar {
  padding: 8px 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
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

.macos-btn {
  border-radius: 6px;
  font-size: 12px;
  padding: 4px 8px;
}

.extract-action-btn {
  border-radius: 6px;
  font-size: 12px;
  font-weight: 600;
  background: linear-gradient(135deg, #10b981, #059669);
  border: none;
  box-shadow: 0 2px 6px rgba(16, 185, 129, 0.25);
}
.extract-action-btn:hover:not(:disabled) {
  background: linear-gradient(135deg, #059669, #047857);
}

.extract-menu-dropdown {
  min-width: 180px;
}

.dropdown-group-header {
  padding: 4px 12px;
  font-size: 11px;
  font-weight: 700;
  color: var(--el-text-color-secondary);
}

.dropdown-group-header.divider {
  margin-top: 6px;
  border-top: 1px solid var(--el-border-color-lighter);
  padding-top: 6px;
}

/* ──────────── 中间表格区域 ──────────── */
.macos-table-container {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.extract-main-table {
  width: 100%;
  flex: 1;
}

.email-text {
  cursor: pointer;
}
.email-text:hover {
  color: var(--el-color-primary);
  text-decoration: underline;
}

.url-cell {
  display: flex;
  align-items: center;
  gap: 6px;
}

.url-link {
  font-size: 12px;
  max-width: 380px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ──────────── 底部紧凑分页栏 ──────────── */
.macos-pagination-bar {
  padding: 6px 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--el-fill-color-blank);
  border-top: 1px solid var(--el-border-color-lighter);
  flex-shrink: 0;
}

.total-text {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.text-xs {
  font-size: 11px;
}

.text-muted {
  color: var(--el-text-color-placeholder);
}

.text-danger {
  color: var(--el-color-danger);
}
</style>
