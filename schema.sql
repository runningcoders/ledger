-- ledger schema：表结构 + 预置分类 seed
-- 口径来源：《记账应用前后端架构设计与技术栈选型 v1.0》（artifact 01a13856）§3/§12.6
-- 数据库：DuckDB 单文件 ~/.ai_orz/data/personal/ledger.duckdb（隐私红线：不入仓库不入平台库）
-- 全部示例与 seed 数据均为脱敏虚构（隐私红线）。
-- 演进约定：个人场景以 ALTER 为主，禁止改写历史建表语义（对应平台「迁移只增不改」精神）。

-- 建表顺序：被引用表先行（categories → accounts → transactions → tags → transaction_tags → budgets）。

-- E2 分类（两级树；R6 两级封顶 + 预置集不可删只可停用）
CREATE TABLE IF NOT EXISTS categories (
  id         VARCHAR PRIMARY KEY,
  name       VARCHAR NOT NULL,
  parent_id  VARCHAR REFERENCES categories(id),
  direction  VARCHAR NOT NULL CHECK (direction IN ('expense','income')),
  enabled    BOOLEAN NOT NULL DEFAULT TRUE,
  is_preset  BOOLEAN NOT NULL DEFAULT FALSE,
  sort       INTEGER NOT NULL DEFAULT 0,
  -- UNIQUE (parent_id, name)：DuckDB 对含 NULL 的 UNIQUE 不去重，同父重名由服务层兜底强制
);

-- E3 账户（资金存放处；余额派生计算，不落冗余列）
CREATE TABLE IF NOT EXISTS accounts (
  id              VARCHAR PRIMARY KEY,
  name            VARCHAR NOT NULL UNIQUE,
  type            VARCHAR NOT NULL CHECK (type IN ('cash','debit','credit','virtual')),
  currency        VARCHAR NOT NULL DEFAULT 'CNY',
  initial_balance DECIMAL(18,2) NOT NULL DEFAULT 0,
  enabled         BOOLEAN NOT NULL DEFAULT TRUE,
  created_at      TIMESTAMP NOT NULL DEFAULT current_timestamp
);

-- E1 交易（核心实体；对齐本体 event 语义，occurred_at 对齐 event_time「业务口径时间」）
CREATE TABLE IF NOT EXISTS transactions (
  id                  VARCHAR PRIMARY KEY,
  type                VARCHAR NOT NULL
                      CHECK (type IN ('expense','income','transfer')),
  amount              DECIMAL(18,2) NOT NULL CHECK (amount > 0),
  currency            VARCHAR NOT NULL DEFAULT 'CNY',
  category_id         VARCHAR REFERENCES categories(id),
  account_id          VARCHAR NOT NULL REFERENCES accounts(id),
  counter_account_id  VARCHAR REFERENCES accounts(id),
  occurred_at         TIMESTAMP NOT NULL,
  note                VARCHAR,
  in_sum              BOOLEAN NOT NULL DEFAULT TRUE,
  status              VARCHAR NOT NULL DEFAULT 'active'
                      CHECK (status IN ('active','voided')),
  created_at          TIMESTAMP NOT NULL DEFAULT current_timestamp,
  updated_at          TIMESTAMP NOT NULL DEFAULT current_timestamp,
  loc                 VARCHAR,  -- LocationInfo JSON 串（任务 01a14d06；NULL=无地点，存量零迁移）
  -- R3 声明式兜底：转账一律不计收支（服务层同样强制，双保险）
  CHECK (type <> 'transfer' OR in_sum = FALSE)
);
CREATE INDEX IF NOT EXISTS idx_tx_occurred ON transactions(occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_tx_account  ON transactions(account_id);
CREATE INDEX IF NOT EXISTS idx_tx_category ON transactions(category_id);
CREATE INDEX IF NOT EXISTS idx_tx_status   ON transactions(status);

-- E4 标签（分类之外的横切多值维度）
CREATE TABLE IF NOT EXISTS tags (id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS transaction_tags (
  transaction_id VARCHAR NOT NULL REFERENCES transactions(id),
  tag_id         VARCHAR NOT NULL REFERENCES tags(id),
  PRIMARY KEY (transaction_id, tag_id)
);

-- E5 预算（周期起步固定按月；总预算=NULL 分类 + 分类预算）
CREATE TABLE IF NOT EXISTS budgets (
  id          VARCHAR PRIMARY KEY,
  period      VARCHAR NOT NULL,
  category_id VARCHAR REFERENCES categories(id),
  amount      DECIMAL(18,2) NOT NULL CHECK (amount > 0),
  created_at  TIMESTAMP NOT NULL DEFAULT current_timestamp,
  UNIQUE (period, category_id)
);

-- 转账建模（§12.7a 拍板：transfer 为 transactions.type 之一，单行记账
-- account_id=转出、counter_account_id=转入，余额派生 SQL 双边处理，不建独立实体）。

-- ============ 预置分类 seed（§12.6 拍板口径：支出 9 类 + 收入 5 类；R6 预置集禁删可停）============
-- id 固定前缀 cat_exp_/cat_inc_；幂等 seed（已存在不覆盖）。
INSERT INTO categories (id, name, parent_id, direction, enabled, is_preset, sort) VALUES
  ('cat_exp_01','餐饮', NULL,'expense',TRUE,TRUE,1),
  ('cat_exp_02','交通', NULL,'expense',TRUE,TRUE,2),
  ('cat_exp_03','购物', NULL,'expense',TRUE,TRUE,3),
  ('cat_exp_04','居住', NULL,'expense',TRUE,TRUE,4),
  ('cat_exp_05','娱乐', NULL,'expense',TRUE,TRUE,5),
  ('cat_exp_06','医疗', NULL,'expense',TRUE,TRUE,6),
  ('cat_exp_07','教育', NULL,'expense',TRUE,TRUE,7),
  ('cat_exp_08','通讯', NULL,'expense',TRUE,TRUE,8),
  ('cat_exp_09','其他', NULL,'expense',TRUE,TRUE,9),
  ('cat_inc_01','工资', NULL,'income',TRUE,TRUE,1),
  ('cat_inc_02','奖金', NULL,'income',TRUE,TRUE,2),
  ('cat_inc_03','理财', NULL,'income',TRUE,TRUE,3),
  ('cat_inc_04','红包', NULL,'income',TRUE,TRUE,4),
  ('cat_inc_05','其他', NULL,'income',TRUE,TRUE,5)
ON CONFLICT DO NOTHING;

-- ============ 默认账户 seed（账户机制改造·AMan 拍板案 B，任务 01a14375）============
-- 幂等 seed（ON CONFLICT DO NOTHING，与分类 seed 同构）；默认账户「现金」type=cash；
-- 固定 id=acc_default_cash：M1 缺省兜底 + services.accounts.ensure_default_account 既有库补种锚点。
INSERT INTO accounts (id, name, type, currency, initial_balance) VALUES
  ('acc_default_cash', '现金', 'cash', 'CNY', 0)
ON CONFLICT DO NOTHING;
