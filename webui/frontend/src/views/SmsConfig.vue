<script setup>
import { computed, onActivated, ref } from 'vue'
import { ElMessage } from 'element-plus'
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
} from '@element-plus/icons-vue'
import { getSmsConfig, saveSmsConfig, testSms, getSmsAllCountries, getSmsPriceTiers } from '@/api/settings'
import FooterToolbar from '@/components/FooterToolbar.vue'

const enabled = ref(false)
const provider = ref('smsbower')
const apiKey = ref('')
const apiKeyPh = ref('粘贴接码平台 API Key')
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

const countryOptions = computed(() =>
  allCountries.value.map((c) => {
    const bits = [`${c.id} · ${c.name_cn}`]
    if (c.count != null && c.count !== '') bits.push(`余${c.count}`)
    else bits.push('暂无库存')
    if (c.price != null && c.price !== '') bits.push(`${c.price}`)
    return { value: c.id, label: bits.join(' · '), safe: c.openai_sms_safe }
  }),
)

async function loadPriceTiers() {
  if (provider.value !== 'smsbower' || !country.value || country.value === 'AUTO') {
    priceTiers.value = []
    return
  }
  priceTiersLoading.value = true
  try {
    const res = await getSmsPriceTiers(country.value, service.value || 'dr', provider.value)
    priceTiers.value = res.tiers || []
  } catch (e) {
    priceTiers.value = []
  } finally {
    priceTiersLoading.value = false
  }
}

async function loadCountries(p) {
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
  try {
    const { config } = await getSmsConfig()
    provider.value = config.sms_provider || 'smsbower'
    await loadCountries(provider.value)
    enabled.value = config.sms_enabled === '1'
    apiKey.value = ''
    apiKeyPh.value = config.sms_api_key === '***' ? '已设置（留空不修改）' : '粘贴接码平台 API Key'
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
    await loadPriceTiers()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

async function onCountryChange() {
  await loadPriceTiers()
}

async function onProviderChange() {
  allowed.value = []
  await loadCountries(provider.value)
  await loadPriceTiers()
}

async function save() {
  saving.value = true
  try {
    await saveSmsConfig({
      sms_enabled: enabled.value ? '1' : '0',
      sms_provider: provider.value,
      sms_api_key: apiKey.value.trim() || '***',
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
    ElMessage.success('SMS 配置保存成功')
    setTimeout(load, 300)
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    saving.value = false
  }
}

async function test() {
  testing.value = true
  try {
    const r = await testSms()
    ElMessage.success(r.message || '接码平台连通正常，余额充足')
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    testing.value = false
  }
}

onActivated(() => load())
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
              <el-switch v-model="enabled" size="large" inline-prompt active-text="开" inactive-text="关" />
            </div>

            <div class="field-divider"></div>

            <el-row :gutter="14" class="field-grid">
              <el-col :xs="24" :sm="10">
                <div class="field-col">
                  <span class="field-label">接码平台服务商</span>
                  <el-radio-group v-model="provider" class="segmented-radio-group" @change="onProviderChange">
                    <el-radio-button value="smsbower">
                      <span class="radio-label-bold">SmsBower</span>
                      <span class="radio-label-sub">即退款</span>
                    </el-radio-button>
                    <el-radio-button value="herosms">
                      <span class="radio-label-bold">HeroSMS</span>
                      <span class="radio-label-sub">20分退</span>
                    </el-radio-button>
                  </el-radio-group>
                </div>
              </el-col>
              <el-col :xs="24" :sm="14">
                <div class="field-col">
                  <div class="label-with-action">
                    <span class="field-label">接码平台 API 密钥 (API Key)</span>
                    <el-button size="small" type="primary" link :loading="testing" @click="test">
                      <el-icon><Wallet /></el-icon> 测试连通与余额
                    </el-button>
                  </div>
                  <el-input
                    v-model="apiKey"
                    type="password"
                    show-password
                    :placeholder="apiKeyPh"
                    :prefix-icon="Lock"
                    clearable
                  />
                </div>
              </el-col>
            </el-row>
          </div>

          <!-- 卡片 2: 首选国家与号池档位精确锁定 -->
          <div class="card-section">
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
          <div class="card-section">
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
          <div class="card-section guide-section">
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
              当前接码商：<b>{{ provider === 'herosms' ? 'HeroSMS' : 'SmsBower' }}</b>
              <span v-if="allowed.length" class="summary-extra"> · 允许轮换 {{ allowed.length }} 国</span>
              <span v-if="maxPrice" class="summary-extra"> · 锁定金额 ${{ maxPrice }}</span>
            </span>
          </div>
        </template>
        <template #right>
          <el-button :loading="testing" class="ghost-toolbar-btn" @click="test">
            <el-icon><Wallet /></el-icon>测试连通与余额
          </el-button>
          <el-button type="primary" :loading="saving" class="primary-toolbar-btn" @click="save">
            <el-icon><Check /></el-icon>保存 SMS 全局配置
          </el-button>
        </template>
      </FooterToolbar>
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
</style>
