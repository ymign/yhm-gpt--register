import http from './request'

// 代理连通性测试（后端并发测试，可能耗时，单独放宽超时到 3 分钟）
export const testProxies = (proxies, timeout = 8) =>
  http.post('/api/proxy/test', { proxies, timeout }, { timeout: 180000 })

// 代理健康度（死号反哺）：每个代理注册的号数 / 验死数 / 拉黑状态
export const getProxyHealth = () => http.get('/api/proxy_health')

// 健康度总览面板：汇总统计 + 问题代理榜 + 最近死亡号
export const getProxyHealthOverview = () => http.get('/api/proxy_health/overview')

// 手动拉黑 / 取消拉黑：country 传国家码=只拉黑该出口组合；空串=整模板拉黑
export const setProxyBlacklist = (proxy, country = '', on, reason = '') =>
  http.post('/api/proxy_health/blacklist', { proxy, country, on, reason })
