#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analytics.py — 市場データの分析ロジック

外部ライブラリを使わない純Python実装。理由は2つ。
  1. GitHub Actions で pip install の時間と失敗要因を減らすため
  2. 計算の中身が全部このファイルに見えている方が、後で検算しやすいため

日付の突き合わせ（align）が最重要。市場ごとに休場日が違うので、
日付を揃えずに相関を取ると意味のない数字が出る。
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta


# ==========================================================================
# 基本
# ==========================================================================
def align(a: dict[str, float], b: dict[str, float]) -> tuple[list[str], list[float], list[float]]:
    """日付→値の辞書2つを、両方に存在する日付だけで揃える。

    市場ごとに休場日が違うため、これをやらないと日付がずれたまま
    相関を計算してしまう。
    """
    keys = sorted(set(a) & set(b))
    return keys, [a[k] for k in keys], [b[k] for k in keys]


def returns(series: dict[str, float]) -> dict[str, float]:
    """日次の変化率（％）。連続する2営業日の終値から計算する。"""
    ks = sorted(series)
    out = {}
    for i in range(1, len(ks)):
        p0, p1 = series[ks[i - 1]], series[ks[i]]
        if p0 and p0 != 0:
            out[ks[i]] = (p1 / p0 - 1.0) * 100.0
    return out


def diffs(series: dict[str, float]) -> dict[str, float]:
    """日次の差分。利回りなど「％で表された水準」に使う（変化率では意味が壊れる）。"""
    ks = sorted(series)
    return {ks[i]: series[ks[i]] - series[ks[i - 1]] for i in range(1, len(ks))}


def mean(v: list[float]) -> float:
    return sum(v) / len(v) if v else 0.0


def stdev(v: list[float]) -> float:
    """標本標準偏差（n-1で割る）。"""
    if len(v) < 2:
        return 0.0
    m = mean(v)
    return math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))


def corr(x: list[float], y: list[float]) -> float | None:
    """ピアソン相関。標本が10未満、または片方が動いていない場合は None。"""
    n = min(len(x), len(y))
    if n < 10:
        return None
    x, y = x[-n:], y[-n:]
    mx, my = mean(x), mean(y)
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx <= 0 or syy <= 0:
        return None
    return max(-1.0, min(1.0, sxy / math.sqrt(sxx * syy)))


def corr_ci(r: float | None, n: int, conf: float = 0.95) -> dict | None:
    """相関係数の信頼区間。フィッシャーのz変換を使う。

    標本30個の相関は誤差が大きい。0.06 と −0.2 が統計的に区別できないことを
    画面で示すために必要な計算。
    """
    if r is None or n < 5 or abs(r) >= 1:
        return None
    z = 1.959963985 if conf == 0.95 else 1.644853627
    f = 0.5 * math.log((1 + r) / (1 - r))     # z変換
    se = 1 / math.sqrt(n - 3)
    lo, hi = f - z * se, f + z * se
    inv = lambda v: (math.exp(2 * v) - 1) / (math.exp(2 * v) + 1)
    return {"r": round(r, 4), "n": n, "conf": conf,
            "lo": round(inv(lo), 4), "hi": round(inv(hi), 4)}


def ols(y: list[float], x: list[float]) -> dict | None:
    """最小二乗回帰 y = α + βx。β・α・決定係数R²を返す。

    R² は「yの動きのうち x で説明できた割合」。0.18 なら18%しか説明できていない。
    """
    n = min(len(x), len(y))
    if n < 20:
        return None
    x, y = x[-n:], y[-n:]
    mx, my = mean(x), mean(y)
    sxx = sum((a - mx) ** 2 for a in x)
    if sxx <= 0:
        return None
    beta = sum((a - mx) * (b - my) for a, b in zip(x, y)) / sxx
    alpha = my - beta * mx
    ss_tot = sum((b - my) ** 2 for b in y)
    ss_res = sum((b - (alpha + beta * a)) ** 2 for a, b in zip(x, y))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return {"alpha": alpha, "beta": beta, "r2": r2, "n": n}


def rolling_corr(dates: list[str], x: list[float], y: list[float], w: int) -> list[list]:
    """ローリング相関。[[日付, 相関], ...] を返す。"""
    out = []
    for i in range(w, len(x) + 1):
        c = corr(x[i - w:i], y[i - w:i])
        if c is not None:
            out.append([dates[i - 1], round(c, 4)])
    return out


def zscore(series: list[float], window: int = 252) -> float | None:
    """直近値が、過去windowの分布から何σ離れているか。"""
    if len(series) < 30:
        return None
    hist = series[-window:]
    s = stdev(hist[:-1])
    if s <= 0:
        return None
    return (hist[-1] - mean(hist[:-1])) / s


def percentile_rank(value: float, sample: list[float]) -> float | None:
    """valueがsampleの下から何％の位置にあるか（0〜100）。"""
    if len(sample) < 30:
        return None
    below = sum(1 for v in sample if v < value)
    return below / len(sample) * 100.0


def quantile(sample: list[float], q: float) -> float:
    """線形補間による分位点。q は 0〜1。"""
    if not sample:
        return 0.0
    s = sorted(sample)
    if len(s) == 1:
        return s[0]
    pos = q * (len(s) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def sma(series: dict[str, float], w: int) -> dict[str, float]:
    ks = sorted(series)
    out = {}
    for i in range(w - 1, len(ks)):
        out[ks[i]] = mean([series[k] for k in ks[i - w + 1:i + 1]])
    return out


def realized_vol(rets: list[float], w: int = 20) -> float | None:
    """年率換算した実現ボラティリティ（％）。日次リターン％の標準偏差×√252。"""
    if len(rets) < w:
        return None
    return stdev(rets[-w:]) * math.sqrt(252)


def max_drawdown(series: dict[str, float]) -> dict:
    """過去最高値からの下落率と、その最高値の日付。"""
    ks = sorted(series)
    peak, peak_k, mdd, mdd_k = -1e18, None, 0.0, None
    for k in ks:
        v = series[k]
        if v > peak:
            peak, peak_k = v, k
        dd = (v / peak - 1) * 100 if peak > 0 else 0.0
        if dd < mdd:
            mdd, mdd_k = dd, k
    last = series[ks[-1]]
    cur = (last / peak - 1) * 100 if peak > 0 else 0.0
    return {"current_dd": round(cur, 2), "peak_date": peak_k,
            "max_dd": round(mdd, 2), "max_dd_date": mdd_k}


# ==========================================================================
# リード・ラグ
# ==========================================================================
def lead_lag(x: list[float], y: list[float], max_lag: int = 7) -> list[list]:
    """xをずらしてyとの相関を測る。

    lag>0 は「xがlag日前、yが今日」。つまり x が先行して y が後追い。
    返り値は [[lag, 相関], ...]。
    """
    out = []
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            a, b = x[:len(x) - lag], y[lag:]
        else:
            a, b = x[-lag:], y[:len(y) + lag]
        c = corr(a, b)
        out.append([lag, round(c, 4) if c is not None else None])
    return out


# ==========================================================================
# 曜日・月のアノマリー
# ==========================================================================
def calendar_stats(rets: dict[str, float], kind: str = "dow") -> list[dict]:
    """曜日別または月別の平均リターン・勝率・標本数。

    標本数も必ず返す。少ない標本の平均値を根拠にしないため。
    """
    buckets: dict[int, list[float]] = {}
    for k, v in rets.items():
        d = datetime.strptime(k, "%Y-%m-%d").date()
        key = d.weekday() if kind == "dow" else d.month
        buckets.setdefault(key, []).append(v)

    rng = range(7) if kind == "dow" else range(1, 13)
    out = []
    for i in rng:
        vs = buckets.get(i, [])
        if not vs:
            out.append({"key": i, "n": 0, "mean": None, "winrate": None, "tstat": None})
            continue
        m, s = mean(vs), stdev(vs)
        # t値：平均が0から何σ離れているか。|t|<2 なら偶然の範囲と考えるのが普通。
        t = m / (s / math.sqrt(len(vs))) if s > 0 and len(vs) > 1 else None
        out.append({
            "key": i, "n": len(vs), "mean": round(m, 4),
            "winrate": round(sum(1 for v in vs if v > 0) / len(vs) * 100, 1),
            "tstat": round(t, 2) if t is not None else None,
        })
    return out


# ==========================================================================
# 類似局面（予測ではなく、過去実績の分布）
# ==========================================================================
def independent_clusters(dates: list[str], gap_days: int = 20) -> int:
    """該当日のうち、互いにgap_days以上離れているものを数える。

    連続した日が何十回該当しても、それは「1つの局面」でしかない。
    31回という数字をそのまま標本数として扱わないための計算。
    """
    if not dates:
        return 0
    ds = sorted(datetime.strptime(d, "%Y-%m-%d").date() for d in dates)
    n, last = 1, ds[0]
    for d in ds[1:]:
        if (d - last).days >= gap_days:
            n += 1
            last = d
    return n


def analogs(prices: dict[str, float], match_dates: list[str], horizon: int = 20) -> dict | None:
    """条件に該当した日から先のリターン分布を、パーセンタイル帯で返す。

    これは予測ではない。「過去に同じ条件だったとき、その後どう散らばったか」。
    """
    ks = sorted(prices)
    idx = {k: i for i, k in enumerate(ks)}
    paths = []
    for d in match_dates:
        i = idx.get(d)
        if i is None or i + horizon >= len(ks):
            continue
        base = prices[ks[i]]
        if not base:
            continue
        paths.append([(prices[ks[i + h]] / base - 1) * 100 for h in range(horizon + 1)])
    if len(paths) < 5:
        return None
    bands = {q: [] for q in (10, 25, 50, 75, 90)}
    for h in range(horizon + 1):
        col = [p[h] for p in paths]
        for q in bands:
            bands[q].append(round(quantile(col, q / 100), 2))
    return {
        "n_matches": len(paths),
        "n_independent": independent_clusters(match_dates),
        "horizon": horizon,
        "bands": {str(q): v for q, v in bands.items()},
    }


# ==========================================================================
# リスク選好指数
# ==========================================================================
RISK_COMPONENTS = [
    # (名前, 銘柄キー, 向き, 説明)
    ("株式", "SPX", +1, "S&P500の5日変化。上がればリスクオン"),
    ("恐怖指数", "VIX", -1, "VIXの5日変化。下がればリスクオン"),
    ("景気敏感", "COPPER_GOLD", +1, "銅÷金の5日変化。銅が優勢ならリスクオン"),
    ("長期金利", "US10Y", +1, "米10年利回りの5日変化。上がればリスクオン"),
    ("ドル", "DXY", -1, "ドル指数の5日変化。ドル安ならリスクオン"),
]


def risk_appetite(closes: dict[str, dict[str, float]], lookback: int = 252) -> dict | None:
    """5つの市場の5日変化をzスコア化し、平均して −100〜+100 に写す。

    各成分の内訳も返す。どの市場がメーターを動かしているかを画面に出すため。
    """
    parts, detail = [], []
    for name, key, sign, desc in RISK_COMPONENTS:
        s = closes.get(key)
        if not s or len(s) < 60:
            continue
        ks = sorted(s)
        ch = [(s[ks[i]] - s[ks[i - 5]]) for i in range(5, len(ks))]
        z = zscore(ch, lookback)
        if z is None:
            continue
        parts.append(sign * z)
        detail.append({"name": name, "z": round(sign * z, 2), "desc": desc})
    if len(parts) < 3:
        return None
    raw = mean(parts)
    # tanh で −100〜+100 に収める。2σ相当がおよそ ±76 になる。
    return {"value": round(math.tanh(raw) * 100, 1), "components": detail,
            "raw_mean_z": round(raw, 3)}


def risk_appetite_series(closes: dict[str, dict[str, float]], days: int = 180) -> list[list]:
    """リスク選好指数の推移。日ごとに同じ計算を繰り返す。"""
    # 全銘柄に共通して存在する日付だけを使う
    keys = None
    for _, k, _, _ in RISK_COMPONENTS:
        s = closes.get(k)
        if not s:
            continue
        keys = set(s) if keys is None else keys & set(s)
    if not keys:
        return []
    ks = sorted(keys)
    out = []
    for i in range(len(ks)):
        if i < 260:
            continue
        window = {k: {d: closes[k][d] for d in ks[:i + 1] if d in closes.get(k, {})}
                  for _, k, _, _ in RISK_COMPONENTS if k in closes}
        r = risk_appetite(window)
        if r:
            out.append([ks[i], r["value"]])
    return out[-days:]


# ==========================================================================
# セルフテスト
# ==========================================================================
def _approx(a, b, tol=1e-6):
    return a is not None and abs(a - b) < tol


def selftest() -> int:
    print("analytics.py セルフテスト")
    print("-" * 70)
    fails, total = [], [0]

    def ck(name, cond, got=""):
        total[0] += 1
        if cond:
            print(f"  OK  {name}")
        else:
            print(f"  NG  {name}  {got}")
            fails.append(name)

    # 相関：完全一致 / 完全逆 / 無相関
    a = [float(i) for i in range(30)]
    ck("相関 完全一致=1", _approx(corr(a, a), 1.0))
    ck("相関 完全逆=-1", _approx(corr(a, [-v for v in a]), -1.0))
    ck("相関 標本不足はNone", corr([1, 2, 3], [1, 2, 3]) is None)
    ck("相関 定数列はNone", corr(a, [5.0] * 30) is None)

    # 信頼区間
    ci = corr_ci(0.06, 30)
    ck("信頼区間が相関を挟む", ci["lo"] < 0.06 < ci["hi"], ci)
    ck("標本30の相関0.06は0を跨ぐ", ci["lo"] < 0 < ci["hi"], ci)
    ck("標本が増えると幅が狭まる",
       (corr_ci(0.06, 500)["hi"] - corr_ci(0.06, 500)["lo"])
       < (ci["hi"] - ci["lo"]))
    ck("相関がNoneならNone", corr_ci(None, 30) is None)

    # 回帰：y = 3 + 2x なら β=2, α=3, R²=1
    x = [float(i) for i in range(50)]
    y = [3 + 2 * v for v in x]
    r = ols(y, x)
    ck("回帰 β=2", _approx(r["beta"], 2.0), r)
    ck("回帰 α=3", _approx(r["alpha"], 3.0), r)
    ck("回帰 R²=1", _approx(r["r2"], 1.0), r)

    # 日付の突き合わせ：片方にしかない日を落とす
    A = {"2026-01-01": 1.0, "2026-01-02": 2.0, "2026-01-03": 3.0}
    B = {"2026-01-02": 20.0, "2026-01-03": 30.0, "2026-01-04": 40.0}
    k, va, vb = align(A, B)
    ck("日付揃え 共通日のみ", k == ["2026-01-02", "2026-01-03"] and va == [2.0, 3.0], (k, va, vb))

    # 変化率
    rr = returns({"2026-01-01": 100.0, "2026-01-02": 110.0, "2026-01-03": 99.0})
    ck("変化率 +10%", _approx(rr["2026-01-02"], 10.0), rr)
    ck("変化率 -10%", _approx(rr["2026-01-03"], -10.0), rr)

    # zスコア：平均0・標準偏差1の系列に最後だけ3を足す
    base = [0.0, 1.0, -1.0] * 40
    ck("zスコア 符号が正", (zscore(base + [3.0]) or 0) > 2)

    # 分位点
    ck("分位点 中央値", _approx(quantile([1, 2, 3, 4, 5], 0.5), 3.0))
    ck("分位点 補間", _approx(quantile([0, 10], 0.25), 2.5))

    # パーセンタイル
    ck("パーセンタイル 最小値付近", _approx(percentile_rank(-99, list(range(100))), 0.0))

    # リード・ラグ：yがxの2日遅れなら lag=+2 が最大になるはず
    import random
    random.seed(7)
    src = [random.gauss(0, 1) for _ in range(400)]
    xs, ys = src, [0.0, 0.0] + src[:-2]
    ll = lead_lag(xs, ys, 5)
    best = max((v for v in ll if v[1] is not None), key=lambda v: v[1])
    ck("リード・ラグ 2日遅れを検出", best[0] == 2, ll)

    # ドローダウン
    dd = max_drawdown({"2026-01-01": 100.0, "2026-01-02": 120.0, "2026-01-03": 90.0})
    ck("ドローダウン 高値120から-25%", _approx(dd["current_dd"], -25.0), dd)
    ck("ドローダウン 高値日付", dd["peak_date"] == "2026-01-02", dd)

    # 曜日集計：2026-01-05 は月曜
    ck("曜日 月曜の判定", datetime.strptime("2026-01-05", "%Y-%m-%d").weekday() == 0)
    cs = calendar_stats({"2026-01-05": 1.0, "2026-01-12": 3.0, "2026-01-06": -1.0}, "dow")
    mon = [c for c in cs if c["key"] == 0][0]
    ck("曜日 月曜の平均=2.0", _approx(mon["mean"], 2.0) and mon["n"] == 2, mon)
    ck("曜日 勝率100%", _approx(mon["winrate"], 100.0), mon)
    ck("曜日 データ無しはNone", [c for c in cs if c["key"] == 4][0]["mean"] is None)

    # 独立局面のクラスタ数
    seq = ["2026-01-01", "2026-01-02", "2026-01-03", "2026-03-01", "2026-03-02"]
    ck("独立局面 連続日は1つに数える", independent_clusters(seq, 20) == 2,
       independent_clusters(seq, 20))

    # 類似局面：単調上昇の価格なら全帯が正
    prices = {f"2026-{(i//28)+1:02d}-{(i%28)+1:02d}": 100.0 * (1.01 ** i) for i in range(300)}
    md = sorted(prices)[:50]
    an = analogs(prices, md, 20)
    ck("類似局面 中央値が正", an and an["bands"]["50"][-1] > 0, an and an["bands"]["50"][-1])
    ck("類似局面 独立数を併記", an and an["n_independent"] < an["n_matches"],
       an and (an["n_independent"], an["n_matches"]))
    ck("類似局面 標本不足はNone", analogs(prices, md[:2], 20) is None)

    # 利回りは差分、価格は変化率
    yld = diffs({"2026-01-01": 4.50, "2026-01-02": 4.79})
    ck("利回りは差分で計算", _approx(yld["2026-01-02"], 0.29), yld)

    print("-" * 70)
    print(f"結果: {total[0] - len(fails)}/{total[0]} 合格")
    if fails:
        print("失敗:", ", ".join(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(selftest())
