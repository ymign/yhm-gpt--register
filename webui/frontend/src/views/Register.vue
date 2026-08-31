<script setup>
import { computed, onActivated, ref } from 'vue'
import { useRoute } from 'vue-router'
import { storeToRefs } from 'pinia'
import { ElMessage } from 'element-plus'
import {
  VideoPlay,
  Setting,
  CopyDocument,
  Lock,
  Key,
  Position,
  Connection,
  Timer,
  Message,
} from '@element-plus/icons-vue'
import { startRegister, getRegistered } from '@/api/register'
import { copyText } from '@/api/request'
import { useFormStore, proxyText, COUNTRY_OPTIONS } from '@/stores/form'
import { useProxyStore } from '@/stores/proxy'
import { useRuntimeStore } from '@/stores/runtime'
import LogPanel from '@/components/LogPanel.vue'

const route = useRoute()
const { form } = storeToRefs(useFormStore())
const { list: proxyList } = storeToRefs(useProxyStore())
const runtime = useRuntimeStore()
const { runningSingle, lastRunResult } = storeToRefs(runtime)

const starting = ref(false)
const regEmail = ref('')

const emailPlaceholder = computed(() => {
  if (form.value.mailSource === 'remail') {
    return '留空 = 自动调用 Remail 接口购买全新临时邮箱并注册 / 或填入指定邮箱'
  }
  if (form.value.mailSource === 'cf_temp') {
    return '留空 = 自动由 CF 临时邮箱动态生成新地址 / 或填入指定自定义地址'
  }
  if (form.value.mailSource === 'outlook') {
    return '留空 = 自动从微软号池 Claim 可用账号 / 或填入指定 Outlook 邮箱'
  }
  if (form.value.mailSource === 'icloud_relay') {
    return '留空 = 自动从 iCloud 号池 Claim 可用账号 / 或填入指定 iCloud 邮箱'
  }
  return '留空 = 自动从号池挑选可用账号 / 或填入指定邮箱'
})

onActivated(() => {
  if (route.query.email) regEmail.value = String(route.query.email)
})

async function run() {
  starting.value = true
  runtime.clearLogs()
  lastRunResult.value = null
  try {
    const r = await startRegister({
      email: regEmail.value.trim() || null,
      mail_source: form.value.mailSource || 'cf_temp',
      proxy: proxyText(form.value),
      proxy_country: form.value.proxyCountry || '',
      otp_timeout: parseInt(form.value.otpTimeout, 10) || 10,
      want_access_token: true,
      want_session_token: true,
      want_refresh_token: form.value.wantRefreshToken || false,
      want_2fa: form.value.want2fa,
      want_password: form.value.wantPassword,
    })
    runtime.addLog(`[client] 启动注册 run_id=${r.run_id} email=${r.email} 渠道=${form.value.mailSource || 'cf_temp'}`, 'evt')
    runtime.streamRun(r.run_id)
  } catch (e) {
    ElMessage.error(e.message)
    lastRunResult.value = { error: e.message }
  } finally {
    starting.value = false
  }
}

async function copyField(email, field) {
  try {
    const { data } = await getRegistered(email)
    const val = data[field] || ''
    if (!val) { ElMessage.warning(`${field} 为空`); return }
    await copyText(val)
  } catch (e) {
    ElMessage.error('加载凭证失败: ' + e.message)
  }
}
</script>

<template>
  <div class="register-page">
    <div class="macos-window-panel">
      <!-- 窗口标题栏 -->
      <div class="macos-panel-header">
        <div class="header-left">
          <div class="window-dot-group">
            <span class="dot red"></span>
            <span class="dot yellow"></span>
            <span class="dot green"></span>
          </div>
          <span class="panel-title">单次注册调试控制台 · Register Workbench</span>
        </div>
        <div class="header-right">
          <el-tag size="small" type="primary" effect="plain" class="header-tag">
            <el-icon><Position /></el-icon>单账号注册与凭证生成
          </el-tag>
        </div>
      </div>

      <!-- 双栏布局 (左配置表单 + 右 macOS 终端) -->
      <div class="macos-split-container">
        <!-- 左栏：注册配置表单 -->
        <div class="form-pane">
          <div class="pane-inner">
            <div class="pane-section-title">
              <el-icon><Setting /></el-icon>注册参数配置
            </div>

            <el-form label-position="top" size="small" class="macos-form">
              <!-- 核心：邮箱接收渠道选择 -->
              <el-form-item label="接码邮箱渠道 (选择本次注册使用的邮箱来源)">
                <el-radio-group v-model="form.mailSource" class="macos-radio-group full-width-radio">
                  <el-radio-button value="remail">🍎 Remail 自动购号</el-radio-button>
                  <el-radio-button value="cf_temp">⚡ CF 临时邮箱</el-radio-button>
                  <el-radio-button value="outlook">📦 微软 Outlook</el-radio-button>
                  <el-radio-button value="icloud_relay">✉️ iCloud 邮箱</el-radio-button>
                </el-radio-group>
                <div class="mail-source-hint">
                  <span v-if="form.mailSource === 'remail'" class="hint-remail" style="color: #10b981">🍎 Remail 全自动购号：支持按需购买 Project 2 (ChatGPT专属 30积分 iCloud/15积分 微软邮箱) 及多平台临时邮箱，在「邮箱配置」可随时自定义项目与后缀</span>
                  <span v-else-if="form.mailSource === 'cf_temp'" class="hint-cf">⚡ 无需号池：由 Cloudflare Worker 动态生成临时地址并全自动收信</span>
                  <span v-else-if="form.mailSource === 'outlook'" class="hint-outlook">📦 号池接码：自动从微软号池 Claim 可用账号（需提前导入）</span>
                  <span v-else-if="form.mailSource === 'icloud_relay'" class="hint-ic">✉️ iCloud 中转：自动从 iCloud 号池领取带中转链接的账号</span>
                </div>
              </el-form-item>

              <el-form-item label="目标邮箱地址 (留空自动生成 / 从号池领取)">
                <el-input
                  v-model="regEmail"
                  :placeholder="emailPlaceholder"
                  clearable
                  class="mono"
                />
              </el-form-item>

              <el-form-item label="单次注册代理 (支持下拉选择/代理池轮询/手动输入/留空直连)">
                <el-select
                  v-model="form.proxy"
                  filterable
                  clearable
                  allow-create
                  default-first-option
                  :reserve-keyword="false"
                  placeholder="socks5://user:pass@host:1080"
                  style="width: 100%"
                >
                  <el-option v-for="p in proxyList" :key="p" :label="p" :value="p" />
                </el-select>
                <div class="form-hint">
                  批量并发轮换与代理质量检测请前往「代理池」页面管理。
                </div>
              </el-form-item>

              <el-row :gutter="10">
                <el-col :span="16">
                  <el-form-item label="代理目标国家出口">
                    <el-select
                      v-model="form.proxyCountry"
                      filterable
                      allow-create
                      placeholder="选择国家"
                      style="width: 100%"
                    >
                      <el-option v-for="c in COUNTRY_OPTIONS" :key="c.value" :label="c.label" :value="c.value" />
                    </el-select>
                  </el-form-item>
                </el-col>
                <el-col :span="8">
                  <el-form-item label="OTP超时(秒)">
                    <el-input-number v-model="form.otpTimeout" :min="10" :max="600" style="width: 100%" />
                  </el-form-item>
                </el-col>
              </el-row>

              <!-- 自动化附加功能卡片 -->
              <div class="feature-switches-card">
                <div class="switch-row">
                  <div class="switch-meta">
                    <span class="switch-title">自动设置强随机密码</span>
                    <span class="switch-desc">强制生成 16 位高强度密码并落库，确保账号随时可密码登录</span>
                  </div>
                  <el-switch v-model="form.wantPassword" />
                </div>

                <div class="switch-divider"></div>

                <div class="switch-row">
                  <div class="switch-meta">
                    <span class="switch-title">自动绑定 2FA (TOTP)</span>
                    <span class="switch-desc">注册成功后自动生成并绑定 TOTP Secret，大幅降低 OpenAI 封号率</span>
                  </div>
                  <el-switch v-model="form.want2fa" />
                </div>
              </div>

              <!-- 启动按钮 -->
              <div class="form-actions">
                <el-button
                  type="primary"
                  class="start-btn"
                  :loading="starting || runningSingle"
                  @click="run"
                >
                  <el-icon><VideoPlay /></el-icon>
                  {{ starting || runningSingle ? '正在执行注册流程...' : '开始单次注册' }}
                </el-button>
              </div>
            </el-form>

            <!-- 注册成功凭证卡片 -->
            <el-collapse-transition>
              <div v-if="lastRunResult && !lastRunResult.error" class="result-cred-card">
                <div class="result-header">
                  <span class="result-badge">SUCCESS</span>
                  <span class="result-email mono">{{ lastRunResult.email }}</span>
                </div>

                <div class="result-grid">
                  <div v-if="lastRunResult.password" class="result-item">
                    <span class="item-label">登录密码</span>
                    <div class="item-val-row">
                      <span class="item-val mono">{{ lastRunResult.password }}</span>
                      <el-button size="small" text type="primary" @click="copyText(lastRunResult.password, '密码已复制')">
                        <el-icon><CopyDocument /></el-icon>
                      </el-button>
                    </div>
                  </div>

                  <div v-if="lastRunResult.totp_secret" class="result-item">
                    <span class="item-label">2FA Secret (仅此一次)</span>
                    <div class="item-val-row">
                      <span class="item-val mono highlight-2fa">{{ lastRunResult.totp_secret }}</span>
                      <el-button size="small" text type="primary" @click="copyText(lastRunResult.totp_secret, '2FA Secret 已复制')">
                        <el-icon><CopyDocument /></el-icon>
                      </el-button>
                    </div>
                  </div>
                </div>

                <div class="result-quick-copy">
                  <el-button size="small" @click="copyText(lastRunResult.email)">复制邮箱</el-button>
                  <el-button
                    v-if="lastRunResult.password"
                    size="small"
                    @click="copyText(lastRunResult.email + '----' + lastRunResult.password, '邮箱----密码已复制')"
                  >
                    复制 邮箱----密码
                  </el-button>
                  <el-button
                    v-if="lastRunResult.access_token_len > 0"
                    size="small"
                    type="primary"
                    plain
                    @click="copyField(lastRunResult.email, 'access_token')"
                  >
                    复制 Access Token
                  </el-button>
                </div>
              </div>
            </el-collapse-transition>

            <el-alert
              v-if="lastRunResult && lastRunResult.error"
              type="error"
              :closable="false"
              style="margin-top: 14px; border-radius: 8px"
              :title="lastRunResult.error"
            />
          </div>
        </div>

        <!-- 右栏：macOS 风格实时终端 -->
        <div class="terminal-pane">
          <LogPanel />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.register-page {
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

.macos-split-container {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 440px 1fr;
  overflow: hidden;
}

.form-pane {
  border-right: 1px solid var(--app-border);
  overflow-y: auto;
  background: var(--app-card-bg);
}
.pane-inner {
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.pane-section-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 700;
  color: var(--app-title);
}

.full-width-radio {
  width: 100%;
  display: flex;
}
.full-width-radio :deep(.el-radio-button) {
  flex: 1;
}
.full-width-radio :deep(.el-radio-button__inner) {
  width: 100%;
  text-align: center;
  padding: 8px 10px;
  font-size: 12px;
}

.mail-source-hint {
  margin-top: 6px;
  font-size: 11px;
  line-height: 1.4;
}
.hint-cf {
  color: #67c23a;
  font-weight: 500;
}
.hint-outlook {
  color: #409eff;
  font-weight: 500;
}
.hint-ic {
  color: #e6a23c;
  font-weight: 500;
}

.form-hint {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  line-height: 1.5;
  margin-top: 4px;
}

.feature-switches-card {
  background: var(--el-fill-color-light);
  border: 1px solid var(--app-border);
  border-radius: 8px;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-bottom: 14px;
}
.switch-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.switch-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.switch-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--app-title);
}
.switch-desc {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}
.switch-divider {
  height: 1px;
  background: var(--app-border);
  opacity: 0.6;
}

.form-actions {
  margin-top: 6px;
}
.start-btn {
  width: 100%;
  height: 38px;
  font-size: 13px;
  font-weight: 600;
  border-radius: 8px;
}

.terminal-pane {
  background: #1e1e1e;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.result-cred-card {
  margin-top: 14px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-color-success-light-5, #a3e635);
  border-radius: 8px;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.result-header {
  display: flex;
  align-items: center;
  gap: 8px;
}
.result-badge {
  font-size: 10px;
  font-weight: 800;
  background: #67c23a;
  color: #fff;
  padding: 1px 6px;
  border-radius: 4px;
}
.result-email {
  font-size: 12.5px;
  font-weight: 700;
  color: var(--app-title);
}
.result-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 8px;
  background: var(--app-card-bg);
  padding: 10px;
  border-radius: 6px;
  border: 1px solid var(--app-border);
}
.result-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.item-label {
  font-size: 10.5px;
  color: var(--el-text-color-secondary);
}
.item-val-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.item-val {
  font-size: 12px;
  font-weight: 600;
  color: var(--app-title);
  word-break: break-all;
}
.highlight-2fa {
  color: #e6a23c;
}
.result-quick-copy {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
</style>
