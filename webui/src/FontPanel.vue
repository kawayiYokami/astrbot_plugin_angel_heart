<script lang="ts">
// 字体状态的数据形状与后端 GET fonts 的 data 一致
export interface FontWeightItem {
  key: string
  file: string
  ready: boolean
  size: number
}

export interface FontState {
  dir: string
  ready: boolean
  downloading: boolean
  cooldown_remaining: number
  weights: FontWeightItem[]
}
</script>

<template>
  <n-card class="font-card" embedded>
    <p class="group-desc">
      长文转图需要一款黑体，插件不自带，第一次发长文时会自动下载（约 18MB）。
      下载不了的话，把字体文件从这里传上来就行。
    </p>

    <div class="font-status">
      <n-tag size="small" :bordered="false" :type="ready ? 'success' : 'warning'">
        {{ ready ? '字体已就绪' : '字体还没准备好' }}
      </n-tag>
      <n-tag v-if="downloading" size="small" :bordered="false" type="info">正在下载</n-tag>
      <n-tag v-if="cooldownRemaining > 0" size="small" :bordered="false" type="error">
        刚才没下载成，{{ cooldownRemaining }} 秒后可以再试
      </n-tag>
    </div>

    <div class="font-dir">
      <span class="font-dir-label">存放位置</span>
      <span class="font-dir-value">{{ dir || '—' }}</span>
    </div>

    <div class="font-weights">
      <div v-for="w in weights" :key="w.key" class="font-row">
        <div class="font-row-main">
          <span class="font-row-title">{{ weightLabel(w.key) }}</span>
          <span class="font-row-file">{{ w.file }}</span>
        </div>
        <div class="font-row-side">
          <n-tag size="small" :bordered="false" :type="w.ready ? 'success' : 'default'">
            {{ w.ready ? '已就绪' : '未就绪' }}
          </n-tag>
          <span class="font-row-size">{{ fmtSize(w.size) }}</span>
          <n-button size="tiny" :loading="busyWeight === w.key" @click="pick(w.key)">
            <template #icon><Icon icon="lucide:upload" /></template>
            上传
          </n-button>
        </div>
        <input
          :ref="(el) => setInput(w.key, el as HTMLInputElement | null)"
          class="font-file-input"
          type="file"
          accept=".ttf,font/ttf,application/x-font-ttf"
          @change="onFileChange($event, w.key)"
        />
      </div>
      <n-empty v-if="!weights.length" description="暂无字体信息" size="small" />
    </div>

    <div class="font-actions">
      <n-button
        size="tiny"
        type="primary"
        :loading="fetching"
        :disabled="downloading || cooldownRemaining > 0"
        @click="emit('fetch')"
      >
        <template #icon><Icon icon="lucide:download" /></template>
        立即下载
      </n-button>
    </div>
  </n-card>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { NButton, NCard, NEmpty, NTag } from 'naive-ui'
import { Icon } from '@iconify/vue'

defineProps<{
  dir: string
  ready: boolean
  downloading: boolean
  cooldownRemaining: number
  weights: FontWeightItem[]
  fetching: boolean
  busyWeight: string | null
}>()

const emit = defineEmits<{
  fetch: []
  upload: [weight: string, file: File]
}>()

// 每个字重挂一个隐藏 input：同一文件重复选择也要能再次触发 change，所以选完就清空
const inputs = ref<Record<string, HTMLInputElement | null>>({})

function setInput(key: string, el: HTMLInputElement | null) {
  inputs.value[key] = el
}

function pick(key: string) {
  const el = inputs.value[key]
  if (!el) return
  el.value = ''
  el.click()
}

function onFileChange(e: Event, key: string) {
  const el = e.target as HTMLInputElement
  const file = el.files?.[0]
  if (file) emit('upload', key, file)
  el.value = ''
}

function weightLabel(key: string): string {
  if (key === 'regular') return '字体'
  return key
}

function fmtSize(size: number): string {
  if (!size || size <= 0) return '0 MB'
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}
</script>

<style scoped>
.font-card {
  margin-bottom: 12px;
}

.group-desc {
  font-size: 12px;
  color: var(--text-3);
  margin-bottom: 10px;
}

.font-status {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.font-dir {
  display: flex;
  gap: 8px;
  align-items: baseline;
  font-size: 12px;
  color: var(--text-3);
  margin-bottom: 10px;
}

.font-dir-label {
  flex: none;
}

.font-dir-value {
  font-family: 'Consolas', 'Courier New', monospace;
  color: var(--text-1);
  word-break: break-all;
}

.font-weights {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.font-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-md);
  background: var(--glass-divider);
}

/* input 用不可见覆盖而不是 display:none：部分浏览器对 display:none 的
   file input 调 click() 会被拦掉，覆盖层能保证选择框一定弹出来 */
.font-file-input {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
  pointer-events: none;
}

.font-row-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.font-row-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-1);
}

.font-row-file {
  font-size: 12px;
  color: var(--text-3);
  font-family: 'Consolas', 'Courier New', monospace;
  word-break: break-all;
}

.font-row-side {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: none;
}

.font-row-size {
  font-size: 12px;
  color: var(--text-2);
  min-width: 56px;
  text-align: right;
}

.font-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
</style>
