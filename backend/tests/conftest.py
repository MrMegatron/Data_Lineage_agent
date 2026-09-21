from collections.abc import Generator

import pytest
from sqlalchemy.orm import Session

from app.database import SessionLocal


# ============================================================
# 为每个测试提供独立的数据库Session
# ============================================================

@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """
    为每个测试创建独立的 SQLAlchemy Session。

    测试过程中：
        使用 flush() 把SQL真实发送给MySQL；
        不允许调用 commit()。

    测试结束后：
        统一执行 rollback()；
        撤销当前测试插入的数据；
        最后关闭Session。
    """

    session = SessionLocal()

    try:
        yield session
    finally:
        session.rollback()
        session.close()