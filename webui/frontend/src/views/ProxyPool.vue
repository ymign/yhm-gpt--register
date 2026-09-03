<script setup>
import { computed, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Connection,
  VideoPlay,
  CopyDocument,
  Delete,
  Plus,
  Refresh,
  DocumentAdd,
} from '@element-plus/icons-vue'
import { useProxyStore, isValidProxy, proxyScheme, changeProxyProtocol } from '@/stores/proxy'
import { useFormStore } from '@/stores/form'
import { testProxies, getProxyHealth, getProxyHealthOverview, setProxyBlacklist } from '@/api/proxy'
import { copyText } from '@/api/request'

const proxyStore = useProxyStore()
const formStore = useFormStore()
const { list, count, protocol } = storeToRefs(proxyStore)

const draft = ref('')
const testResults = ref({})
const testingAll = ref(false)

function handleProtocolChange(val) {
  proxyStore.setProtocol(val, true)
  if (formStore.form.proxy) {
    formStore.form.proxy = changeProxyProtocol(formStore.form.proxy, val)
  }
  ElMessage.success(`已切换为 ${val === 'http' ? 'HTTP' : 'SOCKS5H'} 协议模式，全局代理池已自动同步转换生效！`)
}

function convertCurrentPool() {
  proxyStore.convertAll()
  if (formStore.form.proxy) {
    formStore.form.proxy = changeProxyProtocol(formStore.form.proxy, protocol.value)
  }
  ElMessage.success(`已将当前代理池内全部节点统一转换为 ${protocol.value === 'http' ? 'HTTP' : 'SOCKS5H'} 协议！`)
}

// ── 代理健康度（死号反哺）：号注册成功计 total，事后验死计 dead ──
// 动态住宅代理一号一个 session，健康度按「归一化模板 × 国家」聚合（后端算），
// 前端把代理池条目同样归一化后 join。
const proxyHealth = ref({}) // template -> {total, dead, blacklisted, countries: []}
const healthLoading = ref(false)
const overview = ref(null) // 总览面板数据
const overviewLoading = ref(false)
const showOverview = ref(true)

// 与后端 proxy_util.normalize_proxy_key 同规则：抹掉 session/sid 段得到模板
function normalizeProxyKey(p) {
  p = String(p || '').trim()
  if (!p) return ''
  const m = p.match(/^([a-z0-9+.-]+:\/\/)?([^@]*)@?(.+)$/i)
  if (!m) return p
  let [, scheme = 'http://', cred = '', host = ''] = m
  let [username = '', password = ''] = cred.split(':')
  username = username.replace(/(-sid-|-session-|_session-)[a-z0-9]+/gi, '$1*')
  const pm = password.match(/^(.+)-([A-Za-z]{2})-(\d+)-(\d+[A-Za-z]+)$/)
  if (pm) password = `${pm[1]}-${pm[2]}-*-${pm[4]}`
  if (!scheme) scheme = 'http://'
  return username || password ? `${scheme}${username}:${password}@${host}` : `${scheme}${host}`
}

async function loadHealth() {
  healthLoading.value = true
  overviewLoading.value = true
  try {
    const [r, ov] = await Promise.all([getProxyHealth(), getProxyHealthOverview()])
    const map = {}
    for (const h of r.items || []) map[h.template] = h
    proxyHealth.value = map
    overview.value = ov
  } catch (_) {}
  finally {
    healthLoading.value = false
    overviewLoading.value = false
  }
}
loadHealth()

function fmtAgo(ts) {
  if (!ts) return ''
  const s = Math.floor(Date.now() / 1000 - ts)
  if (s < 60) return `${s}秒前`
  if (s < 3600) return `${Math.floor(s / 60)}分钟前`
  if (s < 86400) return `${Math.floor(s / 3600)}小时前`
  return `${Math.floor(s / 86400)}天前`
}

function pct(rate) {
  return (rate * 100).toFixed(rate * 100 >= 10 ? 0 : 1) + '%'
}

function maskProxy(p) {
  if (!p) return '（老号无存档）'
  if (p.length <= 30) return p
  return p.slice(0, 24) + '…' + p.slice(-6)
}

// 分国家明细文案（表格 tooltip 用）
function countryDetail(h) {
  if (!h || !h.countries?.length) return '暂无分国家数据'
  return h.countries
    .map((c) => `${c.country || '?'}: ${c.dead}/${c.total}${c.blacklisted ? ' 🚫' : ''}`)
    .join('\n')
}

const rows = computed(() =>
  list.value.map((p, i) => ({
    index: i + 1,
    proxy: p,
    valid: isValidProxy(p),
    result: testResults.value[p] || null,
    health: proxyHealth.value[normalizeProxyKey(p)] || null,
  })),
)
const invalidCount = computed(() => rows.value.filter((r) => !r.valid).length)
const blacklistedCount = computed(() => rows.value.filter((r) => r.health?.blacklisted).length)

// 死亡率档位：>=25% 红（该拉黑了）、>=10% 黄（警惕）、有死亡但 <10% 灰、没死绿
function healthClass(h) {
  if (!h || !h.total) return 'health-none'
  const rate = h.dead / h.total
  if (h.blacklisted) return 'health-black'
  if (rate >= 0.25) return 'health-bad'
  if (rate >= 0.1) return 'health-warn'
  return 'health-ok'
}

function healthText(h) {
  if (!h) return '无记录'
  if (!h.total) return '无记录'
  return `${h.dead}/${h.total}`
}

async function toggleBlacklist(row) {
  const on = !row.health?.blacklisted
  const label = on ? '拉黑' : '取消拉黑'
  try {
    if (on && !(await ElMessageBox.confirm(
      `确定拉黑整个代理模板？\n\n${row.proxy}\n\n动态代理一号一 IP，按「模板×国家」聚合健康度。整模板拉黑后全自动批量立即跳过该代理（正在跑的号不受影响）；只是某国出口脏的话建议在总览面板单独拉黑该国。`,
      '拉黑代理模板', { type: 'warning', confirmButtonText: '拉黑', cancelButtonText: '取消' },
    ))) return
    await setProxyBlacklist(row.proxy, '', on)
    await loadHealth()
    ElMessage.success(`已${label}`)
  } catch (e) {
    ElMessage.error(`${label}失败: ` + e.message)
  }
}

// 面板「按国家死亡率」chip 点击：拉黑 / 取消拉黑该国家出口（所有模板）
async function toggleCountryBlacklist(c) {
  const on = !c.blacklisted
  try {
    if (on && !(await ElMessageBox.confirm(
      `确定拉黑 ${c.country} 出口？\n\n该出口累计注册 ${c.total} 个号死了 ${c.dead} 个（死亡率 ${pct(c.rate)}）。\n拉黑后注册时目标国家命中它会自动换国（所有代理模板），其它国家出口不受影响。`,
      `拉黑 ${c.country} 出口`, { type: 'warning', confirmButtonText: '拉黑', cancelButtonText: '取消' },
    ))) return
    await setProxyBlacklist('', c.country, on)
    await loadHealth()
    ElMessage.success(`已${on ? '拉黑' : '恢复'} ${c.country} 出口`)
  } catch (e) {
    ElMessage.error('操作失败: ' + e.message)
  }
}

// 问题出口榜快捷拉黑（组合级：模板×国家）
async function blacklistCombo(w) {
  try {
    if (!(await ElMessageBox.confirm(
      `确定拉黑 ${w.country} 出口组合？\n\n${w.template}\n\n死亡率 ${w.dead}/${w.total}。拉黑后注册时自动换国。`,
      `拉黑 ${w.country} 出口`, { type: 'warning', confirmButtonText: '拉黑', cancelButtonText: '取消' },
    ))) return
    await setProxyBlacklist(w.template, w.country, true)
    await loadHealth()
    ElMessage.success(`已拉黑 ${w.country} 出口`)
  } catch (e) {
    ElMessage.error('拉黑失败: ' + e.message)
  }
}

async function runTest(targets) {
  if (!targets.length) return
  for (const p of targets) testResults.value[p] = { status: 'testing' }
  try {
    const { results } = await testProxies(targets)
    for (const [proxy, res] of Object.entries(results)) {
      testResults.value[proxy] = { status: res.ok ? 'ok' : 'fail', ...res }
    }
  } catch (e) {
    for (const p of targets) testResults.value[p] = { status: 'fail', error: e.message }
    ElMessage.error('测试失败: ' + e.message)
  }
}

async function testOne(proxy) {
  await runTest([proxy])
}

async function testAll() {
  if (!count.value) return
  testingAll.value = true
  try {
    await runTest([...list.value])
  } finally {
    testingAll.value = false
  }
}

function save() {
  if (!draft.value.trim()) {
    ElMessage.warning('请先粘贴代理')
    return
  }
  const r = proxyStore.setFromText(draft.value)
  draft.value = ''
  ElMessage.success(`已保存 ${r.kept} 个代理${r.duplicated ? `（去重 ${r.duplicated} 个）` : ''}`)
}

function append() {
  if (!draft.value.trim()) {
    ElMessage.warning('请先粘贴代理')
    return
  }
  const r = proxyStore.append(draft.value)
  draft.value = ''
  ElMessage.success(`已追加 ${r.added} 个新代理`)
}

async function clearAll() {
  if (!count.value) return
  try {
    await ElMessageBox.confirm(`确定清空全部 ${count.value} 个代理？`, '确认', {
      type: 'warning',
      confirmButtonText: '确定',
      cancelButtonText: '取消',
    })
    proxyStore.clear()
    ElMessage.success('已清空')
  } catch (_) {}
}

function editInDraft() {
  draft.value = proxyStore.text
  ElMessage.info('已把当前代理池载入编辑框，改完点「覆盖保存」')
}
</script>

<template>
  <div class="proxypool-page">
    <div class="macos-window-panel">
      <!-- 窗口标题栏 -->
      <div class="macos-panel-header">
        <div class="header-left">
          <div class="window-dot-group">
            <span class="dot red"></span>
            <span class="dot yellow"></span>
            <span class="dot green"></span>
          </div>
          <span class="panel-title">全局代理池管理 · Proxy Cluster</span>
        </div>
        <div class="header-right">
          <span class="header-badge">共 {{ count }} 个代理节点</span>
          <span v-if="blacklistedCount" class="header-badge badge-err">🚫 {{ blacklistedCount }} 个已拉黑</span>
          <span v-if="invalidCount" class="header-badge badge-err">{{ invalidCount }} 个异常</span>
        </div>
      </div>

      <!-- ── 代理健康度总览面板（死号反哺数据一览） ── -->
      <div class="health-overview-panel">
        <div class="overview-header" @click="showOverview = !showOverview">
          <div class="overview-title">
            <span>📊 代理健康度总览</span>
            <span class="overview-sub">死号反哺 · 脏 IP 自动出局</span>
          </div>
          <div class="overview-actions">
            <el-button size="small" text :loading="overviewLoading" @click.stop="loadHealth">
              <el-icon><Refresh /></el-icon>刷新
            </el-button>
            <span class="overview-toggle">{{ showOverview ? '收起 ▲' : '展开 ▼' }}</span>
          </div>
        </div>

        <div v-if="showOverview" v-loading="overviewLoading" class="overview-body">
          <!-- 统计卡横排 -->
          <div class="stat-cards">
            <div class="stat-card">
              <div class="stat-num">{{ overview?.summary?.tracked ?? 0 }}</div>
              <div class="stat-label">出口组合</div>
            </div>
            <div class="stat-card" :class="{ 'stat-danger': overview?.summary?.blacklisted > 0 }">
              <div class="stat-num">{{ overview?.summary?.blacklisted ?? 0 }}</div>
              <div class="stat-label">🚫 已拉黑组合</div>
            </div>
            <div class="stat-card">
              <div class="stat-num">{{ overview?.summary?.total_registered ?? 0 }}</div>
              <div class="stat-label">注册号数</div>
            </div>
            <div class="stat-card" :class="{ 'stat-danger': overview?.summary?.total_dead > 0 }">
              <div class="stat-num">{{ overview?.summary?.total_dead ?? 0 }}</div>
              <div class="stat-label">验死号数</div>
            </div>
            <div class="stat-card" :class="overview?.summary?.death_rate >= 0.1 ? 'stat-danger' : overview?.summary?.death_rate >= 0.05 ? 'stat-warn' : 'stat-ok'">
              <div class="stat-num">{{ pct(overview?.summary?.death_rate || 0) }}</div>
              <div class="stat-label">总体死亡率</div>
            </div>
            <div class="stat-card" :class="{ 'stat-warn': (overview?.worst?.length || 0) > 0 }">
              <div class="stat-num">{{ overview?.worst?.length ?? 0 }}</div>
              <div class="stat-label">问题代理</div>
            </div>
          </div>

          <!-- 按国家死亡率条（核心视角：哪国出口在杀号） -->
          <div v-if="overview?.by_country?.length" class="country-bar">
            <span class="country-bar-title">按国家死亡率：</span>
            <span
              v-for="c in overview.by_country"
              :key="c.country"
              class="country-chip"
              :class="c.blacklisted ? 'chip-black' : c.rate >= 0.25 ? 'chip-bad' : c.rate >= 0.1 ? 'chip-warn' : 'chip-ok'"
              :title="`${c.country}：注册 ${c.total} 死 ${c.dead}${c.blacklisted ? '（已拉黑，注册自动换国）' : '（点击拉黑该出口）'}`"
              @click="toggleCountryBlacklist(c)"
            >
              {{ c.country }} {{ pct(c.rate) }} <small>{{ c.dead }}/{{ c.total }}</small>{{ c.blacklisted ? ' 🚫' : '' }}
            </span>
          </div>

          <!-- 双栏：问题出口榜 + 最近死亡号 -->
          <div class="overview-columns">
            <div class="overview-col">
              <div class="col-title">⚠️ 问题出口 TOP（模板×国家，按死亡率）</div>
              <div v-if="!overview?.worst?.length" class="col-empty">暂无死亡记录，代理池很干净 🎉</div>
              <div v-for="w in overview?.worst || []" :key="w.template + w.country" class="worst-row">
                <span class="country-badge">{{ w.country || '?' }}</span>
                <span class="mono worst-proxy" :title="w.template">{{ maskProxy(w.template) }}</span>
                <span class="health-pill" :class="w.blacklisted ? 'health-black' : (w.dead / w.total >= 0.25 ? 'health-bad' : 'health-warn')">
                  {{ w.dead }}/{{ w.total }}
                </span>
                <span v-if="w.blacklisted" class="worst-flag">🚫{{ w.blacklist_reason }}</span>
                <el-button
                  v-else
                  size="small" text type="danger"
                  class="worst-bl-btn"
                  @click="blacklistCombo(w)"
                >拉黑</el-button>
                <span class="worst-time">{{ fmtAgo(w.last_dead) }}</span>
              </div>
            </div>
            <div class="overview-col">
              <div class="col-title">💀 最近验死号（banned / 凭证失效）</div>
              <div v-if="!overview?.recent_dead?.length" class="col-empty">暂无记录 —— 新注册的号验活后这里会出现</div>
              <div v-for="d in overview?.recent_dead || []" :key="d.email" class="dead-row">
                <span class="mono dead-email">{{ d.email }}</span>
                <el-tag :type="d.status === 'banned' ? 'danger' : 'warning'" size="small" effect="plain">
                  {{ d.status === 'banned' ? '封号' : '失效' }}
                </el-tag>
                <span class="dead-country">{{ d.country || '—' }}</span>
                <span class="dead-proxy" :title="d.proxy">{{ d.proxy ? maskProxy(d.proxy) : '老号无存档' }}</span>
                <span class="worst-time">{{ fmtAgo(d.ts) }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 主体双栏布局 -->
      <div class="macos-split-container">
        <!-- 左栏：代理快速录入/编辑器 -->
        <div class="form-pane">
          <div class="pane-inner">
            <div class="pane-section-title">
              <el-icon><DocumentAdd /></el-icon>批量录入与协议设置
            </div>

            <!-- 🌐 全局首选代理协议切换卡片（HTTP / SOCKS5H） -->
            <div class="proto-switch-card">
              <div class="proto-row-top">
                <span class="proto-label">⚙️ 本机首选代理协议:</span>
                <el-tag size="small" :type="protocol === 'http' ? 'success' : 'primary'" effect="dark" round>
                  {{ protocol === 'http' ? '🌐 HTTP 模式' : '⚡ SOCKS5H 模式' }}
                </el-tag>
              </div>
              <el-radio-group
                v-model="protocol"
                size="small"
                class="proto-radio-group"
                @change="handleProtocolChange"
              >
                <el-radio-button value="socks5h">⚡ SOCKS5H 协议 (远端DNS解析)</el-radio-button>
                <el-radio-button value="http">🌐 HTTP 协议 (兼容家用/机房代理)</el-radio-button>
              </el-radio-group>
              <div class="proto-tip">
                <span v-if="protocol === 'http'">
                  💡 <b>已启用 HTTP 模式</b>：录入裸代理自动补全为 <code>http://</code>，当前全部代理节点均自动作为 HTTP 代理发送请求，完美适配家用代理工具。
                </span>
                <span v-else>
                  💡 <b>已启用 SOCKS5H 模式</b>：DNS 解析由代理端完成，杜绝本地 DNS 污染与泄露。
                </span>
              </div>
            </div>

            <div class="hint-card">
              <p class="hint-text">
                每行一条：<span class="mono">[协议://][user:pass@]host:port</span><br />
                支持输入裸地址，系统会自动按所选【{{ protocol === 'http' ? 'HTTP' : 'SOCKS5H' }}】协议前缀补全。
              </p>
            </div>

            <el-input
              v-model="draft"
              type="textarea"
              :rows="10"
              class="mono proxy-textarea"
              :placeholder="protocol === 'http'
                ? 'http://127.0.0.1:7890\nhttp://user:pass@1.2.3.4:8080\n5.6.7.8:8080 (将自动按http识别)'
                : 'socks5h://127.0.0.1:7890\nsocks5h://user:pass@1.2.3.4:1080\nhttp://5.6.7.8:8080'"
            />

            <div class="editor-action-bar">
              <el-button type="primary" class="macos-btn" @click="save">
                覆盖保存
              </el-button>
              <el-button class="macos-btn" @click="append">
                <el-icon><Plus /></el-icon>追加合并
              </el-button>
              <el-button class="macos-btn" @click="editInDraft">
                载入当前池
              </el-button>
              <el-button class="macos-btn btn-convert" @click="convertCurrentPool" title="将当前列表所有代理强制转为当前选定协议">
                一键转为 {{ protocol === 'http' ? 'HTTP' : 'SOCKS5H' }}
              </el-button>
            </div>
          </div>
        </div>

        <!-- 右栏：代理池列表与连通性测试表格 -->
        <div class="table-pane">
          <div class="table-toolbar">
            <div class="toolbar-info">
              <span class="toolbar-title">节点列表 ({{ count }})</span>
            </div>
            <div class="toolbar-actions">
              <el-button
                size="small"
                type="primary"
                plain
                :loading="testingAll"
                :disabled="!count"
                @click="testAll"
              >
                <el-icon><Connection /></el-icon>测试全部连通性
              </el-button>
              <el-button
                size="small"
                :disabled="!count"
                @click="copyText(proxyStore.text, '代理池已复制')"
              >
                <el-icon><CopyDocument /></el-icon>复制全部
              </el-button>
              <el-button
                size="small"
                type="danger"
                plain
                :disabled="!count"
                @click="clearAll"
              >
                <el-icon><Delete /></el-icon>清空
              </el-button>
            </div>
          </div>

          <div class="table-content-box">
            <el-table :data="rows" size="small" stripe height="100%" class="macos-table">
              <el-table-column prop="index" label="#" width="48" align="center" />
              <el-table-column prop="proxy" label="代理地址" min-width="210" show-overflow-tooltip>
                <template #default="{ row }">
                  <span class="mono" style="font-size: 11.5px">{{ row.proxy }}</span>
                </template>
              </el-table-column>
              <el-table-column label="协议" width="90" align="center">
                <template #default="{ row }">
                  <el-tag size="small" effect="plain" class="macos-tag">
                    {{ proxyScheme(row.proxy) }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="连通性 / 延迟" min-width="150">
                <template #default="{ row }">
                  <span v-if="!row.result" class="hint">未测试</span>
                  <el-tag v-else-if="row.result.status === 'testing'" type="warning" size="small">
                    测试中...
                  </el-tag>
                  <div v-else-if="row.result.status === 'ok'" class="latency-cell">
                    <el-tag type="success" size="small">
                      {{ row.result.latency_ms }}ms
                    </el-tag>
                    <span v-if="row.result.ip" class="mono ip-text">{{ row.result.ip }}</span>
                  </div>
                  <el-tooltip v-else :content="row.result.error || '连接失败'" placement="top">
                    <el-tag type="danger" size="small">连接失败</el-tag>
                  </el-tooltip>
                </template>
              </el-table-column>
              <el-table-column label="健康度" width="120" align="center">
                <template #default="{ row }">
                  <el-tooltip
                    :content="`该代理注册成功 ${row.health?.total || 0} 个号，其中 ${row.health?.dead || 0} 个事后被验死（banned/凭证失效）${row.health?.blacklisted ? '，已拉黑：批量注册自动跳过' : ''}`"
                    placement="top"
                  >
                    <span class="health-pill" :class="healthClass(row.health)">
                      {{ row.health?.blacklisted ? '🚫 ' : '' }}{{ healthText(row.health) }}
                    </span>
                  </el-tooltip>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="170" align="center" fixed="right">
                <template #default="{ row }">
                  <el-button
                    size="small"
                    text
                    type="primary"
                    :loading="row.result && row.result.status === 'testing'"
                    @click="testOne(row.proxy)"
                  >
                    测试
                  </el-button>
                  <el-button
                    size="small"
                    text
                    :type="row.health?.blacklisted ? 'success' : 'warning'"
                    @click="toggleBlacklist(row)"
                  >
                    {{ row.health?.blacklisted ? '恢复' : '拉黑' }}
                  </el-button>
                  <el-button
                    size="small"
                    text
                    type="danger"
                    @click="proxyStore.remove(row.proxy)"
                  >
                    删除
                  </el-button>
                </template>
              </el-table-column>
              <template #empty>
                <el-empty description="代理池暂无节点，在左侧粘贴代理保存" :image-size="60" />
              </template>
            </el-table>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.proxypool-page {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* 健康度徽章：死/总。绿=没死号，灰=无记录，黄=死亡率≥10%，红=≥25%该拉黑，黑=已拉黑 */
.health-pill {
  display: inline-block;
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
  padding: 4px 7px;
  border-radius: 5px;
  white-space: nowrap;
  cursor: default;
}
.health-ok    { color: #34d399; background: rgba(52, 211, 153, 0.12); }
.health-none  { color: #94a3b8; background: rgba(148, 163, 184, 0.12); }
.health-warn  { color: #fbbf24; background: rgba(251, 191, 36, 0.14); }
.health-bad   { color: #f87171; background: rgba(248, 113, 113, 0.14); }
.health-black { color: #f87171; background: rgba(248, 113, 113, 0.2); border: 1px dashed rgba(248, 113, 113, 0.5); }

/* ── 健康度总览面板 ── */
.health-overview-panel {
  flex: none;
  margin: 8px 10px 0;
  border: 1px solid rgba(120, 130, 150, 0.18);
  border-radius: 10px;
  background: rgba(128, 140, 160, 0.05);
  overflow: hidden;
}
.overview-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  cursor: pointer;
  user-select: none;
}
.overview-title {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 13px;
  font-weight: 700;
}
.overview-sub {
  font-size: 11px;
  font-weight: 400;
  color: #94a3b8;
}
.overview-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.overview-toggle {
  font-size: 11px;
  color: #94a3b8;
}
.overview-body {
  padding: 0 12px 12px;
}
.stat-cards {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 8px;
  margin-bottom: 10px;
}
.stat-card {
  text-align: center;
  padding: 8px 4px;
  border-radius: 8px;
  background: rgba(128, 140, 160, 0.08);
  border: 1px solid rgba(120, 130, 150, 0.12);
}
.stat-num {
  font-size: 20px;
  font-weight: 800;
  line-height: 1.2;
}
.stat-label {
  font-size: 11px;
  color: #94a3b8;
  margin-top: 2px;
}
.stat-ok      { border-color: rgba(52, 211, 153, 0.3); }
.stat-ok .stat-num    { color: #34d399; }
.stat-warn    { border-color: rgba(251, 191, 36, 0.35); }
.stat-warn .stat-num  { color: #fbbf24; }
.stat-danger  { border-color: rgba(248, 113, 113, 0.4); }
.stat-danger .stat-num { color: #f87171; }

.overview-columns {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

/* 按国家死亡率 chip 条 */
.country-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-bottom: 10px;
}
.country-bar-title {
  font-size: 11.5px;
  color: #94a3b8;
}
.country-chip {
  font-size: 11px;
  font-weight: 700;
  padding: 3px 8px;
  border-radius: 10px;
  cursor: pointer;
  user-select: none;
  white-space: nowrap;
}
.country-chip small {
  font-weight: 500;
  opacity: 0.75;
}
.chip-ok   { color: #34d399; background: rgba(52, 211, 153, 0.12); }
.chip-warn { color: #fbbf24; background: rgba(251, 191, 36, 0.15); }
.chip-bad  { color: #f87171; background: rgba(248, 113, 113, 0.15); }
.chip-black { color: #f87171; background: rgba(248, 113, 113, 0.22); border: 1px dashed rgba(248, 113, 113, 0.5); }
.country-badge {
  flex: none;
  font-size: 10.5px;
  font-weight: 800;
  padding: 2px 6px;
  border-radius: 4px;
  background: rgba(96, 165, 250, 0.15);
  color: #60a5fa;
}
.worst-bl-btn {
  padding: 2px 6px;
  font-size: 10.5px;
}
.overview-col {
  min-width: 0;
  padding: 8px 10px;
  border-radius: 8px;
  background: rgba(128, 140, 160, 0.06);
}
.col-title {
  font-size: 12px;
  font-weight: 700;
  margin-bottom: 6px;
}
.col-empty {
  font-size: 11.5px;
  color: #94a3b8;
  padding: 6px 0;
}
.worst-row, .dead-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 0;
  font-size: 11.5px;
  border-top: 1px dashed rgba(120, 130, 150, 0.14);
}
.worst-row:first-of-type, .dead-row:first-of-type {
  border-top: none;
}
.worst-proxy {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11px;
}
.worst-flag {
  color: #f87171;
  font-size: 10.5px;
  white-space: nowrap;
}
.worst-time {
  flex: none;
  color: #94a3b8;
  font-size: 10.5px;
  margin-left: auto;
}
.dead-email {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11px;
}
.dead-country {
  flex: none;
  font-weight: 700;
  font-size: 10.5px;
}
.dead-proxy {
  flex: none;
  max-width: 130px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #94a3b8;
  font-size: 10.5px;
}
@media (max-width: 1100px) {
  .stat-cards { grid-template-columns: repeat(3, 1fr); }
  .overview-columns { grid-template-columns: 1fr; }
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
.header-badge.badge-err {
  color: var(--apple-red);
  background: rgba(255, 59, 48, 0.1);
}

.macos-split-container {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 420px 1fr;
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

.proto-switch-card {
  background: rgba(14, 165, 233, 0.06);
  border: 1px solid rgba(14, 165, 233, 0.25);
  border-radius: 8px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.proto-row-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.proto-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--app-title);
}
.proto-radio-group {
  width: 100%;
}
.proto-radio-group :deep(.el-radio-button) {
  flex: 1;
}
.proto-radio-group :deep(.el-radio-button__inner) {
  width: 100%;
  font-size: 11.5px;
  padding: 7px 10px;
}
.proto-tip {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  line-height: 1.45;
}
.proto-tip code {
  font-family: monospace;
  color: var(--el-color-primary);
  background: rgba(14, 165, 233, 0.1);
  padding: 1px 4px;
  border-radius: 3px;
}

.hint-card {
  background: var(--el-fill-color-light);
  border: 1px solid var(--app-border);
  border-radius: 8px;
  padding: 10px 12px;
}
.hint-text {
  margin: 0;
  font-size: 11px;
  color: var(--el-text-color-secondary);
  line-height: 1.5;
}

.proxy-textarea {
  font-size: 11.5px;
}

.editor-action-bar {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.table-pane {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--app-window-bg);
}

.table-toolbar {
  padding: 10px 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--app-border);
  background: var(--el-fill-color-blank);
  flex-shrink: 0;
}
.toolbar-title {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--app-title);
}
.toolbar-actions {
  display: flex;
  gap: 6px;
}

.table-content-box {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.latency-cell {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.ip-text {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

@media (max-width: 900px) {
  .macos-split-container {
    grid-template-columns: 1fr;
  }
}
</style>
