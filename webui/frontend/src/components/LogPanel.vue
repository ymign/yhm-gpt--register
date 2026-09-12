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
  background: var(--app-log-bg, #0b1619);
  border: 1px solid var(--app-log-border, rgba(93, 164, 177, 0.32));
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 12px 28px -12px rgba(0, 0, 0, 0.35);
}

.terminal-titlebar {
  padding: 8px 14px;
  background: var(--app-log-chrome, #152428);
  border-bottom: 1px solid var(--app-log-border, rgba(93, 164, 177, 0.32));
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
  color: #8aa8ad;
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
  font-family: "Cascadia Mono", "JetBrains Mono", "SF Mono", Consolas, "Liberation Mono", Menlo, monospace;
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--app-log-text, #e8f3f2);
  word-break: break-word;
  white-space: pre-wrap;
}

.terminal-line {
  margin-bottom: 1px;
  padding: 1px 8px;
  border-radius: 4px;
  border-left: 3px solid transparent;
}
.terminal-line.err {
  color: #ffb4ae;
  font-weight: 650;
  background: rgba(220, 70, 62, 0.18);
  border-left-color: #f07167;
}
.terminal-line.warn {
  color: #f5c26b;
  background: rgba(217, 119, 6, 0.12);
  border-left-color: #f5c26b;
}
.terminal-line.ok {
  color: #6ee7b7;
  background: rgba(16, 185, 129, 0.1);
  border-left-color: #34d399;
}
.terminal-line.evt { color: #8ed7e0; }

.terminal-placeholder {
  color: #8aa8ad;
  font-style: italic;
  padding: 20px 0;
}
</style>
