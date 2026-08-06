<template>
  <div class="layout">
    <!-- 左侧：模板列表 -->
    <aside class="sidebar">
      <div class="sidebar-header">
        <div class="brand">群聊独立配置</div>
        <n-button size="small" type="primary" @click="openCreateModal">
          <template #icon><Icon icon="lucide:plus" /></template>
          新建模板
        </n-button>
      </div>
      <div class="monitor-card" :class="{ active: viewMode === 'monitor' }" @click="switchMonitor">
        <div class="nav-item">
          <Icon icon="lucide:activity" class="nav-icon" />
          <div>
            <div class="global-name">群聊监控</div>
            <div class="global-desc">在场状态、巡检与最近决策</div>
          </div>
        </div>
      </div>
      <div class="global-card" :class="{ active: viewMode === 'config' && !selectedId }" @click="selectTemplate(null)">
        <div class="nav-item">
          <Icon icon="lucide:settings" class="nav-icon" />
          <div>
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
          >
            <div class="template-title">
              <Icon icon="lucide:file-text" class="template-icon" />
              <span class="template-name">{{ tpl.name }}</span>
            </div>
            <div class="template-desc">
              {{ tpl.description || '无描述' }}
              <span v-if="bindingCount(tpl.id)" class="binding-badge">
                {{ bindingCount(tpl.id) }} 群
              </span>
            </div>
            <div class="template-actions" @click.stop>
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
          href="https://github.com/kawayiYokami/astrbot_plugin_angel_heart"
          target="_blank"
          rel="noopener"
          class="footer-link"
        >⭐ Star</a>
        <a
          href="https://github.com/kawayiYokami/astrbot_plugin_angel_heart/issues/new"
          target="_blank"
          rel="noopener"
          class="footer-link"
        >🐛 Issues</a>
      </div>
    </aside>

    <!-- 右侧：配置详情 -->
    <main class="content">
      <n-scrollbar class="content-scroll">
      <div class="content-inner">
      <!-- 群聊监控 -->
      <template v-if="viewMode === 'monitor'">
        <div class="content-header">
          <h2>群聊监控</h2>
          <span class="content-sub">在场状态、巡检与最近决策，每 3 秒自动刷新</span>
        </div>
        <div class="status-grid">
          <div v-for="item in statusItems" :key="item.chat_id" class="status-card">
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
            v-if="!statusItems.length"
            size="small"
            description="暂无群聊（群聊产生消息后才会出现在这里）"
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
    </main>

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
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import {
  NButton,
  NEmpty,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NModal,
  NPopconfirm,
  NRadio,
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

const { apiGet, apiPost } = useBridge()
const message = useMessage()

const templates = ref<TemplateDetail[]>([])
const globalConfig = ref<TemplateConfig | null>(null)
const chats = ref<ChatItem[]>([])
const statusItems = ref<ChatStatusItem[]>([])
const viewMode = ref<'config' | 'monitor'>('config')
const selectedId = ref<string | null>(null)
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
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

html,
body,
#app {
  height: 100%;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC',
    'Microsoft YaHei', sans-serif;
}

.layout {
  display: flex;
  height: 100vh;
  background: #1a1a1f;
}

.sidebar {
  width: 260px;
  min-width: 260px;
  background: #222228;
  border-right: 1px solid #33333a;
  display: flex;
  flex-direction: column;
}

.sidebar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px;
  border-bottom: 1px solid #33333a;
}

.brand {
  font-size: 15px;
  font-weight: 600;
  color: #eee;
}

.global-card {
  margin: 12px;
  padding: 10px 12px;
  border-radius: 8px;
  background: #2a2a31;
  cursor: pointer;
  border: 1px solid transparent;
  transition: all 0.2s;
}

.global-card:hover,
.monitor-card:hover {
  border-color: #44444c;
}

.global-card.active,
.monitor-card.active {
  border-color: #63e2b7;
  background: #2a3a35;
}

.monitor-card {
  margin: 12px 12px 0;
  padding: 10px 12px;
  border-radius: 8px;
  background: #2a2a31;
  cursor: pointer;
  border: 1px solid transparent;
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
  color: #888;
  flex-shrink: 0;
}

.global-card.active .nav-icon,
.monitor-card.active .nav-icon {
  color: #63e2b7;
}

.template-title {
  display: flex;
  align-items: center;
  gap: 6px;
}

.template-icon {
  width: 14px;
  height: 14px;
  color: #888;
  flex-shrink: 0;
}

.inline-icon {
  width: 13px;
  height: 13px;
  vertical-align: -2px;
  margin-right: 2px;
  color: #888;
}

.global-name {
  font-size: 13px;
  font-weight: 600;
  color: #eee;
}

.global-desc {
  font-size: 12px;
  color: #888;
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
  border-top: 1px solid #33333a;
}

.footer-link {
  font-size: 12px;
  color: #888;
  text-decoration: none;
  padding: 4px 8px;
  border-radius: 6px;
  transition: all 0.2s;
}

.footer-link:hover {
  color: #eee;
  background: #2a2a31;
}

.template-list {
  padding: 0 12px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.template-item {
  padding: 10px 12px;
  border-radius: 8px;
  background: #2a2a31;
  cursor: pointer;
  border: 1px solid transparent;
  transition: all 0.2s;
}

.template-item:hover {
  border-color: #44444c;
}

.template-item.active {
  border-color: #63e2b7;
  background: #2a3a35;
}

.template-name {
  font-size: 13px;
  font-weight: 600;
  color: #eee;
}

.template-desc {
  font-size: 12px;
  color: #888;
  margin-top: 2px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.binding-badge {
  background: #3a5a4f;
  color: #63e2b7;
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
  max-width: 920px;
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
  color: #eee;
}

.content-sub {
  font-size: 12px;
  color: #888;
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
  color: #bbb;
  line-height: 28px;
}

.field-value {
  font-size: 13px;
  color: #eee;
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
  color: #888;
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

.status-card {
  background: #2a2a31;
  border: 1px solid transparent;
  border-radius: 8px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  transition: border-color 0.2s;
}

.status-card:hover {
  border-color: #44444c;
}

.status-chat {
  font-size: 13px;
  font-weight: 600;
  color: #eee;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.status-chat-id {
  font-size: 11px;
  font-weight: 400;
  color: #888;
  font-family: 'Consolas', 'Courier New', monospace;
}

.status-chat-placeholder {
  font-size: 13px;
  font-weight: 600;
  color: #666;
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
  background: #2a3a35;
  color: #63e2b7;
}

.status-badge.off {
  background: #33333a;
  color: #888;
}

.status-energy {
  font-size: 12px;
  color: #aaa;
}

.status-line {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 12px;
}

.status-label {
  color: #888;
  min-width: 52px;
  flex-shrink: 0;
}

.status-value {
  color: #ccc;
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
