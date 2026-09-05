<script setup>
import { computed, onActivated, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  Setting,
  Message,
  Check,
  Connection,
  Search,
  Refresh,
  Money,
  Goods,
  InfoFilled,
  View,
  Hide,
  Platform,
} from '@element-plus/icons-vue'
import {
  getMailConfig,
  getMailProviders,
  saveMailConfig,
  testMail,
  fetchCfDomains,
  fetchRemailProjects,
} from '@/api/settings'
import FooterToolbar from '@/components/FooterToolbar.vue'

// 统一归一化邮箱后缀（智能兼容 gmail, gamil, gmail变种, icloud 等各种常见写法）
function canonicalizeSuffix(val) {
  const raw = (val || '').trim().toLowerCase()
  if (!raw) return 'gmail.com'
  if (['gmail_variant', 'gmail变种', 'gmail-variant', 'gmail+', 'variant', 'jiahao'].includes(raw)) {
    return 'gmail_variant'
  }
  if (['gmail', 'gmail.com', 'gamil', 'gamil.com', '@gmail.com', '@gmail'].includes(raw)) {
    return 'gmail.com'
  }
  if (['icloud', 'icloud.com', '@icloud.com', 'apple'].includes(raw)) {
    return 'icloud.com'
  }
  if (['outlook', 'outlook.com', '@outlook.com', 'microsoft'].includes(raw)) {
    return 'outlook.com'
  }
  if (['hotmail', 'hotmail.com', '@hotmail.com'].includes(raw)) {
    return 'hotmail.com'
  }
  return raw
}

// 默认内置的 4 大邮箱渠道（防止初次渲染空白）
const DEFAULT_PROVIDERS = [
  {
    kind: 'remail',
    display_name: '🍎 Remail 自动购号 (iCloud / 微软临时邮箱)',
    pooled: false,
    ephemeral: true,
    config_fields: [
      { key: 'remail_api_key', label: 'API Key' },
      { key: 'remail_project_id', label: '项目 ID' },
      { key: 'remail_email_suffix', label: '邮箱后缀' },
      { key: 'remail_service_mode', label: '服务模式' },
      { key: 'remail_base_url', label: '平台 API 地址' },
      { key: 'remail_max_recycle_retries', label: '最大重试复用次数' },
    ],
  },
  {
    kind: 'cf_temp',
    display_name: '⚡ CF Worker 域名临时邮箱',
    pooled: false,
    ephemeral: true,
    config_fields: [
      { key: 'cf_api_url', label: 'Worker API 地址' },
      { key: 'cf_domain', label: '域名' },
      { key: 'cf_admin_token', label: 'Admin Token' },
      { key: 'cf_site_password', label: '网站密码' },
    ],
  },
  { kind: 'outlook', display_name: '📦 微软 Outlook 接码池', pooled: true, ephemeral: false, line_segments: 4, config_fields: [] },
  { kind: 'icloud_relay', display_name: '✉️ iCloud 隐藏邮箱 (中转)', pooled: true, ephemeral: false, line_segments: 2, config_fields: [] },
]

function formatRemailStock(num) {
  if (num === undefined || num === null) return ''
  const n = Number(num)
  if (n <= 0) return '0'
  if (n >= 1e9) return `${(n / 1e9).toFixed(n % 1e9 === 0 ? 0 : 1)}B`
  if (n >= 1e6) return `${(n / 1e6).toFixed(n % 1e6 === 0 ? 0 : 1)}M`
  if (n >= 1e3) return `${(n / 1e3).toFixed(n % 1e3 === 0 ? 0 : 1)}K`
  return String(n)
}

// 默认内置的 Remail 项目列表（开箱即用，无需等待远程请求）
const DEFAULT_REMAIL_PROJECTS = [
  {
    id: 2,
    name: 'chatgpt',
    targetPlatform: 'https://chatgpt.com/',
    mailRuleCount: 5,
    is_chatgpt: true,
    products: [
      { type: 'icloud', purchasePrice: 60.0, codePrice: 60.0, totalAvailable: 3894, suffixes: [{ suffix: 'icloud.com', totalAvailable: 3894 }] },
      { type: 'microsoft', purchasePrice: 15.0, codePrice: 10.0, totalAvailable: 380561, suffixes: [
        { suffix: 'outlook.com', totalAvailable: 378959 },
        { suffix: 'hotmail.com', totalAvailable: 213 },
        { suffix: 'outlook.com.au', totalAvailable: 1112 },
        { suffix: 'outlook.sa', totalAvailable: 325 },
        { suffix: 'outlook.co.nz', totalAvailable: 12 },
      ] },
      { type: 'gmail', purchasePrice: 200.0, codePrice: 100.0, totalAvailable: 1075070, suffixes: [{ suffix: 'gmail.com', totalAvailable: 1075070 }] },
      { type: 'gmail_variant', purchasePrice: 10.0, codePrice: 6.0, totalAvailable: 1000000000, suffixes: [{ suffix: 'gmail_variant', totalAvailable: 1000000000 }] },
      { type: 'domain', purchasePrice: 0.02, codePrice: 0.01, totalAvailable: 0, suffixes: [{ suffix: 'domain', totalAvailable: 0 }] },
    ],
  },
  {
    id: 110,
    name: 'cloudflare',
    targetPlatform: 'https://www.cloudflare.com/',
    mailRuleCount: 4,
    products: [
      { type: 'microsoft', purchasePrice: 10.0, codePrice: 8.0, suffixes: [{ suffix: 'outlook.com', totalAvailable: 9999 }, { suffix: 'hotmail.com', totalAvailable: 9999 }] },
    ],
  },
  {
    id: 73,
    name: 'sub2api',
    targetPlatform: 'https://github.com/Wei-Shaw/sub2api.git',
    mailRuleCount: 8,
    products: [
      { type: 'microsoft', purchasePrice: 10.0, codePrice: 8.0, suffixes: [{ suffix: 'outlook.com', totalAvailable: 9999 }, { suffix: 'hotmail.com', totalAvailable: 9999 }] },
    ],
  },
  {
    id: 84,
    name: 'Apple',
    targetPlatform: 'https://account.apple.com',
    mailRuleCount: 3,
    products: [
      { type: 'microsoft', purchasePrice: 10.0, codePrice: 8.0, suffixes: [{ suffix: 'outlook.com', totalAvailable: 9999 }] },
    ],
  },
]

const providers = ref(DEFAULT_PROVIDERS)
const source = ref('remail')
const form = ref({
  remail_api_key: 'rk-a18f1eed-cc59-4eaf-9c5f-ac4d711c758d',
  remail_project_id: '2',
  remail_email_suffix: 'outlook.com',
  remail_service_mode: 'purchase',
  remail_max_recycle_retries: 3,
  remail_base_url: 'https://remail.aishop6.com',
  cf_api_url: 'https://mail-api.shaosiming.online',
  cf_domain: 'yhmsiming.site',
})
const saved = ref({})
const loading = ref(false)
const saving = ref(false)
const testing = ref(false)

// CF Temp
const fetchingDomains = ref(false)
const discoveredDomains = ref([])

// Remail (aishop6)
const fetchingRemail = ref(false)
const remailWallet = ref({
  consumerBalance: '9780.00',
  historicalSpend: '270.00',
  orderCount: 9,
})
const remailProjects = ref(DEFAULT_REMAIL_PROJECTS)

const current = computed(
  () => providers.value.find((p) => p.kind === source.value) || null,
)
const fields = computed(() => current.value?.config_fields || [])
const canTest = computed(() => !!current.value && !current.value.pooled)

const domainPresets = computed(() => {
  const list = [
    { label: '🌟 yhmsiming.site (主推荐)', value: 'yhmsiming.site' },
    { label: '🌐 shaosiming.online (备用)', value: 'shaosiming.online' },
    { label: '🔀 双域名自动轮换', value: 'yhmsiming.site, shaosiming.online' },
  ]
  for (const d of discoveredDomains.value) {
    if (!list.some((p) => p.value === d)) {
      list.push({ label: `🌐 ${d}`, value: d })
    }
  }
  return list
})

// Remail 推荐项目预设
const remailRecommendedProjects = [
  { id: 2, name: 'ChatGPT 专属', desc: 'OpenAI 官方专属 · 5条收信规则 · 支持 Gmail/iCloud/Outlook', tag: '★★★★★ 推荐', type: 'success' },
  { id: 110, name: 'Cloudflare', desc: 'CF 通用收信', tag: '备用', type: 'info' },
  { id: 73, name: 'Sub2API', desc: 'Sub2API 认证专用', tag: '推荐', type: 'primary' },
  { id: 84, name: 'Apple 官方', desc: 'Apple ID / iCloud 验证', tag: '专用', type: 'warning' },
  { id: 106, name: 'Windsurf', desc: 'Windsurf AI 专用', tag: '通用', type: 'info' },
  { id: 90, name: 'Mistral AI', desc: 'Mistral 专属收信', tag: '通用', type: 'info' },
]

// 当前所选 Remail 项目对象
const currentRemailProject = computed(() => {
  const pid = parseInt(form.value.remail_project_id, 10) || 2
  return remailProjects.value.find((p) => p.id === pid) || DEFAULT_REMAIL_PROJECTS[0]
})

function formatSuffixLabel(sname, price, stock, ptype) {
  const stockText = stock ? ` · 库${formatRemailStock(stock)}` : ''
  if (sname === 'icloud.com') {
    return `🍏 icloud.com (苹果隐藏邮箱 · ${price}积分${stockText} · 推荐 ★★★★★)`
  } else if (sname === 'outlook.com') {
    return `📧 outlook.com (微软常用 · ${price}积分${stockText} · 推荐)`
  } else if (sname === 'gmail_variant') {
    return `⚡ gmail_variant (Gmail加号变种 · ${price}积分${stockText} · 性价比高)`
  } else if (sname === 'gmail.com') {
    return `🇬 gmail.com (Gmail官方点号 · ${price}积分${stockText})`
  } else if (sname === 'hotmail.com') {
    return `📮 hotmail.com (微软备用 · ${price}积分${stockText})`
  } else if (sname === 'domain') {
    return `🌐 domain (自备/公开域名邮箱 · ${price}积分${stockText})`
  } else if (sname.startsWith('outlook.')) {
    return `📫 ${sname} (微软海外 · ${price}积分${stockText})`
  }
  return `${sname} (${price}积分${stockText} · ${ptype})`
}

// 推荐后缀排序权重
const SUFFIX_ORDER_WEIGHT = {
  'icloud.com': 100,
  'outlook.com': 90,
  'gmail_variant': 80,
  'gmail.com': 70,
  'hotmail.com': 60,
  'domain': 50,
  'outlook.com.au': 40,
  'outlook.sa': 30,
  'outlook.co.nz': 20,
}

// 当前选中的 Remail 项目支持的所有后缀及价格
const currentProjectSuffixOptions = computed(() => {
  const proj = currentRemailProject.value
  const result = []
  if (!proj || !Array.isArray(proj.products) || proj.products.length === 0) {
    return [
      { suffix: 'icloud.com', label: '🍏 icloud.com (苹果隐藏邮箱 · 60.00积分 · 推荐 ★★★★★)', price: '60.00', type: 'icloud', stock: 3894 },
      { suffix: 'outlook.com', label: '📧 outlook.com (微软常用 · 15.00积分 · 推荐)', price: '15.00', type: 'microsoft', stock: 378959 },
      { suffix: 'gmail_variant', label: '⚡ gmail_variant (Gmail加号变种 · 10.00积分 · 库1B · 性价比高)', price: '10.00', type: 'gmail_variant', stock: 1000000000 },
      { suffix: 'gmail.com', label: '🇬 gmail.com (Gmail官方点号 · 200.00积分 · 库1.1M)', price: '200.00', type: 'gmail', stock: 1075070 },
      { suffix: 'hotmail.com', label: '📮 hotmail.com (微软备用 · 15.00积分)', price: '15.00', type: 'microsoft', stock: 213 },
      { suffix: 'domain', label: '🌐 domain (自备/公开域名 · 0.02积分)', price: '0.02', type: 'domain', stock: 0 },
    ]
  }

  for (const prod of proj.products || []) {
    const ptype = prod?.type || '邮箱'
    let pPrice = '15.00'
    if (prod && prod.purchasePrice != null) {
      pPrice = typeof prod.purchasePrice === 'number' ? prod.purchasePrice.toFixed(2) : String(prod.purchasePrice)
    }
    for (const s of prod?.suffixes || []) {
      const sname = (s?.suffix || '').trim().toLowerCase()
      if (sname && !result.some((r) => r.suffix.toLowerCase() === sname)) {
        const stock = s?.totalAvailable || prod?.totalAvailable || 0
        const label = formatSuffixLabel(sname, pPrice, stock, ptype)
        result.push({
          suffix: sname,
          label,
          price: pPrice,
          type: ptype,
          stock,
          weight: SUFFIX_ORDER_WEIGHT[sname] || 10,
        })
      }
    }
  }

  // 保证核心官方推荐后缀全部齐备
  if (!result.some((r) => r.suffix.toLowerCase() === 'icloud.com')) {
    result.push({ suffix: 'icloud.com', label: '🍏 icloud.com (苹果隐藏邮箱 · 60.00积分 · 推荐 ★★★★★)', price: '60.00', type: 'icloud', stock: 3894, weight: 100 })
  }
  if (!result.some((r) => r.suffix.toLowerCase() === 'gmail_variant')) {
    result.push({ suffix: 'gmail_variant', label: '⚡ gmail_variant (Gmail加号变种 · 10.00积分 · 库1B · 性价比高)', price: '10.00', type: 'gmail_variant', stock: 1000000000, weight: 80 })
  }
  if (!result.some((r) => r.suffix.toLowerCase() === 'gmail.com')) {
    result.push({ suffix: 'gmail.com', label: '🇬 gmail.com (Gmail官方点号 · 200.00积分 · 库1.1M)', price: '200.00', type: 'gmail', stock: 1075070, weight: 70 })
  }

  // 确保当前选中的自定义后缀在选项列表中呈现，防止回退丢失
  const curSuffix = canonicalizeSuffix(form.value?.remail_email_suffix || '')
  if (curSuffix && !result.some((r) => r.suffix.toLowerCase() === curSuffix.toLowerCase())) {
    result.push({
      suffix: curSuffix,
      label: `✉️ ${curSuffix} (已选)`,
      price: '自定',
      type: 'custom',
      stock: 9999,
      weight: 105,
    })
  }

  // 按权重降序排序，使最推荐常用后缀排在最前
  result.sort((a, b) => (b.weight || 0) - (a.weight || 0))
  return result
})

// 当前选定后缀的预估消耗价格说明
const currentSelectedSuffixPriceHint = computed(() => {
  const suffix = canonicalizeSuffix(form.value.remail_email_suffix || 'gmail.com')
  const matched = currentProjectSuffixOptions.value.find((s) => s.suffix.toLowerCase() === suffix.toLowerCase())
  if (matched) {
    return `当前选择后缀: ${suffix} · 预计单价: ${matched.price} 积分 / 每次购买`
  }
  return `当前选择后缀: ${suffix} · 预计单价: 约 10.00 ~ 200.00 积分`
})

function phFor(f) {
  return f.placeholder || ''
}

async function load() {
  testResultMsg.value = ''
  try {
    const [pr, cfg] = await Promise.allSettled([getMailProviders(), getMailConfig()])
    if (pr.status === 'fulfilled' && pr.value && Array.isArray(pr.value.providers)) {
      providers.value = pr.value.providers
    }
    if (cfg.status === 'fulfilled' && cfg.value && cfg.value.config) {
      saved.value = cfg.value.config
      if (saved.value.mail_source) {
        source.value = saved.value.mail_source
      }
    }

    const next = { ...form.value }
    for (const p of providers.value) {
      for (const f of p.config_fields || []) {
        if (saved.value[f.key] !== undefined && saved.value[f.key] !== '') {
          next[f.key] = saved.value[f.key]
        }
      }
    }
    // 强行合并 saved 中的全部已知属性，并做类型规范与别名纠错
    if (saved.value) {
      for (const k of Object.keys(saved.value)) {
        if (saved.value[k] !== undefined && saved.value[k] !== '') {
          next[k] = saved.value[k]
        }
      }
      if (saved.value.remail_email_suffix) {
        next.remail_email_suffix = canonicalizeSuffix(saved.value.remail_email_suffix)
      }
      if (saved.value.remail_max_recycle_retries !== undefined) {
        next.remail_max_recycle_retries = Number(saved.value.remail_max_recycle_retries) || 1
      }
    }
    form.value = next

    if (source.value === 'remail' && form.value.remail_api_key) {
      handleFetchRemailProjects(true)
    }
  } catch (e) {
    console.warn('[MailConfig] 加载提示:', e)
  }
}

async function handleFetchDomains(silent = false) {
  const apiUrl = (form.value.cf_api_url || saved.value.cf_api_url || '').trim()
  const token = (form.value.cf_admin_token || '').trim()
  if (!apiUrl) {
    if (!silent) ElMessage.warning('请先填写 Worker API 地址')
    return
  }
  fetchingDomains.value = true
  try {
    const res = await fetchCfDomains({
      api_url: apiUrl,
      admin_token: token || undefined,
    })
    discoveredDomains.value = res.domains || []
    if (discoveredDomains.value.length > 0) {
      if (!silent) ElMessage.success(`成功探测到 ${discoveredDomains.value.length} 个可用域名`)
      if (!form.value.cf_domain) {
        form.value.cf_domain = discoveredDomains.value[0]
        await save(false)
      }
    } else if (!silent) {
      ElMessage.info('Worker 未返回可用域名，可手动输入收件域名')
    }
  } catch (e) {
    if (!silent) ElMessage.error('探测域名失败: ' + (e.message || e))
  } finally {
    fetchingDomains.value = false
  }
}

async function handleFetchRemailProjects(silent = false) {
  const apiKey = (form.value.remail_api_key || saved.value.remail_api_key || '').trim()
  const baseUrl = (form.value.remail_base_url || '').trim()
  fetchingRemail.value = true
  try {
    const res = await fetchRemailProjects({
      api_key: apiKey || undefined,
      base_url: baseUrl || undefined,
    })
    if (res.wallet) {
      remailWallet.value = res.wallet
    }
    if (Array.isArray(res.projects) && res.projects.length > 0) {
      remailProjects.value = res.projects
      if (!silent) {
        ElMessage.success(`🎉 成功同步 Remail 开放平台 ${res.projects.length} 个项目及实时价格！`)
      }
    } else if (!silent) {
      ElMessage.info('已拉取 Remail 平台状态')
    }
  } catch (e) {
    if (!silent) ElMessage.error('拉取 Remail 项目与价格失败: ' + (e.message || e))
  } finally {
    fetchingRemail.value = false
  }
}

function selectRemailProject(pid) {
  form.value.remail_project_id = String(pid)
  save(false)
  ElMessage.success(`已切换项目 ID: ${pid}`)
}

function selectRemailSuffix(suffix) {
  const clean = canonicalizeSuffix(suffix)
  form.value.remail_email_suffix = clean
  save(false)
  ElMessage.success(`已选定邮箱后缀: ${clean}`)
}

function resetDefaultRemailKey() {
  form.value.remail_api_key = 'rk-a18f1eed-cc59-4eaf-9c5f-ac4d711c758d'
  form.value.remail_project_id = '2'
  form.value.remail_email_suffix = 'icloud.com'
  form.value.remail_service_mode = 'purchase'
  save(false)
  ElMessage.success('已恢复为官方默认专属配置 (Project 2 · icloud · 30积分)')
}

async function selectDomain(dom) {
  form.value.cf_domain = dom
  await save(false)
  ElMessage.success(`已切换并自动保存生效: ${dom}`)
}

async function handleSourceChange(val) {
  source.value = val
  if (val === 'remail') {
    handleFetchRemailProjects(true)
  }
  await save(false)
  ElMessage.success(`接信渠道已切换为: ${current.value?.display_name || val}`)
}

async function save(notify = true) {
  const payload = {
    mail_source: source.value,
    ...form.value,
  }

  if (source.value === 'remail') {
    payload.remail_project_id = String(form.value.remail_project_id || '2').trim()
    payload.remail_email_suffix = canonicalizeSuffix(form.value.remail_email_suffix)
    payload.remail_service_mode = (form.value.remail_service_mode || 'purchase').trim()
    payload.remail_base_url = (form.value.remail_base_url || 'https://remail.aishop6.com').trim()
    payload.remail_api_key = (form.value.remail_api_key || '').trim()
    payload.remail_max_recycle_retries = Number(form.value.remail_max_recycle_retries) || 1
  } else if (source.value === 'cf_temp') {
    payload.cf_api_url = (form.value.cf_api_url || '').trim()
    payload.cf_admin_token = (form.value.cf_admin_token || '').trim()
    payload.cf_domain = (form.value.cf_domain || '').trim()
    payload.cf_site_password = (form.value.cf_site_password || '').trim()
  }

  saving.value = true
  try {
    const res = await saveMailConfig(payload)
    saved.value = res.config || payload
    if (notify) ElMessage.success('🎉 邮箱配置已成功保存并写入数据库，重启不丢失！')
    return true
  } catch (e) {
    if (notify) ElMessage.error(e.message || '保存失败')
    return false
  } finally {
    saving.value = false
  }
}

function handleFieldChange() {
  save(false)
}

async function test() {
  testing.value = true
  testResultMsg.value = ''
  try {
    const r = await testMail()
    testResultMsg.value = r.message || '连通正常'
    if (Array.isArray(r.domains) && r.domains.length > 0) {
      discoveredDomains.value = r.domains
    }
    if (source.value === 'remail') {
      await handleFetchRemailProjects(true)
    }
    ElMessage.success('测试成功')
  } catch (e) {
    testResultMsg.value = '测试失败: ' + (e.message || e)
    ElMessage.error(e.message || '测试失败')
  } finally {
    testing.value = false
  }
}

onMounted(() => load())
onActivated(() => load())
</script>

<template>
  <div class="mailconfig-page">
    <div class="macos-window-panel">
      <div class="macos-panel-header">
        <div class="header-left">
          <div class="window-dot-group">
            <span class="dot red"></span>
            <span class="dot yellow"></span>
            <span class="dot green"></span>
          </div>
          <span class="panel-title">邮箱接收源配置 · Mail Preferences</span>
        </div>
        <div class="header-right">
          <span class="header-badge">当前生效: {{ current?.display_name || source }}</span>
        </div>
      </div>

      <div class="config-scroll-body">
        <div class="macos-settings-card">
          <!-- 渠道单选选择区 -->
          <div class="card-section">
            <span class="section-heading">选择验证码接信渠道</span>
            <el-radio-group v-model="source" class="macos-radio-group full-width-group" @change="handleSourceChange">
              <el-radio-button
                v-for="p in providers"
                :key="p.kind"
                :value="p.kind"
              >
                {{ p.display_name }}
              </el-radio-button>
            </el-radio-group>
          </div>

          <!-- 渠道特性标签 -->
          <div v-if="current" class="card-section info-section">
            <span class="section-heading">渠道特性</span>
            <div class="caps-tags">
              <el-tag size="small" :type="current.pooled ? 'warning' : 'success'" effect="light">
                {{ current.pooled ? '📦 号池型：需先批量导入账号' : '⚡ 自建型：动态自动生成地址' }}
              </el-tag>
              <el-tag size="small" :type="current.ephemeral ? 'success' : 'info'" effect="plain">
                {{ current.ephemeral ? '每次注册使用新地址' : '固定地址轮询' }}
              </el-tag>
              <el-tag v-if="current.kind === 'remail'" size="small" type="success" effect="plain">
                🍏 支持 Remail OpenAPI 自动下单、多项目容灾与验证码秒级提取
              </el-tag>
              <el-tag v-if="current.kind === 'cf_temp'" size="small" type="success" effect="plain">
                支持 dreamhunter2333 / cloudflare_temp_email Worker 协议
              </el-tag>
              <el-tag v-if="current.line_segments > 0" size="small" type="info" effect="plain">
                导入格式 {{ current.line_segments }} 段
              </el-tag>
            </div>
          </div>

          <!-- 🍎 Remail 专属高级可视化控制面板 -->
          <div v-if="source === 'remail'" class="card-section remail-panel">
            <div class="section-header-row">
              <div class="header-with-icon">
                <span class="remail-logo">🍎</span>
                <span class="section-heading">Remail 开放平台参数与项目配置</span>
              </div>
              <div class="header-actions">
                <el-button
                  size="small"
                  type="primary"
                  plain
                  :loading="fetchingRemail"
                  @click="handleFetchRemailProjects(false)"
                >
                  <el-icon><Refresh /></el-icon>探测平台项目与实时价格
                </el-button>
                <el-button size="small" text type="info" @click="resetDefaultRemailKey">
                  恢复默认推荐
                </el-button>
              </div>
            </div>

            <!-- 钱包余额与运行状态卡片 -->
            <div class="remail-wallet-bar">
              <div class="wallet-stat">
                <span class="stat-label">💰 钱包可用积分</span>
                <span class="stat-val highlight-green">{{ remailWallet ? remailWallet.consumerBalance : '9780.00' }} 积分</span>
              </div>
              <div class="wallet-divider"></div>
              <div class="wallet-stat">
                <span class="stat-label">📊 累计下单次数</span>
                <span class="stat-val">{{ remailWallet ? remailWallet.orderCount : 9 }} 次</span>
              </div>
              <div class="wallet-divider"></div>
              <div class="wallet-stat">
                <span class="stat-label">📉 累计消费积分</span>
                <span class="stat-val">{{ remailWallet ? remailWallet.historicalSpend : '270.00' }} 积分</span>
              </div>
            </div>

            <el-form label-position="top" size="small" class="remail-form">
              <!-- 1. 开放平台项目选择 -->
              <el-form-item label="1. 开放平台项目 (Project ID - 决定收信规则与目标平台)" required>
                <div class="remail-sub-card">
                  <!-- 快捷推荐胶囊 -->
                  <div class="presets-row">
                    <span class="preset-label">🌟 推荐专属项目:</span>
                    <div class="preset-capsules">
                      <el-button
                        v-for="p in remailRecommendedProjects"
                        :key="p.id"
                        size="small"
                        :type="String(form.remail_project_id) === String(p.id) ? 'primary' : 'default'"
                        class="preset-capsule-btn"
                        @click="selectRemailProject(p.id)"
                      >
                        {{ p.name }} ({{ p.id }}) · {{ p.tag }}
                      </el-button>
                    </div>
                  </div>

                  <!-- 下拉选择与手动输入 -->
                  <div class="project-selector-row">
                    <el-select
                      v-model="form.remail_project_id"
                      filterable
                      placeholder="从已探测项目列表选择 / 或输入数字 ID"
                      style="width: 100%"
                      @change="selectRemailProject"
                    >
                      <el-option
                        v-for="proj in remailProjects"
                        :key="proj.id"
                        :value="String(proj.id)"
                        :label="`[ID: ${proj.id}] ${proj.name} · ${proj.targetPlatform || '通用'} (规则数: ${proj.mailRuleCount})`"
                      >
                        <div class="project-opt-item">
                          <span class="opt-name">
                            <span v-if="proj.id === 2" class="gold-badge">ChatGPT 专属 ★</span>
                            <strong>#{{ proj.id }}</strong> {{ proj.name }}
                          </span>
                          <span class="opt-sub">{{ proj.targetPlatform }} ({{ proj.mailRuleCount }}条规则)</span>
                        </div>
                      </el-option>
                    </el-select>
                  </div>

                  <!-- 当前选中项目说明 -->
                  <div class="current-proj-desc" v-if="currentRemailProject">
                    <span class="proj-name-badge">✅ 已选: {{ currentRemailProject.name }} (ID: {{ currentRemailProject.id }})</span>
                    <span class="proj-detail">目标平台: {{ currentRemailProject.targetPlatform || '全网' }} | 收信规则: {{ currentRemailProject.mailRuleCount }} 条</span>
                  </div>
                </div>
              </el-form-item>

              <!-- 2. 邮箱后缀与价格类型选择 -->
              <el-form-item label="2. 购买邮箱后缀与类型 (Email Suffix & Pricing)" required>
                <div class="remail-sub-card">
                  <!-- 后缀胶囊列表 -->
                  <div class="presets-row">
                    <span class="preset-label">✉️ 可选后缀与价格:</span>
                    <div class="preset-capsules">
                      <el-button
                        v-for="s in currentProjectSuffixOptions"
                        :key="s.suffix"
                        size="small"
                        :type="canonicalizeSuffix(form.remail_email_suffix) === canonicalizeSuffix(s.suffix) ? 'primary' : 'default'"
                        class="preset-capsule-btn"
                        @click="selectRemailSuffix(s.suffix)"
                      >
                        {{ s.label }}
                      </el-button>
                    </div>
                  </div>

                  <!-- 自定义输入/选择框 -->
                  <div class="project-selector-row">
                    <el-select
                      v-model="form.remail_email_suffix"
                      filterable
                      allow-create
                      default-first-option
                      placeholder="选择支持的邮箱后缀或输入自定义后缀"
                      style="width: 100%"
                      @change="selectRemailSuffix"
                    >
                      <el-option
                        v-for="s in currentProjectSuffixOptions"
                        :key="s.suffix"
                        :value="s.suffix"
                        :label="s.label"
                      />
                    </el-select>
                  </div>

                  <div class="price-hint-bar">
                    <div class="price-hint-left">
                      <el-icon><Money /></el-icon>
                      <span>{{ currentSelectedSuffixPriceHint }}</span>
                    </div>
                    <el-button
                      size="small"
                      type="success"
                      plain
                      :loading="saving"
                      @click="save(true)"
                    >
                      <el-icon><Check /></el-icon>立即永久保存此后缀
                    </el-button>
                  </div>
                </div>
              </el-form-item>

              <!-- 3. 服务模式与复用重试上限 -->
              <el-row :gutter="12">
                <el-col :span="12">
                  <el-form-item label="3. 服务模式 (Service Mode)">
                    <el-radio-group v-model="form.remail_service_mode" class="mode-radio-group" @change="handleFieldChange">
                      <el-radio-button value="purchase">📦 purchase (长效独享购买 · 推荐)</el-radio-button>
                      <el-radio-button value="code">⚡ code (短效接码)</el-radio-button>
                    </el-radio-group>
                  </el-form-item>
                </el-col>
                <el-col :span="12">
                  <el-form-item label="4. 暂存邮箱最大重试复用次数 (相同账号上限)">
                    <el-input-number
                      v-model="form.remail_max_recycle_retries"
                      :min="1"
                      :max="10"
                      style="width: 100%"
                      @change="handleFieldChange"
                    />
                    <div class="hint-text" style="font-size: 11px; margin-top: 4px; color: var(--el-text-color-secondary);">
                      同一购买暂存邮箱失败重试超过此次数（默认 3 次）后自动放弃复用并重新购号。
                    </div>
                  </el-form-item>
                </el-col>
              </el-row>

              <!-- 4. 平台 API 地址与 Key -->
              <el-row :gutter="12">
                <el-col :span="12">
                  <el-form-item label="5. 平台 API 地址">
                    <el-input v-model="form.remail_base_url" placeholder="https://remail.aishop6.com" clearable @change="handleFieldChange" />
                  </el-form-item>
                </el-col>
                <el-col :span="12">
                  <el-form-item label="6. Remail API Key (平台密钥)" required>
                    <el-input
                      v-model="form.remail_api_key"
                      type="password"
                      show-password
                      placeholder="rk-a18f1eed-cc59-4eaf-9c5f-ac4d711c758d"
                      clearable
                      @change="handleFieldChange"
                    />
                  </el-form-item>
                </el-col>
              </el-row>

              <!-- 5. 卡片内常驻操作与保存条 -->
              <div class="card-embedded-save-bar">
                <div class="save-status-hint">
                  <el-icon color="#10b981"><Check /></el-icon>
                  <span>配置将持久化保存至本地数据库，服务重启后自动加载生效。</span>
                </div>
                <div class="save-btn-group">
                  <el-button :loading="testing" @click="test">
                    <el-icon><Connection /></el-icon>测试连通性与余额
                  </el-button>
                  <el-button type="primary" :loading="saving" @click="save(true)">
                    <el-icon><Check /></el-icon>保存配置并生效
                  </el-button>
                </div>
              </div>
            </el-form>
          </div>

          <!-- 通用渠道表单区（非 Remail 时展示） -->
          <div v-else-if="fields.length" class="card-section form-section">
            <div class="section-header-row">
              <span class="section-heading">渠道参数详情</span>
              <div class="header-actions">
                <el-button
                  v-if="source === 'cf_temp'"
                  size="small"
                  type="primary"
                  plain
                  :loading="fetchingDomains"
                  @click="handleFetchDomains(false)"
                >
                  <el-icon><Search /></el-icon>探测可用域名
                </el-button>
              </div>
            </div>

            <el-form label-position="top" size="small">
              <el-form-item
                v-for="f in fields"
                :key="f.key"
                :label="f.label"
                :required="f.required"
              >
                <!-- 针对收件域名的专属快捷切换与多域名轮换交互 -->
                <div v-if="f.key === 'cf_domain'" class="domain-input-wrapper">
                  <div class="domain-presets-row">
                    <span class="preset-title">⚡ 快捷选用域名:</span>
                    <div class="preset-buttons">
                      <el-button
                        v-for="preset in domainPresets"
                        :key="preset.value"
                        size="small"
                        :type="form[f.key] === preset.value ? 'primary' : 'default'"
                        class="preset-btn"
                        @click="selectDomain(preset.value)"
                      >
                        {{ preset.label }}
                      </el-button>
                    </div>
                  </div>

                  <el-input
                    v-model="form[f.key]"
                    placeholder="输入单个域名（如 yhmsiming.site）或多域名逗号分隔（如 yhmsiming.site, shaosiming.online）"
                    clearable
                    @change="handleFieldChange"
                  />

                  <div v-if="discoveredDomains.length > 0" class="discovered-domains-list">
                    <span class="hint-label">Worker 探测到的所有域名:</span>
                    <el-tag
                      v-for="d in discoveredDomains"
                      :key="d"
                      size="small"
                      :type="form[f.key]?.includes(d) ? 'primary' : 'info'"
                      class="domain-tag"
                      effect="plain"
                      @click="selectDomain(d)"
                    >
                      {{ d }}
                    </el-tag>
                  </div>
                  <div class="hint-text">支持填写单个收件域名；若填写多个域名（以英文逗号分隔），系统将在批量注册时自动随机轮询选用，避免单域名频率受限。</div>
                </div>

                <!-- 密钥类与常规字段：支持眼睛图标随时切换明文/密文 -->
                <div v-else class="generic-input-wrapper">
                  <el-input
                    v-model="form[f.key]"
                    :type="f.type === 'password' ? 'password' : 'text'"
                    :show-password="f.type === 'password'"
                    :placeholder="phFor(f)"
                    clearable
                    @change="handleFieldChange"
                  />
                  <div v-if="f.help" class="hint-text">{{ f.help }}</div>
                </div>
              </el-form-item>

              <!-- 通用表单底部保存条 -->
              <div class="card-embedded-save-bar">
                <div class="save-status-hint">
                  <el-icon color="#10b981"><Check /></el-icon>
                  <span>修改后点击保存，配置将持久化写入本地数据库，重启不丢失。</span>
                </div>
                <div class="save-btn-group">
                  <el-button v-if="canTest" :loading="testing" @click="test">
                    <el-icon><Connection /></el-icon>测试连通性
                  </el-button>
                  <el-button type="primary" :loading="saving" @click="save(true)">
                    <el-icon><Check /></el-icon>保存配置并生效 (重启保留)
                  </el-button>
                </div>
              </div>
            </el-form>
          </div>

          <div v-if="testResultMsg" class="test-result-box">
            <pre class="test-result-text">{{ testResultMsg }}</pre>
          </div>

          <el-alert
            v-if="source === 'cf_temp'"
            type="info"
            :closable="false"
            show-icon
            style="border-radius: 8px"
            title="Cloudflare 域名临时邮箱使用说明：在 Worker 中配置环境变量 ADMIN_PASSWORDS 与 DOMAINS，并在 Cloudflare Email Routing 设置 Catch-all 发送到该 Worker 即可无限动态生成邮箱并自动收码。"
          />
        </div>
      </div>

      <FooterToolbar>
        <template #right>
          <el-button v-if="canTest" :loading="testing" @click="test">
            <el-icon><Connection /></el-icon>测试连通性与钱包
          </el-button>
          <el-button type="primary" :loading="saving" @click="save">
            <el-icon><Check /></el-icon>保存配置并生效
          </el-button>
        </template>
      </FooterToolbar>
    </div>
  </div>
</template>

<style scoped>
.mailconfig-page {
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

.config-scroll-body {
  flex: 1;
  min-height: 0;
  padding: 20px 24px;
  overflow-y: auto;
}

.macos-settings-card {
  max-width: 760px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.card-section {
  background: var(--el-fill-color-light);
  border: 1px solid var(--app-border);
  border-radius: 10px;
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.full-width-group {
  width: 100%;
  display: flex;
}
.full-width-group :deep(.el-radio-button) {
  flex: 1;
}
.full-width-group :deep(.el-radio-button__inner) {
  width: 100%;
  text-align: center;
}

.section-header-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.header-with-icon {
  display: flex;
  align-items: center;
  gap: 8px;
}
.remail-logo {
  font-size: 16px;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.section-heading {
  font-size: 13px;
  font-weight: 700;
  color: var(--app-title);
}

.caps-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

/* Remail 专属样式 */
.remail-panel {
  border-color: var(--el-color-primary-light-5, #93c5fd);
  background: var(--app-card-bg);
}

.remail-wallet-bar {
  display: flex;
  align-items: center;
  justify-content: space-around;
  background: var(--el-fill-color);
  border: 1px solid var(--app-border);
  border-radius: 8px;
  padding: 10px 14px;
}
.wallet-stat {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
}
.stat-label {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}
.stat-val {
  font-size: 13px;
  font-weight: 700;
  color: var(--app-title);
}
.highlight-green {
  color: #10b981;
}
.wallet-divider {
  width: 1px;
  height: 24px;
  background: var(--app-border);
}

.remail-sub-card {
  width: 100%;
  background: var(--el-fill-color-light);
  border: 1px solid var(--app-border);
  border-radius: 8px;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.presets-row {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.preset-label {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
}
.preset-capsules {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.preset-capsule-btn {
  font-size: 11px;
  padding: 4px 8px;
  border-radius: 6px;
}

.project-opt-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
}
.opt-name {
  display: flex;
  align-items: center;
  gap: 6px;
}
.gold-badge {
  background: #f59e0b;
  color: #fff;
  font-size: 9px;
  font-weight: 800;
  padding: 1px 4px;
  border-radius: 3px;
}
.opt-sub {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.current-proj-desc {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11.5px;
  padding: 4px 8px;
  background: var(--el-fill-color);
  border-radius: 4px;
}
.proj-name-badge {
  font-weight: 700;
  color: #10b981;
}
.proj-detail {
  color: var(--el-text-color-secondary);
}

.price-hint-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 11.5px;
  color: #10b981;
  font-weight: 600;
  background: rgba(16, 185, 129, 0.08);
  border: 1px dashed #10b981;
  border-radius: 6px;
  padding: 6px 12px;
}
.price-hint-left {
  display: flex;
  align-items: center;
  gap: 6px;
}

.mode-radio-group {
  width: 100%;
  display: flex;
}
.mode-radio-group :deep(.el-radio-button) {
  flex: 1;
}
.mode-radio-group :deep(.el-radio-button__inner) {
  width: 100%;
  font-size: 11.5px;
}

.domain-input-wrapper, .generic-input-wrapper {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.domain-presets-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 2px;
}
.preset-title {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
}
.preset-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.preset-btn {
  font-size: 11.5px;
  padding: 4px 10px;
  border-radius: 6px;
}

.discovered-domains-list {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 4px;
}

.hint-label {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.domain-tag {
  cursor: pointer;
  transition: all 0.2s ease;
}

.domain-tag:hover {
  transform: translateY(-1px);
  box-shadow: 0 2px 5px rgba(0,0,0,0.1);
}

.hint-text {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  line-height: 1.4;
}

.test-result-box {
  background: var(--el-fill-color);
  border: 1px solid var(--app-border);
  border-radius: 8px;
  padding: 12px;
}
.test-result-text {
  font-family: monospace;
  font-size: 12px;
  color: var(--app-title);
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
}

.card-embedded-save-bar {
  margin-top: 14px;
  padding: 12px 16px;
  background: var(--el-fill-color);
  border: 1px solid var(--app-border);
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
}

.save-status-hint {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11.5px;
  color: var(--el-text-color-secondary);
}

.save-btn-group {
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>
