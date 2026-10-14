import { writeFileSync } from 'node:fs'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, type Plugin } from 'vite'

// 构建后补回后端仓库的 ledger/ui/.gitkeep 占位文件：
// emptyOutDir 会清空产物目录（含该被 git 跟踪的占位文件），closeBundle 原位恢复，
// 保持「ledger/ui/* 忽略、.gitkeep 在库」的仓库约定不被构建破坏。
function keepUiGitkeep(): Plugin {
  return {
    name: 'keep-ui-gitkeep',
    closeBundle() {
      writeFileSync(new URL('../ledger/ui/.gitkeep', import.meta.url), '')
    },
  }
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss(), keepUiGitkeep()],
  server: {
    proxy: {
      // 开发态代理到本地 FastAPI（127.0.0.1:8723），避免跨域；生产态由后端同源托管
      '/api': 'http://127.0.0.1:8723',
    },
  },
  build: {
    // 架构方案 §9.3：构建产物输出到后端静态托管目录 ledger/ui/
    outDir: '../ledger/ui',
    emptyOutDir: true,
  },
})
