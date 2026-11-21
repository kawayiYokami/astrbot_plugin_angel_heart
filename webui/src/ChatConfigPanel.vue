<template>
  <!-- 左侧：导航（260px，可折叠到 56px） -->
  <v-navigation-drawer
    :rail="rail"
    permanent
    width="260"
    rail-width="56"
    class="heart-drawer"
    color="transparent"
  >
    <div class="d-flex flex-column h-100">
      <div class="drawer-head pa-2" :class="rail ? 'd-flex flex-column align-center ga-2' : 'd-flex align-center ga-2'">
        <v-btn
          :icon="rail ? 'lucide:panel-left-open' : 'lucide:panel-left-close'"
          variant="text"
          size="small"
          :aria-label="rail ? '展开侧栏' : '收起侧栏'"
          @click="rail = !rail"
        />
        <span v-if="!rail" class="text-subtitle-1 font-weight-medium flex-grow-1 text-truncate">天使之心</span>
        <v-btn
          v-if="!rail"
          color="primary"
          variant="flat"
          size="small"
          prepend-icon="lucide:plus"
          @click="openCreateModal"
        >
          新增
        </v-btn>
        <v-btn
          v-else
          color="primary"
          variant="flat"
          size="small"
          icon="lucide:plus"
          aria-label="新增模板"
          @click="openCreateModal"
        />
      </div>

      <v-divider />

      <v-list class="flex-grow-1 overflow-y-auto py-2" nav bg-color="transparent">
        <v-list-item
          value="monitor"
          :active="viewMode === 'monitor'"
          color="primary"
          @click="onSidebarMenu('monitor')"
        >
          <template #prepend><v-icon icon="lucide:activity" /></template>
          <v-list-item-title v-if="!rail">联系人监控</v-list-item-title>
          <v-tooltip v-if="rail" activator="parent" location="end">联系人监控</v-tooltip>
        </v-list-item>
        <v-list-item
          value="fonts"
          :active="viewMode === 'fonts'"
          color="primary"
          @click="onSidebarMenu('fonts')"
        >
          <template #prepend><v-icon icon="lucide:type" /></template>
          <v-list-item-title v-if="!rail">字体</v-list-item-title>
          <v-tooltip v-if="rail" activator="parent" location="end">字体</v-tooltip>
        </v-list-item>
        <v-list-item
          v-for="tpl in templates"
          :key="tpl.id"
          :value="tpl.id"
          :active="viewMode === 'config' && selectedId === tpl.id"
          color="primary"
          @click="onSidebarMenu(tpl.id)"
        >
          <template #prepend><v-icon icon="lucide:file-text" /></template>
          <v-list-item-title v-if="!rail">{{ tpl.name }}</v-list-item-title>
          <template v-if="!rail" #append>
            <div class="tpl-actions">
              <v-btn
                size="x-small"
                variant="text"
                icon="lucide:pencil"
                :aria-label="'重命名 ' + tpl.name"
                @click.stop="openRenameModal(tpl)"
              />
              <v-btn
                size="x-small"
                variant="text"
                icon="lucide:trash-2"
                color="error"
                :aria-label="'删除 ' + tpl.name"
                @click.stop="askDeleteTemplate(tpl.id, tpl.name)"
              />
            </div>
          </template>
          <v-tooltip v-if="rail" activator="parent" location="end">{{ tpl.name }}</v-tooltip>
        </v-list-item>

        <div v-if="!templates.length && !rail">
          <v-empty-state
            icon="lucide:file-text"
            title="还没有模板"
            text="点上方「新增」建第一个"
            class="pa-2"
          />
        </div>
      </v-list>

      <v-divider />

      <v-list class="py-2" nav bg-color="transparent">
        <v-list-item
          value="settings"
          :active="viewMode === 'config' && !selectedId"
          color="primary"
          @click="selectTemplate(null)"
        >
          <template #prepend><v-icon icon="lucide:settings" /></template>
          <v-list-item-title v-if="!rail">插件设置</v-list-item-title>
          <v-tooltip v-if="rail" activator="parent" location="end">插件设置</v-tooltip>
        </v-list-item>
      </v-list>
    </div>
  </v-navigation-drawer>

  <v-main>
    <div class="content-inner">
      <!-- 联系人监控 -->
      <template v-if="viewMode === 'monitor'">
        <div class="content-header">
          <div>
            <h2 class="text-h4 mb-0">联系人监控</h2>
            <p class="content-sub text-caption text-medium-emphasis mb-0">
              群聊与私聊的在场状态、巡检与最近决定，每 3 秒自动刷新
            </p>
          </div>
          <v-btn variant="text" size="small" prepend-icon="lucide:scroll-text" @click="openRequestDrawer">
            最近请求
          </v-btn>
        </div>

        <v-tabs v-model="kindFilter" color="primary" class="mb-4">
          <v-tab value="all">全部</v-tab>
          <v-tab value="group">群聊</v-tab>
          <v-tab value="private">私聊</v-tab>
        </v-tabs>

        <v-row v-if="filteredStatusItems.length">
          <v-col
            v-for="item in filteredStatusItems"
            :key="item.chat_id"
            cols="12"
            sm="6"
          >
            <v-card rounded="md" class="status-card">
              <v-card-text class="d-flex flex-column ga-2">
                <div class="status-chat">
                  <template v-if="item.display_name">{{ item.display_name }}</template>
                  <span v-else class="status-chat-placeholder">
                    <v-icon icon="lucide:user-x" size="14" /> 未命名
                  </span>
                  <span class="status-chat-id" :title="item.chat_id">{{ chatShortId(item.chat_id) }}</span>
                </div>
                <div class="d-flex align-center ga-2">
                  <v-chip
                    size="small"
                    variant="tonal"
                    :color="item.status.current_status === 'OBSERVATION' ? 'success' : undefined"
                  >
                    {{ item.status.current_status === 'OBSERVATION' ? '在场' : '离场' }}
                  </v-chip>
                  <v-chip v-if="chatKind(item.chat_id)" size="small" variant="tonal">
                    {{ chatKind(item.chat_id) }}
                  </v-chip>
                  <span class="text-caption text-medium-emphasis d-flex align-center ga-1">
                    <v-icon icon="lucide:zap" size="14" /> 能量 {{ fmtEnergy(item.energy) }}
                  </span>
                </div>
                <div class="status-line">
                  <span class="status-label">巡检</span>
                  <span v-if="item.patrol.waiting" class="status-value d-flex align-center ga-1">
                    <v-icon icon="lucide:timer" size="14" />
                    {{ patrolLabel(item.patrol.waiting) }} {{ item.patrol.remaining }}/{{ item.patrol.total }}s
                  </span>
                  <span v-else class="status-value">空闲</span>
                </div>
                <div class="status-line">
                  <span class="status-label">最近决定</span>
                  <span v-if="item.last_decision" class="status-value">
                    {{ item.last_decision.should_reply ? '回复' : '不回' }} ·
                    {{ decisionTime(item.last_decision.decided_at) }} ·
                    {{ item.last_decision.summary || '没留说明' }}
                  </span>
                  <span v-else class="status-value">暂无</span>
                </div>
                <v-select
                  :model-value="item.template_id ?? ''"
                  :items="bindingOptions(item.chat_id)"
                  label="用的模板"
                  variant="outlined"
                  density="compact"
                  hide-details
                  @update:model-value="(v: string) => setChatBinding(item.chat_id, v)"
                />
              </v-card-text>
            </v-card>
          </v-col>
        </v-row>
        <v-empty-state
          v-else
          icon="lucide:activity"
          :title="kindFilter === 'all' ? '还没有联系人' : (kindFilter === 'group' ? '还没有群聊' : '还没有私聊')"
          text="有新消息后才会出现在这里"
        />
      </template>

      <!-- 字体：插件级资源，独立视图 -->
      <template v-else-if="viewMode === 'fonts'">
        <div class="content-header mb-4">
          <div>
            <h2 class="text-h4 mb-0">字体</h2>
            <p class="content-sub text-caption text-medium-emphasis mb-0">
              长文转成图片前，先把字体下好
            </p>
          </div>
        </div>
        <FontPanel
          :dir="fontState.dir"
          :ready="fontState.ready"
          :downloading="fontState.downloading"
          :cooldown-remaining="fontState.cooldown_remaining"
          :fonts="fontState.fonts"
          :fetching="fontFetching"
          :selecting-key="fontSelectingKey"
          :uploading="fontUploading"
          @select="selectFont"
          @fetch="triggerFontFetch"
          @upload="uploadFont"
        />
      </template>

      <!-- 全局配置：schema 驱动分段卡片；有草稿变更时右下角浮出保存按钮 -->
      <template v-else-if="!selectedId">
        <div class="content-header mb-4">
          <div>
            <h2 class="text-h4 mb-0">全局配置</h2>
            <p class="content-sub text-caption text-medium-emphasis mb-0">
              没绑模板的群聊都用这套；和原生插件配置页是同一份数据，保存后即时生效，不用重启
            </p>
          </div>
        </div>
        <v-card rounded="md" style="margin-bottom: 16px;" class="setting-card">
          <v-card-text class="config-section">
            <div class="config-title">系统提示词</div>
            <div class="config-hint">预设和自定义一次只用一种。选预设就用内置的固定文本；选自定义就用下面你写的文本。</div>
          </v-card-text>
          <v-card-text class="pt-0">
            <v-tabs v-model="promptMode" color="primary" class="mb-3">
              <v-tab value="preset">预设</v-tab>
              <v-tab value="custom">自定义</v-tab>
            </v-tabs>
            <template v-if="promptMode === 'preset'">
              <div v-if="promptLoaded" class="preset-text">{{ promptPreset }}</div>
              <div v-else class="d-flex justify-center py-4">
                <v-progress-circular indeterminate color="primary" />
              </div>
            </template>
            <template v-else>
              <v-textarea
                v-model="promptCustom"
                rows="8"
                auto-grow
                placeholder="写你自己的系统提示词"
                variant="outlined"
                density="compact"
                hide-details
              />
              <div class="d-flex ga-2 mt-2">
                <v-btn size="small" variant="text" prepend-icon="lucide:clipboard-copy" @click="promptCustom = promptPreset">
                  填入预设提示词
                </v-btn>
                <v-btn size="small" variant="text" prepend-icon="lucide:sparkles" @click="promptCustom = promptInitial">
                  填入初始提示词
                </v-btn>
              </div>
            </template>
            <div class="d-flex mt-3">
              <v-btn
                color="primary"
                variant="flat"
                size="small"
                prepend-icon="lucide:save"
                :disabled="!promptDirty"
                :loading="promptSaving"
                @click="savePromptSettings"
              >
                保存提示词
              </v-btn>
            </div>
          </v-card-text>
        </v-card>
        <SchemaForm
          v-if="settingsLoaded"
          :schema="settingsSchema"
          v-model:model-value="settingsValues"
          :providers="settingsProviders"
        />
        <div v-else class="d-flex justify-center py-10">
          <v-progress-circular indeterminate color="primary" />
        </div>
      </template>

      <!-- 模板配置编辑：与全局配置共用 SchemaForm 分段卡 -->
      <template v-else>
        <div class="content-header mb-4">
          <div>
            <h2 class="text-h4 mb-0">{{ currentTemplate?.name }}</h2>
          </div>
        </div>
        <SchemaForm
          v-if="settingsLoaded"
          v-model:model-value="configModel"
          :schema="templateSchema"
          :fallback-values="templateDefaults"
          :providers="settingsProviders"
        />
        <div v-else class="d-flex justify-center py-10">
          <v-progress-circular indeterminate color="primary" />
        </div>
      </template>
    </div>

    <!-- 悬浮保存：全局配置或模板草稿变更时浮出 -->
    <Transition name="fab">
      <v-btn
        v-if="configDirty"
        class="save-fab"
        color="primary"
        variant="flat"
        rounded="pill"
        size="large"
        elevation="6"
        prepend-icon="lucide:save"
        :loading="saving || savingSettings"
        @click="saveCurrent"
      >
        保存更改
      </v-btn>
    </Transition>
  </v-main>

  <!-- 最近请求：全局最近 10 条 -->
  <v-navigation-drawer
    v-model="requestDrawerVisible"
    location="right"
    temporary
    :width="640"
    class="req-drawer"
  >
    <div class="d-flex flex-column ga-3 pa-4 h-100 overflow-y-auto">
      <div class="d-flex align-center ga-2">
        <span class="text-h6">最近 10 次请求</span>
        <v-spacer />
        <v-btn icon="lucide:x" variant="text" size="small" aria-label="关闭" @click="requestDrawerVisible = false" />
      </div>
      <p class="text-body-2 text-medium-emphasis mb-0">
        这里记的是插件发出去的请求原文，最多留 10 条，方便排查为什么回了这句。
      </p>
      <div v-if="requestLoading" class="d-flex justify-center py-8">
        <v-progress-circular indeterminate color="primary" />
      </div>
      <v-empty-state
        v-else-if="!requestItems.length"
        icon="lucide:scroll-text"
        title="还没有请求"
        text="插件重载后才会开始记录"
      />
      <div v-else class="d-flex flex-column ga-3">
        <v-card
          v-for="item in requestItems"
          :key="item.seq"
          variant="outlined"
          rounded="control"
          class="req-card"
        >
          <v-card-text class="d-flex flex-column ga-2">
            <div class="d-flex align-center ga-2 flex-wrap text-caption">
              <v-chip size="small" variant="tonal" :color="item.source === 'assistant' ? 'success' : 'info'">
                {{ item.source === 'assistant' ? '助理' : '秘书' }}
              </v-chip>
              <span class="text-medium-emphasis">{{ item.time }}</span>
              <span class="req-chat" :title="item.chat_id">{{ shortChatId(item.chat_id) }}</span>
              <v-chip size="small" variant="tonal" :color="item.pairing.ok ? undefined : 'error'">
                {{ pairingLabel(item) }}
              </v-chip>
            </div>
            <div class="req-json-box">
              <VueJsonPretty :data="item" :deep="5" :show-length="true" />
            </div>
          </v-card-text>
        </v-card>
      </div>
    </div>
  </v-navigation-drawer>

  <!-- 新增模板 -->
  <v-dialog v-model="createModalVisible" max-width="440" persistent>
    <v-card rounded="card">
      <v-card-title class="text-h6">新增配置模板</v-card-title>
      <v-card-text>
        <v-text-field
          v-model="createForm.name"
          label="模板名称"
          placeholder="如：游戏群、学习群"
          variant="outlined"
          density="compact"
          class="mb-4"
        />
        <v-radio-group v-model="createForm.mode" label="初始内容" hide-details>
          <v-radio label="空白模板" value="empty" />
          <v-radio label="照着全局配置抄一份" value="global" />
        </v-radio-group>
      </v-card-text>
      <v-card-actions class="px-4 pb-3">
        <v-spacer />
        <v-btn variant="text" @click="createModalVisible = false">取消</v-btn>
        <v-btn color="primary" variant="flat" @click="onCreateConfirm">创建</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>

  <!-- 重命名模板 -->
  <v-dialog v-model="renameModalVisible" max-width="440" persistent>
    <v-card rounded="card">
      <v-card-title class="text-h6">重命名模板</v-card-title>
      <v-card-text>
        <v-text-field
          v-model="renameForm.name"
          label="模板名称"
          placeholder="模板名称"
          variant="outlined"
          density="compact"
          hide-details
        />
      </v-card-text>
      <v-card-actions class="px-4 pb-3">
        <v-spacer />
        <v-btn variant="text" @click="renameModalVisible = false">取消</v-btn>
        <v-btn color="primary" variant="flat" @click="onRenameConfirm">保存</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import {
  GROUPS,
  type ChatItem,
  type ChatStatusItem,
  type RequestRingResponse,
  type RequestSnapshot,
  type TemplateDetail,
  type TemplateConfig,
} from './fields'
import { useBridge } from './composables/useBridge'
import { toast } from './composables/useToast'
import { confirmDialog } from './composables/useConfirm'
import FontPanel, { type FontState } from './FontPanel.vue'
import { formatMB } from './format'
import SchemaForm from './SchemaForm.vue'
import VueJsonPretty from 'vue-json-pretty'
import 'vue-json-pretty/lib/styles.css'
import type { SchemaMeta } from './schema'

const { apiGet, apiPost, upload } = useBridge()

const templates = ref<TemplateDetail[]>([])
const chats = ref<ChatItem[]>([])
// ---------- 请求快照抽屉：全局最近 10 次 ----------
const requestDrawerVisible = ref(false)
const requestItems = ref<RequestSnapshot[]>([])
const requestLoading = ref(false)
const statusItems = ref<ChatStatusItem[]>([])
const kindFilter = ref<'all' | 'group' | 'private'>('all')
const viewMode = ref<'config' | 'monitor' | 'fonts'>('monitor')
const selectedId = ref<string | null>(null)
const rail = ref(false)
// 窄屏自动收起侧栏，保证可点区域不被挤掉
const narrowMql = window.matchMedia('(max-width: 768px)')
const syncSidebarToWidth = () => {
  rail.value = narrowMql.matches
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
const templateDefaults = ref<TemplateConfig>({})
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

function onSidebarMenu(key: string) {
  stopFontPoll()
  if (key === 'monitor') {
    viewMode.value = 'monitor'
  } else if (key === 'fonts') {
    viewMode.value = 'fonts'
    // 进入即加载一次
    void loadFonts()
  } else {
    selectTemplate(key)
  }
}

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

// ---------- 系统提示词（插件自有存储，不进自带设置页） ----------

const promptMode = ref<'preset' | 'custom'>('preset')
const promptCustom = ref('')
const promptPreset = ref('')
const promptInitial = ref('')
const promptLoaded = ref(false)
const promptSaving = ref(false)
const promptBaseline = ref('')
const promptDirty = computed(
  () =>
    promptLoaded.value &&
    JSON.stringify({ mode: promptMode.value, custom_prompt: promptCustom.value }) !==
      promptBaseline.value,
)

function loadPromptSettings() {
  if (promptLoaded.value) return
  void (async () => {
    try {
      const data = await apiGet<{
        mode: string
        custom_prompt: string
        preset_text: string
        initial_prompt: string
      }>('prompt_settings')
      promptMode.value = data?.mode === 'custom' ? 'custom' : 'preset'
      promptCustom.value = String(data?.custom_prompt ?? '')
      promptPreset.value = String(data?.preset_text ?? '')
      promptInitial.value = String(data?.initial_prompt ?? '')
      promptBaseline.value = JSON.stringify({
        mode: promptMode.value,
        custom_prompt: promptCustom.value,
      })
      promptLoaded.value = true
    } catch (e) {
      toast.error(`加载系统提示词失败：${(e as Error).message}`)
    }
  })()
}

async function savePromptSettings() {
  promptSaving.value = true
  try {
    const data = await apiPost<{
      mode: string
      custom_prompt: string
    }>('prompt_settings/save', {
      mode: promptMode.value,
      custom_prompt: promptCustom.value,
    })
    promptMode.value = data?.mode === 'custom' ? 'custom' : 'preset'
    promptCustom.value = String(data?.custom_prompt ?? '')
    promptBaseline.value = JSON.stringify({
      mode: promptMode.value,
      custom_prompt: promptCustom.value,
    })
    toast.success('提示词已保存')
  } catch (e) {
    toast.error(`保存失败：${(e as Error).message}`)
  } finally {
    promptSaving.value = false
  }
}

// ---------- 字体（插件级资源，放在全局配置里） ----------

const fontState = ref<FontState>({
  dir: '',
  selected: '',
  ready: false,
  downloading: false,
  cooldown_remaining: 0,
  fonts: [],
})
const fontLoading = ref(false)
const fontFetching = ref(false)
const fontSelectingKey = ref<string | null>(null)
const fontUploading = ref(false)

// 切到未下载的字体时后端会起后台任务，这里轮询到下载结束
let fontPollTimer: number | null = null

function stopFontPoll() {
  if (fontPollTimer !== null) {
    clearInterval(fontPollTimer)
    fontPollTimer = null
  }
}

function watchFontDownload() {
  stopFontPoll()
  fontPollTimer = window.setInterval(() => {
    void loadFonts().then(() => {
      if (!fontState.value.downloading) stopFontPoll()
    })
  }, 2000)
}

function applyFontState(data?: Partial<FontState> | null) {
  fontState.value = {
    dir: String(data?.dir ?? ''),
    selected: String(data?.selected ?? ''),
    ready: Boolean(data?.ready),
    downloading: Boolean(data?.downloading),
    cooldown_remaining: Math.ceil(Number(data?.cooldown_remaining ?? 0)),
    fonts: data?.fonts || [],
  }
}

async function loadFonts() {
  if (fontLoading.value) return
  fontLoading.value = true
  try {
    applyFontState(await apiGet<FontState>('fonts'))
    // 下载可能是长回复触发的，进来时已经在跑；这时得把轮询接上，否则面板停在「正在下载」不再动
    if (fontState.value.downloading && fontPollTimer === null) watchFontDownload()
  } catch (e) {
    toast.error(`加载字体状态失败：${(e as Error).message}`)
  } finally {
    fontLoading.value = false
  }
}

// 点一款字体：后端切选中项，未下载的话顺手起下载，这里接着轮询
async function selectFont(key: string) {
  if (fontSelectingKey.value) return
  fontSelectingKey.value = key
  try {
    const data = await apiPost<FontState & { started: boolean }>(
      `fonts/select/${encodeURIComponent(key)}`,
      {},
    )
    applyFontState(data)
    if (data?.started || fontState.value.downloading) {
      toast.info('正在后台下载，下完自动生效')
      watchFontDownload()
    } else if (!fontState.value.ready) {
      toast.warning('这款字体还没下好，可以点「重新下载」再试一次')
    }
  } catch (e) {
    toast.error(`切换字体失败：${(e as Error).message}`)
  } finally {
    fontSelectingKey.value = null
  }
}

async function triggerFontFetch() {
  fontFetching.value = true
  try {
    // 桥接只把 data 解出来，信封里的 message 取不到，所以按 started 判断结果
    const data = await apiPost<{ started: boolean }>('fonts/fetch')
    if (data?.started) {
      toast.success('已开始下载，下完自动生效')
      watchFontDownload()
    } else {
      toast.warning('冷却中，稍后再试')
    }
    await loadFonts()
  } catch (e) {
    toast.error(`触发下载失败：${(e as Error).message}`)
  } finally {
    fontFetching.value = false
  }
}

// 桥接的 endpoint 校验禁止出现 ?，key 只能走路径参数
async function uploadFont(key: string, file: File) {
  if (!/\.ttf$/i.test(file.name)) {
    toast.error('只能上传 .ttf 字体文件')
    return
  }
  fontUploading.value = true
  try {
    const data = await upload<{ key: string; size: number; ready: boolean }>(
      `fonts/upload/${encodeURIComponent(key)}`,
      file,
    )
    toast.success(`已上传（${formatMB(Number(data?.size ?? 0))}），已切换为这款字体`)
    // 后端上传成功后会自动把选中项切到该 key，这里重新拉一次就够
    await loadFonts()
  } catch (e) {
    toast.error(`上传失败：${(e as Error).message}`)
  } finally {
    fontUploading.value = false
  }
}

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
      toast.error(`加载插件设置失败：${(e as Error).message}`)
    }
  })()
}

async function savePluginConfig() {
  savingSettings.value = true
  try {
    await apiPost('plugin_config/save', { values: settingsValues.value })
    settingsBaseline.value = JSON.stringify(settingsValues.value)
    await loadAll()
    toast.success('已保存并即时生效')
  } catch (e) {
    toast.error(`保存失败：${(e as Error).message}`)
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
  stopFontPoll()
  viewMode.value = 'config'
  selectedId.value = id
  if (!id) {
    // 全局配置视图：懒加载可编辑数据源
    loadPluginConfig()
    loadPromptSettings()
    return
  }
  const tpl = templates.value.find((t) => t.id === id)
  if (!tpl) return
  // 缺失字段继续继承全局配置；仅复制显式覆盖，避免保存时将继承值固化。
  configModel.value = JSON.parse(JSON.stringify(tpl.config || {}))
  templateBaseline.value = JSON.stringify(configModel.value)
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

function openCreateModal() {
  createForm.name = ''
  createForm.mode = 'empty'
  createModalVisible.value = true
}

async function onCreateConfirm() {
  if (!createForm.name.trim()) {
    toast.warning('请输入模板名称')
    return
  }
  try {
    const data = await apiPost<TemplateDetail>('profiles/create', {
      name: createForm.name,
      from_global: createForm.mode === 'global',
    })
    toast.success('模板已创建')
    createModalVisible.value = false
    await loadAll()
    selectTemplate(data.id)
  } catch (e) {
    toast.error('创建失败：' + String((e as Error)?.message || e))
  }
}

function openRenameModal(tpl: TemplateDetail) {
  renameTarget = tpl
  renameForm.name = tpl.name
  renameModalVisible.value = true
}

async function onRenameConfirm() {
  if (!renameTarget || !renameForm.name.trim()) {
    toast.warning('请输入模板名称')
    return
  }
  try {
    await apiPost('profiles/update', {
      id: renameTarget.id,
      name: renameForm.name.trim(),
    })
    toast.success('已保存')
    renameModalVisible.value = false
    await loadAll()
  } catch (e) {
    toast.error('重命名失败：' + String((e as Error)?.message || e))
  }
}

async function askDeleteTemplate(id: string, name: string) {
  const ok = await confirmDialog({
    title: `删除「${name}」？`,
    text: '删除后，用这个模板的群聊会用回全局配置',
    confirmText: '删除',
    cancelText: '取消',
    danger: true,
  })
  if (ok) void deleteTemplate(id)
}

async function deleteTemplate(id: string) {
  try {
    await apiPost('profiles/delete', { id })
    toast.success('模板已删除')
    if (selectedId.value === id) selectedId.value = null
    await loadAll()
  } catch (e) {
    toast.error('删除失败：' + String((e as Error)?.message || e))
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
    toast.success('配置已保存')
  } catch (e) {
    toast.error('保存失败：' + String((e as Error)?.message || e))
  } finally {
    saving.value = false
  }
}

function bindingOptions(_chatId: string) {
  return [
    { title: '全局配置（默认）', value: '' },
    ...templates.value.map((t) => ({ title: t.name, value: t.id })),
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
    toast.success(templateId ? '已绑定' : '已解除绑定')
  } catch (e) {
    toast.error('绑定失败：' + String((e as Error)?.message || e))
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

let profilesLoadVersion = 0

// ---------- 请求快照：全局最近 10 次 ----------

async function openRequestDrawer() {
  requestDrawerVisible.value = true
  await loadRequestSnapshots()
}

async function loadRequestSnapshots() {
  requestLoading.value = true
  try {
    const data = await apiGet<RequestRingResponse>('request_ring')
    requestItems.value = data.items || []
  } catch (e) {
    toast.error('读取请求快照失败：' + String((e as Error)?.message || e))
  } finally {
    requestLoading.value = false
  }
}

// 全局队列里会话混在一起，列表里只显示会话尾号，标题保留全文
function shortChatId(chatId: string): string {
  if (!chatId) return '未知会话'
  const segments = chatId.split(':').filter(Boolean)
  return segments[segments.length - 1] || chatId
}

// 监控卡片的会话标识：冒号分隔的原始结构（如 default:GroupMessage:123）不直接显示，
// 只显示尾号，类型解析成「群聊 / 私聊」标签，原文收进 title tooltip 方便排查。
// 类型全集：框架 MessageType 只有 Group/Friend/Other 三种（message_type.py），
// PrivateMessage 是旧写法（后端 is_private_chat 也兼容它）；本地 17 条登记数据
// 只有 GroupMessage×9 + FriendMessage×8，无其他类型。
function chatKind(chatId: string): string {
  if (!chatId) return ''
  const segments = chatId.split(':').filter(Boolean)
  const typeSeg = segments.length >= 2 ? segments[segments.length - 2] : ''
  if (/^(FriendMessage|PrivateMessage)$/i.test(typeSeg)) return '私聊'
  if (/^GroupMessage$/i.test(typeSeg)) return '群聊'
  if (/^OtherMessage$/i.test(typeSeg)) return '其他'
  return ''
}

function chatShortId(chatId: string): string {
  return shortChatId(chatId)
}

function pairingLabel(snapshot: RequestSnapshot): string {
  const p = snapshot.pairing
  if (p.ok) return '配对正常'
  const issues: string[] = []
  if (p.duplicate_result.length) issues.push(`重复回执 ${p.duplicate_result.length}`)
  if (p.missing.length) issues.push(`缺回执 ${p.missing.length}`)
  if (p.orphan.length) issues.push(`无主回执 ${p.orphan.length}`)
  if (p.duplicate_call.length) issues.push(`重复调用 ${p.duplicate_call.length}`)
  return issues.join('、') || '配对异常'
}

async function loadAll() {
  const version = ++profilesLoadVersion
  try {
    const data = await apiGet<{
      templates: TemplateDetail[]
      bindings: Record<string, string>
      global_config: TemplateConfig
    }>('profiles')
    if (version !== profilesLoadVersion) return
    templates.value = data.templates || []
    templateDefaults.value = data.global_config || {}
    const bindingMap = data.bindings || {}
    const chatList = await apiGet<ChatItem[]>('chats')
    if (version !== profilesLoadVersion) return
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
    toast.error('加载失败：' + String((e as Error)?.message || e))
  }
}

onMounted(async () => {
  loadPluginConfig()
  loadPromptSettings()
  await loadAll()
  await refreshStatus()
  statusTimer = setInterval(refreshStatus, 3000)
})

onUnmounted(() => {
  if (statusTimer) {
    clearInterval(statusTimer)
    statusTimer = null
  }
  stopFontPoll()
})
</script>

<style scoped>
.heart-drawer {
  border-right: 1px solid rgba(var(--v-border-color), 0.12);
  /* drawer 默认吃 surface；侧栏必须跟页面同 background，卡片才配浮出 */
  background-color: rgb(var(--v-theme-background)) !important;
}
/* v-list 默认吃 surface；侧栏里的列表必须透明，否则菜单区比空白亮一档 */
.v-application .heart-drawer .v-list {
  background-color: transparent !important;
}
.drawer-head {
  min-height: 56px;
}

/* 模板行的重命名/删除：hover 行时才出现 */
.tpl-actions {
  display: none;
  align-items: center;
  gap: 2px;
  margin-left: auto;
  flex-shrink: 0;
}
.heart-drawer .v-list-item:hover .tpl-actions {
  display: flex;
}

.content-inner {
  max-width: 960px;
  margin: 0 auto;
  padding: 22px 28px 96px;
}
.content-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.content-sub {
  margin-top: 4px;
}

/* ---- 上游 AstrBotConfigV4.vue 行式范式（照抄数值；颜色用 rgb() 包裹主题三元组） ---- */
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

/* 联系人卡片 */
.status-card {
  border-color: rgba(var(--v-border-color), 0.16);
}
.status-chat {
  font-size: 0.875rem;
  font-weight: 500;
  color: rgb(var(--v-theme-primaryText));
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.status-chat-id {
  font-size: 0.75rem;
  font-weight: 400;
  color: rgb(var(--v-theme-secondaryText));
  font-family: Consolas, 'Courier New', monospace;
}
.status-chat-placeholder {
  color: rgb(var(--v-theme-secondaryText));
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.status-line {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 0.75rem;
}
.status-label {
  color: rgb(var(--v-theme-secondaryText));
  min-width: 52px;
  flex-shrink: 0;
}
.status-value {
  color: rgb(var(--v-theme-primaryText));
  line-height: 1.5;
  word-break: break-all;
}

/* 预设提示词正文 */
.preset-text {
  padding: 10px 12px;
  border-radius: 6px;
  background: rgba(var(--v-border-color), 0.08);
  font-size: 0.8125rem;
  line-height: 1.7;
  color: rgb(var(--v-theme-primaryText));
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 360px;
  overflow-y: auto;
}

/* 请求快照 */
.req-chat {
  color: rgb(var(--v-theme-primary));
  font-family: Consolas, 'Courier New', monospace;
}

/* 快照原样交给 json 组件渲染，超长时内部滚动 */
.req-json-box {
  background: rgba(var(--v-border-color), 0.08);
  border-radius: 8px;
  padding: 8px 10px 8px 4px;
  max-height: 56vh;
  overflow: auto;
}
.req-json-box :deep(.vjs-tree) {
  font-family: Consolas, 'Courier New', monospace;
  font-size: 12px;
}
.req-card {
  border-color: rgba(var(--v-border-color), 0.16);
}

.setting-card {
  border-color: rgba(var(--v-border-color), 0.16);
}

.save-fab {
  position: fixed;
  right: 28px;
  bottom: 28px;
  z-index: 100;
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
</style>
