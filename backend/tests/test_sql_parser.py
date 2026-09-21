import pytest

from app.services.sql_parser import (
    parse_sql_script,
)


# ============================================================
# 1. 测试解析多条 SQL
# ============================================================

def test_parse_multiple_statements():
    sql = (
        "INSERT INTO dwd.orders\n"
        "SELECT * FROM ods.orders;\n"
        "\n"
        "SELECT *\n"
        "FROM dwd.orders;\n"
    )

    result = parse_sql_script(
        source_code=sql,
        business_dialect="hive",
    )

    assert result.parse_status == "success"
    assert result.parse_error is None

    assert result.total_count == 2
    assert result.success_count == 2
    assert result.failed_count == 0

    first_statement = result.statements[0]
    second_statement = result.statements[1]

    assert first_statement.statement_no == 1
    assert first_statement.statement_type == "insert"
    assert first_statement.line_start == 1
    assert first_statement.line_end == 2

    assert second_statement.statement_no == 2
    assert second_statement.statement_type == "select"
    assert second_statement.line_start == 4
    assert second_statement.line_end == 5

    assert first_statement.expression is not None
    assert second_statement.expression is not None


# ============================================================
# 2. 测试字符串中的分号不会拆分 SQL
# ============================================================

def test_semicolon_inside_string_is_not_split():
    sql = (
        "SELECT '上海;深圳' AS city_names;\n"
        "SELECT '北京' AS city_name;\n"
    )

    result = parse_sql_script(
        source_code=sql,
        business_dialect="unknown",
    )

    assert result.parse_status == "success"
    assert result.total_count == 2

    assert "上海;深圳" in (
        result.statements[0].source_sql
    )


# ============================================================
# 3. 测试注释中的分号不会拆分 SQL
# ============================================================

def test_semicolon_inside_comment_is_not_split():
    sql = (
        "-- 注释里的分号不能拆分 ; ; ;\n"
        "SELECT * FROM ods.orders;\n"
        "\n"
        "/* 块注释里的分号也不能拆分 ; */\n"
        "SELECT * FROM dwd.orders;\n"
    )

    result = parse_sql_script(
        source_code=sql,
        business_dialect="hive",
    )

    assert result.parse_status == "success"
    assert result.total_count == 2


# ============================================================
# 4. 测试一条失败不丢失其他语句
# ============================================================

def test_failed_statement_does_not_remove_successful_ones():
    sql = (
        "SELECT 1;\n"
        "SELECT (1 +;\n"
        "SELECT 3;\n"
    )

    result = parse_sql_script(
        source_code=sql,
        business_dialect="unknown",
    )

    assert result.parse_status == "failed"
    assert result.parse_error is not None

    assert result.total_count == 3
    assert result.success_count == 2
    assert result.failed_count == 1

    assert (
        result.statements[0].parse_status
        == "success"
    )

    assert (
        result.statements[1].parse_status
        == "failed"
    )

    assert (
        result.statements[2].parse_status
        == "success"
    )

    assert (
        result.statements[1].error_message
        is not None
    )

    assert "第 2 条语句" in result.parse_error


# ============================================================
# 5. 测试最后一条 SQL 没有分号
# ============================================================

def test_last_statement_without_semicolon():
    sql = (
        "SELECT 1;\n"
        "SELECT 2"
    )

    result = parse_sql_script(
        source_code=sql,
        business_dialect="unknown",
    )

    assert result.parse_status == "success"
    assert result.total_count == 2
    assert result.success_count == 2


# ============================================================
# 6. 测试空文件和纯注释文件
# ============================================================

@pytest.mark.parametrize(
    "sql",
    [
        "",
        "   \n\n   ",
        "-- 这个文件只有注释\n",
        "/* 这个文件也只有注释 */",
    ],
)
def test_empty_or_comment_only_script(sql):
    result = parse_sql_script(
        source_code=sql,
        business_dialect="unknown",
    )

    assert result.parse_status == "failed"
    assert result.total_count == 0

    assert result.parse_error == (
        "文件中没有可解析的 SQL 语句"
    )


# ============================================================
# 7. 测试不支持的业务方言
# ============================================================

def test_unsupported_business_dialect():
    with pytest.raises(
        ValueError,
        match="不支持的业务方言",
    ):
        parse_sql_script(
            source_code="SELECT 1",
            business_dialect="mysql",
        )