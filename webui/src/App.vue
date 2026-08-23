<template>
  <n-config-provider
    :theme="naiveTheme"
    :theme-overrides="overrides"
    :locale="zhCN"
    :date-locale="dateZhCN"
  >
    <n-message-provider>
      <n-dialog-provider>
        <chat-config-panel />
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>

<script setup lang="ts">
import { computed, provide, watchEffect } from 'vue'
import {
  NConfigProvider,
  NDialogProvider,
  NMessageProvider,
  darkTheme,
  dateZhCN,
  zhCN,
} from 'naive-ui'
import ChatConfigPanel from './ChatConfigPanel.vue'
import { buildThemeOverrides, createThemeApi, themeKey } from './theme'

const theme = createThemeApi()
provide(themeKey, theme)

// data-theme 同步到根节点：CSS 变量按 :root[data-theme] 分组，
// 且 NModal 会 teleport 到 body，变量必须挂在 documentElement 上才能覆盖弹窗。
watchEffect(() => {
  document.documentElement.dataset.theme = theme.mode.value
})

const naiveTheme = computed(() => (theme.isDark.value ? darkTheme : null))
const overrides = computed(() => buildThemeOverrides(theme.mode.value))
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

/* ---------- Liquid Glass 设计令牌（iOS 26 基准，与 angel 系列插件同款） ---------- */

:root[data-theme='light'] {
  --bg-base: #f2f2f7;
  --glass-thick-bg: rgba(255, 255, 255, 0.95);
  --glass-regular-bg: rgba(255, 255, 255, 0.6);
  --glass-border: rgba(255, 255, 255, 0.65);
  --glass-highlight: rgba(255, 255, 255, 0.9);
  --glass-shadow: 0 8px 32px rgba(0, 0, 0, 0.08);
  --glass-divider: rgba(60, 60, 67, 0.12);
  --accent: #007aff;
  --accent-soft: rgba(0, 122, 255, 0.12);
  --text-1: #1d1d1f;
  --text-2: #6e6e73;
  --text-3: #8e8e93;
  --radius-xs: 8px;
  --radius-sm: 10px;
  --radius-md: 14px;
  --radius-lg: 20px;
  --radius-full: 999px;
}

:root[data-theme='dark'] {
  --bg-base: #0d0d0f;
  --glass-thick-bg: rgba(60, 60, 64, 0.92);
  --glass-regular-bg: rgba(48, 48, 52, 0.6);
  --glass-border: rgba(255, 255, 255, 0.14);
  --glass-highlight: rgba(255, 255, 255, 0.22);
  --glass-shadow: 0 8px 32px rgba(0, 0, 0, 0.45);
  --glass-divider: rgba(84, 84, 88, 0.4);
  --accent: #0a84ff;
  --accent-soft: rgba(10, 132, 255, 0.18);
  --text-1: #f5f5f7;
  --text-2: #aeaeb2;
  --text-3: #8e8e93;
  --radius-xs: 8px;
  --radius-sm: 10px;
  --radius-md: 14px;
  --radius-lg: 20px;
  --radius-full: 999px;
}

body {
  background: var(--bg-base);
}

/* 环境光斑：给玻璃提供可折射的色彩层。固定在背景，不参与交互。 */
body::before {
  content: '';
  position: fixed;
  inset: 0;
  z-index: 0;
  pointer-events: none;
  background:
    radial-gradient(42% 36% at 12% 8%, rgba(10, 132, 255, 0.16), transparent 70%),
    radial-gradient(38% 32% at 92% 16%, rgba(94, 92, 230, 0.13), transparent 70%),
    radial-gradient(46% 40% at 55% 100%, rgba(255, 55, 95, 0.08), transparent 72%);
}

:root[data-theme='dark'] body::before {
  background:
    radial-gradient(42% 36% at 12% 8%, rgba(10, 132, 255, 0.2), transparent 70%),
    radial-gradient(38% 32% at 92% 16%, rgba(191, 90, 242, 0.14), transparent 70%),
    radial-gradient(46% 40% at 55% 100%, rgba(100, 210, 255, 0.07), transparent 72%);
}

#app {
  position: relative;
  z-index: 1;
}

/* default 型按钮的玻璃胶囊折射层（Button 无 blur token，全局补） */
.n-button.n-button--default-type {
  backdrop-filter: blur(24px) saturate(180%);
  -webkit-backdrop-filter: blur(24px) saturate(180%);
}

/* 弹窗卡片：Liquid Glass 大圆角 */
.n-card,
.n-modal {
  border-radius: var(--radius-lg);
}

.n-card {
  border: 1px solid var(--glass-border);
  box-shadow:
    inset 0 1px 0 var(--glass-highlight),
    var(--glass-shadow) !important;
}
</style>
