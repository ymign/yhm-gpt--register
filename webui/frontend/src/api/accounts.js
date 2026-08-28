import http from './request'

// ──────────────── 统计 ────────────────
export const getStats = () => http.get('/api/stats')

// ──────────────── 号池 accounts ────────────────
// kind = 邮箱来源（outlook / ...）。留空后端会按段数猜，
// 但 Outlook 和 Gmail 都是 4 段猜不出来，所以页面上必选。
export const importAccounts = (text, kind = '') =>
  http.post('/api/import', { text, kind }, { timeout: 300000 })

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
