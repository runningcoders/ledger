# frontend/ — React 前端源码（执行单②落位）

【占位说明 · 骨架】本目录当前为空骨架（任务 01a138a2 仓库初始化），React 工程由执行单②（React P0）初始化，本单不预置脚手架。

- 技术栈（已拍板）：Vite + react-ts 模板 + TypeScript + Tailwind v4 + DaisyUI v5 + recharts（架构设计方案 v1.0 §5/§6）
- 初始化（执行单②）：`npm create vite@latest frontend -- --template react-ts`
- 构建输出：vite.config 配 `build.outDir = '../ledger/ui'` → FastAPI 静态托管 ledger/ui/
- 页面落位：src/pages/（P0 流水列表 + 概览卡 + 记一笔表单；P1 统计三件套/预算/账户）——详见仓库根 README.md
- 样式体系：HUD 设计令牌主题平移 theme.css（方案 §9.1，零新增令牌）；react-bits 按需 copy-paste 单组件进 src/components/vendor/（保留 license 头）
