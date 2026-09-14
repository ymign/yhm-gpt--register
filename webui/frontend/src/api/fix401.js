import http from './request'

export const previewFix401 = (payload) =>
  http.post('/api/fix401/preview', payload)

export const checkFix401Proxy = (proxy) =>
  http.post('/api/fix401/proxy/check', { proxy })

export const startFix401 = (payload) =>
  http.post('/api/fix401/start', payload)

export const stopFix401 = (taskId) =>
  http.post(`/api/fix401/${encodeURIComponent(taskId)}/stop`)

export const getFix401Snapshot = (taskId) =>
  http.get(`/api/fix401/${encodeURIComponent(taskId)}`)

export const fix401StreamUrl = (taskId) =>
  `/api/fix401/${encodeURIComponent(taskId)}/stream`

export const getFix401Log = (taskId, email) =>
  http.get(`/api/fix401/${encodeURIComponent(taskId)}/log`, { params: { email } })

export const downloadFix401Cpa = (taskId, layout = 'txt') =>
  http.get(`/api/fix401/${encodeURIComponent(taskId)}/download_cpa`, {
    params: { layout },
    responseType: 'blob',
  })

export const downloadFix401Sub2 = (taskId, layout = 'bundle') =>
  http.get(`/api/fix401/${encodeURIComponent(taskId)}/download_sub2`, {
    params: { layout },
    responseType: 'blob',
  })
