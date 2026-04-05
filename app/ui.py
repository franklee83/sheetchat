import json

import pandas as pd
import streamlit as st
from streamlit_chat import message

from .clients import get_client
from .config import APP_NAME, APP_VERSION, load_config
from .query import conversational_chat, has_embeddings
from .storage import embed_texts, ensure_storage, fetch_dataset_overview, import_dataset


def init_session_state():
    st.session_state.setdefault("history", [])
    st.session_state.setdefault(
        "generated",
        [{"answer": "你好，我是一个表格数据助手，可以回答 CSV / Excel 中的问题。", "sql": None}],
    )
    st.session_state.setdefault("past", ["你好"])
    st.session_state.setdefault("current_dataset_id", None)


def reset_chat(dataset_id):
    if st.session_state.get("current_dataset_id") != dataset_id:
        st.session_state["history"] = []
        st.session_state["generated"] = [{"answer": "已切换到新的数据集，可以开始提问。", "sql": None}]
        st.session_state["past"] = ["你好"]
        st.session_state["current_dataset_id"] = dataset_id


def normalize_generated_item(item):
    if isinstance(item, dict):
        return {"answer": item.get("answer", ""), "sql": item.get("sql")}
    return {"answer": str(item), "sql": None}


def render_sql_card(sql, key_suffix):
    st.markdown(
        """
        <style>
        .sql-card {
            background: linear-gradient(180deg, #fffdf7 0%, #fff7e8 100%);
            border: 1px solid #f1d6a8;
            border-radius: 12px;
            padding: 0.85rem 1rem;
            margin: 0.4rem 0 1rem 0;
            box-shadow: 0 1px 2px rgba(120, 87, 23, 0.08);
        }
        .sql-card-title {
            color: #8a5a12;
            font-size: 0.84rem;
            font-weight: 600;
            margin-bottom: 0.45rem;
        }
        .sql-card pre {
            background: #fffaf0;
            color: #5e4520;
            border: 1px solid #f3dfbb;
            border-radius: 8px;
            padding: 0.8rem;
            overflow-x: auto;
            white-space: pre-wrap;
            word-break: break-word;
            font-size: 0.9rem;
            line-height: 1.5;
            margin: 0;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    escaped_sql = sql.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    st.markdown(
        f"""
        <div class="sql-card" id="sql-card-{key_suffix}">
            <div class="sql-card-title">SQL 查询语句</div>
            <pre>{escaped_sql}</pre>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_sidebar(config):
    st.sidebar.title("导航")
    uploaded_file = st.sidebar.file_uploader("上传 CSV / Excel 文件", type=["csv", "xlsx", "xls"])
    query_mode = st.sidebar.radio(
        "查询模式",
        options=["混合", "SQL 精确查询", "语义检索"],
        help=(
            "混合模式：先尝试 SQL 精确查询，适合大多数场景；\n"
            "SQL 精确查询：适合统计、筛选、排序、汇总、时间区间等结构化问题；\n"
            "语义检索：适合备注、描述、文本理解、模糊问法，但前提是当前数据已经建立 embedding 索引。"
        ),
    )
    with st.sidebar.expander("配置", expanded=False):
        api_base_url = st.text_input(
            label="API Base URL",
            value=config.api_base_url,
            placeholder="例如 DashScope 的 OpenAI 兼容端点",
        )
        api_key = st.text_input(
            label="API Key",
            value=config.api_key,
            placeholder="输入你的 OpenAI-compatible API Key",
            type="password",
        )
        chat_model = st.text_input(
            label="聊天模型",
            value=config.chat_model,
            placeholder="例如 qwen-plus / qwen-max",
        )
        embedding_model = st.text_input(
            label="Embedding 模型",
            value=config.embedding_model,
            placeholder="例如 text-embedding-v3",
        )
        test_embedding = st.button("测试 Embedding 模型", use_container_width=True)
        st.caption("开源场景下建议用户在这里填写自己的 API Key。")
    return api_key, api_base_url, chat_model, embedding_model, query_mode, uploaded_file, test_embedding


def render_embedding_test(api_key, api_base_url, embedding_model):
    if not api_key:
        st.sidebar.warning("请先填写 API Key，再测试 Embedding 模型。")
        return

    try:
        test_client = get_client(api_key, api_base_url)
        vectors = embed_texts(test_client, embedding_model, ["这是一个 embedding 连通性测试。"])
        dimension = len(vectors[0]) if vectors and vectors[0] else 0
        st.sidebar.success(f"Embedding 模型可用，返回向量维度: {dimension}")
    except Exception as exc:
        st.sidebar.error(f"Embedding 模型测试失败: {exc}")


def render_dataset_preview(conn, dataset, tables, query_mode):
    file_name, file_type, created_at = dataset
    dataset_id = st.session_state.get("current_dataset_id")
    embedding_ready = has_embeddings(conn, dataset_id) if dataset_id else False
    st.subheader("当前数据集")
    st.write(
        {
            "file_name": file_name,
            "file_type": file_type,
            "created_at": created_at,
            "query_mode": query_mode,
            "embedding_index": "ready" if embedding_ready else "not_ready",
        }
    )

    for table_name, source_name, row_count, columns_json in tables:
        with st.expander(f"{source_name} -> {table_name} ({row_count} rows)"):
            st.write({"columns": json.loads(columns_json)})
            preview = pd.read_sql_query(f'SELECT * FROM "{table_name}" LIMIT 10', conn)
            st.dataframe(preview, width="stretch")


def render_chat(conn, client, dataset_id, query_mode, chat_model, embedding_model):
    response_container = st.container()
    input_container = st.container()

    with input_container:
        with st.form(key="query_form", clear_on_submit=True):
            user_input = st.text_input(
                "查询",
                placeholder="例如：销售额最高的客户是谁？或者 2024 年 3 月一共有多少订单？",
                key="input",
            )
            submit_button = st.form_submit_button(label="发送")

        if submit_button and user_input:
            try:
                output, sql = conversational_chat(
                    conn=conn,
                    client=client,
                    dataset_id=dataset_id,
                    mode=query_mode,
                    question=user_input,
                    chat_model=chat_model,
                    embedding_model=embedding_model,
                )
            except Exception as exc:
                output = f"查询失败: {exc}"
                sql = None

            st.session_state["history"].append((user_input, output))
            st.session_state["past"].append(user_input)
            st.session_state["generated"].append({"answer": output, "sql": sql})

    with response_container:
        for i in range(len(st.session_state["generated"])):
            generated_item = normalize_generated_item(st.session_state["generated"][i])
            message(st.session_state["past"][i], is_user=True, key=f"{i}_user", avatar_style="big-smile")
            message(generated_item["answer"], key=str(i), avatar_style="thumbs")
            if generated_item["sql"]:
                render_sql_card(generated_item["sql"], key_suffix=i)


def main():
    st.set_page_config(page_title=f"{APP_NAME} - {APP_VERSION}", layout="wide")
    conn = ensure_storage()
    init_session_state()
    config = load_config()

    api_key, api_base_url, chat_model, embedding_model, query_mode, uploaded_file, test_embedding = render_sidebar(config)

    st.title(f"{APP_NAME} - {APP_VERSION}")
    st.caption("支持 CSV、Excel、多 Sheet 导入，数据持久化到本地 SQLite，并支持 SQL + Embedding 混合查询。")

    if test_embedding:
        render_embedding_test(api_key, api_base_url, embedding_model)

    client = get_client(api_key, api_base_url)

    if not uploaded_file:
        st.write("请上传 CSV 或 Excel 文件以继续。")
        st.info("当前版本会把导入后的表数据持久化到本地 SQLite: data/app_data.db")
        return

    try:
        dataset_id, reused, embedding_warning = import_dataset(conn, uploaded_file, client, embedding_model)
        reset_chat(dataset_id)
        dataset, tables = fetch_dataset_overview(conn, dataset_id)

        if reused:
            st.info("检测到相同文件，已复用本地持久化数据。")
        else:
            st.success("文件已导入 SQLite。")
        if embedding_warning:
            st.warning(embedding_warning)

        render_dataset_preview(conn, dataset, tables, query_mode)
        render_chat(conn, client, dataset_id, query_mode, chat_model, embedding_model)
    except Exception as exc:
        st.error(f"文件处理失败: {exc}")
