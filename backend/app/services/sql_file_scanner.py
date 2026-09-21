from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path


# ============================================================
# 1. SQL 文件扫描异常
# ============================================================

class SqlFileScanError(ValueError):
    """
    扫描 SQL 文件时产生的业务异常。

    例如：
    - 扫描目录不存在
    - 扫描路径不是目录
    - SQL 文件编码无法识别
    """


# ============================================================
# 2. 单个 SQL 文件的扫描结果
# ============================================================

@dataclass(frozen=True, slots=True)
class ScannedSqlFile:
    """
    表示一个扫描完成的 SQL 文件。

    这个对象目前只存在于 Python 内存中，
    本步骤还不会把它写入数据库。
    """

    # SQL 文件的绝对路径
    absolute_path: Path

    # SQL 文件相对于扫描根目录的路径
    relative_path: str

    # 文件名称，例如 order_detail.sql
    file_name: str

    # SQL 文件的文本内容
    source_code: str

    # 原始文件内容的 SHA-256
    file_hash: str

    # 成功读取文件时使用的字符编码
    encoding: str

    # 文件大小，单位是字节
    size_bytes: int


# ============================================================
# 3. SQL 文件解码
# ============================================================

def _decode_sql_file(
    raw_content: bytes,
    file_path: Path,
) -> tuple[str, str]:
    """
    把 SQL 文件的原始字节转换成字符串。

    读取顺序：

    1. utf-8-sig
       同时兼容普通 UTF-8 和带 BOM 的 UTF-8 文件。

    2. gb18030
       兼容 Windows 中文环境中常见的 GBK/GB18030 文件。

    返回值：

        (
            SQL 文本内容,
            实际使用的编码
        )
    """

    supported_encodings = (
        "utf-8-sig",
        "gb18030",
    )

    for encoding in supported_encodings:
        try:
            source_code = raw_content.decode(encoding)

            return source_code, encoding

        except UnicodeDecodeError:
            continue

    raise SqlFileScanError(
        "无法识别 SQL 文件编码："
        f"{file_path}。"
        "目前只支持 UTF-8、UTF-8 BOM、GBK 和 GB18030。"
    )


# ============================================================
# 4. 扫描 SQL 文件
# ============================================================

def scan_sql_files(
    root_directory: str | Path,
) -> list[ScannedSqlFile]:
    """
    递归扫描指定目录中的全部 SQL 文件。

    扫描规则：

    1. 扫描根目录以及所有子目录；
    2. 只处理扩展名为 .sql 的文件；
    3. 扩展名不区分大小写；
    4. 忽略 txt、py、md 等其他文件；
    5. 统一使用正斜杠保存相对路径；
    6. 按相对路径排序，保证结果稳定。
    """

    root_path = (
        Path(root_directory)
        .expanduser()
        .resolve()
    )

    # --------------------------------------------------------
    # 1. 检查目录是否存在
    # --------------------------------------------------------

    if not root_path.exists():
        raise SqlFileScanError(
            f"扫描目录不存在：{root_path}"
        )

    # --------------------------------------------------------
    # 2. 检查传入路径是不是目录
    # --------------------------------------------------------

    if not root_path.is_dir():
        raise SqlFileScanError(
            f"扫描路径不是目录：{root_path}"
        )

    # --------------------------------------------------------
    # 3. 递归查找全部 SQL 文件
    # --------------------------------------------------------

    sql_paths = [
        file_path
        for file_path in root_path.rglob("*")
        if (
            file_path.is_file()
            and file_path.suffix.lower()
            == ".sql"
        )
    ]

    # --------------------------------------------------------
    # 4. 按相对路径排序
    # --------------------------------------------------------

    sql_paths.sort(
        key=lambda file_path: (
            file_path
            .relative_to(root_path)
            .as_posix()
            .casefold()
        )
    )

    scanned_files: list[
        ScannedSqlFile
    ] = []

    # --------------------------------------------------------
    # 5. 逐个读取所有 SQL 文件
    # --------------------------------------------------------

    for file_path in sql_paths:

        raw_content = (
            file_path.read_bytes()
        )

        source_code, encoding = (
            _decode_sql_file(
                raw_content=raw_content,
                file_path=file_path,
            )
        )

        file_hash = sha256(
            raw_content
        ).hexdigest()

        relative_path = (
            file_path
            .relative_to(root_path)
            .as_posix()
        )

        scanned_file = ScannedSqlFile(
            absolute_path=file_path,
            relative_path=relative_path,
            file_name=file_path.name,
            source_code=source_code,
            file_hash=file_hash,
            encoding=encoding,
            size_bytes=len(raw_content),
        )

        scanned_files.append(
            scanned_file
        )

    # 这里必须位于 for 循环外面。
    #
    # 所有文件处理完成后，才统一返回。
    return scanned_files