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


# 必须在修复 sys.path 以后再导入 app
from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import (
    DataTable,
    LineageProject,
    ScriptDependency,
    ScriptTableAccess,
    SourceScript,
)
from app.services.catalog_query_service import (
    list_project_scripts,
    list_project_tables,
)
from app.services.sql_directory_ingestion_service import (
    ingest_sql_directory,
)


# ============================================================
# 2. 创建验收 SQL 文件
# ============================================================

def create_acceptance_sql_files(
    root_directory: Path,
) -> None:
    """
    创建三份具有明确上下游关系的 Hive SQL。

    预期链路：

        01_ods_to_dwd.sql
                ↓
            dwd.orders
                ↓
        02_dwd_to_dws.sql
                ↓
        dws.order_summary
                ↓
        03_dws_to_ads.sql
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
    # 第一份 SQL：
    #
    # READ  ods.orders
    # WRITE dwd.orders
    # --------------------------------------------------------

    first_file = (
        hive_directory
        / "01_ods_to_dwd.sql"
    )

    first_file.write_text(
        (
            "INSERT INTO dwd.orders\n"
            "SELECT\n"
            "    order_id,\n"
            "    price,\n"
            "    quantity\n"
            "FROM ods.orders;\n"
        ),
        encoding="utf-8",
    )

    # --------------------------------------------------------
    # 第二份 SQL：
    #
    # READ  dwd.orders
    # WRITE dws.order_summary
    # --------------------------------------------------------

    second_file = (
        hive_directory
        / "02_dwd_to_dws.sql"
    )

    second_file.write_text(
        (
            "INSERT INTO dws.order_summary\n"
            "SELECT\n"
            "    order_id,\n"
            "    price * quantity AS amount\n"
            "FROM dwd.orders;\n"
        ),
        encoding="utf-8",
    )

    # --------------------------------------------------------
    # 第三份 SQL：
    #
    # READ  dws.order_summary
    # WRITE ads.order_report
    # --------------------------------------------------------

    third_file = (
        hive_directory
        / "03_dws_to_ads.sql"
    )

    third_file.write_text(
        (
            "INSERT INTO ads.order_report\n"
            "SELECT\n"
            "    order_id,\n"
            "    amount\n"
            "FROM dws.order_summary;\n"
        ),
        encoding="utf-8",
    )


# ============================================================
# 3. 执行完整业务闭环
# ============================================================

def main() -> None:
    """
    执行：

        创建项目
            ↓
        创建三份 SQL 文件
            ↓
        扫描 SQL 目录
            ↓
        识别 SQL 方言
            ↓
        解析 READ / WRITE 表
            ↓
        保存脚本和数据表
            ↓
        自动生成脚本依赖
            ↓
        使用 Catalog Service 重新查询
            ↓
        验证数据库记录数量
            ↓
        commit 永久保存
    """

    db = SessionLocal()

    # 每次执行生成一个新的项目名，
    # 避免和以前的验收数据混在一起。
    project_name = (
        "phase3_acceptance_"
        f"{uuid4().hex[:8]}"
    )

    try:
        # ----------------------------------------------------
        # 第一步：创建独立验收项目
        # ----------------------------------------------------

        project = LineageProject(
            name=project_name,
            description=(
                "第三阶段完整业务闭环验收项目"
            ),
        )

        db.add(project)
        db.flush()

        print("=" * 70)
        print("1. 验收项目创建成功")
        print(f"project_id   = {project.id}")
        print(f"project_name = {project.name}")

        # ----------------------------------------------------
        # 第二步：创建临时 SQL 目录
        # ----------------------------------------------------

        with TemporaryDirectory(
            prefix="lineage_phase3_"
        ) as temporary_directory:
            sql_root = Path(
                temporary_directory
            )

            create_acceptance_sql_files(
                root_directory=sql_root,
            )

            print("=" * 70)
            print("2. 验收 SQL 文件创建成功")
            print(f"root_directory = {sql_root}")

            for sql_file in sorted(
                sql_root.rglob("*.sql")
            ):
                print(
                    "SQL file       = "
                    f"{sql_file.relative_to(sql_root)}"
                )

            # ------------------------------------------------
            # 第三步：调用正式目录导入服务
            # ------------------------------------------------

            ingestion_result = (
                ingest_sql_directory(
                    db=db,
                    project_id=project.id,
                    root_directory=sql_root,
                )
            )

        print("=" * 70)
        print("3. SQL 目录导入完成")
        print(
            "status                = "
            f"{ingestion_result.status}"
        )
        print(
            "total_files           = "
            f"{ingestion_result.total_files}"
        )
        print(
            "success_files         = "
            f"{ingestion_result.success_files}"
        )
        print(
            "failed_files          = "
            f"{ingestion_result.failed_files}"
        )
        print(
            "created_scripts       = "
            f"{ingestion_result.created_scripts}"
        )
        print(
            "updated_scripts       = "
            f"{ingestion_result.updated_scripts}"
        )
        print(
            "total_statements      = "
            f"{ingestion_result.total_statements}"
        )
        print(
            "table_access_count    = "
            f"{ingestion_result.table_access_count}"
        )
        print(
            "total_dependency_count = "
            f"{ingestion_result.total_dependency_count}"
        )

        # ----------------------------------------------------
        # 第四步：使用 Catalog Service 查询脚本列表
        # ----------------------------------------------------

        script_result = list_project_scripts(
            db=db,
            project_id=project.id,
            page=1,
            page_size=100,
        )

        print("=" * 70)
        print("4. Catalog 脚本列表")

        for script in script_result.items:
            print(
                f"script_id={script.script_id}, "
                f"path={script.relative_path}, "
                f"dialect={script.dialect}, "
                f"status={script.parse_status}, "
                f"read={script.read_table_count}, "
                f"write={script.write_table_count}, "
                f"upstream={script.upstream_dependency_count}, "
                f"downstream={script.downstream_dependency_count}"
            )

        # ----------------------------------------------------
        # 第五步：使用 Catalog Service 查询数据表列表
        # ----------------------------------------------------

        table_result = list_project_tables(
            db=db,
            project_id=project.id,
            page=1,
            page_size=100,
        )

        print("=" * 70)
        print("5. Catalog 数据表列表")

        for table in table_result.items:
            print(
                f"table_id={table.table_id}, "
                f"full_name={table.full_name}, "
                f"readers={table.reader_script_count}, "
                f"writers={table.writer_script_count}, "
                f"dependencies={table.dependency_count}"
            )

        # ----------------------------------------------------
        # 第六步：直接统计数据库记录
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

        access_count = int(
            db.scalar(
                select(
                    func.count(
                        ScriptTableAccess.id
                    )
                )
                .join(
                    SourceScript,
                    SourceScript.id
                    == ScriptTableAccess.script_id,
                )
                .where(
                    SourceScript.project_id
                    == project.id
                )
            )
            or 0
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

        print("=" * 70)
        print("6. 数据库记录统计")
        print(f"script_count     = {script_count}")
        print(f"table_count      = {table_count}")
        print(f"access_count     = {access_count}")
        print(
            f"dependency_count = {dependency_count}"
        )

        # ----------------------------------------------------
        # 第七步：执行关键断言
        # ----------------------------------------------------

        assert ingestion_result.status == "success"

        assert ingestion_result.total_files == 3
        assert ingestion_result.success_files == 3
        assert ingestion_result.failed_files == 0

        assert script_count == 3
        assert script_result.total == 3

        # 应该识别出四张数据表：
        #
        # ods.orders
        # dwd.orders
        # dws.order_summary
        # ads.order_report
        assert table_count == 4
        assert table_result.total == 4

        # 每个 SQL 都有：
        #
        # 1 条 READ
        # 1 条 WRITE
        #
        # 三个脚本共 6 条访问记录。
        assert access_count == 6

        # 预期依赖：
        #
        # 01 -> 02 via dwd.orders
        # 02 -> 03 via dws.order_summary
        assert dependency_count == 2

        # 所有脚本都应该成功解析。
        assert all(
            item.parse_status == "success"
            for item in script_result.items
        )

        # 所有脚本都应该识别为 Hive。
        assert all(
            item.dialect == "hive"
            for item in script_result.items
        )

        # ----------------------------------------------------
        # 第八步：提交事务
        # ----------------------------------------------------

        db.commit()

        print("=" * 70)
        print("7. 第三阶段业务闭环验收通过")
        print("数据库事务已经提交")
        print()
        print("请记录下面两个值：")
        print(f"project_id   = {project.id}")
        print(f"project_name = {project.name}")
        print("=" * 70)

    except Exception:
        db.rollback()

        print("=" * 70)
        print("验收失败，事务已经回滚")
        print("=" * 70)

        raise

    finally:
        db.close()


if __name__ == "__main__":
    main()