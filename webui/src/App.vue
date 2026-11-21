<template>
  <v-app>
    <ChatConfigPanel />
    <ToastHost />
    <ConfirmHost />
  </v-app>
</template>

<script setup lang="ts">
import ChatConfigPanel from './ChatConfigPanel.vue'
import ToastHost from './components/ToastHost.vue'
import ConfirmHost from './components/ConfirmHost.vue'
</script>

<style>
html,
body,
#app {
  height: 100%;
  margin: 0;
  padding: 0;
}

/* 宿主主题色板变化由 Vuetify 主题样式表驱动，这里只处理 iframe 里的滚动观感 */
html {
  overscroll-behavior: none;
}

::-webkit-scrollbar {
  width: 10px;
  height: 10px;
}

::-webkit-scrollbar-thumb {
  border-radius: 999px;
  background: rgba(var(--v-border-color), 0.24);
}

::-webkit-scrollbar-thumb:hover {
  background: rgba(var(--v-border-color), 0.4);
}

::-webkit-scrollbar-track {
  background: transparent;
}
</style>
