import http from './request'

// 获取提链全局配置
export const getExtractConfig = () => http.get('/api/extract/config')

// 保存提链全局配置
export const saveExtractConfig = (payload) => http.post('/api/extract/config', payload)

// 查询 CDK 剩余次数与状态
export const queryExtractCdk = (params) => http.get('/api/extract/cdk', { params })

// 启动批量提链任务
export const startExtract = (payload) => http.post('/api/extract/start', payload)

// 停止提链任务
export const stopExtract = (taskId) =>
  http.post(`/api/extract/${encodeURIComponent(taskId)}/stop`)

// SSE 实时推流 URL
export const extractStreamUrl = (taskId) =>
  `/api/extract/${encodeURIComponent(taskId)}/stream`

// 获取单账号提链日志
export const getExtractLog = (taskId, email) =>
  http.get(`/api/extract/${encodeURIComponent(taskId)}/log`, { params: { email } })

// 导出提链成功链接
export const exportExtractLinks = (params) =>
  http.get('/api/extract/export_links', { params, responseType: 'blob' })

// ──────────────── 本地原生全渠道提炼任务台 ────────────────
export const startNativeExtractTask = (payload) =>
  http.post('/api/extract/task/start', payload)

export const stopNativeExtractTask = (taskId) =>
  http.post(`/api/extract/task/${encodeURIComponent(taskId)}/stop`)

export const nativeExtractStreamUrl = (taskId) =>
  `/api/extract/task/${encodeURIComponent(taskId)}/stream`

export const getNativeExtractTaskLog = (taskId, email) =>
  http.get(`/api/extract/task/${encodeURIComponent(taskId)}/log`, { params: { email } })

