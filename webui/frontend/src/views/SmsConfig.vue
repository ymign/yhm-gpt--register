<script setup>
import { computed, onActivated, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Setting,
  Check,
  Connection,
  Phone,
  Wallet,
  Lock,
  Discount,
  Operation,
  InfoFilled,
  CircleCheckFilled,
  Location,
  Money,
  Ticket,
  Plus,
  Delete,
  Refresh,
  CopyDocument,
  WarningFilled,
  Document,
} from '@element-plus/icons-vue'
import {
  getSmsProviders,
  getSmsConfig,
  saveSmsConfig,
  testSms,
  getSmsAllCountries,
  getSmsPriceTiers,
  getSmsCdkPool,
  getSmsCdkPoolStats,
  importSmsCdks,
  updateSmsCdk,
  deleteSmsCdk,
  clearSmsCdkPool,
} from '@/api/settings'
import FooterToolbar from '@/components/FooterToolbar.vue'

const DEFAULT_SMS_PROVIDERS = [
  {
    kind: 'smsbower',
    display_name: 'SmsBower',
    short_label: '即退款',
    description: '遇到手机号验证时自动租号收码，未接通即时取消即退款',
    uses_cdk_pool: false,
    uses_country: true,
    uses_price_tiers: true,
    uses_provider_ids: true,
    uses_auto_country: true,
    needs_api_key: true,
    recommended_timeout: 80,
  },
  {
    kind: 'herosms',
    display_name: 'HeroSMS',
    short_label: '20分退',
    description: '与 SmsBower 同协议，号码约 20 分钟未用自动退款',
    uses_cdk_pool: false,
    uses_country: true,
    uses_price_tiers: true,
    uses_provider_ids: true,
    uses_auto_country: true,
    needs_api_key: true,
    recommended_timeout: 80,
  },
  {
    kind: 'cdk_sms',
    aliases: ['cdk', 'ndk', 'ndk_cdk', 'lubansms'],
    display_name: 'CDK 卡密兑换',
    short_label: 'ndk.cc.cd',
    description: '从 CDK 号池提取卡密兑换号码，被拒自动免费换号',
    uses_cdk_pool: true,
    uses_country: false,
    uses_price_tiers: false,
    uses_provider_ids: false,
    uses_auto_country: false,
    needs_api_key: false,
    recommended_timeout: 35,
  },
]

const smsProviders = ref(DEFAULT_SMS_PROVIDERS)
const enabled = ref(false)
const provider = ref('smsbower')
const apiKey = ref('')
const apiKeyPh = ref('粘贴接码平台 API Key')
const cdkUrl = ref('https://ndk.cc.cd')
const country = ref('150')
const service = ref('dr')
const maxPrice = ref('')
const providerIds = ref('')
const exceptProviderIds = ref([])
const phoneSuccessMax = ref('3')
const reusePhone = ref(false)
const autoCountry = ref(false)
const autoMinStock = ref('20')
const autoMaxPrice = ref('')
const allowed = ref([])
const maxPhoneAttempts = ref('')
const perPhoneTimeout = ref('80')

const allCountries = ref([])
const countriesLoading = ref(false)
const saving = ref(false)
const testing = ref(false)
const priceTiers = ref([])
const priceTiersLoading = ref(false)
let loadSeq = 0
let saveSeq = 0

const currentProvider = computed(
  () => smsProviders.value.find((p) => p.kind === provider.value) || smsProviders.value[0] || null,
)
const isCdkProvider = computed(() => !!currentProvider.value?.uses_cdk_pool)

// ── CDK 卡密号池状态与管理 ──
const cdkPoolItems = ref([])
const cdkPoolTotal = ref(0)
const cdkPoolLoading = ref(false)
const cdkPoolPage = ref(1)
const cdkPoolLimit = ref(20)
const cdkPoolStatus = ref('all')
const cdkPoolSearch = ref('')
const cdkPoolStats = ref({
  total: 0,
  available: 0,
  exhausted: 0,
  expired: 0,
  total_success_codes: 0,
})

// 批量导入弹窗
const showImportModal = ref(false)
const importing = ref(false)
const importForm = ref({
  cdks: '',
  max_use_mode: 'multi', // multi(多次卡/不限次) | single(单次卡) | custom(自定义N次)
  custom_max_use: 3,
  notes: '',
})

async function loadCdkPool() {
  cdkPoolLoading.value = true
  try {
    const res = await getSmsCdkPool({
      status: cdkPoolStatus.value,
      search: cdkPoolSearch.value.trim(),
      limit: cdkPoolLimit.value,
      offset: (cdkPoolPage.value - 1) * cdkPoolLimit.value,
    })
    cdkPoolItems.value = res.items || []
    cdkPoolTotal.value = res.total || 0
  } catch (e) {
    console.error('加载 CDK 号池失败:', e)
  } finally {
    cdkPoolLoading.value = false
  }
}

async function loadCdkStats() {
  try {
    const res = await getSmsCdkPoolStats()
    if (res.stats) {
      cdkPoolStats.value = res.stats
    }
  } catch (e) {
    console.error('加载 CDK 号池统计失败:', e)
  }
}

function openImportModal() {
  importForm.value = {
    cdks: '',
    max_use_mode: 'multi',
    custom_max_use: 3,
    notes: '',
  }
  showImportModal.value = true
}

async function handleImportSubmit() {
  const text = importForm.value.cdks.trim()
  if (!text) {
    ElMessage.warning('请输入至少一行 CDK 卡密')
    return
  }

  let maxUse = 0
  if (importForm.value.max_use_mode === 'single') {
    maxUse = 1
  } else if (importForm.value.max_use_mode === 'custom') {
    maxUse = Math.max(1, parseInt(importForm.value.custom_max_use || 3))
  } else {
    maxUse = 0 // 多次卡/长期不限次
  }

  importing.value = true
  try {
    const res = await importSmsCdks({
      cdks: text,
      max_use_count: maxUse,
      notes: importForm.value.notes.trim(),
    })
    const r = res.result || {}
    ElMessage.success(
      `成功导入 ${r.inserted || 0} 个新卡密，更新 ${r.updated || 0} 个现有卡密 (模式: ${
        maxUse === 0 ? '多次长期卡' : `${maxUse}次限制卡`
      })`
    )
    showImportModal.value = false
    cdkPoolPage.value = 1
    await Promise.all([loadCdkPool(), loadCdkStats()])
  } catch (e) {
    ElMessage.error(e.message || '导入失败')
  } finally {
    importing.value = false
  }
}

async function handleDeleteCdk(row) {
  try {
    await ElMessageBox.confirm(`确定要从号池中删除卡密【${row.cdk}】吗？`, '删除确认', {
      type: 'warning',
      confirmButtonText: '确定删除',
      cancelButtonText: '取消',
    })
    await deleteSmsCdk(row.id)
    ElMessage.success('已从号池删除卡密')
    await Promise.all([loadCdkPool(), loadCdkStats()])
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(e.message || '删除失败')
  }
}

async function handleSetMode(row, maxUse) {
  try {
    await updateSmsCdk(row.id, {
      max_use_count: maxUse,
      status: maxUse === 0 || row.use_count < maxUse ? 'available' : 'exhausted',
    })
    ElMessage.success(`卡密已变更为: ${maxUse === 0 ? '多次卡 (长期复用)' : '单次卡 (接码1次用尽)'}`)
    await Promise.all([loadCdkPool(), loadCdkStats()])
  } catch (e) {
    ElMessage.error(e.message || '修改失败')
  }
}

async function handleResetAvailable(row) {
  try {
    await updateSmsCdk(row.id, { status: 'available' })
    ElMessage.success(`卡密【${row.cdk}】已手动重置为可用就绪状态`)
    await Promise.all([loadCdkPool(), loadCdkStats()])
  } catch (e) {
    ElMessage.error(e.message || '重置失败')
  }
}

async function handleClearPool(status) {
  const statusLabel = status === 'all' ? '全部卡密' : status === 'exhausted' ? '已用尽卡密' : '已失效/到期卡密'
  try {
    await ElMessageBox.confirm(`确定要彻底清理号池中的【${statusLabel}】吗？此操作无法撤销。`, '清理确认', {
      type: 'warning',
      confirmButtonText: '立即清理',
      cancelButtonText: '取消',
    })
    const res = await clearSmsCdkPool({ status })
    ElMessage.success(`清理完成，已清除 ${res.cleared || 0} 个卡密`)
    cdkPoolPage.value = 1
    await Promise.all([loadCdkPool(), loadCdkStats()])
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(e.message || '清理失败')
  }
}

function handleCopy(text, label = '内容') {
  if (!text) return
  navigator.clipboard
    .writeText(text)
    .then(() => ElMessage.success(`${label}已复制到剪贴板`))
    .catch(() => ElMessage.error('复制失败，请手动选择复制'))
}

const countryOptions = computed(() =>
  allCountries.value.map((c) => {
    const bits = [`${c.id} · ${c.name_cn}`]
    if (c.count != null && c.count !== '') {
      const n = Number(c.count)
      bits.push(Number.isFinite(n) && n > 0 ? `余${c.count}` : '暂无库存')
    }
    if (c.price != null && c.price !== '') bits.push(`${c.price}`)
    return { value: c.id, label: bits.join(' · '), safe: c.openai_sms_safe }
  }),
)

async function loadPriceTiers() {
  if (!currentProvider.value?.uses_price_tiers || !country.value || country.value === 'AUTO') {
    priceTiers.value = []
    return
  }
  priceTiersLoading.value = true
  try {
    const res = await getSmsPriceTiers(country.value, service.value || 'dr', provider.value)
    priceTiers.value = res.tiers || []
    if (priceTiers.value.length) {
      const sum = priceTiers.value.reduce((s, t) => s + (Number(t.count) || 0), 0)
      const prices = priceTiers.value.map((t) => Number(t.price)).filter((n) => Number.isFinite(n) && n > 0)
      const idx = allCountries.value.findIndex((x) => String(x.id) === String(country.value))
      if (idx >= 0) {
        const cur = allCountries.value[idx]
        allCountries.value[idx] = {
          ...cur,
          count: Math.max(Number(cur.count) || 0, sum),
          price: prices.length ? Math.min(Number(cur.price) || prices[0], ...prices) : cur.price,
        }
      }
    }
  } catch (e) {
    priceTiers.value = []
  } finally {
    priceTiersLoading.value = false
  }
}

async function loadSmsProviderCatalog() {
  try {
    const res = await getSmsProviders()
    if (Array.isArray(res.providers) && res.providers.length) {
      smsProviders.value = res.providers
    }
  } catch (e) {
    console.error('加载接码渠道清单失败:', e)
  }
}

async function loadCountries(p) {
  const meta = smsProviders.value.find((x) => x.kind === (p || provider.value))
  if (meta && !meta.uses_country) {
    allCountries.value = []
    return
  }
  countriesLoading.value = true
  try {
    const r = await getSmsAllCountries(p || provider.value)
    allCountries.value = r.countries || []
  } catch (e) {
    console.error('加载国家列表失败:', e)
  } finally {
    countriesLoading.value = false
  }
}

async function load() {
  const seq = ++loadSeq
  try {
    await loadSmsProviderCatalog()
    if (seq !== loadSeq) return
    const { config } = await getSmsConfig()
    if (seq !== loadSeq) return
    applyConfig(config)
    if (isCdkProvider.value) {
      await Promise.all([loadCdkPool(), loadCdkStats()])
      if (seq !== loadSeq) return
    } else {
      await loadCountries(provider.value)
      if (seq !== loadSeq) return
    }
    await loadPriceTiers()
  } catch (e) {
    if (seq === loadSeq) ElMessage.error(e.message)
  }
}

function applyConfig(config) {
  provider.value = config.sms_provider || 'smsbower'
  cdkUrl.value = config.sms_cdk_url || 'https://ndk.cc.cd'
  enabled.value = config.sms_enabled === '1'
  apiKey.value = ''
  const meta = smsProviders.value.find((p) => p.kind === provider.value)
  if (meta?.uses_cdk_pool) {
    apiKeyPh.value =
      config.sms_api_key === '***'
        ? '已设定固定卡密（留空则全自动走号池）'
        : '全自动走号池调度（亦可填单个静态卡密）'
  } else {
    apiKeyPh.value = config.sms_api_key === '***' ? '已设置（留空不修改）' : '粘贴接码平台 API Key'
  }
  country.value = config.sms_country || '150'
  service.value = config.sms_service || 'dr'
  maxPrice.value = config.sms_max_price || ''
  providerIds.value = config.sms_provider_ids || config.sms_operator || ''
  exceptProviderIds.value = String(config.sms_except_provider_ids || '')
    .split(/[,;]/)
    .map((s) => s.trim())
    .filter(Boolean)
  phoneSuccessMax.value = config.sms_phone_success_max || '3'
  reusePhone.value = config.sms_reuse_phone === '1'
  autoCountry.value = config.sms_auto_country === '1'
  autoMinStock.value = config.sms_auto_min_stock || '20'
  autoMaxPrice.value = config.sms_auto_max_price || ''
  allowed.value = (config.sms_allowed_countries || '').split(',').map((s) => s.trim()).filter(Boolean)
  maxPhoneAttempts.value = config.sms_max_phone_attempts || ''
  perPhoneTimeout.value = config.sms_per_phone_timeout || '80'
}

async function onCountryChange() {
  await loadPriceTiers()
}

async function onProviderChange() {
  loadSeq += 1
  allowed.value = []
  const persist = save(false, { reload: false })
  const meta = currentProvider.value
  if (meta?.recommended_timeout && !perPhoneTimeout.value) {
    perPhoneTimeout.value = String(meta.recommended_timeout)
  }
  if (meta?.uses_cdk_pool) {
    apiKeyPh.value = '全自动走号池调度（亦可填单个静态卡密）'
    allCountries.value = []
    priceTiers.value = []
    await Promise.all([loadCdkPool(), loadCdkStats()])
  } else {
    if (!apiKeyPh.value.includes('已设置')) apiKeyPh.value = '粘贴接码平台 API Key'
    await loadCountries(provider.value)
    await loadPriceTiers()
  }
  const ok = await persist
  if (ok) {
    ElMessage.success(`接码平台已切换为 ${meta?.display_name || provider.value}，已写入配置`)
  }
}

async function onEnabledChange() {
  const ok = await save(false, { reload: false })
  if (ok) {
    ElMessage.success(enabled.value ? '已开启自动接码并保存' : '已关闭自动接码并保存')
  }
}

async function save(notify = true, { reload = true } = {}) {
  const seq = ++saveSeq
  saving.value = true
  try {
    const res = await saveSmsConfig({
      sms_enabled: enabled.value ? '1' : '0',
      sms_provider: provider.value,
      sms_api_key: apiKey.value.trim() || '***',
      sms_cdk_url: cdkUrl.value.trim() || 'https://ndk.cc.cd',
      sms_country: String(country.value || '').trim() || '52',
      sms_service: service.value.trim() || 'dr',
      sms_max_price: maxPrice.value.trim(),
      sms_provider_ids: providerIds.value.trim(),
      sms_except_provider_ids: exceptProviderIds.value.join(','),
      sms_phone_success_max: phoneSuccessMax.value.trim() || '3',
      sms_reuse_phone: reusePhone.value ? '1' : '0',
      sms_auto_country: autoCountry.value ? '1' : '0',
      sms_allowed_countries: allowed.value.join(','),
      sms_auto_min_stock: autoMinStock.value.trim() || '20',
      sms_auto_max_price: autoMaxPrice.value.trim(),
      sms_max_phone_attempts: maxPhoneAttempts.value.trim(),
      sms_per_phone_timeout: perPhoneTimeout.value.trim() || '80',
    })
    if (seq !== saveSeq) return false
    if (res?.config?.sms_api_key === '***') {
      apiKey.value = ''
      apiKeyPh.value = isCdkProvider.value
        ? '已设定固定卡密（留空则全自动走号池）'
        : '已设置（留空不修改）'
    }
    if (notify) ElMessage.success('SMS 配置保存成功')
    if (reload) await load()
    return true
  } catch (e) {
    if (seq === saveSeq) ElMessage.error(e.message || 'SMS 配置保存失败')
    return false
  } finally {
    if (seq === saveSeq) saving.value = false
  }
}

async function test() {
  testing.value = true
  try {
    const ok = await save(false, { reload: false })
    if (!ok) {
      ElMessage.error('当前接码配置未能保存，已取消测试')
      return
    }
    const r = await testSms()
    ElMessage.success(r.message || '接码平台连通正常')
    if (isCdkProvider.value) {
      await Promise.all([loadCdkPool(), loadCdkStats()])
    }
  } catch (e) {
    ElMessage.error(e.message || '测试连接失败')
  } finally {
    testing.value = false
  }
}

onActivated(() => {
  if (!saving.value && !testing.value) load()
})
load()
</script>

<template>
  <div class="smsconfig-page">
    <div class="macos-window-panel">
      <!-- 窗口标题栏 -->
      <div class="macos-panel-header">
        <div class="header-left">
          <div class="window-dot-group">
            <span class="dot red"></span>
            <span class="dot yellow"></span>
            <span class="dot green"></span>
          </div>
          <div class="title-with-badge">
            <span class="panel-title">SMS 手机短信接码服务配置</span>
            <span class="panel-sub-badge">AUTO PHONE VERIFY</span>
          </div>
        </div>
        <div class="header-right">
          <div class="status-indicator-pill" :class="{ 'is-active': enabled }">
            <span class="status-dot"></span>
            <span class="status-text">{{ enabled ? '自动接码已开启' : '接码未启用' }}</span>
          </div>
        </div>
      </div>

      <div class="config-scroll-body">
        <div class="macos-settings-card">
          <!-- 卡片 1: 核心启用开关与平台认证 -->
          <div class="card-section highlight-card">
            <div class="section-switch-row">
              <div class="switch-meta">
                <div class="switch-title-row">
                  <el-icon class="section-icon text-primary"><Phone /></el-icon>
                  <span class="switch-title">全局启用自动短信接码</span>
                </div>
                <span class="switch-desc">
                  当注册流程或 OAuth 导出命中 OpenAI 手机号风控（add-phone）时，全自动调用 API 租用虚拟号收码推进
                </span>
              </div>
              <el-switch v-model="enabled" size="large" inline-prompt active-text="开" inactive-text="关" @change="onEnabledChange" />
            </div>

            <div class="field-divider"></div>

            <el-row :gutter="14" class="field-grid">
              <el-col :xs="24" :sm="10">
                <div class="field-col">
                  <span class="field-label">接码平台服务商</span>
                  <el-radio-group v-model="provider" class="segmented-radio-group" @change="onProviderChange">
                    <el-radio-button
                      v-for="p in smsProviders"
                      :key="p.kind"
                      :value="p.kind"
                      :label="p.kind"
                    >
                      <span class="radio-label-bold">{{ p.display_name }}</span>
                      <span class="radio-label-sub">{{ p.short_label }}</span>
                    </el-radio-button>
                  </el-radio-group>
                </div>
              </el-col>
              <el-col :xs="24" :sm="14">
                <div class="field-col">
                  <div class="label-with-action">
                    <span class="field-label">
                      {{ isCdkProvider ? 'CDK 卡密兑换码 (支持单卡密或换行批量填入)' : '接码平台 API 密钥 (API Key)' }}
                    </span>
                    <el-button size="small" type="primary" link :loading="testing" @click="test">
                      <el-icon><Wallet /></el-icon> {{ isCdkProvider ? '测试卡密并兑换' : '测试连通与余额' }}
                    </el-button>
                  </div>
                  <el-input
                    v-model="apiKey"
                    :type="isCdkProvider ? 'text' : 'password'"
                    :show-password="!isCdkProvider"
                    :placeholder="apiKeyPh"
                    :prefix-icon="isCdkProvider ? Ticket : Lock"
                    clearable
                  />
                </div>
              </el-col>
            </el-row>

            <!-- CDK 专属平台地址与快捷填充栏 -->
            <el-row v-if="isCdkProvider" :gutter="14" class="field-grid" style="margin-top: 10px;">
              <el-col :xs="24" :sm="12">
                <div class="field-col">
                  <span class="field-label">CDK 平台接口基地址</span>
                  <el-input v-model="cdkUrl" placeholder="https://ndk.cc.cd" clearable />
                </div>
              </el-col>
              <el-col :xs="24" :sm="12">
                <div class="field-col">
                  <span class="field-label">快捷预设操作</span>
                  <div style="display: flex; gap: 8px; margin-top: 4px;">
                    <el-button size="small" type="success" plain @click="apiKey = 'SMS-59B1-A897'; save(false)">
                      填入当前卡密: SMS-59B1-A897
                    </el-button>
                  </div>
                </div>
              </el-col>
            </el-row>
          </div>

          <!-- 卡片 2 (CDK专属工作台 或 常规国家号池锁定) -->
          <div v-if="isCdkProvider" class="cdk-pool-container">
            <!-- 空池严重警告条 -->
            <el-alert
              v-if="cdkPoolStats.available === 0"
              type="error"
              show-icon
              :closable="false"
              class="empty-pool-alert"
            >
              <template #title>
                <div class="empty-pool-title">
                  <span>⚠️ 当前 CDK 号池已耗尽！可用卡密为 0 张</span>
                  <el-button size="small" type="danger" plain @click="openImportModal">
                    <el-icon><Plus /></el-icon> 立即批量导入新卡密
                  </el-button>
                </div>
              </template>
              <div class="empty-pool-desc">
                当号池无可用卡密时，若注册或 OAuth 导出命中手机号风控（add-phone），系统将无法进行短信验证并自动中断报错。请导入可用卡密！
              </div>
            </el-alert>

            <!-- 号池 5 大核心 KPI 磁贴 -->
            <div class="cdk-kpi-grid">
              <div class="kpi-card total-card">
                <div class="kpi-icon"><Ticket /></div>
                <div class="kpi-body">
                  <div class="kpi-num">{{ cdkPoolStats.total }}</div>
                  <div class="kpi-label">号池总收纳</div>
                </div>
              </div>
              <div class="kpi-card available-card" :class="{ 'is-zero': cdkPoolStats.available === 0 }">
                <div class="kpi-icon"><CircleCheckFilled /></div>
                <div class="kpi-body">
                  <div class="kpi-num">{{ cdkPoolStats.available }}</div>
                  <div class="kpi-label">当前就绪可用</div>
                </div>
              </div>
              <div class="kpi-card exhausted-card">
                <div class="kpi-icon"><WarningFilled /></div>
                <div class="kpi-body">
                  <div class="kpi-num">{{ cdkPoolStats.exhausted }}</div>
                  <div class="kpi-label">次数已用尽</div>
                </div>
              </div>
              <div class="kpi-card expired-card">
                <div class="kpi-icon"><Lock /></div>
                <div class="kpi-body">
                  <div class="kpi-num">{{ cdkPoolStats.expired }}</div>
                  <div class="kpi-label">平台到期/失效</div>
                </div>
              </div>
              <div class="kpi-card success-card">
                <div class="kpi-icon"><Check /></div>
                <div class="kpi-body">
                  <div class="kpi-num">{{ cdkPoolStats.total_success_codes }}</div>
                  <div class="kpi-label">累计成功接码</div>
                </div>
              </div>
            </div>

            <!-- 卡密列表与操作工作台 -->
            <div class="card-section cdk-table-card">
              <div class="section-header-row">
                <div class="section-title-wrap">
                  <el-icon class="section-icon text-accent"><Ticket /></el-icon>
                  <span class="section-heading">CDK 号池卡密明细与调度台</span>
                </div>
                <div class="table-actions-right">
                  <el-radio-group v-model="cdkPoolStatus" size="small" @change="cdkPoolPage = 1; loadCdkPool()">
                    <el-radio-button value="all">全部 ({{ cdkPoolStats.total }})</el-radio-button>
                    <el-radio-button value="available">可用 ({{ cdkPoolStats.available }})</el-radio-button>
                    <el-radio-button value="exhausted">已用尽 ({{ cdkPoolStats.exhausted }})</el-radio-button>
                    <el-radio-button value="expired">已失效 ({{ cdkPoolStats.expired }})</el-radio-button>
                  </el-radio-group>
                  <el-input
                    v-model="cdkPoolSearch"
                    size="small"
                    placeholder="搜索 CDK / 号码 / 地区"
                    clearable
                    style="width: 170px"
                    @change="cdkPoolPage = 1; loadCdkPool()"
                    @clear="cdkPoolPage = 1; loadCdkPool()"
                  />
                  <el-button size="small" :loading="cdkPoolLoading" @click="loadCdkPool(); loadCdkStats()">
                    <el-icon><Refresh /></el-icon>
                  </el-button>
                  <el-button size="small" type="primary" class="glow-btn" @click="openImportModal">
                    <el-icon><Plus /></el-icon> 批量导入卡密
                  </el-button>
                  <el-dropdown trigger="click">
                    <el-button size="small" plain>
                      清理 <el-icon class="el-icon--right"><Setting /></el-icon>
                    </el-button>
                    <template #dropdown>
                      <el-dropdown-menu>
                        <el-dropdown-item @click="handleClearPool('expired')">清理失效/到期卡密</el-dropdown-item>
                        <el-dropdown-item @click="handleClearPool('exhausted')">清理已用尽卡密</el-dropdown-item>
                        <el-dropdown-item divided style="color: var(--el-color-danger)" @click="handleClearPool('all')">
                          清空号池全部卡密
                        </el-dropdown-item>
                      </el-dropdown-menu>
                    </template>
                  </el-dropdown>
                </div>
              </div>

              <!-- 卡密数据表格 -->
              <el-table
                v-loading="cdkPoolLoading"
                :data="cdkPoolItems"
                stripe
                size="small"
                class="cdk-data-table"
                empty-text="暂无卡密，请点击右上角【批量导入卡密】"
              >
                <el-table-column prop="id" label="#" width="50" align="center" />
                <el-table-column label="CDK 兑换码" min-width="170">
                  <template #default="{ row }">
                    <div class="cdk-code-cell">
                      <span class="mono-cdk-text">{{ row.cdk }}</span>
                      <el-button link size="small" @click="handleCopy(row.cdk, 'CDK卡密')">
                        <el-icon><CopyDocument /></el-icon>
                      </el-button>
                    </div>
                  </template>
                </el-table-column>
                <el-table-column label="使用模式" width="160">
                  <template #default="{ row }">
                    <el-tag v-if="row.max_use_count === 0" size="small" type="success" effect="dark">
                      多次长期卡 (不限次)
                    </el-tag>
                    <el-tag v-else-if="row.max_use_count === 1" size="small" type="info" effect="plain">
                      单次卡 (接1次即满)
                    </el-tag>
                    <el-tag v-else size="small" type="warning" effect="plain">
                      限制卡 (上限{{ row.max_use_count }}次)
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="成功接码次数" width="130" align="center">
                  <template #default="{ row }">
                    <span class="use-count-badge">
                      <b>{{ row.use_count }}</b> 次
                      <span class="count-sub" v-if="row.max_use_count > 0">/ {{ row.max_use_count }}</span>
                      <span class="count-sub text-success" v-else>/ 长期</span>
                    </span>
                  </template>
                </el-table-column>
                <el-table-column label="状态" width="110" align="center">
                  <template #default="{ row }">
                    <el-tag v-if="row.status === 'available'" size="small" type="success">就绪可用</el-tag>
                    <el-tag v-else-if="row.status === 'exhausted'" size="small" type="warning">已用尽</el-tag>
                    <el-tag v-else-if="row.status === 'expired'" size="small" type="danger">已失效/到期</el-tag>
                    <el-tag v-else size="small">{{ row.status }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="分配手机号 / 地区" min-width="190">
                  <template #default="{ row }">
                    <div v-if="row.phone_number" class="phone-cell">
                      <span class="mono-phone">{{ row.phone_number }}</span>
                      <el-button link size="small" @click="handleCopy(row.phone_number, '手机号')">
                        <el-icon><CopyDocument /></el-icon>
                      </el-button>
                      <el-tag size="small" type="info" class="region-badge" v-if="row.region_label">
                        {{ row.region_label }}
                      </el-tag>
                    </div>
                    <span v-else class="text-muted-xs">未分配 (接码时自动兑换)</span>
                  </template>
                </el-table-column>
                <el-table-column label="平台到期时间" min-width="150">
                  <template #default="{ row }">
                    <span class="expiry-text" v-if="row.expiry_label">{{ row.expiry_label }}</span>
                    <span class="text-muted-xs" v-else>-</span>
                  </template>
                </el-table-column>
                <el-table-column label="备注说明" min-width="140" show-overflow-tooltip>
                  <template #default="{ row }">
                    <span class="notes-text">{{ row.notes || '-' }}</span>
                  </template>
                </el-table-column>
                <el-table-column label="操作" width="170" fixed="right" align="center">
                  <template #default="{ row }">
                    <div class="row-actions">
                      <el-button
                        v-if="row.status !== 'available'"
                        link
                        size="small"
                        type="success"
                        @click="handleResetAvailable(row)"
                      >
                        设为可用
                      </el-button>
                      <el-button
                        v-if="row.max_use_count !== 0"
                        link
                        size="small"
                        type="primary"
                        @click="handleSetMode(row, 0)"
                      >
                        转多次卡
                      </el-button>
                      <el-button
                        v-else
                        link
                        size="small"
                        type="warning"
                        @click="handleSetMode(row, 1)"
                      >
                        转单次卡
                      </el-button>
                      <el-button link size="small" type="danger" @click="handleDeleteCdk(row)">
                        删除
                      </el-button>
                    </div>
                  </template>
                </el-table-column>
              </el-table>

              <!-- 分页器 -->
              <div class="cdk-pagination-wrap" v-if="cdkPoolTotal > cdkPoolLimit">
                <el-pagination
                  v-model:current-page="cdkPoolPage"
                  :page-size="cdkPoolLimit"
                  :total="cdkPoolTotal"
                  layout="prev, pager, next, total"
                  size="small"
                  @current-change="loadCdkPool"
                />
              </div>
            </div>

            <!-- CDK 专属运行机理说明 -->
            <div class="card-section cdk-hero-section">
              <div class="section-header-row">
                <div class="section-title-wrap">
                  <el-icon class="section-icon text-accent"><InfoFilled /></el-icon>
                  <span class="section-heading">CDK 智能接码全景工作机制</span>
                </div>
                <el-tag size="small" type="success" effect="plain">ndk.cc.cd 驱动已就绪</el-tag>
              </div>
              <div class="cdk-desc-grid">
                <div class="cdk-desc-item">
                  <span class="cdk-item-title">🔄 严格支持多次复用 (用户核心要求)</span>
                  <span class="cdk-item-desc">
                    默认以<b>多次长期卡</b>模式入库。成功接码 1 次后仅递增使用计数，<b>绝不提前标记为已用尽</b>，持续保持就绪，直到平台返回 409(到期) 或 422(作废)。
                  </span>
                </div>
                <div class="cdk-desc-item">
                  <span class="cdk-item-title">⚡ 遇被拒自动免费换新号码</span>
                  <span class="cdk-item-desc">
                    当手机号被 OpenAI 判定已被注册或拦截时，系统自动调用 <code>/api/v2/public/change-number</code> 免人工干预更换新号 (支持免费换号 20 次)。
                  </span>
                </div>
                <div class="cdk-desc-item">
                  <span class="cdk-item-title">🇬🇧 号码国家自适应绑定</span>
                  <span class="cdk-item-desc">
                    卡密兑换时平台自动下发对应运营商号源（默认分配英国 44 线路 OpenAI 专属号码），无需在控制台反复调试国家代码与单价。
                  </span>
                </div>
              </div>
            </div>
          </div>

          <!-- 卡片 2: 首选国家与号池档位精确锁定 (仅常规平台展示) -->
          <div v-else-if="currentProvider?.uses_country" class="card-section">
            <div class="section-header-row">
              <div class="section-title-wrap">
                <el-icon class="section-icon text-accent"><Location /></el-icon>
                <span class="section-heading">默认国家与号池线路锁定</span>
              </div>
              <span class="section-tip-badge">支持按金额/供应商精准锁定</span>
            </div>

            <el-row :gutter="12">
              <el-col :xs="24" :sm="15">
                <el-form-item label="默认首选国家 (未开启多国自动轮换时强制生效)">
                  <el-select
                    v-model="country"
                    filterable
                    :loading="countriesLoading"
                    style="width: 100%"
                    @change="onCountryChange"
                    @visible-change="(open) => open && loadCountries(provider)"
                  >
                    <el-option v-for="o in countryOptions" :key="o.value" :label="o.label" :value="o.value">
                      <div class="country-option-item">
                        <span>{{ o.label }}</span>
                        <el-tag v-if="o.safe" size="small" type="success" effect="plain" class="safe-badge">
                          免WhatsApp
                        </el-tag>
                      </div>
                    </el-option>
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="9">
                <el-form-item label="Service 服务代码 (OpenAI 专属 = dr)">
                  <el-input v-model="service" placeholder="dr" @change="onCountryChange" />
                </el-form-item>
              </el-col>
            </el-row>

            <!-- 实时号池档位直选 Pills -->
            <div class="price-tier-block">
              <div class="tier-header-meta">
                <span class="tier-title"><el-icon><Discount /></el-icon> 当前国家实时号池档位 (点击直接锁定)</span>
                <span v-if="priceTiersLoading" class="tier-loading">正在拉取最新号池...</span>
              </div>
              <div v-if="priceTiers.length" class="tier-chips-wrap">
                <div
                  v-for="t in priceTiers"
                  :key="t.id || t.price_str"
                  class="tier-pill-card"
                  :class="{ 'is-selected': providerIds === t.id || maxPrice === t.price_str }"
                  @click="() => { maxPrice = t.price_str; if (t.id) providerIds = t.id; }"
                >
                  <span class="tier-pill-name">{{ t.label }}</span>
                  <el-icon v-if="providerIds === t.id || maxPrice === t.price_str" class="tier-check-icon">
                    <CircleCheckFilled />
                  </el-icon>
                </div>
              </div>
              <div v-else-if="!priceTiersLoading" class="tier-empty-tip">
                当前国家暂无在线档位或无需指定，可直接在下方填写目标金额
              </div>
            </div>

            <el-row :gutter="12" style="margin-top: 4px">
              <el-col :xs="24" :sm="12">
                <el-form-item label="接码金额要求 (点选档位即锁定，如 0.008 或 0.007-0.01)">
                  <el-input
                    v-model="maxPrice"
                    placeholder="输入 0.008 锁定单档 或 0.007-0.01"
                    :prefix-icon="Money"
                    clearable
                  />
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="12">
                <el-form-item label="指定供应商线路 ID (下拉直选带金额/库存)">
                  <el-select
                    v-model="providerIds"
                    filterable
                    allow-create
                    clearable
                    placeholder="选择或输入线路 ID，如 3237"
                    style="width: 100%"
                    @change="(val) => {
                      const found = priceTiers.find((x) => x.id === val)
                      if (found && found.price_str) maxPrice = found.price_str
                    }"
                  >
                    <el-option
                      v-for="t in priceTiers"
                      :key="t.id"
                      :label="t.label"
                      :value="t.id"
                    />
                  </el-select>
                </el-form-item>
              </el-col>
            </el-row>

            <el-row :gutter="12">
              <el-col :xs="24" :sm="12">
                <el-form-item label="排除供应商线路 ID (多选拉黑低质/受限通道)">
                  <el-select
                    v-model="exceptProviderIds"
                    multiple
                    filterable
                    allow-create
                    clearable
                    collapse-tags
                    collapse-tags-tooltip
                    placeholder="可多选排除，如 3327、1170"
                    style="width: 100%"
                  >
                    <el-option
                      v-for="t in priceTiers"
                      :key="t.id"
                      :label="t.label"
                      :value="t.id"
                    />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="12">
                <el-form-item label="单号码最大复用次数 (默认 3 次)">
                  <el-input v-model="phoneSuccessMax" type="number" placeholder="3" />
                </el-form-item>
              </el-col>
            </el-row>
          </div>

          <!-- 卡片 3: 智能多国自动轮换策略 -->
          <div v-if="currentProvider?.uses_auto_country" class="card-section">
            <div class="section-switch-row">
              <div class="switch-meta">
                <div class="switch-title-row">
                  <el-icon class="section-icon text-success"><Operation /></el-icon>
                  <span class="switch-title">智能多国自动轮换策略</span>
                </div>
                <span class="switch-desc">根据实时价格与库存深度，在指定国家池中自动调度最优低价高爆国家</span>
              </div>
              <el-switch v-model="autoCountry" />
            </div>

            <el-collapse-transition>
              <div v-show="autoCountry" class="auto-country-body">
                <el-row :gutter="12">
                  <el-col :xs="24" :sm="12">
                    <el-form-item label="最低库存门槛 (低于此数量自动切国)">
                      <el-input v-model="autoMinStock" type="number" placeholder="20" />
                    </el-form-item>
                  </el-col>
                  <el-col :xs="24" :sm="12">
                    <el-form-item label="最高限价 (0 表示不限价)">
                      <el-input v-model="autoMaxPrice" placeholder="0" />
                    </el-form-item>
                  </el-col>
                </el-row>

                <el-form-item label="允许轮换的目标国家池 (多选，留空表示允许全库国家)">
                  <el-select
                    v-model="allowed"
                    multiple
                    filterable
                    clearable
                    collapse-tags
                    collapse-tags-tooltip
                    :loading="countriesLoading"
                    placeholder="搜索并选择允许自动轮换的国家..."
                    style="width: 100%"
                  >
                    <el-option v-for="o in countryOptions" :key="o.value" :label="o.label" :value="o.value">
                      <div class="country-option-item">
                        <span>{{ o.label }}</span>
                        <el-tag v-if="o.safe" size="small" type="success" effect="plain" class="safe-badge">
                          免WhatsApp
                        </el-tag>
                      </div>
                    </el-option>
                  </el-select>
                </el-form-item>
              </div>
            </el-collapse-transition>
          </div>

          <!-- 卡片 4: 重试与超时控制 -->
          <div class="card-section">
            <div class="section-header-row">
              <div class="section-title-wrap">
                <el-icon class="section-icon text-muted"><Setting /></el-icon>
                <span class="section-heading">换号重试与超时控制</span>
              </div>
            </div>
            <el-row :gutter="12">
              <el-col :xs="24" :sm="12">
                <el-form-item label="单账号最大换号重试次数 (默认 3 次)">
                  <el-input v-model="maxPhoneAttempts" type="number" placeholder="默认 3 次" />
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="12">
                <el-form-item label="单号码最长等待短信时间 (秒)">
                  <el-input v-model="perPhoneTimeout" type="number" placeholder="默认 80 秒" />
                </el-form-item>
              </el-col>
            </el-row>
          </div>

          <!-- 卡片 5: 配置教程与规则速查表 -->
          <div v-if="currentProvider?.uses_price_tiers" class="card-section guide-section">
            <div class="section-header-row">
              <div class="section-title-wrap">
                <el-icon class="section-icon text-info"><InfoFilled /></el-icon>
                <span class="section-heading">接码金额 / 线路配置教程 (对齐 SmsBower 点选)</span>
              </div>
              <span class="guide-target-badge">点哪个价 · 只拿哪个号</span>
            </div>

            <div class="guide-rules-grid">
              <div class="rule-mini-card">
                <div class="rule-badge">1. 选定即锁定</div>
                <div class="rule-desc">选 <code>0.008</code> 绝不会拿 <code>0.007</code>。无货报 <code>NO_NUMBERS</code>，不会偷偷换便宜号。</div>
              </div>
              <div class="rule-mini-card">
                <div class="rule-badge">2. 区间灵活性</div>
                <div class="rule-desc">填 <code>0.007-0.008</code> 允许两档；填 <code>&lt;=0.008</code> 才会优先派更便宜号。</div>
              </div>
              <div class="rule-mini-card">
                <div class="rule-badge">3. 坏线自动剔除</div>
                <div class="rule-desc">指定线路若被平台 BANNED，会自动去掉线路参数，但仍排除更便宜档。</div>
              </div>
            </div>

            <div class="guide-table-wrap">
              <table class="guide-table">
                <thead>
                  <tr>
                    <th>您期望的目标效果</th>
                    <th>推荐填法</th>
                    <th>是否会拿到更便宜的号</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>只要指定 0.008 档（绝不要0.007）</td>
                    <td>点选 <code>0.008 $</code> 或填 <code>0.008</code></td>
                    <td><span class="text-success-bold">🚫 绝不会</span></td>
                  </tr>
                  <tr>
                    <td>只要指定供应商线路 3237</td>
                    <td>下拉直选 <code>3237 · 0.008 $</code></td>
                    <td><span class="text-success-bold">🚫 绝不会 (金额连带锁定)</span></td>
                  </tr>
                  <tr>
                    <td>0.007 与 0.008 均可接受</td>
                    <td>填写区间 <code>0.007-0.008</code></td>
                    <td><span class="text-warning-bold">✔️ 会 (主动放宽区间)</span></td>
                  </tr>
                  <tr>
                    <td>不超过 0.008 越便宜越好</td>
                    <td>填写 <code>&lt;=0.008</code></td>
                    <td><span class="text-info-bold">✔️ 会 (平台优先派更便宜号)</span></td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>

      <FooterToolbar>
        <template #left>
          <div class="footer-summary">
            <span class="summary-dot"></span>
            <span class="footer-info-text">
              当前接码商：<b>{{ currentProvider?.display_name || provider }}</b>
              <span v-if="isCdkProvider" class="summary-extra">
                · 号池可用: {{ cdkPoolStats.available }}/{{ cdkPoolStats.total }} 张 · 累计接码: {{ cdkPoolStats.total_success_codes }} 次
              </span>
              <span v-else-if="allowed.length" class="summary-extra"> · 允许轮换 {{ allowed.length }} 国</span>
              <span v-else-if="maxPrice" class="summary-extra"> · 锁定金额 ${{ maxPrice }}</span>
            </span>
          </div>
        </template>
        <template #right>
          <el-button v-if="isCdkProvider" type="success" plain class="ghost-toolbar-btn" @click="openImportModal">
            <el-icon><Plus /></el-icon>批量导入卡密
          </el-button>
          <el-button :loading="testing" class="ghost-toolbar-btn" @click="test">
            <el-icon><Wallet /></el-icon>{{ isCdkProvider ? '测试卡密兑换' : '测试连通与余额' }}
          </el-button>
          <el-button type="primary" :loading="saving" class="primary-toolbar-btn" @click="save">
            <el-icon><Check /></el-icon>保存 SMS 全局配置
          </el-button>
        </template>
      </FooterToolbar>

      <!-- 批量导入 CDK 卡密对话框 -->
      <el-dialog
        v-model="showImportModal"
        title="🎟️ 批量导入 CDK 卡密到号池"
        width="620px"
        align-center
        destroy-on-close
        class="cdk-import-dialog"
      >
        <div class="import-modal-body">
          <div class="import-tip-banner">
            <el-icon><InfoFilled /></el-icon>
            <span>
              支持同时粘贴多个 CDK 卡密（每行一个，或逗号/分号分隔）。系统会自动进行清洗与去重。
            </span>
          </div>

          <el-form label-position="top">
            <el-form-item label="卡密列表 (一行一个)">
              <el-input
                v-model="importForm.cdks"
                type="textarea"
                :rows="6"
                placeholder="例如：
SMS-336A-20BC
SMS-E7CA-0727
SMS-59B1-A897"
              />
            </el-form-item>

            <el-form-item label="接码使用模式 (核心约束设置)">
              <el-radio-group v-model="importForm.max_use_mode" class="import-mode-radios">
                <el-radio value="multi">
                  <div class="radio-content">
                    <span class="radio-main">🔄 多次卡 / 长期复用 (强烈推荐)</span>
                    <span class="radio-hint">成功接码后<b>绝不提前置为已用尽</b>，可持续反复用于多个账号接码，直到平台明确报到期。</span>
                  </div>
                </el-radio>
                <el-radio value="single">
                  <div class="radio-content">
                    <span class="radio-main">1️⃣ 单次卡</span>
                    <span class="radio-hint">成功接码 1 次后立即标记为已用尽 (exhausted)，不再分配。</span>
                  </div>
                </el-radio>
                <el-radio value="custom">
                  <div class="radio-content">
                    <span class="radio-main">🔢 限制使用次数</span>
                    <div v-if="importForm.max_use_mode === 'custom'" class="custom-use-input" @click.stop>
                      最多成功接码
                      <el-input-number
                        v-model="importForm.custom_max_use"
                        :min="1"
                        :max="999"
                        size="small"
                        controls-position="right"
                        style="width: 80px; margin: 0 6px;"
                      />
                      次后标记用尽
                    </div>
                  </div>
                </el-radio>
              </el-radio-group>
            </el-form-item>

            <el-form-item label="卡密备注 (可选)">
              <el-input v-model="importForm.notes" placeholder="例如：9月采购长期英国卡、自用测试等" clearable />
            </el-form-item>
          </el-form>
        </div>

        <template #footer>
          <div class="dialog-footer">
            <el-button @click="showImportModal = false">取消</el-button>
            <el-button type="primary" :loading="importing" @click="handleImportSubmit">
              <el-icon><Check /></el-icon> 确认导入号池
            </el-button>
          </div>
        </template>
      </el-dialog>
    </div>
  </div>
</template>

<style scoped>
.smsconfig-page {
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
  padding: 12px 20px;
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
  gap: 14px;
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

.title-with-badge {
  display: flex;
  align-items: center;
  gap: 8px;
}
.panel-title {
  font-size: 13.5px;
  font-weight: 700;
  color: var(--app-title);
}
.panel-sub-badge {
  font-size: 10px;
  font-weight: 700;
  color: var(--el-color-primary);
  background: rgba(0, 122, 255, 0.09);
  padding: 1px 6px;
  border-radius: 4px;
  letter-spacing: 0.4px;
}

.status-indicator-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px;
  border-radius: 14px;
  font-size: 11.5px;
  font-weight: 500;
  background: var(--el-fill-color);
  color: var(--el-text-color-secondary);
  border: 1px solid var(--app-border);
}
.status-indicator-pill.is-active {
  background: rgba(16, 185, 129, 0.09);
  color: #10b981;
  border-color: rgba(16, 185, 129, 0.28);
}
.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

.config-scroll-body {
  flex: 1;
  min-height: 0;
  padding: 20px 24px 80px;
  overflow-y: auto;
}

.macos-settings-card {
  max-width: 820px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.card-section {
  background: var(--el-bg-color-overlay, #ffffff);
  border: 1px solid var(--app-border);
  border-radius: 10px;
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.02);
}

.highlight-card {
  border-color: rgba(0, 122, 255, 0.22);
  background: linear-gradient(180deg, rgba(0, 122, 255, 0.025) 0%, var(--el-bg-color-overlay, #ffffff) 100%);
}

.section-switch-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.switch-meta {
  display: flex;
  flex-direction: column;
}
.switch-title-row {
  display: flex;
  align-items: center;
  gap: 7px;
}
.section-icon {
  font-size: 15px;
}
.text-primary { color: #007aff; }
.text-accent { color: #8b5cf6; }
.text-success { color: #10b981; }
.text-info { color: #0ea5e9; }
.text-muted { color: #64748b; }

.switch-title {
  font-size: 13.5px;
  font-weight: 700;
  color: var(--app-title);
}
.switch-desc {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 3px;
  line-height: 1.5;
}

.field-divider {
  height: 1px;
  background: var(--app-border);
  margin: 2px 0;
}

.field-col {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.label-with-action {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.field-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--app-title);
}

.segmented-radio-group {
  display: flex;
  width: 100%;
}
.segmented-radio-group :deep(.el-radio-button) {
  flex: 1;
}
.segmented-radio-group :deep(.el-radio-button__inner) {
  width: 100%;
  display: flex;
  flex-direction: column;
  padding: 6px 10px;
  gap: 2px;
  align-items: center;
}
.radio-label-bold {
  font-size: 12px;
  font-weight: 600;
}
.radio-label-sub {
  font-size: 10px;
  opacity: 0.75;
}

.section-header-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.section-title-wrap {
  display: flex;
  align-items: center;
  gap: 7px;
}
.section-heading {
  font-size: 13px;
  font-weight: 700;
  color: var(--app-title);
}
.section-tip-badge {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.country-option-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
}
.safe-badge {
  margin-left: 8px;
  font-size: 10.5px;
}

/* 实时档位选择胶囊 */
.price-tier-block {
  background: var(--el-fill-color-light);
  border: 1px solid var(--app-border);
  border-radius: 8px;
  padding: 10px 12px;
}
.tier-header-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.tier-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--app-title);
  display: flex;
  align-items: center;
  gap: 5px;
}
.tier-loading {
  font-size: 11px;
  color: #007aff;
}
.tier-chips-wrap {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.tier-pill-card {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  border-radius: 6px;
  background: var(--el-bg-color-overlay, #fff);
  border: 1px solid var(--app-border);
  font-size: 11.5px;
  font-weight: 500;
  color: var(--el-text-color-primary);
  cursor: pointer;
  user-select: none;
  transition: all 0.16s ease;
}
.tier-pill-card:hover {
  border-color: #007aff;
  transform: translateY(-1px);
}
.tier-pill-card.is-selected {
  background: #007aff;
  color: #ffffff;
  border-color: #007aff;
  font-weight: 600;
}
.tier-check-icon {
  font-size: 13px;
}
.tier-empty-tip {
  font-size: 11.5px;
  color: var(--el-text-color-secondary);
  padding: 4px 0;
}

.auto-country-body {
  margin-top: 10px;
  padding-top: 12px;
  border-top: 1px dashed var(--app-border);
}

/* 教程卡片与表格 */
.guide-section {
  background: rgba(14, 165, 233, 0.04);
  border-color: rgba(14, 165, 233, 0.2);
}
.guide-target-badge {
  font-size: 11px;
  font-weight: 600;
  color: #0284c7;
  background: rgba(14, 165, 233, 0.1);
  padding: 2px 8px;
  border-radius: 4px;
}
.guide-rules-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 8px;
  margin-top: 2px;
}
.rule-mini-card {
  background: var(--el-bg-color-overlay, #fff);
  border: 1px solid rgba(14, 165, 233, 0.18);
  border-radius: 6px;
  padding: 8px 10px;
}
.rule-badge {
  font-size: 11.5px;
  font-weight: 700;
  color: var(--app-title);
  margin-bottom: 2px;
}
.rule-desc {
  font-size: 11px;
  line-height: 1.45;
  color: var(--el-text-color-secondary);
}

.guide-table-wrap {
  overflow-x: auto;
  border-radius: 6px;
  border: 1px solid rgba(14, 165, 233, 0.2);
  margin-top: 6px;
}
.guide-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 11.5px;
  line-height: 1.5;
  background: var(--el-bg-color-overlay, #fff);
}
.guide-table th,
.guide-table td {
  padding: 7px 10px;
  border-bottom: 1px solid rgba(14, 165, 233, 0.12);
  text-align: left;
}
.guide-table th {
  background: rgba(14, 165, 233, 0.08);
  font-weight: 700;
  color: var(--app-title);
  white-space: nowrap;
}
.guide-table tr:last-child td {
  border-bottom: none;
}
.text-success-bold { color: #10b981; font-weight: 600; }
.text-warning-bold { color: #f59e0b; font-weight: 600; }
.text-info-bold { color: #0284c7; font-weight: 600; }

code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11px;
  padding: 1px 4px;
  border-radius: 4px;
  background: rgba(15, 23, 42, 0.08);
  color: #0f172a;
}
html.dark code {
  background: rgba(255, 255, 255, 0.12);
  color: #f1f5f9;
}

.cdk-hero-section {
  border-color: rgba(16, 185, 129, 0.3);
  background: rgba(16, 185, 129, 0.03);
}
.cdk-desc-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
  margin-top: 4px;
}
.cdk-desc-item {
  background: var(--el-bg-color-overlay, #fff);
  border: 1px solid rgba(16, 185, 129, 0.2);
  border-radius: 8px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.cdk-item-title {
  font-size: 12px;
  font-weight: 600;
  color: #10b981;
}
.cdk-item-desc {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  line-height: 1.4;
}

.footer-summary {
  display: flex;
  align-items: center;
  gap: 8px;
}
.summary-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #10b981;
}
.footer-info-text {
  font-size: 12px;
  color: var(--app-text-secondary);
}
.summary-extra {
  opacity: 0.85;
}
.ghost-toolbar-btn {
  border-radius: 6px;
}
.primary-toolbar-btn {
  background: #007aff;
  border-color: #007aff;
  border-radius: 6px;
  font-weight: 600;
}

/* ──────────────── CDK 号池工作台专属样式 ──────────────── */
.cdk-pool-container {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.empty-pool-alert {
  border-radius: 8px;
  border: 1px solid var(--el-color-error-light-5);
}

.empty-pool-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  font-size: 14px;
  font-weight: 600;
}

.empty-pool-desc {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.5;
  opacity: 0.9;
}

/* KPI 磁贴网格 */
.cdk-kpi-grid {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 10px;
}

@media (max-width: 900px) {
  .cdk-kpi-grid {
    grid-template-columns: repeat(3, 1fr);
  }
}
@media (max-width: 600px) {
  .cdk-kpi-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}

.kpi-card {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border-radius: 8px;
  background: var(--el-bg-color-overlay);
  border: 1px solid var(--el-border-color-lighter);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.03);
  transition: all 0.2s ease;
}

.kpi-card:hover {
  transform: translateY(-1px);
  border-color: var(--el-color-primary-light-5);
}

.kpi-icon {
  font-size: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 6px;
  background: var(--el-fill-color-light);
}

.total-card .kpi-icon { color: var(--el-color-primary); }
.available-card .kpi-icon { color: var(--el-color-success); }
.available-card.is-zero .kpi-icon { color: var(--el-color-error); background: var(--el-color-error-light-9); }
.available-card.is-zero .kpi-num { color: var(--el-color-error); }
.exhausted-card .kpi-icon { color: var(--el-color-warning); }
.expired-card .kpi-icon { color: var(--el-color-info); }
.success-card .kpi-icon { color: var(--el-color-success); }

.kpi-body {
  display: flex;
  flex-direction: column;
}

.kpi-num {
  font-size: 17px;
  font-weight: 700;
  line-height: 1.2;
  font-family: var(--el-font-family);
}

.kpi-label {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  margin-top: 2px;
}

/* 工作台表格卡片 */
.cdk-table-card {
  padding: 14px 18px;
}

.table-actions-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.cdk-data-table {
  width: 100%;
  margin-top: 10px;
  border-radius: 6px;
  overflow: hidden;
}

.cdk-code-cell {
  display: flex;
  align-items: center;
  gap: 6px;
}

.mono-cdk-text {
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 12px;
  font-weight: 600;
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  padding: 1px 6px;
  border-radius: 4px;
}

.phone-cell {
  display: flex;
  align-items: center;
  gap: 6px;
}

.mono-phone {
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 12px;
  font-weight: 600;
}

.use-count-badge {
  font-size: 13px;
}

.use-count-badge b {
  color: var(--el-color-primary);
}

.count-sub {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  margin-left: 2px;
}

.expiry-text {
  font-size: 11px;
  color: var(--el-text-color-regular);
}

.notes-text {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.text-muted-xs {
  font-size: 11px;
  color: var(--el-text-color-placeholder);
}

.row-actions {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
}

.cdk-pagination-wrap {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}

/* 导入弹窗样式 */
.import-tip-banner {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px 14px;
  border-radius: 6px;
  background: var(--el-color-info-light-9);
  color: var(--el-color-info);
  font-size: 12px;
  line-height: 1.5;
  margin-bottom: 14px;
}

.import-mode-radios {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 10px;
  width: 100%;
}

.import-mode-radios .el-radio {
  height: auto;
  align-items: flex-start;
  padding: 8px 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  width: 100%;
  margin-right: 0;
  transition: all 0.2s;
}

.import-mode-radios .el-radio.is-checked {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}

.radio-content {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.radio-main {
  font-weight: 600;
  font-size: 13px;
  color: var(--el-text-color-primary);
}

.radio-hint {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  line-height: 1.4;
}

.custom-use-input {
  display: flex;
  align-items: center;
  font-size: 12px;
  margin-top: 4px;
}
</style>
