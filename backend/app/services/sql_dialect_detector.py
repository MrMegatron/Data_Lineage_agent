from __future__ import annotations

import re
from dataclasses import dataclass


# ============================================================
# 1. 系统支持的业务方言
# ============================================================

SUPPORTED_DIALECTS = (
    "hive",
    "spark",
    "postgresql",
)


# ============================================================
# 2. 业务方言和 SQLGlot 方言名称映射
# ============================================================

SQLGLOT_DIALECT_MAP = {
    "hive": "hive",
    "spark": "spark",
    "postgresql": "postgres",
}


# ============================================================
# 3. 方言识别结果
# ============================================================

@dataclass(frozen=True, slots=True)
class DialectDetectionResult:
    """
    SQL 方言识别结果。

    dialect:
        hive、spark、postgresql 或 unknown。

    confidence:
        high、medium、low 或 unknown。

    reasons:
        本次判断使用了哪些证据。
    """

    dialect: str
    confidence: str
    reasons: tuple[str, ...]


# ============================================================
# 4. 文件路径识别规则
# ============================================================

PATH_RULES = {
    "spark": (
        (
            r"(^|[/_.-])"
            r"(spark|pyspark|databricks)"
            r"(?=$|[/_.-])"
        ),
        4,
        "文件路径包含 Spark、PySpark 或 Databricks 标记",
    ),

    "hive": (
        (
            r"(^|[/_.-])"
            r"(hive|hql)"
            r"(?=$|[/_.-])"
        ),
        4,
        "文件路径包含 Hive 或 HQL 标记",
    ),

    "postgresql": (
        (
            r"(^|[/_.-])"
            r"(postgres|postgresql|pgsql)"
            r"(?=$|[/_.-])"
        ),
        4,
        "文件路径包含 PostgreSQL、Postgres 或 PGSQL 标记",
    ),
}


# ============================================================
# 5. SQL 内容识别规则
# ============================================================

CONTENT_RULES = {
    "spark": (
        (
            r"\bUSING\s+"
            r"(DELTA|PARQUET|ORC|JSON|CSV)\b",
            3,
            "发现 Spark 常见的 USING 数据源语法",
        ),
        (
            r"\bCREATE\s+"
            r"(OR\s+REPLACE\s+)?"
            r"TEMP(?:ORARY)?\s+VIEW\b",
            3,
            "发现 Spark 临时视图语法",
        ),
        (
            r"\bCACHE\s+TABLE\b",
            3,
            "发现 Spark CACHE TABLE 语法",
        ),
        (
            r"\bDESCRIBE\s+DETAIL\b",
            4,
            "发现 Databricks/Spark DESCRIBE DETAIL 语法",
        ),
    ),

    "hive": (
        (
            r"\bROW\s+FORMAT\s+"
            r"(DELIMITED|SERDE)\b",
            4,
            "发现 Hive ROW FORMAT 语法",
        ),
        (
            r"\bSTORED\s+AS\s+\w+\b",
            3,
            "发现 Hive STORED AS 语法",
        ),
        (
            r"\bCLUSTERED\s+BY\s*\(",
            3,
            "发现 Hive CLUSTERED BY 语法",
        ),
        (
            r"\bINSERT\s+OVERWRITE\s+TABLE\b",
            2,
            "发现 Hive 常见的 INSERT OVERWRITE TABLE 语法",
        ),
    ),

    "postgresql": (
        (
            r"::\s*"
            r"[A-Z_][A-Z0-9_]*(?:\s*\[\s*\])?",
            3,
            "发现 PostgreSQL 双冒号类型转换语法",
        ),
        (
            r"\bDISTINCT\s+ON\s*\(",
            4,
            "发现 PostgreSQL DISTINCT ON 语法",
        ),
        (
            r"\bRETURNING\b",
            3,
            "发现 PostgreSQL RETURNING 语法",
        ),
        (
            r"\bILIKE\b",
            2,
            "发现 PostgreSQL ILIKE 语法",
        ),
        (
            r"\bJSONB\b",
            3,
            "发现 PostgreSQL JSONB 类型",
        ),
        (
            r"\b"
            r"(SMALLSERIAL|BIGSERIAL|SERIAL)"
            r"\b",
            3,
            "发现 PostgreSQL SERIAL 类型",
        ),
    ),
}


# ============================================================
# 6. 计算置信度
# ============================================================

def _calculate_confidence(
    highest_score: int,
    second_score: int,
) -> str:
    """
    根据最高得分和第二名得分计算置信度。

    不只看最高分，还要看第一名是否明显领先第二名。
    """

    score_difference = highest_score - second_score

    if (
        highest_score >= 6
        or (
            highest_score >= 4
            and score_difference >= 2
        )
    ):
        return "high"

    if highest_score >= 3:
        return "medium"

    return "low"


# ============================================================
# 7. SQL 方言识别
# ============================================================

def detect_sql_dialect(
    source_code: str,
    relative_path: str = "",
) -> DialectDetectionResult:
    """
    根据 SQL 内容和相对路径识别 SQL 方言。

    识别流程：

    1. 检查文件路径特征；
    2. 检查 SQL 内容特征；
    3. 为不同方言累计得分；
    4. 如果没有明确证据，返回 unknown；
    5. 如果最高分并列，返回 unknown；
    6. 只有一个方言明确领先时，才返回具体方言。

    参数：

        source_code:
            SQL 文件内容。

        relative_path:
            SQL 文件相对于扫描目录的路径。

    返回：

        DialectDetectionResult
    """

    if not isinstance(source_code, str):
        raise TypeError(
            "source_code 必须是字符串"
        )

    if not isinstance(relative_path, str):
        raise TypeError(
            "relative_path 必须是字符串"
        )

    scores = {
        dialect: 0
        for dialect in SUPPORTED_DIALECTS
    }

    reasons = {
        dialect: []
        for dialect in SUPPORTED_DIALECTS
    }

    # --------------------------------------------------------
    # 1. 检查文件路径
    # --------------------------------------------------------

    normalized_path = (
        relative_path
        .replace("\\", "/")
        .lower()
    )

    for dialect, rule in PATH_RULES.items():
        pattern, points, reason = rule

        if re.search(
            pattern,
            normalized_path,
            flags=re.IGNORECASE,
        ):
            scores[dialect] += points
            reasons[dialect].append(reason)

    # --------------------------------------------------------
    # 2. 检查 SQL 内容
    # --------------------------------------------------------

    for dialect, dialect_rules in CONTENT_RULES.items():

        for pattern, points, reason in dialect_rules:

            if re.search(
                pattern,
                source_code,
                flags=(
                    re.IGNORECASE
                    | re.MULTILINE
                ),
            ):
                scores[dialect] += points
                reasons[dialect].append(reason)

    # --------------------------------------------------------
    # 3. 找出最高得分
    # --------------------------------------------------------

    highest_score = max(scores.values())

    # 所有方言都是 0 分，说明没有发现方言特征。
    if highest_score == 0:
        return DialectDetectionResult(
            dialect="unknown",
            confidence="unknown",
            reasons=(
                "没有发现足以区分 SQL 方言的特征",
            ),
        )

    winning_dialects = [
        dialect
        for dialect, score in scores.items()
        if score == highest_score
    ]

    # --------------------------------------------------------
    # 4. 最高分并列时不猜测
    # --------------------------------------------------------

    if len(winning_dialects) > 1:
        dialect_names = ", ".join(
            sorted(winning_dialects)
        )

        return DialectDetectionResult(
            dialect="unknown",
            confidence="unknown",
            reasons=(
                "多个 SQL 方言得分并列，"
                f"无法安全判断：{dialect_names}",
            ),
        )

    detected_dialect = winning_dialects[0]

    # --------------------------------------------------------
    # 5. 取得第二名分数
    # --------------------------------------------------------

    other_scores = [
        score
        for dialect, score in scores.items()
        if dialect != detected_dialect
    ]

    second_score = max(other_scores)

    confidence = _calculate_confidence(
        highest_score=highest_score,
        second_score=second_score,
    )

    return DialectDetectionResult(
        dialect=detected_dialect,
        confidence=confidence,
        reasons=tuple(
            reasons[detected_dialect]
        ),
    )


# ============================================================
# 8. 转换成 SQLGlot 方言名称
# ============================================================

def get_sqlglot_dialect(
    business_dialect: str,
) -> str | None:
    """
    将系统内部方言名称转换成 SQLGlot 使用的名称。

    对应关系：

        hive        -> hive
        spark       -> spark
        postgresql  -> postgres
        unknown     -> None
    """

    return SQLGLOT_DIALECT_MAP.get(
        business_dialect
    )