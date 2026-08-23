<template>
  <n-layout class="layout" has-sider>
    <!-- 左侧：模板列表 -->
    <n-layout-sider
      width="260"
      collapse-mode="width"
      :collapsed-width="56"
      show-trigger="bar"
      :collapsed="sidebarCollapsed"
      @collapse="sidebarCollapsed = true"
      @expand="sidebarCollapsed = false"
    >
      <div class="sidebar-inner" :class="{ collapsed: sidebarCollapsed }">
        <div class="sidebar-header">
          <button
            class="theme-toggle"
            type="button"
            :title="isDark ? '切换到光模式' : '切换到暗模式'"
            @click="toggleTheme()"
          >
            <Icon :icon="isDark ? 'lucide:sun' : 'lucide:moon'" />
          </button>
          <div v-if="!sidebarCollapsed" class="brand">天使之心</div>
          <n-button
            v-if="!sidebarCollapsed"
            size="small"
            type="primary"
            @click="openCreateModal"
          >
            <template #icon><Icon icon="lucide:plus" /></template>
            新增
          </n-button>
          <n-button
            v-else
            size="small"
            type="primary"
            circle
            title="新增"
            @click="openCreateModal"
          >
            <template #icon><Icon icon="lucide:plus" /></template>
          </n-button>
        </div>
        <n-scrollbar class="sidebar-scroll">
          <n-menu
            class="sidebar-menu"
            :value="sidebarMenuValue"
            :options="sidebarMenuOptions"
            :collapsed="sidebarCollapsed"
            :collapsed-width="56"
            :collapsed-icon-size="20"
            @update:value="onSidebarMenu"
          />
          <n-empty
            v-if="!templates.length && !sidebarCollapsed"
            size="small"
            description="还没有模板，点击右上角新建"
            class="list-empty"
          />
        </n-scrollbar>
        <div class="sidebar-footer">
          <n-menu
            class="sidebar-menu"
            :value="viewMode === 'config' && !selectedId ? 'settings' : null"
            :options="settingsMenuOptions"
            :collapsed="sidebarCollapsed"
            :collapsed-width="56"
            :collapsed-icon-size="20"
            @update:value="selectTemplate(null)"
          />
        </div>
      </div>
    </n-layout-sider>

    <!-- 右侧：配置详情 -->
    <n-layout-content class="content">
      <n-scrollbar class="content-scroll">
      <div class="content-inner">
      <!-- 联系人监控 -->
      <template v-if="viewMode === 'monitor'">
        <div class="content-header">
          <h2>联系人监控</h2>
          <span class="content-sub">群聊与私聊的在场状态、巡检与最近决策，每 3 秒自动刷新</span>
        </div>
        <n-radio-group v-model:value="kindFilter" size="small" class="kind-filter">
          <n-radio-button value="all">全部</n-radio-button>
          <n-radio-button value="group">群聊</n-radio-button>
          <n-radio-button value="private">私聊</n-radio-button>
        </n-radio-group>
        <div class="status-grid">
          <div v-for="item in filteredStatusItems" :key="item.chat_id" class="status-card">
            <div class="status-chat">
              <template v-if="item.display_name">{{ item.display_name }}</template>
              <span v-else class="status-chat-placeholder">
                <Icon icon="lucide:user-x" class="inline-icon" /> 未命名
              </span>
              <span class="status-chat-id">{{ item.chat_id }}</span>
            </div>
            <div class="status-meta">
              <span
                class="status-badge"
                :class="item.status.current_status === 'OBSERVATION' ? 'on' : 'off'"
              >
                {{ item.status.current_status === 'OBSERVATION' ? '在场' : '离场' }}
              </span>
              <span class="status-energy">
                <Icon icon="lucide:zap" class="inline-icon" /> 能量 {{ fmtEnergy(item.energy) }}
              </span>
            </div>
            <div class="status-line">
              <span class="status-label">巡检</span>
              <span v-if="item.patrol.waiting" class="status-value">
                <Icon icon="lucide:timer" class="inline-icon" />
                {{ patrolLabel(item.patrol.waiting) }} {{ item.patrol.remaining }}/{{ item.patrol.total }}s
              </span>
              <span v-else class="status-value">空闲</span>
            </div>
            <div class="status-line">
              <span class="status-label">最近决策</span>
              <span v-if="item.last_decision" class="status-value">
                <Icon icon="lucide:message-square" class="inline-icon" />
                {{ item.last_decision.should_reply ? '回复' : '不回' }} ·
                {{ decisionTime(item.last_decision.decided_at) }} ·
                {{ item.last_decision.summary || '无说明' }}
              </span>
              <span v-else class="status-value">暂无</span>
            </div>
            <div class="status-binding">
              <n-select
                :value="item.template_id"
                size="small"
                :options="bindingOptions(item.chat_id)"
                @update:value="(v: string) => setChatBinding(item.chat_id, v)"
              />
            </div>
          </div>
          <n-empty
            v-if="!filteredStatusItems.length"
            size="small"
            :description="kindFilter === 'all' ? '暂无联系人（产生消息后才会出现在这里）' : (kindFilter === 'group' ? '暂无群聊' : '暂无私聊')"
          />
        </div>
      </template>

      <!-- 全局配置（可编辑）：schema 驱动分段卡片；有草稿变更时右下角浮出保存按钮 -->
      <template v-else-if="!selectedId">
        <div class="content-header">
          <h2>全局配置</h2>
          <span class="content-sub">未绑定群聊使用的默认配置，与原生插件配置页同源</span>
        </div>
        <SchemaForm
          v-if="settingsLoaded"
          :schema="settingsSchema"
          v-model:model-value="settingsValues"
          :providers="settingsProviders"
        />
        <n-spin v-else size="small" style="margin-top: 40px" />
      </template>

      <!-- 模板配置编辑：与全局配置共用 SchemaForm 分段卡 -->
      <template v-else>
        <div class="content-header">
          <h2>{{ currentTemplate?.name }}</h2>
        </div>
        <SchemaForm
          v-if="settingsLoaded"
          v-model:model-value="configModel"
          :schema="templateSchema"
          :providers="settingsProviders"
        />
        <n-spin v-else size="small" style="margin-top: 40px" />
      </template>
      </div>
      </n-scrollbar>
    </n-layout-content>

    <!-- 新建模板弹窗 -->
    <n-modal
      v-model:show="createModalVisible"
      preset="dialog"
      title="新建配置模板"
      positive-text="创建"
      negative-text="取消"
      @positive-click="createTemplate"
    >
      <n-form label-placement="top" class="create-form">
        <n-form-item label="模板名称">
          <n-input v-model:value="createForm.name" placeholder="如：游戏群、学术群" />
        </n-form-item>
        <n-form-item label="初始配置">
          <n-radio-group v-model:value="createForm.mode">
            <n-radio value="empty">空白模板</n-radio>
            <n-radio value="global">从全局配置复制</n-radio>
          </n-radio-group>
        </n-form-item>
      </n-form>
    </n-modal>

    <!-- 重命名弹窗 -->
    <n-modal
      v-model:show="renameModalVisible"
      preset="dialog"
      title="重命名模板"
      positive-text="保存"
      negative-text="取消"
      @positive-click="renameTemplate"
    >
      <n-input v-model:value="renameForm.name" placeholder="模板名称" />
    </n-modal>

    <!-- 悬浮保存按钮：全局配置或模板草稿变更时浮出 -->
    <Transition name="fab">
      <n-button
        v-if="configDirty"
        class="save-fab"
        type="primary"
        round
        size="large"
        :loading="saving || savingSettings"
        @click="saveCurrent"
      >
        <template #icon><Icon icon="lucide:save" /></template>
        保存更改
      </n-button>
    </Transition>
  </n-layout>
</template>

<script setup lang="ts">
import { computed, h, inject, onMounted, onUnmounted, reactive, ref } from 'vue'
import {
  NButton,
  NEmpty,
  NForm,
  NFormItem,
  NInput,
  NLayout,
  NLayoutContent,
  NLayoutSider,
  NMenu,
  NModal,
  NPopconfirm,
  NRadio,
  NRadioButton,
  NRadioGroup,
  NScrollbar,
  NSelect,
  NSwitch,
  NTabPane,
  NTabs,
  useMessage,
} from 'naive-ui'
import type { MenuOption } from 'naive-ui'
import { Icon } from '@iconify/vue'
import {
  GROUPS,
  type ChatItem,
  type ChatStatusItem,
  type TemplateDetail,
  type TemplateConfig,
} from './fields'
import { useBridge } from './composables/useBridge'
import { themeKey } from './theme'
import SchemaForm from './SchemaForm.vue'
import type { SchemaMeta } from './schema'

const { apiGet, apiPost } = useBridge()
const message = useMessage()
const { isDark, toggle: toggleTheme } = inject(themeKey)!

const templates = ref<TemplateDetail[]>([])
const chats = ref<ChatItem[]>([])
const statusItems = ref<ChatStatusItem[]>([])
const kindFilter = ref<'all' | 'group' | 'private'>('all')
const viewMode = ref<'config' | 'monitor'>('config')
const selectedId = ref<string | null>(null)
const sidebarCollapsed = ref(false)
// naive-ui 的 n-layout-sider 没有 breakpoint 属性，用 matchMedia 实现窄屏自动收起
const narrowMql = window.matchMedia('(max-width: 768px)')
const syncSidebarToWidth = () => {
  sidebarCollapsed.value = narrowMql.matches
}
onMounted(() => {
  syncSidebarToWidth()
  narrowMql.addEventListener('change', syncSidebarToWidth)
})
onUnmounted(() => {
  narrowMql.removeEventListener('change', syncSidebarToWidth)
})
const saving = ref(false)
let statusTimer: ReturnType<typeof setInterval> | null = null

const configModel = ref<Record<string, Record<string, unknown>>>({})
const createModalVisible = ref(false)
const renameModalVisible = ref(false)
const createForm = reactive({ name: '', mode: 'empty' })
const renameForm = reactive({ name: '' })
let renameTarget: TemplateDetail | null = null

const currentTemplate = computed(() =>
  templates.value.find((t) => t.id === selectedId.value) ?? null
)

const filteredStatusItems = computed(() => {
  if (kindFilter.value === 'all') return statusItems.value
  return statusItems.value.filter((item) => effectiveKind(item) === kindFilter.value)
})

// kind 缺失（来源登记未覆盖，如白名单纯群号）时按 chat_id 形态兜底推断
function effectiveKind(item: ChatStatusItem): string {
  if (item.kind === 'group' || item.kind === 'private') return item.kind
  if (/:FriendMessage:|:PrivateMessage:/.test(item.chat_id)) return 'private'
  return 'group'
}

// ---------- 侧栏导航（n-menu 渲染，颜色/选中态全部来自全局主题） ----------
const settingsMenuOptions: MenuOption[] = [
  { label: '插件设置', key: 'settings', icon: () => h(Icon, { icon: 'lucide:settings' }) },
]

function renderTemplateLabel(tpl: TemplateDetail) {
  return () =>
    h('div', { class: 'tpl-label' }, [
      h('span', { class: 'tpl-name' }, tpl.name),
      h(
        'div',
        {
          class: 'tpl-actions',
          onClick: (e: MouseEvent) => e.stopPropagation(),
        },
        [
          h(
            NButton,
            {
              size: 'tiny',
              quaternary: true,
              circle: true,
              title: '重命名',
              onClick: () => openRenameModal(tpl),
            },
            { icon: () => h(Icon, { icon: 'lucide:pencil' }) },
          ),
          h(
            NPopconfirm,
            { onPositiveClick: () => deleteTemplate(tpl.id) },
            {
              trigger: () =>
                h(
                  NButton,
                  { size: 'tiny', quaternary: true, circle: true, type: 'error', title: '删除' },
                  { icon: () => h(Icon, { icon: 'lucide:trash-2' }) },
                ),
              default: () => '删除后绑定它的群聊将回退到全局配置',
            },
          ),
        ],
      ),
    ])
}

const sidebarMenuOptions = computed<MenuOption[]>(() => [
  { label: '联系人监控', key: 'monitor', icon: () => h(Icon, { icon: 'lucide:activity' }) },
  ...templates.value.map((tpl) => ({
    key: tpl.id,
    icon: () => h(Icon, { icon: 'lucide:file-text' }),
    label: renderTemplateLabel(tpl),
  })),
])

function onSidebarMenu(key: string) {
  if (key === 'monitor') viewMode.value = 'monitor'
  else selectTemplate(key)
}

const sidebarMenuValue = computed(() => {
  if (viewMode.value === 'monitor') return 'monitor'
  return selectedId.value
})

// ---------- 插件设置（全局 _conf_schema.json） ----------

const settingsSchema = ref<Record<string, SchemaMeta>>({})
const settingsValues = ref<Record<string, unknown>>({})
const settingsProviders = ref<Record<string, string[]>>({})
const settingsLoaded = ref(false)
const savingSettings = ref(false)
// 草稿基线：与当前值的快照不一致即视为有变更，浮出保存按钮
const settingsBaseline = ref('')
const settingsDirty = computed(
  () =>
    settingsLoaded.value &&
    JSON.stringify(settingsValues.value) !== settingsBaseline.value,
)

// 全局配置可编辑：首次切到全局配置视图时加载 schema + 当前值
function loadPluginConfig() {
  if (settingsLoaded.value) return
  void (async () => {
    try {
      const data = await apiGet<{
        schema: Record<string, SchemaMeta>
        values: Record<string, unknown>
        providers: Record<string, string[]>
      }>('plugin_config')
      settingsSchema.value = data?.schema || {}
      settingsValues.value = data?.values || {}
      settingsProviders.value = data?.providers || {}
      settingsBaseline.value = JSON.stringify(settingsValues.value)
      settingsLoaded.value = true
    } catch (e) {
      message.error(`加载插件设置失败: ${(e as Error).message}`)
    }
  })()
}

async function savePluginConfig() {
  savingSettings.value = true
  try {
    await apiPost('plugin_config/save', { values: settingsValues.value })
    settingsBaseline.value = JSON.stringify(settingsValues.value)
    message.success('已保存并即时生效')
  } catch (e) {
    message.error(`保存失败: ${(e as Error).message}`)
  } finally {
    savingSettings.value = false
  }
}

function fmtEnergy(v: number | null): string {
  if (v === null || v === undefined) return '—'
  return String(Math.round(v))
}

function patrolLabel(kind: string): string {
  if (kind === 'secretary') return '巡检中'
  if (kind === 'assistant') return '点名等待'
  if (kind === 'rest') return '休息中'
  return ''
}

function decisionTime(ts: number): string {
  if (!ts) return ''
  const diff = Math.max(0, Date.now() / 1000 - ts)
  if (diff < 60) return `${Math.floor(diff)}秒前`
  if (diff < 3600) return `${Math.floor(diff / 60)}分钟前`
  const d = new Date(ts * 1000)
  return `${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`
}

function selectTemplate(id: string | null) {
  viewMode.value = 'config'
  selectedId.value = id
  if (!id) {
    // 全局配置视图：懒加载可编辑数据源
    loadPluginConfig()
    return
  }
  const tpl = templates.value.find((t) => t.id === id)
  if (!tpl) return
  // 初始化编辑模型：按组补全缺失字段
  const model: Record<string, Record<string, unknown>> = {}
  for (const group of GROUPS) {
    model[group.key] = {}
    for (const field of group.fields) {
      model[group.key][field.key] =
        tpl.config?.[group.key]?.[field.key] ?? defaultFor(field.type)
    }
  }
  configModel.value = model
  templateBaseline.value = JSON.stringify(model)
}

// 模板草稿基线：与全局配置共用悬浮保存按钮
const templateBaseline = ref('')
const templateDirty = computed(() => JSON.stringify(configModel.value) !== templateBaseline.value)

// 悬浮保存统一入口与可见性：当前视图有草稿变更即浮出
const configDirty = computed(() => {
  if (viewMode.value !== 'config') return false
  return selectedId.value ? templateDirty.value : settingsDirty.value
})

function saveCurrent() {
  if (selectedId.value) void saveTemplate()
  else void savePluginConfig()
}

// 模板编辑用 schema：从全局 schema 按 fields.ts 的精选分组裁剪出子集
const templateSchema = computed<Record<string, SchemaMeta>>(() => {
  const out: Record<string, SchemaMeta> = {}
  for (const group of GROUPS) {
    const meta = settingsSchema.value[group.key]
    if (meta?.type !== 'object') continue
    const items: Record<string, SchemaMeta> = {}
    for (const field of group.fields) {
      const fieldMeta = meta.items?.[field.key]
      if (fieldMeta) items[field.key] = fieldMeta
    }
    out[group.key] = { ...meta, hint: group.desc, items }
  }
  return out
})

function defaultFor(type: string): unknown {
  if (type === 'bool') return false
  if (type === 'number') return 0
  return ''
}

function openCreateModal() {
  createForm.name = ''
  createForm.mode = 'empty'
  createModalVisible.value = true
}

async function createTemplate() {
  if (!createForm.name.trim()) {
    message.warning('请输入模板名称')
    return false
  }
  try {
    const data = await apiPost<TemplateDetail>('profiles/create', {
      name: createForm.name,
      from_global: createForm.mode === 'global',
    })
    message.success('模板已创建')
    await loadAll()
    selectTemplate(data.id)
    return true
  } catch (e) {
    message.error('创建失败：' + String((e as Error)?.message || e))
    return false
  }
}

function openRenameModal(tpl: TemplateDetail) {
  renameTarget = tpl
  renameForm.name = tpl.name
  renameModalVisible.value = true
}

async function renameTemplate() {
  if (!renameTarget || !renameForm.name.trim()) {
    message.warning('请输入模板名称')
    return false
  }
  try {
    await apiPost('profiles/update', {
      id: renameTarget.id,
      name: renameForm.name.trim(),
    })
    message.success('已保存')
    await loadAll()
    return true
  } catch (e) {
    message.error('重命名失败：' + String((e as Error)?.message || e))
    return false
  }
}

async function deleteTemplate(id: string) {
  try {
    await apiPost('profiles/delete', { id })
    message.success('模板已删除')
    if (selectedId.value === id) selectedId.value = null
    await loadAll()
  } catch (e) {
    message.error('删除失败：' + String((e as Error)?.message || e))
  }
}

async function saveTemplate() {
  if (!currentTemplate.value) return
  saving.value = true
  try {
    await apiPost('profiles/update', {
      id: currentTemplate.value.id,
      description: currentTemplate.value.description,
      config: JSON.parse(JSON.stringify(configModel.value)),
    })
    templateBaseline.value = JSON.stringify(configModel.value)
    message.success('配置已保存')
  } catch (e) {
    message.error('保存失败：' + String((e as Error)?.message || e))
  } finally {
    saving.value = false
  }
}

function bindingOptions(_chatId: string) {
  return [
    { label: '全局配置（默认）', value: '' },
    ...templates.value.map((t) => ({ label: t.name, value: t.id })),
  ]
}

async function setChatBinding(chatId: string, templateId: string) {
  try {
    await apiPost('bindings/set', {
      chat_id: chatId,
      template_id: templateId,
    })
    const item = chats.value.find((c) => c.chat_id === chatId)
    if (item) item.template_id = templateId
    const sitem = statusItems.value.find((c) => c.chat_id === chatId)
    if (sitem) sitem.template_id = templateId
    message.success(templateId ? '已绑定' : '已解除绑定')
  } catch (e) {
    message.error('绑定失败：' + String((e as Error)?.message || e))
    await refreshStatus()
  }
}

async function refreshStatus() {
  if (viewMode.value !== 'monitor') return
  try {
    const list = await apiGet<ChatStatusItem[]>('chat_status')
    statusItems.value = list || []
  } catch {
    // 轮询失败静默，下次再试
  }
}

async function loadAll() {
  try {
    const data = await apiGet<{
      templates: TemplateDetail[]
      bindings: Record<string, string>
      global_config: TemplateConfig
    }>('profiles')
    templates.value = data.templates || []
    const bindingMap = data.bindings || {}
    const chatList = await apiGet<ChatItem[]>('chats')
    // chats 已含绑定，补上仅存在于 bindingMap 的群聊
    const merged = new Map<string, ChatItem>()
    for (const c of chatList || []) merged.set(c.chat_id, c)
    for (const [cid, tid] of Object.entries(bindingMap)) {
      const prev = merged.get(cid)
      if (prev) {
        prev.template_id = tid
      } else {
        merged.set(cid, { chat_id: cid, template_id: tid })
      }
    }
    chats.value = [...merged.values()]
      .sort((a, b) => a.chat_id.localeCompare(b.chat_id))
  } catch (e) {
    message.error('加载失败：' + String((e as Error)?.message || e))
  }
}

onMounted(async () => {
  loadPluginConfig()
  await loadAll()
  if (templates.value.length) selectTemplate(templates.value[0].id)
  await refreshStatus()
  statusTimer = setInterval(refreshStatus, 3000)
})

onUnmounted(() => {
  if (statusTimer) {
    clearInterval(statusTimer)
    statusTimer = null
  }
})
</script>

<style>
.layout {
  display: flex;
  height: 100vh;
}

.sidebar-inner {
  height: 100%;
  /* Liquid Glass · thick */
  background: var(--glass-thick-bg);
  backdrop-filter: blur(40px) saturate(180%);
  -webkit-backdrop-filter: blur(40px) saturate(180%);
  border-right: 1px solid var(--glass-border);
  display: flex;
  flex-direction: column;
}

.sidebar-inner.collapsed .sidebar-header {
  flex-direction: column;
  justify-content: center;
  padding: 10px 8px;
}

.sidebar-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--glass-divider);
}

.brand {
  flex: 1;
  font-size: 15px;
  font-weight: 600;
  color: var(--text-1);
}

/* 左上角主题切换：胶囊玻璃圆钮 */
.theme-toggle {
  flex-shrink: 0;
  width: 34px;
  height: 34px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 17px;
  color: inherit;
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-full);
  background: var(--glass-regular-bg);
  backdrop-filter: blur(24px) saturate(180%);
  -webkit-backdrop-filter: blur(24px) saturate(180%);
  box-shadow:
    inset 0 1px 0 var(--glass-highlight),
    var(--glass-shadow);
  cursor: pointer;
  transition: transform 0.15s ease, background 0.2s ease;
}

.theme-toggle:hover {
  transform: scale(1.06);
}

.theme-toggle:active {
  transform: scale(0.94);
}

.inline-icon {
  width: 13px;
  height: 13px;
  vertical-align: -2px;
  margin-right: 2px;
  color: var(--text-3);
}

.sidebar-scroll {
  flex: 1;
}

.sidebar-footer {
  border-top: 1px solid var(--glass-divider);
}

.list-empty {
  margin-top: 30px;
}

.content {
  flex: 1;
  min-width: 0;
  overflow: hidden;
}

.content-scroll {
  height: 100%;
}

.content-inner {
  max-width: 800px;
  margin: 0 auto;
  padding: 20px 28px;
}

.content-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 12px;
}

.content-header h2 {
  font-size: 18px;
  color: var(--text-1);
}

.content-sub {
  font-size: 12px;
  color: var(--text-3);
}

.status-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 12px;
}

.kind-filter {
  margin-bottom: 12px;
}

.status-card {
  background: var(--glass-regular-bg);
  border: 1px solid var(--glass-border);
  box-shadow: inset 0 1px 0 var(--glass-highlight);
  border-radius: var(--radius-md);
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  transition: border-color 0.2s;
}

.status-card:hover {
  border-color: var(--accent);
}

.status-chat {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-1);
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.status-chat-id {
  font-size: 11px;
  font-weight: 400;
  color: var(--text-3);
  font-family: 'Consolas', 'Courier New', monospace;
}

.status-chat-placeholder {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-3);
}

.status-meta {
  display: flex;
  align-items: center;
  gap: 8px;
}

.status-badge {
  font-size: 11px;
  line-height: 18px;
  padding: 0 8px;
  border-radius: 10px;
}

.status-badge.on {
  background: var(--accent-soft);
  color: var(--accent);
}

.status-badge.off {
  background: var(--glass-divider);
  color: var(--text-3);
}

.status-energy {
  font-size: 12px;
  color: var(--text-2);
}

.status-line {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 12px;
}

.status-label {
  color: var(--text-3);
  min-width: 52px;
  flex-shrink: 0;
}

.status-value {
  color: var(--text-2);
  line-height: 1.5;
  word-break: break-all;
}

.status-binding {
  margin-top: 2px;
}

/* 悬浮保存按钮：右下角浮出，带玻璃阴影 */
.save-fab {
  position: fixed;
  right: 28px;
  bottom: 28px;
  z-index: 100;
  box-shadow: var(--glass-shadow);
}

.fab-enter-active,
.fab-leave-active {
  transition: opacity 0.25s ease, transform 0.25s ease;
}

.fab-enter-from,
.fab-leave-to {
  opacity: 0;
  transform: translateY(14px) scale(0.92);
}

.create-form {
  padding-top: 12px;
}

/* n-menu 模板项的行内操作钮：hover 行时才出现（render 函数节点无 scoped 标记，需全局样式） */
.tpl-label {
  display: flex;
  align-items: center;
  width: 100%;
  min-width: 0;
}

.tpl-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-align: left;
}

.tpl-actions {
  display: none;
  align-items: center;
  gap: 2px;
  margin-left: auto;
  flex-shrink: 0;
}

.n-menu-item-content:hover .tpl-actions {
  display: flex;
}
</style>
