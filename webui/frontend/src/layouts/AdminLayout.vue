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

const statPills = computed(() => {
  const pills = [
    { label: '总数', value: stats.value.total, type: 'info' },
    { label: '可用', value: stats.value.available, type: 'success' },
    { label: '运行', value: stats.value.in_use, type: 'warning' },
    { label: '完成', value: stats.value.done, type: 'primary' },
    { label: '失败', value: stats.value.failed, type: 'danger' },
  ]
  // 归档号（只留存不再使用）只在真有归档时露出来，免得平时占位
  if (stats.value.archived > 0) {
    pills.push({ label: '归档', value: stats.value.archived, type: 'info' })
  }
  return pills
})

onMounted(() => {
  theme.apply()
  statsStore.startPolling()
  runtime.connectAutoStream()
})
</script>

<template>
  <el-container class="admin-macos-shell">
    <!-- 全局背景流体环境微光光球 (Ambient Glass Glow Orbs) -->
    <div class="shell-ambient-sphere sphere-1"></div>
    <div class="shell-ambient-sphere sphere-2"></div>
    <div class="shell-ambient-sphere sphere-3"></div>

    <!-- 左侧 3D 水晶拟态浮空边栏 (Liquid Glass Sidebar) -->
    <el-aside :width="collapse ? '74px' : '232px'" class="macos-sidebar">
      <div class="sidebar-glass-specular"></div>
      <div class="sidebar-caustic-flare"></div>
      <!-- 边栏顶部：品牌 Logo (3D 水晶徽标) -->
      <div class="sidebar-topbar">
        <div class="macos-brand">
          <span class="brand-badge">GPT</span>
          <span v-if="!collapse" class="brand-title">少司命</span>
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
/* ──────────── 3D实体液态水晶 macOS 系统级全屏工作区 ──────────── */
.admin-macos-shell {
  height: 100vh;
  width: 100vw;
  overflow: hidden;
  display: flex;
  position: relative;
  /* 摄影棚柔和瓷白底台 + 极细透气网格底纹 (严格对照素材图舒适高质感底色) */
  background-color: #f1f4f9;
  background-image:
    radial-gradient(ellipse at 12% 15%, rgba(199, 210, 254, 0.45) 0%, transparent 45%),
    radial-gradient(ellipse at 88% 85%, rgba(254, 215, 170, 0.25) 0%, transparent 45%),
    radial-gradient(ellipse at 50% 0%, rgba(255, 255, 255, 0.95) 0%, transparent 60%),
    linear-gradient(to right, rgba(148, 163, 184, 0.18) 1px, transparent 1px),
    linear-gradient(to bottom, rgba(148, 163, 184, 0.18) 1px, transparent 1px);
  background-size: 100% 100%, 100% 100%, 100% 100%, 28px 28px, 28px 28px;
}

/* ════════ 全局环境微光光球 (柔和马卡龙环境透光，不刺眼) ════════ */
.shell-ambient-sphere {
  position: absolute;
  border-radius: 50%;
  filter: blur(100px);
  pointer-events: none;
  z-index: 0;
  opacity: 0.55;
}
.sphere-1 {
  top: -60px;
  left: -40px;
  width: 500px;
  height: 500px;
  background: radial-gradient(circle, rgba(199, 210, 254, 0.7) 0%, rgba(165, 180, 252, 0.12) 70%);
  animation: floatOrb1 18s ease-in-out infinite alternate;
}
.sphere-2 {
  top: 35%;
  right: 10%;
  width: 520px;
  height: 520px;
  background: radial-gradient(circle, rgba(233, 213, 255, 0.6) 0%, rgba(192, 132, 252, 0.08) 70%);
  animation: floatOrb2 22s ease-in-out infinite alternate-reverse;
}
.sphere-3 {
  bottom: -60px;
  left: 30%;
  width: 550px;
  height: 550px;
  background: radial-gradient(circle, rgba(254, 215, 170, 0.5) 0%, rgba(251, 191, 36, 0.08) 70%);
  animation: floatOrb1 20s ease-in-out infinite alternate;
}
@keyframes floatOrb1 {
  0% { transform: translate(0, 0) scale(1); }
  100% { transform: translate(25px, 15px) scale(1.06); }
}
@keyframes floatOrb2 {
  0% { transform: translate(0, 0) scale(1); }
  100% { transform: translate(-20px, -15px) scale(1.05); }
}

/* ──────────── 左侧 3D 浮空实体水晶边栏 (Liquid Glass Floating Island) ──────────── */
.macos-sidebar {
  position: relative;
  margin: 10px 0 10px 12px;
  height: calc(100vh - 20px) !important;
  border-radius: 20px;
  background: rgba(255, 255, 255, 0.65);
  backdrop-filter: blur(30px) saturate(180%);
  -webkit-backdrop-filter: blur(30px) saturate(180%);
  border: 1.5px solid rgba(255, 255, 255, 0.92);
  border-top: 2.2px solid #ffffff;
  border-bottom: 1.5px solid rgba(203, 213, 225, 0.5);
  box-shadow: 0 20px 48px -8px rgba(100, 116, 139, 0.12), 0 6px 18px -4px rgba(100, 116, 139, 0.06), inset 0 2px 2px #ffffff, inset 0 -1.5px 2px rgba(148, 163, 184, 0.15);
  display: flex;
  flex-direction: column;
  transition: width 0.22s cubic-bezier(0.4, 0, 0.2, 1);
  overflow: hidden;
  z-index: 10;
  user-select: none;
}
html.dark .macos-sidebar {
  background: rgba(15, 23, 42, 0.82);
  border-color: rgba(255, 255, 255, 0.08);
  box-shadow: 0 20px 48px -8px rgba(0, 0, 0, 0.5);
}

/* 顶部高光反射膜与底部彩色光焦散 */
.sidebar-glass-specular {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 38px;
  border-radius: 20px 20px 45% 45% / 20px 20px 18px 18px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.95) 0%, rgba(255, 255, 255, 0.25) 55%, transparent 100%);
  pointer-events: none;
  z-index: 2;
}
.sidebar-caustic-flare {
  position: absolute;
  bottom: -4px;
  left: 15%;
  right: 15%;
  height: 16px;
  border-radius: 50%;
  background: radial-gradient(ellipse at center, rgba(199, 210, 254, 0.4) 0%, rgba(254, 215, 170, 0.2) 50%, transparent 80%);
  filter: blur(6px);
  pointer-events: none;
  z-index: 1;
}

/* 边栏顶部品牌区域 (3D 水晶大徽标) */
.sidebar-topbar {
  padding: 14px 14px 12px;
  margin: 6px 8px 8px;
  background: rgba(255, 255, 255, 0.7);
  backdrop-filter: blur(16px);
  border: 1.2px solid rgba(255, 255, 255, 0.95);
  border-top: 1.8px solid #ffffff;
  border-radius: 14px;
  box-shadow: 0 4px 14px -2px rgba(15, 23, 42, 0.05), inset 0 1.5px 1px #ffffff;
  display: flex;
  align-items: center;
  justify-content: flex-start;
  flex-shrink: 0;
  position: relative;
  z-index: 3;
}
html.dark .sidebar-topbar {
  background: rgba(30, 41, 59, 0.85);
  border-color: rgba(255, 255, 255, 0.1);
}

.macos-brand {
  display: flex;
  align-items: center;
  gap: 8px;
}
.brand-badge {
  font-size: 11px;
  font-weight: 800;
  background: linear-gradient(180deg, #38bdf8 0%, #0284c7 60%, #0369a1 100%);
  color: #ffffff;
  padding: 3px 8px;
  border-radius: 8px;
  letter-spacing: 0.5px;
  border: 1.2px solid rgba(255, 255, 255, 0.95);
  box-shadow: 0 4px 12px rgba(2, 132, 199, 0.35), inset 0 1.5px 1px rgba(255, 255, 255, 0.85);
}
.brand-title {
  font-size: 14.5px;
  font-weight: 800;
  color: #0f172a;
  letter-spacing: -0.01em;
}
html.dark .brand-title {
  color: #f1f5f9;
}

.sidebar-scroll {
  flex: 1;
  min-height: 0;
  padding: 0 4px;
}

.macos-side-menu {
  border-right: none !important;
  background: transparent !important;
}

/* 分组标题 */
:deep(.macos-menu-group .el-menu-item-group__title) {
  padding: 12px 14px 4px !important;
  font-size: 10.5px !important;
  font-weight: 700 !important;
  color: #64748b !important;
  letter-spacing: 0.8px !important;
  text-transform: uppercase !important;
}
html.dark :deep(.macos-menu-group .el-menu-item-group__title) {
  color: #94a3b8 !important;
}

/* 导航项 (通透柔和水晶微胶囊，对齐素材 2ac2e3a9c1fd2371a185add9ac5a345a.jpg 与 3c96dc7ad762c523c88e687794b9c39a.jpg) */
.macos-menu-item {
  height: 38px !important;
  line-height: 38px !important;
  margin: 4px 8px !important;
  border-radius: 11px !important;
  padding: 0 10px !important;
  background: rgba(255, 255, 255, 0.45) !important;
  backdrop-filter: blur(10px) !important;
  -webkit-backdrop-filter: blur(10px) !important;
  border: 1px solid rgba(255, 255, 255, 0.8) !important;
  border-top: 1.4px solid rgba(255, 255, 255, 0.95) !important;
  box-shadow: 0 2px 6px rgba(15, 23, 42, 0.02), inset 0 1px 1px rgba(255, 255, 255, 0.8) !important;
  color: #334155 !important;
  font-size: 12.5px !important;
  font-weight: 600 !important;
  transition: all 0.16s cubic-bezier(0.16, 1, 0.3, 1) !important;
  display: flex !important;
  align-items: center !important;
}
html.dark .macos-menu-item {
  background: rgba(30, 41, 59, 0.45) !important;
  border-color: rgba(255, 255, 255, 0.06) !important;
  color: #cbd5e1 !important;
}

.macos-menu-item:hover {
  background: rgba(255, 255, 255, 0.9) !important;
  border-color: rgba(56, 189, 248, 0.6) !important;
  border-top-color: #ffffff !important;
  color: #0284c7 !important;
  transform: translateY(-1px) !important;
  box-shadow: 0 4px 14px rgba(2, 132, 199, 0.15), inset 0 1.5px 1px #ffffff !important;
}
html.dark .macos-menu-item:hover {
  background: rgba(51, 65, 85, 0.7) !important;
  color: #38bdf8 !important;
  border-color: rgba(56, 189, 248, 0.3) !important;
}

/* 选中激活态：3D 冰海蓝宝石微晶药丸 (对齐素材 2ac2e3a9c1fd2371a185add9ac5a345a.jpg 的清透琉璃) */
.macos-menu-item.is-active {
  background: linear-gradient(180deg, rgba(224, 242, 254, 0.96) 0%, rgba(186, 230, 253, 0.9) 50%, rgba(125, 211, 252, 0.82) 100%) !important;
  color: #0284c7 !important;
  font-weight: 700 !important;
  border: 1.5px solid rgba(255, 255, 255, 0.98) !important;
  border-top: 2px solid #ffffff !important;
  border-bottom: 1.5px solid rgba(56, 189, 248, 0.65) !important;
  box-shadow:
    0 6px 16px -2px rgba(2, 132, 199, 0.28),
    0 2px 5px rgba(15, 23, 42, 0.04),
    inset 0 1.8px 2px #ffffff,
    inset 0 -1.5px 2px rgba(2, 132, 199, 0.15) !important;
  transform: translateY(-1px) !important;
}
html.dark .macos-menu-item.is-active {
  background: linear-gradient(180deg, rgba(2, 132, 199, 0.6) 0%, rgba(3, 105, 161, 0.75) 100%) !important;
  color: #ffffff !important;
  border-color: rgba(56, 189, 248, 0.7) !important;
  box-shadow: 0 6px 18px -2px rgba(2, 132, 199, 0.4), inset 0 1.5px 1px rgba(255, 255, 255, 0.35) !important;
}

/* 菜单图标通透微晶座舱 */
.menu-icon {
  font-size: 15px !important;
  margin-right: 9px !important;
  color: #64748b !important;
  background: rgba(255, 255, 255, 0.6);
  border: 1px solid rgba(255, 255, 255, 0.85);
  border-radius: 7px;
  width: 25px;
  height: 25px;
  display: inline-flex !important;
  align-items: center;
  justify-content: center;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.03), inset 0 1px 1px #ffffff;
  transition: all 0.16s ease !important;
  flex-shrink: 0;
}
html.dark .menu-icon {
  color: #94a3b8 !important;
  background: rgba(15, 23, 42, 0.6);
  border-color: rgba(255, 255, 255, 0.08);
}
.macos-menu-item:hover .menu-icon {
  background: #ffffff !important;
  color: #0284c7 !important;
  border-color: rgba(56, 189, 248, 0.6) !important;
  box-shadow: 0 2px 6px rgba(2, 132, 199, 0.18) !important;
}
.macos-menu-item.is-active .menu-icon {
  color: #0284c7 !important;
  background: rgba(255, 255, 255, 0.92) !important;
  border-color: rgba(255, 255, 255, 0.98) !important;
  border-top-color: #ffffff !important;
  box-shadow: 0 2px 6px rgba(2, 132, 199, 0.18), inset 0 1px 1px #ffffff !important;
}
html.dark .macos-menu-item.is-active .menu-icon {
  color: #ffffff !important;
  background: rgba(2, 132, 199, 0.6) !important;
  border-color: rgba(56, 189, 248, 0.4) !important;
}

.menu-label {
  letter-spacing: -0.01em;
  font-size: 12.5px;
}

/* 边栏底部 */
.sidebar-bottom {
  padding: 10px 12px 14px;
  border-top: 1.2px solid rgba(255, 255, 255, 0.8);
  flex-shrink: 0;
  background: rgba(255, 255, 255, 0.45);
  backdrop-filter: blur(16px);
}
html.dark .sidebar-bottom {
  border-top: 1.2px solid rgba(255, 255, 255, 0.08);
  background: rgba(15, 23, 42, 0.5);
}
.collapse-btn {
  width: 100%;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  background: rgba(255, 255, 255, 0.8);
  backdrop-filter: blur(12px);
  border: 1.2px solid rgba(255, 255, 255, 0.95);
  border-top: 1.6px solid #ffffff;
  border-radius: 9999px;
  color: #475569;
  font-size: 11.5px;
  font-weight: 700;
  cursor: pointer;
  outline: none;
  box-shadow: 0 2px 6px rgba(15, 23, 42, 0.04), inset 0 1px 1px #ffffff;
  transition: all 0.16s ease;
}
.collapse-btn:hover {
  background: #ffffff;
  color: #0284c7;
  border-color: #7dd3fc;
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(2, 132, 199, 0.16);
}
html.dark .collapse-btn {
  background: rgba(30, 41, 59, 0.8);
  border-color: rgba(255, 255, 255, 0.1);
  color: #94a3b8;
  box-shadow: none;
}
html.dark .collapse-btn:hover {
  background: rgba(51, 65, 85, 0.9);
  color: #38bdf8;
  border-color: #38bdf8;
}

/* ──────────── 右侧主体外壳与毛玻璃顶栏 ──────────── */
.macos-main-shell {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: transparent;
  overflow: hidden;
  position: relative;
  z-index: 1;
}

.macos-topbar {
  height: 48px !important;
  margin: 10px 10px 0 10px;
  padding: 0 16px !important;
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: rgba(255, 255, 255, 0.68);
  backdrop-filter: blur(28px) saturate(180%);
  -webkit-backdrop-filter: blur(28px) saturate(180%);
  border: 1.2px solid rgba(255, 255, 255, 0.92);
  border-top: 1.8px solid #ffffff;
  border-radius: 16px;
  box-shadow: 0 8px 24px -4px rgba(100, 116, 139, 0.08), inset 0 1.5px 1px #ffffff;
  flex-shrink: 0;
  z-index: 9;
}
html.dark .macos-topbar {
  background: rgba(15, 23, 42, 0.8);
  border-bottom: 1.5px solid rgba(255, 255, 255, 0.08);
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

/* ──────────── 3D 实体双层厚水晶 Spotlight 搜索框 (1:1 复刻素材图 Search projects...) ──────────── */
.macos-spotlight-search {
  width: 195px;
  position: relative;
  display: inline-flex;
  align-items: center;
  padding: 2px 3px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.5);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  border: 1.8px solid rgba(255, 255, 255, 0.95);
  border-top: 2.2px solid #ffffff;
  border-bottom: 1.8px solid rgba(203, 213, 225, 0.6);
  box-shadow:
    0 4px 12px -2px rgba(15, 23, 42, 0.06),
    inset 0 1.5px 1.5px #ffffff,
    inset 0 -1.5px 2px rgba(148, 163, 184, 0.18);
  transition: all 0.16s ease;
}
.macos-spotlight-search:hover {
  background: rgba(255, 255, 255, 0.7);
  border-color: rgba(56, 189, 248, 0.6);
  box-shadow:
    0 6px 16px -2px rgba(2, 132, 199, 0.15),
    inset 0 1.5px 1.5px #ffffff;
}
.macos-spotlight-search :deep(.el-select__wrapper) {
  background: rgba(255, 255, 255, 0.92) !important;
  border-radius: 8px !important;
  border: 1px solid rgba(226, 232, 240, 0.85) !important;
  box-shadow: inset 0 1px 2px rgba(15, 23, 42, 0.04) !important;
  height: 24px !important;
  min-height: 24px !important;
  padding: 0 6px !important;
}
.macos-spotlight-search :deep(.el-select__wrapper.is-focused) {
  background: #ffffff !important;
  border-color: #38bdf8 !important;
  box-shadow: 0 0 0 2px rgba(56, 189, 248, 0.2), inset 0 1px 2px rgba(15, 23, 42, 0.04) !important;
}

/* 状态指标胶囊群 (3D 通透水晶微胶囊，对齐素材舒适配色) */
.macos-stat-pills {
  display: flex;
  align-items: center;
  gap: 6px;
}
.stat-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 9px;
  border-radius: 9999px;
  font-size: 11px;
  background: rgba(255, 255, 255, 0.65);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  border: 1px solid rgba(255, 255, 255, 0.9);
  border-top: 1.5px solid #ffffff;
  box-shadow: 0 2px 6px rgba(15, 23, 42, 0.03), inset 0 1px 1px #ffffff;
  transition: all 0.16s ease;
}
.stat-pill:hover {
  background: rgba(255, 255, 255, 0.85);
  transform: translateY(-1px);
}
.stat-pill .pill-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #64748b;
  box-shadow: 0 0 5px currentColor;
}
.stat-pill .pill-label {
  color: #64748b;
  font-weight: 600;
}
.stat-pill .pill-val {
  font-weight: 700;
  font-family: -apple-system, BlinkMacSystemFont, "SFMono-Regular", Consolas, monospace;
  color: #0f172a;
}

.stat-pill.pill-info .pill-dot { background: #0284c7; color: #0284c7; }
.stat-pill.pill-info .pill-val { color: #0369a1; }
.stat-pill.pill-success .pill-dot { background: #10b981; color: #10b981; }
.stat-pill.pill-success .pill-val { color: #047857; }
.stat-pill.pill-warning .pill-dot { background: #f59e0b; color: #f59e0b; }
.stat-pill.pill-warning .pill-val { color: #b45309; }
.stat-pill.pill-primary .pill-dot { background: #3b82f6; color: #3b82f6; }
.stat-pill.pill-primary .pill-val { color: #1d4ed8; }
.stat-pill.pill-danger .pill-dot { background: #f43f5e; color: #f43f5e; }
.stat-pill.pill-danger .pill-val { color: #be123c; }

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
  background: var(--el-color-primary);
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
  background: transparent;
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
