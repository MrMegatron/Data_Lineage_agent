from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


# backend目录
BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """
    系统统一配置。

    本地开发时，从 backend/.env 读取配置。
    """

    # -------------------------
    # 应用配置
    # -------------------------
    app_name: str = "Data Lineage Agent"
    app_env: str = "dev"
    app_host: str = "127.0.0.1"
    app_port: int = 8000

    # -------------------------
    # MySQL配置
    # -------------------------
    db_host: str = "127.0.0.1"
    db_port: int
    db_name: str = "data_lineage"
    db_user: str
    db_password: str
    db_charset: str
    # -------------------------
    # SQL 源文件目录
    # -------------------------

    # API 只允许扫描这个根目录下的文件。
    sql_source_root: Path = (
        BASE_DIR.parent / "sql_sources"
    )

    # -------------------------
    # Pydantic Settings配置
    # -------------------------
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def database_url(self) -> URL:
        """
        创建SQLAlchemy数据库连接URL。
        """

        return URL.create(
            drivername="mysql+pymysql",
            username=self.db_user,
            password=self.db_password,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
            query={
                "charset": self.db_charset,
            },
        )


# 创建整个系统唯一的配置对象
settings = Settings()