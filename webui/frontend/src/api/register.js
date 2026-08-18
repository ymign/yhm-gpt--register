import http from './request'

// ──────────────── 单个注册 ────────────────
export const startRegister = (payload) => http.post('/api/register', payload)

// ──────────────── 运行记录 ────────────────
export const listRuns = (limit = 50) => http.get('/api/runs', { params: { limit } })
export const getRunLog = (runId) => http.get(`/api/runs/${encodeURIComponent(runId)}/log`)

// ──────────────── 注册结果 registered ────────────────
export const listRegistered = (params) =>
  http.get('/api/registered', { params }) // { limit, offset, filter }

export const getRegistered = (email) =>
  http.get(`/api/registered/${encodeURIComponent(email)}`)

export const deleteRegistered = (email) =>
  http.delete(`/api/registered/${encodeURIComponent(email)}`)

// 手填凭证：不传的字段后端不动，传空串才是清空
export const updateCredentials = (payload) =>
  http.post('/api/registered/update_credentials', payload)

export const bulkDeleteRegistered = (payload) =>
  http.post('/api/registered/bulk_delete', payload) // { emails } 或 { all: true }

export const cleanInvalidRegistered = () =>
  http.post('/api/registered/clean_invalid')

// 导出后清理用：把号池那一行也删掉。
// 从 accounts.js 转出来一份，省得 Registered.vue 同时 import 两个 api 模块。
export { bulkDeleteAccounts } from './accounts'

// 批量导出：格式清单由后端 export_formats.py 提供，加格式前端不用改
export const listExportFormats = () => http.get('/api/registered/export/formats')
export const exportRegistered = (payload) => http.post('/api/registered/export', payload)

export const checkPlus = (emails, proxy = '') =>
  http.post('/api/registered/check_plus', { emails, proxy })

export const listRegisteredEmails = (filter = 'all') =>
  http.get('/api/registered_emails', { params: { filter } })

// ──────────────── Plus 状态并发检测任务 ────────────────
export const startPlusCheck = (payload) =>
  http.post('/api/registered/plus_check/start', payload) // { emails, proxies, workers, timeout }

export const stopPlusCheck = (taskId) =>
  http.post(`/api/registered/plus_check/${encodeURIComponent(taskId)}/stop`)

export const plusCheckStreamUrl = (taskId) =>
  `/api/registered/plus_check/${encodeURIComponent(taskId)}/stream`

export const getPlusCheckLog = (taskId, email) =>
  http.get(`/api/registered/plus_check/${encodeURIComponent(taskId)}/log`, { params: { email } })

// ──────────────── 账号批量验活 (Token 验活 & 套餐验活) ────────────────
export const startHealthCheck = (payload) =>
  http.post('/api/registered/health_check/start', payload) // { emails, mode, proxies, workers, timeout }

export const stopHealthCheck = (taskId) =>
  http.post(`/api/registered/health_check/${encodeURIComponent(taskId)}/stop`)

export const healthCheckStreamUrl = (taskId) =>
  `/api/registered/health_check/${encodeURIComponent(taskId)}/stream`

export const getHealthCheckLog = (taskId, email) =>
  http.get(`/api/registered/health_check/${encodeURIComponent(taskId)}/log`, { params: { email } })

// ──────────────── OAICS 资格检测 ────────────────
export const startOACheck = (payload) =>
  http.post('/api/registered/oa_check/start', payload) // { emails, proxies, workers, ... }

export const stopOACheck = (taskId) =>
  http.post(`/api/registered/oa_check/${encodeURIComponent(taskId)}/stop`)

export const oaCheckStreamUrl = (taskId) =>
  `/api/registered/oa_check/${encodeURIComponent(taskId)}/stream`

export const getOACheckLog = (taskId, email) =>
  http.get(`/api/registered/oa_check/${encodeURIComponent(taskId)}/log`, { params: { email } })

export const exportToPanel = (email, targets) =>
  http.post('/api/registered/export_to_panel', { email, targets })

// ──────────────── OAuth 导出 / Codex 凭证导出 ────────────────
export const startOAuthExport = (payload) =>
  http.post('/api/registered/oauth_export/start', payload)

export const stopOAuthExport = (taskId) =>
  http.post(`/api/registered/oauth_export/${encodeURIComponent(taskId)}/stop`)

export const oauthExportStreamUrl = (taskId) =>
  `/api/registered/oauth_export/${encodeURIComponent(taskId)}/stream`

export const getOAuthExportLog = (taskId, email) =>
  http.get(`/api/registered/oauth_export/${encodeURIComponent(taskId)}/log`, { params: { email } })

export const downloadOAuthExportCpa = (taskId, emails = '') =>
  http.get(`/api/registered/oauth_export/${encodeURIComponent(taskId)}/download_cpa`, {
    params: { emails },
    responseType: 'blob',
  })

export const downloadOAuthExportSub2 = (taskId, emails = '') =>
  http.get(`/api/registered/oauth_export/${encodeURIComponent(taskId)}/download_sub2`, {
    params: { emails },
    responseType: 'blob',
  })

// ──────────────── Token 重新获取与刷新 (Token Refresh Studio) ────────────────
export const startTokenRefresh = (payload) =>
  http.post('/api/registered/token_refresh/start', payload)

export const stopTokenRefresh = (taskId) =>
  http.post(`/api/registered/token_refresh/${encodeURIComponent(taskId)}/stop`)

export const tokenRefreshStreamUrl = (taskId) =>
  `/api/registered/token_refresh/${encodeURIComponent(taskId)}/stream`

export const getTokenRefreshLog = (taskId, email) =>
  http.get(`/api/registered/token_refresh/${encodeURIComponent(taskId)}/log`, { params: { email } })

export const downloadTokenRefreshExport = (taskId, format = 'txt') =>
  http.get(`/api/registered/token_refresh/${encodeURIComponent(taskId)}/download`, {
    params: { format },
    responseType: 'blob',
  })

// ──────────────── 2FA TOTP 动态码 & 邮箱 OTP & 补密补2FA ────────────────
export const getAccountTotp = (email) =>
  http.get(`/api/registered/${encodeURIComponent(email)}/totp`)

export const fetchMailOtp = (email, payload = {}) =>
  http.post(`/api/registered/${encodeURIComponent(email)}/fetch_otp`, payload)

export const bindAccount2fa = (email, payload = {}) =>
  http.post(`/api/registered/${encodeURIComponent(email)}/bind_2fa`, payload)

export const setAccountPassword = (email, payload = {}) =>
  http.post(`/api/registered/${encodeURIComponent(email)}/set_password`, payload)

export const bulkBind2fa = (payload) =>
  http.post('/api/registered/bulk_bind_2fa', payload)

export const bulkSetPassword = (payload) =>
  http.post('/api/registered/bulk_set_password', payload)


// ──────────────── 自动跑号 auto-loop ────────────────
export const autoStart = (payload) => http.post('/api/auto/start', payload)
export const autoPause = () => http.post('/api/auto/pause')
export const autoResume = () => http.post('/api/auto/resume')
export const autoStop = () => http.post('/api/auto/stop')
export const autoStatus = () => http.get('/api/auto/status')
