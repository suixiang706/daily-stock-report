"""
每日A股收盘简报 — GitHub Actions 定时运行
周一至周五 15:30 CST 自动执行，输出 Markdown 笔记
"""
import akshare as ak
import pandas as pd
import numpy as np
import os
from datetime import datetime, timezone, timedelta

# ==================== 配置 ====================
# 监控股票池（可自行增减）
STOCKS = [
    ("688807", "优迅股份"),
    ("688008", "澜起科技"),
    ("601899", "紫金矿业"),
    ("600586", "金晶科技"),
]

# 大盘指数
INDEXES = [("sh000001", "上证指数"), ("sz399001", "深证成指"), ("sh000300", "沪深300")]

# 输出目录（GitHub Actions 环境）
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", os.path.dirname(os.path.abspath(__file__)))
REPORT_PATH = os.path.join(OUTPUT_DIR, "reports")


def fetch_stock_data(symbol):
    """拉取个股日线，返回最后一行"""
    try:
        df = ak.stock_zh_a_hist(
            symbol=symbol, period="daily",
            start_date="20260101", end_date="20991231",
            adjust="qfq"
        )
        if df.empty:
            return None
        return df.iloc[-1]
    except Exception as e:
        print(f"  [{symbol}] 数据拉取失败: {e}")
        return None


def fetch_index_value(code):
    """从新浪拉取指数当前值"""
    import urllib.request
    import re
    try:
        url = f"http://hq.sinajs.cn/list={code}"
        req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn"})
        resp = urllib.request.urlopen(req, timeout=10)
        raw = resp.read().decode("gbk")
        parts = raw.split('"')[1].split(",")
        if len(parts) < 4:
            return None
        name = parts[0]
        price = float(parts[3])
        prev = float(parts[2])
        change = (price - prev) / prev * 100
        return {"name": name, "price": price, "change": change}
    except Exception as e:
        print(f"  [{code}] 指数拉取失败: {e}")
        return None


def calc_indicators(df):
    """计算均线、MACD、KDJ、布林带"""
    close = df["收盘"].values
    high = df["最高"].values
    low = df["最低"].values
    vol = df["成交量"].values

    if len(close) < 21:
        return {}

    t = -1
    ma5 = pd.Series(close).rolling(5).mean().values[t]
    ma10 = pd.Series(close).rolling(10).mean().values[t]
    ma20 = pd.Series(close).rolling(20).mean().values[t]

    # MACD
    ema12 = pd.Series(close).ewm(span=12, adjust=False).mean()
    ema26 = pd.Series(close).ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    macd_bar = 2 * (dif - dea)

    # KDJ
    n = 9
    low_n = pd.Series(low).rolling(n).min().values
    high_n = pd.Series(high).rolling(n).max().values
    rsv = np.where(high_n != low_n, (close - low_n) / (high_n - low_n) * 100, 50)
    k_vals = np.zeros(len(close))
    d_vals = np.zeros(len(close))
    k_vals[0], d_vals[0] = 50, 50
    for i in range(1, len(close)):
        k_vals[i] = 2/3 * k_vals[i-1] + 1/3 * rsv[i]
        d_vals[i] = 2/3 * d_vals[i-1] + 1/3 * k_vals[i]
    j_vals = 3 * k_vals - 2 * d_vals

    # 布林
    bb_mid = pd.Series(close).rolling(20).mean().values[t]
    bb_std = pd.Series(close).rolling(20).std().values[t]
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std
    bb_pos = (close[t] - bb_lower) / (bb_upper - bb_lower) * 100 if bb_upper != bb_lower else 50

    # 涨跌幅
    chg = (close[t] - close[t-1]) / close[t-1] * 100
    chg5 = (close[t] - close[t-5]) / close[t-5] * 100 if len(close) > 5 else 0
    chg20 = (close[t] - close[t-20]) / close[t-20] * 100 if len(close) > 20 else 0

    # 量比
    vol5 = pd.Series(vol).rolling(5).mean().values[t]
    vol_ratio = vol[t] / vol5 if vol5 > 0 else 1

    # 信号
    signals = []
    if close[t] > ma5 > ma10:
        signals.append("多头排列")
    if close[t] < ma5 < ma10:
        signals.append("空头排列")
    if close[t] < bb_lower:
        signals.append("跌破布林下轨⚡")
    if k_vals[t] < 20 and d_vals[t] < 20:
        signals.append("KDJ超卖区")
    if j_vals[t] > 100:
        signals.append("J值>100")
    if vol_ratio > 2:
        signals.append("放量异动")

    return {
        "date": df["日期"].values[t],
        "open": df["开盘"].values[t],
        "close": close[t],
        "high": high[t],
        "low": low[t],
        "chg": round(chg, 2),
        "chg5": round(chg5, 2),
        "chg20": round(chg20, 2),
        "vol_ratio": round(vol_ratio, 2),
        "ma5": round(ma5, 2),
        "ma10": round(ma10, 2),
        "ma20": round(ma20, 2),
        "bb_pos": round(bb_pos, 1),
        "bb_lower": round(bb_lower, 2),
        "bb_upper": round(bb_upper, 2),
        "macd_bar": round(macd_bar.values[t], 3),
        "k": round(k_vals[t], 1),
        "d": round(d_vals[t], 1),
        "j": round(j_vals[t], 1),
        "signals": signals,
    }


def generate_report():
    """生成每日简报"""
    now = datetime.now(timezone(timedelta(hours=8)))  # CST
    date_str = now.strftime("%Y-%m-%d")
    weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]

    if now.weekday() >= 5:
        print(f">>> 今天是{weekday}，非交易日，跳过。")
        return

    print(f">>> 生成 {date_str} {weekday} 市场简报 ...")

    lines = [
        f"# {date_str} {weekday} A股收盘简报\n",
        f"自动生成时间：{now.strftime('%H:%M')}\n",
    ]

    # ---- 大盘 ----
    lines.append("## 大盘指数\n")
    lines.append("| 指数 | 收盘价 | 涨跌幅 |")
    lines.append("|------|--------|--------|")
    index_data = {}
    for code, label in INDEXES:
        d = fetch_index_value(code)
        if d:
            index_data[label] = d
            arrow = "🔴" if d["change"] < 0 else "🟢" if d["change"] > 0 else "⚪"
            lines.append(f"| {label} | {d['price']:.2f} | {arrow} {d['change']:+.2f}% |")
    lines.append("")

    # ---- 个股 ----
    lines.append("## 监控个股\n")
    stock_results = []

    for symbol, name in STOCKS:
        row = fetch_stock_data(symbol)
        if row is None:
            lines.append(f"### {name} ({symbol})\n")
            lines.append("> ⚠️ 数据获取失败\n")
            stock_results.append((name, symbol, None))
            continue

        df = ak.stock_zh_a_hist(
            symbol=symbol, period="daily",
            start_date="20251001", end_date="20991231",
            adjust="qfq"
        )
        ind = calc_indicators(df)
        if not ind:
            lines.append(f"### {name} ({symbol})\n")
            lines.append("> 数据不足\n")
            stock_results.append((name, symbol, None))
            continue

        stock_results.append((name, symbol, ind))

        arrow = "🔴" if ind["chg"] < 0 else "🟢" if ind["chg"] > 0 else "⚪"
        lines.append(f"### {name} ({symbol})  {arrow} {ind['chg']:+.2f}%\n")
        lines.append(f"- **收盘**: {ind['close']:.2f}  |  开盘: {ind['open']:.2f}")
        lines.append(f"  最高: {ind['high']:.2f}  |  最低: {ind['low']:.2f}")
        lines.append(f"- **均线**: MA5={ind['ma5']}  MA10={ind['ma10']}  MA20={ind['ma20']}")
        lines.append(f"- **布林**: 位置 {ind['bb_pos']:.1f}%（下轨 {ind['bb_lower']} / 上轨 {ind['bb_upper']}）")
        lines.append(f"- **MACD**: 柱 {ind['macd_bar']:.3f}（{'红柱' if ind['macd_bar'] > 0 else '绿柱'}）")
        lines.append(f"- **KDJ**: K={ind['k']:.1f}  D={ind['d']:.1f}  J={ind['j']:.1f}")
        lines.append(f"- **量比**: {ind['vol_ratio']:.2f}x")
        lines.append(f"- **涨跌**: 日 {ind['chg']:+.2f}%  |  5日 {ind['chg5']:+.2f}%  |  20日 {ind['chg20']:+.2f}%")
        if ind["signals"]:
            lines.append(f"- **信号**: {' | '.join(ind['signals'])}")
        lines.append("")

    lines.append("---\n")
    lines.append("*本报告由 GitHub Actions 自动生成，仅供记录参考，不构成投资建议。*\n")

    # 写入文件
    os.makedirs(REPORT_PATH, exist_ok=True)
    filename = os.path.join(REPORT_PATH, f"report_{date_str}.md")

    with open(filename, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f">>> 报告已生成: {filename}")
    print("\n".join(lines))

    # 生成微信推送摘要
    generate_push_summary(now, date_str, weekday, index_data, stock_results)
    return index_data, stock_results


def generate_push_summary(now, date_str, weekday, index_data, stock_results):
    """生成 Server酱 微信推送摘要"""
    push_lines = []

    # 标题行（微信卡片标题）
    sh_idx = index_data.get("上证指数", {})
    title = f"{date_str.split('-')[1]}/{date_str.split('-')[2]} 简报"
    if sh_idx:
        arrow = "↑" if sh_idx["change"] > 0 else "↓" if sh_idx["change"] < 0 else "→"
        title += f" 上证{arrow}{abs(sh_idx['change']):.1f}%"

    # 找出涨跌最显著的个股加到标题
    alerts = []
    for name, symbol, ind in stock_results:
        if not ind:
            continue
        if abs(ind["chg"]) > 3:
            alerts.append(f"{name}{'🔴' if ind['chg']<0 else '🟢'}{ind['chg']:+.1f}%")
    if alerts:
        title += " " + " ".join(alerts[:2])

    body = []

    # 指数一行
    idx_parts = []
    for label, data in index_data.items():
        if data:
            a = "↑" if data["change"] > 0 else "↓" if data["change"] < 0 else "→"
            idx_parts.append(f"{label} {data['price']:.1f} {a}{data['change']:+.2f}%")
    body.append("  ".join(idx_parts))
    body.append("")

    # 个股（每只一行）
    for name, symbol, ind in stock_results:
        if not ind:
            body.append(f"**{name}** ⚠️ 数据获取失败")
            continue
        arrow = "↑" if ind["chg"] > 0 else "↓" if ind["chg"] < 0 else "→"
        line = f"**{name}** {arrow}{ind['chg']:+.2f}% {ind['close']:.2f}"
        if ind["vol_ratio"] > 1.5:
            line += " | 放量"
        if ind["signals"]:
            line += f" | {' '.join(ind['signals'])}"
        line += f"\n  MA5={ind['ma5']} MA20={ind['ma20']} | KDJ K={ind['k']:.0f} J={ind['j']:.0f} | 布林{ind['bb_pos']:.0f}%"
        body.append(line)

    body.append("")
    body.append(f"[查看完整报告](https://github.com/suixiang706/daily-stock-report/tree/main/reports)")

    # 写入推送文件
    push_file = os.path.join(REPORT_PATH, "push_summary.txt")
    with open(push_file, "w", encoding="utf-8") as f:
        f.write(title + "\n")
        f.write("\n".join(body))

    print(f">>> 推送摘要已生成: {push_file}")


if __name__ == "__main__":
    generate_report()
