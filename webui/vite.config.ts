import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vuetify from 'vite-plugin-vuetify'
import { resolve } from 'path'

export default defineConfig({
  plugins: [
    vue(),
    // 按需引入组件（不写 import * as components，避免 1.3MB 全量包），
    // styles 指向本地 settings.scss 以复用上游 dashboard 的圆角/字号基线。
    vuetify({
      autoImport: true,
      styles: { configFile: 'src/styles/settings.scss' },
    }),
  ],
  base: './',
  build: {
    outDir: resolve(__dirname, '../pages/chat-config'),
    emptyOutDir: true,
    cssCodeSplit: false,
    rollupOptions: {
      output: {
        // 禁用代码分割，全部打成单文件，避免 iframe CORS 问题
        inlineDynamicImports: true,
        entryFileNames: 'assets/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash][extname]',
      },
    },
  },
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
})
