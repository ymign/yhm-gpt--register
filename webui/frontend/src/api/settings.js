import http from './request'

// ──────────────── 邮箱来源配置 ────────────────
// 已注册的 provider 清单（含能力声明和配置项声明）。
// 页面据此动态渲染，后端加一种邮箱，这里和页面都不用改。
export const getMailProviders = (pooledOnly = false) =>
  http.get('/api/mail/providers', { params: { pooled_only: pooledOnly } })

export const getMailConfig = () => http.get('/api/settings/mail')
export const saveMailConfig = (payload) => http.post('/api/settings/mail', payload)
export const testMail = () => http.post('/api/settings/mail/test')
export const fetchCfDomains = (payload) => http.post('/api/mail/cf/domains', payload)
export const fetchRemailProjects = (payload = {}) => http.post('/api/mail/remail/projects', payload)

// ──────────────── SMS 接码配置 ────────────────
export const getSmsConfig = () => http.get('/api/settings/sms')
export const saveSmsConfig = (payload) => http.post('/api/settings/sms', payload)
export const testSms = () => http.post('/api/settings/sms/test')
export const getSmsTopCountries = () => http.get('/api/settings/sms/countries')
export const getSmsAllCountries = (provider = '') =>
  http.get('/api/settings/sms/all_countries', { params: { provider } })
export const getSmsPriceTiers = (country = '6', service = 'dr', provider = '') =>
  http.get('/api/settings/sms/price_tiers', { params: { country, service, provider } })

// ──────────────── 自动导出配置 (CPA / SUB2API) ────────────────
export const getExportConfig = () => http.get('/api/settings/export')
export const saveExportConfig = (payload) => http.post('/api/settings/export', payload)
export const testExport = (target) => http.post('/api/settings/export/test', { target })

// ──────────────── CDK 卡密号池 API ────────────────
export const getSmsCdkPool = (params = {}) => http.get('/api/settings/sms/cdk_pool', { params })
export const getSmsCdkPoolStats = () => http.get('/api/settings/sms/cdk_pool/stats')
export const importSmsCdks = (payload) => http.post('/api/settings/sms/cdk_pool/import', payload)
export const updateSmsCdk = (id, payload) => http.post(`/api/settings/sms/cdk_pool/${id}/update`, payload)
export const deleteSmsCdk = (id) => http.delete(`/api/settings/sms/cdk_pool/${id}`)
export const clearSmsCdkPool = (payload) => http.post('/api/settings/sms/cdk_pool/clear', payload)

