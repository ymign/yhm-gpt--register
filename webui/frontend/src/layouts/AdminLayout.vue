<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import {
  Fold,
  Expand,
  Moon,
  Sunny,
  User,
  ArrowDown,
  Search,
} from '@element-plus/icons-vue'
import { useThemeStore } from '@/stores/theme'
import { useStatsStore } from '@/stores/stats'
import { useRuntimeStore } from '@/stores/runtime'

const route = useRoute()
const router = useRouter()
const theme = useThemeStore()
const statsStore = useStatsStore()
const runtime = useRuntimeStore()
const { stats } = storeToRefs(statsStore)
const { banner } = storeToRefs(runtime)

const collapse = ref(false)

const GROUP_ORDER = ['概览', '账号', '流水线', '提炼', '配置']
const groups = computed(() => {
  const map = {}
  for (const r of router.getRoutes()) {
    if (!r.meta?.title) continue
    const g = r.meta.group || '其他'
    ;(map[g] ||= []).push(r)
  }
  return GROUP_ORDER.filter((g) => map[g]).map((g) => ({ name: g, items: map[g] }))
})

const activeMenu = computed(() => route.path)
const crumb = computed(() => [route.meta.group, route.meta.title].filter(Boolean))

const menuOptions = computed(() =>
  router.getRoutes()
    .filter((r) => r.meta?.title)
    .map((r) => ({ value: r.path, label: `${r.meta.group} · ${r.meta.title}` })),
)
const search = ref('')
function onSearch(path) {
  if (path) router.push(path)
  search.value = ''
}

const statPills = computed(() => [
  { label: '总数', value: stats.value.total, type: 'info' },
  { label: '可用', value: stats.value.available, type: 'success' },
  { label: '运行', value: stats.value.in_use, type: 'warning' },
  { label: '完成', value: stats.value.done, type: 'primary' },
  { label: '失败', value: stats.value.failed, type: 'danger' },
])

onMounted(() => {
  theme.apply()
  statsStore.startPolling()
  runtime.connectAutoStream()
})
</script>

<template>
  <el-container class="admin-macos-shell">
    <!-- 左侧 macOS 原生风格边栏 (支持毛玻璃与红黄绿交通灯) -->
    <el-aside :width="collapse ? '68px' : '224px'" class="macos-sidebar">
      <!-- 边栏顶部：macOS 经典三色交通灯与品牌 Logo -->
      <div class="sidebar-topbar">
        <div class="mac-traffic-lights">
          <span class="traffic-dot red" title="关闭"></span>
          <span class="traffic-dot yellow" title="最小化"></span>
          <span class="traffic-dot green" title="最大化"></span>
        </div>
        <div v-if="!collapse" class="macos-brand">
          <span class="brand-badge">GPT</span>
          <span class="brand-title">少司命</span>
        </div>
      </div>

      <!-- 侧边导航项 -->
      <el-scrollbar class="sidebar-scroll">
        <el-menu
          :default-active="activeMenu"
          router
          :collapse="collapse"
          class="macos-side-menu"
        >
          <el-menu-item-group
            v-for="grp in groups"
            :key="grp.name"
            :title="collapse ? '' : grp.name"
            class="macos-menu-group"
          >
            <el-menu-item
              v-for="r in grp.items"
              :key="r.path"
              :index="r.path"
              class="macos-menu-item"
            >
              <el-icon class="menu-icon"><component :is="r.meta.icon" /></el-icon>
              <template #title>
                <span class="menu-label">{{ r.meta.title }}</span>
              </template>
            </el-menu-item>
          </el-menu-item-group>
        </el-menu>
      </el-scrollbar>

      <!-- 边栏底部极简折叠切换 -->
      <div class="sidebar-bottom">
        <button
          class="collapse-btn"
          :title="collapse ? '展开导航栏' : '收起导航栏'"
          @click="collapse = !collapse"
        >
          <el-icon :size="15"><Fold v-if="!collapse" /><Expand v-else /></el-icon>
          <span v-if="!collapse" class="collapse-text">收起侧栏</span>
        </button>
      </div>
    </el-aside>

    <!-- 右侧主体内容容器 -->
    <el-container class="macos-main-shell">
      <!-- macOS 顶部毛玻璃导航栏 -->
      <el-header class="macos-topbar">
        <div class="topbar-left">
          <button
            class="macos-icon-btn"
            :title="collapse ? '展开侧栏' : '收起侧栏'"
            @click="collapse = !collapse"
          >
            <el-icon :size="16"><Fold v-if="!collapse" /><Expand v-else /></el-icon>
          </button>

          <el-breadcrumb separator="/" class="macos-breadcrumb">
            <el-breadcrumb-item v-for="c in crumb" :key="c">{{ c }}</el-breadcrumb-item>
          </el-breadcrumb>
        </div>

        <div class="topbar-right">
          <!-- Spotlight 快捷跳转检索 -->
          <el-select
            v-model="search"
            filterable
            clearable
            placeholder="⌘K 快速查找功能..."
            size="small"
            class="macos-spotlight-search"
            :prefix-icon="Search"
            @change="onSearch"
          >
            <el-option
              v-for="o in menuOptions"
              :key="o.value"
              :label="o.label"
              :value="o.value"
            />
          </el-select>

          <!-- 账号池实时指标胶囊 -->
          <div class="macos-stat-pills">
            <div
              v-for="p in statPills"
              :key="p.label"
              class="stat-pill"
              :class="'pill-' + p.type"
            >
              <span class="pill-dot"></span>
              <span class="pill-label">{{ p.label }}</span>
              <span class="pill-val">{{ p.value }}</span>
            </div>
          </div>

          <!-- 主题切换 -->
          <el-tooltip :content="theme.dark ? '切换为浅色模式' : '切换为深色模式'" placement="bottom">
            <button class="macos-theme-toggle" @click="theme.toggle">
              <el-icon :size="16"><Moon v-if="!theme.dark" /><Sunny v-else /></el-icon>
            </button>
          </el-tooltip>

          <!-- 用户头像与菜单 -->
          <el-dropdown trigger="click">
            <div class="macos-user-profile">
              <div class="avatar-circle">
                <el-icon :size="14"><User /></el-icon>
              </div>
              <span class="user-name">Admin</span>
              <el-icon :size="11" class="dropdown-arrow"><ArrowDown /></el-icon>
            </div>
            <template #dropdown>
              <el-dropdown-menu class="macos-dropdown-menu">
                <el-dropdown-item @click="theme.toggle">
                  {{ theme.dark ? '☀️ 浅色模式' : '🌙 深色模式' }}
                </el-dropdown-item>
                <el-dropdown-item divided @click="router.push('/settings/mail')">
                  ⚙️ 系统设置
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <!-- 主视图渲染区域 (100% 弹性视口，彻底消除外层滚动条) -->
      <el-main class="macos-content-viewport">
        <el-alert
          v-if="banner"
          :title="banner"
          type="error"
          show-icon
          class="circuit-banner"
          @close="runtime.dismissBanner"
        />
        <div class="macos-viewport-inner">
          <router-view v-slot="{ Component }">
            <keep-alive>
              <component :is="Component" />
            </keep-alive>
          </router-view>
        </div>
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
/* ──────────── macOS 系统级全屏外壳 ──────────── */
.admin-macos-shell {
  height: 100vh;
  width: 100vw;
  overflow: hidden;
  display: flex;
  background: var(--app-canvas-bg);
}

/* ──────────── 左侧 macOS 原生边栏 ──────────── */
.macos-sidebar {
  background: var(--app-sidebar-bg);
  backdrop-filter: blur(24px) saturate(180%);
  -webkit-backdrop-filter: blur(24px) saturate(180%);
  border-right: 1px solid var(--app-border);
  display: flex;
  flex-direction: column;
  transition: width 0.22s cubic-bezier(0.4, 0, 0.2, 1);
  overflow: hidden;
  z-index: 10;
  user-select: none;
}

.sidebar-topbar {
  padding: 16px 16px 12px;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}

.macos-brand {
  display: flex;
  align-items: center;
  gap: 6px;
}
.brand-badge {
  font-size: 10.5px;
  font-weight: 700;
  background: #007aff;
  color: #fff;
  padding: 1px 6px;
  border-radius: 5px;
  letter-spacing: 0.5px;
}
.brand-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--app-title);
  letter-spacing: -0.02em;
}

.sidebar-scroll {
  flex: 1;
  min-height: 0;
  padding: 0 8px;
}

.macos-side-menu {
  border-right: none !important;
  background: transparent !important;
}

/* 分组标题 */
:deep(.macos-menu-group .el-menu-item-group__title) {
  padding: 10px 12px 4px !important;
  font-size: 10.5px !important;
  font-weight: 700 !important;
  color: var(--app-text-secondary) !important;
  letter-spacing: 0.6px !important;
  text-transform: uppercase !important;
}

/* 导航项：macOS 胶囊按钮 */
.macos-menu-item {
  height: 34px !important;
  line-height: 34px !important;
  margin-bottom: 2px !important;
  border-radius: 8px !important;
  color: var(--app-text-regular) !important;
  font-size: 12.5px !important;
  font-weight: 500 !important;
  padding: 0 10px !important;
  transition: all 0.16s cubic-bezier(0.4, 0, 0.2, 1) !important;
}

.macos-menu-item:hover {
  background: rgba(0, 0, 0, 0.05) !important;
  color: var(--app-title) !important;
}
html.dark .macos-menu-item:hover {
  background: rgba(255, 255, 255, 0.08) !important;
}

/* 选中激活态：Apple SF Blue 拟态胶囊 */
.macos-menu-item.is-active {
  background: #007aff !important;
  color: #ffffff !important;
  font-weight: 600 !important;
  box-shadow: none !important;
}
.macos-menu-item.is-active .menu-icon {
  color: #ffffff !important;
}

.menu-icon {
  font-size: 15px !important;
  margin-right: 8px !important;
  color: var(--app-text-secondary);
}

.menu-label {
  letter-spacing: -0.01em;
}

/* 边栏底部 */
.sidebar-bottom {
  padding: 10px 12px;
  border-top: 1px solid var(--app-border);
  flex-shrink: 0;
}
.collapse-btn {
  width: 100%;
  height: 30px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 6px;
  color: var(--app-text-secondary);
  font-size: 11.5px;
  cursor: pointer;
  outline: none;
  transition: all 0.15s ease;
}
.collapse-btn:hover {
  background: rgba(0, 0, 0, 0.05);
  color: var(--app-title);
}
html.dark .collapse-btn:hover {
  background: rgba(255, 255, 255, 0.08);
}

/* ──────────── 右侧主体外壳与毛玻璃顶栏 ──────────── */
.macos-main-shell {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: var(--app-content-bg);
  overflow: hidden;
}

.macos-topbar {
  height: 50px !important;
  padding: 0 16px !important;
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--app-header-bg);
  backdrop-filter: blur(24px) saturate(180%);
  -webkit-backdrop-filter: blur(24px) saturate(180%);
  border-bottom: 1px solid var(--app-border);
  flex-shrink: 0;
  z-index: 9;
}

.topbar-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.macos-icon-btn {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: 1px solid var(--app-border);
  border-radius: 6px;
  color: var(--app-text-regular);
  cursor: pointer;
  outline: none;
  transition: all 0.15s ease;
}
.macos-icon-btn:hover {
  background: var(--el-fill-color-light);
  color: var(--el-color-primary);
}

.macos-breadcrumb :deep(.el-breadcrumb__inner) {
  font-size: 12.5px;
  font-weight: 500;
  color: var(--app-text-secondary);
}
.macos-breadcrumb :deep(.el-breadcrumb__item:last-child .el-breadcrumb__inner) {
  color: var(--app-title);
  font-weight: 600;
}

.topbar-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.macos-spotlight-search {
  width: 190px;
}
.macos-spotlight-search :deep(.el-input__wrapper) {
  background: var(--el-fill-color-light) !important;
  border-radius: 8px !important;
  box-shadow: none !important;
}

/* 状态指标胶囊群 */
.macos-stat-pills {
  display: flex;
  align-items: center;
  gap: 6px;
}
.stat-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 7px;
  border-radius: 12px;
  font-size: 11px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--app-border);
}
.stat-pill .pill-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #8e8e93;
}
.stat-pill .pill-label {
  color: var(--app-text-secondary);
}
.stat-pill .pill-val {
  font-weight: 700;
  font-family: "SFMono-Regular", Consolas, monospace;
}

.stat-pill.pill-success .pill-dot { background: var(--apple-green); }
.stat-pill.pill-success .pill-val { color: var(--apple-green); }
.stat-pill.pill-warning .pill-dot { background: var(--apple-orange); }
.stat-pill.pill-warning .pill-val { color: var(--apple-orange); }
.stat-pill.pill-primary .pill-dot { background: var(--apple-blue); }
.stat-pill.pill-primary .pill-val { color: var(--apple-blue); }
.stat-pill.pill-danger .pill-dot { background: var(--apple-red); }
.stat-pill.pill-danger .pill-val { color: var(--apple-red); }

/* 主题切换开关 */
.macos-theme-toggle {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: 1px solid var(--app-border);
  border-radius: 6px;
  color: var(--app-text-regular);
  cursor: pointer;
  outline: none;
  transition: all 0.15s ease;
}
.macos-theme-toggle:hover {
  background: var(--el-fill-color-light);
  color: var(--el-color-primary);
}

/* 用户 Profile 胶囊 */
.macos-user-profile {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 8px 3px 4px;
  border-radius: 16px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--app-border);
  cursor: pointer;
  transition: all 0.15s ease;
}
.macos-user-profile:hover {
  background: var(--el-fill-color);
}
.avatar-circle {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #007aff;
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
}
.user-name {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--app-title);
}
.dropdown-arrow {
  color: var(--app-text-secondary);
}

/* ──────────── 视口内容容器 (弹性伸缩，绝不溢出) ──────────── */
.macos-content-viewport {
  flex: 1;
  min-height: 0;
  padding: 10px 14px 14px !important;
  display: flex;
  flex-direction: column;
  overflow: hidden !important;
  background: var(--app-content-bg);
}

.circuit-banner {
  margin-bottom: 8px;
  border-radius: 8px;
  flex-shrink: 0;
}

.macos-viewport-inner {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

@media (max-width: 860px) {
  .macos-stat-pills,
  .macos-spotlight-search {
    display: none;
  }
}
</style>
