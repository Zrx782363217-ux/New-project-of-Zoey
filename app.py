from __future__ import annotations

import re
import os
from datetime import date
from html import escape
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv

from db import get_engine, init_db, load_daily_metrics


load_dotenv(override=True)

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_CSV = OUTPUT_DIR / "normalized_data.csv"

STANDARD_COLUMNS = [
    "date",
    "brand",
    "platform",
    "channel",
    "gmv",
    "net_gmv",
    "orders",
    "ad_spend",
    "roi",
    "net_roi",
    "refund_rate",
    "refund_amount",
    "conversion_rate",
    "click_to_order_rate",
    "commission",
]

DISPLAY_COLUMNS = STANDARD_COLUMNS.copy()

COLOR_PALETTE = {
    "最护-抖店": "#2563EB",
    "最护-拼多多": "#059669",
    "碧维-抖店": "#F97316",
    "碧维-拼多多": "#16A34A",
    "最护-抖店-整体": "#2563EB",
    "最护-抖店-商品卡": "#7C3AED",
    "最护-抖店-直播": "#0EA5E9",
    "最护-抖店-短视频": "#6366F1",
    "最护-抖店-店铺号商品卡": "#4F46E5",
    "最护-抖店-洗脸巾直播": "#0891B2",
    "最护-拼多多-整体": "#1D4ED8",
    "最护-拼多多-商品卡": "#10B981",
    "最护-抖店-千川投放": "#9333EA",
    "最护-抖店-千川·直播": "#9333EA",
    "最护-抖店-千川·商品卡": "#A855F7",
    "最护-抖店-千川·洗脸巾直播": "#DB2777",
    "最护-抖店-千川·店铺号商品卡": "#C026D3",
    "最护-抖店-千川·短视频": "#BE185D",
    "碧维-抖店-整体": "#F97316",
    "碧维-抖店-商品卡": "#EA580C",
    "碧维-抖店-直播": "#FB923C",
    "碧维-抖店-短视频": "#F59E0B",
    "碧维-拼多多-整体": "#16A34A",
    "碧维-拼多多-商品卡": "#22C55E",
    "碧维-抖店-千川投放": "#DB2777",
}

FALLBACK_COLORS = [
    "#2563EB",
    "#F97316",
    "#16A34A",
    "#7C3AED",
    "#DC2626",
    "#0891B2",
    "#CA8A04",
    "#DB2777",
    "#4F46E5",
    "#059669",
    "#EA580C",
    "#9333EA",
]


def get_series_color(label: str) -> str:
    normalized = str(label).strip()
    if normalized in COLOR_PALETTE:
        return COLOR_PALETTE[normalized]
    if "千川" in normalized:
        qianchuan_colors = ["#9333EA", "#A855F7", "#C026D3", "#DB2777", "#7E22CE", "#BE185D"]
        checksum = sum((index + 1) * ord(char) for index, char in enumerate(normalized))
        return qianchuan_colors[checksum % len(qianchuan_colors)]
    if "拼多多" in normalized:
        return "#059669" if normalized.startswith("最护-") else "#16A34A"
    if normalized.startswith("最护-"):
        cold_colors = ["#2563EB", "#7C3AED", "#0EA5E9", "#4F46E5", "#0891B2"]
        checksum = sum((index + 1) * ord(char) for index, char in enumerate(normalized))
        return cold_colors[checksum % len(cold_colors)]
    if normalized.startswith("碧维-"):
        warm_colors = ["#F97316", "#EA580C", "#FB923C", "#F59E0B", "#16A34A"]
        checksum = sum((index + 1) * ord(char) for index, char in enumerate(normalized))
        return warm_colors[checksum % len(warm_colors)]
    checksum = sum((index + 1) * ord(char) for index, char in enumerate(normalized))
    return FALLBACK_COLORS[checksum % len(FALLBACK_COLORS)]


def get_qianchuan_channel_label(channel) -> str:
    channel_text = str(channel).strip() if pd.notna(channel) else ""
    if not channel_text or channel_text in {"整体", "千川"}:
        return "千川投放"
    return f"千川·{channel_text}"


def get_series_label(brand, platform, channel) -> str:
    brand_text = str(brand).strip()
    platform_text = str(platform).strip()
    if platform_text == "千川":
        return f"{brand_text}-抖店-{get_qianchuan_channel_label(channel)}"
    return f"{brand_text}-{platform_text}-{str(channel).strip()}"

METRIC_ALIASES = {
    "gmv": ["GMV", "整体成交", "成交金额", "支付金额", "销售额"],
    "net_gmv": ["净成交", "净GMV", "净销售额"],
    "orders": ["单量", "订单量", "成交单量", "支付订单数"],
    "ad_spend": ["投放消耗", "付费总消耗", "整体消耗", "消耗", "广告消耗"],
    "roi": ["ROI", "整体ROI", "付费ROI"],
    "net_roi": ["净ROI", "净roi"],
    "refund_rate": ["退款率"],
    "refund_amount": ["退款金额"],
    "conversion_rate": ["成交转化率"],
    "click_to_order_rate": ["商品点击-成交率"],
    "commission": ["平台佣金"],
}

CHANNEL_KEYWORDS = ["店铺号商品卡", "洗脸巾直播", "商品卡", "短视频", "直播", "千川", "整体"]
RATE_METRICS = {"refund_rate", "conversion_rate", "click_to_order_rate"}
ROI_METRICS = {"roi", "net_roi"}
MONEY_OR_COUNT_METRICS = {
    "gmv",
    "net_gmv",
    "orders",
    "ad_spend",
    "refund_amount",
    "commission",
}


def identify_brand(filename: str) -> str:
    if "最护" in filename:
        return "最护"
    if "碧维" in filename:
        return "碧维"
    return "未知品牌"


def identify_platform(filename: str) -> str:
    if "抖店" in filename:
        return "抖店"
    if "拼多多" in filename:
        return "拼多多"
    if "千川" in filename:
        return "千川"
    return "未知平台"


def get_default_year() -> int:
    env_year = os.getenv("DEFAULT_YEAR", "").strip()
    if env_year.isdigit() and len(env_year) == 4:
        return int(env_year)
    return date.today().year


def identify_year(text: str) -> int | None:
    cleaned = clean_text(text)
    patterns = [
        r"(20\d{2})\s*年\s*(?:1[0-2]|0?[1-9])\s*月",
        r"(20\d{2})[-./](?:1[0-2]|0?[1-9])",
    ]
    for pattern in patterns:
        match = re.search(pattern, cleaned)
        if match:
            return int(match.group(1))
    return None


def identify_month(text: str) -> int | None:
    cleaned = clean_text(text)
    patterns = [
        r"20\d{2}\s*年\s*(1[0-2]|0?[1-9])\s*月份?",
        r"20\d{2}[-./](1[0-2]|0?[1-9])",
        r"(1[0-2]|0?[1-9])\s*月份?",
    ]
    for pattern in patterns:
        match = re.search(pattern, cleaned)
        if match:
            month = int(match.group(1))
            if 1 <= month <= 12:
                return month
    return None


def identify_period(*parts: str) -> tuple[int, int | None]:
    text = " ".join(clean_text(part) for part in parts if part)
    return identify_year(text) or get_default_year(), identify_month(text)


def clean_text(value) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    text = text.replace("\n", "").replace("\r", "").replace(" ", "")
    return "" if text.lower() in {"nan", "none"} else text


def normalize_metric_name(text: str) -> str:
    return re.sub(r"[\s:_：/\\（）()【】\[\]-]", "", clean_text(text)).lower()


def map_metric(metric: str) -> str | None:
    normalized = normalize_metric_name(metric)
    if not normalized:
        return None
    for standard, aliases in METRIC_ALIASES.items():
        for alias in aliases:
            if normalize_metric_name(alias) in normalized:
                return standard
    return None


def detect_channel(*parts: str) -> str:
    text = " ".join(clean_text(part) for part in parts if part)
    for keyword in CHANNEL_KEYWORDS:
        if keyword in text:
            return keyword
    return "整体"


def parse_date_cell(value, default_year: int | None = None, file_month: int | None = None) -> pd.Timestamp | None:
    default_year = default_year or get_default_year()
    if pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.normalize()
    if hasattr(value, "date") and not isinstance(value, str):
        try:
            return pd.Timestamp(value).normalize()
        except Exception:
            pass

    text = clean_text(value)
    if not text:
        return None

    text = re.sub(r"\(.+?\)", "", text)
    text = re.sub(r"（.+?）", "", text)
    normalized = text.replace("月", ".").replace("日", "")
    normalized = normalized.replace("号", "").replace("/", ".").replace("-", ".")

    full_match = re.fullmatch(r"(20\d{2})\.(\d{1,2})\.(\d{1,2})", normalized)
    if full_match:
        try:
            return pd.Timestamp(year=int(full_match.group(1)), month=int(full_match.group(2)), day=int(full_match.group(3)))
        except ValueError:
            return None

    match = re.fullmatch(r"(\d{1,2})\.(\d{1,2})", normalized)
    if match:
        month = int(match.group(1))
        day = int(match.group(2))
        try:
            return pd.Timestamp(year=default_year, month=month, day=day)
        except ValueError:
            return None

    single_day = re.fullmatch(r"\d{1,2}", normalized)
    if single_day and file_month:
        try:
            return pd.Timestamp(year=default_year, month=file_month, day=int(normalized))
        except ValueError:
            return None
    if single_day:
        return None

    parsed = pd.to_datetime(text, errors="coerce")
    if pd.notna(parsed):
        parsed = pd.Timestamp(parsed).normalize()
        if parsed.year == 1900:
            parsed = parsed.replace(year=default_year)
        return parsed

    return None


def clean_number(value, metric_std: str | None = None) -> float | None:
    if pd.isna(value):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = clean_text(value)
        if not text or text in {"-", "—", "--", "/", "无"}:
            return None
        percent = "%" in text
        text = (
            text.replace(",", "")
            .replace("，", "")
            .replace("￥", "")
            .replace("¥", "")
            .replace("%", "")
            .replace("元", "")
            .replace("单", "")
        )
        multiplier = 1.0
        if "万" in text:
            multiplier = 10000.0
            text = text.replace("万", "")
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        if not match:
            return None
        number = float(match.group()) * multiplier
        if percent:
            number = number / 100.0

    if metric_std in RATE_METRICS and number > 1:
        return number / 100.0
    if metric_std in ROI_METRICS and number > 20:
        return number / 100.0
    return number


def find_date_header(df: pd.DataFrame, default_year: int | None = None, file_month: int | None = None) -> tuple[int | None, dict[int, pd.Timestamp]]:
    best_row = None
    best_dates: dict[int, pd.Timestamp] = {}
    for row_idx in range(min(len(df), 40)):
        dates = {}
        for col_idx, value in enumerate(df.iloc[row_idx].tolist()):
            parsed = parse_date_cell(value, default_year=default_year, file_month=file_month)
            if parsed is not None:
                dates[col_idx] = parsed
        if len(dates) > len(best_dates):
            best_row = row_idx
            best_dates = dates
    if len(best_dates) < 2:
        return None, {}
    return best_row, best_dates


def parse_sheet(df: pd.DataFrame, filename: str, sheet_name: str) -> tuple[list[dict], str | None]:
    default_year, file_month = identify_period(filename, sheet_name)
    header_row, date_cols = find_date_header(df, default_year=default_year, file_month=file_month)
    if header_row is None:
        if file_month is None:
            return [], "未找到横向日期列，且无法从文件名或 sheet 名识别月份"
        return [], "未找到横向日期列"

    min_date_col = min(date_cols)
    brand = identify_brand(filename)
    platform = identify_platform(filename)
    rows = []

    for row_idx in range(header_row + 1, len(df)):
        row = df.iloc[row_idx]
        left_cells = [clean_text(v) for v in row.iloc[:min_date_col].tolist()]
        left_text = " ".join(cell for cell in left_cells if cell)
        if not left_text:
            continue

        metric_candidates = [cell for cell in left_cells if cell]
        metric = metric_candidates[-1] if metric_candidates else ""
        metric_std = map_metric(metric)
        channel = detect_channel(sheet_name, left_text)

        for col_idx, parsed_date in date_cols.items():
            raw_value = row.iloc[col_idx]
            value = clean_number(raw_value, metric_std)
            if value is None:
                continue
            rows.append(
                {
                    "date": parsed_date.date().isoformat(),
                    "brand": brand,
                    "platform": platform,
                    "channel": channel,
                    "metric": metric,
                    "metric_std": metric_std or "",
                    "value": value,
                    "source_file": filename,
                    "source_sheet": sheet_name,
                }
            )

    if not rows:
        return [], "找到日期列，但未识别到有效指标数据"
    return rows, None


def empty_dataframes() -> tuple[pd.DataFrame, pd.DataFrame]:
    raw_columns = ["date", "brand", "platform", "channel", "metric", "value", "source_file", "source_sheet"]
    return pd.DataFrame(columns=raw_columns), pd.DataFrame(columns=STANDARD_COLUMNS)


def export_normalized_data(raw_df: pd.DataFrame) -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    export_df = raw_df.drop(columns=["metric_std"], errors="ignore")
    export_df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")


def parse_excel_sources(sources: list[tuple[str, object]]) -> tuple[pd.DataFrame, pd.DataFrame, list[str], list[str]]:
    OUTPUT_DIR.mkdir(exist_ok=True)

    all_rows = []
    warnings = []
    missing: list[str] = []

    for filename, file_obj in sources:
        if hasattr(file_obj, "seek"):
            file_obj.seek(0)
        try:
            workbook = pd.read_excel(file_obj, sheet_name=None, header=None, engine="openpyxl")
        except Exception as exc:
            warnings.append(f"{filename} 读取失败：{exc}")
            continue

        for sheet_name, df in workbook.items():
            try:
                rows, reason = parse_sheet(df, filename, str(sheet_name))
                if rows:
                    all_rows.extend(rows)
                else:
                    warnings.append(f"{filename} / {sheet_name}：该 sheet 未识别（{reason}）")
            except Exception as exc:
                warnings.append(f"{filename} / {sheet_name}：该 sheet 未识别（{exc}）")

    raw_df = pd.DataFrame(
        all_rows,
        columns=["date", "brand", "platform", "channel", "metric", "metric_std", "value", "source_file", "source_sheet"],
    )

    if raw_df.empty:
        export_normalized_data(raw_df)
        return raw_df, pd.DataFrame(columns=STANDARD_COLUMNS), warnings, missing

    raw_df["date"] = pd.to_datetime(raw_df["date"], errors="coerce")
    raw_df = raw_df.dropna(subset=["date"])
    export_normalized_data(raw_df)

    op_df = build_operating_table(raw_df)
    return raw_df, op_df, warnings, missing


def load_uploaded_data(uploaded_files) -> tuple[pd.DataFrame, pd.DataFrame, list[str], list[str]]:
    if not uploaded_files:
        raw_df, op_df = empty_dataframes()
        export_normalized_data(raw_df)
        return raw_df, op_df, [], []
    sources = [(uploaded_file.name, uploaded_file) for uploaded_file in uploaded_files]
    return parse_excel_sources(sources)


def load_history_from_database() -> tuple[pd.DataFrame, str]:
    engine = get_engine()
    if engine is None:
        return pd.DataFrame(columns=STANDARD_COLUMNS), "未配置 DATABASE_URL，请管理员配置数据库连接。"
    try:
        init_db(engine)
        history_df = load_daily_metrics(engine=engine)
        if history_df.empty:
            return pd.DataFrame(columns=STANDARD_COLUMNS), "数据库已连接，但还没有历史经营数据。"
        for col in STANDARD_COLUMNS:
            if col not in history_df.columns:
                history_df[col] = pd.NA
        history_df["date"] = pd.to_datetime(history_df["date"], errors="coerce")
        return history_df, f"已从数据库读取 {len(history_df)} 条历史经营数据。"
    except Exception as exc:
        return pd.DataFrame(columns=STANDARD_COLUMNS), f"数据库读取失败：{exc}"


def build_operating_table(raw_df: pd.DataFrame) -> pd.DataFrame:
    mapped = raw_df.copy()
    mapped["metric_std"] = mapped["metric"].apply(map_metric)
    mapped = mapped.dropna(subset=["metric_std"])
    if mapped.empty:
        return pd.DataFrame(columns=STANDARD_COLUMNS)

    records = []
    group_cols = ["date", "brand", "platform", "channel"]
    for keys, group in mapped.groupby(group_cols, dropna=False):
        row = dict(zip(group_cols, keys))
        row["source_file"] = "；".join(sorted({str(v) for v in group["source_file"].dropna()}))
        row["source_sheet"] = "；".join(sorted({str(v) for v in group["source_sheet"].dropna()}))
        for metric in STANDARD_COLUMNS[4:]:
            metric_values = group.loc[group["metric_std"] == metric, "value"].dropna()
            if metric_values.empty:
                row[metric] = pd.NA
            elif metric in RATE_METRICS | ROI_METRICS:
                row[metric] = metric_values.mean()
            else:
                row[metric] = metric_values.sum()
        records.append(row)

    op_df = pd.DataFrame(records)
    for col in STANDARD_COLUMNS:
        if col not in op_df.columns:
            op_df[col] = pd.NA
    for col in ["source_file", "source_sheet"]:
        if col not in op_df.columns:
            op_df[col] = pd.NA
    op_df = op_df[STANDARD_COLUMNS + ["source_file", "source_sheet"]].sort_values(["date", "brand", "platform", "channel"])

    for col in STANDARD_COLUMNS[4:]:
        op_df[col] = pd.to_numeric(op_df[col], errors="coerce")

    can_calc_roi = op_df["gmv"].notna() & op_df["ad_spend"].notna() & (op_df["ad_spend"] != 0)
    op_df.loc[can_calc_roi, "roi"] = op_df.loc[can_calc_roi, "gmv"] / op_df.loc[can_calc_roi, "ad_spend"]
    can_calc_net_roi = op_df["net_gmv"].notna() & op_df["ad_spend"].notna() & (op_df["ad_spend"] != 0)
    op_df.loc[can_calc_net_roi, "net_roi"] = op_df.loc[can_calc_net_roi, "net_gmv"] / op_df.loc[can_calc_net_roi, "ad_spend"]
    return op_df


def inject_custom_css():
    st.markdown(
        """
        <style>
        :root {
            --page: #f6f7fb;
            --surface: rgba(255, 255, 255, 0.88);
            --surface-strong: #ffffff;
            --ink: #1a1d29;
            --muted: #6f778a;
            --weak: #9aa3b2;
            --line: rgba(123, 135, 158, 0.16);
            --blue: #5b7cfa;
            --purple: #8f7cff;
            --cyan: #54c6eb;
            --green: #12a66a;
            --red: #e0565b;
            --orange: #f59e0b;
        }
        html, body, [class*="css"] {
            font-family: Inter, "SF Pro Display", "PingFang SC", "Microsoft YaHei", sans-serif;
            color: var(--ink);
            letter-spacing: 0;
        }
        .stApp {
            background:
                linear-gradient(135deg, rgba(91, 124, 250, 0.08) 0%, transparent 28%),
                linear-gradient(225deg, rgba(84, 198, 235, 0.07) 0%, transparent 24%),
                var(--page);
        }
        [data-testid="stHeader"] { background: transparent; }
        .block-container {
            max-width: 1440px;
            padding-top: 1.7rem;
            padding-bottom: 4rem;
        }
        .dashboard-hero {
            padding: 18px 0 22px;
        }
        .hero-kicker {
            color: var(--blue);
            font-size: 12px;
            font-weight: 750;
            text-transform: uppercase;
            margin-bottom: 8px;
        }
        .main-title {
            color: var(--ink);
            font-size: clamp(34px, 4vw, 50px);
            line-height: 1.08;
            font-weight: 780;
            margin: 0 0 12px;
        }
        .subtle-note {
            color: var(--muted);
            font-size: 14px;
            line-height: 1.75;
            max-width: 820px;
        }
        .status-row { display: flex; flex-wrap: wrap; gap: 8px; margin: 20px 0 14px; }
        .status-chip, .date-chip {
            display: inline-flex;
            align-items: center;
            min-height: 30px;
            padding: 5px 10px;
            border-radius: 999px;
            background: rgba(255, 255, 255, 0.76);
            border: 1px solid var(--line);
            color: var(--muted);
            font-size: 12px;
            font-weight: 650;
        }
        .status-chip::before {
            content: "";
            width: 6px;
            height: 6px;
            border-radius: 50%;
            background: var(--green);
            margin-right: 7px;
            box-shadow: 0 0 0 3px rgba(18, 166, 106, 0.10);
        }
        .page-heading { margin: 26px 0 16px; }
        .page-eyebrow { color: var(--blue); font-size: 11px; font-weight: 750; margin-bottom: 6px; }
        .page-title { color: var(--ink); font-size: 25px; line-height: 1.25; font-weight: 760; }
        .page-description { color: var(--muted); font-size: 13px; margin-top: 6px; }
        .section-title {
            color: var(--ink);
            font-size: 18px;
            font-weight: 740;
            margin: 28px 0 12px;
        }
        .section-subtitle { color: var(--muted); font-size: 13px; margin: -6px 0 14px; }
        .metric-card {
            position: relative;
            overflow: hidden;
            background: var(--surface);
            border: 1px solid rgba(255, 255, 255, 0.88);
            border-radius: 8px;
            padding: 20px 20px 18px;
            box-shadow: 0 16px 38px rgba(31, 41, 55, 0.07);
            backdrop-filter: blur(18px);
            min-height: 132px;
            margin-bottom: 14px;
        }
        .metric-card::after {
            content: "";
            position: absolute;
            inset: 0 auto 0 0;
            width: 3px;
            background: linear-gradient(180deg, var(--blue), var(--cyan));
            opacity: 0.72;
        }
        .metric-card.metric-secondary { min-height: 118px; box-shadow: 0 10px 26px rgba(31, 41, 55, 0.055); }
        .metric-label { color: var(--muted); font-size: 12px; font-weight: 650; margin-bottom: 12px; }
        .metric-value { color: var(--ink); font-size: 31px; font-weight: 780; line-height: 1.12; }
        .metric-secondary .metric-value { font-size: 27px; }
        .metric-help { color: var(--weak); font-size: 11px; margin-top: 9px; }
        .delta-pos, .delta-neg, .delta-flat {
            display: inline-flex;
            margin-top: 10px;
            padding: 4px 8px;
            border-radius: 999px;
            font-size: 11px;
            font-weight: 750;
        }
        .delta-pos { color: #0c8b59; background: rgba(18, 166, 106, 0.10); }
        .delta-neg { color: #c8454c; background: rgba(224, 86, 91, 0.10); }
        .delta-flat { color: var(--muted); background: rgba(111, 119, 138, 0.09); }
        .info-box {
            background: linear-gradient(135deg, rgba(91, 124, 250, 0.07), rgba(84, 198, 235, 0.05));
            border: 1px solid rgba(91, 124, 250, 0.13);
            border-radius: 8px;
            padding: 18px 20px;
            margin-bottom: 16px;
            color: #46506a;
            font-size: 13px;
            line-height: 1.65;
        }
        .empty-title { color: var(--ink); font-size: 15px; font-weight: 730; margin-bottom: 4px; }
        .empty-description { color: var(--muted); font-size: 13px; }
        .business-card {
            position: relative;
            background: var(--surface);
            border: 1px solid rgba(255, 255, 255, 0.9);
            border-radius: 8px;
            padding: 22px;
            box-shadow: 0 16px 38px rgba(31, 41, 55, 0.065);
            backdrop-filter: blur(18px);
            min-height: 292px;
            margin-bottom: 16px;
        }
        .business-head { display: flex; justify-content: space-between; gap: 12px; align-items: center; }
        .business-name { color: var(--ink); font-size: 17px; font-weight: 750; }
        .business-gmv-label { color: var(--weak); font-size: 11px; margin-top: 22px; }
        .business-gmv { color: var(--ink); font-size: 34px; line-height: 1.15; font-weight: 780; margin-top: 5px; }
        .business-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 13px;
            margin-top: 20px;
            padding-top: 17px;
            border-top: 1px solid var(--line);
        }
        .business-stat-label { color: var(--weak); font-size: 10px; margin-bottom: 4px; }
        .business-stat-value { color: #34394a; font-size: 14px; font-weight: 720; word-break: break-word; }
        .business-foot { color: var(--muted); font-size: 11px; margin-top: 18px; }
        .state-tag { display: inline-flex; padding: 4px 9px; border-radius: 999px; font-size: 11px; font-weight: 750; }
        .state-normal { color: #0c8b59; background: rgba(18, 166, 106, 0.10); }
        .state-watch { color: #b56c00; background: rgba(245, 158, 11, 0.12); }
        .state-risk { color: #c8454c; background: rgba(224, 86, 91, 0.11); }
        .alert-card {
            position: relative;
            border-radius: 8px;
            padding: 18px 20px 18px 22px;
            margin-bottom: 14px;
            border: 1px solid var(--line);
            box-shadow: 0 10px 28px rgba(31, 41, 55, 0.045);
            overflow: hidden;
        }
        .alert-card::before { content: ""; position: absolute; inset: 0 auto 0 0; width: 4px; }
        .alert-high { background: rgba(255, 247, 247, 0.92); border-color: rgba(224, 86, 91, 0.18); }
        .alert-high::before { background: var(--red); }
        .alert-mid { background: rgba(255, 250, 241, 0.92); border-color: rgba(245, 158, 11, 0.19); }
        .alert-mid::before { background: var(--orange); }
        .alert-low { background: rgba(245, 249, 255, 0.94); border-color: rgba(91, 124, 250, 0.16); }
        .alert-low::before { background: var(--blue); }
        .alert-title { color: var(--ink); font-size: 15px; font-weight: 750; }
        .alert-line { color: #4f586d; font-size: 13px; line-height: 1.65; margin-top: 8px; }
        .alert-action { margin-top: 10px; padding: 10px 12px; border-radius: 6px; background: rgba(255, 255, 255, 0.72); }
        .alert-meta { margin-top: 11px; color: var(--weak); font-size: 11px; }
        .tag { display: inline-block; padding: 3px 9px; border-radius: 999px; font-size: 10px; font-weight: 750; margin-left: 8px; }
        .tag-high { background: rgba(224, 86, 91, 0.13); color: #c8454c; }
        .tag-mid { background: rgba(245, 158, 11, 0.14); color: #b56c00; }
        .tag-low { background: rgba(91, 124, 250, 0.12); color: #4867d8; }

        div[data-baseweb="tab-list"] {
            width: fit-content;
            max-width: 100%;
            gap: 5px;
            padding: 5px;
            border-radius: 8px;
            background: rgba(229, 233, 242, 0.72);
            border: 1px solid rgba(123, 135, 158, 0.10);
        }
        button[data-baseweb="tab"] {
            height: 42px;
            padding: 0 18px;
            border-radius: 6px;
            color: var(--muted);
            font-weight: 650;
        }
        button[data-baseweb="tab"]:hover { background: rgba(255, 255, 255, 0.58); color: var(--ink); }
        button[data-baseweb="tab"][aria-selected="true"] {
            color: var(--ink);
            background: #ffffff;
            box-shadow: 0 6px 16px rgba(31, 41, 55, 0.08);
        }
        button[data-baseweb="tab"] [data-testid="stMarkdownContainer"] p { font-size: 13px; }
        div[data-baseweb="tab-highlight"] { display: none; }

        .stButton > button {
            min-height: 44px;
            border: 0;
            border-radius: 8px;
            padding: 0 18px;
            color: #ffffff;
            font-weight: 700;
            background: linear-gradient(135deg, var(--blue), var(--purple));
            box-shadow: 0 9px 20px rgba(91, 124, 250, 0.22);
            transition: transform 160ms ease, box-shadow 160ms ease;
        }
        .stButton > button:hover {
            color: #ffffff;
            border: 0;
            transform: translateY(-1px);
            box-shadow: 0 12px 26px rgba(91, 124, 250, 0.28);
        }
        [data-testid="stAlert"] {
            border-radius: 8px;
            border: 1px solid rgba(245, 158, 11, 0.20);
            background: rgba(255, 249, 225, 0.94);
            color: #765700;
        }
        [data-testid="stAlert"] * { color: inherit !important; }
        div[data-baseweb="select"] > div,
        div[data-baseweb="input"] > div,
        [data-testid="stDateInput"] div[data-baseweb="input"] > div {
            min-height: 48px;
            border-radius: 8px;
            border-color: rgba(123, 135, 158, 0.22);
            background: rgba(255, 255, 255, 0.90) !important;
        }
        [data-testid="stWidgetLabel"] p { color: var(--muted) !important; font-size: 12px; font-weight: 650; }
        div[data-baseweb="select"] div,
        div[data-baseweb="select"] span,
        div[data-baseweb="select"] input,
        div[data-baseweb="input"] input,
        [data-testid="stDateInput"] input {
            color: var(--ink) !important;
            -webkit-text-fill-color: var(--ink) !important;
        }
        div[data-baseweb="select"] svg { fill: var(--muted); }
        div[data-testid="stRadio"] > div { gap: 8px; }
        div[data-testid="stRadio"] label {
            padding: 8px 10px;
            border-radius: 6px;
            background: rgba(255, 255, 255, 0.62);
        }
        div[data-testid="stRadio"] label p { color: #34394a !important; font-size: 12px; }
        div[data-testid="stVerticalBlockBorderWrapper"] {
            background: var(--surface);
            border: 1px solid rgba(255, 255, 255, 0.88);
            border-radius: 8px;
            box-shadow: 0 14px 34px rgba(31, 41, 55, 0.055);
            backdrop-filter: blur(18px);
        }
        div[data-testid="stPlotlyChart"] {
            background: rgba(255, 255, 255, 0.88);
            border: 1px solid rgba(255, 255, 255, 0.92);
            border-radius: 8px;
            padding: 8px 10px 2px;
            margin-bottom: 14px;
            box-shadow: 0 14px 34px rgba(31, 41, 55, 0.055);
        }
        .login-atmosphere {
            min-height: 530px;
            padding: 54px;
            border-radius: 8px;
            background: linear-gradient(145deg, #4265df 0%, #6578e9 48%, #54b5d7 100%);
            color: white;
            box-shadow: 0 24px 60px rgba(61, 84, 173, 0.18);
            display: flex;
            flex-direction: column;
            justify-content: space-between;
        }
        .login-mark { font-size: 12px; font-weight: 750; opacity: 0.78; }
        .login-title { max-width: 560px; font-size: 43px; line-height: 1.12; font-weight: 780; margin-top: 90px; }
        .login-en { font-size: 14px; opacity: 0.76; margin-top: 14px; }
        .login-copy { max-width: 520px; font-size: 14px; line-height: 1.8; opacity: 0.82; margin-top: 28px; }
        .login-points { display: flex; flex-wrap: wrap; gap: 9px; margin-top: 28px; }
        .login-point { padding: 7px 10px; border: 1px solid rgba(255,255,255,0.22); border-radius: 999px; background: rgba(255,255,255,0.10); font-size: 11px; }
        .login-form-title { color: var(--ink); font-size: 25px; font-weight: 760; margin-top: 28px; }
        .login-form-copy { color: var(--muted); font-size: 13px; margin: 7px 0 28px; }
        .login-foot { color: var(--weak); font-size: 11px; margin-top: 20px; }
        .st-key-login_card {
            min-height: 530px;
            padding: 34px 30px !important;
            background: rgba(255, 255, 255, 0.88);
            border: 1px solid rgba(255, 255, 255, 0.94) !important;
            border-radius: 8px;
            box-shadow: 0 22px 54px rgba(31, 41, 55, 0.09);
            backdrop-filter: blur(18px);
        }
        .st-key-view_controls,
        .st-key-trend_filters,
        .st-key-channel_filters,
        .st-key-alert_filters {
            padding: 18px 20px 14px !important;
            margin-bottom: 16px;
            background: rgba(255, 255, 255, 0.82);
            border: 1px solid rgba(255, 255, 255, 0.92) !important;
            border-radius: 8px;
            box-shadow: 0 14px 34px rgba(31, 41, 55, 0.055);
            backdrop-filter: blur(18px);
        }
        @media (max-width: 900px) {
            .block-container { padding-left: 1rem; padding-right: 1rem; }
            .main-title { font-size: 34px; }
            .login-atmosphere { min-height: 360px; padding: 32px; }
            .login-title { margin-top: 46px; font-size: 34px; }
            .business-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def safe_float(value) -> float | None:
    if value is None or pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_number(value, digits: int = 0) -> str:
    number = safe_float(value)
    if number is None:
        return "暂无"
    return f"{number:,.{digits}f}"


def format_percent(value) -> str:
    number = safe_float(value)
    if number is None:
        return "暂无"
    return f"{number * 100:.1f}%"


def format_roi(value) -> str:
    number = safe_float(value)
    if number is None:
        return "暂无"
    return f"{number:.2f}"


def format_delta(current, previous) -> tuple[str, str]:
    current_num = safe_float(current)
    previous_num = safe_float(previous)
    if current_num is None or previous_num is None or previous_num == 0:
        return "暂无对比", "flat"
    change = (current_num - previous_num) / abs(previous_num)
    if change > 0:
        return f"+{change * 100:.1f}%", "pos"
    if change < 0:
        return f"{change * 100:.1f}%", "neg"
    return "0.0%", "flat"


def ratio(numerator, denominator) -> float | None:
    n = safe_float(numerator)
    d = safe_float(denominator)
    if n is None or d is None or d == 0:
        return None
    return n / d


def aggregate_metrics(df: pd.DataFrame) -> dict:
    result = {metric: None for metric in STANDARD_COLUMNS[4:]}
    if df.empty:
        return result
    working = df.copy()
    for col in STANDARD_COLUMNS[4:]:
        if col in working.columns:
            working[col] = pd.to_numeric(working[col], errors="coerce")

    for metric in MONEY_OR_COUNT_METRICS:
        if metric in working.columns:
            value = working[metric].sum(min_count=1)
            result[metric] = None if pd.isna(value) else float(value)

    result["roi"] = ratio(result.get("gmv"), result.get("ad_spend"))
    result["net_roi"] = ratio(result.get("net_gmv"), result.get("ad_spend"))
    result["refund_rate"] = ratio(result.get("refund_amount"), result.get("gmv"))
    if result["refund_rate"] is None and "refund_rate" in working.columns:
        mean_refund = working["refund_rate"].dropna().mean()
        result["refund_rate"] = None if pd.isna(mean_refund) else float(mean_refund)
    return result


def aggregate_for_period(df: pd.DataFrame, period: str) -> pd.DataFrame:
    """Aggregate metrics by brand, platform, channel and display period."""
    if df is None or df.empty or "date" not in df.columns:
        return pd.DataFrame(columns=STANDARD_COLUMNS)

    working = df.copy()
    working["date"] = pd.to_datetime(working["date"], errors="coerce").dt.normalize()
    working = working.dropna(subset=["date"])
    if working.empty:
        return pd.DataFrame(columns=STANDARD_COLUMNS)

    if period == "每周":
        working["period_date"] = working["date"] - pd.to_timedelta(
            working["date"].dt.weekday, unit="D"
        )
    elif period == "每月":
        working["period_date"] = working["date"].dt.to_period("M").dt.to_timestamp()
    else:
        working["period_date"] = working["date"]

    group_columns = ["period_date"]
    for column in ["brand", "platform", "channel"]:
        if column not in working.columns:
            working[column] = ""
        group_columns.append(column)

    rows = []
    for keys, group in working.groupby(group_columns, dropna=False, sort=True):
        period_date, brand, platform, channel = keys
        metrics = aggregate_metrics(group)
        rows.append(
            {
                "date": pd.Timestamp(period_date),
                "brand": brand,
                "platform": platform,
                "channel": channel,
                **metrics,
            }
        )

    if not rows:
        return pd.DataFrame(columns=STANDARD_COLUMNS)
    return pd.DataFrame(rows).sort_values(
        ["date", "brand", "platform", "channel"], ignore_index=True
    )


def prepare_trend_chart_df(df: pd.DataFrame, period: str, view_mode: str) -> pd.DataFrame:
    """Prepare trend series without averaging ratio metrics."""
    if df is None or df.empty or "date" not in df.columns:
        return pd.DataFrame(columns=STANDARD_COLUMNS + ["分组"])

    working = df.copy()
    working["date"] = pd.to_datetime(working["date"], errors="coerce").dt.normalize()
    working = working.dropna(subset=["date"])
    if working.empty:
        return pd.DataFrame(columns=STANDARD_COLUMNS + ["分组"])

    if period == "每周":
        working["period_date"] = working["date"] - pd.to_timedelta(
            working["date"].dt.weekday, unit="D"
        )
    elif period == "每月":
        working["period_date"] = working["date"].dt.to_period("M").dt.to_timestamp()
    else:
        working["period_date"] = working["date"]

    for column in ["brand", "platform", "channel"]:
        if column not in working.columns:
            working[column] = ""

    detail_view = view_mode == "按渠道明细"
    if detail_view:
        working["display_platform"] = working["platform"].replace({"千川": "抖店"})
        working["display_channel"] = working.apply(
            lambda row: get_qianchuan_channel_label(row["channel"])
            if row["platform"] == "千川"
            else row["channel"],
            axis=1,
        )
        group_columns = ["period_date", "brand", "display_platform", "display_channel"]
    else:
        # 千川是抖店投放补充数据，默认品牌平台趋势不重复汇总其成交。
        working = working[working["platform"] != "千川"]
        group_columns = ["period_date", "brand", "platform"]

    rows = []
    for keys, group in working.groupby(group_columns, dropna=False, sort=True):
        if detail_view:
            period_date, brand, platform, channel = keys
            label = f"{brand}-{platform}-{channel}"
        else:
            period_date, brand, platform = keys
            channel = "全部渠道"
            label = f"{brand}-{platform}"
        metrics = aggregate_metrics(group)
        rows.append(
            {
                "date": pd.Timestamp(period_date),
                "brand": brand,
                "platform": platform,
                "channel": channel,
                "分组": label,
                **metrics,
            }
        )

    if not rows:
        return pd.DataFrame(columns=STANDARD_COLUMNS + ["分组"])
    return pd.DataFrame(rows).sort_values(
        ["date", "brand", "platform", "channel"], ignore_index=True
    )


def get_available_dates(df: pd.DataFrame) -> list[pd.Timestamp]:
    if df.empty or "date" not in df.columns:
        return []
    dates = pd.to_datetime(df["date"], errors="coerce").dropna().dt.normalize().drop_duplicates().sort_values()
    return list(dates)


def get_previous_available_date(df: pd.DataFrame, selected_date) -> pd.Timestamp | None:
    dates = [d for d in get_available_dates(df) if d < pd.Timestamp(selected_date).normalize()]
    return dates[-1] if dates else None


def filter_by_selected_date(df: pd.DataFrame, selected_date, mode: str) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    selected = pd.Timestamp(selected_date).normalize()
    working = df.copy()
    working["date"] = pd.to_datetime(working["date"], errors="coerce").dt.normalize()
    if mode == "本月累计":
        month_start = selected.replace(day=1)
        return working[(working["date"] >= month_start) & (working["date"] <= selected)]
    return working[working["date"] == selected]


def filter_df(
    df: pd.DataFrame,
    brand: str = "全部",
    platform: str = "全部",
    channel: str = "全部",
    date_range=None,
) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    working = df.copy()
    if "date" in working.columns:
        working["date"] = pd.to_datetime(working["date"], errors="coerce").dt.normalize()
        working = working.dropna(subset=["date"])

    if date_range is not None and "date" in working.columns:
        if isinstance(date_range, (tuple, list)) and len(date_range) == 2:
            start_date = pd.Timestamp(date_range[0]).normalize()
            end_date = pd.Timestamp(date_range[1]).normalize()
        else:
            start_date = pd.Timestamp(date_range).normalize()
            end_date = start_date
        working = working[(working["date"] >= start_date) & (working["date"] <= end_date)]

    if brand != "全部" and "brand" in working.columns:
        working = working[working["brand"] == brand]
    if platform != "全部" and "platform" in working.columns:
        working = working[working["platform"] == platform]
    if channel != "全部" and "channel" in working.columns:
        working = working[working["channel"] == channel]

    return working


def render_metric_card(
    title: str,
    value: str,
    delta: str | None = None,
    delta_state: str = "flat",
    help_text: str | None = None,
    variant: str = "primary",
):
    delta_html = ""
    if delta is not None:
        delta_html = f'<div class="delta-{delta_state}">{delta}</div>'
    help_html = f'<div class="metric-help">{help_text}</div>' if help_text else ""
    card_class = "metric-card metric-secondary" if variant == "secondary" else "metric-card"
    st.markdown(
        f'<div class="{card_class}"><div class="metric-label">{escape(str(title))}</div>'
        f'<div class="metric-value">{escape(str(value))}</div>{delta_html}{help_html}</div>',
        unsafe_allow_html=True,
    )


def render_info_box(text: str, title: str | None = None):
    title_html = f'<div class="empty-title">{escape(title)}</div>' if title else ""
    st.markdown(
        f'<div class="info-box">{title_html}<div class="empty-description">{escape(text)}</div></div>',
        unsafe_allow_html=True,
    )


def render_page_heading(title: str, eyebrow: str, description: str):
    st.markdown(
        f"""
        <div class="page-heading">
            <div class="page-eyebrow">{escape(eyebrow)}</div>
            <div class="page-title">{escape(title)}</div>
            <div class="page-description">{escape(description)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def get_app_password() -> str:
    secret_password = ""
    try:
        secret_password = st.secrets.get("APP_PASSWORD", "")
    except Exception:
        secret_password = ""
    return secret_password or os.getenv("APP_PASSWORD", "")


def require_password() -> bool:
    password = get_app_password()
    if not password:
        st.warning("安全提醒：当前未设置 APP_PASSWORD，页面默认允许访问。部署前请在 Streamlit Secrets 中设置访问密码。")
        return True

    if st.session_state.get("authenticated"):
        return True

    left, right = st.columns([1.2, 0.8], gap="large", vertical_alignment="center")
    with left:
        st.markdown(
            """
            <div class="login-atmosphere">
                <div>
                    <div class="login-mark">COMMERCE OPERATING INTELLIGENCE</div>
                    <div class="login-title">电商经营复盘看板</div>
                    <div class="login-en">Ecommerce Intelligence Dashboard</div>
                    <div class="login-copy">为日常经营复盘、趋势判断与渠道效率分析提供清晰视角。</div>
                    <div class="login-points">
                        <span class="login-point">双品牌经营总览</span>
                        <span class="login-point">日 / 月趋势追踪</span>
                        <span class="login-point">渠道效率复盘</span>
                    </div>
                </div>
                <div class="login-mark">INTERNAL BUSINESS VIEW</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with right:
        with st.container(border=True, key="login_card"):
            st.markdown('<div class="login-form-title">欢迎进入 BOSS 看板</div>', unsafe_allow_html=True)
            st.markdown('<div class="login-form-copy">请输入访问密码</div>', unsafe_allow_html=True)
            entered = st.text_input("访问密码", type="password", label_visibility="collapsed", key="login_password")
            if st.button("进入看板", key="login_submit", use_container_width=True):
                if entered == password:
                    st.session_state["authenticated"] = True
                    st.rerun()
                else:
                    st.error("密码不正确，请重新输入。")
            st.markdown('<div class="login-foot">内部经营数据，仅限授权访问</div>', unsafe_allow_html=True)
    return False


def render_dashboard_header():
    left, right = st.columns([4.7, 1.3], gap="large", vertical_alignment="center")
    with left:
        st.markdown(
            """
            <div class="dashboard-hero">
                <div class="hero-kicker">Commerce Operating Intelligence</div>
                <div class="main-title">电商经营复盘看板</div>
                <div class="subtle-note">
                    最护和碧维是不同品类品牌，本看板展示各自经营状态，不做品牌输赢对比。<br>
                    千川数据只作为投放补充分析，不默认计入总 GMV，避免和抖店重复。
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with right:
        st.markdown(
            """
            <div class="status-row">
                <span class="status-chip">数据源：Supabase</span>
                <span class="status-chip">访问：BOSS View</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("刷新数据", key="refresh_data", use_container_width=True):
            st.rerun()


def render_chart(df: pd.DataFrame, y_col: str, title: str, formatter: str | None = None):
    chart_df = df.dropna(subset=[y_col]).copy() if y_col in df.columns else pd.DataFrame()
    if chart_df.empty:
        render_info_box("可尝试切换日期、品牌或平台。", title="当前筛选条件下暂无数据")
        return
    if "分组" not in chart_df.columns:
        chart_df["分组"] = chart_df.apply(
            lambda row: get_series_label(row.get("brand", ""), row.get("platform", ""), row.get("channel", "")),
            axis=1,
        )
    series_labels = [str(label) for label in chart_df["分组"].dropna().unique()]
    color_discrete_map = {label: get_series_color(label) for label in series_labels}
    many_series = len(series_labels) > 8
    fig = px.line(
        chart_df,
        x="date",
        y=y_col,
        color="分组",
        markers=True,
        title=title,
        color_discrete_map=color_discrete_map,
    )
    fig.update_layout(
        height=460 if many_series else 430,
        margin=dict(l=18, r=18, t=72, b=150 if many_series else 112),
        legend_title_text="",
        hovermode="x unified",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#7B8497", size=12),
        title=dict(font=dict(color="#1A1D29", size=17), x=0.02),
        legend=dict(
            orientation="h",
            yanchor="top",
            y=-0.22,
            xanchor="left",
            x=0,
            font=dict(color="#5F687A", size=11),
            traceorder="normal",
        ),
        hoverlabel=dict(bgcolor="#FFFFFF", bordercolor="#E9ECF3", font=dict(color="#1A1D29")),
    )
    fig.update_xaxes(showgrid=False, zeroline=False, title_text="", tickfont=dict(color="#7B8497"))
    fig.update_yaxes(gridcolor="#E9ECF3", zeroline=False, title_text="", tickfont=dict(color="#7B8497"))
    fig.update_traces(line=dict(width=3), marker=dict(size=5, line=dict(width=1, color="#ffffff")))
    if formatter == "percent":
        fig.update_yaxes(tickformat=".1%")
    st.plotly_chart(fig, use_container_width=True, config={"displaylogo": False, "modeBarButtonsToRemove": ["lasso2d", "select2d"]})


def render_bar(df: pd.DataFrame, y_col: str, title: str, formatter: str | None = None):
    chart_df = df.dropna(subset=[y_col]).copy() if y_col in df.columns else pd.DataFrame()
    if chart_df.empty:
        render_info_box("可尝试切换日期、品牌或平台。", title="当前筛选条件下暂无数据")
        return
    fig = px.bar(
        chart_df,
        x="channel",
        y=y_col,
        color="brand",
        barmode="group",
        title=title,
        color_discrete_map={"最护": "#2563EB", "碧维": "#F97316", "千川": "#9333EA"},
    )
    fig.update_layout(
        height=360,
        margin=dict(l=18, r=18, t=68, b=22),
        legend_title_text="",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#7B8497", size=12),
        title=dict(font=dict(color="#1A1D29", size=17), x=0.02),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    fig.update_xaxes(showgrid=False, zeroline=False, title_text="", tickfont=dict(color="#7B8497"))
    fig.update_yaxes(gridcolor="#E9ECF3", zeroline=False, title_text="", tickfont=dict(color="#7B8497"))
    fig.update_traces(marker_line_width=0, marker_cornerradius=5)
    if formatter == "percent":
        fig.update_yaxes(tickformat=".1%")
    st.plotly_chart(fig, use_container_width=True, config={"displaylogo": False, "modeBarButtonsToRemove": ["lasso2d", "select2d"]})


def render_date_controls(op_df: pd.DataFrame) -> tuple[pd.Timestamp | None, str, pd.Timestamp | None]:
    dates = get_available_dates(op_df)
    if not dates:
        return None, "本日数据", None
    latest = dates[-1]
    years = sorted({d.year for d in dates})
    default_year_index = years.index(latest.year)

    with st.container(border=True, key="view_controls"):
        st.markdown('<div class="section-title" style="margin:2px 0 12px;">数据视角</div>', unsafe_allow_html=True)
        cols = st.columns([0.8, 0.8, 1.15, 1.45, 1.15, 1.15], gap="medium", vertical_alignment="top")
        year = cols[0].selectbox("年份", years, index=default_year_index)
        months = sorted({d.month for d in dates if d.year == year})
        default_month = latest.month if latest.year == year and latest.month in months else months[-1]
        month = cols[1].selectbox("月份", months, index=months.index(default_month), format_func=lambda m: f"{m}月")
        day_dates = [d for d in dates if d.year == year and d.month == month]
        default_day = latest if latest in day_dates else day_dates[-1]
        selected_date = cols[2].selectbox("日期", day_dates, index=day_dates.index(default_day), format_func=lambda d: pd.Timestamp(d).strftime("%Y-%m-%d"))
        mode = cols[3].radio("查看口径", ["本日数据", "本月累计"], horizontal=True)
        previous_date = get_previous_available_date(op_df, selected_date)
        compare_text = "本月累计暂无对比" if mode == "本月累计" else (str(pd.Timestamp(previous_date).date()) if previous_date is not None else "暂无对比")
        cols[4].markdown(
            f'<div style="height:28px;"></div><div class="date-chip" style="background:rgba(91,124,250,.10);color:#4867d8;">当前 · {pd.Timestamp(selected_date).date()}</div>',
            unsafe_allow_html=True,
        )
        cols[5].markdown(f'<div style="height:28px;"></div><div class="date-chip">对比 · {escape(compare_text)}</div>', unsafe_allow_html=True)
    return pd.Timestamp(selected_date), mode, previous_date


def main_scope(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["platform"].isin(["抖店", "拼多多"])].copy() if not df.empty else df.copy()


def generate_alerts(op_df: pd.DataFrame) -> pd.DataFrame:
    columns = ["标题", "严重程度", "数据依据", "可能原因", "建议动作", "brand", "platform", "channel", "date"]
    if op_df is None or op_df.empty:
        return pd.DataFrame(columns=columns)

    required = ["date", "brand", "platform", "channel"]
    for col in required:
        if col not in op_df.columns:
            return pd.DataFrame(columns=columns)

    df = op_df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    if df.empty:
        return pd.DataFrame(columns=columns)

    for col in ["gmv", "ad_spend", "roi", "refund_rate"]:
        if col not in df.columns:
            df[col] = pd.NA
        df[col] = pd.to_numeric(df[col], errors="coerce")

    alerts = []

    def add_alert(row, title, severity, evidence, reason, action):
        alerts.append(
            {
                "标题": title,
                "严重程度": severity,
                "数据依据": evidence,
                "可能原因": reason,
                "建议动作": action,
                "brand": str(row.get("brand", "")),
                "platform": str(row.get("platform", "")),
                "channel": str(row.get("channel", "")),
                "date": pd.Timestamp(row.get("date")).strftime("%Y-%m-%d"),
            }
        )

    for _, row in df.iterrows():
        gmv = safe_float(row.get("gmv"))
        ad_spend = safe_float(row.get("ad_spend"))
        roi = safe_float(row.get("roi"))
        refund_rate = safe_float(row.get("refund_rate"))

        if ad_spend is not None and ad_spend > 0 and (gmv is None or gmv == 0):
            add_alert(
                row,
                "有消耗但无成交",
                "高",
                f"投放消耗 {format_number(ad_spend)}，GMV 为 0 或为空",
                "投放承接、素材、价格或商品页面存在问题",
                "检查投放计划、素材、落地页和商品转化，必要时暂停低效消耗",
            )

        if refund_rate is not None and refund_rate > 0.10:
            add_alert(
                row,
                "退款率偏高",
                "高",
                f"退款率 {format_percent(refund_rate)}，超过 10%",
                "商品预期差、质量、客服或售后承接问题",
                "检查退款原因、评价反馈、商品详情页表达和客服承接",
            )

        if roi is not None and roi < 1:
            add_alert(
                row,
                "ROI 偏低",
                "中",
                f"ROI {format_roi(roi)}，低于 1",
                "投放成本偏高或转化不足",
                "检查出价、素材、人群和商品承接，降低低效消耗",
            )

        if roi is not None and roi >= 5 and (gmv is None or gmv < 3000):
            add_alert(
                row,
                "高 ROI 低规模",
                "低",
                f"ROI {format_roi(roi)}，GMV {format_number(gmv)}",
                "当前渠道效率较高但流量规模偏小",
                "可以小幅测试放量，同时观察退款率和转化稳定性",
            )

    grouped = df.sort_values(["brand", "platform", "channel", "date"]).groupby(["brand", "platform", "channel"], dropna=False)
    for _, group in grouped:
        if len(group) < 2:
            continue
        group = group.sort_values("date")
        previous = None
        for _, row in group.iterrows():
            if previous is not None:
                current_gmv = safe_float(row.get("gmv"))
                previous_gmv = safe_float(previous.get("gmv"))
                current_roi = safe_float(row.get("roi"))
                previous_roi = safe_float(previous.get("roi"))

                if current_gmv is not None and previous_gmv is not None and previous_gmv > 0:
                    change = (current_gmv - previous_gmv) / abs(previous_gmv)
                    if change < -0.10:
                        add_alert(
                            row,
                            "GMV 较上一有数据日下滑",
                            "中",
                            f"GMV 较上一有数据日下降 {abs(change) * 100:.1f}%",
                            "流量、转化、活动节奏或商品承接可能出现波动",
                            "检查流量来源、价格活动、直播节奏和商品转化承接",
                        )

                if current_roi is not None and previous_roi is not None and previous_roi > 0:
                    change = (current_roi - previous_roi) / abs(previous_roi)
                    if change < -0.10:
                        add_alert(
                            row,
                            "ROI 较上一有数据日下滑",
                            "中",
                            f"ROI 较上一有数据日下降 {abs(change) * 100:.1f}%",
                            "投放成本上升或成交转化下降",
                            "检查出价、素材、人群、成交转化和低效计划",
                        )
            previous = row

    if not alerts:
        return pd.DataFrame(columns=columns)

    result = pd.DataFrame(alerts, columns=columns)
    severity_order = {"高": 0, "中": 1, "低": 2}
    result["排序"] = result["严重程度"].map(severity_order).fillna(9)
    result = result.sort_values(["排序", "date"], ascending=[True, False]).drop(columns=["排序"])
    return result.reset_index(drop=True)


def render_business_card(brand: str, platform: str, df: pd.DataFrame, previous_df: pd.DataFrame | None, mode: str):
    card_df = df[(df["brand"] == brand) & (df["platform"] == platform)] if not df.empty else pd.DataFrame()
    if card_df.empty:
        st.markdown(
            f"""
            <div class="business-card">
                <div class="business-head"><div class="business-name">{escape(brand)} · {escape(platform)}</div><span class="state-tag state-watch">暂无数据</span></div>
                <div class="info-box" style="margin-top:26px;">所选日期暂无经营数据。</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    current = aggregate_metrics(card_df)
    previous = aggregate_metrics(previous_df[(previous_df["brand"] == brand) & (previous_df["platform"] == platform)]) if previous_df is not None and not previous_df.empty else {}
    gmv_delta, gmv_state = ("本月累计暂无对比", "flat") if mode == "本月累计" else format_delta(current.get("gmv"), previous.get("gmv"))
    roi_delta, _ = ("本月累计暂无对比", "flat") if mode == "本月累计" else format_delta(current.get("roi"), previous.get("roi"))

    current_gmv = safe_float(current.get("gmv"))
    previous_gmv = safe_float(previous.get("gmv"))
    gmv_change = None if current_gmv is None or previous_gmv in (None, 0) else (current_gmv - previous_gmv) / abs(previous_gmv)
    roi = safe_float(current.get("roi"))
    refund_rate = safe_float(current.get("refund_rate"))
    if (roi is not None and roi < 1) or (refund_rate is not None and refund_rate > 0.10):
        status, status_class, status_note = "风险", "state-risk", "效率或售后指标需要优先检查。"
    elif gmv_change is not None and gmv_change < -0.10:
        status, status_class, status_note = "关注", "state-watch", "成交出现波动，建议关注流量与转化承接。"
    else:
        status, status_class, status_note = "正常", "state-normal", "当前经营状态平稳，继续观察趋势变化。"

    stats = [
        ("单量", format_number(current.get("orders"))),
        ("投放消耗", format_number(current.get("ad_spend"))),
        ("ROI", format_roi(current.get("roi"))),
        ("净 ROI", format_roi(current.get("net_roi"))),
        ("退款率", format_percent(current.get("refund_rate"))),
        ("GMV 变化", gmv_delta),
        ("ROI 变化", roi_delta),
    ]
    stats_html = "".join(
        f'<div><div class="business-stat-label">{escape(label)}</div><div class="business-stat-value">{escape(value)}</div></div>'
        for label, value in stats
    )
    st.markdown(
        f"""
        <div class="business-card">
            <div class="business-head">
                <div class="business-name">{escape(brand)} · {escape(platform)}</div>
                <span class="state-tag {status_class}">{status}</span>
            </div>
            <div class="business-gmv-label">GMV</div>
            <div class="business-gmv">{escape(format_number(current.get("gmv")))}</div>
            <div class="business-grid">{stats_html}</div>
            <div class="business-foot">{escape(status_note)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_boss_home(op_df: pd.DataFrame):
    render_page_heading("BOSS首页", "OPERATING OVERVIEW", "聚焦核心经营结果、效率变化与当日风险。")
    if op_df.empty:
        render_info_box("暂无历史数据，请管理员在后台导入数据。")
        return

    selected_date, mode, previous_date = render_date_controls(op_df)
    if selected_date is None:
        render_info_box("暂无历史数据，请管理员在后台导入数据。")
        return

    current_df = filter_by_selected_date(op_df, selected_date, mode)
    if current_df.empty:
        render_info_box("请切换到其他有数据的日期。", title="当前日期暂无数据")
        return
    previous_df = filter_by_selected_date(op_df, previous_date, "本日数据") if previous_date is not None and mode == "本日数据" else pd.DataFrame()

    current_main = main_scope(current_df)
    previous_main = main_scope(previous_df)
    current = aggregate_metrics(current_main)
    previous = aggregate_metrics(previous_main)
    gmv_delta, gmv_state = ("本月累计暂无对比", "flat") if mode == "本月累计" else format_delta(current.get("gmv"), previous.get("gmv"))
    roi_delta, roi_state = ("本月累计暂无对比", "flat") if mode == "本月累计" else format_delta(current.get("roi"), previous.get("roi"))

    st.markdown('<div class="section-title">核心指标</div>', unsafe_allow_html=True)
    cols = st.columns(4)
    with cols[0]:
        render_metric_card("总 GMV", format_number(current.get("gmv")), help_text="不含千川，避免重复计算")
    with cols[1]:
        render_metric_card("总单量", format_number(current.get("orders")))
    with cols[2]:
        render_metric_card("总投放消耗", format_number(current.get("ad_spend")))
    with cols[3]:
        render_metric_card("整体 ROI", format_roi(current.get("roi")))
    cols = st.columns(4)
    with cols[0]:
        render_metric_card("净 ROI", format_roi(current.get("net_roi")), variant="secondary")
    with cols[1]:
        render_metric_card("退款率", format_percent(current.get("refund_rate")), variant="secondary")
    with cols[2]:
        render_metric_card("较上一有数据日 GMV 变化", gmv_delta, delta_state=gmv_state, variant="secondary")
    with cols[3]:
        render_metric_card("较上一有数据日 ROI 变化", roi_delta, delta_state=roi_state, variant="secondary")

    st.markdown('<div class="section-title">四个经营卡片</div>', unsafe_allow_html=True)
    rows = [[("最护", "抖店"), ("最护", "拼多多")], [("碧维", "抖店"), ("碧维", "拼多多")]]
    for row in rows:
        cols = st.columns(2)
        for col, (brand, platform) in zip(cols, row):
            with col:
                render_business_card(brand, platform, current_main, previous_main, mode)

    st.markdown('<div class="section-title">当前日期核心提醒</div>', unsafe_allow_html=True)
    alert_df = generate_alerts(current_df if mode == "本日数据" else current_df[current_df["date"] == selected_date])
    if alert_df.empty:
        render_info_box("经营状态整体平稳，可继续观察趋势变化。", title="当前日期暂无明显异常")
    else:
        render_alert_cards(alert_df.head(3), compact=True)


def render_trends(op_df: pd.DataFrame):
    render_page_heading("历史趋势", "TREND WORKSPACE", "按日期、品牌、平台与渠道观察经营变化。")
    if op_df.empty:
        render_info_box("暂无历史数据，请管理员在后台导入数据。")
        return
    min_date = op_df["date"].min().date()
    max_date = op_df["date"].max().date()
    with st.container(border=True, key="trend_filters"):
        st.markdown('<div class="section-title" style="margin:2px 0 12px;">分析筛选</div>', unsafe_allow_html=True)
        cols = st.columns([1.55, 0.9, 0.9, 1.15, 0.9], gap="medium")
        date_range = cols[0].date_input("日期范围", value=(min_date, max_date), min_value=min_date, max_value=max_date)
        brand = cols[1].selectbox("品牌", ["全部", "最护", "碧维"])
        platform = cols[2].selectbox("平台", ["全部", "抖店", "拼多多"])
        view_mode = cols[3].selectbox("展示维度", ["按品牌平台", "按渠道明细"])
        period = cols[4].selectbox("趋势粒度", ["每日", "每周", "每月"])
        channel = "全部"
        if view_mode == "按渠道明细":
            channel_col, _ = st.columns([1.15, 3.85], gap="medium")
            channel = channel_col.selectbox("渠道", ["全部", "整体", "商品卡", "直播", "短视频", "千川"], key="trend_channel")

    filtered = filter_df(op_df, brand, "全部", "全部", date_range)
    if platform == "抖店":
        filtered = filtered[filtered["platform"].isin(["抖店", "千川"])]
    elif platform == "拼多多":
        filtered = filtered[filtered["platform"] == "拼多多"]
    if view_mode == "按渠道明细" and channel != "全部":
        if channel == "千川":
            filtered = filtered[filtered["platform"] == "千川"]
        else:
            filtered = filtered[filtered["channel"] == channel]
    if filtered.empty:
        render_info_box("可尝试切换日期、品牌或平台。", title="当前筛选条件下暂无数据")
        return
    trend_df = prepare_trend_chart_df(filtered, period, view_mode)
    series_count = trend_df["分组"].nunique() if "分组" in trend_df.columns else 0
    if series_count > 8:
        render_info_box(
            "当前线条较多，建议选择具体品牌、平台或渠道查看。",
            title=f"当前展示 {series_count} 条趋势线",
        )
    render_chart(trend_df, "gmv", f"GMV {period}趋势")
    render_chart(trend_df, "orders", f"单量 {period}走势")
    render_chart(trend_df, "ad_spend", f"投放消耗 {period}变化")
    render_chart(trend_df, "roi", f"ROI {period}效率走势")
    render_chart(trend_df, "refund_rate", f"退款率 {period}走势", formatter="percent")


def render_channel_analysis(op_df: pd.DataFrame):
    render_page_heading("渠道分析", "CHANNEL STRATEGY", "拆解抖店渠道表现，并独立观察千川投放效率。")
    if op_df.empty:
        render_info_box("暂无历史数据，请管理员在后台导入数据。")
        return
    min_date = op_df["date"].min().date()
    max_date = op_df["date"].max().date()
    with st.container(border=True, key="channel_filters"):
        st.markdown('<div class="section-title" style="margin:2px 0 4px;">抖店渠道经营视角</div>', unsafe_allow_html=True)
        st.markdown('<div class="section-subtitle">对比整体、商品卡、直播与短视频的规模和效率。</div>', unsafe_allow_html=True)
        date_range = st.date_input("日期范围", value=(min_date, max_date), min_value=min_date, max_value=max_date, key="channel_date_range")
    filtered = filter_df(op_df, "全部", "全部", "全部", date_range)
    if filtered.empty:
        render_info_box("可尝试切换日期范围。", title="当前筛选条件下暂无数据")
        return

    douyin_channels = ["整体", "商品卡", "直播", "短视频", "店铺号商品卡", "洗脸巾直播"]
    douyin_df = filtered[(filtered["platform"] == "抖店") & (filtered["channel"].isin(douyin_channels))].copy()
    if douyin_df.empty:
        render_info_box("暂无抖店渠道数据")
    else:
        channel_df = aggregate_for_period(douyin_df, "每日")
        channel_summary = []
        for keys, group in channel_df.groupby(["brand", "channel"], dropna=False):
            row = aggregate_metrics(group)
            row.update({"brand": keys[0], "channel": keys[1]})
            channel_summary.append(row)
        summary_df = pd.DataFrame(channel_summary)
        render_bar(summary_df, "gmv", "各渠道 GMV")
        render_bar(summary_df, "orders", "各渠道单量")
        render_bar(summary_df, "ad_spend", "各渠道投放消耗")
        render_bar(summary_df, "roi", "各渠道 ROI")
        render_bar(summary_df, "refund_rate", "各渠道退款率", formatter="percent")

    st.markdown('<div class="section-title">千川投放补充分析</div>', unsafe_allow_html=True)
    render_info_box("千川属于抖店投放体系，仅作补充分析，不与抖店 GMV 合并计算。", title="抖店投放补充")
    qianchuan_df = filtered[filtered["platform"] == "千川"].copy()
    if qianchuan_df.empty:
        render_info_box("暂无千川数据")
    else:
        render_chart(qianchuan_df, "gmv", "千川成交趋势")
        render_chart(qianchuan_df, "net_gmv", "千川净成交趋势")
        render_chart(qianchuan_df, "ad_spend", "千川消耗趋势")
        render_chart(qianchuan_df, "roi", "千川 ROI 趋势")
        render_chart(qianchuan_df, "net_roi", "千川净 ROI 趋势")


def severity_class(severity: str) -> tuple[str, str]:
    if severity == "高":
        return "alert-high", "tag-high"
    if severity == "中":
        return "alert-mid", "tag-mid"
    return "alert-low", "tag-low"


def render_alert_cards(alerts: pd.DataFrame, compact: bool = False):
    if alerts.empty:
        render_info_box("当前筛选范围内暂无明显异常。")
        return
    for _, row in alerts.iterrows():
        box_class, tag_class = severity_class(str(row.get("严重程度", "低")))
        reason = escape(str(row.get("可能原因", "")))
        action = escape(str(row.get("建议动作", "")))
        details = "" if compact else f'<div class="alert-line"><b>可能原因</b> · {reason}</div>'
        st.markdown(
            f'<div class="alert-card {box_class}">'
            f'<div><span class="alert-title">{escape(str(row.get("标题", "")))}</span>'
            f'<span class="tag {tag_class}">{escape(str(row.get("严重程度", "")))}</span></div>'
            f'<div class="alert-line"><b>数据依据</b> · {escape(str(row.get("数据依据", "")))}</div>'
            f'{details}<div class="alert-line alert-action"><b>建议动作</b> · {action}</div>'
            f'<div class="alert-meta">{escape(str(row.get("brand", "")))} / {escape(str(row.get("platform", "")))} / '
            f'{escape(str(row.get("channel", "")))} / {escape(str(row.get("date", "")))}</div></div>',
            unsafe_allow_html=True,
        )


def render_alerts(op_df: pd.DataFrame):
    render_page_heading("自动复盘提醒", "OPERATING ADVISOR", "基于经营指标变化识别风险与可放大的机会。")
    if op_df.empty:
        render_info_box("暂无历史数据，请管理员在后台导入数据。")
        return
    min_date = op_df["date"].min().date()
    max_date = op_df["date"].max().date()
    with st.container(border=True, key="alert_filters"):
        st.markdown('<div class="section-title" style="margin:2px 0 10px;">提醒范围</div>', unsafe_allow_html=True)
        date_range = st.date_input("日期范围", value=(min_date, max_date), min_value=min_date, max_value=max_date, key="alert_date_range")
    filtered = filter_df(op_df, "全部", "全部", "全部", date_range)
    alerts = generate_alerts(filtered)
    if alerts.empty:
        render_info_box("可以继续观察 GMV、ROI 与退款率的变化趋势。", title="当前范围暂无明显异常")
        return
    severity_order = {"高": 0, "中": 1, "低": 2}
    alerts["排序"] = alerts["严重程度"].map(severity_order).fillna(9)
    alerts = alerts.sort_values(["排序", "date"], ascending=[True, False]).drop(columns=["排序"])
    render_alert_cards(alerts)


def main():
    st.set_page_config(page_title="电商经营复盘看板", layout="wide")
    inject_custom_css()

    if not require_password():
        return

    render_dashboard_header()

    display_df, history_message = load_history_from_database()

    tab_home, tab_trend, tab_channel, tab_alert = st.tabs(["BOSS首页", "历史趋势", "渠道分析", "自动复盘提醒"])

    if display_df.empty:
        empty_title = "数据库读取失败" if history_message.startswith("数据库读取失败") else "暂无历史数据"
        empty_text = "请管理员检查连接配置。" if empty_title == "数据库读取失败" else "请管理员在后台导入数据。"
        with tab_home:
            render_info_box(empty_text, title=empty_title)
        with tab_trend:
            render_info_box(empty_text, title=empty_title)
        with tab_channel:
            render_info_box(empty_text, title=empty_title)
        with tab_alert:
            render_info_box(empty_text, title=empty_title)
        return

    with tab_home:
        render_boss_home(display_df)
    with tab_trend:
        render_trends(display_df)
    with tab_channel:
        render_channel_analysis(display_df)
    with tab_alert:
        render_alerts(display_df)


if __name__ == "__main__":
    main()
