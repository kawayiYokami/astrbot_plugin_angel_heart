<template>
  <div class="schema-form">
    <!-- 分段卡片：每张 v-card 一个分组，标题硬分段，卡内字段软分段 -->
    <v-card
      v-for="group in groups"
      :key="group.key"
      rounded="md"
      style="margin-bottom: 16px; padding-bottom: 8px;"
      class="group-card"
    >
      <v-card-text class="config-section" style="padding-bottom: 8px;">
        <div class="d-flex align-center ga-3">
          <div class="flex-grow-1 min-w-0">
            <div class="config-title text-truncate">{{ group.title }}</div>
            <div v-if="group.subtitle" class="config-hint">{{ group.subtitle }}</div>
          </div>
          <v-btn
            v-if="group.canReset"
            variant="text"
            size="small"
            prepend-icon="lucide:rotate-ccw"
            @click="resetGroup(group)"
          >
            恢复默认
          </v-btn>
        </div>
      </v-card-text>

      <div class="object-config">
        <div class="config-item">
          <template v-for="row in group.rows" :key="rowKey(row)">
            <!-- 二级子分区（组内嵌套 object）：小标题软分段 -->
            <div v-if="row.kind === 'subsection'" class="subsection-title">{{ row.title }}</div>

            <template v-else>
              <v-row class="config-row" :class="{ 'is-disabled': isDisabled(row) }">
                <v-col cols="12" :sm="row.block ? 12 : 6" class="property-info">
                  <v-list-item density="compact">
                    <v-list-item-title class="property-name">{{ row.label }}</v-list-item-title>
                    <v-list-item-subtitle v-if="displayHint(row)" class="property-hint">{{ displayHint(row) }}</v-list-item-subtitle>
                  </v-list-item>
                </v-col>
                <v-col cols="12" :sm="row.block ? 12 : 6" class="config-input">
                  <!-- 服务商选择：显式标记或 *_provider_id 且有可选数据 -->
                  <v-select
                    v-if="isProviderSelect(row.meta, row.path)"
                    :model-value="asString(getIn(row.path))"
                    :items="providerOptions(row.path)"
                    :disabled="isDisabled(row)"
                    clearable
                    placeholder="选择模型服务商"
                    variant="outlined"
                    density="compact"
                    hide-details
                    class="config-field"
                    @update:model-value="(v: unknown) => setIn(row.path, v ?? '')"
                  />
                  <!-- 键值映射：行式键值编辑器替代手写 JSON -->
                  <div v-else-if="isKvMap(row.meta, row.path)" class="kv-editor">
                    <div
                      v-for="(kv, kvIdx) in kvRowsOf(row.path)"
                      :key="kvIdx"
                      class="d-flex align-center ga-2 mb-2"
                    >
                      <v-text-field
                        :model-value="kv.key"
                        placeholder="人格名 / 会话 ID"
                        variant="outlined"
                        density="compact"
                        hide-details
                        class="config-field flex-grow-1"
                        @update:model-value="(v: string) => { kv.key = v; kvSync(row.path) }"
                      />
                      <v-text-field
                        :model-value="kv.value"
                        placeholder="记忆分类域"
                        variant="outlined"
                        density="compact"
                        hide-details
                        class="config-field flex-grow-1"
                        @update:model-value="(v: string) => { kv.value = v; kvSync(row.path) }"
                      />
                      <v-btn
                        variant="text"
                        size="small"
                        icon="lucide:x"
                        color="error"
                        :aria-label="'删除第 ' + (kvIdx + 1) + ' 组'"
                        @click="kvRemove(row.path, kvIdx)"
                      />
                    </div>
                    <v-btn
                      variant="text"
                      size="small"
                      prepend-icon="lucide:plus"
                      @click="kvAdd(row.path)"
                    >
                      添加一组
                    </v-btn>
                  </div>
                  <v-switch
                    v-else-if="row.meta.type === 'bool'"
                    :model-value="!!getIn(row.path)"
                    :disabled="isDisabled(row)"
                    color="primary"
                    hide-details
                    density="compact"
                    inset
                    @update:model-value="(v: unknown) => setIn(row.path, v)"
                  />
                  <v-text-field
                    v-else-if="row.meta.type === 'int' || row.meta.type === 'float'"
                    :model-value="asNumber(getIn(row.path))"
                    :disabled="isDisabled(row)"
                    type="number"
                    :step="row.meta.type === 'float' ? 0.1 : 1"
                    variant="outlined"
                    density="compact"
                    hide-details
                    class="config-field"
                    @update:model-value="(v: unknown) => setIn(row.path, v === '' || v === null || v === undefined ? null : Number(v))"
                  />
                  <v-combobox
                    v-else-if="row.meta.type === 'list'"
                    :model-value="asList(getIn(row.path))"
                    :disabled="isDisabled(row)"
                    multiple
                    chips
                    closable-chips
                    variant="outlined"
                    density="compact"
                    hide-details
                    class="config-field"
                    @update:model-value="(v: unknown) => setIn(row.path, v)"
                  />
                  <v-textarea
                    v-else-if="row.meta.type === 'text'"
                    :model-value="asString(getIn(row.path))"
                    :disabled="isDisabled(row)"
                    rows="3"
                    auto-grow
                    variant="outlined"
                    density="compact"
                    hide-details
                    class="config-field"
                    @update:model-value="(v: unknown) => setIn(row.path, v)"
                  />
                  <v-text-field
                    v-else
                    :model-value="asString(getIn(row.path))"
                    :disabled="isDisabled(row)"
                    variant="outlined"
                    density="compact"
                    hide-details
                    class="config-field"
                    @update:model-value="(v: unknown) => setIn(row.path, v)"
                  />
                </v-col>
              </v-row>
              <v-divider class="config-divider" />
            </template>
          </template>
        </div>
      </div>
    </v-card>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { SchemaMeta } from './schema'

interface FieldRow {
  kind: 'field'
  path: string[]
  label: string
  hint?: string
  meta: SchemaMeta
  block: boolean
}

interface SubsectionRow {
  kind: 'subsection'
  title: string
}

type CardRow = FieldRow | SubsectionRow

interface Group {
  key: string
  title: string
  subtitle?: string
  canReset: boolean
  rows: CardRow[]
}

const props = defineProps<{
  schema: Record<string, SchemaMeta>
  modelValue?: Record<string, unknown>
  providers?: Record<string, string[]>
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: Record<string, unknown>): void
}>()

// 后端 schema 里个别说明带了内部实现词（工具名、配置键），前端展示时换成人话。
// 后端文件不动，只在这里覆盖显示，保证原生配置页与插件页互不影响。
const HINT_OVERRIDES: Record<string, string> = {
  'image_caption_provider_id':
    '长文或图片转成文字时用哪个模型。主模型只在需要看清某张历史图片细节时才用它；上游自带的看图模型请留空，不然同一张图会被处理两次。',
  'context_compression.max_conversation_tokens':
    '插件最多攒多少上下文，超过就压缩。实际用的是这里和模型自带上限里更小的那个；填 0 表示不限，完全按模型的来。',
}

function displayHint(row: FieldRow): string {
  const override = HINT_OVERRIDES[row.path.join('.')]
  if (override !== undefined) return override
  return row.hint ?? ''
}

// 去掉 schema description 里的「[分组] 」前缀，界面不需要重复标签
function cleanLabel(text: string | undefined): string {
  return (text ?? '').replace(/^\[[^\]]+\]\s*/, '')
}

function buildField(prefix: string[], key: string, meta: SchemaMeta): FieldRow {
  const type = meta.type ?? 'string'
  // 宽控件独占整行：多行文本、标签编辑器与键值编辑器
  const block = type === 'text' || type === 'list'
  return {
    kind: 'field',
    path: [...prefix, key],
    label: cleanLabel(meta.description) || key,
    hint: meta.hint,
    meta,
    block,
  }
}

function rowKey(row: CardRow): string {
  return row.kind === 'field' ? row.path.join('.') : row.title
}

// 顶层叶子归入「常规」组置顶；顶层 object 各成一卡；二级 object 成为卡内子分区
const groups = computed<Group[]>(() => {
  const schema = props.schema || {}
  const rootRows: CardRow[] = []
  const sections: Group[] = []

  for (const [key, meta] of Object.entries(schema)) {
    if (!meta || typeof meta !== 'object') continue
    if (meta.type === 'object') {
      const rows: CardRow[] = []
      for (const [subKey, subMeta] of Object.entries(meta.items || {})) {
        if (subMeta?.type === 'object') {
          rows.push({ kind: 'subsection', title: cleanLabel(subMeta.description) || subKey })
          for (const [k, m] of Object.entries(subMeta.items || {})) {
            rows.push(buildField([key, subKey], k, m))
          }
        } else {
          rows.push(buildField([key], subKey, subMeta))
        }
      }
      sections.push({
        key,
        title: cleanLabel(meta.description) || key,
        subtitle: meta.hint,
        canReset: false,
        rows,
      })
    } else {
      rootRows.push(buildField([], key, meta))
    }
  }

  const result: Group[] = []
  if (rootRows.length) {
    // 「常规」组不提供恢复默认（顶层字段无统一默认语义）
    result.push({ key: '__root__', title: '常规', canReset: false, rows: rootRows })
  }
  for (const s of sections) {
    s.canReset = s.rows.some((r) => r.kind === 'field' && r.meta.default !== undefined)
    result.push(s)
  }
  return result
})

// 恢复默认：组内所有带默认值的字段重置为 schema 默认值；改动后由父级 dirty 检测浮出保存按钮
function resetGroup(group: Group) {
  for (const row of group.rows) {
    if (row.kind === 'field' && row.meta.default !== undefined) {
      setIn(row.path, row.meta.default)
    }
  }
}

function getIn(path: string[]): unknown {
  let cur: unknown = props.modelValue
  for (const k of path) {
    if (cur && typeof cur === 'object') cur = (cur as Record<string, unknown>)[k]
    else return undefined
  }
  return cur
}

function setIn(path: string[], value: unknown) {
  const root: Record<string, unknown> = { ...(props.modelValue || {}) }
  let cur = root
  for (let i = 0; i < path.length - 1; i++) {
    const next = cur[path[i]]
    cur[path[i]] = next && typeof next === 'object' ? { ...(next as Record<string, unknown>) } : {}
    cur = cur[path[i]] as Record<string, unknown>
  }
  cur[path[path.length - 1]] = value
  emit('update:modelValue', root)
}

function asNumber(v: unknown): number | null {
  return typeof v === 'number' ? v : null
}

function asString(v: unknown): string {
  return typeof v === 'string' ? v : ''
}

function asList(v: unknown): string[] {
  return Array.isArray(v) ? v.map(String) : []
}

// 从属字段：左侧开关关掉时，右侧控件只禁用、不清值，重新打开即恢复原设置
const FIELD_DEPENDS: Record<string, { on: string; active: (v: unknown) => boolean }> = {
  long_text_image_threshold: { on: 'long_text_image_enabled', active: (v) => v !== false },
}

function isDisabled(row: FieldRow): boolean {
  const rule = FIELD_DEPENDS[row.path[row.path.length - 1]]
  if (!rule) return false
  return !rule.active(getIn([...row.path.slice(0, -1), rule.on]))
}

// ---------- JSON 映射字段：行式键值编辑器替代手写 JSON ----------
interface KvRow {
  key: string
  value: string
}

const kvDrafts = new Map<string, { source: string; rows: KvRow[] }>()

function parseKvObject(v: unknown): Record<string, string> | null {
  if (typeof v !== 'string') return null
  try {
    const parsed = JSON.parse(v) as unknown
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? (parsed as Record<string, string>)
      : null
  } catch {
    return null
  }
}

// schema 标记 editor_language=json 且当前值为合法 JSON 对象字符串时启用；解析失败回退文本框
function isKvMap(meta: SchemaMeta, path: string[]): boolean {
  return meta.editor_language === 'json' && parseKvObject(getIn(path)) !== null
}

function kvRowsOf(path: string[]): KvRow[] {
  const id = path.join('.')
  const source = asString(getIn(path))
  let draft = kvDrafts.get(id)
  if (!draft || draft.source !== source) {
    draft = {
      source,
      rows: Object.entries(parseKvObject(getIn(path)) || {}).map(([key, value]) => ({
        key,
        value: String(value),
      })),
    }
    kvDrafts.set(id, draft)
  }
  return draft.rows
}

function kvWrite(path: string[], rows: ReadonlyArray<KvRow>) {
  const obj: Record<string, string> = {}
  for (const r of rows) {
    if (typeof r.key === 'string' && r.key.trim()) obj[r.key.trim()] = String(r.value ?? '')
  }
  const source = JSON.stringify(obj)
  kvDrafts.set(path.join('.'), { source, rows: rows as KvRow[] })
  setIn(path, source)
}

// 行内输入直接改行对象，输入变化后手动序列化写回
function kvSync(path: string[]) {
  const draft = kvDrafts.get(path.join('.'))
  if (draft) kvWrite(path, draft.rows)
}

function kvAdd(path: string[]) {
  const rows = kvRowsOf(path)
  kvWrite(path, [...rows, { key: '', value: '' }])
}

function kvRemove(path: string[], idx: number) {
  const rows = kvRowsOf(path)
  kvWrite(path, rows.filter((_, i) => i !== idx))
}

// 服务商字段判定：显式标记，或 string 类型的 *_provider_id 键且对应列表非空
function isProviderSelect(meta: SchemaMeta, path: string[]): boolean {
  if (meta._special === 'select_provider') return true
  if (meta.type && meta.type !== 'string') return false
  const key = path[path.length - 1]
  if (!/^(.*_)?provider_id$/.test(key)) return false
  const p = props.providers || {}
  const ids = /embed/i.test(key) ? p.embedding : /rerank/i.test(key) ? p.rerank : p.chat
  return !!ids?.length
}

function providerOptions(path: string[]) {
  const key = path[path.length - 1]
  const p = props.providers || {}
  const ids = /embed/i.test(key)
    ? p.embedding || []
    : /rerank/i.test(key)
      ? p.rerank || []
      : p.chat || []
  return [{ title: '(留空)', value: '' }, ...ids.map((id) => ({ title: id, value: id }))]
}
</script>

<style scoped>
.schema-form {
  display: flex;
  flex-direction: column;
}

/* ---- 上游 AstrBotConfigV4.vue 行式范式（照抄数值；颜色用 rgb() 包裹主题三元组） ---- */
.v-application .config-section,
.config-section {
  margin-bottom: 4px;
}
.config-title {
  font-size: 0.875rem;
  font-weight: 500;
  color: rgb(var(--v-theme-primaryText));
}
.config-hint {
  font-size: 0.75rem;
  color: rgb(var(--v-theme-secondaryText));
  margin-top: 2px;
}
.object-config {
  width: 100%;
}
.config-row {
  margin: 0;
  align-items: center;
  padding: 8px 8px;
  border-radius: 4px;
}
.config-row:hover {
  background-color: rgba(0, 0, 0, 0.03);
}
.property-info {
  padding: 0;
}
.property-name {
  font-size: 0.875rem;
  color: rgb(var(--v-theme-primaryText));
}
.property-hint {
  font-size: 0.75rem;
  color: rgb(var(--v-theme-secondaryText));
  margin-top: 2px;
}
.config-input {
  padding: 4px 8px;
}
.config-field {
  margin-bottom: 0;
}
.config-divider {
  border-color: rgba(0, 0, 0, 0.1);
  margin-left: 24px;
}

/* 宽控件独占整行时，标题与控件上下排 */
.config-row .property-info:has(+ .config-input) {
  min-width: 0;
}

/* 从属字段被上游开关关掉：整体压淡，但值仍留在配置里 */
.config-row.is-disabled .property-name,
.config-row.is-disabled .property-hint {
  opacity: 0.45;
}
.config-row.is-disabled .config-input {
  opacity: 0.55;
}

/* 键值编辑器占满右列 */
.kv-editor {
  width: 100%;
}

/* 二级子分区标题 */
.subsection-title {
  padding: 14px 16px 6px;
  font-size: 0.875rem;
  font-weight: 500;
  color: rgb(var(--v-theme-primaryText));
}

.group-card {
  border-color: rgba(var(--v-border-color), 0.16);
}
</style>
