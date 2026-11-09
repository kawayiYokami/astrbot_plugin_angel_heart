<script lang="ts">
// 与后端 GET fonts 的 data 对齐：四张卡片 = 三款内置 + 自定义
export interface FontItem {
  key: string
  label: string
  script: string
  file: string
  size: number
  ready: boolean
  selected: boolean
  custom: boolean
}

export interface FontState {
  dir: string
  selected: string
  ready: boolean
  downloading: boolean
  cooldown_remaining: number
  fonts: FontItem[]
}
</script>

<template>
  <n-card class="font-card" embedded>
    <div class="font-status">
      <n-tag size="small" :bordered="false" :type="ready ? 'success' : 'warning'">
        {{ ready ? '字体已就绪' : '字体未就绪' }}
      </n-tag>
      <n-tag v-if="downloading" size="small" :bordered="false" type="info">正在下载</n-tag>
      <n-tag v-if="cooldownRemaining > 0" size="small" :bordered="false" type="error">
        刚才没下载成，{{ cooldownRemaining }} 秒后可以再试
      </n-tag>
    </div>

    <p class="font-desc">选一款喜欢的字体；第一次用会先下载，下完自动生效。</p>

    <div class="font-grid" role="radiogroup" aria-label="字体选择">
      <div
        v-for="f in fonts"
        :key="f.key"
        class="font-tile"
        :class="{ active: f.selected, busy: isBusy(f) }"
        role="radio"
        :aria-checked="f.selected"
        tabindex="0"
        @click="onTileClick(f)"
        @keydown.enter.prevent="onTileClick(f)"
        @keydown.space.prevent="onTileClick(f)"
      >
        <div class="tile-row">
          <span class="tile-name">{{ f.label }}</span>
          <span v-if="f.script" class="tile-script">{{ f.script }}</span>
          <span class="tile-size">{{ sizeText(f.size) }}</span>
          <n-tag size="small" :bordered="false" :type="statusType(f)">{{ statusText(f) }}</n-tag>
        </div>
        <div v-if="f.custom" class="tile-upload">
          <n-button size="tiny" :loading="uploading" @click.stop="pick()">
            <template #icon><Icon icon="lucide:upload" /></template>
            {{ f.ready ? '换个文件' : '上传 .ttf 文件' }}
          </n-button>
        </div>
      </div>
      <n-empty v-if="!fonts.length" description="暂无字体信息" size="small" />
    </div>

    <!-- 只有自定义卡片用得到，放一份在 v-for 外面：写在 v-for 里的 ref 会被 Vue 收成数组 -->
    <input
      ref="fileInput"
      class="font-file-input"
      type="file"
      accept=".ttf,font/ttf,application/x-font-ttf"
      @change="onFileChange"
    />

    <div class="font-footer">
      <div class="font-dir">
        <button type="button" class="dir-toggle" @click="dirOpen = !dirOpen">
          <Icon :icon="dirOpen ? 'lucide:chevron-down' : 'lucide:chevron-right'" class="dir-icon" />
          存放位置
        </button>
        <span v-if="dirOpen" class="dir-value">{{ dir || '—' }}</span>
      </div>
      <n-button
        size="tiny"
        :loading="fetching"
        :disabled="downloading || cooldownRemaining > 0"
        @click="emit('fetch')"
      >
        <template #icon><Icon icon="lucide:refresh-cw" /></template>
        重试下载
      </n-button>
    </div>
  </n-card>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { NButton, NCard, NEmpty, NTag } from 'naive-ui'
import { Icon } from '@iconify/vue'
import { formatMB } from './format'

const props = defineProps<{
  dir: string
  ready: boolean
  downloading: boolean
  cooldownRemaining: number
  fonts: FontItem[]
  fetching: boolean
  selectingKey: string | null
  uploading: boolean
}>()

const emit = defineEmits<{
  select: [key: string]
  fetch: []
  upload: [key: string, file: File]
}>()

// 存放位置默认折叠：它是排查信息，不该占主要操作的视线
const dirOpen = ref(false)

const fileInput = ref<HTMLInputElement | null>(null)

function pick() {
  const el = fileInput.value
  if (!el) return
  // 同一文件重复选择也要能再次触发 change
  el.value = ''
  el.click()
}

function onFileChange(e: Event) {
  const el = e.target as HTMLInputElement
  const file = el.files?.[0]
  if (file) emit('upload', 'custom', file)
  el.value = ''
}

function onTileClick(f: FontItem) {
  if (f.selected) return
  // 自定义款还没有可用文件时，点卡片直接弹选择框，比先选中再找按钮少一步
  if (f.custom && !f.ready) {
    pick()
    return
  }
  emit('select', f.key)
}

// 切到这一款还没落地时算「正在下载」，避免卡片状态与顶部标签打架
function isBusy(f: FontItem): boolean {
  return props.selectingKey === f.key || (f.selected && props.downloading)
}

function statusText(f: FontItem): string {
  if (f.ready) return '已就绪'
  if (isBusy(f)) return '正在下载'
  if (f.custom) return f.size > 0 ? '文件不可用' : '未上传'
  return '未下载'
}

function statusType(f: FontItem): 'success' | 'info' | 'default' {
  if (f.ready) return 'success'
  if (isBusy(f)) return 'info'
  return 'default'
}

function sizeText(size: number): string {
  // 0 字节说「未占用空间」比「0 MB」更像人话；格式化本身交给 formatMB
  return size > 0 ? formatMB(size) : '未占用空间'
}
</script>

<style scoped>
.font-card {
  margin-bottom: 12px;
}

.font-desc {
  font-size: 12px;
  color: var(--text-3);
  margin-bottom: 10px;
}

.font-status {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

.font-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  align-items: start;
  gap: 10px;
}

/* 窄屏一列，避免两个字名挤成两行 */
@media (max-width: 520px) {
  .font-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}

.font-tile {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-md);
  background: var(--glass-divider);
  cursor: pointer;
  transition: border-color 0.2s, box-shadow 0.2s;
  position: relative;
}

.font-tile:hover {
  border-color: var(--accent);
}

.font-tile:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.font-tile.active {
  border-color: var(--accent);
  box-shadow: inset 0 0 0 1px var(--accent);
}

.tile-row {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}

.tile-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-1);
}

.tile-script {
  font-size: 12px;
  color: var(--text-3);
}

.tile-size {
  font-size: 12px;
  color: var(--text-2);
  margin-left: auto;
}

.tile-upload {
  display: flex;
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

.font-footer {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 12px;
}

.font-dir {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}

.dir-toggle {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 0;
  border: 0;
  background: transparent;
  font-size: 12px;
  color: var(--text-3);
  cursor: pointer;
}

.dir-toggle:hover {
  color: var(--text-1);
}

.dir-icon {
  font-size: 12px;
}

.dir-value {
  font-family: 'Consolas', 'Courier New', monospace;
  font-size: 12px;
  color: var(--text-3);
  word-break: break-all;
}
</style>
