import os
from dataclasses import dataclass

from dotenv import load_dotenv


APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(APP_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "app_data.db")
APP_NAME = "表格数据问答助手"
APP_VERSION = "v0.5"
MAX_RESULT_ROWS = 50
EMBED_BATCH_SIZE = 10

load_dotenv(os.path.join(APP_DIR, ".env"))


@dataclass(frozen=True)
class AppConfig:
    api_key: str
    api_base_url: str
    chat_model: str
    embedding_model: str


def load_config():
    return AppConfig(
        api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        api_base_url=os.getenv(
            "OPENAI_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ).strip(),
        chat_model=os.getenv("CHAT_MODEL", "qwen-plus").strip(),
        embedding_model=os.getenv("EMBEDDING_MODEL", "text-embedding-v3").strip(),
    )
