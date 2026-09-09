<script setup>
import { computed, onMounted, onUnmounted, onActivated, onDeactivated, ref, reactive, nextTick } from 'vue'
import { storeToRefs } from 'pinia'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
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
  Search,
  ArrowRight,
  Sunny,
  WarningFilled,
  DocumentCopy,
  CopyDocument,
  Filter,
  Message,
  Operation,
  Edit,
  Check,
  MoreFilled,
  Folder,
  Calendar,
  User,
} from '@element-plus/icons-vue'
import { useStatsStore } from '@/stores/stats'
import { useRuntimeStore } from '@/stores/runtime'
import { getDashboardSummary } from '@/api/accounts'
import { getSentinelPoolStats, getProxyHealthSummary } from '@/api/register'
import { COUNTRY_NAME_MAP, formatCountry } from '@/stores/form'
import { fmtTime } from '@/api/request'

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

// ════════════════ 9 大核心功能模块定义 (3D 环绕 / 侧向层叠场景) ════════════════
const modules = [
  {
    id: "SYS-AUTOLOOP-001", short: "跑号 · 舰队", title: "全自动并发跑号", symbol: "舰",
    subtitle: "多 Worker 自动化无人值守", state: "高频就绪", folder: "调度中心 / 自动舰队", priority: "最高",
    path: "/auto", date: "实时轮询调度", status: "运行状态 正常",
    description: "多协程并发跑号引擎，结合真实浏览器指纹库与住宅代理路由，支持自动化取件、PoW 碰撞及 2FA 强制绑定。",
    tags: ["并发 Worker", "指纹对齐", "PoW 预计算", "2FA 强制"],
    advice: ["当前号池存量充沛，推荐开启 5~10 并发 Worker 进行不间断跑号", "遇 409 限流时系统已自动启用 15min 冷冻隔离防风控"],
    accent: "green", completion: 98,
  },
  {
    id: "SYS-ASSETS-002", short: "资产 · 中枢", title: "账号资产管理中枢", symbol: "资",
    subtitle: "Token / 2FA / 密码 / 导出", state: "核心资产", folder: "资产中枢 / 注册结果", priority: "最高",
    path: "/registered", date: "秒级实时入库", status: "覆盖率 92%",
    description: "全量账号资产集中管理平台，支持 Access Token 极速置换、Session JSON 复制、CPA/Sub2API 导出及批次备注留痕。",
    tags: ["Token 凭证", "2FA TOTP", "官方设密", "多格式导出"],
    advice: ["已导出账号建议添加去向便签备注，便于日后随时追溯批次", "支持一键批量补设强随机密码，免除后续验证码登入繁琐流程"],
    accent: "ice", completion: 94,
  },
  {
    id: "SYS-POW-003", short: "算力 · 防御", title: "Sentinel PoW 预计算池", symbol: "算",
    subtitle: "0ms 瞬时取用 / 削峰填谷", state: "就绪 10 槽", folder: "防御引擎 / 算力缓冲", priority: "高",
    path: "/auto", date: "协程常驻预热", status: "0ms 命中率 99%",
    description: "后台守护协程预计算 OpenAI Sentinel Proof-of-Work，注册流水线免去 15s 客户端碰撞等待，平滑 CPU 瞬时波峰。",
    tags: ["PoW 预计算", "0ms 瞬时", "削峰填谷", "CPU 优化"],
    advice: ["保持预计算池目标深度为 10~20，可完全吸收并发注册时的突发算力需求", "若出现频控可适当调大超时阈值"],
    accent: "silver", completion: 99,
  },
  {
    id: "SYS-PROXY-004", short: "代理 · 路由", title: "动态住宅代理池", symbol: "网",
    subtitle: "健康评级 / 15min 自动冷冻", state: "实时防护", folder: "网络中枢 / 住宅路由", priority: "高",
    path: "/proxy-pool", date: "实时健康探测", status: "隔离冷冻 0",
    description: "出口代理智能健康度跟踪与风控隔离系统，一号一 IP 严格隔离，连续 409 代理自动熔断冷冻 15 分钟。",
    tags: ["住宅代理", "一号一IP", "409 冷冻", "智能换国"],
    advice: ["推荐优先配置支持 SOCKS5 / HTTP 的多国住宅代理池", "出现连续 409 限流会自动拉黑冷冻，无需人工干预"],
    accent: "warm", completion: 91,
  },
  {
    id: "SYS-REMAIL-005", short: "邮箱 · 枢纽", title: "Remail & 邮箱配置", symbol: "邮",
    subtitle: "Remail 暂存 / 微软 OAuth / CF", state: "0s 即取", folder: "邮箱枢纽 / 渠道配置", priority: "高",
    path: "/mail-config", date: "全协议支持", status: "暂存复用 0s",
    description: "全功能邮箱中枢，支持 Remail 失败复用免扣积分、微软 Graph OAuth 授权直连与 Cloudflare Worker 邮件提取。",
    tags: ["Remail 复用", "微软 OAuth", "CF Worker", "IMAP4"],
    advice: ["开启 Remail 暂存后，注册失败账号可无损复用 3 次，大幅节省接码成本", "微软 OAuth 拥有极高稳定性与收信速度"],
    accent: "mist", completion: 96,
  },
  {
    id: "SYS-OAUTH-006", short: "授权 · 接码", title: "Codex OAuth 授权导出", symbol: "码",
    subtitle: "Codex 接码 / 授权直通", state: "多渠道", folder: "接码流水 / OAuth 导出", priority: "中",
    path: "/sms-config", date: "按需接码授权", status: "直通授权",
    description: "Codex 及第三方平台 OAuth 自动授权工作台，支持短信平台自动租赁号码、收码与回填，实现免封直连。",
    tags: ["Codex 凭证", "短信接码", "OAuth 2.0", "一键直连"],
    advice: ["建议配置超时等待为 60~85 秒，避免 OpenAI 授权会话超时失效", "可锁定指定接码价格区间"],
    accent: "ice", completion: 88,
  },
  {
    id: "SYS-EXTRACT-007", short: "代付 · 提链", title: "全渠道提链出码代付", symbol: "链",
    subtitle: "PayPal / GCash / PIX / iDEAL", state: "秒级提链", folder: "提炼中枢 / 全球代付", priority: "高",
    path: "/extract", date: "多币种支持", status: "提链出码",
    description: "全渠道支付链接提取与自动代付流水线，覆盖 PayPal 一条龙、GCash、PIX、Hosted Invoice 等主流结账链路。",
    tags: ["PayPal 代付", "GCash 出码", "PIX 巴西", "订阅升级"],
    advice: ["推荐使用 PayPal Pipeline 进行一键提链加代付全自动流程", "提取链接后可随时批量导出至收银台"],
    accent: "silver", completion: 92,
  },
  {
    id: "SYS-HEALTH-008", short: "验活 · 探测", title: "账号批量并发验活", symbol: "验",
    subtitle: "Token 有效性 / Plus 资格", state: "并发探测", folder: "质检流水 / 验活中枢", priority: "中",
    path: "/registered", date: "高并发检测", status: "存活率 99.4%",
    description: "批量 Access Token 状态验活与订阅套餐（Plus / Pro / Team / Promo）深度探测，支持毫秒级异常标记与归档。",
    tags: ["Token 验活", "Plus 探测", "试用特权", "批量体检"],
    advice: ["大批量出号后可一键运行 Token 快速验活，过滤失效凭证", "支持使用代理池并发分散探测压力"],
    accent: "warm", completion: 97,
  },
  {
    id: "SYS-WARM-009", short: "保温 · 保鲜", title: "账号自动化保温保鲜", symbol: "温",
    subtitle: "官方会话交互 / 活跃度沉淀", state: "智能保鲜", folder: "生命周期 / 自动保温", priority: "中",
    path: "/registered", date: "周期性轮询", status: "活跃防封",
    description: "自动化与 OpenAI 官方模型发起多轮拟真对话，沉淀账号活跃度画像，显著降低批量冷号被风控扫荡的概率。",
    tags: ["模型交互", "活跃保鲜", "拟真对话", "防封加固"],
    advice: ["冷号建议每隔 3~7 天进行一次交互保温，保持会话活跃度", "可配合住宅代理模拟真实用户日常访问"],
    accent: "mist", completion: 86,
  },
]

// ════════════════ 任务队列分组定义 (左侧面板) ════════════════
const queueTabs = ref('all') // 'all' | 'running' | 'warning'
const openGroups = ref({ 0: true, 1: true, 2: false, 3: false })

const taskGroups = computed(() => [
  {
    name: "全自动注册舰队", count: autoStatus.value.concurrency || 3, color: "#2aa9e8",
    items: [
      { title: `Worker 舰队实时并发跑号`, badge: autoStatus.value.state === 'running' ? '运行中' : '待机', desc: `成功: ${autoStatus.value.registered_ok || 0} / 失败: ${autoStatus.value.registered_fail || 0}`, isRunning: autoStatus.value.state === 'running', path: '/auto' },
      { title: `真实浏览器指纹预热`, badge: '对齐中', desc: `TLS / UA / WebGL 多重拟真`, isRunning: false, path: '/auto' },
      { title: `号源与代理自愈调度`, badge: '实时', desc: `一号一IP · 智能换国`, isRunning: false, path: '/auto' },
    ]
  },
  {
    name: "安全加固与自愈", count: summaryData.value.registered.with_2fa || 0, color: "#23e2a0",
    items: [
      { title: `TOTP 2FA 强制补绑`, badge: `${summaryData.value.registered.sec_rate}%`, desc: `已保护 ${summaryData.value.registered.with_2fa} 个账号`, isRunning: false, path: '/registered' },
      { title: `官方免邮箱强密补设`, badge: `${summaryData.value.registered.with_pwd}`, desc: `已设密码账号无需验证码`, isRunning: false, path: '/registered' },
    ]
  },
  {
    name: "代理防御与算力", count: sentinelStats.value.current_size || 10, color: "#845ee7",
    items: [
      { title: `Sentinel PoW 预计算池`, badge: `${sentinelStats.value.current_size}/${sentinelStats.value.target_size}`, desc: `0ms 命中率 ${Math.round((sentinelStats.value.hit_rate || 0) * 100)}%`, isRunning: sentinelStats.value.enabled, path: '/auto' },
      { title: `409 出口 IP 智能冷冻`, badge: `${proxyHealthStats.value.cooling_down_count} 隔离`, desc: `15 分钟熔断冷却保护`, isRunning: false, path: '/proxy-pool' },
    ]
  },
  {
    name: "资产流转与导出", count: summaryData.value.registered.exported || 0, color: "#e69d23",
    items: [
      { title: `Codex OAuth 凭证导出`, badge: '已授权', desc: `标准 JSON / Sub2API`, isRunning: false, path: '/registered' },
      { title: `多渠道提链与出码`, badge: 'PayPal/PIX', desc: `全币种代付支持`, isRunning: false, path: '/extract' },
    ]
  },
])

function toggleGroup(idx) {
  openGroups.value[idx] = !openGroups.value[idx]
}

// ════════════════ 3D Carousel & Fan-View 场景数学与交互控制 ════════════════
const viewMode = ref('orbit') // 'orbit' | 'fan'
const selectedIndex = ref(0)
const isPlaying = ref(false) // 默认静止，降低能耗与内存开销；用户可随时一键播放
const speed = ref(2)
let animFrame = null
let visualPosition = 0
let targetPosition = 0
let lastFrameTime = 0
let lastWheelDirection = 1
let isDragging = ref(false)
let dragStartX = 0
let dragOriginPos = 0

const activeModule = computed(() => modules[selectedIndex.value] || modules[0])

function modulo(value, length = modules.length) {
  return ((value % length) + length) % length
}

function relativePosition(index, position = visualPosition) {
  let delta = index - modulo(position)
  const half = modules.length / 2
  if (delta > half) delta -= modules.length
  if (delta < -half) delta += modules.length
  return delta
}

function getCardStyle(index) {
  const delta = relativePosition(index, visualPosition)
  const absolute = Math.abs(delta)
  const isFan = viewMode.value === 'fan'

  const x = isFan ? -12 + delta * 46 : 85 + Math.sin(delta * 0.6) * 270
  const z = isFan ? (absolute < 0.08 ? 105 : 58 - Math.min(absolute, 4.5) * 7) : 80 - Math.min(absolute, 2.5) * 70
  const y = isFan ? 2 + Math.min(absolute, 4.5) * 2.5 : Math.min(absolute, 2.5) * 7
  const rotation = isFan
    ? (absolute < 0.08 ? -34 : -Math.min(64, 56 + absolute * 1.8))
    : -Math.sign(delta) * Math.min(22, absolute * 11)
  const scale = isFan ? (absolute < 0.08 ? 1.02 : Math.max(0.88, 0.98 - absolute * 0.018)) : Math.max(0.72, 1.02 - absolute * 0.12)

  const depthOpacity = isFan ? Math.max(0.52, 1 - absolute * 0.105) : Math.max(0.46, 1 - absolute * 0.22)
  const wrapFade = isFan ? (absolute > 3.8 ? Math.max(0.46, 1 - (absolute - 3.8) * 0.7) : 1) : (absolute > 2.15 ? Math.max(0, (2.5 - absolute) / 0.35) : 1)
  const cardOpacity = depthOpacity * wrapFade

  const zIndex = isFan && absolute < 0.08 ? 90 : Math.round(isFan ? 58 + delta * 2 : 30 - absolute * 8)

  return {
    transform: `translate3d(${x}px, ${y}px, ${z}px) rotateY(${rotation}deg) scale(${scale})`,
    opacity: cardOpacity,
    zIndex: String(zIndex),
    pointerEvents: cardOpacity < 0.08 ? 'none' : 'auto',
  }
}

function stopAnimation() {
  if (animFrame !== null) {
    cancelAnimationFrame(animFrame)
    animFrame = null
  }
  lastFrameTime = 0
}

function runLoop(now) {
  if (!lastFrameTime) lastFrameTime = now
  const elapsed = Math.min(34, now - lastFrameTime)
  lastFrameTime = now

  let needContinue = false

  // 1. 自动轮播推进（非拖拽状态下）
  if (isPlaying.value && !isDragging.value) {
    targetPosition -= elapsed * 0.00018 * speed.value
    needContinue = true
  }

  // 2. 指数缓动平滑跟踪
  const distance = targetPosition - visualPosition
  if (Math.abs(distance) > 0.002) {
    const smoothing = 1 - Math.exp(-elapsed / 170)
    visualPosition += distance * smoothing
    needContinue = true
  } else if (!isPlaying.value) {
    visualPosition = targetPosition
  }

  const visualIndex = modulo(Math.round(visualPosition))
  if (visualIndex !== selectedIndex.value) {
    selectedIndex.value = visualIndex
  }

  // 3. 按需递归，静止时自动休眠（零 CPU/GPU 开销）
  if (needContinue) {
    animFrame = requestAnimationFrame(runLoop)
  } else {
    animFrame = null
    lastFrameTime = 0
  }
}

function requestTick() {
  if (animFrame === null) {
    lastFrameTime = performance.now()
    animFrame = requestAnimationFrame(runLoop)
  }
}

function selectCard(idx, requestedDirection = 0) {
  const norm = modulo(idx)
  const delta = requestedDirection || relativePosition(norm, visualPosition)
  if (Math.abs(delta) < 0.001) {
    selectedIndex.value = norm
    visualPosition = norm
    targetPosition = norm
    return
  }
  targetPosition = visualPosition + delta
  requestTick()
}

function nextCard(dir = 1) {
  targetPosition = Math.round(targetPosition) + dir
  lastWheelDirection = dir
  requestTick()
}

function togglePlay() {
  isPlaying.value = !isPlaying.value
  if (isPlaying.value) {
    requestTick()
    showToast('已开启 3D 自动轮播')
  } else {
    stopAnimation()
    showToast('已暂停轮播')
  }
}

function toggleSpeed() {
  speed.value = speed.value === 2 ? 1 : 2
  showToast(`轮播速度已切换为 ${speed.value}×`)
}

function setViewMode(mode) {
  viewMode.value = mode
  showToast(mode === 'fan' ? '已切换为侧向层叠视图' : '已切换为 3D 环绕视图')
}

// 滚轮交互
function handleSceneWheel(e) {
  e.preventDefault()
  const delta = Math.sign(e.deltaY || e.deltaX)
  if (delta !== 0) {
    targetPosition += delta * 0.8
    lastWheelDirection = delta
    requestTick()
  }
}

// 拖拽手势交互
function handlePointerDown(e) {
  isDragging.value = true
  dragStartX = e.clientX || (e.touches && e.touches[0].clientX) || 0
  dragOriginPos = targetPosition
}

function handlePointerMove(e) {
  if (!isDragging.value) return
  const currentX = e.clientX || (e.touches && e.touches[0].clientX) || 0
  const diff = currentX - dragStartX
  targetPosition = dragOriginPos - diff * 0.005
  requestTick()
}

function handlePointerUp() {
  if (!isDragging.value) return
  isDragging.value = false
  targetPosition = Math.round(targetPosition)
  requestTick()
}

// ════════════════ 材质与强调色设置抽屉 (Material Overlay) ════════════════
const materialDrawerOpen = ref(false)
const selectedMetricIndex = ref(0)
const accentName = ref('ocean')

// 4 种现代东方护眼材质预设 (天水碧 / 凝脂 / 晴翠 / 远山冷玉)
const materialPalettes = {
  cyan: ["#7ebbc5", "#5da4b1", "#bfe3e8"],
  original: ["#eec9a8", "#f0d5be", "#e4b693"],
  rain: ["#88d4c3", "#6ec5b8", "#a7e5d8"],
  chrome: ["#9ec8d0", "#c2e1e7", "#7eaebb"],
}

const metricCardSettings = ref([
  { material: 'cyan', opacity: 100, blur: 20, flow: 150, colorA: '#7ebbc5', colorB: '#5da4b1', colorC: '#bfe3e8' },
  { material: 'original', opacity: 92, blur: 22, flow: 175, colorA: '#eec9a8', colorB: '#f0d5be', colorC: '#e4b693' },
  { material: 'rain', opacity: 85, blur: 24, flow: 130, colorA: '#88d4c3', colorB: '#6ec5b8', colorC: '#a7e5d8' },
  { material: 'chrome', opacity: 94, blur: 21, flow: 190, colorA: '#9ec8d0', colorB: '#c2e1e7', colorC: '#7eaebb' },
])

const currentSetting = computed(() => metricCardSettings.value[selectedMetricIndex.value])

function openMaterialDrawer(cardIdx = null) {
  if (cardIdx !== null) selectedMetricIndex.value = cardIdx
  materialDrawerOpen.value = true
}

function closeMaterialDrawer() {
  materialDrawerOpen.value = false
}

function applyAccent(acc) {
  accentName.value = acc
  document.documentElement.dataset.accent = acc
  try { localStorage.setItem("kaogong-workbench-accent", acc) } catch {}
  const names = { emerald: "翡翠", ocean: "静海", iris: "鸢尾", amber: "琥珀", sakura: "绯樱" }
  showToast(`界面强调色已切换为「${names[acc] || acc}」`)
}

function applyMaterialPreset(mat) {
  const p = materialPalettes[mat]
  if (p) {
    currentSetting.value.material = mat
    currentSetting.value.colorA = p[0]
    currentSetting.value.colorB = p[1]
    currentSetting.value.colorC = p[2]
  }
}

function resetMaterialSettings() {
  metricCardSettings.value[selectedMetricIndex.value] = {
    material: 'cyan', opacity: 100, blur: 20, flow: 150,
    colorA: '#27e8df', colorB: '#11a9c8', colorC: '#17346f',
  }
  showToast('已重置该卡片材质参数')
}

// ════════════════ Toast 提示 ════════════════
const toastText = ref('')
const toastVisible = ref(false)
let toastTimer = null

function showToast(msg) {
  toastText.value = msg
  toastVisible.value = true
  if (toastTimer) clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { toastVisible.value = false }, 1800)
}

function copyText(text, msg = '已复制') {
  if (!text) return
  navigator.clipboard?.writeText(text).then(() => {
    showToast(msg)
  }).catch(() => {
    ElMessage.info('复制操作已触发')
  })
}

// ════════════════ 快捷操作 ════════════════
function executeCurrentModule() {
  if (activeModule.value?.path) {
    router.push(activeModule.value.path)
  }
}

// ════════════════ 数据加载与轮询 ════════════════
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

let isDashboardActive = true

onMounted(() => {
  loadDashboardSummary()
})

onActivated(() => {
  isDashboardActive = true
  loadDashboardSummary()
  if (timer) clearInterval(timer)
  timer = setInterval(() => {
    if (isDashboardActive) loadDashboardSummary()
  }, 12000)
  if (isPlaying.value) {
    requestTick()
  }
})

onDeactivated(() => {
  isDashboardActive = false
  if (timer) {
    clearInterval(timer)
    timer = null
  }
  stopAnimation()
  if (toastTimer) {
    clearTimeout(toastTimer)
    toastTimer = null
  }
})

onUnmounted(() => {
  isDashboardActive = false
  if (timer) {
    clearInterval(timer)
    timer = null
  }
  stopAnimation()
  if (toastTimer) {
    clearTimeout(toastTimer)
    toastTimer = null
  }
})
</script>

<template>
  <div class="octopus-app-wrapper" :class="{ 'material-open': materialDrawerOpen }">
    <main class="octopus-workbench-shell">
      <!-- ════════════════ 顶部控制中枢 Header (Top Bar) ════════════════ -->
      <header class="topbar">
        <div class="page-title">
          <h1>OpenAI 自动化注册资产工作台</h1>
          <p>全自动并发舰队 · 算力防风控 · 资产自愈 · 全球出口拓扑</p>
        </div>

        <div class="search-box">
          <el-icon><Search /></el-icon>
          <input
            placeholder="搜索功能模块、邮箱、代理或任务条件…"
            @keyup.enter="showToast('已筛选相关模块')"
          />
          <kbd>⌘ K</kbd>
        </div>

        <div class="top-actions">
          <!-- 强调色快速切换 -->
          <div class="theme-control">
            <button
              class="round-btn theme-toggle"
              title="切换强调色 (静海 / 翡翠 / 鸢尾 / 琥珀 / 绯樱)"
              @click="openMaterialDrawer(null)"
            >
              <span class="theme-toggle-icon">✦</span>
            </button>
          </div>

          <!-- 流体材质设置抽屉按钮 -->
          <button
            class="round-btn filter-btn active"
            title="四张流体卡片材质与调色设置"
            @click="openMaterialDrawer(selectedMetricIndex)"
          >
            <el-icon><Filter /></el-icon>
          </button>

          <!-- 刷新概览 -->
          <button
            class="round-btn"
            :class="{ 'is-loading': summaryLoading }"
            title="刷新数据概览"
            @click="loadDashboardSummary"
          >
            <el-icon :class="{ 'is-spinning': summaryLoading }"><Refresh /></el-icon>
          </button>

          <!-- 核心行动按键: 一键批量并发 -->
          <button class="scan-btn" @click="router.push('/auto')">
            <el-icon><Compass /></el-icon>
            <span>一键批量并发</span>
          </button>
        </div>
      </header>

      <!-- ════════════════ 1. 核心四大 3D 流体卡片 (Metric Grid) ════════════════ -->
      <section class="metric-grid" aria-label="核心指标概览">
        <!-- Card 0: 号池库存总览 (Cyan) -->
        <article
          class="metric-card aurora"
          :class="{ selected: selectedMetricIndex === 0 }"
          :data-material="metricCardSettings[0].material"
          :style="{
            '--material-opacity': metricCardSettings[0].opacity / 100,
            '--material-blur': `${metricCardSettings[0].blur}px`,
            '--flow-duration': `${Math.max(2.2, 8 - metricCardSettings[0].flow / 50)}s`,
            '--mat-a': metricCardSettings[0].colorA,
            '--mat-b': metricCardSettings[0].colorB,
            '--mat-c': metricCardSettings[0].colorC,
          }"
          @click="selectedMetricIndex = 0"
          @dblclick="router.push('/pool')"
        >
          <span class="metric-select-mark" v-if="selectedMetricIndex === 0">✓ 已选择</span>
          <span>POOL / 号池总览</span>
          <h3>号池库存</h3>
          <strong>{{ stats.total || summaryData.pool.total || 0 }}<small>个</small></strong>
          <p>可用 {{ stats.available || summaryData.pool.available || 0 }} · <em>可用率 {{ Math.min(100, Math.round(((stats.available || summaryData.pool.available || 0) / (stats.total || summaryData.pool.total || 1)) * 100)) }}%</em></p>
        </article>

        <!-- Card 1: GPT 注册资产 (Original) -->
        <article
          class="metric-card aurora"
          :class="{ selected: selectedMetricIndex === 1 }"
          :data-material="metricCardSettings[1].material"
          :style="{
            '--material-opacity': metricCardSettings[1].opacity / 100,
            '--material-blur': `${metricCardSettings[1].blur}px`,
            '--flow-duration': `${Math.max(2.2, 8 - metricCardSettings[1].flow / 50)}s`,
            '--mat-a': metricCardSettings[1].colorA,
            '--mat-b': metricCardSettings[1].colorB,
            '--mat-c': metricCardSettings[1].colorC,
          }"
          @click="selectedMetricIndex = 1"
          @dblclick="router.push('/registered')"
        >
          <span class="metric-select-mark" v-if="selectedMetricIndex === 1">✓ 已选择</span>
          <span>ASSETS / 注册资产</span>
          <h3>GPT 资产库</h3>
          <strong>{{ summaryData.registered.total }}<small>个账号</small></strong>
          <p>2FA 保护 {{ summaryData.registered.with_2fa }} · <em>覆盖率 {{ summaryData.registered.sec_rate }}%</em></p>
        </article>

        <!-- Card 2: 自动化跑号舰队 (Rain) -->
        <article
          class="metric-card aurora"
          :class="{ selected: selectedMetricIndex === 2 }"
          :data-material="metricCardSettings[2].material"
          :style="{
            '--material-opacity': metricCardSettings[2].opacity / 100,
            '--material-blur': `${metricCardSettings[2].blur}px`,
            '--flow-duration': `${Math.max(2.2, 8 - metricCardSettings[2].flow / 50)}s`,
            '--mat-a': metricCardSettings[2].colorA,
            '--mat-b': metricCardSettings[2].colorB,
            '--mat-c': metricCardSettings[2].colorC,
          }"
          @click="selectedMetricIndex = 2"
          @dblclick="router.push('/auto')"
        >
          <span class="metric-select-mark" v-if="selectedMetricIndex === 2">✓ 已选择</span>
          <span>FLEET / 自动跑号</span>
          <h3>并发 Worker</h3>
          <strong>{{ autoStatus.concurrency || 1 }}<small>核并发</small></strong>
          <p>成功 {{ autoStatus.registered_ok || summaryData.pool.done || 0 }} · <em>成功率 {{ summaryData.registered.success_rate || 100 }}%</em></p>
        </article>

        <!-- Card 3: PoW 与算力防御 (Chrome) -->
        <article
          class="metric-card aurora"
          :class="{ selected: selectedMetricIndex === 3 }"
          :data-material="metricCardSettings[3].material"
          :style="{
            '--material-opacity': metricCardSettings[3].opacity / 100,
            '--material-blur': `${metricCardSettings[3].blur}px`,
            '--flow-duration': `${Math.max(2.2, 8 - metricCardSettings[3].flow / 50)}s`,
            '--mat-a': metricCardSettings[3].colorA,
            '--mat-b': metricCardSettings[3].colorB,
            '--mat-c': metricCardSettings[3].colorC,
          }"
          @click="selectedMetricIndex = 3"
          @dblclick="router.push('/proxy-pool')"
        >
          <span class="metric-select-mark" v-if="selectedMetricIndex === 3">✓ 已选择</span>
          <span>SENTINEL / 算力防风控</span>
          <h3>PoW 预计算</h3>
          <strong>{{ sentinelStats.current_size }}<small>槽位就绪</small></strong>
          <p>冷冻隔离 {{ proxyHealthStats.cooling_down_count }} · <em class="danger">0ms 瞬时取用</em></p>
        </article>
      </section>

      <!-- ════════════════ 2. 核心工作台网格 (左任务队列 + 中3D环绕 + 右详情面板) ════════════════ -->
      <div class="workspace-grid">
        <!-- ───── 左侧栏: 实时任务队列 (Queue Pane) ───── -->
        <section class="queue-pane">
          <div class="section-heading">
            <h2>实时任务队列</h2>
            <span>流水 · 阶段 · 耗时</span>
          </div>

          <div class="queue-tabs" role="tablist">
            <button :class="{ active: queueTabs === 'all' }" @click="queueTabs = 'all'">全部任务</button>
            <button :class="{ active: queueTabs === 'running' }" @click="queueTabs = 'running'">进行中</button>
            <button :class="{ active: queueTabs === 'warning' }" @click="queueTabs = 'warning'">风控隔离</button>
          </div>

          <div class="queue-groups">
            <article
              v-for="(grp, gIdx) in taskGroups"
              :key="grp.name"
              class="queue-group"
              :class="{ open: openGroups[gIdx] }"
            >
              <button
                class="group-head"
                :style="{ '--group-color': grp.color }"
                @click="toggleGroup(gIdx)"
              >
                <span>
                  <i></i>{{ grp.name }} <b>{{ grp.count }}</b>
                </span>
                <el-icon class="group-chevron"><ArrowRight /></el-icon>
              </button>

              <div class="group-items">
                <button
                  v-for="item in grp.items"
                  :key="item.title"
                  class="queue-item"
                  @click="router.push(item.path)"
                >
                  <span>{{ item.title }}</span>
                  <em :class="{ reading: item.isRunning }">{{ item.badge }}</em>
                  <small>{{ item.desc }}</small>
                </button>
              </div>
            </article>
          </div>
        </section>

        <!-- ───── 中间栏: 3D 环绕 / 侧向层叠模块演示 (Carousel Panel) ───── -->
        <section class="carousel-panel panel" aria-label="核心模块交互大屏">
          <div class="carousel-toolbar">
            <div class="toolbar-left-btns">
              <button
                class="pill-btn"
                :class="{ active: viewMode === 'orbit', quiet: viewMode !== 'orbit' }"
                @click="setViewMode('orbit')"
              >
                <span>✦</span> 3D 环绕
              </button>
              <button
                class="pill-btn"
                :class="{ active: isPlaying, playing: isPlaying }"
                @click="togglePlay"
              >
                <span>▶</span>
                <span class="play-label">{{ isPlaying ? '自动播放中' : '自动播放' }}</span>
              </button>
              <button
                class="pill-btn"
                :class="{ active: viewMode === 'fan', quiet: viewMode !== 'fan' }"
                @click="setViewMode('fan')"
              >
                <span>▥</span> 侧向层叠
              </button>
            </div>

            <div class="toolbar-right-btns">
              <button class="pill-btn" :class="{ active: speed === 2 }" @click="toggleSpeed">
                速度 {{ speed }}×
              </button>
              <button class="pill-btn quiet" @click="nextCard(-1)">‹ 前项</button>
              <button class="icon-btn" @click="nextCard(1)">›</button>
            </div>
          </div>

          <div class="live-badge"><i></i>实时交互</div>

          <!-- 3D 视口 -->
          <div
            class="carousel-viewport"
            :class="{ 'fan-view': viewMode === 'fan' }"
            tabindex="0"
            @wheel="handleSceneWheel"
            @mousedown="handlePointerDown"
            @mousemove="handlePointerMove"
            @mouseup="handlePointerUp"
            @touchstart="handlePointerDown"
            @touchmove="handlePointerMove"
            @touchend="handlePointerUp"
          >
            <div class="ambient-glow"></div>
            <div class="card-aura" aria-hidden="true"><i></i><b></b></div>

            <!-- 悬浮健康度小卡 -->
            <div class="completion-card">
              <span>模块就绪度</span>
              <strong>{{ activeModule.completion }}%</strong>
              <small>Sentinel 守护</small>
              <i><b :style="{ width: `${activeModule.completion}%` }"></b></i>
            </div>

            <!-- 3D 卡片场景 -->
            <div class="card-scene">
              <button
                v-for="(mod, idx) in modules"
                :key="mod.id"
                class="doc-card"
                :class="[mod.accent, { selected: idx === selectedIndex }]"
                :style="getCardStyle(idx)"
                @click="selectCard(idx)"
              >
                <span class="paper-shine"></span>
                <span class="doc-kicker">{{ mod.short }}</span>
                <span class="doc-lines"><i></i><i></i><i></i><i></i><i></i></span>
                <span class="doc-symbol">{{ mod.symbol }}</span>
                <span class="doc-copy">
                  <strong>{{ mod.title }}</strong>
                  <small>{{ mod.subtitle }} · {{ mod.state }}</small>
                </span>
              </button>
            </div>

            <div class="scroll-tip">
              <span class="mouse-icon"><i></i></span>
              <p>连续滚动或拖拽切换核心模块；点击卡片展开详情</p>
              <strong>{{ activeModule.title }}</strong>
            </div>
          </div>

          <!-- 底部出号时间轴与操作栏 -->
          <div class="timeline-panel">
            <div class="timeline-head">
              <b>出号全链路节拍</b>
              <span><el-icon><Calendar /></el-icon> 24 小时出号节律</span>
              <div>
                <button @click="showToast('已切换为实时节律')">实时⌄</button>
                <button @click="showToast('查看今日出号计划')">今日</button>
              </div>
            </div>

            <div class="timeline-dates">
              <button
                v-for="(m, mIdx) in modules"
                :key="m.id"
                :class="{ active: mIdx === selectedIndex }"
                @click="selectCard(mIdx)"
              >
                <i v-if="mIdx === selectedIndex">{{ m.symbol }}</i>
                0{{ mIdx + 1 }}
              </button>
            </div>

            <div class="timeline-track">
              <i :style="{ width: `${25 + selectedIndex * (56 / Math.max(1, modules.length - 1))}%` }"></i>
            </div>

            <div class="scene-actions">
              <button @click="openMaterialDrawer(selectedMetricIndex)">配置策略</button>
              <button class="primary" @click="router.push('/auto')">启动跑号</button>
              <button @click="router.push('/registered')">导出资产</button>
              <button @click="executeCurrentModule">进入当前模块</button>
            </div>
          </div>
        </section>

        <!-- ───── 右侧栏: 模块运行详情 (Details Panel) ───── -->
        <aside class="details-panel panel">
          <div class="details-heading">
            <h2>✦ 模块运行详情</h2>
            <button title="前往该模块" @click="executeCurrentModule">
              <el-icon><Folder /></el-icon>
            </button>
          </div>

          <div class="details-content">
            <code>{{ activeModule.id }}</code>
            <div class="detail-title-row">
              <h2>{{ activeModule.title }}</h2>
              <span>{{ activeModule.status }}</span>
            </div>
            <p class="detail-description">{{ activeModule.description }}</p>

            <dl class="metadata">
              <div>
                <dt>负责人</dt>
                <dd><span class="mini-avatar">GPT</span>yhm / 管理员</dd>
              </div>
              <div>
                <dt>所属目录</dt>
                <dd><el-icon><Folder /></el-icon><span>{{ activeModule.folder }}</span></dd>
              </div>
              <div>
                <dt>调度节点</dt>
                <dd><el-icon><Calendar /></el-icon><span>{{ activeModule.date }}</span></dd>
              </div>
              <div>
                <dt>当前状态</dt>
                <dd><i class="status-dot"></i><span>{{ activeModule.state }}</span></dd>
              </div>
              <div>
                <dt>优先级</dt>
                <dd><i class="priority-dot"></i><span>{{ activeModule.priority }}</span></dd>
              </div>
              <div>
                <dt>核心标签</dt>
                <dd class="tag-list">
                  <span v-for="tag in activeModule.tags" :key="tag">{{ tag }}</span>
                </dd>
              </div>
            </dl>

            <article class="ai-note">
              <h3>🛡️ 智能风控策略建议</h3>
              <ul>
                <li v-for="(adv, aIdx) in activeModule.advice" :key="aIdx">{{ adv }}</li>
              </ul>
              <button @click="executeCurrentModule">打开该功能工作台</button>
            </article>
          </div>

          <footer class="detail-actions">
            <button @click="openMaterialDrawer(selectedMetricIndex)">
              <el-icon><Edit /></el-icon> 调整配置
            </button>
            <button class="primary" @click="executeCurrentModule">
              <el-icon><Check /></el-icon> 立即执行
            </button>
            <button class="more" @click="showToast('已激活高级调试')">
              <el-icon><MoreFilled /></el-icon>
            </button>
          </footer>
        </aside>
      </div>
    </main>

    <!-- ════════════════ 3. 材质与强调色设置抽屉 (Material Overlay) ════════════════ -->
    <div class="material-overlay" :class="{ open: materialDrawerOpen }">
      <aside class="material-drawer panel" role="dialog" aria-modal="true">
        <header class="material-heading">
          <div>
            <span>FLUID GLASS MATERIAL</span>
            <h2>四张流体卡片设置</h2>
          </div>
          <button @click="closeMaterialDrawer">×</button>
        </header>

        <div class="material-body">
          <label class="material-select">
            <span>当前调节卡片</span>
            <select v-model="selectedMetricIndex">
              <option :value="0">号池库存总览 (Cyan)</option>
              <option :value="1">GPT 注册资产 (Original)</option>
              <option :value="2">全自动跑号引擎 (Rain)</option>
              <option :value="3">PoW 算力防风控 (Chrome)</option>
            </select>
          </label>

          <!-- 强调色选择 -->
          <section class="accent-section">
            <div class="accent-section-heading">
              <div>
                <h3>系统界面强调色</h3>
                <p>按统一明暗比例同步按钮、状态灯、光晕与选中辉光</p>
              </div>
              <output>{{ accentName }}</output>
            </div>
            <div class="accent-swatches">
              <button
                class="accent-swatch"
                :class="{ active: accentName === 'emerald' }"
                @click="applyAccent('emerald')"
              >
                <i></i><span>翡翠</span>
              </button>
              <button
                class="accent-swatch"
                :class="{ active: accentName === 'ocean' }"
                @click="applyAccent('ocean')"
              >
                <i></i><span>静海</span>
              </button>
              <button
                class="accent-swatch"
                :class="{ active: accentName === 'iris' }"
                @click="applyAccent('iris')"
              >
                <i></i><span>鸢尾</span>
              </button>
              <button
                class="accent-swatch"
                :class="{ active: accentName === 'amber' }"
                @click="applyAccent('amber')"
              >
                <i></i><span>琥珀</span>
              </button>
              <button
                class="accent-swatch"
                :class="{ active: accentName === 'sakura' }"
                @click="applyAccent('sakura')"
              >
                <i></i><span>绯樱</span>
              </button>
            </div>
          </section>

          <!-- 实时预览卡片 -->
          <div
            class="material-preview"
            :data-material="currentSetting.material"
            :style="{
              '--material-opacity': currentSetting.opacity / 100,
              '--material-blur': `${currentSetting.blur}px`,
              '--flow-duration': `${Math.max(2.2, 8 - currentSetting.flow / 50)}s`,
              '--mat-a': currentSetting.colorA,
              '--mat-b': currentSetting.colorB,
              '--mat-c': currentSetting.colorC,
            }"
          >
            <small>LIVE PREVIEW</small>
            <strong>卡片 #{{ selectedMetricIndex + 1 }} 实时预览</strong>
            <span>动态实时调整 · 边框与微光发生同步变化</span>
          </div>

          <!-- 配色预设 -->
          <section class="material-section">
            <h3>配色预设</h3>
            <div class="material-swatches">
              <button
                class="material-swatch"
                :class="{ active: currentSetting.material === 'cyan' }"
                @click="applyMaterialPreset('cyan')"
              >
                <i style="background: radial-gradient(circle at 70% 20%, #bfe3e8, transparent 38%), linear-gradient(135deg,#7ebbc5,#5da4b1);"></i>
                <span>Cyan</span>
              </button>
              <button
                class="material-swatch"
                :class="{ active: currentSetting.material === 'original' }"
                @click="applyMaterialPreset('original')"
              >
                <i style="background: radial-gradient(circle at 70% 35%,#f6dfcb,transparent 36%), linear-gradient(135deg,#f0d5be,#eec9a8);"></i>
                <span>Original</span>
              </button>
              <button
                class="material-swatch"
                :class="{ active: currentSetting.material === 'rain' }"
                @click="applyMaterialPreset('rain')"
              >
                <i style="background: radial-gradient(circle at 78% 60%,#a7e5d8,transparent 35%), linear-gradient(135deg,#88d4c3,#6ec5b8);"></i>
                <span>Rain</span>
              </button>
              <button
                class="material-swatch"
                :class="{ active: currentSetting.material === 'chrome' }"
                @click="applyMaterialPreset('chrome')"
              >
                <i style="background: radial-gradient(circle at 70% 20%,#c2e1e7,transparent 32%), linear-gradient(135deg,#9ec8d0,#7eaebb);"></i>
                <span>Chrome</span>
              </button>
            </div>
          </section>

          <!-- 自定义颜色 -->
          <section class="material-section">
            <h3>自定义颜色</h3>
            <div class="color-fields">
              <label>
                <span>主色 A</span>
                <input type="color" v-model="currentSetting.colorA">
                <small>{{ currentSetting.colorA }}</small>
              </label>
              <label>
                <span>辅色 B</span>
                <input type="color" v-model="currentSetting.colorB">
                <small>{{ currentSetting.colorB }}</small>
              </label>
              <label>
                <span>阴影 C</span>
                <input type="color" v-model="currentSetting.colorC">
                <small>{{ currentSetting.colorC }}</small>
              </label>
            </div>
          </section>

          <!-- 材质滑块 -->
          <section class="material-section material-controls">
            <h3>卡片材质与流速</h3>
            <label>
              <span>面板不透明度 <output>{{ currentSetting.opacity }}%</output></span>
              <input type="range" min="45" max="100" v-model.number="currentSetting.opacity">
            </label>
            <label>
              <span>背景模糊强度 <output>{{ currentSetting.blur }}px</output></span>
              <input type="range" min="4" max="30" v-model.number="currentSetting.blur">
            </label>
            <label>
              <span>流动速度 <output>{{ (currentSetting.flow / 100).toFixed(2) }}×</output></span>
              <input type="range" min="50" max="300" v-model.number="currentSetting.flow">
            </label>
          </section>
        </div>

        <footer class="material-actions">
          <button @click="resetMaterialSettings">重置参数</button>
          <button @click="closeMaterialDrawer">恢复初始</button>
          <button class="primary" @click="closeMaterialDrawer">保存设置</button>
        </footer>
      </aside>
    </div>

    <!-- 提示条 -->
    <div class="toast" :class="{ show: toastVisible }" role="status">
      {{ toastText }}
    </div>
  </div>
</template>

<style scoped>
/* ──────────── 天水碧 × 凝脂/冷白玉润 现代极简护眼体系 ──────────── */
.octopus-app-wrapper {
  --bg: #f4f7f6;
  --panel: #ffffff;
  --panel-2: #fbfdfd;
  --panel-soft: #edf6f8;
  --line: rgba(93, 164, 177, 0.18);
  --line-bright: rgba(93, 164, 177, 0.28);
  --text: #1a3c42;
  --muted: #657e82;
  --accent-h: 189;
  --accent-s: 36%;
  --accent-rgb: 93 164 177;
  --accent-foreground: #ffffff;
  --accent-300: #7ebbc5;
  --accent-500: #5da4b1;
  --accent-600: #488793;
  --accent-700: #396e78;
  --accent-800: #295159;
  --accent-900: #1a3c42;
  --green: #5da4b1;
  --danger: #c7564d;
  --radius: 12px;
  --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;

  font-family: var(--font);
  color: var(--text);
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  position: relative;
  background: var(--bg);
}

.octopus-workbench-shell {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-rows: 54px 150px minmax(0, 1fr);
  gap: 12px;
  padding: 12px 18px;
  overflow: hidden;
  transition: filter 0.34s ease, transform 0.46s cubic-bezier(0.16, 1, 0.3, 1);
}

.material-open .octopus-workbench-shell {
  filter: blur(2px) saturate(0.85);
  transform: scale(0.996);
}

.panel {
  background: #ffffff;
  border: 1px solid rgba(93, 164, 177, 0.2);
  border-radius: var(--radius);
  box-shadow: 0 4px 16px -2px rgba(35, 75, 82, 0.05), 0 1px 3px rgba(35, 75, 82, 0.03);
}

/* ════════════════ Topbar ════════════════ */
.topbar {
  display: grid;
  grid-template-columns: minmax(270px, 0.9fr) minmax(320px, 1.2fr) auto;
  gap: 16px;
  align-items: center;
}

.page-title h1 {
  margin: 0;
  font-size: 20px;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: #1a3c42;
}
.page-title p {
  margin: 3px 0 0;
  color: #657e82;
  font-size: 11.5px;
  letter-spacing: 0.01em;
}

.search-box {
  height: 38px;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 14px;
  border: 1px solid rgba(93, 164, 177, 0.25);
  background: #ffffff;
  border-radius: 20px;
  box-shadow: 0 1px 4px rgba(35, 75, 82, 0.04);
  transition: all 0.16s ease;
}
.search-box:focus-within {
  border-color: #5da4b1;
  box-shadow: 0 0 0 2px rgba(93, 164, 177, 0.2);
}
.search-box .el-icon {
  color: #657e82;
  font-size: 15px;
}
.search-box input {
  flex: 1;
  min-width: 0;
  border: 0;
  outline: 0;
  color: #1a3c42;
  background: transparent;
  font-size: 12.5px;
}
.search-box input::placeholder {
  color: #8c9ea0;
}
.search-box kbd {
  color: #21474e;
  background: #edf6f8;
  border: 1px solid rgba(93, 164, 177, 0.25);
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 10.5px;
  font-family: var(--el-font-family-monospace, monospace);
}

.top-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.round-btn, .scan-btn {
  border: 1px solid rgba(93, 164, 177, 0.28);
  background: #ffffff;
  cursor: pointer;
  color: #21474e;
  transition: all 0.16s ease;
}
.round-btn {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  display: grid;
  place-items: center;
}
.round-btn:hover {
  border-color: #5da4b1;
  background: #edf6f8;
  color: #1a3c42;
  transform: translateY(-1px);
}
.round-btn.active {
  color: #ffffff;
  border-color: #5da4b1;
  background: #5da4b1;
  box-shadow: 0 2px 6px rgba(93, 164, 177, 0.3);
}

.theme-toggle-icon {
  font-size: 15px;
  font-weight: 700;
  color: #5da4b1;
}
.round-btn.active .theme-toggle-icon {
  color: #ffffff;
}

.scan-btn {
  height: 36px;
  border-radius: 18px;
  padding: 0 16px;
  display: flex;
  align-items: center;
  gap: 8px;
  color: #ffffff;
  background: #5da4b1;
  border-color: #488793;
  font-size: 12.5px;
  font-weight: 600;
  box-shadow: 0 2px 8px rgba(93, 164, 177, 0.25);
}
.scan-btn:hover {
  background: #488793;
  box-shadow: 0 4px 14px rgba(93, 164, 177, 0.35);
}

.is-spinning {
  animation: spin 1s linear infinite;
}
@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

/* ════════════════ 1. Metric Grid ════════════════ */
.metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.metric-card {
  --metric-rx: 0deg;
  --metric-ry: 0deg;
  --metric-lift: 0px;
  --metric-scale: 1;
  position: relative;
  overflow: hidden;
  border-radius: 14px;
  padding: 16px 20px;
  border: 1px solid rgba(93, 164, 177, 0.22);
  background: #ffffff;
  isolation: isolate;
  box-shadow: 0 2px 8px rgba(35, 75, 82, 0.04);
  cursor: pointer;
  user-select: none;
  transition: transform 0.25s cubic-bezier(0.16, 1, 0.3, 1), border-color 0.2s ease, box-shadow 0.25s ease;
}

.metric-card::before {
  content: "";
  position: absolute;
  inset: -27%;
  z-index: -2;
  filter: blur(var(--material-blur, 20px));
  opacity: var(--material-opacity, 0.9);
  animation: metricFluidDrift var(--flow-duration, 8s) cubic-bezier(0.45, 0.05, 0.55, 0.95) infinite alternate;
  will-change: transform;
  transform: translateZ(0);
}

.metric-card::after {
  content: "";
  position: absolute;
  inset: 0;
  z-index: -1;
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.85) 0%, rgba(255, 255, 255, 0.6) 100%);
  opacity: 0.88;
}

.metric-card:hover {
  transform: translateY(-2px);
  border-color: #5da4b1;
  box-shadow: 0 8px 24px rgba(35, 75, 82, 0.08);
}

.metric-card.selected {
  border-color: #5da4b1;
  box-shadow: 0 0 0 1.5px #5da4b1, 0 8px 24px rgba(93, 164, 177, 0.16);
}

.metric-select-mark {
  position: absolute;
  z-index: 3;
  top: 12px;
  right: 12px;
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: 999px;
  color: #ffffff;
  background: #5da4b1;
  font-size: 9px;
  font-weight: 600;
  box-shadow: 0 1px 4px rgba(93, 164, 177, 0.3);
}

/* 4 种东方纯雅流体卡片底色 (温润如玉、绝无深黑杂色) */
.metric-card[data-material="cyan"] { background: #f0f7f8; color: #1a3c42; }
.metric-card[data-material="cyan"]::before {
  background: radial-gradient(circle at 76% 28%, #7ebbc5 0 14%, transparent 35%),
              radial-gradient(circle at 45% 73%, #5da4b1 0 12%, transparent 35%),
              radial-gradient(circle at 25% 30%, #bfe3e8 0 10%, transparent 32%), #f0f7f8;
}

.metric-card[data-material="original"] { background: #fbf9f5; color: #1a3c42; }
.metric-card[data-material="original"]::before {
  background: radial-gradient(circle at 86% 36%, rgba(238, 201, 168, 0.7) 0 14%, transparent 35%),
              radial-gradient(circle at 68% 78%, rgba(240, 213, 190, 0.7) 0 12%, transparent 34%),
              radial-gradient(circle at 91% 20%, rgba(228, 182, 147, 0.6) 0 10%, transparent 30%);
}

.metric-card[data-material="rain"] { background: #f2f8f6; color: #1a3c42; }
.metric-card[data-material="rain"]::before {
  background: radial-gradient(circle at 75% 70%, #88d4c3 0 14%, transparent 36%),
              radial-gradient(circle at 43% 24%, #6ec5b8 0 12%, transparent 35%),
              radial-gradient(circle at 18% 74%, #a7e5d8 0 10%, transparent 35%), #f2f8f6;
}

.metric-card[data-material="chrome"] { background: #f4f7f8; color: #1a3c42; }
.metric-card[data-material="chrome"]::before {
  background: radial-gradient(circle at 74% 28%, #c2e1e7 0 12%, transparent 34%),
              radial-gradient(circle at 47% 74%, #9ec8d0 0 12%, transparent 36%),
              radial-gradient(circle at 22% 28%, #7eaebb 0 10%, transparent 34%), #f4f7f8;
}

.metric-card > span {
  font-size: 10px;
  font-weight: 600;
  font-family: var(--el-font-family-monospace, monospace);
  color: #657e82;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.metric-card h3 {
  margin: 4px 0 6px;
  font-size: 15px;
  font-weight: 700;
  color: #1a3c42;
}
.metric-card strong {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 32px;
  font-weight: 700;
  font-family: var(--el-font-family-monospace, monospace);
  line-height: 1;
  color: #1a3c42;
}
.metric-card strong small {
  font-size: 11px;
  font-weight: 500;
  color: #657e82;
}
.metric-card p {
  margin: 6px 0 0;
  font-size: 11px;
  color: #5a767b;
}
.metric-card em {
  color: #28646e;
  font-style: normal;
  font-weight: 600;
}
.metric-card .danger {
  color: #c7564d;
}

@keyframes metricFluidDrift {
  0% { transform: scale(1) rotate(0deg); }
  100% { transform: scale(1.08) rotate(6deg); }
}

/* ════════════════ 2. Workspace Grid ════════════════ */
.workspace-grid {
  display: grid;
  grid-template-columns: 290px minmax(500px, 1fr) 340px;
  gap: 12px;
  min-height: 0;
  overflow: hidden;
}

/* ── 左侧栏: 任务队列 ── */
.queue-pane {
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.section-heading {
  height: 34px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.section-heading h2 {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: #1a3c42;
}
.section-heading span {
  color: #7b9498;
  font-size: 11px;
}

.queue-tabs {
  display: flex;
  gap: 6px;
  height: 34px;
  border-bottom: 1px solid rgba(93, 164, 177, 0.16);
}
.queue-tabs button {
  height: 28px;
  padding: 0 10px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: #657e82;
  font-size: 11.5px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}
.queue-tabs button.active {
  color: #1a3c42;
  font-weight: 600;
  background: #ffffff;
  border-color: rgba(93, 164, 177, 0.28);
  box-shadow: 0 1px 3px rgba(35, 75, 82, 0.05);
}

.queue-groups {
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  overflow-y: auto;
  scrollbar-width: none;
}
.queue-groups::-webkit-scrollbar { display: none; }

.queue-group {
  border-radius: 8px;
  border: 1px solid rgba(93, 164, 177, 0.18);
  background: #ffffff;
  overflow: hidden;
  box-shadow: 0 2px 6px rgba(35, 75, 82, 0.02);
}

.group-head {
  width: 100%;
  height: 36px;
  padding: 0 12px;
  border: 0;
  background: #f8faf9;
  display: flex;
  justify-content: space-between;
  align-items: center;
  color: #1a3c42;
  font-weight: 600;
  cursor: pointer;
  font-size: 12px;
}
.group-head > span {
  display: flex;
  align-items: center;
  gap: 7px;
}
.group-head i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--group-color);
  box-shadow: 0 0 6px var(--group-color);
}
.group-head b {
  color: #657e82;
  font-weight: 500;
  font-size: 11px;
}
.group-chevron {
  font-size: 12px;
  color: #7b9498;
  transition: transform 0.2s ease;
}
.queue-group.open .group-chevron {
  transform: rotate(90deg);
}

.group-items {
  display: none;
}
.queue-group.open .group-items {
  display: block;
}

.queue-item {
  position: relative;
  width: 100%;
  height: 44px;
  border: 0;
  border-top: 1px solid rgba(93, 164, 177, 0.08);
  padding: 0 12px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  background: #ffffff;
  color: #324e53;
  text-align: left;
  cursor: pointer;
  transition: background 0.15s ease;
}
.queue-item:hover {
  background: #edf6f8;
  color: #1a3c42;
}
.queue-item span {
  font-size: 11px;
  font-weight: 500;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.queue-item em {
  justify-self: end;
  color: #21474e;
  background: #edf6f8;
  border: 1px solid rgba(93, 164, 177, 0.3);
  border-radius: 4px;
  font-size: 9.5px;
  padding: 2px 6px;
  font-style: normal;
  font-weight: 600;
}
.queue-item em.reading {
  color: #1a454d;
  background: rgba(93, 164, 177, 0.18);
  border-color: #5da4b1;
}
.queue-item small {
  position: absolute;
  right: 12px;
  bottom: 2px;
  color: #7b9498;
  font-size: 8.5px;
}

/* ── 中间栏: 3D 环绕 / 侧向层叠 (Carousel Panel) ── */
.carousel-panel {
  position: relative;
  min-height: 0;
  overflow: hidden;
  border-radius: 12px;
  display: grid;
  grid-template-rows: 48px minmax(260px, 1fr) 110px;
  background: #ffffff;
}

.carousel-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 14px;
  border-bottom: 1px solid rgba(93, 164, 177, 0.14);
  background: #ffffff;
}
.toolbar-left-btns, .toolbar-right-btns {
  display: flex;
  gap: 6px;
}

.pill-btn, .icon-btn {
  height: 28px;
  border: 1px solid rgba(93, 164, 177, 0.25);
  border-radius: 6px;
  background: #ffffff;
  color: #21474e;
  font-size: 11px;
  font-weight: 500;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 0 10px;
  transition: all 0.15s ease;
}
.pill-btn:hover, .icon-btn:hover {
  background: #edf6f8;
  border-color: #5da4b1;
  color: #1a3c42;
}
.pill-btn.active {
  color: #ffffff;
  background: #5da4b1;
  border-color: #5da4b1;
  font-weight: 600;
  box-shadow: 0 2px 6px rgba(93, 164, 177, 0.25);
}
.pill-btn.quiet {
  color: #657e82;
}
.icon-btn {
  width: 28px;
  padding: 0;
  justify-content: center;
  font-size: 14px;
}

.live-badge {
  position: absolute;
  z-index: 30;
  right: 16px;
  top: 56px;
  height: 24px;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 9px;
  border-radius: 6px;
  background: #ffffff;
  border: 1px solid rgba(93, 164, 177, 0.3);
  color: #1a454d;
  font-size: 10px;
  font-weight: 600;
  box-shadow: 0 2px 8px rgba(35, 75, 82, 0.05);
}
.live-badge i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #5da4b1;
  box-shadow: 0 0 6px #5da4b1;
}

.carousel-viewport {
  position: relative;
  min-height: 0;
  overflow: hidden;
  outline: 0;
  cursor: grab;
  background: radial-gradient(ellipse at 50% 60%, rgba(93, 164, 177, 0.08), transparent 50%), #f8faf9;
  perspective: 1050px;
  user-select: none;
}
.carousel-viewport:active {
  cursor: grabbing;
}
.carousel-viewport::before {
  content: "";
  position: absolute;
  inset: 0;
  background-image: linear-gradient(rgba(93, 164, 177, 0.05) 1px, transparent 1px), linear-gradient(90deg, rgba(93, 164, 177, 0.05) 1px, transparent 1px);
  background-size: 32px 32px;
  mask-image: linear-gradient(to bottom, transparent, black 24%, black 75%, transparent);
}

.ambient-glow {
  position: absolute;
  z-index: 0;
  left: 50%;
  bottom: 25px;
  width: 320px;
  height: 60px;
  transform: translateX(-50%);
  background: rgba(93, 164, 177, 0.2);
  filter: blur(50px);
  border-radius: 50%;
}

.completion-card {
  position: absolute;
  z-index: 26;
  left: 6%;
  top: 10%;
  width: 115px;
  height: 125px;
  padding: 14px 12px;
  border-radius: 10px;
  color: #1a3c42;
  transform: rotate(-5deg);
  background: rgba(255, 255, 255, 0.94);
  border: 1px solid rgba(93, 164, 177, 0.28);
  box-shadow: 0 8px 24px rgba(35, 75, 82, 0.08);
  backdrop-filter: blur(12px);
}
.completion-card span, .completion-card small { display: block; font-size: 9px; color: #657e82; }
.completion-card strong { display: block; margin-top: 4px; font: 700 26px/1 var(--el-font-family-monospace, monospace); color: #1a3c42; }
.completion-card small { margin-top: 3px; color: #7b9498; }
.completion-card > i { display: block; height: 4px; margin-top: 10px; background: rgba(93, 164, 177, 0.15); border-radius: 4px; overflow: hidden; }
.completion-card b { display: block; height: 100%; background: #5da4b1; box-shadow: 0 0 6px #5da4b1; transition: width 0.5s ease; }

.card-scene {
  position: absolute;
  left: 50%;
  top: 48%;
  width: 600px;
  height: 220px;
  transform: translate(-50%, -45%);
  transform-style: preserve-3d;
}

.doc-card {
  position: absolute;
  left: calc(50% - 65px);
  top: 10px;
  width: 130px;
  height: 188px;
  border-radius: 12px;
  padding: 0;
  overflow: hidden;
  transform-origin: center bottom;
  background: linear-gradient(150deg, #ffffff, #edf5f6);
  border: 1px solid rgba(93, 164, 177, 0.28);
  box-shadow: 0 16px 32px rgba(35, 75, 82, 0.1);
  color: #1a3c42;
  cursor: pointer;
  backface-visibility: hidden;
  transition: opacity 0.3s ease, filter 0.3s ease, border-color 0.25s ease, box-shadow 0.3s ease;
  will-change: transform;
  transform-style: preserve-3d;
}
.doc-card.selected {
  color: #ffffff;
  background: linear-gradient(145deg, #7ebbc5 0%, #5da4b1 55%, #488793 100%);
  border-color: #bfe3e8;
  box-shadow: 0 20px 42px rgba(35, 75, 82, 0.22), 0 0 28px rgba(93, 164, 177, 0.35);
}

.paper-shine {
  position: absolute;
  inset: 0;
  background: radial-gradient(circle at 78% 9%, rgba(255, 255, 255, 0.6) 0 2px, transparent 3px), linear-gradient(120deg, rgba(255, 255, 255, 0.25), transparent 28%);
}
.doc-kicker {
  position: absolute;
  top: 10px;
  left: 12px;
  font: 600 8.5px/1.2 var(--el-font-family-monospace, monospace);
  opacity: 0.75;
}
.doc-lines {
  position: absolute;
  top: 38px;
  left: 16px;
  right: 16px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  opacity: 0.22;
}
.doc-lines i { height: 2px; border-radius: 2px; background: currentColor; }
.doc-lines i:nth-child(2) { width: 88%; }
.doc-lines i:nth-child(3) { width: 70%; }
.doc-lines i:nth-child(4) { margin-top: 8px; }
.doc-lines i:nth-child(5) { width: 76%; }

.doc-symbol {
  position: absolute;
  top: 76px;
  left: 0;
  right: 0;
  text-align: center;
  font: 700 20px/1 var(--el-font-family-monospace, monospace);
  opacity: 0.85;
}
.doc-copy {
  position: absolute;
  left: 10px;
  right: 10px;
  bottom: 12px;
  text-align: center;
}
.doc-copy strong { display: block; font-size: 11px; }
.doc-copy small { display: block; margin-top: 3px; font-size: 8px; opacity: 0.75; }

.scroll-tip {
  position: absolute;
  z-index: 27;
  left: 50%;
  bottom: 6px;
  transform: translateX(-50%);
  min-width: 280px;
  height: 36px;
  display: grid;
  grid-template-columns: 24px minmax(150px, 1fr) auto;
  align-items: center;
  gap: 8px;
  padding: 0 10px;
  border-radius: 12px;
  border: 1px solid rgba(93, 164, 177, 0.25);
  background: rgba(255, 255, 255, 0.94);
  box-shadow: 0 4px 16px rgba(35, 75, 82, 0.06);
  backdrop-filter: blur(12px);
}
.scroll-tip p { margin: 0; color: #657e82; font-size: 9px; }
.scroll-tip strong { padding-left: 8px; border-left: 1px solid rgba(93, 164, 177, 0.2); font-size: 9.5px; color: #1a3c42; }
.mouse-icon { width: 16px; height: 20px; border: 1px solid rgba(93, 164, 177, 0.4); border-radius: 8px; display: grid; place-items: start center; padding-top: 2px; }
.mouse-icon i { width: 2px; height: 4px; border-radius: 2px; background: #5da4b1; }

/* ── 底部时间轴 (Timeline) ── */
.timeline-panel {
  position: relative;
  padding: 8px 14px 0;
  border-top: 1px solid rgba(93, 164, 177, 0.14);
  background: #ffffff;
}
.timeline-head {
  display: grid;
  grid-template-columns: 1fr 1fr auto;
  align-items: center;
  font-size: 10px;
}
.timeline-head b { font-size: 11.5px; color: #1a3c42; }
.timeline-head > span { color: #657e82; text-align: center; }
.timeline-head button, .scene-actions button {
  border: 1px solid rgba(93, 164, 177, 0.25);
  background: #edf6f8;
  color: #21474e;
  border-radius: 6px;
  cursor: pointer;
  height: 24px;
  margin-left: 4px;
  padding: 0 8px;
  font-size: 9.5px;
  transition: all 0.15s ease;
}
.timeline-head button:hover, .scene-actions button:hover {
  background: #5da4b1;
  color: #ffffff;
  border-color: #5da4b1;
}

.timeline-dates {
  display: grid;
  grid-template-columns: repeat(9, 1fr);
  align-items: end;
  margin-top: 6px;
}
.timeline-dates button {
  position: relative;
  height: 20px;
  border: 0;
  background: none;
  color: #7b9498;
  font: 9px/1 var(--el-font-family-monospace, monospace);
  cursor: pointer;
}
.timeline-dates button.active {
  color: #1a3c42;
  font-weight: 700;
}
.timeline-dates button i {
  position: absolute;
  left: 50%;
  top: -10px;
  transform: translateX(-50%);
  width: 16px;
  height: 16px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  color: #ffffff;
  background: #5da4b1;
  border: 1px solid #7ebbc5;
  font-size: 8px;
  font-style: normal;
}

.timeline-track {
  position: absolute;
  left: 18px;
  right: 18px;
  bottom: 5px;
  height: 4px;
  background: rgba(93, 164, 177, 0.14);
  border-radius: 6px;
  overflow: hidden;
}
.timeline-track i {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: #5da4b1;
  box-shadow: 0 0 8px rgba(93, 164, 177, 0.4);
  transition: width 0.5s ease;
}

.scene-actions {
  position: absolute;
  z-index: 40;
  left: 50%;
  bottom: -6px;
  transform: translateX(-50%);
  height: 42px;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 8px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.96);
  border: 1px solid rgba(93, 164, 177, 0.28);
  box-shadow: 0 8px 24px rgba(35, 75, 82, 0.08);
}
.scene-actions button {
  height: 28px;
  padding: 0 10px;
  font-size: 10px;
  white-space: nowrap;
}
.scene-actions button.primary {
  color: #ffffff;
  background: #5da4b1;
  border-color: #488793;
  font-weight: 600;
  box-shadow: 0 2px 6px rgba(93, 164, 177, 0.25);
}
.scene-actions button.primary:hover {
  background: #488793;
}

/* ── 右侧栏: 模块详情面板 (Details Panel) ── */
.details-panel {
  min-height: 0;
  display: grid;
  grid-template-rows: 50px minmax(0, 1fr) 56px;
  overflow: hidden;
  background: #ffffff;
}

.details-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 16px;
  border-bottom: 1px solid rgba(93, 164, 177, 0.14);
  background: #ffffff;
}
.details-heading h2 {
  margin: 0;
  font-size: 14.5px;
  font-weight: 700;
  color: #1a3c42;
}
.details-heading button {
  border: 0;
  background: transparent;
  color: #657e82;
  cursor: pointer;
  font-size: 16px;
  transition: color 0.15s ease;
}
.details-heading button:hover {
  color: #5da4b1;
}

.details-content {
  padding: 14px 16px;
  overflow-y: auto;
  scrollbar-width: none;
}
.details-content::-webkit-scrollbar { display: none; }

.details-content code {
  display: block;
  color: #657e82;
  font: 9.5px/1.2 var(--el-font-family-monospace, monospace);
  letter-spacing: 0.04em;
  margin-bottom: 10px;
}

.detail-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.detail-title-row h2 {
  margin: 0;
  font-size: 17px;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: #1a3c42;
}
.detail-title-row span {
  color: #21474e;
  background: #edf6f8;
  border: 1px solid rgba(93, 164, 177, 0.3);
  padding: 1px 7px;
  border-radius: 4px;
  font-size: 9.5px;
  font-weight: 600;
  white-space: nowrap;
}

.detail-description {
  margin: 8px 0 14px;
  color: #324e53;
  font-size: 11.5px;
  line-height: 1.6;
}

.metadata {
  display: flex;
  flex-direction: column;
  gap: 9px;
  margin: 0;
}
.metadata > div {
  display: grid;
  grid-template-columns: 65px 1fr;
  align-items: center;
  font-size: 11px;
}
.metadata dt { color: #657e82; font-weight: 500; }
.metadata dd { margin: 0; display: flex; align-items: center; gap: 6px; color: #1a3c42; }
.metadata .el-icon { font-size: 12px; color: #7b9498; }

.mini-avatar {
  width: 18px;
  height: 18px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: #edf6f8;
  border: 1px solid rgba(93, 164, 177, 0.3);
  color: #21474e;
  font-size: 8px;
  font-weight: 700;
}
.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #5da4b1;
  box-shadow: 0 0 6px #5da4b1;
}
.priority-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #c7564d;
  box-shadow: 0 0 6px #c7564d;
}
.tag-list {
  flex-wrap: wrap;
  gap: 4px;
}
.tag-list span {
  padding: 2px 7px;
  border-radius: 4px;
  background: #edf6f8;
  border: 1px solid rgba(93, 164, 177, 0.25);
  color: #21474e;
  font-size: 9px;
  font-weight: 500;
}

.ai-note {
  margin-top: 16px;
  padding: 12px;
  border: 1px solid rgba(93, 164, 177, 0.22);
  border-left: 3px solid #5da4b1;
  border-radius: 8px;
  background: #fbfdfd;
}
.ai-note h3 {
  margin: 0 0 6px;
  font-size: 12px;
  font-weight: 600;
  color: #1a3c42;
}
.ai-note ul {
  margin: 0 0 10px;
  padding-left: 14px;
  color: #324e53;
  font-size: 10.5px;
  line-height: 1.65;
}
.ai-note button {
  height: 24px;
  padding: 0 9px;
  border: 1px solid rgba(93, 164, 177, 0.3);
  border-radius: 4px;
  background: #edf6f8;
  color: #21474e;
  font-size: 9.5px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}
.ai-note button:hover {
  background: #5da4b1;
  color: #ffffff;
  border-color: #5da4b1;
}

.detail-actions {
  display: grid;
  grid-template-columns: 1fr 1.2fr 36px;
  gap: 6px;
  align-items: center;
  padding: 0 14px;
  border-top: 1px solid rgba(93, 164, 177, 0.14);
  background: #ffffff;
}
.detail-actions button {
  height: 34px;
  border-radius: 6px;
  border: 1px solid rgba(93, 164, 177, 0.28);
  background: #ffffff;
  color: #21474e;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  font-size: 11px;
  font-weight: 500;
  transition: all 0.15s ease;
}
.detail-actions button:hover {
  background: #edf6f8;
  border-color: #5da4b1;
  color: #1a3c42;
}
.detail-actions button.primary {
  color: #ffffff;
  border-color: #488793;
  background: #5da4b1;
  font-weight: 600;
  box-shadow: 0 2px 6px rgba(93, 164, 177, 0.25);
}
.detail-actions button.primary:hover {
  background: #488793;
}

/* ════════════════ 3. 材质设置抽屉 (Material Overlay) ════════════════ */
.material-overlay {
  position: fixed;
  z-index: 80;
  inset: 0;
  visibility: hidden;
  opacity: 0;
  pointer-events: none;
  background: rgba(25, 45, 50, 0.25);
  backdrop-filter: blur(0);
  transition: opacity 0.24s ease, visibility 0s linear 0.4s;
}
.material-overlay.open {
  visibility: visible;
  opacity: 1;
  pointer-events: auto;
  backdrop-filter: blur(8px);
  transition: opacity 0.24s ease, visibility 0s;
}

.material-drawer {
  position: absolute;
  z-index: 81;
  top: 16px;
  right: 16px;
  bottom: 16px;
  width: 440px;
  display: grid;
  grid-template-rows: 72px minmax(0, 1fr) 64px;
  overflow: hidden;
  opacity: 0;
  transform: translate3d(40px, 0, 0);
  transition: opacity 0.22s ease, transform 0.4s cubic-bezier(0.16, 1, 0.3, 1);
  background: #ffffff;
  border: 1px solid rgba(93, 164, 177, 0.25);
  border-radius: 14px;
  box-shadow: 0 16px 48px rgba(35, 75, 82, 0.16);
}
.material-overlay.open .material-drawer {
  opacity: 1;
  transform: translate3d(0, 0, 0);
}

.material-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 18px;
  border-bottom: 1px solid rgba(93, 164, 177, 0.14);
}
.material-heading span {
  display: block;
  margin-bottom: 3px;
  color: #5da4b1;
  font: 700 8.5px/1 var(--el-font-family-monospace, monospace);
  letter-spacing: 0.14em;
}
.material-heading h2 {
  margin: 0;
  font-size: 18px;
  font-weight: 700;
  color: #1a3c42;
}
.material-heading button {
  width: 30px;
  height: 30px;
  border: 1px solid rgba(93, 164, 177, 0.25);
  border-radius: 50%;
  color: #657e82;
  background: #ffffff;
  cursor: pointer;
  font-size: 16px;
  display: grid;
  place-items: center;
  transition: all 0.15s ease;
}
.material-heading button:hover {
  background: #edf6f8;
  color: #1a3c42;
  border-color: #5da4b1;
}

.material-body {
  padding: 14px 16px 18px;
  overflow-y: auto;
  scrollbar-width: thin;
}

.material-select {
  display: grid;
  grid-template-columns: 90px 1fr;
  align-items: center;
  margin-bottom: 12px;
  color: #657e82;
  font-size: 11.5px;
}
.material-select select {
  height: 32px;
  padding: 0 10px;
  border: 1px solid rgba(93, 164, 177, 0.28);
  border-radius: 6px;
  color: #1a3c42;
  background: #ffffff;
  font-size: 11.5px;
}

.accent-section {
  margin-bottom: 12px;
  padding: 12px;
  border: 1px solid rgba(93, 164, 177, 0.2);
  border-radius: 8px;
  background: #fbfdfd;
}
.accent-section-heading {
  display: flex;
  align-items: start;
  justify-content: space-between;
}
.accent-section-heading h3 {
  margin: 0;
  color: #1a3c42;
  font-size: 11.5px;
  font-weight: 600;
}
.accent-section-heading p {
  margin: 3px 0 0;
  color: #7b9498;
  font-size: 8.5px;
}
.accent-section-heading output {
  padding: 2px 7px;
  border-radius: 4px;
  color: #21474e;
  background: #edf6f8;
  border: 1px solid rgba(93, 164, 177, 0.3);
  font-size: 8.5px;
  font-weight: 700;
}

.accent-swatches {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 6px;
  margin-top: 10px;
}
.accent-swatch {
  padding: 4px 2px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: #657e82;
  font-size: 8.5px;
  cursor: pointer;
}
.accent-swatch i {
  display: block;
  width: 24px;
  height: 24px;
  margin: 0 auto 4px;
  border-radius: 50%;
  border: 1px solid rgba(93, 164, 177, 0.3);
}
.accent-swatch:nth-child(1) i { background: linear-gradient(145deg, #7ebbc5, #5da4b1); }
.accent-swatch:nth-child(2) i { background: linear-gradient(145deg, #38bdf8, #0284c7); }
.accent-swatch:nth-child(3) i { background: linear-gradient(145deg, #a78bfa, #7c3aed); }
.accent-swatch:nth-child(4) i { background: linear-gradient(145deg, #fbbf24, #d97706); }
.accent-swatch:nth-child(5) i { background: linear-gradient(145deg, #f472b6, #db2777); }
.accent-swatch.active {
  color: #1a454d;
  font-weight: 600;
  border-color: #5da4b1;
  background: #edf6f8;
}

.material-preview {
  position: relative;
  height: 90px;
  padding: 16px 18px;
  overflow: hidden;
  border: 1px solid rgba(93, 164, 177, 0.25);
  border-radius: 12px;
  background: #ffffff;
  color: #1a3c42;
}
.material-preview::before {
  content: "";
  position: absolute;
  z-index: -1;
  inset: -50%;
  opacity: var(--material-opacity, 1);
  filter: blur(var(--material-blur, 20px));
  animation: materialFlow var(--flow-duration, 4s) ease-in-out infinite alternate;
}
.material-preview[data-material="cyan"]::before { background: radial-gradient(circle at 76% 28%, #7ebbc5 0 14%, transparent 35%), radial-gradient(circle at 45% 73%, #5da4b1 0 12%, transparent 35%), #f0f7f8; }
.material-preview[data-material="original"]::before { background: radial-gradient(circle at 86% 36%, rgba(238, 201, 168, 0.7) 0 14%, transparent 35%), radial-gradient(circle at 68% 78%, rgba(240, 213, 190, 0.7) 0 12%, transparent 34%), #fbf9f5; }
.material-preview[data-material="rain"]::before { background: radial-gradient(circle at 75% 70%, #88d4c3 0 14%, transparent 36%), radial-gradient(circle at 43% 24%, #6ec5b8 0 12%, transparent 35%), #f2f8f6; }
.material-preview[data-material="chrome"]::before { background: radial-gradient(circle at 74% 28%, #c2e1e7 0 12%, transparent 34%), radial-gradient(circle at 47% 74%, #9ec8d0 0 12%, transparent 36%), #f4f7f8; }

.material-preview small { display: block; margin-bottom: 4px; color: #657e82; font: 700 7.5px/1 var(--el-font-family-monospace, monospace); }
.material-preview strong { display: block; font-size: 16px; color: #1a3c42; }
.material-preview span { display: block; margin-top: 4px; font-size: 9px; color: #5a767b; }

.material-section {
  margin-top: 12px;
  padding: 12px;
  border: 1px solid rgba(93, 164, 177, 0.2);
  border-radius: 8px;
  background: #fbfdfd;
}
.material-section h3 {
  margin: 0 0 10px;
  color: #1a3c42;
  font-size: 11.5px;
  font-weight: 600;
}

.material-swatches {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 6px;
}
.material-swatch {
  height: 54px;
  padding: 4px;
  border: 1px solid rgba(93, 164, 177, 0.2);
  border-radius: 6px;
  background: #ffffff;
  color: #657e82;
  font-size: 8.5px;
  cursor: pointer;
}
.material-swatch i {
  display: block;
  height: 30px;
  margin-bottom: 3px;
  border-radius: 4px;
}
.material-swatch.active {
  color: #1a454d;
  font-weight: 600;
  border-color: #5da4b1;
  background: #edf6f8;
}

.color-fields {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
}
.color-fields label { color: #657e82; font-size: 8.5px; }
.color-fields input {
  display: block;
  width: 100%;
  height: 28px;
  margin: 5px 0 3px;
  border: 1px solid rgba(93, 164, 177, 0.25);
  border-radius: 4px;
  background: transparent;
  cursor: pointer;
}
.color-fields small { color: #7b9498; font: 7.5px/1 var(--el-font-family-monospace, monospace); }

.material-controls label {
  display: block;
  margin-top: 10px;
}
.material-controls label > span {
  display: flex;
  justify-content: space-between;
  color: #657e82;
  font-size: 9px;
}
.material-controls input {
  width: 100%;
  height: 4px;
  margin-top: 6px;
  accent-color: #5da4b1;
  cursor: pointer;
}

.material-actions {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 6px;
  align-items: center;
  padding: 0 16px;
  border-top: 1px solid rgba(93, 164, 177, 0.14);
}
.material-actions button {
  height: 32px;
  border: 1px solid rgba(93, 164, 177, 0.28);
  border-radius: 6px;
  color: #21474e;
  background: #ffffff;
  cursor: pointer;
  font-size: 10px;
  font-weight: 500;
  transition: all 0.15s ease;
}
.material-actions button:hover {
  background: #edf6f8;
  border-color: #5da4b1;
}
.material-actions button.primary {
  color: #ffffff;
  background: #5da4b1;
  border-color: #488793;
  font-weight: 600;
  box-shadow: 0 2px 6px rgba(93, 164, 177, 0.25);
}
.material-actions button.primary:hover {
  background: #488793;
}

/* ════════════════ Toast ════════════════ */
.toast {
  position: fixed;
  z-index: 100;
  left: 50%;
  bottom: 24px;
  transform: translate(-50%, 15px);
  padding: 8px 14px;
  border-radius: 8px;
  border: 1px solid rgba(93, 164, 177, 0.35);
  background: #ffffff;
  box-shadow: 0 8px 24px rgba(35, 75, 82, 0.12);
  color: #1a454d;
  font-size: 11.5px;
  font-weight: 500;
  opacity: 0;
  pointer-events: none;
  transition: all 0.25s ease;
}
.toast.show {
  opacity: 1;
  transform: translate(-50%, 0);
}
</style>
