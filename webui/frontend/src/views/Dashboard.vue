<script setup>
import { computed } from 'vue'
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
} from '@element-plus/icons-vue'
import { useStatsStore } from '@/stores/stats'
import { useRuntimeStore } from '@/stores/runtime'
import StatusDot from '@/components/StatusDot.vue'

const router = useRouter()
const { stats } = storeToRefs(useStatsStore())
const { autoStatus } = storeToRefs(useRuntimeStore())

const kpiCards = computed(() => [
  {
    label: '号池总数',
    sub: 'Total Pool',
    value: stats.value.total || 0,
    color: '#007aff',
    bg: 'rgba(0, 122, 255, 0.08)',
    border: 'rgba(0, 122, 255, 0.2)',
    icon: Files,
  },
  {
    label: '可用库存',
    sub: 'Available',
    value: stats.value.available || 0,
    color: '#34c759',
    bg: 'rgba(52, 199, 89, 0.08)',
    border: 'rgba(52, 199, 89, 0.2)',
    icon: CircleCheck,
  },
  {
    label: '进行中',
    sub: 'In Progress',
    value: stats.value.in_use || 0,
    color: '#ff9500',
    bg: 'rgba(255, 149, 0, 0.08)',
    border: 'rgba(255, 149, 0, 0.2)',
    icon: Loading,
  },
  {
    label: '注册完成',
    sub: 'Success Done',
    value: stats.value.done || 0,
    color: '#5856d6',
    bg: 'rgba(88, 86, 214, 0.08)',
    border: 'rgba(88, 86, 214, 0.2)',
    icon: Select,
  },
  {
    label: '注册失败',
    sub: 'Failed',
    value: stats.value.failed || 0,
    color: '#ff3b30',
    bg: 'rgba(255, 59, 48, 0.08)',
    border: 'rgba(255, 59, 48, 0.2)',
    icon: CircleClose,
  },
])

const autoStateLabel = computed(() => ({
  stopped: '未运行 (Idle)',
  running: '正在并发跑号 (Running)',
  paused: '已暂停 (Paused)',
}[autoStatus.value.state] || autoStatus.value.state))

const autoStateType = computed(() => ({
  stopped: 'info',
  running: 'success',
  paused: 'warning',
}[autoStatus.value.state] || 'info'))

const quickActions = [
  { title: '批量导入邮箱', desc: '支持多格式号池导入', icon: Upload, path: '/import', color: '#007aff' },
  { title: '单次注册调试', desc: '即时测通与凭证生成', icon: VideoPlay, path: '/register', color: '#34c759' },
  { title: '全自动并发批量', desc: '多Worker无人值守跑号', icon: Compass, path: '/auto', color: '#ff9500' },
  { title: '注册结果总览', desc: 'Token / 2FA / Plus凭证', icon: Key, path: '/registered', color: '#af52de' },
  { title: '全渠道提链中心', desc: 'PayPal/PIX/GCash出码', icon: Link, path: '/extract', color: '#5856d6' },
  { title: 'PayPal 协议代付', desc: '纯协议高并发极速代付', icon: CreditCard, path: '/paypal-pay', color: '#007aff' },
]
</script>

<template>
  <div class="dashboard-page">
    <div class="macos-window-panel">
      <!-- 顶部 macOS 窗口控制条 -->
      <div class="macos-panel-header">
        <div class="header-left">
          <div class="window-dot-group">
            <span class="dot red"></span>
            <span class="dot yellow"></span>
            <span class="dot green"></span>
          </div>
          <span class="panel-title">系统运行总览 · Dashboard</span>
        </div>
        <div class="header-right">
          <span class="header-badge">macOS Native UI</span>
        </div>
      </div>

      <div class="dashboard-scrollable-content">
        <!-- 核心 Bento KPI 指标矩阵 -->
        <div class="bento-kpi-grid">
          <div
            v-for="c in kpiCards"
            :key="c.label"
            class="bento-card"
            :style="{ borderColor: c.border, background: c.bg }"
          >
            <div class="bento-header">
              <span class="bento-sub">{{ c.sub }}</span>
              <div class="bento-icon-badge" :style="{ backgroundColor: c.color }">
                <el-icon :size="14" color="#ffffff"><component :is="c.icon" /></el-icon>
              </div>
            </div>
            <div class="bento-body">
              <div class="bento-val" :style="{ color: c.color }">{{ c.value }}</div>
              <div class="bento-label">{{ c.label }}</div>
            </div>
          </div>
        </div>

        <!-- 中间双栏卡片：自动跑号状态 & 快速操作入口 -->
        <div class="bento-main-grid">
          <!-- 卡片 1: 自动跑号调度中心 -->
          <div class="bento-widget-card">
            <div class="widget-header">
              <div class="widget-title-box">
                <span class="widget-badge pulse-badge">AUTO LOOP</span>
                <span class="widget-title">全自动批量跑号控制台</span>
              </div>
              <el-tag :type="autoStateType" size="small" effect="light" class="state-tag">
                <StatusDot :type="autoStateType" :text="autoStateLabel" />
              </el-tag>
            </div>

            <div class="widget-body">
              <div class="auto-stats-grid">
                <div class="stat-mini-card">
                  <span class="mini-label">并发线程 Worker</span>
                  <span class="mini-val">{{ autoStatus.concurrency || 1 }}</span>
                </div>
                <div class="stat-mini-card">
                  <span class="mini-label">成功注册数 (OK)</span>
                  <span class="mini-val text-success">{{ autoStatus.registered_ok || 0 }}</span>
                </div>
                <div class="stat-mini-card">
                  <span class="mini-label">失败重试数 (Fail)</span>
                  <span class="mini-val text-danger">{{ autoStatus.registered_fail || 0 }}</span>
                </div>
                <div class="stat-mini-card">
                  <span class="mini-label">累计耗时 / 周期</span>
                  <span class="mini-val text-primary">{{ autoStatus.total_cycles || 0 }} 次</span>
                </div>
              </div>

              <div class="widget-action-bar">
                <el-button type="primary" class="macos-action-btn" @click="router.push('/auto')">
                  <el-icon><Compass /></el-icon>前往批量跑号控制台
                </el-button>
                <el-button class="macos-sub-btn" @click="router.push('/runs')">
                  <el-icon><Files /></el-icon>历史运行日志
                </el-button>
              </div>
            </div>
          </div>

          <!-- 卡片 2: 快捷功能导航矩阵 -->
          <div class="bento-widget-card">
            <div class="widget-header">
              <div class="widget-title-box">
                <span class="widget-badge quick-badge">LAUNCHPAD</span>
                <span class="widget-title">快捷功能直达 · Launchpad</span>
              </div>
            </div>

            <div class="widget-body">
              <div class="quick-launchpad-grid">
                <div
                  v-for="act in quickActions"
                  :key="act.title"
                  class="launchpad-item"
                  @click="router.push(act.path)"
                >
                  <div class="launchpad-icon-box" :style="{ backgroundColor: act.color }">
                    <el-icon :size="18" color="#ffffff"><component :is="act.icon" /></el-icon>
                  </div>
                  <div class="launchpad-info">
                    <span class="launchpad-title">{{ act.title }}</span>
                    <span class="launchpad-desc">{{ act.desc }}</span>
                  </div>
                </div>
              </div>
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

.dashboard-scrollable-content {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 16px 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* ──────────── Bento KPI 卡片矩阵 ──────────── */
.bento-kpi-grid {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 12px;
}

.bento-card {
  padding: 14px 16px;
  border-radius: 12px;
  border: 1px solid;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  min-height: 96px;
  transition: transform 0.18s cubic-bezier(0.4, 0, 0.2, 1), box-shadow 0.18s ease;
}
.bento-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--app-shadow-md);
}

.bento-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.bento-sub {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.6px;
  text-transform: uppercase;
  color: var(--app-text-secondary);
}
.bento-icon-badge {
  width: 22px;
  height: 22px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.bento-body {
  margin-top: 10px;
}
.bento-val {
  font-size: 24px;
  font-weight: 700;
  line-height: 1.1;
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", sans-serif;
  letter-spacing: -0.02em;
}
.bento-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--app-text-regular);
  margin-top: 4px;
}

/* ──────────── 主体双栏组件卡片 ──────────── */
.bento-main-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.bento-widget-card {
  background: var(--app-card-bg);
  border: 1px solid var(--app-card-border);
  border-radius: 12px;
  box-shadow: var(--app-shadow-sm);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.widget-header {
  padding: 12px 16px;
  background: var(--el-fill-color-light);
  border-bottom: 1px solid var(--app-border);
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.widget-title-box {
  display: flex;
  align-items: center;
  gap: 8px;
}
.widget-badge {
  font-size: 10px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 4px;
  letter-spacing: 0.5px;
}
.widget-badge.pulse-badge {
  background: linear-gradient(135deg, #007aff, #0056b3);
  color: #fff;
}
.widget-badge.quick-badge {
  background: linear-gradient(135deg, #af52de, #5856d6);
  color: #fff;
}

.widget-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--app-title);
}

.widget-body {
  padding: 16px;
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}

/* 自动跑号数据子网格 */
.auto-stats-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  margin-bottom: 16px;
}
.stat-mini-card {
  background: var(--el-fill-color-light);
  border: 1px solid var(--app-border);
  border-radius: 8px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
}
.mini-label {
  font-size: 11px;
  color: var(--app-text-secondary);
}
.mini-val {
  font-size: 18px;
  font-weight: 700;
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", sans-serif;
  margin-top: 4px;
}
.text-success { color: var(--apple-green); }
.text-danger { color: var(--apple-red); }
.text-primary { color: var(--apple-blue); }

.widget-action-bar {
  display: flex;
  gap: 10px;
}
.macos-action-btn {
  flex: 1;
  border-radius: 8px;
  font-weight: 600;
}
.macos-sub-btn {
  border-radius: 8px;
}

/* Launchpad 快速操作网格 */
.quick-launchpad-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.launchpad-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--app-border);
  cursor: pointer;
  transition: all 0.16s ease;
}
.launchpad-item:hover {
  background: var(--el-fill-color);
  border-color: var(--el-color-primary-light-5);
  transform: translateX(2px);
}
.launchpad-icon-box {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.12);
}
.launchpad-info {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.launchpad-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--app-title);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.launchpad-desc {
  font-size: 10.5px;
  color: var(--app-text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

@media (max-width: 1024px) {
  .bento-kpi-grid {
    grid-template-columns: repeat(3, 1fr);
  }
  .bento-main-grid {
    grid-template-columns: 1fr;
  }
}
</style>
