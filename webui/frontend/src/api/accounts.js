import http from './request'

// ──────────────── 统计与全景概览 ────────────────
export const getStats = () => http.get('/api/stats')
export const getDashboardSummary = () => http.get('/api/dashboard/summary')

// ──────────────── 号池 accounts ────────────────
// kind = 邮箱来源（outlook / ...）。留空后端会按段数猜或全智能嗅探，
// strategy = 导入策略（smart_merge / skip_duplicates / overwrite）
export const importAccounts = (text, kind = '', strategy = 'smart_merge') =>
  http.post('/api/import', { text, kind, strategy }, { timeout: 300000 })

export const analyzeImportAccounts = (text, kind = '') =>
  http.post('/api/accounts/analyze_import', { text, kind }, { timeout: 60000 })

export const listAccounts = (params) =>
  http.get('/api/accounts', { params }) // { status, limit, offset, kind }

export const deleteAccount = (email) =>
  http.delete(`/api/accounts/${encodeURIComponent(email)}`)

export const bulkDeleteAccounts = (payload) =>
  http.post('/api/accounts/bulk_delete', payload) // { status } 或 { emails }

export const resetFailed = () => http.post('/api/accounts/reset_failed')

// 归档 = 只留存不再使用：failed 全部 → archived（退出注册/验活领取队列）
export const archiveFailed = () => http.post('/api/accounts/archive_failed')

// 取消归档：archived 全部 → failed（失败原因保留）
export const unarchiveAccounts = () => http.post('/api/accounts/unarchive')

export const resetAccount = (email) =>
  http.post(`/api/accounts/reset/${encodeURIComponent(email)}`)

export const bulkResetAccounts = (emails) =>
  http.post('/api/accounts/bulk_reset', { emails })

export const releaseStale = () => http.post('/api/accounts/release_stale')

export const exportPoolAccounts = (payload) =>
  http.post('/api/accounts/export', payload)

export const cleanRegisteredFromPool = (mode = 'delete') =>
  http.post(`/api/accounts/clean-registered?mode=${mode}`)

// ──────────────── 邮箱号池快速验活 ────────────────
export const startMailboxValidation = (payload) =>
  http.post('/api/accounts/validate/start', payload)

export const stopMailboxValidation = (taskId) =>
  http.post(`/api/accounts/validate/${taskId}/stop`)

export const mailboxValidateStreamUrl = (taskId) =>
  `/api/accounts/validate/${taskId}/stream`
