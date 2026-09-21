from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import ErrorLevel, parse_one
from sqlglot.errors import ParseError, TokenError

from app.services.sql_dialect_detector import (
    SUPPORTED_DIALECTS,
    get_sqlglot_dialect,
)


# ============================================================
# 1. 单条 SQL 语句解析结果
# ============================================================

@dataclass(frozen=True, slots=True)
class ParsedSqlStatement:
    """
    一条 SQL 语句的解析结果。
    """

    # SQL 在文件中的顺序，从 1 开始
    statement_no: int

    # 原始 SQL 文本
    source_sql: str

    # SQL 在源文件中的开始行
    line_start: int

    # SQL 在源文件中的结束行
    line_end: int

    # success 或 failed
    parse_status: str

    # select、insert、create 等
    statement_type: str | None

    # SQLGlot 格式化后的 SQL
    normalized_sql: str | None

    # SQLGlot 抽象语法树
    expression: Any | None

    # 解析失败时的错误信息
    error_message: str | None


# ============================================================
# 2. 整个 SQL 文件的解析结果
# ============================================================

@dataclass(frozen=True, slots=True)
class SqlScriptParseResult:
    """
    整个 SQL 文件的解析结果。
    """

    # success 或 failed
    parse_status: str

    # 文件级错误摘要
    parse_error: str | None

    # 文件中全部 SQL 语句
    statements: tuple[ParsedSqlStatement, ...]

    @property
    def total_count(self) -> int:
        """
        SQL 语句总数。
        """

        return len(self.statements)

    @property
    def success_count(self) -> int:
        """
        解析成功的 SQL 语句数。
        """

        return sum(
            statement.parse_status == "success"
            for statement in self.statements
        )

    @property
    def failed_count(self) -> int:
        """
        解析失败的 SQL 语句数。
        """

        return sum(
            statement.parse_status == "failed"
            for statement in self.statements
        )


# ============================================================
# 3. 内部使用的 SQL 文本块
# ============================================================

@dataclass(frozen=True, slots=True)
class _SqlStatementChunk:
    """
    从 SQL 文件中拆分出来的原始语句块。
    """

    source_sql: str
    line_start: int
    line_end: int


# ============================================================
# 4. 判断当前位置是否是 PostgreSQL 美元引号
# ============================================================

def _match_dollar_quote(
    source_code: str,
    position: int,
) -> str | None:
    """
    识别 PostgreSQL 美元引号。

    支持：

        $$
        $body$
        $function_1$
    """

    match = re.match(
        r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$",
        source_code[position:],
    )

    if match is None:
        return None

    return match.group(0)


# ============================================================
# 5. 把原始文本片段转换成 SQL 块
# ============================================================

def _build_statement_chunk(
    complete_source: str,
    raw_start: int,
    raw_end: int,
) -> _SqlStatementChunk | None:
    """
    清除语句前后的空白，并计算行号。

    raw_end 是不包含在片段中的结束位置。
    """

    raw_statement = complete_source[
        raw_start:raw_end
    ]

    # 计算左侧空白字符数量
    left_whitespace_count = (
        len(raw_statement)
        - len(raw_statement.lstrip())
    )

    # 计算去除右侧空白后的长度
    trimmed_right_length = len(
        raw_statement.rstrip()
    )

    statement_start = (
        raw_start
        + left_whitespace_count
    )

    statement_end = (
        raw_start
        + trimmed_right_length
    )

    source_sql = complete_source[
        statement_start:statement_end
    ]

    if not source_sql:
        return None

    line_start = (
        complete_source.count(
            "\n",
            0,
            statement_start,
        )
        + 1
    )

    line_end = (
        complete_source.count(
            "\n",
            0,
            statement_end,
        )
        + 1
    )

    return _SqlStatementChunk(
        source_sql=source_sql,
        line_start=line_start,
        line_end=line_end,
    )


# ============================================================
# 6. 安全拆分多条 SQL
# ============================================================

def _split_sql_statements(
    source_code: str,
) -> list[_SqlStatementChunk]:
    """
    根据分号拆分 SQL，但不会错误拆分以下内容：

    1. 单引号字符串中的分号；
    2. 双引号标识符中的分号；
    3. 反引号标识符中的分号；
    4. -- 单行注释中的分号；
    5. /* */ 块注释中的分号；
    6. PostgreSQL 美元引号中的分号。

    不能直接使用：

        source_code.split(";")

    因为：

        SELECT 'a;b';

    中间的分号属于字符串，而不是语句结束符。
    """

    chunks: list[_SqlStatementChunk] = []

    statement_start = 0
    position = 0
    source_length = len(source_code)

    # 当前引号类型：
    # 单引号、双引号或者反引号
    current_quote: str | None = None

    # PostgreSQL 美元引号，例如 $$ 或 $body$
    dollar_quote: str | None = None

    # 是否处于 -- 单行注释
    in_line_comment = False

    # 块注释嵌套层级
    block_comment_depth = 0

    while position < source_length:

        current_char = source_code[position]

        next_char = (
            source_code[position + 1]
            if position + 1 < source_length
            else ""
        )

        # ----------------------------------------------------
        # 1. 处理单行注释
        # ----------------------------------------------------

        if in_line_comment:

            if current_char == "\n":
                in_line_comment = False

            position += 1
            continue

        # ----------------------------------------------------
        # 2. 处理块注释
        # ----------------------------------------------------

        if block_comment_depth > 0:

            if (
                current_char == "/"
                and next_char == "*"
            ):
                block_comment_depth += 1
                position += 2
                continue

            if (
                current_char == "*"
                and next_char == "/"
            ):
                block_comment_depth -= 1
                position += 2
                continue

            position += 1
            continue

        # ----------------------------------------------------
        # 3. 处理 PostgreSQL 美元引号
        # ----------------------------------------------------

        if dollar_quote is not None:

            if source_code.startswith(
                dollar_quote,
                position,
            ):
                position += len(dollar_quote)
                dollar_quote = None
                continue

            position += 1
            continue

        # ----------------------------------------------------
        # 4. 处理普通引号
        # ----------------------------------------------------

        if current_quote is not None:

            # 反斜杠转义，例如：
            #
            # 'it\\'s'
            if (
                current_char == "\\"
                and position + 1 < source_length
            ):
                position += 2
                continue

            if current_char == current_quote:

                # 连续两个相同引号表示转义：
                #
                # 'Tom''s order'
                if next_char == current_quote:
                    position += 2
                    continue

                current_quote = None

            position += 1
            continue

        # ----------------------------------------------------
        # 5. 开始单行注释
        # ----------------------------------------------------

        if (
            current_char == "-"
            and next_char == "-"
        ):
            in_line_comment = True
            position += 2
            continue

        # ----------------------------------------------------
        # 6. 开始块注释
        # ----------------------------------------------------

        if (
            current_char == "/"
            and next_char == "*"
        ):
            block_comment_depth = 1
            position += 2
            continue

        # ----------------------------------------------------
        # 7. 开始普通引号
        # ----------------------------------------------------

        if current_char in ("'", '"', "`"):
            current_quote = current_char
            position += 1
            continue

        # ----------------------------------------------------
        # 8. 开始 PostgreSQL 美元引号
        # ----------------------------------------------------

        if current_char == "$":

            matched_quote = _match_dollar_quote(
                source_code=source_code,
                position=position,
            )

            if matched_quote is not None:
                dollar_quote = matched_quote
                position += len(matched_quote)
                continue

        # ----------------------------------------------------
        # 9. 真正的 SQL 结束分号
        # ----------------------------------------------------

        if current_char == ";":

            chunk = _build_statement_chunk(
                complete_source=source_code,
                raw_start=statement_start,
                raw_end=position,
            )

            if chunk is not None:
                chunks.append(chunk)

            statement_start = position + 1
            position += 1
            continue

        position += 1

    # --------------------------------------------------------
    # 10. 处理最后一条没有分号的 SQL
    # --------------------------------------------------------

    final_chunk = _build_statement_chunk(
        complete_source=source_code,
        raw_start=statement_start,
        raw_end=source_length,
    )

    if final_chunk is not None:
        chunks.append(final_chunk)

    return chunks


# ============================================================
# 7. 判断文本块是否只有注释
# ============================================================

def _has_sql_tokens(
    source_sql: str,
    sqlglot_dialect: str | None,
) -> bool:
    """
    使用 SQLGlot tokenizer 判断文本中是否存在 SQL token。

    只有注释和空白的文本不算一条 SQL 语句。
    """

    try:
        tokens = sqlglot.tokenize(
            source_sql,
            read=sqlglot_dialect,
        )

        return bool(tokens)

    except TokenError:
        # Tokenizer 报错说明文本中存在无法识别的内容。
        # 仍然把它作为 SQL 交给正式解析流程，
        # 由正式解析流程记录 failed。
        return True


# ============================================================
# 8. 解析整个 SQL 文件
# ============================================================

def parse_sql_script(
    source_code: str,
    business_dialect: str,
) -> SqlScriptParseResult:
    """
    解析一个 SQL 文件中的全部 SQL 语句。

    参数：

        source_code:
            SQL 文件原始内容。

        business_dialect:
            hive、spark、postgresql 或 unknown。

    unknown 会使用 SQLGlot 通用方言解析。

    文件状态规则：

    1. 所有语句成功：
       parse_status = success

    2. 任意一条语句失败：
       parse_status = failed

    3. 没有可解析语句：
       parse_status = failed

    即使文件最终状态为 failed，
    已成功解析的语句仍然会保留。
    """

    if not isinstance(source_code, str):
        raise TypeError(
            "source_code 必须是字符串"
        )

    allowed_dialects = (
        *SUPPORTED_DIALECTS,
        "unknown",
    )

    if business_dialect not in allowed_dialects:
        raise ValueError(
            "不支持的业务方言："
            f"{business_dialect}。"
            "允许值为："
            f"{', '.join(allowed_dialects)}"
        )

    sqlglot_dialect = get_sqlglot_dialect(
        business_dialect
    )

    chunks = _split_sql_statements(
        source_code
    )

    parsed_statements: list[
        ParsedSqlStatement
    ] = []

    statement_no = 0

    for chunk in chunks:

        if not _has_sql_tokens(
            source_sql=chunk.source_sql,
            sqlglot_dialect=sqlglot_dialect,
        ):
            # 只有空白或注释，不计入 SQL 语句编号。
            continue

        statement_no += 1

        try:
            expression = parse_one(
                chunk.source_sql,
                read=sqlglot_dialect,
                error_level=ErrorLevel.RAISE,
            )

            statement_type = (
                type(expression)
                .__name__
                .lower()
            )

            normalized_sql = expression.sql(
                dialect=sqlglot_dialect,
                pretty=False,
            )

            parsed_statement = ParsedSqlStatement(
                statement_no=statement_no,
                source_sql=chunk.source_sql,
                line_start=chunk.line_start,
                line_end=chunk.line_end,
                parse_status="success",
                statement_type=statement_type,
                normalized_sql=normalized_sql,
                expression=expression,
                error_message=None,
            )

        except (ParseError, TokenError) as exc:

            parsed_statement = ParsedSqlStatement(
                statement_no=statement_no,
                source_sql=chunk.source_sql,
                line_start=chunk.line_start,
                line_end=chunk.line_end,
                parse_status="failed",
                statement_type=None,
                normalized_sql=None,
                expression=None,
                error_message=str(exc),
            )

        parsed_statements.append(
            parsed_statement
        )

    statements_tuple = tuple(
        parsed_statements
    )

    # --------------------------------------------------------
    # 文件中没有 SQL
    # --------------------------------------------------------

    if not statements_tuple:
        return SqlScriptParseResult(
            parse_status="failed",
            parse_error="文件中没有可解析的 SQL 语句",
            statements=(),
        )

    # --------------------------------------------------------
    # 收集失败语句
    # --------------------------------------------------------

    failed_statements = [
        statement
        for statement in statements_tuple
        if statement.parse_status == "failed"
    ]

    if failed_statements:

        error_summaries = [
            (
                f"第 {statement.statement_no} 条语句"
                f"（第 {statement.line_start}-"
                f"{statement.line_end} 行）解析失败："
                f"{statement.error_message}"
            )
            for statement in failed_statements
        ]

        return SqlScriptParseResult(
            parse_status="failed",
            parse_error="\n".join(
                error_summaries
            ),
            statements=statements_tuple,
        )

    # --------------------------------------------------------
    # 全部成功
    # --------------------------------------------------------

    return SqlScriptParseResult(
        parse_status="success",
        parse_error=None,
        statements=statements_tuple,
    )