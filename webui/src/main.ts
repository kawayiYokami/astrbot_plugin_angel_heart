import { createApp } from 'vue'
import { Icon } from '@iconify/vue'
import App from './App.vue'
import { loadThemeMode } from './theme'

// 首帧前同步主题到根节点，避免光暗切换闪烁；App.vue 内 watchEffect 持续维护
document.documentElement.dataset.theme = loadThemeMode()

const app = createApp(App)
app.component('Icon', Icon)
app.mount('#app')
