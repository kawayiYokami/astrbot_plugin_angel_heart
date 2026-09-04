<template>
  <n-config-provider
    :theme="naiveTheme"
    :theme-overrides="overrides"
    :locale="zhCN"
    :date-locale="dateZhCN"
  >
    <n-global-style />
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
  NGlobalStyle,
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

/* ---------- 设计令牌（与 theme.ts tokens 对齐，供自定义元素使用） ---------- */

:root[data-theme='light'] {
  --bg-base: #F5F5F5;
  --glass-thick-bg: #ffffff;
  --glass-regular-bg: #ffffff;
  --glass-border: rgba(60, 60, 67, 0.12);
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
  --bg-base: #101014;
  --glass-thick-bg: #18181c;
  --glass-regular-bg: #18181c;
  --glass-border: rgba(84, 84, 88, 0.4);
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

#app {
  position: relative;
  z-index: 1;
}

/* 弹窗卡片：大圆角 */
.n-card,
.n-modal {
  border-radius: var(--radius-lg);
}
</style>
