#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MTO 型钢清单逐根正确率比对脚本

用法:
  python compare_steel.py
  python compare_steel.py --baseline "MTO.xlsx" --candidate "steel_takeoff (4).xlsx"
  python compare_steel.py --tol 2.0 --csv diff.csv
  python compare_steel.py --include-plate   # 把 PL12/PL20/... 纳入核对

返回码: 0=全部匹配, 1=存在差异（便于 CI 判定）
"""

from __future__ import annotations
import argparse, csv, re, sys
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    import pandas as pd
except ImportError:
    sys.exit("请先安装依赖: pip install pandas openpyxl")

DEFAULT_BASELINE  = "4422-AI3D-MM51302003-PR001I-A_MTO_IFC_00(分列).xlsx"
DEFAULT_CANDIDATE = "steel_takeoff (4).xlsx"

# 2LF-L120*12 / 2F-L120*12 → L120*12
_COMBO_PREFIX = re.compile(r"^2(?:LF|F)-")
# 去除连接/安装后缀: /EPW /EPB /EMH /EMTF /EP /LT /EM
_SUFFIX = re.compile(r"/(EPW|EPB|EMH|EMTF|EP|LT|EM)$")
# 板材 (PL12, PL20, PL35, PL50 ...)
_PLATE = re.compile(r"^PL\d+(?:\.\d+)?$")


def normalize_spec(s: Any) -> str:
    if s is None:
        return ""
    t = str(s).strip().upper().replace(" ", "")
    if not t or t in ("NAN", "NONE"):
        return ""
    t = _SUFFIX.sub("", t)
    t = _COMBO_PREFIX.sub("", t)
    return t


def is_plate_spec(spec: str) -> bool:
    return bool(_PLATE.match(spec))


def _pick_column(columns, candidates):
    norm = {str(c).strip().lower().replace(" ", ""): c for c in columns}
    for cand in candidates:
        if cand.lower().replace(" ", "") in norm:
            return norm[cand.lower().replace(" ", "")]
    return None


def load_baseline(path: str, sheet: Any = 0, include_plate: bool = False):
    """MTO 基准: 每行 1 根。返回 [(spec, length), ...]"""
    df = pd.read_excel(path, sheet_name=sheet)
    spec_col = _pick_column(df.columns, ["PROFILE", "SPEC", "规格", "型材"])
    len_col  = _pick_column(df.columns, ["LENGTH(mm)", "LENGTH", "长度(mm)", "长度", "名义长度"])
    if spec_col is None or len_col is None:
        raise SystemExit(f"基准文件缺列 PROFILE/LENGTH(mm)，实际列: {list(df.columns)}")
    out = []
    for _, r in df.iterrows():
        spec = normalize_spec(r[spec_col])
        if not spec: continue
        if not include_plate and is_plate_spec(spec): continue
        try:
            out.append((spec, float(r[len_col])))
        except (ValueError, TypeError):
            continue
    return out


def load_candidate(path: str, sheet: str, include_plate: bool = False):
    """待测汇总: 按 单根数量 展开。返回 [(spec, length), ...]"""
    xl = pd.ExcelFile(path)
    if sheet not in xl.sheet_names:
        raise SystemExit(f"待测 sheet '{sheet}' 不存在。可用: {xl.sheet_names}")
    df = xl.parse(sheet)
    spec_col = _pick_column(df.columns, ["规格", "PROFILE", "型材", "SPEC"])
    len_col  = _pick_column(df.columns, ["名义长度mm", "名义长度", "名义长度(mm)", "LENGTH(mm)", "LENGTH", "长度"])
    qty_col  = _pick_column(df.columns, ["单根数量", "数量", "根数", "QTY", "COUNT"])
    if not (spec_col and len_col and qty_col):
        raise SystemExit(f"待测文件缺列，实际列: {list(df.columns)}")
    out = []
    for _, r in df.iterrows():
        spec = normalize_spec(r[spec_col])
        if not spec: continue
        if not include_plate and is_plate_spec(spec): continue
        try:
            ln  = float(r[len_col]); qty = int(round(float(r[qty_col])))
        except (ValueError, TypeError):
            continue
        if qty > 0:
            out.extend([(spec, ln)] * qty)
    return out


def match_multiset(baseline, candidate, tol):
    """
    按规格分组 → 排序双指针多重集匹配（容差内优先取最近）
    返回 (matched, um_base, um_cand)
    """
    b_by, c_by = defaultdict(list), defaultdict(list)
    for s, l in baseline:  b_by[s].append(l)
    for s, l in candidate: c_by[s].append(l)

    matched, um_b, um_c = [], [], []
    for s in sorted(set(b_by) | set(c_by)):
        b = sorted(b_by.get(s, [])); c = sorted(c_by.get(s, []))
        i = j = 0
        while i < len(b) and j < len(c):
            if abs(b[i] - c[j]) <= tol:
                matched.append((s, b[i], c[j])); i += 1; j += 1
            elif b[i] < c[j]:
                um_b.append((s, b[i])); i += 1
            else:
                um_c.append((s, c[j])); j += 1
        um_b.extend((s, x) for x in b[i:])
        um_c.extend((s, x) for x in c[j:])
    return matched, um_b, um_c


def fmt_len(x: float) -> str:
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return f"{x:.2f}".rstrip("0").rstrip(".")


def pct(a: int, b: int) -> str:
    return f"{a/b*100:.2f}%" if b else "n/a"


def summarize_by_spec(baseline, candidate, matched):
    b_n, c_n, m_n = defaultdict(int), defaultdict(int), defaultdict(int)
    for s, _ in baseline:  b_n[s] += 1
    for s, _ in candidate: c_n[s] += 1
    for s, _, _ in matched: m_n[s] += 1
    rows = []
    for s in sorted(set(b_n) | set(c_n)):
        B, C, M = b_n.get(s, 0), c_n.get(s, 0), m_n.get(s, 0)
        rows.append({
            "规格": s, "基准": B, "待测": C, "匹配": M,
            "待测错/多": C - M, "基准漏": B - M,
            "待测正确率": pct(M, C), "基准覆盖率": pct(M, B),
        })
    return rows


def group_by_spec_len(pairs):
    d = defaultdict(int)
    for s, l in pairs: d[(s, round(float(l), 2))] += 1
    return sorted(d.items(), key=lambda kv: (kv[0][0], kv[0][1]))


def print_table(headers, rows):
    widths = [len(str(h)) for h in headers]
    for r in rows:
        for i, v in enumerate(r):
            widths[i] = max(widths[i], len(str(v)))
    print("  ".join(str(h).ljust(widths[i]) for i, h in enumerate(headers)))
    print("  ".join("-" * widths[i] for i in range(len(headers))))
    for r in rows:
        print("  ".join(str(v).ljust(widths[i]) for i, v in enumerate(r)))


def export_csv(path, matched, um_b, um_c):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["类别", "规格", "基准长度_mm", "待测长度_mm", "差值_mm"])
        for s, bl, cl in matched:
            w.writerow(["匹配", s, fmt_len(bl), fmt_len(cl), fmt_len(cl - bl)])
        for s, l in um_b:
            w.writerow(["基准漏计", s, fmt_len(l), "", ""])
        for s, l in um_c:
            w.writerow(["待测多计/错长", s, "", fmt_len(l), ""])


def main():
    ap = argparse.ArgumentParser(
        description="MTO 型钢清单逐根正确率比对",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--baseline", default=DEFAULT_BASELINE, help="MTO 基准 xlsx")
    ap.add_argument("--baseline-sheet", default=0, help="基准 sheet 名或索引")
    ap.add_argument("--candidate", default=DEFAULT_CANDIDATE, help="待测 xlsx")
    ap.add_argument("--candidate-sheet", default="Quantity Length Summary", help="待测 sheet 名")
    ap.add_argument("--tol", type=float, default=1.0, help="长度容差 mm")
    ap.add_argument("--include-plate", action="store_true", help="把 PL 板纳入核对")
    ap.add_argument("--csv", default=None, help="导出逐根差异 CSV")
    args = ap.parse_args()

    for p in (args.baseline, args.candidate):
        if not Path(p).exists():
            sys.exit(f"文件不存在: {p}")

    print(f"[1/3] 读取基准: {args.baseline} (sheet={args.baseline_sheet})")
    baseline = load_baseline(args.baseline, args.baseline_sheet, args.include_plate)
    print(f"      基准型钢根数 = {len(baseline)}")

    print(f"[2/3] 读取待测: {args.candidate} (sheet={args.candidate_sheet})")
    candidate = load_candidate(args.candidate, args.candidate_sheet, args.include_plate)
    print(f"      待测型钢根数 = {len(candidate)}")

    print(f"[3/3] 匹配 (tol=±{args.tol} mm)")
    matched, um_b, um_c = match_multiset(baseline, candidate, args.tol)

    B, C, M = len(baseline), len(candidate), len(matched)

    print()
    print("=" * 60); print("总体结果"); print("=" * 60)
    print(f"基准型钢根数        : {B}")
    print(f"待测型钢根数        : {C}")
    print(f"逐根匹配            : {M}")
    print(f"待测多计/错长        : {len(um_c)}")
    print(f"基准漏计            : {len(um_b)}")
    print(f"待测送审正确率      : {pct(M, C)}  ({M}/{C})")
    print(f"对基准覆盖率/召回率 : {pct(M, B)}  ({M}/{B})")
    print(f"长度容差            : ±{args.tol} mm")
    print(f"BASE PLATE          : {'纳入' if args.include_plate else '已排除'}")

    print()
    print("=" * 60); print("按规格明细"); print("=" * 60)
    rows = summarize_by_spec(baseline, candidate, matched)
    headers = ["规格", "基准", "待测", "匹配", "待测错/多", "基准漏", "待测正确率", "基准覆盖率"]
    table = [[r[h] for h in headers] for r in rows]
    table.append(["合计", B, C, M, len(um_c), len(um_b), pct(M, C), pct(M, B)])
    print_table(headers, table)

    print()
    print("=" * 60); print("差异明细 (按 规格+长度 合并)"); print("=" * 60)

    print("\n-- 待测多计/错长 --")
    if um_c:
        print_table(["规格", "长度_mm", "数量"],
                    [[s, fmt_len(l), n] for (s, l), n in group_by_spec_len(um_c)])
    else:
        print("无")

    print("\n-- 基准漏计 --")
    if um_b:
        print_table(["规格", "长度_mm", "数量"],
                    [[s, fmt_len(l), n] for (s, l), n in group_by_spec_len(um_b)])
    else:
        print("无")

    if args.csv:
        export_csv(args.csv, matched, um_b, um_c)
        print(f"\n逐根差异已导出: {args.csv}")

    return 0 if not um_b and not um_c else 1


if __name__ == "__main__":
    sys.exit(main())