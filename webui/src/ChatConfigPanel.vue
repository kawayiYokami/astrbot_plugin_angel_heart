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
          <div v-if="!sidebarCollapsed" class="brand">群聊独立配置</div>
          <n-button
            v-if="!sidebarCollapsed"
            size="small"
            type="primary"
            @click="openCreateModal"
          >
            <template #icon><Icon icon="lucide:plus" /></template>
            新建模板
          </n-button>
          <n-button
            v-else
            size="small"
            type="primary"
            circle
            title="新建模板"
            @click="openCreateModal"
          >
            <template #icon><Icon icon="lucide:plus" /></template>
          </n-button>
        </div>
        <div
          class="monitor-card"
          :class="{ active: viewMode === 'monitor' }"
          @click="switchMonitor"
          :title="sidebarCollapsed ? '联系人监控' : undefined"
        >
          <div class="nav-item">
            <Icon icon="lucide:activity" class="nav-icon" />
            <div v-if="!sidebarCollapsed">
              <div class="global-name">联系人监控</div>
              <div class="global-desc">群聊与私聊的在场状态、巡检与最近决策</div>
            </div>
          </div>
        </div>
        <div
          class="global-card"
          :class="{ active: viewMode === 'config' && !selectedId }"
          @click="selectTemplate(null)"
          :title="sidebarCollapsed ? '全局配置（默认）' : undefined"
        >
          <div class="nav-item">
            <Icon icon="lucide:settings" class="nav-icon" />
            <div v-if="!sidebarCollapsed">
              <div class="global-name">全局配置（默认）</div>
              <div class="global-desc">未绑定群聊使用此配置</div>
            </div>
          </div>
        </div>
        <n-scrollbar class="sidebar-scroll">
          <div class="template-list">
            <div
              v-for="tpl in templates"
              :key="tpl.id"
              class="template-item"
              :class="{ active: viewMode === 'config' && selectedId === tpl.id }"
              @click="selectTemplate(tpl.id)"
              :title="sidebarCollapsed ? tpl.name : undefined"
            >
              <div class="template-title">
                <Icon icon="lucide:file-text" class="template-icon" />
                <span v-if="!sidebarCollapsed" class="template-name">{{ tpl.name }}</span>
              </div>
              <div v-if="!sidebarCollapsed" class="template-desc">
                {{ tpl.description || '无描述' }}
                <span v-if="bindingCount(tpl.id)" class="binding-badge">
                  {{ bindingCount(tpl.id) }} 群
                </span>
              </div>
              <div v-if="!sidebarCollapsed" class="template-actions" @click.stop>
                <n-button size="tiny" quaternary @click="openRenameModal(tpl)">
                  <template #icon><Icon icon="lucide:pencil" /></template>
                  重命名
                </n-button>
                <n-popconfirm @positive-click="deleteTemplate(tpl.id)">
                  <template #trigger>
                    <n-button size="tiny" quaternary type="error">
                      <template #icon><Icon icon="lucide:trash-2" /></template>
                      删除
                    </n-button>
                  </template>
                  删除后绑定它的群聊将回退到全局配置
                </n-popconfirm>
              </div>
            </div>
            <n-empty
              v-if="!templates.length"
              size="small"
              description="还没有模板，点击右上角新建"
              class="list-empty"
            />
          </div>
        </n-scrollbar>
        <div class="sidebar-footer">
        <a
          v-if="!sidebarCollapsed"
          href="https://github.com/kawayiYokami/astrbot_plugin_angel_heart"
          target="_blank"
          rel="noopener"
          class="footer-link"
        >⭐ Star</a>
        <a
          v-if="!sidebarCollapsed"
          href="https://github.com/kawayiYokami/astrbot_plugin_angel_heart/issues/new"
          target="_blank"
          rel="noopener"
          class="footer-link"
        >🐛 Issues</a>
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

      <!-- 全局配置概览 -->
      <template v-else-if="!selectedId">
        <div class="content-header">
          <h2>全局配置</h2>
          <span class="content-sub">未绑定群聊使用的默认配置，在 AstrBot 插件设置中修改</span>
        </div>
        <n-tabs type="line" animated>
          <n-tab-pane v-for="group in GROUPS" :key="group.key" :name="group.key" :tab="group.label">
            <div class="field-list">
              <div v-for="field in group.fields" :key="field.key" class="field-row">
                <div class="field-label">{{ field.label }}</div>
                <div class="field-value">
                  <span v-if="field.type === 'bool'">
                    {{ globalConfig?.[group.key]?.[field.key] ? '开启' : '关闭' }}
                  </span>
                  <span v-else>
                    {{ displayValue(globalConfig?.[group.key]?.[field.key]) }}
                  </span>
                </div>
                <div v-if="field.hint" class="field-hint">{{ field.hint }}</div>
              </div>
            </div>
          </n-tab-pane>
        </n-tabs>
      </template>

      <!-- 模板配置编辑 -->
      <template v-else>
        <div class="content-header">
          <h2>{{ currentTemplate?.name }}</h2>
          <div class="content-actions">
            <n-button size="small" type="primary" :loading="saving" @click="saveTemplate">
              <template #icon><Icon icon="lucide:save" /></template>
              保存
            </n-button>
          </div>
        </div>
        <n-input
          v-model:value="currentTemplate.description"
          size="small"
          placeholder="模板描述（可选）"
          class="desc-input"
        />
        <n-tabs type="line" animated>
          <n-tab-pane v-for="group in GROUPS" :key="group.key" :name="group.key" :tab="group.label">
            <div class="field-list">
              <div v-for="field in group.fields" :key="field.key" class="field-row">
                <div class="field-label">{{ field.label }}</div>
                <div class="field-control">
                  <n-switch
                    v-if="field.type === 'bool'"
                    v-model:value="configModel[group.key][field.key]"
                    size="small"
                  />
                  <n-input-number
                    v-else-if="field.type === 'number'"
                    v-model:value="configModel[group.key][field.key]"
                    size="small"
                    :step="field.step || 1"
                    class="number-input"
                  />
                  <n-input
                    v-else
                    v-model:value="configModel[group.key][field.key]"
                    size="small"
                    :type="field.type === 'textarea' ? 'textarea' : 'text'"
                    :autosize="field.type === 'textarea' ? { minRows: 2, maxRows: 6 } : undefined"
                    :placeholder="field.placeholder"
                  />
                </div>
                <div v-if="field.hint" class="field-hint">{{ field.hint }}</div>
              </div>
            </div>
          </n-tab-pane>
        </n-tabs>
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
  </n-layout>
</template>

<script setup lang="ts">
import { computed, inject, onMounted, onUnmounted, reactive, ref } from 'vue'
import {
  NButton,
  NEmpty,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NLayout,
  NLayoutContent,
  NLayoutSider,
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
import {
  GROUPS,
  type ChatItem,
  type ChatStatusItem,
  type TemplateDetail,
  type TemplateConfig,
} from './fields'
import { useBridge } from './composables/useBridge'
import { themeKey } from './theme'

const { apiGet, apiPost } = useBridge()
const message = useMessage()
const { isDark, toggle: toggleTheme } = inject(themeKey)!

const templates = ref<TemplateDetail[]>([])
const globalConfig = ref<TemplateConfig | null>(null)
const chats = ref<ChatItem[]>([])
const statusItems = ref<ChatStatusItem[]>([])
const kindFilter = ref<'all' | 'group' | 'private'>('all')
const viewMode = ref<'config' | 'monitor'>('config')
const selectedId = ref<string | null>(null)
const sidebarCollapsed = ref(false)
const saving = ref(false)
let statusTimer: ReturnType<typeof setInterval> | null = null

const configModel = reactive<Record<string, Record<string, unknown>>>({})
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

function bindingCount(templateId: string): number {
  return chats.value.filter((c) => c.template_id === templateId).length
}

function switchMonitor() {
  viewMode.value = viewMode.value === 'monitor' ? 'config' : 'monitor'
}

function displayValue(v: unknown): string {
  if (v === undefined || v === null || v === '') return '—'
  return String(v)
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
  if (!id) return
  const tpl = templates.value.find((t) => t.id === id)
  if (!tpl) return
  // 初始化编辑模型：按组补全缺失字段
  for (const group of GROUPS) {
    if (!configModel[group.key]) configModel[group.key] = {}
    for (const field of group.fields) {
      configModel[group.key][field.key] =
        tpl.config?.[group.key]?.[field.key] ?? defaultFor(field.type)
    }
  }
}

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

function buildConfig(): TemplateConfig {
  const config: TemplateConfig = {}
  for (const group of GROUPS) {
    config[group.key] = { ...configModel[group.key] }
  }
  return config
}

async function saveTemplate() {
  if (!currentTemplate.value) return
  saving.value = true
  try {
    await apiPost('profiles/update', {
      id: currentTemplate.value.id,
      description: currentTemplate.value.description,
      config: buildConfig(),
    })
    message.success('配置已保存')
    await loadAll()
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
    globalConfig.value = data.global_config || null
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

.sidebar-inner.collapsed .monitor-card,
.sidebar-inner.collapsed .global-card {
  padding: 10px 0;
  display: flex;
  justify-content: center;
}

.sidebar-inner.collapsed .nav-item {
  gap: 0;
}

.sidebar-inner.collapsed .template-list {
  padding: 0 8px 8px;
}

.sidebar-inner.collapsed .template-item {
  padding: 10px 0;
  display: flex;
  justify-content: center;
}

.sidebar-inner.collapsed .template-title {
  justify-content: center;
  width: 100%;
}

.sidebar-inner.collapsed .sidebar-footer {
  justify-content: center;
  padding: 10px 0;
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

.global-card {
  margin: 12px;
  padding: 10px 12px;
  border-radius: var(--radius-md);
  background: var(--glass-regular-bg);
  cursor: pointer;
  border: 1px solid var(--glass-border);
  box-shadow: inset 0 1px 0 var(--glass-highlight);
  transition: all 0.2s;
}

.global-card:hover,
.monitor-card:hover {
  background: var(--glass-thick-bg);
}

.global-card.active,
.monitor-card.active {
  border-color: var(--accent);
  background: var(--accent-soft);
}

.monitor-card {
  margin: 12px 12px 0;
  padding: 10px 12px;
  border-radius: var(--radius-md);
  background: var(--glass-regular-bg);
  cursor: pointer;
  border: 1px solid var(--glass-border);
  box-shadow: inset 0 1px 0 var(--glass-highlight);
  transition: all 0.2s;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
}

.nav-icon {
  width: 18px;
  height: 18px;
  color: var(--text-3);
  flex-shrink: 0;
}

.global-card.active .nav-icon,
.monitor-card.active .nav-icon {
  color: var(--accent);
}

.template-title {
  display: flex;
  align-items: center;
  gap: 6px;
}

.template-icon {
  width: 14px;
  height: 14px;
  color: var(--text-3);
  flex-shrink: 0;
}

.inline-icon {
  width: 13px;
  height: 13px;
  vertical-align: -2px;
  margin-right: 2px;
  color: var(--text-3);
}

.global-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-1);
}

.global-desc {
  font-size: 12px;
  color: var(--text-3);
  margin-top: 2px;
}

.sidebar-scroll {
  flex: 1;
}

.sidebar-footer {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  border-top: 1px solid var(--glass-divider);
}

.footer-link {
  font-size: 12px;
  color: var(--text-3);
  text-decoration: none;
  padding: 4px 8px;
  border-radius: 6px;
  transition: all 0.2s;
}

.footer-link:hover {
  color: var(--text-1);
  background: var(--glass-regular-bg);
}

.template-list {
  padding: 0 12px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.template-item {
  padding: 10px 12px;
  border-radius: var(--radius-md);
  background: var(--glass-regular-bg);
  cursor: pointer;
  border: 1px solid var(--glass-border);
  box-shadow: inset 0 1px 0 var(--glass-highlight);
  transition: all 0.2s;
}

.template-item:hover {
  background: var(--glass-thick-bg);
}

.template-item.active {
  border-color: var(--accent);
  background: var(--accent-soft);
}

.template-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-1);
}

.template-desc {
  font-size: 12px;
  color: var(--text-3);
  margin-top: 2px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.binding-badge {
  background: var(--accent-soft);
  color: var(--accent);
  border-radius: 10px;
  padding: 0 6px;
  font-size: 11px;
  line-height: 16px;
}

.template-actions {
  margin-top: 6px;
  display: flex;
  gap: 4px;
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

.content-actions {
  display: flex;
  gap: 8px;
}

.desc-input {
  margin-bottom: 12px;
  max-width: 420px;
}

.field-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 6px 2px;
}

.field-row {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  flex-wrap: wrap;
}

.field-label {
  width: 140px;
  min-width: 140px;
  font-size: 13px;
  color: var(--text-2);
  line-height: 28px;
}

.field-value {
  font-size: 13px;
  color: var(--text-1);
  line-height: 28px;
  white-space: pre-wrap;
  word-break: break-all;
}

.field-control {
  flex: 1;
  max-width: 480px;
}

.field-hint {
  width: 100%;
  font-size: 12px;
  color: var(--text-3);
  line-height: 1.6;
  padding-left: 156px;
  margin-top: -6px;
  margin-bottom: 4px;
}

.number-input {
  width: 180px;
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

.create-form {
  padding-top: 12px;
}
</style>
