import argparse

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.database import SessionLocal
from app.models import (
    LineageProject,
    SourceScript,
)


# ============================================================
# 1. 读取命令行参数
# ============================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Clean one manual ORM commit "
            "test project."
        ),
    )

    parser.add_argument(
        "--project-id",
        type=int,
        required=True,
        help="Exact project ID to delete.",
    )

    parser.add_argument(
        "--project-name",
        type=str,
        required=True,
        help="Exact project name to delete.",
    )

    return parser.parse_args()


# ============================================================
# 2. 安全删除测试项目
# ============================================================

def main() -> None:
    args = parse_args()

    project_id = args.project_id
    project_name = args.project_name

    # --------------------------------------------------------
    # 安全检查1：名称必须是手工测试前缀
    # --------------------------------------------------------

    safe_prefix = "manual_commit_project_"

    if not project_name.startswith(
        safe_prefix
    ):
        raise SystemExit(
            "拒绝删除：项目名称不是"
            "manual_commit_project_测试前缀"
        )

    session = SessionLocal()

    try:
        # ----------------------------------------------------
        # 同时使用ID和完整名称定位记录
        # ----------------------------------------------------

        project = session.scalar(
            select(LineageProject).where(
                LineageProject.id
                == project_id,
                LineageProject.name
                == project_name,
            )
        )

        if project is None:
            raise SystemExit(
                "没有找到同时匹配项目ID和"
                "完整项目名称的测试记录"
            )

        # ----------------------------------------------------
        # 删除前统计该项目的脚本数量
        # ----------------------------------------------------

        script_count_before = (
            session.scalar(
                select(
                    func.count(
                        SourceScript.id
                    )
                ).where(
                    SourceScript.project_id
                    == project_id
                )
            )
            or 0
        )

        print(
            "========== 删除目标确认 =========="
        )
        print(f"项目ID：{project.id}")
        print(f"项目名称：{project.name}")
        print(
            f"关联脚本数量："
            f"{script_count_before}"
        )

        # ----------------------------------------------------
        # 删除项目
        # ----------------------------------------------------

        session.delete(project)
        session.commit()

        print(
            "========== 删除提交成功 =========="
        )

    except SQLAlchemyError as exc:
        session.rollback()

        print(
            "========== 删除失败 =========="
        )
        print(f"错误类型：{type(exc).__name__}")
        print(f"错误信息：{exc}")

        raise SystemExit(1) from exc

    finally:
        session.close()

    # ========================================================
    # 3. 使用新Session验证项目和脚本都已删除
    # ========================================================

    verify_session = SessionLocal()

    try:
        remaining_project = (
            verify_session.scalar(
                select(LineageProject).where(
                    LineageProject.id
                    == project_id
                )
            )
        )

        remaining_script_count = (
            verify_session.scalar(
                select(
                    func.count(
                        SourceScript.id
                    )
                ).where(
                    SourceScript.project_id
                    == project_id
                )
            )
            or 0
        )

        if remaining_project is not None:
            raise RuntimeError(
                "项目记录仍然存在"
            )

        if remaining_script_count != 0:
            raise RuntimeError(
                "关联脚本没有被CASCADE删除"
            )

        print(
            "========== 删除验证通过 =========="
        )
        print("项目剩余数量：0")
        print("关联脚本剩余数量：0")

    finally:
        verify_session.close()


if __name__ == "__main__":
    main()