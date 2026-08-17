import http from './request'

export const startPayPalPayTask = (payload) =>
  http.post('/api/paypal-pay/task/start', payload)

export const stopPayPalPayTask = (taskId) =>
  http.post(`/api/paypal-pay/task/${encodeURIComponent(taskId)}/stop`)

export const paypalPayStreamUrl = (taskId) =>
  `/api/paypal-pay/task/${encodeURIComponent(taskId)}/stream`

export const getPayPalPayTaskLog = (taskId, key = '') =>
  http.get(`/api/paypal-pay/task/${encodeURIComponent(taskId)}/log`, { params: { key } })

export const submitPayPalPayInput = (taskId, key, value) =>
  http.post(`/api/paypal-pay/task/${encodeURIComponent(taskId)}/input`, { key, value })
