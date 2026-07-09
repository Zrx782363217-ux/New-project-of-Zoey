from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine

from app import parse_excel_sources
from db import (
    create_upload_batch,
    init_db,
    insert_import_errors,
    insert_raw_metrics,
    update_upload_batch,
    upsert_daily_metrics,
)


BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"


def get_local_database_url() -> str:
    load_dotenv(BASE_DIR / ".env", override=True)
    database_url = os.getenv("DATABASE_URL", "")
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql+psycopg2://", 1)
    return database_url


def discover_excel_files() -> list[Path]:
    DATA_DIR.mkdir(exist_ok=True)
    files = sorted(DATA_DIR.glob("*.xlsx")) + sorted(DATA_DIR.glob("*.xls"))
    return [path for path in files if not path.name.startswith("~$")]


def main() -> int:
    database_url = get_local_database_url()
    if not database_url:
        print("导入失败：请先在 .env 或系统环境变量中配置 DATABASE_URL。")
        return 1

    excel_files = discover_excel_files()
    if not excel_files:
        print(f"导入失败：{DATA_DIR} 中没有找到 Excel 文件。")
        return 1

    print("读取文件：")
    for path in excel_files:
        print(f"- {path.name}")

    opened_files = []
    try:
        for path in excel_files:
            opened_files.append((path.name, open(path, "rb")))

        raw_df, daily_df, warnings, missing = parse_excel_sources(opened_files)
    finally:
        for _, handle in opened_files:
            handle.close()

    failure_reasons = list(warnings)
    if missing:
        failure_reasons.append("缺少预期文件：" + "、".join(missing))

    if raw_df.empty or daily_df.empty:
        print(f"成功解析 raw_metrics：{len(raw_df)} 条")
        print(f"成功生成 daily_metrics：{len(daily_df)} 条")
        print("新增数量：0")
        print("更新数量：0")
        print(f"失败数量：{len(failure_reasons) or 1}")
        print("失败原因：未解析出可入库的经营数据。")
        for reason in failure_reasons:
            print(f"- {reason}")
        return 1

    engine = create_engine(database_url, pool_pre_ping=True, future=True)
    init_db(engine)

    status = "partial" if failure_reasons else "success"
    batch_id = create_upload_batch(
        file_names=[path.name for path in excel_files],
        uploader_name="local-import",
        status=status,
        message="后台脚本导入开始。",
        engine=engine,
    )

    raw_count = insert_raw_metrics(raw_df, batch_id, engine=engine)
    upsert_counts = upsert_daily_metrics(daily_df, batch_id, raw_df=raw_df, engine=engine)
    error_count = insert_import_errors(failure_reasons, batch_id, engine=engine)

    message = (
        f"后台导入完成：raw_metrics {raw_count} 条，"
        f"daily_metrics 新增 {upsert_counts['inserted']} 条，更新 {upsert_counts['updated']} 条，"
        f"失败/提示 {error_count} 条。"
    )
    if batch_id is not None:
        update_upload_batch(batch_id, status=status, message=message, engine=engine)

    print(f"成功解析 raw_metrics：{len(raw_df)} 条")
    print(f"成功生成 daily_metrics：{len(daily_df)} 条")
    print(f"新增数量：{upsert_counts['inserted']}")
    print(f"更新数量：{upsert_counts['updated']}")
    print(f"失败数量：{error_count}")
    if failure_reasons:
        print("失败原因：")
        for reason in failure_reasons:
            print(f"- {reason}")
    else:
        print("失败原因：无")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
