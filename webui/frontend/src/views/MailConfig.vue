<script setup>
import { computed, onActivated, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Setting, Message, Check, Connection, Search, InfoFilled, View, Hide } from '@element-plus/icons-vue'
import { getMailConfig, getMailProviders, saveMailConfig, testMail, fetchCfDomains } from '@/api/settings'
import FooterToolbar from '@/components/FooterToolbar.vue'

const providers = ref([])
const source = ref('outlook')
const form = ref({})
const saved = ref({})
const loading = ref(true)
const saving = ref(false)
const testing = ref(false)
const fetchingDomains = ref(false)
const discoveredDomains = ref([])
const testResultMsg = ref('')

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

function phFor(f) {
  return f.placeholder || ''
}

async function load() {
  loading.value = true
  testResultMsg.value = ''
  try {
    const [pr, cfg] = await Promise.all([getMailProviders(), getMailConfig()])
    providers.value = pr.providers || []
    saved.value = cfg.config || {}
    source.value = saved.value.mail_source || pr.current || 'outlook'

    const next = {}
    for (const p of providers.value) {
      for (const f of p.config_fields) {
        next[f.key] = saved.value[f.key] ?? ''
      }
    }
    form.value = next

    // 如果是 cf_temp 且配置了 API，自动预热拉取可用域名列表
    if (source.value === 'cf_temp' && (form.value.cf_api_url || '').trim()) {
      handleFetchDomains(true)
    }
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
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
      }
    } else if (!silent) {
      ElMessage.info('Worker 未返回可用域名，可手动输入收件域名')
    }
  } catch (e) {
    if (!silent) ElMessage.error('探测域名失败: ' + e.message)
  } finally {
    fetchingDomains.value = false
  }
}

function selectDomain(dom) {
  form.value.cf_domain = dom
  ElMessage.success(`已选用收件域名: ${dom}`)
}

async function save() {
  const payload = { mail_source: source.value }
  for (const f of fields.value) {
    const v = (form.value[f.key] ?? '').trim()
    payload[f.key] = v
  }

  const missing = fields.value
    .filter((f) => f.required)
    .filter((f) => !(form.value[f.key] ?? '').trim())
  if (missing.length) {
    ElMessage.warning('请填写必填项：' + missing.map((f) => f.label).join('、'))
    return
  }

  saving.value = true
  try {
    await saveMailConfig(payload)
    ElMessage.success('邮箱配置已保存')
    await load()
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    saving.value = false
  }
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
    ElMessage.success('测试成功')
  } catch (e) {
    testResultMsg.value = '测试失败: ' + e.message
    ElMessage.error(e.message)
  } finally {
    testing.value = false
  }
}

onActivated(() => load())
load()
</script>

<template>
  <div class="mailconfig-page" v-loading="loading">
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
          <div class="card-section">
            <span class="section-heading">选择验证码接信渠道</span>
            <el-radio-group v-model="source" class="macos-radio-group">
              <el-radio-button
                v-for="p in providers"
                :key="p.kind"
                :value="p.kind"
              >
                {{ p.display_name }}
              </el-radio-button>
            </el-radio-group>
          </div>

          <div v-if="current" class="card-section info-section">
            <span class="section-heading">渠道特性</span>
            <div class="caps-tags">
              <el-tag size="small" :type="current.pooled ? 'warning' : 'success'" effect="light">
                {{ current.pooled ? '📦 号池型：需先批量导入账号' : '⚡ 自建型：动态自动生成地址' }}
              </el-tag>
              <el-tag size="small" :type="current.ephemeral ? 'success' : 'info'" effect="plain">
                {{ current.ephemeral ? '每次注册使用新地址' : '固定地址轮询' }}
              </el-tag>
              <el-tag v-if="current.kind === 'cf_temp'" size="small" type="success" effect="plain">
                支持 dreamhunter2333 / cloudflare_temp_email Worker 协议
              </el-tag>
              <el-tag v-if="current.line_segments > 0" size="small" type="info" effect="plain">
                导入格式 {{ current.line_segments }} 段
              </el-tag>
            </div>
          </div>

          <div v-if="fields.length" class="card-section form-section">
            <div class="section-header-row">
              <span class="section-heading">渠道参数详情</span>
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

            <el-form label-position="top" size="small">
              <el-form-item
                v-for="f in fields"
                :key="f.key"
                :label="f.label"
                :required="f.required"
              >
                <!-- 针对收件域名的专属快捷切换与多域名轮换交互 -->
                <div v-if="f.key === 'cf_domain'" class="domain-input-wrapper">
                  <!-- 快捷域名切换胶囊群 -->
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
                  />
                  <div v-if="f.help" class="hint-text">{{ f.help }}</div>
                </div>
              </el-form-item>
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
            <el-icon><Connection /></el-icon>测试连通性
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
  max-width: 680px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 18px;
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

.section-header-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.section-heading {
  font-size: 12.5px;
  font-weight: 700;
  color: var(--app-title);
}

.caps-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
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
  margin-top: 3px;
  line-height: 1.4;
}

.test-result-box {
  background: #1e1e1e;
  border-radius: 8px;
  padding: 12px 14px;
  border: 1px solid #333;
}

.test-result-text {
  margin: 0;
  font-family: 'SF Mono', Monaco, Menlo, Consolas, monospace;
  font-size: 11.5px;
  line-height: 1.5;
  color: #5af78e;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
