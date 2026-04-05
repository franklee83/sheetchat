# sheetchat

`v0.5`

一个基于 Streamlit 的开源表格问答工具，用自然语言查询 `CSV` 和 `Excel` 数据，并结合 `SQLite + SQL + Embedding` 提升表格问答的准确性。

## 截图预览

将应用截图保存到 `docs/images/sheetchat-home.png` 后，README 会自动显示：

![sheetchat screenshot](docs/images/sheetchat-home.png)

## 特性

- 支持 `CSV`、`XLSX`、`XLS` 文件上传
- 支持 Excel 多 Sheet 自动导入
- 自动将数据持久化到本地 `SQLite`
- 支持 `SQL 精确查询`
- 支持 `语义检索`
- 支持 `混合查询`
- 支持 OpenAI-compatible 接口
- 默认适配 DashScope / Qwen 配置

## 适用场景

- 销售报表问答
- 订单台账查询
- 财务明细筛选与统计
- Excel 多表快速分析
- 用自然语言代替手写 SQL 的轻量数据助手

## 默认模型配置

- Chat Model: `qwen-plus`
- Embedding Model: `text-embedding-v3`
- Base URL: `https://dashscope.aliyuncs.com/compatible-mode/v1`

## 快速开始

1. 创建虚拟环境

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. 安装依赖

```bash
python3 -m pip install -r requirements.txt
```

3. 启动应用

```bash
python3 -m streamlit run sheetchat.py
```

4. 在左侧导航栏完成配置

- 上传 CSV / Excel 文件
- 选择查询模式
- 在“配置”中填写 API Key、Base URL、聊天模型、Embedding 模型

## 配置方式

应用支持两种配置方式：

- 推荐：在侧边栏“配置”中直接填写
- 可选：使用 `.env` 提供默认值

如果你想使用 `.env`，先复制示例文件：

```bash
cp .env.example .env
```

然后填写：

```dotenv
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
CHAT_MODEL=qwen-plus
EMBEDDING_MODEL=text-embedding-v3
```

`.env` 中的值会作为界面默认值预填。

## 项目结构

```text
sheetchat.py
app/
  config.py
  clients.py
  storage.py
  query.py
  ui.py
data/
```

SQLite 数据库默认位置：

```text
data/app_data.db
```

## 技术方案

- 前端与交互：`Streamlit`
- 数据导入：`pandas` / `openpyxl`
- 本地存储：`SQLite`
- LLM 接入：`openai-python` 兼容接口
- 查询策略：
  - `SQL 精确查询` 适合筛选、聚合、排序、统计
  - `语义检索` 适合模糊理解和上下文召回
  - `混合查询` 先尝试 SQL，再补充语义检索

## 当前版本

`v0.5`

当前版本已支持：

- 表格上传与预览
- CSV / Excel 导入
- 本地持久化
- SQL 可视化展示
- 侧边栏配置
- 开源发布基础文件

## 注意事项

- 如果没有填写 API Key，自然语言问答和 embedding 不会工作
- 首次上传文件时会导入 SQLite，并尝试生成 embedding
- 如果 embedding 失败，SQL 查询仍然可以继续使用
- 对于统计、筛选、聚合类问题，优先使用 `SQL 精确查询` 或 `混合`

## 开源协议

本项目使用 [`MIT License`](LICENSE)。
