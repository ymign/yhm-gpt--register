<script setup>
import { nextTick, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { CopyDocument, Delete } from '@element-plus/icons-vue'
import { useRuntimeStore } from '@/stores/runtime'
import { copyText } from '@/api/request'

const runtime = useRuntimeStore()
const { logs } = storeToRefs(runtime)
const boxRef = ref(null)

// 新日志自动滚到底
watch(
  () => logs.value.length,
  async () => {
    await nextTick()
    const el = boxRef.value
    if (el) el.scrollTop = el.scrollHeight
  },
)

function copyAllLogs() {
  const text = logs.value.map((l) => l.text).join('\n')
  copyText(text, '全部日志已复制')
}
</script>

<template>
  <div class="macos-terminal-card">
    <div class="terminal-titlebar">
      <div class="titlebar-left">
        <div class="mac-traffic-lights">
          <span class="traffic-dot red"></span>
          <span class="traffic-dot yellow"></span>
          <span class="traffic-dot green"></span>
        </div>
        <span class="terminal-title">Console · 实时运行终端</span>
      </div>
      <div class="titlebar-right">
        <el-button size="small" text class="terminal-action-btn" @click="copyAllLogs">
          <el-icon><CopyDocument /></el-icon>复制
        </el-button>
        <el-button size="small" text class="terminal-action-btn" @click="runtime.clearLogs">
          <el-icon><Delete /></el-icon>清空
        </el-button>
      </div>
    </div>
    <div ref="boxRef" class="terminal-viewport">
      <div
        v-for="l in logs"
        :key="l.id"
        class="terminal-line"
        :class="l.kind"
      >
        {{ l.text }}
      </div>
      <div v-if="!logs.length" class="terminal-placeholder">
        <span>$ 等待任务执行与日志输出...</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.macos-terminal-card {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--app-log-bg);
  border: 1px solid var(--app-log-border);
  border-radius: 12px;
  overflow: hidden;
  box-shadow: var(--app-shadow-md);
}

.terminal-titlebar {
  padding: 8px 14px;
  background: rgba(255, 255, 255, 0.04);
  border-bottom: 1px solid var(--app-log-border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  user-select: none;
}

.titlebar-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.terminal-title {
  font-size: 11.5px;
  font-weight: 600;
  color: #94a3b8;
  letter-spacing: 0.2px;
}

.titlebar-right {
  display: flex;
  gap: 4px;
}

.terminal-action-btn {
  color: #94a3b8 !important;
  font-size: 11px !important;
  padding: 2px 6px !important;
}
.terminal-action-btn:hover {
  color: #f1f5f9 !important;
  background: rgba(255, 255, 255, 0.08) !important;
}

.terminal-viewport {
  flex: 1;
  min-height: 280px;
  padding: 12px 14px;
  overflow-y: auto;
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, Courier, monospace;
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--app-log-text);
  word-break: break-all;
  white-space: pre-wrap;
}

.terminal-line {
  margin-bottom: 2px;
}
.terminal-line.err { color: #f87171; font-weight: 500; }
.terminal-line.warn { color: #fbbf24; }
.terminal-line.ok { color: #4ade80; }
.terminal-line.evt { color: #60a5fa; }

.terminal-placeholder {
  color: #64748b;
  font-style: italic;
  padding: 20px 0;
}
</style>
