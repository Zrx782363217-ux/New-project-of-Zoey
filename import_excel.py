from __future__ import annotations

import argparse
import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import func, select
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

from app import identify_brand, identify_month, identify_platform, parse_excel_sources
from db import (
    create_upload_batch,
    daily_metrics,
    init_db,
    insert_import_errors,
    insert_raw_metrics,
    update_upload_batch,
    upsert_daily_metrics,
)


BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"


def joined_values(df: pd.DataFrame, column: str, fallback: str = "未识别") -> str:
    if df.empty or column not in df.columns:
        return fallback
    values = sorted({str(value).strip() for value in df[column].dropna() if str(value).strip()})
    return "、".join(values) if values else fallback


def print_zuihu_douyin_roi_audit(daily_df: pd.DataFrame) -> None:
    print("\n最护抖店 ROI 核对：")
    if daily_df.empty:
        print("无可核对数据")
        return
    audit = daily_df[
        (daily_df["brand"].astype(str).str.strip() == "最护")
        & (daily_df["platform"].astype(str).str.strip() == "抖店")
    ].copy()
    if audit.empty:
        print("未解析到最护 / 抖店数据")
        return
    for column in ["gmv", "ad_spend", "roi"]:
        if column not in audit.columns:
            audit[column] = pd.NA
    if "roi_source" not in audit.columns:
        audit["roi_source"] = "缺失"
    audit["date"] = pd.to_datetime(audit["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    audit = audit.sort_values(["date", "channel"], ascending=[False, True])
    print(audit[["date", "channel", "gmv", "ad_spend", "roi", "roi_source"]].head(30).to_string(index=False))
    if len(audit) > 30:
        print(f"仅显示最近 30 条，共 {len(audit)} 条。")


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


def print_import_preview(excel_files: list[Path], raw_df, daily_df) -> None:
    print("\n导入前预览：")
    for path in excel_files:
        file_raw = raw_df[raw_df["source_file"] == path.name] if not raw_df.empty else pd.DataFrame()
        if not daily_df.empty and "source_file" in daily_df.columns:
            file_daily = daily_df[
                daily_df["source_file"].fillna("").astype(str).str.contains(path.name, regex=False)
            ].copy()
        else:
            file_daily = pd.DataFrame()
        month = identify_month(path.name)
        min_date = file_raw["date"].min() if not file_raw.empty else None
        max_date = file_raw["date"].max() if not file_raw.empty else None
        if month is None and not pd.isna(min_date):
            month = pd.Timestamp(min_date).month
        date_range = "未解析到日期" if pd.isna(min_date) or pd.isna(max_date) else f"{pd.Timestamp(min_date).date()} ~ {pd.Timestamp(max_date).date()}"
        print(f"文件：{path.name}")
        print(f"识别品牌：{joined_values(file_raw, 'brand', identify_brand(path.name))}")
        print(f"识别平台：{joined_values(file_raw, 'platform', identify_platform(path.name))}")
        print(f"识别渠道：{joined_values(file_raw, 'channel')}")
        print(f"识别月份：{month if month is not None else '未识别'}")
        print(f"解析日期范围：{date_range}")
        original_roi_found = (
            not file_raw.empty
            and "metric_std" in file_raw.columns
            and (file_raw["metric_std"] == "roi").any()
        )
        print(f"原始 ROI 指标是否识别到：{'是' if original_roi_found else '否'}")
        print("daily_metrics ROI 前 5 条样例：")
        if file_daily.empty:
            print("无")
        else:
            for column in ["date", "channel", "roi", "roi_source"]:
                if column not in file_daily.columns:
                    file_daily[column] = pd.NA
            sample = file_daily[["date", "channel", "roi", "roi_source"]].sort_values(
                ["date", "channel"]
            ).head(5)
            print(sample.to_string(index=False))
        print()

    if daily_df.empty:
        print("最终 daily_metrics 日期范围：无")
        print("最终 daily_metrics 品牌：无")
        print("最终 daily_metrics 平台：无")
        print("最终 daily_metrics 渠道：无")
        return

    print(f"最终 daily_metrics 日期范围：{daily_df['date'].min().date()} ~ {daily_df['date'].max().date()}")
    print("最终 daily_metrics 品牌：" + "、".join(sorted(daily_df["brand"].dropna().astype(str).unique())))
    print("最终 daily_metrics 平台：" + "、".join(sorted(daily_df["platform"].dropna().astype(str).unique())))
    print("最终 daily_metrics 渠道：" + "、".join(sorted(daily_df["channel"].dropna().astype(str).unique())))
    print()
    print_zuihu_douyin_roi_audit(daily_df)
    print()


def database_summary(engine) -> tuple[int, str]:
    with engine.connect() as conn:
        total_rows = conn.execute(select(func.count()).select_from(daily_metrics)).scalar_one()
        latest_date = conn.execute(select(func.max(daily_metrics.c.date))).scalar_one()
    return int(total_rows or 0), str(latest_date) if latest_date else "无"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导入 data 文件夹中的电商复盘 Excel。")
    parser.add_argument("--dry-run", action="store_true", help="只解析和打印预览，不写入数据库。")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    database_url = get_local_database_url()
    if not database_url and not args.dry_run:
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

    if raw_df.empty or daily_df.empty:
        print_import_preview(excel_files, raw_df, daily_df)
        print(f"成功解析 raw_metrics：{len(raw_df)} 条")
        print(f"成功生成 daily_metrics：{len(daily_df)} 条")
        print("新增数量：0")
        print("更新数量：0")
        print(f"失败数量：{len(failure_reasons) or 1}")
        print("失败原因：未解析出可入库的经营数据。")
        for reason in failure_reasons:
            print(f"- {reason}")
        return 1

    print_import_preview(excel_files, raw_df, daily_df)

    if args.dry_run:
        print("dry-run 模式：只解析和打印预览，不写入数据库。")
        print(f"读取文件数：{len(excel_files)}")
        print(f"raw_metrics 条数：{len(raw_df)}")
        print(f"daily_metrics 条数：{len(daily_df)}")
        print(f"本次导入日期范围：{daily_df['date'].min().date()} ~ {daily_df['date'].max().date()}")
        print("本次导入涉及品牌：" + "、".join(sorted(daily_df["brand"].dropna().astype(str).unique())))
        print("本次导入涉及平台：" + "、".join(sorted(daily_df["platform"].dropna().astype(str).unique())))
        print("本次导入涉及渠道：" + "、".join(sorted(daily_df["channel"].dropna().astype(str).unique())))
        if failure_reasons:
            print("解析提示：")
            for reason in failure_reasons:
                print(f"- {reason}")
        return 0

    try:
        engine = create_engine(database_url, pool_pre_ping=True, future=True)
        init_db(engine)
    except SQLAlchemyError as exc:
        print("导入失败：数据库连接或初始化失败。")
        print("请检查 .env 中的 DATABASE_URL 是否为 Supabase Connect 提供的 pooler URI。")
        print("DATABASE_URL 不应包含 db.xxxxx.supabase.co，建议使用 Session pooler 或 Transaction pooler。")
        print(f"错误信息：{exc}")
        return 1
    except Exception as exc:
        print("导入失败：数据库连接或初始化失败。")
        print("请检查网络、DATABASE_URL、数据库密码和 Supabase pooler 地址。")
        print(f"错误信息：{exc}")
        return 1

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

    db_total_rows, db_latest_date = database_summary(engine)

    print(f"读取文件数：{len(excel_files)}")
    print(f"raw_metrics 条数：{len(raw_df)}")
    print(f"daily_metrics 条数：{len(daily_df)}")
    print(f"新增数量：{upsert_counts['inserted']}")
    print(f"更新数量：{upsert_counts['updated']}")
    print(f"失败数量：{error_count}")
    print(f"数据库 daily_metrics 当前总行数：{db_total_rows}")
    print(f"当前数据库最新日期：{db_latest_date}")
    print(f"本次导入日期范围：{daily_df['date'].min().date()} ~ {daily_df['date'].max().date()}")
    print("本次导入涉及品牌：" + "、".join(sorted(daily_df["brand"].dropna().astype(str).unique())))
    print("本次导入涉及平台：" + "、".join(sorted(daily_df["platform"].dropna().astype(str).unique())))
    print("本次导入涉及渠道：" + "、".join(sorted(daily_df["channel"].dropna().astype(str).unique())))
    if failure_reasons:
        print("失败原因：")
        for reason in failure_reasons:
            print(f"- {reason}")
    else:
        print("失败原因：无")

    print("\n提示：如果之前错误导入过 6 月数据，并且怀疑覆盖了 7 月数据，请不要让脚本自动清库。")
    print("最简单的修复方式是：你确认后手动清空 daily_metrics 和 raw_metrics 测试数据，再用修复后的脚本重新导入 6 月，然后重新导入 7 月。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
