import { createRouter, createWebHashHistory } from 'vue-router'
import NProgress from 'nprogress'

NProgress.configure({ showSpinner: false, trickleSpeed: 120, minimum: 0.15 })

// hash 路由：不依赖后端做 SPA 回退，FastAPI / 未来 Gin 都零配置可用。
const routes = [
  {
    path: '/',
    name: 'dashboard',
    component: () => import('@/views/Dashboard.vue'),
    meta: { title: '仪表盘', icon: 'Odometer', group: '概览' },
  },
  // ── 账号与数据管理 ──
  {
    path: '/registered',
    name: 'registered',
    component: () => import('@/views/Registered.vue'),
    meta: { title: '账号管理', icon: 'UserFilled', group: '账号' },
  },
  {
    path: '/pool',
    name: 'pool',
    component: () => import('@/views/Pool.vue'),
    meta: { title: '邮箱列表', icon: 'Files', group: '账号' },
  },
  {
    path: '/runs',
    name: 'runs',
    component: () => import('@/views/Runs.vue'),
    meta: { title: '运行记录', icon: 'Document', group: '账号' },
  },
  // ── 注册流水线 ──
  {
    path: '/auto',
    name: 'auto',
    component: () => import('@/views/AutoLoop.vue'),
    meta: { title: '全自动批量', icon: 'MagicStick', group: '流水线' },
  },
  {
    path: '/register',
    name: 'register',
    component: () => import('@/views/Register.vue'),
    meta: { title: '单次注册', icon: 'VideoPlay', group: '流水线' },
  },
  {
    path: '/import',
    name: 'import',
    component: () => import('@/views/Import.vue'),
    meta: { title: '导入邮箱', icon: 'Upload', group: '流水线' },
  },
  // ── Plus 提炼与代付 ──
  {
    path: '/extract',
    name: 'extract',
    component: () => import('@/views/Extract.vue'),
    meta: { title: 'Plus 提炼', icon: 'Link', group: '提炼' },
  },
  {
    path: '/paypal-pay',
    name: 'paypal_pay',
    component: () => import('@/views/PayPalPay.vue'),
    meta: { title: 'PayPal 协议支付', icon: 'CreditCard', group: '提炼' },
  },
  // ── 系统配置 ──
  {
    path: '/proxy',
    name: 'proxy',
    component: () => import('@/views/ProxyPool.vue'),
    meta: { title: '代理池管理', icon: 'Connection', group: '配置' },
  },
  {
    path: '/settings/mail',
    name: 'mail',
    component: () => import('@/views/MailConfig.vue'),
    meta: { title: '邮箱配置', icon: 'Message', group: '配置' },
  },
  {
    path: '/settings/sms',
    name: 'sms',
    component: () => import('@/views/SmsConfig.vue'),
    meta: { title: '接码配置', icon: 'Iphone', group: '配置' },
  },
  {
    path: '/settings/export',
    name: 'export',
    component: () => import('@/views/ExportConfig.vue'),
    meta: { title: '自动导出', icon: 'Share', group: '配置' },
  },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

// 路由切换顶部进度条
router.beforeEach((to, from, next) => {
  NProgress.start()
  if (to.meta?.title) document.title = `${to.meta.title} · 少司命`
  next()
})
router.afterEach(() => {
  NProgress.done()
})

export default router
