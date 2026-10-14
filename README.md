# ledger — DuckDB MCP 个人流水账服务

个人本地记账应用：**MCP server + 本地 Web UI 双入口 · 单业务层 · 单数据文件**。Agent 经 ai orz 平台 MCP 底座记账问答，人经浏览器 `http://127.0.0.1:8723` 查账管账，两条入口共享同一业务层与同一本地 DuckDB 文件，业务口径永不漂移。

- **项目**：DuckDB MCP 个人流水账服务自研（B 档）
- **权威设计**：《记账应用前后端架构设计与技术栈选型 v1.0》（artifact 01a13856，平台项目 01a1362a）——本 README 是其 §4 模块划分的落位执行版；业务规则以设计稿/调研报告为准，本文件只管「代码往哪里写」。
- **技术栈**：Python 3.10+（官方 MCP SDK FastMCP + FastAPI + duckdb + pydantic，uv 托管工具链）+ React（Vite + TS + Tailwind v4 + DaisyUI v5 + recharts）。

---

## 目录结构与代码落位表

```
ledger/                                # 仓库根（= uv 项目根，pyproject.toml 在此）
├── pyproject.toml                     # uv 项目定义；依赖在执行单①落码时启用
├── schema.sql                         # 表结构 + 预置分类 seed（执行单①按方案 v1.0 §3 落位）
├── ledger/                            # Python 包（单包双入口）
│   ├── __init__.py
│   ├── __main__.py                    # 入口分发：`python -m ledger serve`=UI 常驻；默认=MCP stdio
│   ├── mcp_server.py                  # MCP 薄层：FastMCP 工具注册（M1-M7）+ 语义参数解析（分类名/账户名→ID）
│   ├── api.py                         # REST 薄层：FastAPI 路由 + 静态托管 ledger/ui/（仅 127.0.0.1:8723）
│   ├── db.py                          # 数据层：短连接（打开→执行→关闭）+ open_with_retry 撞锁重试 + 首启建库
│   ├── models.py                      # pydantic 模型单一事实源：MCP 参数 / REST 请求体 / 响应契约共用
│   ├── services/                      # ★ 共享业务层（唯一口径）：业务规则只写在这里，两个入口都只做薄皮
│   │   ├── transactions.py            #   记账/改账/作废（作废=status 翻转，禁物理删除）
│   │   ├── accounts.py                #   账户 CRUD + 余额派生计算（余额不落冗余列，防口径漂移）
│   │   ├── categories.py              #   两级分类树约束 + 预置集保护（预置类禁删只可停用）
│   │   ├── tags.py                    #   标签 CRUD + 交易打标
│   │   ├── budget.py                  #   预算状态计算（正常 / 临近超支 80% / 超支）
│   │   └── stats.py                   #   区间汇总 / 分组统计（MCP 统计问答与 UI 统计页共用）
│   └── ui/                            # React 构建产物（frontend/ build 输出，构建生成不入手编，.gitkeep 占位）
└── frontend/                          # React 前端源码（执行单② `npm create vite` 初始化，见 frontend/README.md）
```

### 落位规则（新增代码先看这张表）

| 要写什么 | 写到哪里 | 说明 |
|---|---|---|
| 新的记账/查询业务规则、金额口径、校验 | `ledger/services/*.py` | **唯一落点**。R1-R7 规则全在业务层，入口层禁止出现业务逻辑 |
| 新 MCP 工具 | `ledger/mcp_server.py` | 只做「工具签名 + 调 services + 返回」，参数模型从 models.py 取 |
| 新 REST 接口 | `ledger/api.py` | 同上；契约 = FastAPI 自动产出的 OpenAPI schema，前端 TS 类型经 openapi-typescript 生成，不手编 |
| MCP / REST 共用的请求响应结构 | `ledger/models.py` | pydantic 单一事实源；MCP 工具参数与 REST 请求体同一模型 |
| 表结构变更 | `schema.sql` | DuckDB 方言；演进走 ALTER（个人场景）；预置分类 seed 也在本文件 |
| 前端页面/组件 | `frontend/src/pages|components/` | 构建 `build.outDir='../ledger/ui'`；HUD 主题令牌平移进 theme.css（零新增令牌） |
| react-bits 点缀组件 | `frontend/src/components/vendor/` | copy-paste 单组件 + 保留 license 头，不加 CLI 依赖不整包引入 |
| 数据文件 | `~/.ai_orz/data/personal/ledger.duckdb` | **不入仓库**（.gitignore 已兜底 *.duckdb/*.wal）；隐私红线见下 |

**双入口同口径铁律**：MCP 面与 UI REST 面是同一业务语义的两种协议皮，任何记账行为必须走 `services/` 唯一通道——禁止在任一入口内直连 duckdb 写业务逻辑。

---

## 本地运行（执行单①②落码后生效）

```bash
# MCP server（ai orz 平台接入形态：Stdio per-call，平台经 create_mcp_server 注册）
uv run --directory ~/Technology/python/ledger python -m ledger

# 本地 UI 服务（浏览器 http://127.0.0.1:8723）
uv run --directory ~/Technology/python/ledger python -m ledger serve

# 前端开发（执行单②）
cd frontend && npm install && npm run dev    # 构建：npm run build → ledger/ui/
```

## 开发约定

- **工具链**：uv 管理 Python（>=3.10，官方 MCP SDK 要求）；Node 仅用于前端构建。
- **提交**：一次交付一笔语义完整 commit，message 写明对应平台任务单；**push / 远端 remote 须 AMan 另行明确授权**，日常零 push。
- **隐私红线（最高优先级）**：原始流水明细只存本地 `.duckdb`，不进 git、不落平台库、不进任何上报/日志/示例；文档与测试示例一律脱敏虚构数据。
- **测试**：业务层规则（R1-R7）逐条对应测试，随执行单①建立。

## 当前状态

仓库骨架已建（任务 01a138a2）：目录结构 + 占位说明 + 本 README。**全部业务代码尚未落码**，执行顺序：①MCP+业务层+schema 落码 → ②React P0 → ③平台注册接入试点（PMO 编排）。
