<script lang="ts">
// 与后端 GET fonts 的 data 对齐：三款内置 + 自定义
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
  <v-card rounded="md" class="font-card">
    <v-card-text class="pa-5">
      <div class="d-flex flex-wrap align-center ga-2 mb-3">
        <v-chip size="small" :color="ready ? 'success' : 'warning'" variant="tonal">
          {{ ready ? '字体已就绪' : '字体还没下好' }}
        </v-chip>
        <v-chip v-if="downloading" size="small" color="info" variant="tonal">正在下载</v-chip>
        <v-chip v-if="cooldownRemaining > 0" size="small" color="error" variant="tonal">
          刚才没下载成，{{ cooldownRemaining }} 秒后再试
        </v-chip>
      </div>

      <p class="text-body-2 text-medium-emphasis mb-4">选一款喜欢的字体；第一次用会先下载，下完自动生效。</p>

      <div v-if="!fonts.length" class="d-flex justify-center py-8">
        <v-empty-state icon="lucide:type" title="还没有字体信息" />
      </div>
      <div v-else class="font-grid" role="radiogroup" aria-label="字体选择">
        <v-card
          v-for="f in fonts"
          :key="f.key"
          variant="outlined"
          rounded="control"
          class="font-tile"
          :class="{ active: f.selected, busy: isBusy(f) }"
          role="radio"
          :aria-checked="f.selected"
          tabindex="0"
          @click="onTileClick(f)"
          @keydown.enter.prevent="onTileClick(f)"
          @keydown.space.prevent="onTileClick(f)"
        >
          <div class="d-flex flex-wrap align-baseline ga-2 min-w-0">
            <span class="text-body-1 font-weight-medium">{{ f.label }}</span>
            <span v-if="f.script" class="text-caption text-medium-emphasis">{{ f.script }}</span>
            <span class="text-caption text-medium-emphasis ml-auto">{{ sizeText(f.size) }}</span>
            <v-chip size="small" :color="statusColor(f)" variant="tonal">{{ statusText(f) }}</v-chip>
          </div>
          <div v-if="f.custom" class="mt-2">
            <v-file-input
              :model-value="customFiles"
              accept=".ttf,font/ttf,application/x-font-ttf"
              label="上传 .ttf 文件"
              prepend-icon=""
              prepend-inner-icon="lucide:upload"
              variant="outlined"
              density="compact"
              hide-details
              :loading="uploading"
              @update:model-value="onCustomFiles"
            />
          </div>
        </v-card>
      </div>

      <!-- 自定义款的文件选择框在卡片里，选择后直接上传 -->
      <div class="d-flex flex-wrap align-center justify-space-between ga-2 mt-4">
        <div class="d-flex flex-wrap align-baseline ga-2 min-w-0">
          <v-btn variant="text" size="small" :prepend-icon="dirOpen ? 'lucide:chevron-down' : 'lucide:chevron-right'" @click="dirOpen = !dirOpen">
            存储位置
          </v-btn>
          <span v-if="dirOpen" class="text-caption text-medium-emphasis dir-value">{{ dir || '—' }}</span>
        </div>
        <v-btn
          size="small"
          variant="text"
          prepend-icon="lucide:refresh-cw"
          :loading="fetching"
          :disabled="downloading || cooldownRemaining > 0"
          @click="emit('fetch')"
        >
          重新下载
        </v-btn>
      </div>
    </v-card-text>
  </v-card>
</template>

<script setup lang="ts">
import { ref } from 'vue'
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

// 自定义款的文件选择：选完直接上传，清空选择框方便重复选同一文件
const customFiles = ref<File[]>([])

function onCustomFiles(files: File | File[] | null) {
  const list = Array.isArray(files) ? files : files ? [files] : []
  customFiles.value = []
  if (list[0]) emit('upload', 'custom', list[0])
}

function onTileClick(f: FontItem) {
  if (f.selected) return
  // 自定义款的文件框就在卡片里，点卡片只选中；没文件时选了也用不了，后端会提示
  emit('select', f.key)
}

// 切到这一款还没落地时算「正在下载」，避免卡片状态与顶部标签打架
function isBusy(f: FontItem): boolean {
  return props.selectingKey === f.key || (f.selected && props.downloading)
}

function statusText(f: FontItem): string {
  if (f.ready) return '已就绪'
  if (isBusy(f)) return '正在下载'
  if (f.custom) return f.size > 0 ? '文件不可用' : '还没上传'
  return '还没下载'
}

function statusColor(f: FontItem): 'success' | 'info' | 'default' {
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
  border-color: rgba(var(--v-border-color), 0.16);
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
  padding: 10px 12px;
  cursor: pointer;
  transition: border-color 0.2s, box-shadow 0.2s;
  position: relative;
}

.font-tile:hover {
  border-color: rgb(var(--v-theme-primary));
}

.font-tile:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 2px;
}

.font-tile.active {
  border-color: rgb(var(--v-theme-primary));
  box-shadow: inset 0 0 0 1px rgb(var(--v-theme-primary));
}

.dir-value {
  font-family: Consolas, 'Courier New', monospace;
  word-break: break-all;
}
</style>
