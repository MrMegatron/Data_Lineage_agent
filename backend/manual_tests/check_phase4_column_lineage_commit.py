from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4


# ============================================================
# 1. 修复直接运行 manual_tests 脚本时的导入路径
# ============================================================

BACKEND_DIRECTORY = (
    Path(__file__)
    .resolve()
    .parents[1]
)

backend_path = str(
    BACKEND_DIRECTORY
)

if backend_path not in sys.path:
    sys.path.insert(
        0,
        backend_path,
    )


# 必须在 sys.path 修复后导入 app
from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import (
    ColumnLineage,
    DataColumn,
    DataTable,
    LineageEvidence,
    LineageProject,
    ScriptDependency,
    ScriptTableAccess,
    SourceScript,
)
from app.services.sql_directory_ingestion_service import (
    ingest_sql_directory,
)


# ============================================================
# 2. 创建字段血缘验收 SQL 文件
# ============================================================

def create_acceptance_sql_files(
    root_directory: Path,
) -> None:
    """
    创建两份 SQL。

    表级链路：

        ods.orders
            ↓
        01_ods_to_dwd.sql
            ↓
        dwd.orders
            ↓
        02_dwd_to_ads.sql
            ↓
        ads.order_report

    字段级链路：

        ods.orders.order_id
            ↓
        dwd.orders.order_id
            ↓
        ads.order_report.order_id

    customer_id 和 amount 同理。
    """

    hive_directory = (
        root_directory
        / "hive"
    )

    hive_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    # --------------------------------------------------------
    # 第一份 SQL
    #
    # 表级：
    #     ods.orders -> dwd.orders
    #
    # 字段级：
    #     ods.order_id    -> dwd.order_id
    #     ods.customer_id -> dwd.customer_id
    #     ods.amount      -> dwd.amount
    # --------------------------------------------------------

    first_file = (
        hive_directory
        / "01_ods_to_dwd.sql"
    )

    first_file.write_text(
        (
            "INSERT INTO dwd.orders (\n"
            "    order_id,\n"
            "    customer_id,\n"
            "    amount\n"
            ")\n"
            "SELECT\n"
            "    order_id,\n"
            "    customer_id,\n"
            "    amount\n"
            "FROM ods.orders;\n"
        ),
        encoding="utf-8",
    )

    # --------------------------------------------------------
    # 第二份 SQL
    #
    # 表级：
    #     dwd.orders -> ads.order_report
    #
    # 字段级：
    #     dwd.order_id    -> ads.order_id
    #     dwd.customer_id -> ads.customer_id
    #     dwd.amount      -> ads.amount
    # --------------------------------------------------------

    second_file = (
        hive_directory
        / "02_dwd_to_ads.sql"
    )

    second_file.write_text(
        (
            "INSERT INTO ads.order_report (\n"
            "    order_id,\n"
            "    customer_id,\n"
            "    amount\n"
            ")\n"
            "SELECT\n"
            "    order_id,\n"
            "    customer_id,\n"
            "    amount\n"
            "FROM dwd.orders;\n"
        ),
        encoding="utf-8",
    )


# ============================================================
# 3. 查询项目统计
# ============================================================

def count_project_columns(
    db,
    project_id: int,
) -> int:
    return int(
        db.scalar(
            select(
                func.count(
                    DataColumn.id
                )
            )
            .select_from(DataColumn)
            .join(
                DataTable,
                DataTable.id
                == DataColumn.table_id,
            )
            .where(
                DataTable.project_id
                == project_id
            )
        )
        or 0
    )


def count_project_accesses(
    db,
    project_id: int,
) -> int:
    return int(
        db.scalar(
            select(
                func.count(
                    ScriptTableAccess.id
                )
            )
            .select_from(
                ScriptTableAccess
            )
            .join(
                SourceScript,
                SourceScript.id
                == ScriptTableAccess.script_id,
            )
            .where(
                SourceScript.project_id
                == project_id
            )
        )
        or 0
    )


# ============================================================
# 4. 正式执行并提交
# ============================================================

def main() -> None:
    db = SessionLocal()

    project_name = (
        "phase4_column_lineage_"
        f"{uuid4().hex[:8]}"
    )

    try:
        # ----------------------------------------------------
        # 第一步：创建验收项目
        # ----------------------------------------------------

        project = LineageProject(
            name=project_name,
            description=(
                "第四阶段字段级血缘永久验收项目"
            ),
        )

        db.add(project)
        db.flush()

        print("=" * 72)
        print("1. 验收项目创建成功")
        print(f"project_id   = {project.id}")
        print(f"project_name = {project.name}")

        # ----------------------------------------------------
        # 第二步：创建临时 SQL 目录并正式导入
        # ----------------------------------------------------

        with TemporaryDirectory(
            prefix="lineage_phase4_"
        ) as temporary_directory:
            sql_root = Path(
                temporary_directory
            )

            create_acceptance_sql_files(
                root_directory=sql_root,
            )

            print("=" * 72)
            print("2. SQL 文件创建成功")

            for sql_file in sorted(
                sql_root.rglob("*.sql")
            ):
                print(
                    "SQL file = "
                    f"{sql_file.relative_to(sql_root)}"
                )

            ingestion_result = (
                ingest_sql_directory(
                    db=db,
                    project_id=project.id,
                    root_directory=sql_root,
                )
            )

        # ----------------------------------------------------
        # 第三步：打印目录导入结果
        # ----------------------------------------------------

        print("=" * 72)
        print("3. 目录导入结果")
        print(
            "status           = "
            f"{ingestion_result.status}"
        )
        print(
            "total_files      = "
            f"{ingestion_result.total_files}"
        )
        print(
            "success_files    = "
            f"{ingestion_result.success_files}"
        )
        print(
            "failed_files     = "
            f"{ingestion_result.failed_files}"
        )
        print(
            "created_scripts  = "
            f"{ingestion_result.created_scripts}"
        )
        print(
            "table_accesses   = "
            f"{ingestion_result.table_access_count}"
        )
        print(
            "dependencies     = "
            f"{ingestion_result.total_dependency_count}"
        )

        # ----------------------------------------------------
        # 第四步：查询数据库统计
        # ----------------------------------------------------

        script_count = int(
            db.scalar(
                select(
                    func.count(
                        SourceScript.id
                    )
                )
                .where(
                    SourceScript.project_id
                    == project.id
                )
            )
            or 0
        )

        table_count = int(
            db.scalar(
                select(
                    func.count(
                        DataTable.id
                    )
                )
                .where(
                    DataTable.project_id
                    == project.id
                )
            )
            or 0
        )

        column_count = (
            count_project_columns(
                db=db,
                project_id=project.id,
            )
        )

        access_count = (
            count_project_accesses(
                db=db,
                project_id=project.id,
            )
        )

        dependency_count = int(
            db.scalar(
                select(
                    func.count(
                        ScriptDependency.id
                    )
                )
                .where(
                    ScriptDependency.project_id
                    == project.id
                )
            )
            or 0
        )

        lineage_count = int(
            db.scalar(
                select(
                    func.count(
                        ColumnLineage.id
                    )
                )
                .where(
                    ColumnLineage.project_id
                    == project.id
                )
            )
            or 0
        )

        evidence_count = int(
            db.scalar(
                select(
                    func.count(
                        LineageEvidence.id
                    )
                )
                .join(
                    ColumnLineage,
                    ColumnLineage.id
                    == LineageEvidence.column_lineage_id,
                )
                .where(
                    ColumnLineage.project_id
                    == project.id
                )
            )
            or 0
        )

        print("=" * 72)
        print("4. 数据库数量统计")
        print(f"script_count     = {script_count}")
        print(f"table_count      = {table_count}")
        print(f"column_count     = {column_count}")
        print(f"access_count     = {access_count}")
        print(
            f"dependency_count = {dependency_count}"
        )
        print(f"lineage_count    = {lineage_count}")
        print(f"evidence_count   = {evidence_count}")

        # ----------------------------------------------------
        # 第五步：打印字段血缘明细
        # ----------------------------------------------------

        lineages = list(
            db.scalars(
                select(ColumnLineage)
                .where(
                    ColumnLineage.project_id
                    == project.id
                )
                .order_by(
                    ColumnLineage.script_id,
                    ColumnLineage.id,
                )
            ).all()
        )

        print("=" * 72)
        print("5. 字段血缘明细")

        for lineage in lineages:
            source_column = (
                lineage.source_column
            )

            target_column = (
                lineage.target_column
            )

            source_table = (
                source_column.table
                if source_column is not None
                else None
            )

            target_table = (
                target_column.table
            )

            source_full_name = (
                f"{source_table.full_name}."
                f"{source_column.column_name}"
                if (
                    source_table is not None
                    and source_column is not None
                )
                else "None"
            )

            target_full_name = (
                f"{target_table.full_name}."
                f"{target_column.column_name}"
            )

            print(
                f"{source_full_name} "
                f"-> {target_full_name} "
                f"[{lineage.relation_type}, "
                f"{lineage.resolution_status}]"
            )

        # ----------------------------------------------------
        # 第六步：打印血缘证据
        # ----------------------------------------------------

        evidences = list(
            db.scalars(
                select(LineageEvidence)
                .join(
                    ColumnLineage,
                    ColumnLineage.id
                    == LineageEvidence.column_lineage_id,
                )
                .where(
                    ColumnLineage.project_id
                    == project.id
                )
                .order_by(
                    LineageEvidence.id
                )
            ).all()
        )

        print("=" * 72)
        print("6. 字段血缘证据")

        for evidence in evidences:
            print(
                f"lineage_id="
                f"{evidence.column_lineage_id}, "
                f"statement_no="
                f"{evidence.statement_no}, "
                f"code_snippet="
                f"{evidence.code_snippet}"
            )

        # ----------------------------------------------------
        # 第七步：完整断言
        # ----------------------------------------------------

        assert ingestion_result.status == "success"
        assert ingestion_result.total_files == 2
        assert ingestion_result.success_files == 2
        assert ingestion_result.failed_files == 0

        # 两份 SQL 脚本
        assert script_count == 2

        # ods.orders
        # dwd.orders
        # ads.order_report
        assert table_count == 3

        # 三张表，每张表三个字段
        assert column_count == 9

        # 每个脚本一条 READ、一条 WRITE
        assert access_count == 4

        # 第一份脚本通过 dwd.orders
        # 连接第二份脚本
        assert dependency_count == 1

        # 每份脚本三个直接字段映射
        assert lineage_count == 6

        # 每条血缘一条证据
        assert evidence_count == 6

        assert all(
            lineage.relation_type
            == "direct"
            for lineage in lineages
        )

        assert all(
            lineage.resolution_status
            == "confirmed"
            for lineage in lineages
        )

        # ----------------------------------------------------
        # 第八步：提交事务
        # ----------------------------------------------------

        db.commit()

        print("=" * 72)
        print("7. 第四阶段字段血缘永久验收通过")
        print("数据库事务已经提交")
        print()
        print("请记录：")
        print(f"project_id   = {project.id}")
        print(f"project_name = {project.name}")
        print("=" * 72)

    except Exception:
        db.rollback()

        print("=" * 72)
        print("字段血缘验收失败")
        print("数据库事务已经回滚")
        print("=" * 72)

        raise

    finally:
        db.close()


if __name__ == "__main__":
    main()