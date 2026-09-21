from hashlib import sha256

import pytest

from app.services.sql_file_scanner import (
    SqlFileScanError,
    scan_sql_files,
)


# ============================================================
# 1. 测试递归扫描和非 SQL 文件过滤
# ============================================================

def test_scan_sql_files_recursively(tmp_path):
    """
    验证：

    1. 可以找到根目录中的 SQL 文件；
    2. 可以找到子目录中的 SQL 文件；
    3. 可以识别大写 .SQL；
    4. 不会扫描 txt 文件；
    5. 扫描结果顺序稳定。
    """

    sql_root = tmp_path / "sql_source"
    dwd_directory = sql_root / "models" / "dwd"

    dwd_directory.mkdir(parents=True)

    first_sql = sql_root / "01_ods_to_dwd.sql"
    second_sql = dwd_directory / "02_dwd_to_ads.SQL"
    ignored_file = sql_root / "readme.txt"

    first_sql.write_text(
        "SELECT * FROM ods.orders;",
        encoding="utf-8",
    )

    second_sql.write_text(
        "SELECT * FROM dwd.orders;",
        encoding="utf-8",
    )

    ignored_file.write_text(
        "这个文件不应该被扫描",
        encoding="utf-8",
    )

    result = scan_sql_files(sql_root)

    assert len(result) == 2

    assert [
        item.relative_path
        for item in result
    ] == [
        "01_ods_to_dwd.sql",
        "models/dwd/02_dwd_to_ads.SQL",
    ]

    assert result[0].file_name == "01_ods_to_dwd.sql"

    assert result[0].source_code == (
        "SELECT * FROM ods.orders;"
    )

    assert result[1].source_code == (
        "SELECT * FROM dwd.orders;"
    )


# ============================================================
# 2. 测试文件哈希和文件大小
# ============================================================

def test_scan_sql_file_hash_and_size(tmp_path):
    """
    验证扫描器使用原始文件字节计算哈希和文件大小。
    """

    sql_root = tmp_path / "sql_source"
    sql_root.mkdir()

    sql_content = (
        "INSERT INTO dwd.orders\n"
        "SELECT * FROM ods.orders;\n"
    )

    raw_content = sql_content.encode("utf-8")

    sql_file = sql_root / "orders.sql"
    sql_file.write_bytes(raw_content)

    result = scan_sql_files(sql_root)

    assert len(result) == 1

    scanned_file = result[0]

    assert scanned_file.file_hash == (
        sha256(raw_content).hexdigest()
    )

    assert scanned_file.size_bytes == len(raw_content)

    assert len(scanned_file.file_hash) == 64


# ============================================================
# 3. 测试中文 GB18030 文件
# ============================================================

def test_scan_gb18030_sql_file(tmp_path):
    """
    验证扫描器能够读取 Windows 中文环境中常见的
    GBK/GB18030 SQL 文件。
    """

    sql_root = tmp_path / "sql_source"
    sql_root.mkdir()

    sql_content = (
        "-- 订单明细表\n"
        "SELECT * FROM ods.order_detail;\n"
    )

    sql_file = sql_root / "中文订单.sql"

    sql_file.write_bytes(
        sql_content.encode("gb18030")
    )

    result = scan_sql_files(sql_root)

    assert len(result) == 1
    assert result[0].source_code == sql_content
    assert result[0].encoding == "gb18030"


# ============================================================
# 4. 测试不存在的扫描目录
# ============================================================

def test_scan_missing_directory(tmp_path):
    """
    目录不存在时，必须明确报错，
    不能静默返回空列表。
    """

    missing_directory = tmp_path / "not_exists"

    with pytest.raises(
        SqlFileScanError,
        match="扫描目录不存在",
    ):
        scan_sql_files(missing_directory)


# ============================================================
# 5. 测试扫描路径不是目录
# ============================================================

def test_scan_path_is_not_directory(tmp_path):
    """
    用户传入文件路径而不是目录路径时，
    必须给出明确错误。
    """

    normal_file = tmp_path / "normal.txt"

    normal_file.write_text(
        "普通文件",
        encoding="utf-8",
    )

    with pytest.raises(
        SqlFileScanError,
        match="扫描路径不是目录",
    ):
        scan_sql_files(normal_file)