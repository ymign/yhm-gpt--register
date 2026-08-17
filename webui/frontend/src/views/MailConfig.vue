<script setup>
import { computed, onActivated, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Setting, Message, Check, Connection } from '@element-plus/icons-vue'
import { getMailConfig, getMailProviders, saveMailConfig, testMail } from '@/api/settings'
import FooterToolbar from '@/components/FooterToolbar.vue'

const providers = ref([])
const source = ref('outlook')
const form = ref({})
const saved = ref({})
const loading = ref(true)
const saving = ref(false)
const testing = ref(false)

const current = computed(
  () => providers.value.find((p) => p.kind === source.value) || null,
)
const fields = computed(() => current.value?.config_fields || [])
const canTest = computed(() => !!current.value && !current.value.pooled)

function phFor(f) {
  if (f.type === 'password' && saved.value[f.key] === '***') {
    return '已设置（留空则不修改）'
  }
  return f.placeholder || ''
}

async function load() {
  loading.value = true
  try {
    const [pr, cfg] = await Promise.all([getMailProviders(), getMailConfig()])
    providers.value = pr.providers || []
    saved.value = cfg.config || {}
    source.value = saved.value.mail_source || pr.current || 'outlook'

    const next = {}
    for (const p of providers.value) {
      for (const f of p.config_fields) {
        next[f.key] = f.type === 'password' ? '' : (saved.value[f.key] ?? '')
      }
    }
    form.value = next
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

async function save() {
  const payload = { mail_source: source.value }
  for (const f of fields.value) {
    const v = (form.value[f.key] ?? '').trim()
    if (f.type === 'password' && !v) {
      if (saved.value[f.key] === '***') continue
    }
    payload[f.key] = v
  }

  const missing = fields.value
    .filter((f) => f.required)
    .filter((f) => {
      const v = (form.value[f.key] ?? '').trim()
      return !v && !(f.type === 'password' && saved.value[f.key] === '***')
    })
  if (missing.length) {
    ElMessage.warning('请填写必填项：' + missing.map((f) => f.label).join('、'))
    return
  }

  saving.value = true
  try {
    await saveMailConfig(payload)
    ElMessage.success('配置已保存')
    await load()
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    saving.value = false
  }
}

async function test() {
  testing.value = true
  try {
    const r = await testMail()
    ElMessage.success(r.message || '连通正常')
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
              <el-tag v-if="current.line_segments > 0" size="small" type="info" effect="plain">
                导入格式 {{ current.line_segments }} 段
              </el-tag>
            </div>
          </div>

          <div v-if="fields.length" class="card-section form-section">
            <span class="section-heading">渠道参数详情</span>
            <el-form label-position="top" size="small">
              <el-form-item
                v-for="f in fields"
                :key="f.key"
                :label="f.label"
                :required="f.required"
              >
                <el-input
                  v-model="form[f.key]"
                  :type="f.type === 'password' ? 'password' : 'text'"
                  :show-password="f.type === 'password'"
                  :placeholder="phFor(f)"
                />
                <div v-if="f.help" class="hint-text">{{ f.help }}</div>
              </el-form-item>
            </el-form>
          </div>

          <el-alert
            v-if="current && !current.pooled && fields.length"
            type="warning"
            :closable="false"
            show-icon
            style="border-radius: 8px"
            title="自建邮箱需配置好 Catch-all 转发，确保验证码能实时投递给服务端接口。"
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

.hint-text {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  margin-top: 3px;
  line-height: 1.4;
}
</style>
