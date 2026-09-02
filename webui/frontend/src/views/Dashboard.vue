<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useRouter } from 'vue-router'
import {
  Files,
  CircleCheck,
  Loading,
  Select,
  CircleClose,
  Upload,
  VideoPlay,
  Setting,
  Compass,
  Link,
  CreditCard,
  Key,
  Refresh,
  Lock,
  Phone,
  Timer,
  DataAnalysis,
  Opportunity,
  CircleCheckFilled,
  Histogram,
  Connection,
  Download,
} from '@element-plus/icons-vue'
import { useStatsStore } from '@/stores/stats'
import { useRuntimeStore } from '@/stores/runtime'
import { getDashboardSummary } from '@/api/accounts'
import { getSentinelPoolStats, getProxyHealthSummary } from '@/api/register'
import { COUNTRY_NAME_MAP, formatCountry } from '@/stores/form'
import { fmtTime } from '@/api/request'
import StatusDot from '@/components/StatusDot.vue'

const router = useRouter()
const { stats } = storeToRefs(useStatsStore())
const { autoStatus } = storeToRefs(useRuntimeStore())

const summaryLoading = ref(false)
const summaryData = ref({
  pool: { available: 0, in_use: 0, done: 0, failed: 0, archived: 0, total: 0 },
  registered: {
    total: 0,
    with_2fa: 0,
    with_pwd: 0,
    with_oauth: 0,
    exported: 0,
    unexported: 0,
    sec_rate: 0,
    pwd_rate: 0,
    success_rate: 0,
  },
  countries: [],
  recent: [],
  remail_active_cached: 0,
})

const sentinelStats = ref({
  enabled: true,
  target_size: 10,
  current_size: 0,
  precomputed_total: 0,
  popped_total: 0,
  hit_rate: 0,
})

const proxyHealthStats = ref({
  total_tracked: 0,
  healthy_count: 0,
  cooling_down_count: 0,
  recent_frozen: [],
})

let timer = null

async function loadDashboardSummary() {
  summaryLoading.value = true
  try {
    const [dashRes, sentinelRes, proxyRes] = await Promise.allSettled([
      getDashboardSummary(),
      getSentinelPoolStats(),
      getProxyHealthSummary(),
    ])
    if (dashRes.status === 'fulfilled' && dashRes.value && dashRes.value.ok) {
      const res = dashRes.value
      summaryData.value = {
        pool: res.pool || summaryData.value.pool,
        registered: res.registered || summaryData.value.registered,
        countries: res.countries || [],
        recent: res.recent || [],
        remail_active_cached: res.remail_active_cached || 0,
      }
    }
    if (sentinelRes.status === 'fulfilled' && sentinelRes.value && sentinelRes.value.ok) {
      sentinelStats.value = { ...sentinelStats.value, ...sentinelRes.value }
    }
    if (proxyRes.status === 'fulfilled' && proxyRes.value && proxyRes.value.ok) {
      proxyHealthStats.value = { ...proxyHealthStats.value, ...proxyRes.value }
    }
  } catch (e) {
    console.error('loadDashboardSummary error:', e)
  } finally {
    summaryLoading.value = false
  }
}

onMounted(() => {
  loadDashboardSummary()
  timer = setInterval(loadDashboardSummary, 8000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})

// 自动跑号状态映射
const autoStateLabel = computed(() => ({
  stopped: '空闲待机 (Idle)',
  running: '正在并发跑号 (Running)',
  paused: '已暂停 (Paused)',
}[autoStatus.value.state] || autoStatus.value.state))

const autoStateType = computed(() => ({
  stopped: 'info',
  running: 'success',
  paused: 'warning',
}[autoStatus.value.state] || 'info'))

// 快捷操作入口矩阵
const quickActions = [
  { title: '全自动并发批量', desc: '多 Worker 无人值守智能跑号', icon: Compass, path: '/auto', color: '#007aff', badge: '核心推荐' },
  { title: '账号资产中枢', desc: 'Token / 2FA / 密码 / 导出', icon: Key, path: '/registered', color: '#10b981', badge: '资产管理' },
  { title: '单次注册调试', desc: '即时测通与凭证生成', icon: VideoPlay, path: '/register', color: '#8b5cf6' },
  { title: '批量导入邮箱', desc: '支持 4 段式及中转号池导入', icon: Upload, path: '/import', color: '#06b6d4' },
  { title: '全渠道提链出码', desc: 'PayPal / PIX / GCash 代付', icon: Link, path: '/extract', color: '#f59e0b' },
  { title: '邮箱渠道配置', desc: 'Remail / 微软OAuth / CF Worker', icon: Setting, path: '/mail-config', color: '#ec4899' },
]

// 计算国家占比
function getCountryPercent(cnt) {
  const totalReg = summaryData.value.registered.total || 1
  return Math.min(100, Math.round((cnt / totalReg) * 100))
}
</script>

<template>
  <div class="dashboard-page">
    <div class="macos-window-panel">
      <!-- 顶部 macOS 窗口控制与状态条 -->
      <div class="macos-panel-header">
        <div class="header-left">
          <div class="window-dot-group">
            <span class="dot red"></span>
            <span class="dot yellow"></span>
            <span class="dot green"></span>
          </div>
          <div class="header-title-box">
            <span class="panel-title">系统智能运行中枢 · Dashboard 2.0</span>
            <span class="panel-sub-title">实时资产矩阵 · 核心指标与全球出口拓扑</span>
          </div>
        </div>
        <div class="header-right">
          <el-button
            size="small"
            class="header-refresh-btn"
            :loading="summaryLoading"
            @click="loadDashboardSummary"
          >
            <el-icon><Refresh /></el-icon> 刷新概览
          </el-button>
          <span class="header-badge pulse-ring-badge">
            <span class="live-dot"></span>
            <span>SYSTEM ACTIVE</span>
          </span>
        </div>
      </div>

      <div class="dashboard-scrollable-content">
        <!-- ════════════════ 1. 核心四大 Bento KPI 矩阵大屏 ════════════════ -->
        <div class="bento-kpi-grid">
          <!-- Card 1: 号池存量 -->
          <div class="bento-card card-pool" @click="router.push('/pool')">
            <div class="bento-header">
              <div class="bento-title-group">
                <span class="bento-icon-badge bg-blue"><el-icon><Files /></el-icon></span>
                <span class="bento-label">号池库存总览</span>
              </div>
              <span class="bento-sub">TOTAL POOL</span>
            </div>
            <div class="bento-body">
              <div class="bento-main-number text-blue">{{ stats.total || summaryData.pool.total || 0 }}</div>
              <div class="bento-meta-row">
                <span class="meta-item text-success">🟢 可用 {{ stats.available || summaryData.pool.available || 0 }}</span>
                <span class="meta-item text-warning">🟡 运行中 {{ stats.in_use || summaryData.pool.in_use || 0 }}</span>
                <span class="meta-item text-secondary">📦 归档 {{ summaryData.pool.archived || 0 }}</span>
              </div>
            </div>
            <div class="bento-footer-bar">
              <div class="progress-track">
                <div
                  class="progress-fill fill-success"
                  :style="{ width: `${Math.min(100, Math.round(((stats.available || 0) / (stats.total || 1)) * 100))}%` }"
                ></div>
              </div>
            </div>
          </div>

          <!-- Card 2: 注册资产 -->
          <div class="bento-card card-asset" @click="router.push('/registered')">
            <div class="bento-header">
              <div class="bento-title-group">
                <span class="bento-icon-badge bg-emerald"><el-icon><Key /></el-icon></span>
                <span class="bento-label">GPT 注册资产</span>
              </div>
              <span class="bento-sub">REGISTERED ASSETS</span>
            </div>
            <div class="bento-body">
              <div class="bento-main-number text-emerald">{{ summaryData.registered.total }}</div>
              <div class="bento-meta-row">
                <span class="meta-item text-emerald">🛡️ 2FA保护 {{ summaryData.registered.sec_rate }}%</span>
                <span class="meta-item text-blue">🔑 已设密 {{ summaryData.registered.with_pwd }}</span>
                <span class="meta-item text-warning">📦 已导出 {{ summaryData.registered.exported }}</span>
              </div>
            </div>
            <div class="bento-footer-bar">
              <div class="progress-track">
                <div
                  class="progress-fill fill-emerald"
                  :style="{ width: `${summaryData.registered.sec_rate}%` }"
                ></div>
              </div>
            </div>
          </div>

          <!-- Card 3: 自动化并发与成功率 -->
          <div class="bento-card card-loop" @click="router.push('/auto')">
            <div class="bento-header">
              <div class="bento-title-group">
                <span class="bento-icon-badge bg-purple"><el-icon><Compass /></el-icon></span>
                <span class="bento-label">全自动注册引擎</span>
              </div>
              <span class="bento-sub">AUTO WORKER</span>
            </div>
            <div class="bento-body">
              <div class="bento-main-number text-purple">
                {{ autoStatus.concurrency || 1 }} <span class="unit-text">Worker</span>
              </div>
              <div class="bento-meta-row">
                <span class="meta-item text-success">🎉 成功 {{ autoStatus.registered_ok || summaryData.pool.done || 0 }}</span>
                <span class="meta-item text-danger">❌ 失败 {{ autoStatus.registered_fail || summaryData.pool.failed || 0 }}</span>
                <span class="meta-item text-purple">⚡ 周期 {{ autoStatus.total_cycles || 0 }}</span>
              </div>
            </div>
            <div class="bento-footer-bar">
              <div class="progress-track">
                <div
                  class="progress-fill fill-purple"
                  :style="{ width: `${summaryData.registered.success_rate}%` }"
                ></div>
              </div>
            </div>
          </div>

          <!-- Card 4: Remail & 代理中心 -->
          <div class="bento-card card-remail" @click="router.push('/mail-config')">
            <div class="bento-header">
              <div class="bento-title-group">
                <span class="bento-icon-badge bg-amber"><el-icon><Connection /></el-icon></span>
                <span class="bento-label">Remail & 代理枢纽</span>
              </div>
              <span class="bento-sub">RECYCLE & PROXY</span>
            </div>
            <div class="bento-body">
              <div class="bento-main-number text-amber">
                {{ summaryData.remail_active_cached }} <span class="unit-text">暂存复用</span>
              </div>
              <div class="bento-meta-row">
                <span class="meta-item text-success">♻️ 智能免花积分</span>
                <span class="meta-item text-blue">🌐 住宅代理路由</span>
                <span class="meta-item text-amber">⚡ 0秒取用</span>
              </div>
            </div>
            <div class="bento-footer-bar">
              <div class="progress-track">
                <div class="progress-fill fill-amber" style="width: 100%"></div>
              </div>
            </div>
          </div>
        </div>

        <!-- ════════════════ 1.5 核心引擎与防风控状态 HUD (PoW预计算池 & 代理智能冷冻) ════════════════ -->
        <div class="engine-hud-grid">
          <!-- PoW 预计算池状态 -->
          <div class="engine-hud-card">
            <div class="engine-hud-header">
              <div class="engine-hud-title-wrap">
                <span class="engine-hud-dot live-dot"></span>
                <span class="engine-hud-title">⚡ Sentinel PoW 预计算池 (0ms 瞬时取用)</span>
              </div>
              <el-tag size="small" :type="sentinelStats.enabled ? 'success' : 'info'" effect="dark">
                {{ sentinelStats.enabled ? '预计算已开启' : '已停用' }}
              </el-tag>
            </div>
            <div class="engine-hud-body">
              <div class="engine-hud-kpi">
                <div class="hud-kpi-item">
                  <span class="hud-kpi-label">当前就绪余量</span>
                  <span class="hud-kpi-val text-emerald mono">{{ sentinelStats.current_size }} / {{ sentinelStats.target_size }}</span>
                </div>
                <div class="hud-kpi-item">
                  <span class="hud-kpi-label">累计预算 Token</span>
                  <span class="hud-kpi-val text-blue mono">{{ sentinelStats.precomputed_total }}</span>
                </div>
                <div class="hud-kpi-item">
                  <span class="hud-kpi-label">注册 0ms 命中率</span>
                  <span class="hud-kpi-val text-purple mono">{{ Math.round((sentinelStats.hit_rate || 0) * 100) }}%</span>
                </div>
              </div>
              <div class="engine-hud-desc">
                后台守护协程预计算 Sentinel Proof-of-Work，注册流水线免去 15s 碰撞等待，降低 CPU 瞬时波峰。
              </div>
            </div>
          </div>

          <!-- 代理出口智能评级与冷冻状态 -->
          <div class="engine-hud-card">
            <div class="engine-hud-header">
              <div class="engine-hud-title-wrap">
                <span class="engine-hud-dot" :class="proxyHealthStats.cooling_down_count > 0 ? 'dot-warning' : 'live-dot'"></span>
                <span class="engine-hud-title">🛡️ 代理出口 IP 智能评级与 15min 自动冷冻</span>
              </div>
              <el-tag size="small" :type="proxyHealthStats.cooling_down_count > 0 ? 'warning' : 'success'" effect="plain">
                {{ proxyHealthStats.cooling_down_count }} 个冷冻隔离中
              </el-tag>
            </div>
            <div class="engine-hud-body">
              <div class="engine-hud-kpi">
                <div class="hud-kpi-item">
                  <span class="hud-kpi-label">受监控代理</span>
                  <span class="hud-kpi-val text-blue mono">{{ proxyHealthStats.total_tracked }}</span>
                </div>
                <div class="hud-kpi-item">
                  <span class="hud-kpi-label">健康可用</span>
                  <span class="hud-kpi-val text-emerald mono">{{ proxyHealthStats.healthy_count }}</span>
                </div>
                <div class="hud-kpi-item">
                  <span class="hud-kpi-label">冷冻隔离 (409/风控)</span>
                  <span class="hud-kpi-val text-amber mono">{{ proxyHealthStats.cooling_down_count }}</span>
                </div>
              </div>
              <div class="engine-hud-desc">
                连续 409 / 预热失败的代理 IP 自动进入 15 分钟冷冻期，避开 OpenAI 出口频控，保护积分与号源。
              </div>
            </div>
          </div>
        </div>

        <!-- ════════════════ 2. 中部核心分析分屏 (双卡片) ════════════════ -->
        <div class="bento-analysis-grid">
          <!-- 左侧: 全球出口国家注册拓扑 Matrix -->
          <div class="analysis-card">
            <div class="card-title-bar">
              <div class="title-left">
                <span class="card-icon"><Histogram /></span>
                <span class="card-title">全球出口国家注册分布 · Geo Distribution</span>
              </div>
              <el-tag size="small" type="primary" effect="plain" class="geo-tag">
                TOP {{ summaryData.countries.length }} 热门国家
              </el-tag>
            </div>

            <div v-if="summaryData.countries.length" class="geo-country-list">
              <div
                v-for="(geo, gIdx) in summaryData.countries"
                :key="geo.country"
                class="geo-country-item"
              >
                <div class="geo-item-top">
                  <div class="geo-name-wrap">
                    <span class="geo-rank-num">#{{ gIdx + 1 }}</span>
                    <span class="geo-flag-name">{{ formatCountry(geo.country) }}</span>
                    <span class="geo-code-chip mono">[{{ geo.country }}]</span>
                  </div>
                  <div class="geo-count-wrap">
                    <span class="geo-count mono"><strong>{{ geo.count }}</strong> 个</span>
                    <span class="geo-percent mono">{{ getCountryPercent(geo.count) }}%</span>
                  </div>
                </div>
                <div class="geo-bar-track">
                  <div
                    class="geo-bar-fill"
                    :style="{ width: `${getCountryPercent(geo.count)}%` }"
                  ></div>
                </div>
              </div>
            </div>

            <div v-else class="geo-empty-wrap">
              <el-empty description="暂无国家分布记录，开始跑号后将自动汇总" :image-size="50" />
            </div>
          </div>

          <!-- 右侧: 全自动跑号智能监控与快速控制 -->
          <div class="analysis-card">
            <div class="card-title-bar">
              <div class="title-left">
                <span class="card-icon"><Timer /></span>
                <span class="card-title">全自动批量调度控制台 · Live Monitor</span>
              </div>
              <el-tag :type="autoStateType" size="small" effect="dark" class="state-tag">
                <StatusDot :type="autoStateType" :text="autoStateLabel" />
              </el-tag>
            </div>

            <div class="auto-control-body">
              <div class="auto-kpi-subgrid">
                <div class="auto-kpi-cell">
                  <span class="kpi-cell-label">并发 Worker</span>
                  <span class="kpi-cell-val mono text-primary">{{ autoStatus.concurrency || 1 }}</span>
                </div>
                <div class="auto-kpi-cell">
                  <span class="kpi-cell-label">成功出号 (OK)</span>
                  <span class="kpi-cell-val mono text-success">{{ autoStatus.registered_ok || 0 }}</span>
                </div>
                <div class="auto-kpi-cell">
                  <span class="kpi-cell-label">失败重试 (Fail)</span>
                  <span class="kpi-cell-val mono text-danger">{{ autoStatus.registered_fail || 0 }}</span>
                </div>
                <div class="auto-kpi-cell">
                  <span class="kpi-cell-label">累计耗时 / 周期</span>
                  <span class="kpi-cell-val mono text-purple">{{ autoStatus.total_cycles || 0 }} 次</span>
                </div>
              </div>

              <!-- 运行策略说明卡片 -->
              <div class="auto-policy-banner">
                <div class="policy-icon"><CircleCheckFilled /></div>
                <div class="policy-info">
                  <div class="policy-title">智能风控与自愈机制生效中</div>
                  <div class="policy-desc">
                    一号一 IP 自动隔离 · Remail 失败复用上限 3 次 · 自动强制补绑 TOTP 2FA 及强随机密码
                  </div>
                </div>
              </div>

              <!-- 控制快捷按钮 -->
              <div class="auto-actions-row">
                <el-button type="primary" class="action-main-btn" @click="router.push('/auto')">
                  <el-icon><Compass /></el-icon> 前往批量跑号控制台
                </el-button>
                <el-button class="action-sub-btn" @click="router.push('/runs')">
                  <el-icon><Files /></el-icon> 历史运行日志
                </el-button>
                <el-button class="action-sub-btn" @click="router.push('/registered')">
                  <el-icon><Key /></el-icon> 资产中枢
                </el-button>
              </div>
            </div>
          </div>
        </div>

        <!-- ════════════════ 3. 快捷功能发射台 (Launchpad 2.0) ════════════════ -->
        <div class="launchpad-section">
          <div class="section-title-bar">
            <span class="sec-badge">LAUNCHPAD 2.0</span>
            <span class="sec-title">快捷功能直达 · 极客工作台</span>
          </div>

          <div class="quick-launchpad-grid">
            <div
              v-for="act in quickActions"
              :key="act.title"
              class="launchpad-item"
              @click="router.push(act.path)"
            >
              <div class="launchpad-left">
                <div class="launchpad-icon-box" :style="{ backgroundColor: act.color }">
                  <el-icon :size="18" color="#ffffff"><component :is="act.icon" /></el-icon>
                </div>
                <div class="launchpad-info">
                  <div class="launchpad-title-row">
                    <span class="launchpad-title">{{ act.title }}</span>
                    <span v-if="act.badge" class="launchpad-pill" :style="{ color: act.color, borderColor: act.color }">
                      {{ act.badge }}
                    </span>
                  </div>
                  <span class="launchpad-desc">{{ act.desc }}</span>
                </div>
              </div>
              <div class="launchpad-arrow">→</div>
            </div>
          </div>
        </div>

        <!-- ════════════════ 4. 最近入库注册资产动态流 ════════════════ -->
        <div v-if="summaryData.recent.length" class="recent-feed-section">
          <div class="section-title-bar">
            <span class="sec-badge sec-badge-emerald">LIVE ASSETS</span>
            <span class="sec-title">最近成功注册资产 · Recent Created</span>
          </div>

          <div class="recent-table-card">
            <div class="recent-table-header">
              <span class="col-email">账号邮箱</span>
              <span class="col-geo">出口国家</span>
              <span class="col-sec">安全加固 (密码/2FA)</span>
              <span class="col-time">注册时间</span>
              <span class="col-action">操作</span>
            </div>
            <div
              v-for="item in summaryData.recent"
              :key="item.email"
              class="recent-table-row"
            >
              <span class="col-email mono text-primary">{{ item.masked_email }}</span>
              <span class="col-geo">
                <span class="geo-badge-mini">{{ formatCountry(item.country) }}</span>
              </span>
              <span class="col-sec">
                <span class="sec-pill" :class="item.has_pwd ? 'sec-ok' : 'sec-no'">{{ item.has_pwd ? '密码✓' : '密码×' }}</span>
                <span class="sec-pill" :class="item.has_2fa ? 'sec-2fa' : 'sec-no'">{{ item.has_2fa ? '2FA✓' : '2FA×' }}</span>
              </span>
              <span class="col-time mono">{{ fmtTime(item.created_at) }}</span>
              <span class="col-action">
                <el-button size="small" text type="primary" @click="router.push('/registered')">管理</el-button>
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dashboard-page {
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

.header-right {
  display: flex;
  align-items: center;
  gap: 10px;
}
.header-refresh-btn {
  font-size: 11.5px;
}
.header-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: 0.5px;
  color: #10b981;
  background: rgba(16, 185, 129, 0.1);
  border: 1px solid rgba(16, 185, 129, 0.3);
  padding: 2px 8px;
  border-radius: 10px;
}
.live-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #10b981;
  box-shadow: 0 0 8px #10b981;
}

.dashboard-scrollable-content {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 16px 20px 24px 20px;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

/* ──────────── Bento KPI 卡片矩阵 ──────────── */
.bento-kpi-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 14px;
}

.bento-card {
  padding: 16px 18px;
  border-radius: 12px;
  border: 1px solid var(--el-border-color-lighter);
  background: var(--el-fill-color-light);
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  min-height: 120px;
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  box-sizing: border-box;
  position: relative;
  overflow: hidden;
}
.bento-card:hover {
  transform: translateY(-3px);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.25);
  border-color: var(--el-color-primary);
}

.bento-card.card-pool { border-left: 4px solid #007aff; }
.bento-card.card-asset { border-left: 4px solid #10b981; }
.bento-card.card-loop { border-left: 4px solid #8b5cf6; }
.bento-card.card-remail { border-left: 4px solid #f59e0b; }

.bento-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.bento-title-group {
  display: flex;
  align-items: center;
  gap: 8px;
}
.bento-icon-badge {
  width: 26px;
  height: 26px;
  border-radius: 7px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 13px;
}
.bento-icon-badge.bg-blue { background: linear-gradient(135deg, #007aff, #0056b3); }
.bento-icon-badge.bg-emerald { background: linear-gradient(135deg, #10b981, #047857); }
.bento-icon-badge.bg-purple { background: linear-gradient(135deg, #8b5cf6, #6d28d9); }
.bento-icon-badge.bg-amber { background: linear-gradient(135deg, #f59e0b, #b45309); }

.bento-label {
  font-size: 13px;
  font-weight: 700;
  color: var(--app-title);
}
.bento-sub {
  font-size: 9.5px;
  font-weight: 700;
  letter-spacing: 0.6px;
  color: var(--el-text-color-secondary);
}

.bento-body {
  margin-top: 10px;
}
.bento-main-number {
  font-size: 28px;
  font-weight: 800;
  line-height: 1;
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", sans-serif;
  letter-spacing: -0.03em;
}
.unit-text {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  margin-left: 2px;
}

.bento-meta-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 8px;
  font-size: 11px;
}
.meta-item {
  font-weight: 600;
}

.bento-footer-bar {
  margin-top: 12px;
}
.progress-track {
  height: 4px;
  border-radius: 99px;
  background: var(--el-fill-color);
  overflow: hidden;
}
.progress-fill {
  height: 100%;
  border-radius: 99px;
  transition: width 0.4s ease;
}
.progress-fill.fill-success { background: #007aff; }
.progress-fill.fill-emerald { background: #10b981; }
.progress-fill.fill-purple { background: #8b5cf6; }
.progress-fill.fill-amber { background: #f59e0b; }

.text-blue { color: #007aff; }
.text-emerald { color: #10b981; }
.text-purple { color: #8b5cf6; }
.text-amber { color: #f59e0b; }
.text-danger { color: #ef4444; }
.text-success { color: #10b981; }
.text-warning { color: #f59e0b; }
.text-secondary { color: var(--el-text-color-secondary); }

/* ──────────── 1.5 核心引擎与防风控状态 HUD ──────────── */
.engine-hud-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}

.engine-hud-card {
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 12px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  transition: all 0.2s ease;
}

.engine-hud-card:hover {
  border-color: var(--el-color-primary-light-5);
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.05);
}

.engine-hud-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.engine-hud-title-wrap {
  display: flex;
  align-items: center;
  gap: 8px;
}

.engine-hud-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #10b981;
  box-shadow: 0 0 6px #10b981;
}

.engine-hud-dot.dot-warning {
  background: #f59e0b;
  box-shadow: 0 0 6px #f59e0b;
}

.engine-hud-title {
  font-size: 12.5px;
  font-weight: 700;
  color: var(--app-title);
}

.engine-hud-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.engine-hud-kpi {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  background: var(--app-window-bg);
  border: 1px solid var(--app-border);
  border-radius: 8px;
  padding: 8px 12px;
}

.hud-kpi-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.hud-kpi-label {
  font-size: 10.5px;
  color: var(--el-text-color-secondary);
}

.hud-kpi-val {
  font-size: 14px;
  font-weight: 700;
}

.engine-hud-desc {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  line-height: 1.4;
}

/* ──────────── 2. 中部核心分析分屏 ──────────── */
.bento-analysis-grid {
  display: grid;
  grid-template-columns: 1.15fr 1fr;
  gap: 14px;
}

.analysis-card {
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 12px;
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.card-title-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--el-border-color-lighter);
  padding-bottom: 10px;
}
.title-left {
  display: flex;
  align-items: center;
  gap: 8px;
}
.card-icon {
  color: var(--el-color-primary);
  font-size: 16px;
  display: flex;
}
.card-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--app-title);
}

.geo-country-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 250px;
  overflow-y: auto;
  padding-right: 4px;
}
.geo-country-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.geo-item-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.geo-name-wrap {
  display: flex;
  align-items: center;
  gap: 6px;
}
.geo-rank-num {
  font-size: 11px;
  font-weight: 700;
  color: var(--el-text-color-secondary);
}
.geo-flag-name {
  font-size: 12px;
  font-weight: 600;
  color: var(--app-title);
}
.geo-code-chip {
  font-size: 10.5px;
  color: var(--el-color-primary);
  background: rgba(0, 122, 255, 0.08);
  padding: 0 4px;
  border-radius: 4px;
}
.geo-count-wrap {
  display: flex;
  align-items: center;
  gap: 8px;
}
.geo-count {
  font-size: 12px;
  color: var(--el-text-color-primary);
}
.geo-percent {
  font-size: 11px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
}

.geo-bar-track {
  height: 5px;
  border-radius: 99px;
  background: var(--el-fill-color);
  overflow: hidden;
}
.geo-bar-fill {
  height: 100%;
  border-radius: 99px;
  background: linear-gradient(90deg, #007aff, #38bdf8);
  transition: width 0.35s ease;
}

.geo-empty-wrap {
  padding: 30px 0;
}

/* 自动化控制台 */
.auto-control-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.auto-kpi-subgrid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
}
.auto-kpi-cell {
  background: var(--el-bg-color-overlay, #ffffff);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.kpi-cell-label {
  font-size: 10.5px;
  color: var(--el-text-color-secondary);
}
.kpi-cell-val {
  font-size: 16px;
  font-weight: 800;
}

.auto-policy-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  background: rgba(16, 185, 129, 0.06);
  border: 1px solid rgba(16, 185, 129, 0.2);
  border-radius: 8px;
  padding: 8px 12px;
}
.policy-icon {
  font-size: 20px;
  color: #10b981;
  display: flex;
}
.policy-info {
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.policy-title {
  font-size: 11.5px;
  font-weight: 700;
  color: #10b981;
}
.policy-desc {
  font-size: 10.5px;
  color: var(--el-text-color-secondary);
  line-height: 1.35;
}

.auto-actions-row {
  display: flex;
  gap: 8px;
}
.action-main-btn {
  flex: 1.3;
  font-weight: 700;
}
.action-sub-btn {
  flex: 1;
}

/* ──────────── 3. 快捷发射台 (Launchpad 2.0) ──────────── */
.launchpad-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.section-title-bar {
  display: flex;
  align-items: center;
  gap: 8px;
}
.sec-badge {
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.6px;
  color: #007aff;
  background: rgba(0, 122, 255, 0.1);
  padding: 1px 6px;
  border-radius: 4px;
}
.sec-badge-emerald {
  color: #10b981;
  background: rgba(16, 185, 129, 0.1);
}
.sec-title {
  font-size: 12.5px;
  font-weight: 700;
  color: var(--app-title);
}

.quick-launchpad-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
}

.launchpad-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
  cursor: pointer;
  transition: all 0.18s cubic-bezier(0.4, 0, 0.2, 1);
  user-select: none;
}
.launchpad-item:hover {
  transform: translateY(-2px);
  border-color: var(--el-color-primary);
  background: rgba(0, 122, 255, 0.04);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}

.launchpad-left {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}
.launchpad-icon-box {
  width: 34px;
  height: 34px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.launchpad-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.launchpad-title-row {
  display: flex;
  align-items: center;
  gap: 6px;
}
.launchpad-title {
  font-size: 12.5px;
  font-weight: 700;
  color: var(--app-title);
  white-space: nowrap;
}
.launchpad-pill {
  font-size: 9.5px;
  font-weight: 700;
  border: 1px solid;
  padding: 0 4px;
  border-radius: 4px;
  line-height: 1.2;
}
.launchpad-desc {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.launchpad-arrow {
  color: var(--el-text-color-secondary);
  font-size: 14px;
  font-weight: 700;
  transition: transform 0.15s ease;
}
.launchpad-item:hover .launchpad-arrow {
  color: var(--el-color-primary);
  transform: translateX(3px);
}

/* ──────────── 4. 最近注册动态 ──────────── */
.recent-feed-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.recent-table-card {
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
  overflow: hidden;
}
.recent-table-header {
  display: grid;
  grid-template-columns: 2fr 1fr 1.2fr 1.2fr 80px;
  padding: 8px 14px;
  background: var(--el-fill-color);
  font-size: 11px;
  font-weight: 700;
  color: var(--el-text-color-secondary);
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.recent-table-row {
  display: grid;
  grid-template-columns: 2fr 1fr 1.2fr 1.2fr 80px;
  padding: 9px 14px;
  align-items: center;
  font-size: 11.5px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  transition: background 0.15s ease;
}
.recent-table-row:last-child {
  border-bottom: none;
}
.recent-table-row:hover {
  background: rgba(255, 255, 255, 0.03);
}
.geo-badge-mini {
  font-size: 11px;
  font-weight: 600;
  color: #38bdf8;
}
.sec-pill {
  display: inline-block;
  font-size: 10px;
  font-weight: 700;
  padding: 1px 5px;
  border-radius: 3px;
  margin-right: 4px;
}
.sec-pill.sec-ok { background: rgba(16, 185, 129, 0.15); color: #10b981; }
.sec-pill.sec-2fa { background: rgba(59, 130, 246, 0.15); color: #3b82f6; }
.sec-pill.sec-no { background: rgba(239, 68, 68, 0.1); color: #ef4444; }
</style>
