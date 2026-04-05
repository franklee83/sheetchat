import json
import math
import re

import pandas as pd

from .config import MAX_RESULT_ROWS
from .storage import build_schema_prompt, embed_texts


def run_chat_completion(client, model, system_prompt, user_prompt):
    response = client.chat.completions.create(
        model=model,
        temperature=0,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return response.choices[0].message.content.strip()


def generate_sql(client, chat_model, question, schema_text):
    system_prompt = (
        "你是 SQLite 数据分析助手。"
        "你只能输出一条可执行的 SQLite SELECT 或 WITH 查询。"
        "不要输出 markdown、解释、分号之外的额外文字。"
        "涉及表名和列名时使用双引号。"
        "当用户问“某公司/生产商/供应商提供了哪些货物/产品/物料/商品”时，优先使用 LIKE 做模糊匹配，并返回去重后的货品名称。"
        f"返回结果请限制在 {MAX_RESULT_ROWS} 行以内。"
    )
    user_prompt = (
        f"数据库 schema 如下:\n{schema_text}\n\n"
        f"用户问题: {question}\n\n"
        "请生成最准确的 SQLite 查询。"
    )
    sql = run_chat_completion(client, chat_model, system_prompt, user_prompt)
    sql = sql.strip().strip("`")
    if sql.lower().startswith("sql"):
        sql = sql[3:].strip()
    lowered = sql.lower()
    blocked = ["insert ", "update ", "delete ", "drop ", "alter ", "pragma ", "attach ", "detach "]
    if not (lowered.startswith("select") or lowered.startswith("with")) or any(token in lowered for token in blocked):
        raise ValueError(f"模型返回了不安全或不可执行的 SQL: {sql}")
    return sql.rstrip(";") + ";"


def parse_schema(conn, dataset_id):
    rows = conn.execute(
        """
        SELECT table_name, columns_json
        FROM tables_meta
        WHERE dataset_id = ?
        ORDER BY id
        """,
        (dataset_id,),
    ).fetchall()
    return [(table_name, json.loads(columns_json)) for table_name, columns_json in rows]


def choose_column(columns, keywords):
    lowered = [(column, str(column).lower()) for column in columns]
    for keyword in keywords:
        for original, lowered_name in lowered:
            if keyword in lowered_name:
                return original
    return None


def extract_supplier_name(question):
    patterns = [
        r"(.+?)(?:提供了哪些|提供哪些|提供什么|有哪些货物|哪些货物|哪些产品|什么产品|哪些物料|什么物料)",
        r"(?:生产商|供应商|厂家|厂商)[:：]?\s*(.+?)(?:提供了哪些|提供哪些|提供什么|有哪些货物|哪些货物|哪些产品|什么产品|哪些物料|什么物料|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, question)
        if match:
            candidate = match.group(1).strip(" ：:，,。?？")
            if len(candidate) >= 2:
                return candidate
    return None


def build_supplier_goods_fallback_sql(conn, dataset_id, question):
    supplier_name = extract_supplier_name(question)
    if not supplier_name:
        return None

    parsed_schema = parse_schema(conn, dataset_id)
    supplier_keywords = ["生产商", "供应商", "厂家", "厂商", "vendor", "supplier", "manufacturer"]
    goods_keywords = ["货物", "货品", "产品", "物料", "商品", "品名", "名称", "型号", "item", "product", "material"]

    queries = []
    escaped_name = supplier_name.replace("'", "''")
    for table_name, columns in parsed_schema:
        supplier_column = choose_column(columns, supplier_keywords)
        goods_column = choose_column(columns, goods_keywords)
        if not supplier_column or not goods_column:
            continue
        queries.append(
            f"""
            SELECT DISTINCT "{goods_column}" AS "货物"
            FROM "{table_name}"
            WHERE CAST("{supplier_column}" AS TEXT) LIKE '%{escaped_name}%'
              AND COALESCE(TRIM(CAST("{goods_column}" AS TEXT)), '') <> ''
            """
        )

    if not queries:
        return None
    union_sql = "\nUNION\n".join(query.strip() for query in queries)
    return f"{union_sql}\nLIMIT {MAX_RESULT_ROWS};"


def explain_sql_result(client, chat_model, question, sql, dataframe):
    preview = dataframe.head(10).to_markdown(index=False)
    system_prompt = "你是数据分析助手，请根据 SQL 结果直接回答用户问题，回答简洁准确。"
    user_prompt = (
        f"用户问题: {question}\n\n"
        f"执行的 SQL:\n{sql}\n\n"
        f"查询结果预览:\n{preview}\n\n"
        f"总返回行数: {len(dataframe)}"
    )
    return run_chat_completion(client, chat_model, system_prompt, user_prompt)


def cosine_similarity(left, right):
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


def semantic_search(conn, client, embedding_model, dataset_id, question, top_k=5):
    query_vector = embed_texts(client, embedding_model, [question])[0]
    rows = conn.execute(
        """
        SELECT table_name, row_text, embedding_json
        FROM embeddings
        WHERE dataset_id = ?
        """,
        (dataset_id,),
    ).fetchall()
    scored = []
    for table_name, row_text, embedding_json in rows:
        score = cosine_similarity(query_vector, json.loads(embedding_json))
        scored.append((score, table_name, row_text))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[:top_k]


def has_embeddings(conn, dataset_id):
    row = conn.execute(
        "SELECT COUNT(1) FROM embeddings WHERE dataset_id = ?",
        (dataset_id,),
    ).fetchone()
    return bool(row and row[0] > 0)


def answer_with_context(client, chat_model, question, context_lines):
    system_prompt = "你是表格问答助手。请只根据提供的上下文回答，不要编造不存在的数据。"
    user_prompt = (
        f"用户问题: {question}\n\n"
        "相关上下文:\n"
        f"{chr(10).join(context_lines)}"
    )
    return run_chat_completion(client, chat_model, system_prompt, user_prompt)


def conversational_chat(conn, client, dataset_id, mode, question, chat_model, embedding_model):
    if not client:
        raise ValueError("需要先在 .env 中提供 API Key，才能进行自然语言问答。")

    sql = None
    if mode in {"SQL 精确查询", "混合"}:
        schema_text = build_schema_prompt(conn, dataset_id)
        try:
            sql = generate_sql(client, chat_model, question, schema_text)
            sql_result = pd.read_sql_query(sql, conn)
            if not sql_result.empty:
                answer = explain_sql_result(client, chat_model, question, sql, sql_result)
                return answer, sql
            fallback_sql = build_supplier_goods_fallback_sql(conn, dataset_id, question)
            if fallback_sql:
                fallback_result = pd.read_sql_query(fallback_sql, conn)
                if not fallback_result.empty:
                    answer = explain_sql_result(client, chat_model, question, fallback_sql, fallback_result)
                    return answer, fallback_sql
            if mode == "SQL 精确查询":
                return "SQL 查询已执行，但没有返回结果。", sql
        except Exception as exc:
            if mode == "SQL 精确查询":
                raise ValueError(f"SQL 查询失败: {exc}") from exc

    if mode in {"语义检索", "混合"}:
        if not has_embeddings(conn, dataset_id):
            if mode == "混合":
                return "这次 SQL 没有命中结果，当前数据集也还没有可用的 embedding 索引，所以暂时无法继续做语义检索。你可以重新上传文件建立索引，或者切换到“SQL 精确查询”模式再试。", sql
            return "当前数据集还没有可用的 embedding 索引。请重新上传文件建立索引，或改用“SQL 精确查询”模式。", sql
        results = semantic_search(conn, client, embedding_model, dataset_id, question)
        if not results:
            return "没有检索到足够相关的语义上下文。你可以换一种问法，或者切换到“SQL 精确查询”模式。", sql
        context_lines = [f"[{table_name}] {row_text}" for _, table_name, row_text in results]
        answer = answer_with_context(client, chat_model, question, context_lines)
        return answer, sql

    raise ValueError("不支持的查询模式。")
