from uuid import uuid4

import pytest

from app.models import (
    ColumnLineage,
    DataColumn,
    DataTable,
    LineageEvidence,
    LineageProject,
    SourceScript,
)
from app.services.column_lineage_explanation_service import (
    explain_column_lineage,
)


def create_explanation_test_data(
    db_session,
):
    """
    创建：

        ods.orders.price
        ods.orders.quantity
            -> dwd.order_detail.amount
    """

    project = LineageProject(
        name=(
            "explanation_test_"
            f"{uuid4().hex[:12]}"
        ),
        description="字段血缘解释测试",
    )

    db_session.add(project)
    db_session.flush()

    script = SourceScript(
        project_id=project.id,
        file_name="order_detail.sql",
        relative_path=(
            "hive/order_detail.sql"
        ),
        dialect="hive",
        file_hash="e" * 64,
        source_code=(
            "INSERT INTO dwd.order_detail "
            "(amount) "
            "SELECT price * quantity AS amount "
            "FROM ods.orders;"
        ),
        parse_status="success",
    )

    source_table = DataTable(
        project_id=project.id,
        schema_name="ods",
        table_name="orders",
        full_name="ods.orders",
        table_kind="physical",
    )

    target_table = DataTable(
        project_id=project.id,
        schema_name="dwd",
        table_name="order_detail",
        full_name="dwd.order_detail",
        table_kind="physical",
    )

    db_session.add_all([
        script,
        source_table,
        target_table,
    ])

    db_session.flush()

    price = DataColumn(
        table_id=source_table.id,
        column_name="price",
    )

    quantity = DataColumn(
        table_id=source_table.id,
        column_name="quantity",
    )

    amount = DataColumn(
        table_id=target_table.id,
        column_name="amount",
    )

    db_session.add_all([
        price,
        quantity,
        amount,
    ])

    db_session.flush()

    price_lineage = ColumnLineage(
        project_id=project.id,
        script_id=script.id,
        target_column_id=amount.id,
        source_column_id=price.id,
        relation_type="transform",
        resolution_status="confirmed",
        expression_text="price * quantity",
        statement_no=1,
    )

    quantity_lineage = ColumnLineage(
        project_id=project.id,
        script_id=script.id,
        target_column_id=amount.id,
        source_column_id=quantity.id,
        relation_type="transform",
        resolution_status="confirmed",
        expression_text="price * quantity",
        statement_no=1,
    )

    db_session.add_all([
        price_lineage,
        quantity_lineage,
    ])

    db_session.flush()

    # 两条血缘绑定相同代码片段。
    # 解释服务展示时应去重成一条证据。
    price_evidence = LineageEvidence(
        column_lineage_id=(
            price_lineage.id
        ),
        script_id=script.id,
        statement_no=1,
        evidence_order=1,
        line_start=1,
        line_end=1,
        code_snippet=(
            "price * quantity AS amount"
        ),
        expression_text=(
            "price * quantity"
        ),
    )

    quantity_evidence = LineageEvidence(
        column_lineage_id=(
            quantity_lineage.id
        ),
        script_id=script.id,
        statement_no=1,
        evidence_order=1,
        line_start=1,
        line_end=1,
        code_snippet=(
            "price * quantity AS amount"
        ),
        expression_text=(
            "price * quantity"
        ),
    )

    db_session.add_all([
        price_evidence,
        quantity_evidence,
    ])

    db_session.flush()

    return {
        "project": project,
        "script": script,
        "price": price,
        "quantity": quantity,
        "amount": amount,
    }


def test_explain_transform_lineage(
    db_session,
):
    data = create_explanation_test_data(
        db_session
    )

    result = explain_column_lineage(
        db=db_session,
        column_id=data["amount"].id,
    )

    assert result.has_confirmed_lineage is True

    assert (
        result.target_full_name
        == "dwd.order_detail.amount"
    )

    assert len(result.sources) == 2

    assert {
        source.source_full_name
        for source in result.sources
    } == {
        "ods.orders.price",
        "ods.orders.quantity",
    }

    assert all(
        source.relation_type
        == "transform"
        for source in result.sources
    )

    assert (
        "ods.orders.price"
        in result.natural_language
    )

    assert (
        "ods.orders.quantity"
        in result.natural_language
    )

    assert (
        "price * quantity"
        in result.natural_language
    )

    assert (
        "dwd.order_detail.amount"
        in result.pseudocode
    )

    assert (
        "price * quantity"
        in result.pseudocode
    )

    # 两条相同证据被合并成一条。
    assert len(result.evidences) == 1

    assert (
        result.evidences[0].code_snippet
        == "price * quantity AS amount"
    )


def test_explanation_ignores_unresolved_lineage(
    db_session,
):
    data = create_explanation_test_data(
        db_session
    )

    unknown_source = DataColumn(
        table_id=data["price"].table_id,
        column_name="unknown_field",
    )

    db_session.add(
        unknown_source
    )

    db_session.flush()

    unresolved = ColumnLineage(
        project_id=data["project"].id,
        script_id=data["script"].id,
        target_column_id=data["amount"].id,
        source_column_id=unknown_source.id,
        relation_type="unknown",
        resolution_status="unresolved",
        expression_text="unknown_field",
        statement_no=1,
    )

    db_session.add(
        unresolved
    )

    db_session.flush()

    result = explain_column_lineage(
        db=db_session,
        column_id=data["amount"].id,
    )

    assert {
        source.source_column_name
        for source in result.sources
    } == {
        "price",
        "quantity",
    }

    assert "unknown_field" not in (
        result.natural_language
    )


def test_explain_column_without_lineage(
    db_session,
):
    project = LineageProject(
        name=(
            "empty_explanation_"
            f"{uuid4().hex[:12]}"
        ),
    )

    db_session.add(project)
    db_session.flush()

    table = DataTable(
        project_id=project.id,
        schema_name="dwd",
        table_name="empty_table",
        full_name="dwd.empty_table",
        table_kind="physical",
    )

    db_session.add(table)
    db_session.flush()

    column = DataColumn(
        table_id=table.id,
        column_name="empty_column",
    )

    db_session.add(column)
    db_session.flush()

    result = explain_column_lineage(
        db=db_session,
        column_id=column.id,
    )

    assert (
        result.has_confirmed_lineage
        is False
    )

    assert result.sources == ()
    assert result.evidences == ()

    assert (
        "没有已确认的字段血缘"
        in result.natural_language
    )

    assert result.pseudocode == (
        "dwd.empty_table.empty_column "
        ":= UNKNOWN"
    )


def test_explain_missing_column(
    db_session,
):
    with pytest.raises(
        ValueError,
        match="字段不存在",
    ):
        explain_column_lineage(
            db=db_session,
            column_id=999999999,
        )


def test_reject_invalid_column_id(
    db_session,
):
    with pytest.raises(
        ValueError,
        match="column_id 必须大于 0",
    ):
        explain_column_lineage(
            db=db_session,
            column_id=0,
        )