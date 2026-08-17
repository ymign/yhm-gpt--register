<script setup>
import { onActivated, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Setting, Check, Connection, Download, Share } from '@element-plus/icons-vue'
import { getExportConfig, saveExportConfig, testExport } from '@/api/settings'
import FooterToolbar from '@/components/FooterToolbar.vue'

const cpa = reactive({ enabled: false, url: '', key: '', keyPh: '粘贴 CPA 管理密钥', timeout: 30 })
const sub = reactive({ enabled: false, url: '', key: '', keyPh: '粘贴面板里生成的 x-api-key', groupIds: '2', timeout: 30 })
const saving = ref(false)
const testingCpa = ref(false)
const testingSub = ref(false)

async function load() {
  try {
    const { config } = await getExportConfig()
    cpa.enabled = config.cpa_enabled === '1'
    cpa.url = config.cpa_url || ''
    cpa.key = ''
    cpa.keyPh = config.cpa_mgmt_key === '***' ? '已设置（留空不修改）' : '粘贴 CPA 管理密钥'
    cpa.timeout = Number(config.cpa_timeout || 30)
    sub.enabled = config.sub2api_enabled === '1'
    sub.url = config.sub2api_url || ''
    sub.key = ''
    sub.keyPh = config.sub2api_api_key === '***' ? '已设置（留空不修改）' : '粘贴面板里生成的 x-api-key'
    sub.groupIds = config.sub2api_group_ids || '2'
    sub.timeout = Number(config.sub2api_timeout || 30)
  } catch (e) {
    ElMessage.error(e.message)
  }
}

async function save() {
  saving.value = true
  try {
    await saveExportConfig({
      cpa_enabled: cpa.enabled ? '1' : '0',
      cpa_url: cpa.url.trim(),
      cpa_mgmt_key: cpa.key.trim() || '***',
      cpa_timeout: String(cpa.timeout || 30),
      sub2api_enabled: sub.enabled ? '1' : '0',
      sub2api_url: sub.url.trim(),
      sub2api_api_key: sub.key.trim() || '***',
      sub2api_group_ids: sub.groupIds.trim() || '2',
      sub2api_timeout: String(sub.timeout || 30),
    })
    ElMessage.success('自动导出配置已保存')
    load()
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    saving.value = false
  }
}

async function test(target) {
  const flag = target === 'cpa' ? testingCpa : testingSub
  flag.value = true
  try {
    const r = await testExport(target)
    ElMessage.success(r.message || '连通正常')
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    flag.value = false
  }
}

onActivated(() => load())
load()
</script>

<template>
  <div class="exportconfig-page">
    <div class="macos-window-panel">
      <!-- 窗口标题栏 -->
      <div class="macos-panel-header">
        <div class="header-left">
          <div class="window-dot-group">
            <span class="dot red"></span>
            <span class="dot yellow"></span>
            <span class="dot green"></span>
          </div>
          <span class="panel-title">自动推送与外部导出配置 · Auto Export Pipeline</span>
        </div>
        <div class="header-right">
          <el-tag size="small" type="primary" effect="plain" class="header-tag">
            需带 Refresh Token
          </el-tag>
        </div>
      </div>

      <div class="config-scroll-body">
        <div class="macos-settings-card">
          <!-- 提示横幅 -->
          <div class="info-alert-card">
            <div class="alert-title">💡 运作说明</div>
            <div class="alert-desc">
              启用后，每次注册成功并生成 Refresh Token 时，系统会自动将账号凭证即时投递推送至外部管理平台（CPA 或 SUB2API）。若未开启接码或未获取到 RT，该推送会被自动跳过。
            </div>
          </div>

          <!-- CPA 面板卡片 -->
          <div class="card-section">
            <div class="section-switch-row">
              <div class="switch-meta">
                <span class="switch-title">CPA 面板自动同步</span>
                <span class="switch-desc">注册成功自动调用 POST /v0/management/auth-files 导入凭证</span>
              </div>
              <el-switch v-model="cpa.enabled" />
            </div>

            <div v-show="cpa.enabled" class="expanded-form" style="margin-top: 12px">
              <el-form label-position="top" size="small">
                <el-form-item label="CPA 平台地址 (URL)">
                  <el-input v-model="cpa.url" placeholder="https://cpa.example.com" />
                </el-form-item>

                <el-form-item label="CPA 管理密钥 (Management Key)">
                  <el-input v-model="cpa.key" type="password" show-password :placeholder="cpa.keyPh" />
                </el-form-item>

                <el-row :gutter="12">
                  <el-col :span="12">
                    <el-form-item label="请求超时时间 (秒)">
                      <el-input-number v-model="cpa.timeout" :min="5" :max="300" style="width: 100%" />
                    </el-form-item>
                  </el-col>
                  <el-col :span="12" style="display: flex; align-items: flex-end; padding-bottom: 18px">
                    <el-button size="small" :loading="testingCpa" @click="test('cpa')">
                      <el-icon><Connection /></el-icon>测试 CPA 连通性
                    </el-button>
                  </el-col>
                </el-row>
              </el-form>
            </div>
          </div>

          <!-- SUB2API 面板卡片 -->
          <div class="card-section">
            <div class="section-switch-row">
              <div class="switch-meta">
                <span class="switch-title">SUB2API 面板自动同步</span>
                <span class="switch-desc">注册成功自动调用 POST /api/v1/admin/accounts 录入分发池</span>
              </div>
              <el-switch v-model="sub.enabled" />
            </div>

            <div v-show="sub.enabled" class="expanded-form" style="margin-top: 12px">
              <el-form label-position="top" size="small">
                <el-form-item label="SUB2API 平台地址 (URL)">
                  <el-input v-model="sub.url" placeholder="https://sub2api.example.com" />
                </el-form-item>

                <el-form-item label="管理员 API Key">
                  <el-input v-model="sub.key" type="password" show-password :placeholder="sub.keyPh" />
                </el-form-item>

                <el-row :gutter="12">
                  <el-col :span="12">
                    <el-form-item label="分配的目标分组 IDs (如 2 或 1,2,3)">
                      <el-input v-model="sub.groupIds" placeholder="2" />
                    </el-form-item>
                  </el-col>
                  <el-col :span="12">
                    <el-form-item label="请求超时 (秒)">
                      <el-input-number v-model="sub.timeout" :min="5" :max="300" style="width: 100%" />
                    </el-form-item>
                  </el-col>
                </el-row>

                <el-button size="small" :loading="testingSub" @click="test('sub2api')">
                  <el-icon><Connection /></el-icon>测试 SUB2API 连通性
                </el-button>
              </el-form>
            </div>
          </div>
        </div>
      </div>

      <FooterToolbar>
        <template #left>
          <span class="footer-info-text">
            CPA: <b>{{ cpa.enabled ? '已开启' : '未开启' }}</b> · SUB2API: <b>{{ sub.enabled ? '已开启' : '未开启' }}</b>
          </span>
        </template>
        <template #right>
          <el-button type="primary" :loading="saving" @click="save">
            <el-icon><Check /></el-icon>保存配置
          </el-button>
        </template>
      </FooterToolbar>
    </div>
  </div>
</template>

<style scoped>
.exportconfig-page {
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

.info-alert-card {
  background: rgba(0, 122, 255, 0.08);
  border: 1px solid rgba(0, 122, 255, 0.25);
  border-radius: 10px;
  padding: 12px 16px;
}
.alert-title {
  font-size: 12.5px;
  font-weight: 700;
  color: var(--apple-blue);
  margin-bottom: 4px;
}
.alert-desc {
  font-size: 11.5px;
  color: var(--app-title);
  line-height: 1.5;
}

.card-section {
  background: var(--el-fill-color-light);
  border: 1px solid var(--app-border);
  border-radius: 10px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
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

.footer-info-text {
  font-size: 12px;
  color: var(--app-text-secondary);
}
</style>
