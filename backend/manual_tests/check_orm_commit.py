from hashlib import sha256
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload

from app.database import SessionLocal
from app.models import (
    LineageProject,
    SourceScript,
)


# ============================================================
# 1. 创建测试专用唯一名称
# ============================================================

def create_unique_suffix() -> str:
    """
    生成10位随机字符串，
    避免多次手工测试时名称重复。
    """

    return uuid4().hex[:10]


# ============================================================
# 2. 手工提交测试
# ============================================================

def main() -> None:
    """
    创建一个测试项目和一个测试脚本，
    执行commit永久写入MySQL。

    然后关闭原Session，
    使用全新的Session重新查询，
    验证数据确实已经持久化。
    """

    suffix = create_unique_suffix()

    project_name = (
        f"manual_commit_project_{suffix}"
    )

    relative_path = (
        f"manual_tests/{suffix}/"
        "manual_commit.sql"
    )

    source_code = (
        "SELECT 1 AS manual_commit_test"
    )

    file_hash = sha256(
        source_code.encode("utf-8")
    ).hexdigest()

    session = SessionLocal()

    try:
        # ----------------------------------------------------
        # 创建测试项目
        # ----------------------------------------------------

        project = LineageProject(
            name=project_name,
            description=(
                "Manual ORM commit verification"
            ),
        )

        session.add(project)

        # 真实发送INSERT，
        # 让MySQL生成project.id。
        session.flush()

        # ----------------------------------------------------
        # 创建属于该项目的测试脚本
        # ----------------------------------------------------

        script = SourceScript(
            project_id=project.id,
            file_name="manual_commit.sql",
            relative_path=relative_path,
            dialect="mysql",
            file_hash=file_hash,
            source_code=source_code,
            parse_status="success",
        )

        session.add(script)
        session.flush()

        project_id = project.id
        script_id = script.id

        # ----------------------------------------------------
        # 永久提交
        # ----------------------------------------------------

        session.commit()

        print(
            "========== COMMIT执行成功 =========="
        )
        print(f"项目ID：{project_id}")
        print(f"项目名称：{project_name}")
        print(f"脚本ID：{script_id}")
        print(
            f"脚本相对路径：{relative_path}"
        )

    except SQLAlchemyError as exc:
        session.rollback()

        print(
            "========== COMMIT执行失败 =========="
        )
        print(f"错误类型：{type(exc).__name__}")
        print(f"错误信息：{exc}")

        raise SystemExit(1) from exc

    finally:
        session.close()

    # ========================================================
    # 使用全新的Session重新查询
    # ========================================================

    verify_session = SessionLocal()

    try:
        saved_script = verify_session.scalar(
            select(SourceScript)
            .options(
                joinedload(
                    SourceScript.project
                )
            )
            .where(
                SourceScript.id == script_id
            )
        )

        if saved_script is None:
            raise RuntimeError(
                "commit后使用新Session查询不到脚本"
            )

        if saved_script.project.id != project_id:
            raise RuntimeError(
                "脚本所属project_id不正确"
            )

        if (
            saved_script.project.name
            != project_name
        ):
            raise RuntimeError(
                "脚本所属项目名称不正确"
            )

        print(
            "========== 新Session查询成功 =========="
        )
        print(
            f"数据库项目ID："
            f"{saved_script.project.id}"
        )
        print(
            f"数据库项目名称："
            f"{saved_script.project.name}"
        )
        print(
            f"数据库脚本ID："
            f"{saved_script.id}"
        )
        print(
            f"数据库脚本名称："
            f"{saved_script.file_name}"
        )
        print(
            "========== 手工提交测试通过 =========="
        )

        print()
        print(
            "请保存下面两个值，"
            "后续清理时需要使用："
        )
        print(
            f"PROJECT_ID={project_id}"
        )
        print(
            f'PROJECT_NAME="{project_name}"'
        )

    finally:
        verify_session.close()


if __name__ == "__main__":
    main()