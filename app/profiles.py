"""Canonical steel sections and combination multipliers."""
import re

def section(profile):
    label = str(profile).upper().replace('×', '*').replace(' ', '')
    label = re.sub(r'/(?:EPB|EPW|EP|EMH|EMTF|LT)\b', '', label)
    match = re.fullmatch(r'(\d+)(?:LF|F|L)[-_]*(L\d+(?:[X*]\d+(?:[X*]\d+)?)?)', label)
    if match:
        multiplier = int(match[1])
        if not 1 <= multiplier <= 8:
            raise ValueError('组合截面组件数量必须在 1 到 8 之间。')
        return match[2].replace('X', '*'), multiplier
    return label, 1
