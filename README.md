# 电商经营复盘看板

这是一个 Streamlit 只读经营看板。前端只展示 Supabase PostgreSQL 中的历史数据，不提供 Excel 上传入口。每天的数据由管理员在本地通过 `import_excel.py` 导入数据库。

最护和碧维是不同品类品牌，本看板展示各自经营状态，不做简单输赢对比。千川数据只作为投放补充分析，不默认计入总 GMV，避免和抖店重复。

## 项目结构

```text
.
├── app.py
├── db.py
├── import_excel.py
├── requirements.txt
├── README.md
├── .gitignore
├── .env.example
├── data/
│   ├── .gitkeep
│   └── README.md
├── output/
│   └── .gitkeep
└── sample_data/
    └── .gitkeep
```

## 前端部署

1. GitHub 只上传代码。
2. 不上传真实 Excel。
3. Streamlit Community Cloud 设置 `APP_PASSWORD` 和 `DATABASE_URL`。
4. 前端只展示数据，不提供上传入口。

Streamlit Cloud Secrets 示例：

```toml
APP_PASSWORD = "你的前端访问密码"
DATABASE_URL = "postgresql+psycopg2://postgres:你的数据库密码@db.xxxxxx.supabase.co:5432/postgres"
```

主文件路径：

```text
app.py
```

如果你把整个项目文件夹放进仓库子目录，则主文件路径填写：

```text
ecommerce-visual-dashboard/app.py
```

## Supabase PostgreSQL

在 Supabase 创建项目后，进入 Project Settings -> Database，复制 Connection string。

推荐格式：

```text
postgresql+psycopg2://postgres:你的数据库密码@db.xxxxxx.supabase.co:5432/postgres
```

如果 Supabase 给的是 `postgres://...`，代码会自动转换为 SQLAlchemy 可用格式。

首次导入时，后台脚本会自动创建：

- `upload_batches`
- `daily_metrics`
- `raw_metrics`
- `import_errors`

`daily_metrics` 有唯一约束：

```text
date + brand + platform + channel
```

重复导入同一天、同品牌、同平台、同渠道的数据时，会更新已有数据，不会重复新增。

## 后台导入

1. 本地创建 `.env`：

```text
DATABASE_URL=postgresql+psycopg2://postgres:你的数据库密码@db.xxxxxx.supabase.co:5432/postgres
APP_PASSWORD=你的前端访问密码
```

2. 把 Excel 放进 `data` 文件夹。

3. 安装依赖：

```bash
pip install -r requirements.txt
```

4. 运行导入脚本：

```bash
python import_excel.py
```

5. 导入成功后刷新 Streamlit 网页。

命令行会打印：

- 读取了哪些文件
- 成功解析多少条
- 新增多少条
- 更新多少条
- 失败多少条
- 失败原因

## 本地预览前端

```bash
export APP_PASSWORD="你的前端访问密码"
export DATABASE_URL="postgresql+psycopg2://postgres:你的数据库密码@db.xxxxxx.supabase.co:5432/postgres"
streamlit run app.py
```

前端只读取 `daily_metrics` 并展示：

- 老板首页
- 历史趋势
- 渠道分析
- 自动复盘提醒

如果数据库暂无数据，页面只显示：

```text
暂无历史数据，请管理员在后台导入数据。
```

## 安全注意

- 不要提交 `.env`
- 不要提交真实 Excel
- 不要把 `APP_PASSWORD` 写进代码
- 不要把 `DATABASE_URL` 写进代码
- Streamlit 前端只做轻量密码保护，不是复杂用户权限系统
