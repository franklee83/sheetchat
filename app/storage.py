import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime
from io import BytesIO

import pandas as pd

from .config import APP_DIR, DATA_DIR, DB_PATH, EMBED_BATCH_SIZE


def ensure_storage():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS datasets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_name TEXT NOT NULL,
            file_hash TEXT NOT NULL UNIQUE,
            file_type TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tables_meta (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dataset_id INTEGER NOT NULL,
            table_name TEXT NOT NULL,
            source_name TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            columns_json TEXT NOT NULL,
            FOREIGN KEY(dataset_id) REFERENCES datasets(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dataset_id INTEGER NOT NULL,
            table_name TEXT NOT NULL,
            row_number INTEGER NOT NULL,
            row_text TEXT NOT NULL,
            embedding_json TEXT NOT NULL,
            FOREIGN KEY(dataset_id) REFERENCES datasets(id)
        )
        """
    )
    conn.commit()
    return conn


def normalize_identifier(value):
    normalized = re.sub(r"[^0-9a-zA-Z_]+", "_", value.strip().lower())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized or "sheet"


def build_table_name(file_hash, source_name):
    return f"data_{file_hash[:8]}_{normalize_identifier(source_name)[:32]}"


def hash_bytes(content):
    return hashlib.sha256(content).hexdigest()


def batch_items(items, size):
    for index in range(0, len(items), size):
        yield items[index:index + size]


def format_row_text(row):
    parts = []
    for column, value in row.items():
        if pd.isna(value):
            continue
        text = str(value).strip()
        if text:
            parts.append(f"{column}: {text}")
    return " | ".join(parts)


def load_uploaded_frames(uploaded_file):
    suffix = os.path.splitext(uploaded_file.name)[1].lower()
    payload = uploaded_file.getvalue()
    if suffix == ".csv":
        dataframe = pd.read_csv(BytesIO(payload))
        return {os.path.splitext(uploaded_file.name)[0]: dataframe}, "csv", payload
    if suffix in {".xlsx", ".xls"}:
        dataframes = pd.read_excel(BytesIO(payload), sheet_name=None)
        return dataframes, "excel", payload
    raise ValueError("仅支持 CSV、XLSX、XLS 文件。")


def embed_texts(client, embedding_model, texts):
    response = client.embeddings.create(model=embedding_model, input=texts)
    return [item.embedding for item in response.data]


def build_embeddings_for_dataset(conn, dataset_id, client, embedding_model):
    if not client or not embedding_model:
        return "未提供可用的 embedding 配置，跳过语义索引建立。"

    tables = conn.execute(
        """
        SELECT table_name
        FROM tables_meta
        WHERE dataset_id = ?
        ORDER BY id
        """,
        (dataset_id,),
    ).fetchall()
    if not tables:
        return "当前数据集没有可索引的数据表。"

    conn.execute("DELETE FROM embeddings WHERE dataset_id = ?", (dataset_id,))
    warning = None
    try:
        for (table_name,) in tables:
            frame = pd.read_sql_query(f'SELECT * FROM "{table_name}"', conn)
            row_texts = []
            for _, row in frame.iterrows():
                row_text = format_row_text(row)
                if row_text:
                    row_texts.append(row_text)

            row_number = 0
            for text_batch in batch_items(row_texts, EMBED_BATCH_SIZE):
                vectors = embed_texts(client, embedding_model, text_batch)
                records = []
                for text, vector in zip(text_batch, vectors):
                    records.append(
                        (
                            dataset_id,
                            table_name,
                            row_number,
                            text,
                            json.dumps(vector),
                        )
                    )
                    row_number += 1
                conn.executemany(
                    """
                    INSERT INTO embeddings (dataset_id, table_name, row_number, row_text, embedding_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    records,
                )
        conn.commit()
    except Exception as exc:
        conn.execute("DELETE FROM embeddings WHERE dataset_id = ?", (dataset_id,))
        conn.commit()
        warning = f"Embedding 建立失败，已保留 SQLite 数据表: {exc}"
    return warning


def import_dataset(conn, uploaded_file, client, embedding_model):
    frames, file_type, payload = load_uploaded_frames(uploaded_file)
    file_hash = hash_bytes(payload)

    existing = conn.execute(
        "SELECT id FROM datasets WHERE file_hash = ?",
        (file_hash,),
    ).fetchone()
    if existing:
        dataset_id = existing[0]
        embedding_count = conn.execute(
            "SELECT COUNT(1) FROM embeddings WHERE dataset_id = ?",
            (dataset_id,),
        ).fetchone()[0]
        embedding_warning = None
        if embedding_count == 0 and client and embedding_model:
            embedding_warning = build_embeddings_for_dataset(conn, dataset_id, client, embedding_model)
        return dataset_id, True, embedding_warning

    cursor = conn.execute(
        """
        INSERT INTO datasets (file_name, file_hash, file_type, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (uploaded_file.name, file_hash, file_type, datetime.utcnow().isoformat()),
    )
    dataset_id = cursor.lastrowid

    embedding_warning = None
    for source_name, frame in frames.items():
        cleaned = frame.copy()
        cleaned.columns = [
            str(column).strip() or f"column_{index + 1}"
            for index, column in enumerate(cleaned.columns)
        ]
        table_name = build_table_name(file_hash, source_name)

        cleaned.to_sql(table_name, conn, if_exists="replace", index=False)
        conn.execute(
            """
            INSERT INTO tables_meta (dataset_id, table_name, source_name, row_count, columns_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                dataset_id,
                table_name,
                str(source_name),
                int(len(cleaned)),
                json.dumps(list(cleaned.columns), ensure_ascii=False),
            ),
        )

        if client and embedding_model:
            row_texts = []
            for _, row in cleaned.iterrows():
                row_text = format_row_text(row)
                if row_text:
                    row_texts.append(row_text)
            try:
                row_number = 0
                for text_batch in batch_items(row_texts, EMBED_BATCH_SIZE):
                    vectors = embed_texts(client, embedding_model, text_batch)
                    records = []
                    for text, vector in zip(text_batch, vectors):
                        records.append(
                            (
                                dataset_id,
                                table_name,
                                row_number,
                                text,
                                json.dumps(vector),
                            )
                        )
                        row_number += 1
                    conn.executemany(
                        """
                        INSERT INTO embeddings (dataset_id, table_name, row_number, row_text, embedding_json)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        records,
                    )
            except Exception as exc:
                embedding_warning = f"Embedding 建立失败，已保留 SQLite 数据表: {exc}"

    conn.commit()
    return dataset_id, False, embedding_warning


def fetch_dataset_overview(conn, dataset_id):
    dataset = conn.execute(
        "SELECT file_name, file_type, created_at FROM datasets WHERE id = ?",
        (dataset_id,),
    ).fetchone()
    tables = conn.execute(
        """
        SELECT table_name, source_name, row_count, columns_json
        FROM tables_meta
        WHERE dataset_id = ?
        ORDER BY id
        """,
        (dataset_id,),
    ).fetchall()
    return dataset, tables


def build_schema_prompt(conn, dataset_id):
    rows = conn.execute(
        """
        SELECT table_name, source_name, columns_json
        FROM tables_meta
        WHERE dataset_id = ?
        ORDER BY id
        """,
        (dataset_id,),
    ).fetchall()
    schema_lines = []
    for table_name, source_name, columns_json in rows:
        columns = json.loads(columns_json)
        quoted_columns = ", ".join(f'"{column}"' for column in columns)
        schema_lines.append(f'表 "{table_name}" (来源: {source_name})，列: {quoted_columns}')
    return "\n".join(schema_lines)
