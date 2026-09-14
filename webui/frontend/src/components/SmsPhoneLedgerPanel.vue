<script setup>
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh, Search, CopyDocument } from '@element-plus/icons-vue'
import { listSmsPhoneLedger } from '@/api/register'
import { copyText, fmtTime } from '@/api/request'
import { COUNTRY_NAME_MAP } from '@/stores/form'

const props = defineProps({
  compact: { type: Boolean, default: false },
  autoRefresh: { type: Boolean, default: true },
})

const loading = ref(false)
const rows = ref([])
const total = ref(0)
const counts = ref({ all: 0, rejected: 0, used: 0, used_rate: 0 })
const byCountry = ref([])
const byReason = ref([])
const outcome = ref('')
const country = ref('')
const reason = ref('')
const hours = ref(0)
const q = ref('')
const page = ref(1)
const pageSize = ref(40)
const countrySort = ref('n')
let timer = 0
let searchTimer = 0

const OUTCOME_META = {
  rejected_openai: { type: 'danger', text: '拒号' },
  rejected: { type: 'danger', text: '拒号' },
  already_in_use: { type: 'danger', text: '占用' },
  used_success: { type: 'success', text: '已用' },
  success: { type: 'success', text: '已用' },
  skipped_unsubmitted: { type: 'info', text: '跳过' },
  timeout_no_sms: { type: 'warning', text: '无码' },
  cancelled: { type: 'info', text: '取消' },
  session_expired: { type: 'warning', text: '过期' },
}

const REASON_CN = {
  suspicious: '可疑号',
  already_in_use: '已被占用',
  invalid_number: '无效号',
  no_sms: '未出码',
  timeout: '超时',
}

const HOUR_OPTS = [
  { v: 1, l: '1 小时' },
  { v: 6, l: '6 小时' },
  { v: 24, l: '今天' },
  { v: 0, l: '有效期内' },
]

function countryMeta(code) {
  const c = String(code || '').trim().toUpperCase()
  const info = COUNTRY_NAME_MAP[c]
  return {
    code: c || '—',
    name: info?.name || c || '未知',
    flag: info?.flag || '🌐',
  }
}

function outcomeMeta(st) {
  return OUTCOME_META[String(st || '').toLowerCase()] || { type: 'info', text: st || '—' }
}

function reasonLabel(raw) {
  const k = String(raw || '').trim()
  if (!k) return '无原因'
  return REASON_CN[k.toLowerCase()] || k
}

const REJECTED = new Set(['rejected_openai', 'rejected', 'already_in_use'])
const USED = new Set(['used_success', 'success'])

const statsSource = ref('')
const statsBusy = ref(false)
let fallbackAt = 0
let fallbackBusy = false

function ratePct(part, whole) {
  if (!whole) return 0
  return Math.round((1000 * part) / whole) / 10
}

function aggregateItems(items) {
  const cmap = new Map()
  const rmap = new Map()
  for (const it of items || []) {
    const c = String(it.country || '').trim().toLowerCase()
    const rec = cmap.get(c) || { country: c, n: 0, rejected: 0, used: 0 }
    rec.n += 1
    const o = String(it.outcome || '').toLowerCase()
    if (REJECTED.has(o)) rec.rejected += 1
    if (USED.has(o)) rec.used += 1
    cmap.set(c, rec)
    if (REJECTED.has(o)) {
      const why = String(it.reject_reason || '').trim().toLowerCase()
      rmap.set(why, (rmap.get(why) || 0) + 1)
    }
  }
  const by_country = [...cmap.values()].map((r) => {
    const judged = r.rejected + r.used
    return {
      ...r,
      reject_rate: ratePct(r.rejected, judged),
      used_rate: ratePct(r.used, judged),
    }
  })
  const by_reason = [...rmap.entries()]
    .map(([reasonKey, n]) => ({ reason: reasonKey, n }))
    .sort((a, b) => b.n - a.n)
  return { by_country, by_reason }
}

function applyCountryStats(byC, byR, source) {
  byCountry.value = Array.isArray(byC) ? byC : []
  byReason.value = Array.isArray(byR) ? byR : []
  statsSource.value = source
}

async function loadStatsFallback() {
  if (fallbackBusy) return
  fallbackBusy = true
  statsBusy.value = true
  try {
    const first = await listSmsPhoneLedger({
      hours: hours.value,
      limit: 2000,
      offset: 0,
    })
    const acc = [...(first.items || [])]
    const pageLen = acc.length
    const cap = Number(first.all || first.total || 0)
    if (pageLen && cap > acc.length) {
      const offsets = []
      for (let o = pageLen; o < cap; o += pageLen) offsets.push(o)
      const chunk = 6
      for (let i = 0; i < offsets.length && i < 30; i += chunk) {
        const rest = await Promise.all(
          offsets.slice(i, i + chunk).map((o) => listSmsPhoneLedger({
            hours: hours.value,
            limit: 2000,
            offset: o,
          })),
        )
        for (const res of rest) acc.push(...(res.items || []))
        if (acc.length >= cap) break
      }
    }
    const agg = aggregateItems(acc)
    applyCountryStats(agg.by_country, agg.by_reason, 'fallback')
    fallbackAt = Date.now()
  } catch (_) {
    /* 明细仍可用，国家条稍后重试 */
  } finally {
    fallbackBusy = false
    statsBusy.value = false
  }
}

function ingestStats(res, silent) {
  const apiCountries = res.by_country || res.byCountry
  const apiReasons = res.by_reason || res.byReason
  if (Array.isArray(apiCountries) && apiCountries.length) {
    applyCountryStats(apiCountries, apiReasons || [], 'api')
    return
  }
  const partial = aggregateItems(res.items || [])
  if (!byCountry.value.length && partial.by_country.length) {
    applyCountryStats(partial.by_country, partial.by_reason, 'page')
  } else if (Array.isArray(apiReasons) && apiReasons.length) {
    byReason.value = apiReasons
  }
  const all = Number(res.all || res.total || 0)
  const needFull = all > (res.items || []).length
  if (!needFull) {
    if (partial.by_country.length) applyCountryStats(partial.by_country, partial.by_reason, 'page')
    return
  }
  if (silent && byCountry.value.length && Date.now() - fallbackAt < 90000) return
  void loadStatsFallback()
}

const kpis = computed(() => {
  const all = counts.value.all || 0
  const rejected = counts.value.rejected || 0
  const used = counts.value.used || 0
  const judged = rejected + used
  const hit = judged ? ratePct(used, judged).toFixed(1) : '0.0'
  const rej = judged ? ratePct(rejected, judged).toFixed(1) : '0.0'
  return [
    { key: '', label: '有效', value: all, tone: 'plain' },
    { key: 'rejected', label: '拒号', value: rejected, sub: `${rej}%`, tone: 'danger' },
    { key: 'used', label: '已接通', value: used, sub: judged ? `${hit}%` : '', tone: 'ok' },
    { key: '_rate', label: '接通率', value: `${hit}%`, tone: 'accent', readonly: true },
  ]
})

const countryRows = computed(() => {
  const list = [...(byCountry.value || [])]
  const sum = list.reduce((s, r) => s + (Number(r.n) || 0), 0) || 1
  const decorated = list.map((r) => {
    const n = Number(r.n) || 0
    const rejected = Number(r.rejected) || 0
    const used = Number(r.used) || 0
    const judged = rejected + used
    return {
      ...r,
      n,
      rejected,
      used,
      share: ratePct(n, sum),
      reject_rate: r.reject_rate != null ? Number(r.reject_rate) : ratePct(rejected, judged),
      used_rate: r.used_rate != null ? Number(r.used_rate) : ratePct(used, judged),
    }
  })
  if (countrySort.value === 'reject') {
    decorated.sort((a, b) => b.reject_rate - a.reject_rate || b.n - a.n)
  } else if (countrySort.value === 'used') {
    decorated.sort((a, b) => b.used_rate - a.used_rate || b.used - a.used)
  } else if (countrySort.value === 'share') {
    decorated.sort((a, b) => b.share - a.share || b.n - a.n)
  } else {
    decorated.sort((a, b) => b.n - a.n)
  }
  return decorated
})

const insight = computed(() => {
  const list = countryRows.value
  if (!list.length) {
    if (statsBusy.value) return '正在从明细汇总各国接通率 / 拒号率 / 占比…'
    if ((counts.value.all || 0) > 0) return '明细已有数据，国家统计汇总中。'
    return '还没有有效台账。租号并提交 OpenAI 后会出现在这里。'
  }
  const worst = [...list].sort((a, b) => b.reject_rate - a.reject_rate || b.rejected - a.rejected)[0]
  const best = [...list].filter((r) => r.used > 0).sort((a, b) => b.used_rate - a.used_rate)[0]
  const wm = countryMeta(worst?.country)
  const bm = best ? countryMeta(best.country) : null
  const bits = []
  if (worst && worst.rejected > 0) {
    bits.push(`${wm.flag}${wm.name} 拒号率 ${worst.reject_rate}%（${worst.rejected}/${worst.n}）`)
  }
  if (bm) bits.push(`${bm.flag}${bm.name} 接通率 ${best.used_rate}%（已用 ${best.used}）`)
  const topReason = (byReason.value || [])[0]
  if (topReason?.n) bits.push(`主因 ${reasonLabel(topReason.reason)} ×${topReason.n}`)
  if (statsSource.value === 'fallback' || statsSource.value === 'page') bits.push('各国比率已按明细汇总')
  return bits.join('  ·  ')
})

async function load(silent = false) {
  if (!silent) loading.value = true
  try {
    const res = await listSmsPhoneLedger({
      outcome: outcome.value,
      country: country.value,
      reason: reason.value,
      q: q.value,
      hours: hours.value,
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
    })
    rows.value = res.items || []
    total.value = Number(res.total || 0)
    counts.value = {
      all: Number(res.all || 0),
      rejected: Number(res.rejected || 0),
      used: Number(res.used || 0),
      used_rate: Number(res.used_rate || 0),
    }
    ingestStats(res, silent)
  } catch (e) {
    if (!silent) ElMessage.error(e.message || '加载台账失败')
  } finally {
    if (!silent) loading.value = false
  }
}

function resetPageLoad() {
  page.value = 1
  load()
}

function setOutcome(v) {
  if (v === '_rate') return
  outcome.value = outcome.value === v ? '' : v
  resetPageLoad()
}

function toggleCountry(code) {
  const c = String(code || '').toLowerCase()
  country.value = country.value === c ? '' : c
  resetPageLoad()
}

function toggleReason(raw) {
  const k = String(raw || '')
  reason.value = reason.value === k ? '' : k
  resetPageLoad()
}

function copyRow(row) {
  copyText([row.phone_masked, countryMeta(row.country).name, row.reject_reason || row.outcome].filter(Boolean).join(' · '), '已复制')
}

function stopTimer() {
  if (timer) window.clearInterval(timer)
  timer = 0
}
function startTimer() {
  if (!props.autoRefresh || timer) return
  timer = window.setInterval(() => load(true), 8000)
}

watch(pageSize, resetPageLoad)
watch(hours, () => {
  fallbackAt = 0
  byCountry.value = []
  byReason.value = []
  statsSource.value = ''
  resetPageLoad()
})
watch(page, () => load(true))
watch(q, () => {
  if (searchTimer) window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(() => resetPageLoad(), 280)
})

onMounted(() => {
  load()
  startTimer()
})
onActivated(() => {
  load(true)
  startTimer()
})
onDeactivated(stopTimer)
onUnmounted(() => {
  stopTimer()
  if (searchTimer) window.clearTimeout(searchTimer)
})

defineExpose({ load })
</script>

<template>
  <div class="ledger-panel" :class="{ 'is-compact': compact }">
    <div class="kpi-strip">
      <button
        v-for="k in kpis"
        :key="k.key"
        class="kpi-pill"
        :class="[`tone-${k.tone}`, { 'is-on': !k.readonly && outcome === k.key }]"
        :disabled="k.readonly"
        @click="setOutcome(k.key)"
      >
        <span class="kpi-label">{{ k.label }}</span>
        <strong class="kpi-value">{{ k.value }}</strong>
        <em v-if="k.sub" class="kpi-sub">{{ k.sub }}</em>
      </button>
    </div>

    <p class="insight" :title="insight">{{ insight }}</p>

    <div class="chip-band">
      <div class="band-head">
        <span>各国接通率 / 拒号率 / 占比</span>
        <div class="sort-seg">
          <button :class="{ on: countrySort === 'n' }" @click="countrySort = 'n'">按量</button>
          <button :class="{ on: countrySort === 'share' }" @click="countrySort = 'share'">使用率</button>
          <button :class="{ on: countrySort === 'used' }" @click="countrySort = 'used'">接通率</button>
          <button :class="{ on: countrySort === 'reject' }" @click="countrySort = 'reject'">拒号率</button>
        </div>
      </div>
      <div class="chip-scroll">
        <button
          v-for="r in countryRows"
          :key="r.country || '_'"
          class="nation-chip"
          :class="{
            on: country === String(r.country || '').toLowerCase(),
            'is-hot': r.used_rate >= 8,
            'is-dead': r.reject_rate >= 95 && r.n >= 20,
          }"
          :title="`${countryMeta(r.country).name} 接通率 ${r.used_rate}% · 拒号率 ${r.reject_rate}% · 使用率 ${r.share}% · 已用 ${r.used} / 共 ${r.n}`"
          @click="toggleCountry(r.country)"
        >
          <span class="n-id">
            <span class="n-flag">{{ countryMeta(r.country).flag }}</span>
            <span class="n-name">{{ countryMeta(r.country).name }}</span>
            <span class="n-share">占 {{ r.share }}%</span>
          </span>
          <span class="n-metrics">
            <span class="ok">通 {{ r.used_rate }}%</span>
            <span class="rej">拒 {{ r.reject_rate }}%</span>
            <span class="vol">{{ r.used }}/{{ r.n }}</span>
          </span>
        </button>
        <span v-if="!countryRows.length" class="empty-mini">{{ statsBusy ? '正在汇总各国接通率…' : '暂无国家统计' }}</span>
      </div>
      <div v-if="byReason.length" class="chip-scroll reasons">
        <button
          v-for="r in byReason"
          :key="r.reason || '_none'"
          class="reason-chip"
          :class="{ on: reason === r.reason }"
          @click="toggleReason(r.reason)"
        >
          <span>{{ reasonLabel(r.reason) }}</span>
          <em>{{ r.n }}</em>
        </button>
      </div>
    </div>

    <div class="toolbar">
      <el-input
        v-model="q"
        clearable
        size="small"
        placeholder="搜国家、原因、档位、邮箱"
        :prefix-icon="Search"
        class="search"
      />
      <div class="hour-seg">
        <button
          v-for="h in HOUR_OPTS"
          :key="h.v"
          :class="{ on: hours === h.v }"
          @click="hours = h.v"
        >{{ h.l }}</button>
      </div>
      <el-button size="small" class="refresh-btn" @click="load()">
        <el-icon><Refresh /></el-icon>刷新
      </el-button>
    </div>

    <div class="table-wrap">
      <el-table v-loading="loading" :data="rows" size="small" height="100%" class="ledger-table">
        <el-table-column label="国家" width="132">
          <template #default="{ row }">
            <button class="country-cell" type="button" @click="toggleCountry(row.country)">
              <span>{{ countryMeta(row.country).flag }}</span>
              <span>{{ countryMeta(row.country).name }}</span>
            </button>
          </template>
        </el-table-column>
        <el-table-column label="号码" min-width="148">
          <template #default="{ row }">
            <span class="mono phone" @click="copyText(row.phone_masked, '已复制打码号码')">{{ row.phone_masked || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="档位" width="88" align="center">
          <template #default="{ row }">
            <span class="tier">{{ row.price_tier || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="结果" width="88" align="center">
          <template #default="{ row }">
            <span class="dot-chip" :class="outcomeMeta(row.outcome).type">{{ outcomeMeta(row.outcome).text }}</span>
          </template>
        </el-table-column>
        <el-table-column label="原因" min-width="140">
          <template #default="{ row }">
            <button
              v-if="row.reject_reason"
              class="reason-link"
              type="button"
              @click="toggleReason(String(row.reject_reason || '').trim().toLowerCase())"
            >{{ reasonLabel(row.reject_reason) }}</button>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="时间" width="158" align="center">
          <template #default="{ row }">
            <span class="mono-date">{{ fmtTime(row.created_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column width="44" align="center">
          <template #default="{ row }">
            <el-button text size="small" @click="copyRow(row)">
              <el-icon><CopyDocument /></el-icon>
            </el-button>
          </template>
        </el-table-column>
        <template #empty>
          <div class="empty-full">当前筛选没有记录。换个国家、原因或时间窗口再看。</div>
        </template>
      </el-table>
    </div>

    <div class="pager">
      <span class="pager-tip">本页 {{ rows.length }} / 筛选 {{ total }} · 有效 {{ counts.all }}</span>
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :page-sizes="[20, 40, 80, 160]"
        :total="total"
        layout="sizes, prev, pager, next"
        size="small"
      />
    </div>
  </div>
</template>

<style scoped>
.ledger-panel {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  color: #0f172a;
}
.kpi-strip {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 6px;
  flex: 0 0 auto;
}
.kpi-pill {
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-height: 34px;
  padding: 4px 10px;
  text-align: left;
  border: 1px solid rgba(15, 23, 42, 0.07);
  background: rgba(255, 255, 255, 0.78);
  border-radius: 10px;
  cursor: pointer;
  box-shadow: inset 0 1px 0 #fff;
}
.kpi-pill.is-on {
  border-color: rgba(93, 164, 177, 0.55);
  box-shadow: 0 0 0 2px rgba(93, 164, 177, 0.16);
}
.kpi-pill:disabled { cursor: default; }
.kpi-label {
  font-size: 11px;
  font-weight: 700;
  color: #64748b;
}
.kpi-value {
  font-size: 16px;
  font-weight: 800;
  letter-spacing: -0.03em;
  font-variant-numeric: tabular-nums;
  line-height: 1.1;
}
.kpi-sub {
  font-style: normal;
  font-size: 11px;
  font-weight: 700;
  color: #94a3b8;
  font-variant-numeric: tabular-nums;
}
.tone-danger .kpi-value, .tone-danger .kpi-sub { color: #e11d48; }
.tone-ok .kpi-value, .tone-ok .kpi-sub { color: #0f766e; }
.tone-accent .kpi-value { color: #0e7490; }
.insight {
  margin: 0;
  padding: 4px 10px;
  border-radius: 8px;
  background: rgba(93, 164, 177, 0.1);
  border: 1px solid rgba(93, 164, 177, 0.2);
  font-size: 12px;
  color: #134e4a;
  line-height: 1.35;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 0 0 auto;
}
.chip-band {
  flex: 0 0 auto;
  min-height: 0;
}
.band-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 4px;
  font-size: 11px;
  font-weight: 750;
  color: #334155;
}
.sort-seg, .hour-seg {
  display: inline-flex;
  background: rgba(15, 23, 42, 0.05);
  border-radius: 999px;
  padding: 2px;
}
.sort-seg button, .hour-seg button {
  border: 0;
  background: transparent;
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 999px;
  cursor: pointer;
  color: #64748b;
}
.sort-seg button.on, .hour-seg button.on {
  background: #fff;
  color: #0f172a;
  font-weight: 700;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.08);
}
.chip-scroll {
  display: flex;
  gap: 6px;
  overflow-x: auto;
  overflow-y: hidden;
  padding-bottom: 2px;
  scrollbar-width: thin;
}
.chip-scroll.reasons { margin-top: 4px; }
.nation-chip {
  flex: 0 0 auto;
  display: inline-flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  min-width: 148px;
  padding: 5px 8px 4px;
  border-radius: 10px;
  border: 1px solid rgba(15, 23, 42, 0.08);
  background: #fff;
  cursor: pointer;
  text-align: left;
}
.nation-chip:hover, .nation-chip.on { border-color: rgba(93, 164, 177, 0.55); background: #f0fafb; }
.nation-chip.is-hot { box-shadow: inset 3px 0 0 #0d9488; }
.nation-chip.is-dead { box-shadow: inset 3px 0 0 #e11d48; }
.n-id {
  display: flex;
  align-items: center;
  gap: 4px;
  width: 100%;
}
.n-flag { font-size: 13px; }
.n-name { font-size: 12px; font-weight: 750; }
.n-share {
  margin-left: auto;
  font-size: 10px;
  font-weight: 700;
  color: #64748b;
  font-variant-numeric: tabular-nums;
}
.n-metrics {
  display: flex;
  gap: 7px;
  font-size: 11px;
  font-weight: 750;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.n-metrics .ok { color: #0f766e; }
.n-metrics .rej { color: #e11d48; }
.n-metrics .vol { color: #64748b; }
.reason-chip {
  flex: 0 0 auto;
  border: 1px solid rgba(15, 23, 42, 0.08);
  background: #fff;
  border-radius: 999px;
  padding: 3px 8px;
  font-size: 11px;
  cursor: pointer;
  display: inline-flex;
  gap: 5px;
  align-items: center;
}
.reason-chip em { font-style: normal; color: #64748b; font-variant-numeric: tabular-nums; }
.reason-chip.on {
  border-color: #e11d48;
  background: #fff1f2;
  color: #9f1239;
}
.toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 0 0 auto;
}
.search { flex: 1; max-width: 260px; }
.refresh-btn { margin-left: auto; }
.table-wrap {
  flex: 1 1 auto;
  min-height: 0;
  border: 1px solid rgba(15, 23, 42, 0.06);
  border-radius: 12px;
  overflow: hidden;
  background: rgba(255, 255, 255, 0.5);
  display: flex;
  flex-direction: column;
}
.table-wrap :deep(.el-table) {
  flex: 1 1 auto;
  width: 100%;
}
.country-cell {
  border: 0;
  background: transparent;
  cursor: pointer;
  display: inline-flex;
  gap: 6px;
  align-items: center;
  font-size: 12.5px;
  padding: 0;
}
.phone { cursor: pointer; letter-spacing: 0.2px; }
.phone:hover { color: #0284c7; }
.mono, .mono-date, .tier {
  font-variant-numeric: tabular-nums;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
}
.tier { color: #64748b; }
.dot-chip {
  display: inline-flex;
  min-width: 44px;
  justify-content: center;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 750;
}
.dot-chip.danger { background: #fff1f2; color: #e11d48; }
.dot-chip.success { background: #ecfdf5; color: #0f766e; }
.dot-chip.warning { background: #fffbeb; color: #b45309; }
.dot-chip.info { background: #f1f5f9; color: #475569; }
.reason-link {
  border: 0;
  background: transparent;
  color: #0e7490;
  cursor: pointer;
  padding: 0;
  font-size: 12px;
}
.reason-link:hover { text-decoration: underline; }
.muted { color: #94a3b8; font-size: 12px; }
.empty-mini, .empty-full {
  padding: 8px 12px;
  color: #94a3b8;
  font-size: 12px;
  white-space: nowrap;
}
.pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex: 0 0 auto;
}
.pager-tip { font-size: 12px; color: #64748b; }
:deep(.ledger-table) { --el-table-bg-color: transparent; --el-table-tr-bg-color: transparent; }
:deep(.ledger-table th.el-table__cell) {
  background: rgba(241, 245, 249, 0.8);
  color: #64748b;
  font-size: 11px;
  font-weight: 750;
}
@media (max-width: 900px) {
  .kpi-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .insight { white-space: normal; }
}
</style>
