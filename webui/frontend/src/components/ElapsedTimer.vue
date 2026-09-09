<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'

const props = defineProps({
  status: { type: String, default: '' },
  startedAt: { type: Number, default: 0 },
  elapsed: { type: Number, default: 0 },
})

const now = ref(Date.now())
let timer = 0

function stop() {
  if (timer) {
    clearInterval(timer)
    timer = 0
  }
}

function start() {
  if (timer) return
  now.value = Date.now()
  timer = window.setInterval(() => {
    now.value = Date.now()
  }, 1000)
}

watch(
  () => props.status,
  (s) => {
    if (s === 'running') start()
    else stop()
  },
  { immediate: true },
)

onUnmounted(stop)

const text = computed(() => {
  if (props.status === 'running') {
    const st = Number(props.startedAt) || now.value / 1000
    return `${Math.max(0, Math.floor(now.value / 1000 - st))}s`
  }
  if (Number(props.elapsed) > 0) return `${Math.floor(Number(props.elapsed))}s`
  return '—'
})
</script>

<template>
  <span
    class="mono hint"
    :style="{ color: status === 'running' ? 'var(--el-color-primary)' : '' }"
  >{{ text }}</span>
</template>
