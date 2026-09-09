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

// ════════════════ 10 大核心战术功能模块定义 (赛博机械拓扑节点) ════════════════
const modules = [
  {
    id: "SYS-AUTOLOOP-001", num: "01", code: "auto-worker.md", short: "跑号 · 舰队", title: "全自动并发跑号", symbol: "舰",
    subtitle: "多 Worker 自动化无人值守跑号", state: "高频就绪", folder: "调度中心 / 自动舰队", priority: "最高",
    path: "/auto", date: "实时轮询调度", status: "运行状态 正常",
    description: "多协程并发跑号引擎，结合真实浏览器指纹库与住宅代理路由，支持自动化取件、PoW 碰撞及 2FA 强制绑定。",
    tags: ["并发 Worker", "指纹对齐", "PoW 预计算", "2FA 强制"],
    advice: ["当前号池存量充沛，推荐开启 5~10 并发 Worker 进行不间断跑号", "遇 409 限流时系统已自动启用 15min 冷冻隔离防风控"],
    accent: "green", completion: 98,
  },
  {
    id: "SYS-ASSETS-002", num: "02", code: "assets-manager.md", short: "资产 · 中枢", title: "账号资产管理中枢", symbol: "资",
    subtitle: "Token / 2FA / 官方设密 / 导出", state: "核心资产", folder: "资产中枢 / 注册结果", priority: "最高",
    path: "/registered", date: "秒级实时入库", status: "覆盖率 92%",
    description: "全量账号资产集中管理平台，支持 Access Token 极速置换、Session JSON 复制、CPA/Sub2API 导出及批次备注留痕。",
    tags: ["Token 凭证", "2FA TOTP", "官方设密", "多格式导出"],
    advice: ["已导出账号建议添加去向便签备注，便于日后随时追溯批次", "支持一键批量补设强随机密码，免除后续验证码登入繁琐流程"],
    accent: "ice", completion: 94,
  },
  {
    id: "SYS-POW-003", num: "03", code: "pow-sentinel.md", short: "算力 · 防御", title: "Sentinel PoW 预计算池", symbol: "算",
    subtitle: "0ms 瞬时取用 / 削峰填谷", state: "就绪 10 槽", folder: "防御引擎 / 算力缓冲", priority: "高",
    path: "/auto", date: "协程常驻预热", status: "0ms 命中率 99%",
    description: "后台守护协程预计算 OpenAI Sentinel Proof-of-Work，注册流水线免去 15s 客户端碰撞等待，平滑 CPU 瞬时波峰。",
    tags: ["PoW 预计算", "0ms 瞬时", "削峰填谷", "CPU 优化"],
    advice: ["保持预计算池目标深度为 10~20，可完全吸收并发注册时的突发算力需求", "若出现频控可适当调大超时阈值"],
    accent: "silver", completion: 99,
  },
  {
    id: "SYS-PROXY-004", num: "04", code: "proxy-pool.md", short: "代理 · 路由", title: "动态住宅代理池", symbol: "网",
    subtitle: "健康评级 / 15min 自动冷冻隔离", state: "实时防护", folder: "网络中枢 / 住宅路由", priority: "高",
    path: "/proxy-pool", date: "实时健康探测", status: "隔离冷冻 0",
    description: "出口代理智能健康度跟踪与风控隔离系统，一号一 IP 严格隔离，连续 409 代理自动熔断冷冻 15 分钟。",
    tags: ["住宅代理", "一号一IP", "409 冷冻", "智能换国"],
    advice: ["推荐优先配置支持 SOCKS5 / HTTP 的多国住宅代理池", "出现连续 409 限流会自动拉黑冷冻，无需人工干预"],
    accent: "warm", completion: 91,
  },
  {
    id: "SYS-REMAIL-005", num: "05", code: "remail-mail.md", short: "邮箱 · 枢纽", title: "Remail & 邮箱配置", symbol: "邮",
    subtitle: "Remail 暂存 / 微软 OAuth / CF", state: "0s 即取", folder: "邮箱枢纽 / 渠道配置", priority: "高",
    path: "/mail-config", date: "全协议支持", status: "暂存复用 0s",
    description: "全功能邮箱中枢，支持 Remail 失败复用免扣积分、微软 Graph OAuth 授权直连与 Cloudflare Worker 邮件提取。",
    tags: ["Remail 复用", "微软 OAuth", "CF Worker", "IMAP4"],
    advice: ["开启 Remail 暂存后，注册失败账号可无损复用 3 次，大幅节省接码成本", "微软 OAuth 拥有极高稳定性与收信速度"],
    accent: "mist", completion: 96,
  },
  {
    id: "SYS-OAUTH-006", num: "06", code: "oauth-codex.md", short: "授权 · 接码", title: "Codex OAuth 授权导出", symbol: "码",
    subtitle: "Codex 接码 / OAuth 授权直连", state: "多渠道", folder: "接码流水 / OAuth 导出", priority: "中",
    path: "/sms-config", date: "按需接码授权", status: "直通授权",
    description: "Codex 及第三方平台 OAuth 自动授权工作台，支持短信平台自动租赁号码、收码与回填，实现免封直连。",
    tags: ["Codex 凭证", "短信接码", "OAuth 2.0", "一键直连"],
    advice: ["建议配置超时等待为 60~85 秒，避免 OpenAI 授权会话超时失效", "可锁定指定接码价格区间"],
    accent: "ice", completion: 88,
  },
  {
    id: "SYS-EXTRACT-007", num: "07", code: "payment-extract.md", short: "代付 · 提链", title: "全渠道提链出码代付", symbol: "链",
    subtitle: "PayPal / GCash / PIX / iDEAL", state: "秒级提链", folder: "提炼中枢 / 全球代付", priority: "高",
    path: "/extract", date: "多币种支持", status: "提链出码",
    description: "全渠道支付链接提取与自动代付流水线，覆盖 PayPal 一条龙、GCash、PIX、Hosted Invoice 等主流结账链路。",
    tags: ["PayPal 代付", "GCash 出码", "PIX 巴西", "订阅升级"],
    advice: ["推荐使用 PayPal Pipeline 进行一键提链加代付全自动流程", "提取链接后可随时批量导出至收银台"],
    accent: "silver", completion: 92,
  },
  {
    id: "SYS-HEALTH-008", num: "08", code: "token-health.md", short: "验活 · 探测", title: "账号批量并发验活", symbol: "验",
    subtitle: "Token 有效性 / Plus 资格探测", state: "并发探测", folder: "质检流水 / 验活中枢", priority: "中",
    path: "/registered", date: "高并发检测", status: "存活率 99.4%",
    description: "批量 Access Token 状态验活与订阅套餐（Plus / Pro / Team / Promo）深度探测，支持毫秒级异常标记与归档。",
    tags: ["Token 验活", "Plus 探测", "试用特权", "批量体检"],
    advice: ["大批量出号后可一键运行 Token 快速验活，过滤失效凭证", "支持使用代理池并发分散探测压力"],
    accent: "warm", completion: 97,
  },
  {
    id: "SYS-WARM-009", num: "09", code: "session-warm.md", short: "保温 · 保鲜", title: "账号自动化保温保鲜", symbol: "温",
    subtitle: "官方拟真交互 / 活跃度防封", state: "智能保鲜", folder: "生命周期 / 自动保温", priority: "中",
    path: "/registered", date: "周期性轮询", status: "活跃防封",
    description: "自动化与 OpenAI 官方模型发起多轮拟真对话，沉淀账号活跃度画像，显著降低批量冷号被风控扫荡的概率。",
    tags: ["模型交互", "活跃保鲜", "拟真对话", "防封加固"],
    advice: ["冷号建议每隔 3~7 天进行一次交互保温，保持会话活跃度", "可配合住宅代理模拟真实用户日常访问"],
    accent: "mist", completion: 86,
  },
  {
    id: "SYS-EXPORT-010", num: "10", code: "shipment-export.md", short: "发货 · 转换", title: "发货导入与格式转换", symbol: "导",
    subtitle: "NDJSON 入库 / Sub2 / CPA 导出", state: "全格式", folder: "数据流转 / 发货导出", priority: "高",
    path: "/shipment-import", date: "实时批量处理", status: "Sub2/CPA 兼容",
    description: "多渠道账号发货导入与全格式转换中枢，支持 NDJSON 自动解析入库、Sub2API 结构转换及带有 Refresh Token 容错的 CPA 格式导出。",
    tags: ["发货导入", "NDJSON", "Sub2API", "CPA 转换"],
    advice: ["支持第三方发货文件一键拖入，自动识别账密与 2FA 凭证", "导出 Sub2API/CPA 时系统已对未授权账号自动补齐 refresh_token 兜底"],
    accent: "ice", completion: 95,
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

// ════════════════ 赛博战术工控蓝图布局控制 (对齐 Image #22 / #24 美学体系) ════════════════
const pageLayout = ref('split') // 'split' (工控分屏中枢，默认推荐，一屏尽览) | 'matrix' (全屏战术矩阵)
const inspectorDrawerOpen = ref(false)
const selectedIndex = ref(0)
const slideDirection = ref('right') // 'right' | 'left' 用于卡片平滑流体过渡
const isPlaying = ref(false) // 自动巡航展示开关
let cruiseTimer = null
let wheelThrottle = false

const activeModule = computed(() => modules[selectedIndex.value] || modules[0])

const moduleIconMap = [
  Compass,      // 0: 全自动并发跑号 (01)
  Files,        // 1: 账号资产管理中枢 (02)
  Opportunity,  // 2: Sentinel PoW 预计算池 (03)
  Connection,   // 3: 动态住宅代理池 (04)
  Message,      // 4: Remail & 邮箱配置 (05)
  Key,          // 5: Codex OAuth 授权导出 (06)
  CreditCard,   // 6: 全渠道提链出码代付 (07)
  CircleCheck,  // 7: 账号批量并发验活 (08)
  Sunny,        // 8: 账号自动化保温保鲜 (09)
  Download,     // 9: 发货导入与格式转换 (10)
]

// 战术蓝图双列对齐：左列为奇数 (01, 03, 05, 07, 09)，右列为偶数 (02, 04, 06, 08, 10)
const leftModules = computed(() =>
  modules.filter((_, idx) => idx % 2 === 0).map(m => ({ ...m, rawIndex: modules.indexOf(m) }))
)
const rightModules = computed(() =>
  modules.filter((_, idx) => idx % 2 === 1).map(m => ({ ...m, rawIndex: modules.indexOf(m) }))
)

function getModuleIcon(idx) {
  return moduleIconMap[idx] || Compass
}

function selectCard(idx) {
  const target = ((idx % modules.length) + modules.length) % modules.length
  slideDirection.value = target >= selectedIndex.value ? 'right' : 'left'
  selectedIndex.value = target
}

function openModuleInspector(idx) {
  selectCard(idx)
  inspectorDrawerOpen.value = true
}

function executeModule(mod) {
  if (mod?.path) {
    router.push(mod.path)
  }
}

function nextCard(dir = 1) {
  slideDirection.value = dir > 0 ? 'right' : 'left'
  selectedIndex.value = (selectedIndex.value + dir + modules.length) % modules.length
}

function setViewMode(mode) {
  viewMode.value = mode
  showToast(mode === 'hero' ? '已切换至旗舰中枢看板' : '已切换至架构矩阵看板')
}

function startCruiseTimer() {
  stopCruiseTimer()
  cruiseTimer = setInterval(() => {
    nextCard(1)
  }, 3400)
}

function stopCruiseTimer() {
  if (cruiseTimer) {
    clearInterval(cruiseTimer)
    cruiseTimer = null
  }
}

function togglePlay() {
  isPlaying.value = !isPlaying.value
  if (isPlaying.value) {
    startCruiseTimer()
    showToast('已开启旗舰中枢自动巡航')
  } else {
    stopCruiseTimer()
    showToast('已暂停巡航')
  }
}

// 滚轮交互 (在旗舰看板模式下平滑节流轮转卡片)
function handleSceneWheel(e) {
  if (viewMode.value !== 'hero') return
  e.preventDefault()
  if (wheelThrottle) return
  wheelThrottle = true
  const delta = Math.sign(e.deltaY || e.deltaX)
  if (delta !== 0) {
    nextCard(delta > 0 ? 1 : -1)
  }
  setTimeout(() => {
    wheelThrottle = false
  }, 260)
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
    startCruiseTimer()
  }
})

onDeactivated(() => {
  isDashboardActive = false
  if (timer) {
    clearInterval(timer)
    timer = null
  }
  stopCruiseTimer()
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
  stopCruiseTimer()
  if (toastTimer) {
    clearTimeout(toastTimer)
    toastTimer = null
  }
})
</script>

<template>
  <div class="octopus-app-wrapper glass-dashboard-app" :class="{ 'material-open': materialDrawerOpen }">
    <!-- 背景流体微光环境光球 (Ambient Glass Glow Orbs) -->
    <div class="glass-ambient-sphere sphere-1"></div>
    <div class="glass-ambient-sphere sphere-2"></div>
    <div class="glass-ambient-sphere sphere-3"></div>

    <main class="glass-workbench-shell">
      <!-- ════════════════ 1. 顶部悬浮毛玻璃导控栏 (Floating Glass Navbar) ════════════════ -->
      <header class="glass-navbar">
        <div class="card-glass-specular"></div>
        <div class="liquid-caustic-flare"></div>
        <div class="nav-brand-group">
          <div class="brand-orb-icon">
            <el-icon><Operation /></el-icon>
          </div>
          <div class="brand-titles">
            <div class="brand-main-row">
              <h1 class="brand-title">OpenAI 自动化注册资产中枢</h1>
              <span class="glass-pulse-badge">
                <i class="pulse-dot"></i>
                系统运行正常
              </span>
            </div>
            <p class="brand-subtitle">Pipeline Architecture · 多协程防风控与资产自愈中枢</p>
          </div>
        </div>

        <div class="nav-search-slot">
          <el-icon><Search /></el-icon>
          <input
            placeholder="快捷搜索功能模块、路由或数据…"
            @keyup.enter="showToast('已匹配相关功能')"
          />
          <kbd>⌘ K</kbd>
        </div>

        <div class="nav-actions-group">
          <button
            class="glass-icon-btn"
            :class="{ 'is-loading': summaryLoading }"
            title="刷新系统概览"
            @click="loadDashboardSummary"
          >
            <el-icon :class="{ 'is-spinning': summaryLoading }"><Refresh /></el-icon>
          </button>

          <button
            class="glass-icon-btn"
            title="调节毛玻璃流速与材质"
            @click="openMaterialDrawer(0)"
          >
            <el-icon><Filter /></el-icon>
          </button>

          <button class="glass-cta-btn" @click="router.push('/auto')">
            <el-icon><Compass /></el-icon>
            <span>一键并发跑号</span>
          </button>
        </div>
      </header>

      <!-- ════════════════ 2. 四大核心遥测毛玻璃指标卡 (4 Glass Metric Pods) ════════════════ -->
      <section class="glass-metrics-grid" aria-label="核心指标统计">
        <!-- Pod 1: 号池库存 -->
        <div class="glass-metric-card" @click="router.push('/pool')" title="点击直达号池管理">
          <div class="card-glass-specular"></div>
          <div class="liquid-caustic-flare"></div>
          <div class="metric-top-row">
            <div class="metric-icon-orb blue-orb">
              <el-icon><Folder /></el-icon>
            </div>
            <span class="metric-tag">号池库存</span>
            <span class="metric-link-hint"><el-icon><ArrowRight /></el-icon></span>
          </div>
          <div class="metric-value-row">
            <strong class="metric-number">{{ stats.total || summaryData.pool.total || 0 }}</strong>
            <span class="metric-unit">个可用</span>
          </div>
          <div class="metric-footer-row">
            <span>可用 {{ stats.available || summaryData.pool.available || 0 }}</span>
            <span class="metric-highlight">可用率 {{ Math.min(100, Math.round(((stats.available || summaryData.pool.available || 0) / (stats.total || summaryData.pool.total || 1)) * 100)) }}%</span>
          </div>
        </div>

        <!-- Pod 2: GPT 资产库 -->
        <div class="glass-metric-card" @click="router.push('/registered')" title="点击直达账号资产库">
          <div class="card-glass-specular"></div>
          <div class="liquid-caustic-flare"></div>
          <div class="metric-top-row">
            <div class="metric-icon-orb emerald-orb">
              <el-icon><CircleCheckFilled /></el-icon>
            </div>
            <span class="metric-tag">GPT 资产库</span>
            <span class="metric-link-hint"><el-icon><ArrowRight /></el-icon></span>
          </div>
          <div class="metric-value-row">
            <strong class="metric-number">{{ summaryData.registered.total }}</strong>
            <span class="metric-unit">个账号</span>
          </div>
          <div class="metric-footer-row">
            <span>2FA 保护 {{ summaryData.registered.with_2fa }}</span>
            <span class="metric-highlight emerald">覆盖率 {{ summaryData.registered.sec_rate }}%</span>
          </div>
        </div>

        <!-- Pod 3: 自动跑号并发 -->
        <div class="glass-metric-card" @click="router.push('/auto')" title="点击直达全自动并发跑号">
          <div class="card-glass-specular"></div>
          <div class="liquid-caustic-flare"></div>
          <div class="metric-top-row">
            <div class="metric-icon-orb cyan-orb">
              <el-icon><Compass /></el-icon>
            </div>
            <span class="metric-tag">并发跑号</span>
            <span class="metric-link-hint"><el-icon><ArrowRight /></el-icon></span>
          </div>
          <div class="metric-value-row">
            <strong class="metric-number">{{ autoStatus.concurrency || 1 }}</strong>
            <span class="metric-unit">核并发</span>
          </div>
          <div class="metric-footer-row">
            <span>成功 {{ autoStatus.registered_ok || summaryData.pool.done || 0 }}</span>
            <span class="metric-highlight">成功率 {{ summaryData.registered.success_rate || 100 }}%</span>
          </div>
        </div>

        <!-- Pod 4: Sentinel 算力防御 -->
        <div class="glass-metric-card" @click="router.push('/proxy-pool')" title="点击直达代理与算力池">
          <div class="card-glass-specular"></div>
          <div class="liquid-caustic-flare"></div>
          <div class="metric-top-row">
            <div class="metric-icon-orb violet-orb">
              <el-icon><Opportunity /></el-icon>
            </div>
            <span class="metric-tag">Sentinel 防御</span>
            <span class="metric-link-hint"><el-icon><ArrowRight /></el-icon></span>
          </div>
          <div class="metric-value-row">
            <strong class="metric-number">{{ sentinelStats.current_size }}</strong>
            <span class="metric-unit">槽位就绪</span>
          </div>
          <div class="metric-footer-row">
            <span>隔离冷冻 {{ proxyHealthStats.cooling_down_count }}</span>
            <span class="metric-highlight violet">0ms 瞬时取用</span>
          </div>
        </div>
      </section>

      <!-- ════════════════ 3. 核心工作区 (Glass Workspace) ════════════════ -->
      <section class="glass-workspace-layout">
        <!-- 左侧/主体：10 大核心功能玻璃工作台 (Glass Command Hub) -->
        <div class="glass-main-panel">
          <div class="card-glass-specular"></div>
          <div class="liquid-caustic-flare"></div>
          <div class="glass-panel-header">
            <div class="panel-header-left">
              <h2 class="panel-title">✦ 核心功能工作台</h2>
              <span class="panel-subtitle">10 个核心自动化作业流 · 极速直达</span>
            </div>
            <div class="panel-header-right">
              <span class="active-node-count">10 模块就绪</span>
            </div>
          </div>

          <div class="glass-modules-grid">
            <div
              v-for="(mod, idx) in modules"
              :key="mod.id"
              class="glass-module-card"
              @click="executeModule(mod)"
              :title="`前往：${mod.title}`"
            >
              <div class="module-card-specular"></div>
              <div class="liquid-caustic-flare"></div>
              <div class="module-card-top">
                <div class="module-icon-box" :class="`accent-${mod.accent || 'ice'}`">
                  <component :is="getModuleIcon(idx)" />
                </div>
                <div class="module-top-pills">
                  <span class="module-num-pill">{{ mod.num }}</span>
                  <span class="module-state-pill">
                    <i class="state-dot"></i>
                    {{ mod.state }}
                  </span>
                </div>
              </div>

              <div class="module-card-mid">
                <h3 class="module-title">{{ mod.title }}</h3>
                <p class="module-desc">{{ mod.subtitle }}</p>
                <div class="module-tag-row">
                  <span v-for="tag in mod.tags.slice(0, 2)" :key="tag" class="glass-tag">{{ tag }}</span>
                </div>
              </div>

              <div class="module-card-bottom">
                <span class="module-metric-hint">{{ mod.short }}</span>
                <span class="module-enter-btn"><el-icon><ArrowRight /></el-icon></span>
              </div>
            </div>
          </div>
        </div>

        <!-- 右侧：实时系统雷达 & 智能风控建议 (Glass Health Radar & Advisory) -->
        <div class="glass-side-panel">
          <!-- 卡片 1: 实时算力与网络健康 -->
          <div class="glass-subcard">
            <div class="card-glass-specular"></div>
            <div class="liquid-caustic-flare"></div>
            <div class="subcard-head">
              <h3><el-icon><Histogram /></el-icon> 实时防御雷达</h3>
              <span class="subcard-badge">LIVE</span>
            </div>

            <div class="radar-stat-item">
              <div class="stat-item-label">
                <span>PoW 预计算池深度</span>
                <b>{{ sentinelStats.current_size }} / {{ sentinelStats.target_size }} 槽</b>
              </div>
              <div class="glass-progress-bar">
                <div
                  class="progress-fill cyan-fill"
                  :style="{ width: `${Math.min(100, Math.round((sentinelStats.current_size / Math.max(1, sentinelStats.target_size)) * 100))}%` }"
                ></div>
              </div>
            </div>

            <div class="radar-stat-item">
              <div class="stat-item-label">
                <span>住宅代理健康度</span>
                <b>{{ proxyHealthStats.healthy_count }} / {{ Math.max(1, proxyHealthStats.total_tracked) }} 正常</b>
              </div>
              <div class="glass-progress-bar">
                <div
                  class="progress-fill green-fill"
                  :style="{ width: `${Math.min(100, Math.round(((proxyHealthStats.healthy_count || 1) / Math.max(1, proxyHealthStats.total_tracked || 1)) * 100))}%` }"
                ></div>
              </div>
            </div>
          </div>

          <!-- 卡片 2: AI 智能风控策略 -->
          <div class="glass-subcard advisory-card">
            <div class="card-glass-specular"></div>
            <div class="liquid-caustic-flare"></div>
            <div class="subcard-head">
              <h3><el-icon><Opportunity /></el-icon> 智能调度策略建议</h3>
            </div>
            <ul class="glass-advice-list">
              <li>号池库存充沛，推荐开启 <b>5~10 并发 Worker</b> 持续批量作业。</li>
              <li>连续 409 限流时系统已自动启用 <b>15min 智能冷冻隔离</b>，防风控熔断。</li>
              <li>已注册账号支持<b>一键批量补设强密码</b>，可免手机验证码顺畅登入。</li>
            </ul>
          </div>

          <!-- 卡片 3: 快捷出号行动流 -->
          <div class="glass-subcard quick-action-subcard">
            <div class="card-glass-specular"></div>
            <div class="liquid-caustic-flare"></div>
            <button class="glass-action-btn primary" @click="router.push('/auto')">
              <el-icon><Compass /></el-icon>
              <span>启动全自动并发舰队</span>
            </button>
            <button class="glass-action-btn secondary" @click="router.push('/registered')">
              <el-icon><Download /></el-icon>
              <span>导出已注册账号资产</span>
            </button>
          </div>
        </div>
      </section>

      <!-- ════════════════ 4. 底部毛玻璃状态栏 (Glass Status Bar) ════════════════ -->
      <footer class="glass-status-footer">
        <div class="card-glass-specular"></div>
        <div class="liquid-caustic-flare"></div>
        <div class="footer-left-status">
          <span class="live-pulse-dot"></span>
          <span>调度常驻引擎已启动 · 今日已产出 <b>{{ autoStatus.registered_ok || summaryData.pool.done || 0 }}</b> 个账号</span>
        </div>
        <div class="footer-right-shortcuts">
          <button class="status-shortcut-btn" @click="router.push('/pool')">号池入库</button>
          <button class="status-shortcut-btn" @click="router.push('/proxy-pool')">代理检测</button>
          <button class="status-shortcut-btn" @click="router.push('/shipment-import')">发货转换</button>
          <button class="status-shortcut-btn" @click="openMaterialDrawer(0)">护眼材质</button>
        </div>
      </footer>
    </main>

      <!-- 战术技术档案抽屉 (在全景蓝图模式下点击卡片随时划出查看) -->
      <el-drawer
        v-model="inspectorDrawerOpen"
        :title="`NODE // ${activeModule.num} · ${activeModule.title}`"
        direction="rtl"
        size="380px"
      >
        <div class="details-content drawer-details">
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

          <div style="margin-top: 20px; display: flex; gap: 10px;">
            <button class="primary scan-btn" style="flex: 1; height: 36px;" @click="executeCurrentModule">
              <el-icon><Compass /></el-icon>
              <span>立即进入该模块</span>
            </button>
          </div>
        </div>
      </el-drawer>

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
/* ──────────── 3D实体液态水晶玻璃拟态 (Liquid Glass / Crystal Glassmorphism) ──────────── */
.octopus-app-wrapper.glass-dashboard-app {
  --glass-bg: linear-gradient(135deg, rgba(255, 255, 255, 0.82) 0%, rgba(255, 255, 255, 0.52) 50%, rgba(248, 250, 252, 0.68) 100%);
  --glass-border: rgba(255, 255, 255, 0.92);
  --glass-border-light: #ffffff;
  --glass-shadow:
    0 22px 48px -10px rgba(15, 23, 42, 0.08),
    0 8px 18px -4px rgba(15, 23, 42, 0.04);
  --glass-inset:
    inset 0 2px 2px 0 #ffffff,
    inset 1.5px 0 2px 0 rgba(255, 255, 255, 0.85),
    inset -1.5px 0 2px 0 rgba(255, 255, 255, 0.55),
    inset 0 -2px 3px 0 rgba(148, 163, 184, 0.18);
  --accent-primary: #0284c7;
  --accent-hover: #0369a1;
  --text-main: #0f172a;
  --text-muted: #475569;
  --text-sub: #64748b;

  position: relative;
  width: 100%;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  /* 摄影棚哑光台面 + 极细透气网格底纹 (如图片 29/31/32) */
  background-color: #f6f8fb;
  background-image:
    radial-gradient(ellipse at 50% -12%, rgba(255, 255, 255, 0.98) 0%, transparent 65%),
    radial-gradient(ellipse at 88% 18%, rgba(56, 189, 248, 0.12) 0%, transparent 45%),
    radial-gradient(ellipse at 12% 82%, rgba(249, 115, 22, 0.09) 0%, transparent 45%),
    linear-gradient(to right, rgba(203, 213, 225, 0.32) 1px, transparent 1px),
    linear-gradient(to bottom, rgba(203, 213, 225, 0.32) 1px, transparent 1px);
  background-size: 100% 100%, 100% 100%, 100% 100%, 32px 32px, 32px 32px;
  background-attachment: fixed;
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
  color: var(--text-main);
}

/* ════════ 背景环境微光球 (柔和摄影棚漫射光晕) ════════ */
.glass-ambient-sphere {
  position: absolute;
  border-radius: 50%;
  filter: blur(75px);
  pointer-events: none;
  z-index: 0;
  opacity: 0.65;
}
.sphere-1 {
  top: 0%;
  left: 18%;
  width: 420px;
  height: 420px;
  background: radial-gradient(circle, rgba(56, 189, 248, 0.55) 0%, rgba(14, 165, 233, 0.05) 70%);
  animation: floatOrb1 15s ease-in-out infinite alternate;
}
.sphere-2 {
  top: 25%;
  right: 12%;
  width: 450px;
  height: 450px;
  background: radial-gradient(circle, rgba(168, 85, 247, 0.45) 0%, rgba(99, 102, 241, 0.05) 70%);
  animation: floatOrb2 19s ease-in-out infinite alternate-reverse;
}
.sphere-3 {
  bottom: 0%;
  left: 32%;
  width: 480px;
  height: 480px;
  background: radial-gradient(circle, rgba(16, 185, 129, 0.4) 0%, rgba(5, 150, 105, 0.05) 70%);
  animation: floatOrb1 17s ease-in-out infinite alternate;
}
@keyframes floatOrb1 {
  0% { transform: translate(0, 0) scale(1); }
  100% { transform: translate(25px, 15px) scale(1.06); }
}
@keyframes floatOrb2 {
  0% { transform: translate(0, 0) scale(1); }
  100% { transform: translate(-20px, 20px) scale(1.05); }
}

/* ════════ 核心精髓：彩虹棱镜色散高光 (Prismatic Caustic Dispersion Flare) ════════ */
.liquid-caustic-flare {
  position: absolute;
  bottom: 0px;
  left: 12%;
  right: 12%;
  height: 2px;
  border-radius: 9999px;
  background: linear-gradient(90deg,
    transparent 0%,
    rgba(249, 115, 22, 0.65) 15%,
    rgba(251, 191, 36, 0.85) 32%,
    rgba(255, 255, 255, 1) 50%,
    rgba(56, 189, 248, 0.9) 66%,
    rgba(168, 85, 247, 0.7) 84%,
    transparent 100%
  );
  filter: blur(0.5px);
  box-shadow: 0 3px 12px rgba(251, 191, 36, 0.42), 0 4px 14px rgba(56, 189, 248, 0.38);
  pointer-events: none;
  z-index: 3;
}

/* ════════ 核心精髓：顶部水滴凸透镜反光 (Curved Specular Liquid Sheen) ════════ */
.card-glass-specular,
.module-card-specular {
  position: absolute;
  top: 1px;
  left: 2px;
  right: 2px;
  height: 48%;
  border-radius: 15px 15px 42% 42% / 15px 15px 18px 18px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.92) 0%, rgba(255, 255, 255, 0.28) 55%, transparent 100%);
  pointer-events: none;
  z-index: 2;
}

/* ════════ 主工作台容器 ════════ */
.glass-workbench-shell {
  position: relative;
  z-index: 1;
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 16px;
  overflow: hidden;
  transition: filter 0.34s ease, transform 0.46s cubic-bezier(0.16, 1, 0.3, 1);
}

.material-open .glass-workbench-shell {
  filter: blur(2px) saturate(0.85);
  transform: scale(0.996);
}

/* ════════ 1. 顶部悬浮水晶毛玻璃导航栏 ════════ */
.glass-navbar {
  position: relative;
  height: 46px;
  flex-shrink: 0;
  background: var(--glass-bg);
  backdrop-filter: blur(28px) saturate(200%);
  -webkit-backdrop-filter: blur(28px) saturate(200%);
  border: 1.5px solid var(--glass-border);
  border-top: 2px solid var(--glass-border-light);
  border-radius: 16px;
  box-shadow: var(--glass-shadow), var(--glass-inset);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 14px;
}

.nav-brand-group {
  position: relative;
  z-index: 4;
  display: flex;
  align-items: center;
  gap: 10px;
}
.brand-orb-icon {
  width: 32px;
  height: 32px;
  border-radius: 11px;
  background: linear-gradient(180deg, #38bdf8 0%, #0284c7 100%);
  border: 1.5px solid rgba(255, 255, 255, 0.95);
  color: #ffffff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  box-shadow: 0 8px 18px -2px rgba(2, 132, 199, 0.45), inset 0 2px 2px rgba(255, 255, 255, 0.9), inset 0 -2px 3px rgba(0, 0, 0, 0.2);
}
.brand-titles {
  display: flex;
  flex-direction: column;
}
.brand-main-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.brand-title {
  margin: 0;
  font-size: 15px;
  font-weight: 800;
  letter-spacing: -0.02em;
  color: var(--text-main);
  line-height: 1.2;
}
.glass-pulse-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 9.5px;
  font-weight: 700;
  color: #047857;
  background: rgba(16, 185, 129, 0.15);
  border: 1px solid rgba(16, 185, 129, 0.35);
  border-radius: 9999px;
  padding: 2px 8px;
  box-shadow: inset 0 1px 1px rgba(255, 255, 255, 0.8);
}
.pulse-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #10b981;
  box-shadow: 0 0 8px #10b981;
  animation: pulseLight 1.8s infinite ease-in-out;
}
@keyframes pulseLight {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.45; transform: scale(0.8); }
}
.brand-subtitle {
  margin: 1px 0 0 0;
  font-size: 10px;
  color: var(--text-muted);
  font-weight: 500;
}

/* 水晶搜索胶囊 (对齐图片 31/32 的 Search Capsule) */
.nav-search-slot {
  position: relative;
  z-index: 4;
  height: 32px;
  width: 290px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 12px;
  background: rgba(255, 255, 255, 0.75);
  border: 1.5px solid rgba(255, 255, 255, 0.95);
  border-radius: 9999px;
  box-shadow: inset 0 1.5px 3px rgba(15, 23, 42, 0.05), 0 2px 8px rgba(15, 23, 42, 0.03);
  transition: all 0.2s ease;
}
.nav-search-slot:focus-within {
  background: #ffffff;
  border-color: #38bdf8;
  box-shadow: 0 0 0 3px rgba(56, 189, 248, 0.25), 0 6px 16px rgba(15, 23, 42, 0.06);
}
.nav-search-slot .el-icon {
  color: #64748b;
  font-size: 13px;
}
.nav-search-slot input {
  flex: 1;
  border: 0;
  outline: 0;
  background: transparent;
  font-size: 11px;
  color: var(--text-main);
  font-weight: 500;
}
.nav-search-slot input::placeholder {
  color: #94a3b8;
}
.nav-search-slot kbd {
  font-size: 9px;
  color: #64748b;
  background: rgba(241, 245, 249, 0.95);
  border: 1px solid rgba(203, 213, 225, 0.9);
  border-radius: 4px;
  padding: 1px 5px;
  font-family: var(--el-font-family-monospace, monospace);
  font-weight: 700;
}

.nav-actions-group {
  position: relative;
  z-index: 4;
  display: flex;
  align-items: center;
  gap: 8px;
}
.glass-icon-btn {
  width: 32px;
  height: 32px;
  border-radius: 10px;
  border: 1.5px solid rgba(255, 255, 255, 0.95);
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.9) 0%, rgba(241, 245, 249, 0.65) 100%);
  backdrop-filter: blur(16px);
  color: #475569;
  display: grid;
  place-items: center;
  cursor: pointer;
  transition: all 0.18s cubic-bezier(0.16, 1, 0.3, 1);
  box-shadow: 0 4px 10px rgba(15, 23, 42, 0.05), inset 0 1.5px 1px #ffffff;
}
.glass-icon-btn:hover {
  background: #ffffff;
  border-color: #0284c7;
  color: #0284c7;
  transform: translateY(-2px);
  box-shadow: 0 8px 18px -2px rgba(2, 132, 199, 0.25), inset 0 1.5px 1px #ffffff;
}
.is-spinning {
  animation: spin 1s linear infinite;
}
@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

/* 果冻水晶药丸按钮 (对齐图片 31/32/33 的 Create Workspace / Start project 按钮) */
.glass-cta-btn {
  height: 32px;
  padding: 0 14px;
  border-radius: 9999px;
  border: 1.8px solid rgba(255, 255, 255, 0.95);
  background: linear-gradient(180deg, #38bdf8 0%, #0284c7 100%);
  color: #ffffff;
  font-size: 11.5px;
  font-weight: 800;
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  box-shadow: 0 10px 24px -2px rgba(2, 132, 199, 0.48), 0 3px 8px rgba(15, 23, 42, 0.08), inset 0 2px 2px rgba(255, 255, 255, 0.9), inset 0 -2px 3px rgba(0, 0, 0, 0.2);
  transition: all 0.18s cubic-bezier(0.16, 1, 0.3, 1);
}
.glass-cta-btn:hover {
  transform: translateY(-2px) scale(1.02);
  box-shadow: 0 14px 30px -2px rgba(2, 132, 199, 0.6), 0 4px 10px rgba(15, 23, 42, 0.1), inset 0 2px 2px rgba(255, 255, 255, 0.95);
}

/* ════════ 2. 四大核心遥测厚水晶指标卡 ════════ */
.glass-metrics-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
  flex-shrink: 0;
  height: 84px;
}
.glass-metric-card {
  position: relative;
  background: var(--glass-bg);
  backdrop-filter: blur(28px) saturate(200%);
  -webkit-backdrop-filter: blur(28px) saturate(200%);
  border: 1.5px solid var(--glass-border);
  border-top: 2px solid var(--glass-border-light);
  border-radius: 16px;
  box-shadow: var(--glass-shadow), var(--glass-inset);
  padding: 9px 13px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  cursor: pointer;
  user-select: none;
  overflow: hidden;
  transition: all 0.24s cubic-bezier(0.16, 1, 0.3, 1);
}
.glass-metric-card:hover {
  transform: translateY(-3.5px) scale(1.012);
  border-color: rgba(56, 189, 248, 0.6);
  box-shadow: 0 20px 40px -6px rgba(2, 132, 199, 0.18), 0 8px 18px rgba(15, 23, 42, 0.05), var(--glass-inset);
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.92) 0%, rgba(255, 255, 255, 0.65) 100%);
}

.metric-top-row {
  position: relative;
  z-index: 4;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.metric-icon-orb {
  width: 26px;
  height: 26px;
  border-radius: 9px;
  border: 1.2px solid rgba(255, 255, 255, 0.95);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  color: #ffffff;
  box-shadow: inset 0 1.5px 1.5px rgba(255, 255, 255, 0.9), inset 0 -1.5px 2px rgba(0, 0, 0, 0.18);
}
.blue-orb { background: linear-gradient(180deg, #38bdf8 0%, #0284c7 100%); box-shadow: 0 6px 14px -2px rgba(2, 132, 199, 0.45); }
.emerald-orb { background: linear-gradient(180deg, #34d399 0%, #059669 100%); box-shadow: 0 6px 14px -2px rgba(5, 150, 105, 0.45); }
.cyan-orb { background: linear-gradient(180deg, #22d3ee 0%, #0891b2 100%); box-shadow: 0 6px 14px -2px rgba(8, 145, 178, 0.45); }
.violet-orb { background: linear-gradient(180deg, #c084fc 0%, #7c3aed 100%); box-shadow: 0 6px 14px -2px rgba(124, 58, 237, 0.45); }

.metric-tag {
  font-size: 11.5px;
  font-weight: 800;
  color: var(--text-main);
  flex: 1;
  margin-left: 8px;
}
.metric-link-hint {
  font-size: 11px;
  color: #94a3b8;
  opacity: 0.6;
  transition: all 0.15s ease;
}
.glass-metric-card:hover .metric-link-hint {
  color: #0284c7;
  opacity: 1;
  transform: translateX(2px);
}
.metric-value-row {
  position: relative;
  z-index: 4;
  display: flex;
  align-items: baseline;
  gap: 4px;
}
.metric-number {
  font: 800 22px/1 var(--el-font-family-monospace, monospace);
  color: var(--text-main);
  letter-spacing: -0.02em;
}
.metric-unit {
  font-size: 10px;
  color: var(--text-muted);
  font-weight: 600;
}
.metric-footer-row {
  position: relative;
  z-index: 4;
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 9.5px;
  color: var(--text-muted);
  font-weight: 600;
}
.metric-highlight {
  font-weight: 800;
  color: #0284c7;
}
.metric-highlight.emerald { color: #059669; }
.metric-highlight.violet { color: #7c3aed; }

/* ════════ 3. 核心工作区 (Liquid Glass Workspace) ════════ */
.glass-workspace-layout {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 280px;
  gap: 8px;
  overflow: hidden;
}

/* 主面板：10 模块厚水晶玻璃卡片阵列 */
.glass-main-panel {
  position: relative;
  background: var(--glass-bg);
  backdrop-filter: blur(28px) saturate(200%);
  -webkit-backdrop-filter: blur(28px) saturate(200%);
  border: 1.5px solid var(--glass-border);
  border-top: 2px solid var(--glass-border-light);
  border-radius: 16px;
  box-shadow: var(--glass-shadow), var(--glass-inset);
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

.glass-panel-header {
  position: relative;
  z-index: 4;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
  flex-shrink: 0;
}
.panel-header-left {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.panel-title {
  margin: 0;
  font-size: 13.5px;
  font-weight: 800;
  color: var(--text-main);
  letter-spacing: -0.01em;
}
.panel-subtitle {
  font-size: 10px;
  color: var(--text-muted);
}
.active-node-count {
  font: 800 9.5px/1 var(--el-font-family-monospace, monospace);
  color: #0284c7;
  background: rgba(2, 132, 199, 0.12);
  border: 1px solid rgba(2, 132, 199, 0.28);
  border-radius: 9999px;
  padding: 2px 8px;
}

.glass-modules-grid {
  position: relative;
  z-index: 4;
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  grid-template-rows: repeat(2, 1fr);
  gap: 6px;
}

/* 10 个独立厚水晶玻璃小组件 (如图片 31/32/33 中的 Tabs / Card 小面板) */
.glass-module-card {
  position: relative;
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.82) 0%, rgba(255, 255, 255, 0.5) 50%, rgba(248, 250, 252, 0.65) 100%);
  backdrop-filter: blur(24px) saturate(200%);
  -webkit-backdrop-filter: blur(24px) saturate(200%);
  border: 1.5px solid rgba(255, 255, 255, 0.95);
  border-top: 2px solid #ffffff;
  border-radius: 14px;
  box-shadow:
    0 8px 22px -4px rgba(15, 23, 42, 0.05),
    inset 0 1.5px 1.5px #ffffff,
    inset 1px 0 1.5px rgba(255, 255, 255, 0.8),
    inset -1px 0 1.5px rgba(255, 255, 255, 0.5),
    inset 0 -1.5px 2px rgba(148, 163, 184, 0.15);
  padding: 9px 11px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  cursor: pointer;
  user-select: none;
  overflow: hidden;
  transition: all 0.24s cubic-bezier(0.16, 1, 0.3, 1);
}

.glass-module-card:hover {
  transform: translateY(-4px) scale(1.015);
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.95) 0%, rgba(255, 255, 255, 0.7) 100%);
  border-color: rgba(56, 189, 248, 0.7);
  box-shadow:
    0 18px 36px -4px rgba(2, 132, 199, 0.22),
    0 6px 14px rgba(15, 23, 42, 0.06),
    inset 0 2px 2px #ffffff;
}

.module-card-top {
  position: relative;
  z-index: 4;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

/* 3D 拟态水晶图标球 (如图片 31/32/33 中的圆形/圆角凸起图标) */
.module-icon-box {
  width: 32px;
  height: 32px;
  border-radius: 10px;
  border: 1.5px solid rgba(255, 255, 255, 0.95);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  color: #ffffff;
  box-shadow: inset 0 2px 2px rgba(255, 255, 255, 0.92), inset 0 -2px 3px rgba(0, 0, 0, 0.2);
}
.module-icon-box.accent-ice {
  background: linear-gradient(180deg, #38bdf8 0%, #0284c7 100%);
  box-shadow: 0 8px 18px -2px rgba(2, 132, 199, 0.45), inset 0 2px 2px rgba(255, 255, 255, 0.9);
}
.module-icon-box.accent-green {
  background: linear-gradient(180deg, #34d399 0%, #059669 100%);
  box-shadow: 0 8px 18px -2px rgba(5, 150, 105, 0.45), inset 0 2px 2px rgba(255, 255, 255, 0.9);
}
.module-icon-box.accent-silver {
  background: linear-gradient(180deg, #c084fc 0%, #7c3aed 100%);
  box-shadow: 0 8px 18px -2px rgba(124, 58, 237, 0.45), inset 0 2px 2px rgba(255, 255, 255, 0.9);
}
.module-icon-box.accent-warm {
  background: linear-gradient(180deg, #fb923c 0%, #ea580c 100%);
  box-shadow: 0 8px 18px -2px rgba(234, 88, 12, 0.45), inset 0 2px 2px rgba(255, 255, 255, 0.9);
}
.module-icon-box.accent-mist {
  background: linear-gradient(180deg, #2dd4bf 0%, #0891b2 100%);
  box-shadow: 0 8px 18px -2px rgba(8, 145, 178, 0.45), inset 0 2px 2px rgba(255, 255, 255, 0.9);
}

.module-top-pills {
  display: flex;
  align-items: center;
  gap: 4px;
}
.module-num-pill {
  font: 800 9px/1 var(--el-font-family-monospace, monospace);
  color: #475569;
  background: rgba(255, 255, 255, 0.8);
  border: 1px solid rgba(255, 255, 255, 0.95);
  border-radius: 4px;
  padding: 2px 5px;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04);
}
.module-state-pill {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 8.5px;
  font-weight: 700;
  color: #0369a1;
  background: rgba(2, 132, 199, 0.12);
  border: 1px solid rgba(2, 132, 199, 0.22);
  border-radius: 9999px;
  padding: 1.5px 6px;
}
.state-dot {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: #0284c7;
  box-shadow: 0 0 5px #0284c7;
}

.module-card-mid {
  position: relative;
  z-index: 4;
  margin: 3px 0 2px 0;
}
.module-title {
  margin: 0;
  font-size: 13px;
  font-weight: 800;
  color: #0f172a;
  letter-spacing: -0.01em;
  line-height: 1.25;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.module-desc {
  margin: 2px 0 4px 0;
  font-size: 9.5px;
  color: #475569;
  font-weight: 500;
  line-height: 1.25;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.module-tag-row {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}
.glass-tag {
  font-size: 8.5px;
  font-weight: 600;
  color: #475569;
  background: rgba(255, 255, 255, 0.7);
  border: 1px solid rgba(255, 255, 255, 0.9);
  padding: 1.5px 6px;
  border-radius: 4px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.03);
  white-space: nowrap;
}

.module-card-bottom {
  position: relative;
  z-index: 4;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 4px;
  border-top: 1px solid rgba(255, 255, 255, 0.65);
}
.module-metric-hint {
  font-size: 9px;
  font-weight: 700;
  color: #0284c7;
}
.module-enter-btn {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.85);
  border: 1px solid rgba(255, 255, 255, 0.95);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 10px;
  color: #475569;
  box-shadow: 0 2px 6px rgba(15, 23, 42, 0.06);
  transition: all 0.18s ease;
}
.glass-module-card:hover .module-enter-btn {
  background: #0284c7;
  border-color: #0284c7;
  color: #ffffff;
  transform: translateX(2px);
  box-shadow: 0 4px 10px rgba(2, 132, 199, 0.4);
}

/* 右侧：实时系统雷达 & 智能风控 */
.glass-side-panel {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-height: 0;
  overflow: hidden;
}
.glass-subcard {
  position: relative;
  background: var(--glass-bg);
  backdrop-filter: blur(28px) saturate(200%);
  -webkit-backdrop-filter: blur(28px) saturate(200%);
  border: 1.5px solid var(--glass-border);
  border-top: 2px solid var(--glass-border-light);
  border-radius: 16px;
  box-shadow: var(--glass-shadow), var(--glass-inset);
  padding: 10px 12px;
  overflow: hidden;
}
.subcard-head {
  position: relative;
  z-index: 4;
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.subcard-head h3 {
  margin: 0;
  font-size: 12px;
  font-weight: 800;
  color: var(--text-main);
  display: flex;
  align-items: center;
  gap: 5px;
}
.subcard-badge {
  font: 800 8.5px/1 var(--el-font-family-monospace, monospace);
  color: #059669;
  background: rgba(16, 185, 129, 0.15);
  border: 1px solid rgba(16, 185, 129, 0.35);
  padding: 2px 6px;
  border-radius: 9999px;
}
.radar-stat-item {
  position: relative;
  z-index: 4;
  margin-bottom: 8px;
}
.radar-stat-item:last-child {
  margin-bottom: 0;
}
.stat-item-label {
  display: flex;
  justify-content: space-between;
  font-size: 10px;
  color: var(--text-muted);
  font-weight: 600;
  margin-bottom: 4px;
}
.stat-item-label b {
  color: var(--text-main);
  font-family: var(--el-font-family-monospace, monospace);
}
.glass-progress-bar {
  height: 6px;
  border-radius: 9999px;
  background: rgba(203, 213, 225, 0.6);
  box-shadow: inset 0 1px 2px rgba(15, 23, 42, 0.1);
  overflow: hidden;
}
.progress-fill {
  height: 100%;
  border-radius: 9999px;
  transition: width 0.35s ease;
}
.cyan-fill {
  background: linear-gradient(90deg, #38bdf8 0%, #0284c7 100%);
  box-shadow: 0 0 10px rgba(2, 132, 199, 0.6);
}
.green-fill {
  background: linear-gradient(90deg, #34d399 0%, #059669 100%);
  box-shadow: 0 0 10px rgba(5, 150, 105, 0.6);
}

.advisory-card {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  justify-content: center;
}
.glass-advice-list {
  position: relative;
  z-index: 4;
  margin: 0;
  padding-left: 14px;
  font-size: 9.5px;
  color: var(--text-muted);
  font-weight: 500;
  line-height: 1.55;
}
.glass-advice-list li {
  margin-bottom: 4px;
}
.glass-advice-list li b {
  color: #0284c7;
  font-weight: 700;
}

.quick-action-subcard {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px 10px;
}
.glass-action-btn {
  position: relative;
  z-index: 4;
  height: 30px;
  border-radius: 9999px;
  border: 1.5px solid transparent;
  font-size: 11px;
  font-weight: 800;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  cursor: pointer;
  transition: all 0.18s cubic-bezier(0.16, 1, 0.3, 1);
}
.glass-action-btn.primary {
  background: linear-gradient(180deg, #38bdf8 0%, #0284c7 100%);
  color: #ffffff;
  border-color: rgba(255, 255, 255, 0.9);
  box-shadow: 0 8px 20px -2px rgba(2, 132, 199, 0.45), inset 0 1.5px 1.5px rgba(255, 255, 255, 0.9);
}
.glass-action-btn.primary:hover {
  transform: translateY(-2px);
  box-shadow: 0 12px 26px -2px rgba(2, 132, 199, 0.55);
}
.glass-action-btn.secondary {
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.9) 0%, rgba(241, 245, 249, 0.7) 100%);
  border-color: rgba(255, 255, 255, 0.95);
  color: #334155;
  box-shadow: 0 4px 10px rgba(15, 23, 42, 0.05), inset 0 1.5px 1px #ffffff;
}
.glass-action-btn.secondary:hover {
  background: #ffffff;
  border-color: #0284c7;
  color: #0284c7;
  transform: translateY(-2px);
}

/* ════════ 4. 底部厚水晶状态栏 ════════ */
.glass-status-footer {
  position: relative;
  height: 32px;
  flex-shrink: 0;
  background: var(--glass-bg);
  backdrop-filter: blur(28px) saturate(200%);
  -webkit-backdrop-filter: blur(28px) saturate(200%);
  border: 1.5px solid var(--glass-border);
  border-top: 2px solid var(--glass-border-light);
  border-radius: 12px;
  box-shadow: 0 4px 14px rgba(15, 23, 42, 0.04), var(--glass-inset);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 12px;
  font-size: 10px;
  color: var(--text-muted);
  font-weight: 500;
  overflow: hidden;
}
.footer-left-status {
  position: relative;
  z-index: 4;
  display: flex;
  align-items: center;
  gap: 6px;
}
.live-pulse-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #10b981;
  box-shadow: 0 0 8px #10b981;
}
.footer-left-status b {
  color: #0284c7;
  font-weight: 800;
}
.footer-right-shortcuts {
  position: relative;
  z-index: 4;
  display: flex;
  gap: 4px;
}
.status-shortcut-btn {
  height: 22px;
  padding: 0 8px;
  border-radius: 6px;
  border: 1px solid rgba(255, 255, 255, 0.9);
  background: rgba(255, 255, 255, 0.7);
  color: #475569;
  font-size: 9.5px;
  font-weight: 700;
  cursor: pointer;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.03);
  transition: all 0.15s ease;
}
.status-shortcut-btn:hover {
  background: #ffffff;
  border-color: #0284c7;
  color: #0284c7;
  transform: translateY(-1px);
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
