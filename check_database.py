from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import SQLAlchemyError

from db import daily_metrics, normalize_database_url


BASE_DIR = Path(__file__).parent


def get_local_database_url() -> str | None:
    load_dotenv(BASE_DIR / ".env", override=True)
    return normalize_database_url(os.getenv("DATABASE_URL"))


def main() -> int:
    database_url = get_local_database_url()
    if not database_url:
        print("检查失败：请先在项目根目录 .env 中配置 DATABASE_URL。")
        return 1

    try:
        engine = create_engine(database_url, pool_pre_ping=True, future=True)
        with engine.connect() as connection:
            total_rows = connection.execute(
                select(func.count()).select_from(daily_metrics)
            ).scalar_one()
            latest_date = connection.execute(select(func.max(daily_metrics.c.date))).scalar_one()
            statement = (
                select(
                    daily_metrics.c.date,
                    daily_metrics.c.channel,
                    daily_metrics.c.gmv,
                    daily_metrics.c.ad_spend,
                    daily_metrics.c.roi,
                    daily_metrics.c.net_roi,
                    daily_metrics.c.source_file,
                    daily_metrics.c.source_sheet,
                )
                .where(
                    daily_metrics.c.brand == "最护",
                    daily_metrics.c.platform == "抖店",
                )
                .order_by(daily_metrics.c.date.desc(), daily_metrics.c.channel.asc())
                .limit(30)
            )
            audit_df = pd.read_sql(statement, connection)
    except SQLAlchemyError as exc:
        print("数据库检查失败：无法连接或查询 Supabase PostgreSQL。")
        print("请检查网络、数据库密码和 .env 中的 pooler DATABASE_URL。")
        print(f"错误类型：{type(exc).__name__}")
        return 1
    except Exception as exc:
        print("数据库检查失败。")
        print(f"错误类型：{type(exc).__name__}")
        return 1

    print(f"daily_metrics 总行数：{int(total_rows or 0)}")
    print(f"数据库最新日期：{latest_date or '无'}")
    print("\n最护 / 抖店最近 30 条 ROI 数据：")
    if audit_df.empty:
        print("未找到 brand=最护、platform=抖店 的数据。")
        return 0

    audit_df["date"] = pd.to_datetime(audit_df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    print(audit_df.to_string(index=False))
    print("\n口径提示：BOSS首页应使用 channel=整体，不应把商品卡、直播、短视频等子渠道再次合并。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
