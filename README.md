# ledger — DuckDB MCP 个人流水账服务

个人本地记账应用：**MCP server + 本地 Web UI 双入口 · 单业务层 · 单数据文件**。
Agent 经 ai orz 平台 MCP 底座记账问答，人经浏览器 `http://127.0.0.1:8723` 查账管账，
两条入口共享同一业务层与同一本地 DuckDB 数据文件，业务口径永不漂移。

- **项目**：DuckDB MCP 个人流水账服务自研（B 档），平台项目 01a1362a
- **技术栈**：Python 3.10+（官方 MCP SDK FastMCP + FastAPI + duckdb + pydantic，uv 托管工具链）+ React（Vite + TS + Tailwind v4 + DaisyUI v5 + recharts）
- **错误契约**：REST 侧 HTTP 状态码 + `{error:{code,message}}`；MCP 侧 `isError` 语义，同一 message 文案源 `ledger/errors.py`
- **隐私红线**：原始流水明细只存本地 `.duckdb`，不进 git、不落平台库、不入任何上报/日志/示例；文档与测试示例一律脱敏虚构（本 README 所有示例均为占位符）

---

## 快速开始（5 步）

```bash
# 1) 安装依赖
scripts/setup_env.sh

# 2) 构造 LEDGER_CREDENTIAL 凭证（输出一行 base64 串，复制 export）
LEDGER_USER_ID=<你的UID> scripts/make_credential.sh

# 3) MCP stdio 冒烟（验证凭证+握手+8 工具清单；恒用临时库，零触碰真实账本）
scripts/smoke_mcp_stdio.sh

# 4) 启动本地 UI（默认 http://127.0.0.1:8723，回环绑定）
export LEDGER_CREDENTIAL='<第 2 步输出整行>'
scripts/serve_ui.sh

# 5) 接入 ai orz 平台（管理员操作）：凭证页录 platform=ledger 通用凭证（generic_token，
#    值=第 2 步凭证串）；MCP 服务器页注册 Stdio server：
#    command=<uv 绝对路径>，args=["run","--directory","<仓库绝对路径>","python","-m","ledger"]，
#    并在 credential_requirements 声明 env 绑定名 LEDGER_CREDENTIAL。
```

## 环境准备与依赖安装

```bash
# 推荐 uv（官方 Python 工具链管理器）
curl -LsSf https://astral.sh/uv/install.sh | sh   # 或 brew install uv
scripts/setup_env.sh    # = uv sync + 依赖自检（mcp/fastapi/duckdb/pydantic import 验证）
```

依赖清单（`pyproject.toml`）：`mcp>=1.2.0,<2.0.0`（官方 SDK FastMCP 面）/ `fastapi` / `uvicorn` / `duckdb` / `pydantic`；dev 组 `pytest` / `httpx`。

## LEDGER_CREDENTIAL 凭证配置

**格式**：结构化 JSON 的标准 base64 编码单行串（RFC 4648；`v=1` 当前唯一支持版本）：

```json
{"v":1,"user_id":"<你的UID>","username":"<可选，当前版本零消费>"}
```

**构造**（脚本自动回验六步校验链，口径与服务端一致）：

```bash
LEDGER_USER_ID=<你的UID> scripts/make_credential.sh
# 等价手工：echo '{"v":1,"user_id":"<你的UID>","username":"<可选>"}' | base64
```

**脱敏要求**：凭证串等价于用户身份，勿提交仓库、勿写入日志、勿在文档示例明文（一律 `<你的UID>` 占位符）；平台侧经 MCP 凭证机制加密存储注入，本机手工使用时以 env 方式设置。

**六步校验链**（`ledger/db.py parse_credential`；任一步失败 fail-fast：stderr 一行 `credential_error code=...` + exit 2，不降级、不回落默认用户，MCP/REST 双入口零豁免）：

| # | 校验 | 失败 code |
|---|------|-----------|
| 1 | env 非空 | `env_missing` |
| 2 | base64 严格解码（validate=True） | `base64_invalid` |
| 3 | 解码产物 UTF-8 | `json_invalid` |
| 4 | JSON 对象 | `json_invalid` |
| 5 | `v == 1`（缺失/非整数/未知版本拒绝） | `version_unknown` |
| 6 | `user_id` 必填非空且匹配 `^[A-Za-z0-9_-]+$`（路径注入/穿越结构性排除） | `user_id_missing` / `user_id_invalid` |

## LEDGER_DB_PATH 数据文件位置

两级规则（`ledger/db.py db_path()`；无旧路径回退分支）：

1. `LEDGER_DB_PATH` 显式配置**优先**（调试/测试用）；
2. 未配置 → 按用户拼 `~/.ai_orz/data/users/{uid}/personal/ledger.duckdb`（uid 取自凭证）。

单文件 + WAL；首启自动建库并执行 `schema.sql` 幂等 seed（预置分类 14 条 + 默认账户「现金」）。`.gitignore` 已兜底 `*.duckdb`/`*.wal`，原始明细永不入库。

## 启动方式（双入口）

```bash
# MCP server（ai orz 平台接入形态：Stdio per-call 短命进程）
uv run --directory <仓库绝对路径> python -m ledger
# 平台注册 args 对应：["run","--directory","<仓库绝对路径>","python","-m","ledger"]

# 本地 UI 服务（REST + React 静态托管，仅绑定回环 127.0.0.1:8723）
uv run --directory <仓库绝对路径> python -m ledger serve
scripts/serve_ui.sh      # 等价封装：凭证已设=单用户模式启动 / 未设=总管模式启动
```

地址与端口：`LEDGER_HOST`（默认 `127.0.0.1`）/ `LEDGER_PORT`（默认 `8723`）env 可配；**默认只绑定回环地址**，不对外网暴露。

**入口分流（serve 特有）**：MCP 入口恒要求凭证（未设/非法 → fail-fast exit 2，零豁免）；serve 入口按 `LEDGER_CREDENTIAL` 分流——**已设=单用户模式**（现行为不变，非法凭证同样 fail-fast）；**未设=总管只读模式**（见下节）。

## 总管只读模式（serve 入口 · LEDGER_CREDENTIAL 未设）

本机多用户总管形态：无凭证启动 = 只读平台，读端点聚合返回**全部已注册用户**数据；写操作需请求级凭证解锁。

- **已注册用户判定锚**：`~/.ai_orz/data/users/<uid>/personal/ledger.duckdb` 文件存在（uid 白名单 `^[A-Za-z0-9_-]+$`）；发现过程只读零副作用（不建目录不建库）。
- **读端点**：`uid` 查询参数可选——不传=聚合全部已注册用户（空集=空聚合非报错；损坏库自动跳过 stderr 留痕）；传 uid=锁定单用户库（非法/不存在 uid=404）。单用户模式下 uid 参数被忽略（向后兼容）。
- **写端点（恰 9 个）**：要求请求头 `X-Ledger-Credential: <结构化 base64 凭证串>`（与 env 同构，六步校验链复用；**不经 URL** 防访问日志泄漏）；缺失/无效 = HTTP 401；写目标一律由凭证解析（贴他人凭证写不进别人的库——目标库强校验兜底）。前端顶栏「解锁写入」弹窗粘贴即可（凭证仅驻会话，浏览器关闭即清）。
- **健康检查**：`/api/health` 返回 `mode`（single/manager）与 `user`；`/api/v1/users` 返回已注册 uid 清单（单用户模式恒空）。
- **安全口径**：无凭证读全量 = 本机可访问该服务者均可读全部账本（风险等价文件系统读权限）；默认回环绑定 + 非回环启动警示 + 凭证走请求头 + 不建令牌门（AMan 拍板⑤采认）；局域网暴露场景请自担配置责任。

## MCP 工具契约（8 件，FastMCP 注册于 ledger/mcp_server.py）

按名传参（也接受 UUID 直传）；金额一律字符串两位小数（如 "12.50"，必须 >0）。

| 工具 | 用途 | 关键参数与语义要点 |
|------|------|--------------------|
| `add_transaction` | 记一笔账 | `txn_type`(expense/income/transfer) 必填；`amount` 必填；`category` 必填语义（按名，支出/收入同名时按方向消歧；**转账不挂分类**）；`account` 缺省→默认账户「现金」；显式新账户名→自动建档 type=virtual；转账 `counter_account` 必填且双边语义（单行：account=转出、counter_account=转入），不计收支；`occurred_at` ISO 字符串缺省=当下；`in_sum` 是否计入收支（默认 true）；`tags` 标签名列表不存在即创建；`location` 地点结构体（LocationInfo 9 键全可选，见下节） |
| `update_transaction` | 改账 | 仅传需要修改的字段，未传字段保持不变；作废流水不可修改；改为 transfer 时自动清空分类并强制不计收支（R3）；`location` 缺省/None=不变更 |
| `void_transaction` | 作废 | 不物理删除（status 翻转保留可追溯）；重复作废报错 |
| `get_transaction` | 查单笔 | 按 txn_id 查详情 |
| `list_transactions` | 查流水 | 时间倒序分页；组合筛选：时间段（ISO 或 YYYY-MM-DD）+ 类型 + 分类（含子分类）+ 账户（含转账双侧）+ 标签 + 关键词（匹配备注与分类名）+ 金额区间；返回 `{items, total}`；默认不含作废（include_voided=true 时含）；**账户筛选名不存在→空集非报错**；地点过滤 `loc_city`/`loc_type`/`loc_biz`/`loc_poi_id` 精确 + `loc_keyword` 模糊（**任一地点过滤参数生效时强制排除作废流水，include_voided 仅在未使用地点过滤时生效**） |
| `summarize` | 区间收支统计 | 排除转账与不计收支项；`group_by`=category/account/tag/day/month；`direction` 可只看收入或支出；返回各组 `{key, name, income, expense, net}`；group_by 另支持地点维度 loc_city/loc_adcode/loc_type/loc_biz（NULL 归「未标注地点」组） |
| `account_balances` | 余额概览 | 各账户当前余额派生实时计算（含转账双边与不计收支项的资金变动）；返回 `{"accounts":[...]}` |
| `budget_status` | 预算状态 | 缺省=当月；总预算+各分类预算的 已用/剩余/三态（normal / near_limit 临近超支≥80% / over 超支≥100%） |
| `list_categories` | 查在档分类 | 写入前先查后选（无合适分类再向 add/update 传新名称，写入时自动建档）；`direction` 可只看 expense/income 分类；`include_disabled=true` 时含已停用分类（默认仅启用）；返回 `{"categories":[...]}`（id/name/parent_id/direction/enabled/is_preset/sort） |

### 默认账户机制（AMan 拍板案 B）

- schema.sql 幂等 seed 默认账户「现金」（type=cash，id=acc_default_cash）；
- `add_transaction` 缺省 `account` → 直接记到「现金」；
- 显式指定不存在的账户名（写入面 add/update）→ get-or-create 自动建档（type=virtual，name=传入名）；命中停用账户→报 `account_disabled` 不复活；
- 查询面（list_transactions 账户筛选）账户名不存在→返回空集非报错（只读不建档，防幽灵账户）；
- REST 管理面（/api/v1/accounts）维持强存在性校验不变（显式管理动作保持确定性）。

### 分类写入面 get-or-create（任务 01a14fbd·AMan 拍板①②③④⑤）

- `add_transaction` / `update_transaction` 的 `category` 传入**既有分类名称或 ID**；新名称将自动创建一级分类（direction 继承交易方向，enabled/is_preset/sort 默认值，id=uuid4）；
- 命中既有分类即返回（含停用分类——停用写入仍由写路径校验报 `category_disabled`，不复活不新建）；
- **ID 直传未命中仍严格报 `category_not_found` 不建档**（严格校验保留）；同向重名报 `category_name_taken`（R6 服务层兜底）；
- `list_transactions` 查询面分类筛选维持只读解析不建档（查询面语义对齐账户口径）；
- REST 管理面（/api/v1/categories）维持强校验不变（显式管理动作保持确定性）；改名/停用经 REST `update_category` 管理动作，MCP 工具面本期不暴露。

### 地点信息 LocationInfo（任务 01a14d06·AMan 拍板②修正：结构体统一协议）

账单可挂地点（高德地理语义），**对外（MCP 工具参数）/ 内部（服务层）/ 存储（loc 列 JSON 串）同一套 `LocationInfo` 结构体协议**：

| 字段 | 含义 | 长度上限 |
|------|------|----------|
| `name` | 地点名称（如「虚构咖啡店」） | 128 |
| `city` | 城市 | 64 |
| `point` | 经纬度 `"经度,纬度"`（GCJ-02 原值直存直出零变换） | 仅格式校验（正则见下） |
| `poi_id` | 高德 POI ID | 32 |
| `adcode` | 行政区划编码 | 16 |
| `type` | 地点类型（高德分类，如「餐饮服务;咖啡厅」） | 64 |
| `biz` | 商圈 | 64 |
| `addr` | 地址详情 | 256 |
| `raw` | 原始解析片段（备查） | 256 |

- **三级空值语义**：`location` 整体缺省/None=无地点；部分填充（如仅 `raw`）合法；完整 9 键=完整地点。
- **高德解析编排口径**：`add_transaction` / `update_transaction`（MCP 与 REST 同构）传 `location` 结构体对象；**解析高德 API 响应→装配结构体属调用方（Agent 编排）职责**，本服务不做外部 HTTP 调用、不持高德凭证（调用方自行解析后传入，point 直存原值）。
- **修改语义**：`update_transaction` 不传 `location`=不变更（与既有字段一致）；显式清空地点本期不做。
- **查询统计**：`list_transactions` 增 `loc_city` / `loc_type` / `loc_biz` / `loc_poi_id` 精确过滤与 `loc_keyword`（对名称/地址模糊匹配）；`summarize` 的 `group_by` 增 `loc_city` / `loc_adcode` / `loc_type` / `loc_biz` 四维（NULL 统一归「未标注地点」组）。
- **存量账单零迁移**：loc 列 NULL=无地点；旧库经 `_ensure_columns` 连接时自动补列（幂等）。

### 其他共享语义

- **tags get-or-create**：MCP 写入面标签不存在即创建（与账户 get-or-create 同构先例）；改名走 REST 面。
- **budgets**：周期固定按月；总预算（category=NULL）+ 分类预算；经 REST 面设置（PUT /api/v1/budgets），MCP 面 `budget_status` 只读查询三态。
- **分类 seed 14 条**：支出 9（餐饮/交通/购物/居住/娱乐/医疗/教育/通讯/其他）+ 收入 5（工资/奖金/理财/红包/其他）；预置类禁删只可停用（R6）；分类管理走 REST 面。

## REST 面（本地 UI 后端）

前缀 `/api/v1`（完整契约 = `/openapi.json`，前端 TS 类型经 openapi-typescript 生成）：
health `/api/health`；transactions（POST/GET/PATCH/void）；overview；accounts（GET/POST/PATCH，强校验）；categories（GET/POST/PATCH，预置保护）；tags（GET/PATCH 改名）；budgets（GET/PUT）；stats/summary。
前端构建产物 `ledger/ui/` 存在时由 serve 静态托管（`npm run build` 输出，详见 frontend/README.md）；不存在时 `/` 返回 ui_not_built 提示。

## 常见问题（FAQ）

- **启动报 `credential_error code=env_missing`**：LEDGER_CREDENTIAL 未设置或为空——按上文构造并 export（平台接入时检查 server 注册 credential_requirements 是否声明了 env 绑定 LEDGER_CREDENTIAL）。
- **`code=base64_invalid`**：值不是合法 base64（常见=误粘了构造命令或提示文本；重跑 make_credential.sh 取输出整行）。
- **`code=version_unknown` / `user_id_missing` / `user_id_invalid`**：凭证 JSON 格式不对（须 `{"v":1,"user_id":"..."}`，v=1 当前唯一支持版本；user_id 仅限字母数字下划线连字符）。
- **退出码 2 + stderr 一行结构化错误**：fail-fast 设计行为（凭证链失败），不降级不回落默认用户——修正凭证后重启即可。
- **`db_busy`（503）**：另一入口正持写锁（DuckDB 单文件排他），自动重试 5 次耗尽后报错，稍候重试。
- **MCP 握手失败/工具清单为空**：先跑 `scripts/smoke_mcp_stdio.sh` 分流——本仓库侧 OK 则查平台注册配置（command/args/凭证声明）。
- **「账户不存在：xxx」**：仅 REST 管理面强校验；MCP 写入面已按案 B 自动建档，MCP 查询面返回空集。

## 安全口径

- REST 面默认绑定 `127.0.0.1` 回环，不对外网暴露；`LEDGER_HOST`/`LEDGER_PORT` 仅本机自担配置责任。
- 凭证仅 env 单行传递（MCP stdio 注入子进程 env / 本机 shell export）；平台侧经 MCP 凭证机制加密存储；凭证串等价于用户身份，零明文示例零日志零入库。
- 原始流水明细只落本地 DuckDB 文件（.gitignore 已兜底），不进任何远端。
- MCP 入口启动 fail-fast 零豁免：无凭证不启动、坏凭证不降级；serve 入口分流（凭证已设=单用户 fail-fast / 未设=总管只读放行）。
- 总管模式：无凭证读全量风险已如实知悉（AMan 拍板⑤）；缓解=回环绑定默认+非回环警示+写凭证请求头通道+不建令牌门。

## 目录结构与代码落位表

```
ledger/                                # 仓库根（= uv 项目根，pyproject.toml 在此）
├── pyproject.toml                     # uv 项目定义
├── schema.sql                         # 表结构 + 预置分类 seed（14 条）+ 默认账户 seed（现金）
├── scripts/                           # 使用脚本（本 README 快速开始对应四件）
│   ├── setup_env.sh                   #   环境准备：uv sync + 依赖自检
│   ├── make_credential.sh             #   构造 LEDGER_CREDENTIAL（含六步链回验）
│   ├── smoke_mcp_stdio.sh             #   MCP stdio 冒烟（initialize + tools/list 恰 8 工具）
│   └── serve_ui.sh                    #   本地 UI 启动封装（127.0.0.1:8723）
├── ledger/                            # Python 包（单包双入口）
│   ├── __init__.py
│   ├── __main__.py                    # 入口分发：serve=UI 常驻；默认=MCP stdio；启动 fail-fast 凭证校验
│   ├── mcp_server.py                  # MCP 薄层：FastMCP 工具注册（8 件）+ 语义参数解析
│   ├── api.py                         # REST 薄层：FastAPI 路由 + 静态托管 ledger/ui/（仅 127.0.0.1:8723）
│   ├── db.py                          # 数据层：短连接 + 撞锁重试 + 首启建库 + 凭证六步链 + db_path 两级规则
│   ├── models.py                      # pydantic 模型单一事实源：MCP 参数 / REST 请求体 / 响应契约共用
│   ├── services/                      # ★ 共享业务层（唯一口径）：业务规则只写在这里
│   │   ├── transactions.py            #   记账/改账/作废（作废=status 翻转，禁物理删除）
│   │   ├── accounts.py                #   账户服务 + 余额派生 + 默认账户补种 + get-or-create
│   │   ├── categories.py              #   两级分类树约束 + 预置集保护
│   │   ├── tags.py                    #   标签 CRUD + 交易打标（get-or-create）
│   │   ├── budget.py                  #   预算状态计算（normal / near_limit / over）
│   │   └── stats.py                   #   区间汇总 / 分组统计
│   └── ui/                            # React 构建产物（frontend/ build 输出，不入手编）
├── tests/                             # pytest（业务规则 + 多用户凭证 + 账户机制）
└── frontend/                          # React 前端源码（构建：npm run build → ledger/ui/）
```

**双入口同口径铁律**：MCP 面与 UI REST 面是同一业务语义的两种协议皮，任何记账行为必须走 `services/` 唯一通道——禁止在任一入口内直连 duckdb 写业务逻辑。

## 开发约定

- **工具链**：uv 管理 Python（>=3.10）；Node 仅用于前端构建。
- **测试**：`uv run pytest`（业务规则 R1-R7 + 多用户凭证链 + 账户机制全量覆盖）。
- **提交**：一次交付一笔语义完整 commit，message 写明对应平台任务单；push / remote 须 AMan 另行明确授权，日常零 push。
- **演进约定**：schema 演进以 ALTER 为主，禁止改写历史建表语义；文档/测试示例一律脱敏占位符。
