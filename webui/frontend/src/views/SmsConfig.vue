<script setup>
import { computed, onActivated, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  Setting,
  Check,
  Connection,
  Phone,
  Wallet,
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
const exceptProviderIds = ref('')
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
  allCountries.value.map((c) => ({
    value: c.id,
    label: `${c.id} · ${c.name_cn}${c.price != null ? ` (${c.price}₽ / 余${c.count})` : ''}`,
    safe: c.openai_sms_safe,
  })),
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
    exceptProviderIds.value = config.sms_except_provider_ids || ''
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
      sms_except_provider_ids: exceptProviderIds.value.trim(),
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
          <span class="panel-title">SMS 手机短信接码配置 · Phone Verify Preferences</span>
        </div>
        <div class="header-right">
          <el-tag :type="enabled ? 'success' : 'info'" size="small" effect="plain" class="header-tag">
            {{ enabled ? '● 自动接码已启用' : '○ 接码未启用' }}
          </el-tag>
        </div>
      </div>

      <div class="config-scroll-body">
        <div class="macos-settings-card">
          <!-- 开关与平台选择 -->
          <div class="card-section">
            <div class="section-switch-row">
              <div class="switch-meta">
                <span class="switch-title">启用自动短信接码</span>
                <span class="switch-desc">命中 OpenAI 手机号风控 (add-phone) 时自动租用虚拟号收码推进</span>
              </div>
              <el-switch v-model="enabled" />
            </div>

            <div class="field-divider"></div>

            <div class="field-col">
              <span class="section-heading">选择接码平台服务商</span>
              <el-radio-group v-model="provider" class="macos-radio-group" @change="onProviderChange">
                <el-radio-button value="smsbower">SmsBower (立即取消即退款)</el-radio-button>
                <el-radio-button value="herosms">HeroSMS (20分钟自动退款)</el-radio-button>
              </el-radio-group>
            </div>

            <div class="field-col" style="margin-top: 10px">
              <span class="field-label">接码平台 API 密钥 (API Key)</span>
              <el-input v-model="apiKey" type="password" show-password :placeholder="apiKeyPh" />
            </div>
          </div>

          <!-- 号码策略卡片 -->
          <div class="card-section">
            <span class="section-heading">默认国家与 Service 代码</span>
            <el-row :gutter="12">
              <el-col :span="14">
                <el-form-item label="默认首选国家（未开自动轮换时强制生效）">
                  <el-select v-model="country" filterable :loading="countriesLoading" style="width: 100%" @change="onCountryChange">
                    <el-option v-for="o in countryOptions" :key="o.value" :label="o.label" :value="o.value" />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :span="10">
                <el-form-item label="Service 服务代码（OpenAI 专属代码 = dr）">
                  <el-input v-model="service" placeholder="dr" @change="onCountryChange" />
                </el-form-item>
              </el-col>
            </el-row>

            <el-row :gutter="12">
              <el-col :span="12">
                <el-form-item label="接码金额要求（点选档位即锁定该价格，不含更便宜的号）">
                  <el-input v-model="maxPrice" placeholder="输入 0.008 锁定该档 或 0.007-0.01 区间" clearable />
                  <div v-if="priceTiers.length" style="margin-top: 6px; display: flex; flex-wrap: wrap; gap: 6px; align-items: center">
                    <span style="font-size: 11.5px; color: var(--el-text-color-secondary)">实时号池档位(点击直选):</span>
                    <el-tag
                      v-for="t in priceTiers"
                      :key="t.id || t.price_str"
                      size="small"
                      :type="providerIds === t.id || maxPrice === t.price_str ? 'primary' : 'info'"
                      :effect="providerIds === t.id ? 'dark' : 'plain'"
                      style="cursor: pointer; user-select: none"
                      @click="() => { maxPrice = t.price_str; if (t.id) providerIds = t.id; }"
                    >
                      {{ t.label }}
                    </el-tag>
                  </div>
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="指定供应商线路 ID（下拉直选带金额/库存）">
                  <el-select
                    v-model="providerIds"
                    filterable
                    allow-create
                    clearable
                    placeholder="下拉选择或输入线路 ID，如 3237"
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
              <el-col :span="12">
                <el-form-item label="排除供应商线路 ID（避开低质通道）">
                  <el-select
                    v-model="exceptProviderIds"
                    filterable
                    allow-create
                    clearable
                    placeholder="下拉选择或输入排除 ID，如 3327"
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
              <el-col :span="12">
                <el-form-item label="单号码最大复用次数（默认 3）">
                  <el-input v-model="phoneSuccessMax" type="number" />
                </el-form-item>
              </el-col>
            </el-row>
          </div>

          <!-- 自动多国轮换策略 -->
          <div class="card-section">
            <div class="section-switch-row">
              <div class="switch-meta">
                <span class="switch-title">智能多国自动轮换策略</span>
                <span class="switch-desc">根据实时价格与库存深度，在允许的国家列表中自动选择最优低价高爆国家</span>
              </div>
              <el-switch v-model="autoCountry" />
            </div>

            <div v-show="autoCountry" style="margin-top: 12px">
              <el-row :gutter="12">
                <el-col :span="12">
                  <el-form-item label="最低库存门槛（低于此数量自动换国）">
                    <el-input v-model="autoMinStock" type="number" placeholder="20" />
                  </el-form-item>
                </el-col>
                <el-col :span="12">
                  <el-form-item label="最高限价（0 表示不限）">
                    <el-input v-model="autoMaxPrice" placeholder="0" />
                  </el-form-item>
                </el-col>
              </el-row>

              <el-form-item label="允许轮换的目标国家（多选，留空表示全库国家均可轮换）">
                <el-select
                  v-model="allowed"
                  multiple
                  filterable
                  clearable
                  collapse-tags
                  collapse-tags-tooltip
                  :loading="countriesLoading"
                  placeholder="搜索并勾选允许使用的国家..."
                  style="width: 100%"
                >
                  <el-option v-for="o in countryOptions" :key="o.value" :label="o.label" :value="o.value">
                    <span>{{ o.label }}</span>
                    <el-tag v-if="o.safe" size="small" type="success" style="margin-left: 6px">免WhatsApp</el-tag>
                  </el-option>
                </el-select>
              </el-form-item>
            </div>
          </div>

          <!-- 配置教程 -->
          <div class="card-section guide-section">
            <span class="section-heading">接码金额 / 线路配置教程（对齐 SmsBower 网页点选）</span>
            <p class="guide-lead">
              目标：<b>点哪个价格，就只拿那个价格的号</b>。选 <code>0.008</code> 绝不会拿到 <code>0.007</code>。
              该档没货会报 <code>NO_NUMBERS</code>，系统不会偷偷换成更便宜的号。
            </p>
            <ol class="guide-steps">
              <li>先选国家（如印尼 <code>6</code>）。下方会实时列出该国档位：线路 ID · 金额 · 库存。</li>
              <li><b>推荐操作：</b>直接点标签或下拉选 <code>3237 · 0.008 $ (余 1.2万件)</code>。金额和供应商 ID 会一起填上。</li>
              <li>金额写法：
                <ul>
                  <li><code>0.008</code> 或 <code>=0.008</code> → 锁定该档，不含更便宜的号</li>
                  <li><code>0.007-0.008</code> → 允许这个区间</li>
                  <li><code>&lt;=0.008</code> → 最高限价，可能拿到 0.007</li>
                  <li>留空 → 不限价</li>
                </ul>
              </li>
              <li>「指定供应商 ID」优先向该线路要号。若平台对该参数返回 BANNED，会自动去掉指定线路，但<strong>仍排除更便宜档</strong>。</li>
              <li>「排除供应商 ID」用来拉黑低质通道（如 <code>3327</code>）。锁定金额时系统也会自动排除更便宜线路。</li>
            </ol>
            <div class="guide-table-wrap">
              <table class="guide-table">
                <thead>
                  <tr>
                    <th>你想要的效果</th>
                    <th>怎么填</th>
                    <th>会不会拿到更便宜的号</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>只要网页上的 0.008 档</td>
                    <td>点选 <code>0.008 $</code> 或填 <code>0.008</code></td>
                    <td>不会</td>
                  </tr>
                  <tr>
                    <td>只要指定线路 3237</td>
                    <td>下拉选 <code>3237 · 0.008 $</code></td>
                    <td>不会（金额一并锁定）</td>
                  </tr>
                  <tr>
                    <td>0.007 和 0.008 都可以</td>
                    <td>填 <code>0.007-0.008</code></td>
                    <td>会，这是你主动允许的区间</td>
                  </tr>
                  <tr>
                    <td>不超过 0.008 越便宜越好</td>
                    <td>填 <code>&lt;=0.008</code></td>
                    <td>会（平台优先派更便宜的）</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p class="guide-note">
              保存后对「自动注册 / OAuth 导出」全局生效。OAuth 弹窗里也可单独改一次，点「保存为默认配置」会写回这里。
            </p>
          </div>

          <!-- 重试与超时 -->
          <div class="card-section">
            <span class="section-heading">接码重试与超时控制</span>
            <el-row :gutter="12">
              <el-col :span="12">
                <el-form-item label="单账号最大换号重试次数">
                  <el-input v-model="maxPhoneAttempts" type="number" placeholder="默认 3 次" />
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="单号码最长等待短信时间 (秒)">
                  <el-input v-model="perPhoneTimeout" type="number" placeholder="默认 80 秒" />
                </el-form-item>
              </el-col>
            </el-row>
          </div>
        </div>
      </div>

      <FooterToolbar>
        <template #left>
          <span class="footer-info-text">
            接码服务商：<b>{{ provider === 'herosms' ? 'HeroSMS' : 'SmsBower' }}</b>
            {{ allowed.length ? ` · 轮换国家 ${allowed.length} 个` : '' }}
          </span>
        </template>
        <template #right>
          <el-button :loading="testing" @click="test">
            <el-icon><Wallet /></el-icon>测试连通与余额
          </el-button>
          <el-button type="primary" :loading="saving" @click="save">
            <el-icon><Check /></el-icon>保存配置
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
.header-tag {
  font-size: 11px;
}

.config-scroll-body {
  flex: 1;
  min-height: 0;
  padding: 20px 24px;
  overflow-y: auto;
}

.macos-settings-card {
  max-width: 720px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.card-section {
  background: var(--el-fill-color-light);
  border: 1px solid var(--app-border);
  border-radius: 10px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.section-switch-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.switch-meta {
  display: flex;
  flex-direction: column;
}
.switch-title {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--app-title);
}
.switch-desc {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  margin-top: 2px;
  line-height: 1.4;
}

.field-divider {
  height: 1px;
  background: var(--app-border);
  margin: 4px 0;
}

.field-col {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.section-heading {
  font-size: 12.5px;
  font-weight: 700;
  color: var(--app-title);
}

.field-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--app-text-regular);
}

.footer-info-text {
  font-size: 12px;
  color: var(--app-text-secondary);
}

.guide-section {
  background: rgba(14, 165, 233, 0.06);
  border-color: rgba(14, 165, 233, 0.22);
}
.guide-lead {
  margin: 0;
  font-size: 12.5px;
  line-height: 1.65;
  color: var(--app-title);
}
.guide-steps {
  margin: 0;
  padding-left: 18px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--app-text-regular);
}
.guide-steps li + li {
  margin-top: 4px;
}
.guide-steps ul {
  margin: 4px 0 0;
  padding-left: 16px;
}
.guide-steps code,
.guide-lead code,
.guide-table code,
.guide-note code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11.5px;
  padding: 0 4px;
  border-radius: 4px;
  background: rgba(15, 23, 42, 0.06);
}
.guide-table-wrap {
  overflow-x: auto;
}
.guide-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 11.5px;
  line-height: 1.5;
}
.guide-table th,
.guide-table td {
  border: 1px solid var(--app-border);
  padding: 6px 8px;
  text-align: left;
  vertical-align: top;
}
.guide-table th {
  background: rgba(15, 23, 42, 0.04);
  font-weight: 700;
  color: var(--app-title);
  white-space: nowrap;
}
.guide-note {
  margin: 0;
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--el-text-color-secondary);
}
</style>
