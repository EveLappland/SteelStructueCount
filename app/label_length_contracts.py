"""Minimal model claims; member identities and arithmetic stay local."""
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP, localcontext
from typing import Literal
from pydantic import Field
from .domain_base import Contract


class DimensionTerm(Contract):
    text_id: str = Field(min_length=1, max_length=200)
    factor: float = Field(ge=-1000, le=1000)


class LabelLength(Contract):
    label_id: str = Field(min_length=1, max_length=240)
    kind: Literal['STRAIGHT', 'BRACE', 'UNKNOWN']
    dimensions: list[list[DimensionTerm]] = Field(max_length=2)
    endpoints: list[str] = Field(max_length=2)
    basis: str = Field(max_length=2400)
    issue: str = Field(max_length=2000)


class LengthReply(Contract):
    members: list[LabelLength]


class LengthDecision(Contract):
    label_id: str
    choice: Literal['MASTER', 'EXPERT', 'REVISE', 'UNKNOWN']
    reason: str = Field(min_length=1, max_length=2400)
    revised: LabelLength | None


class DecisionReply(Contract):
    decisions: list[LengthDecision]


def check_ids(payload, targets, role):
    rows = payload['decisions' if role == 'arbiter' else 'members']
    counts = Counter(r['label_id'] for r in rows)
    if set(counts) != set(targets) or any(n != 1 for n in counts.values()):
        raise ValueError(f'输出label_id须与本次目标一一对应；缺失={sorted(set(targets)-counts.keys())}；'
                         f'目录外={sorted(counts.keys()-set(targets))}；重复={[k for k,v in counts.items() if v>1]}')
    for row in rows:
        if role == 'arbiter' and row['revised'] and row['revised']['label_id'] != row['label_id']:
            raise ValueError('仲裁修订不能改变label_id')


def evaluate(answer, facts):
    """Ground all operands in native numeric text, then compute locally.

    Valid arithmetic/source IDs do NOT establish correct graphical association.
    """
    answer = LabelLength.model_validate(answer).model_dump()
    registry = {f['id']: f for f in facts}
    checks, values, formulas = [], [], []
    kind = answer['kind']
    expected = {'UNKNOWN': 0, 'STRAIGHT': 1, 'BRACE': 2}[kind]
    if len(answer['dimensions']) != expected:
        checks.append('STRAIGHT需一组尺寸表达式；BRACE需水平、竖向两组；UNKNOWN为空数组。')
    if kind != 'UNKNOWN' and (len(answer['endpoints']) != 2 or
            len(set(answer['endpoints'])) != 2 or not all(s.strip() for s in answer['endpoints']) or not answer['basis'].strip()):
        checks.append('确定长度需要两个不同实际端点及尺寸对应依据。')
    for terms in answer['dimensions']:
        total, formula = Decimal(0), []
        if not terms or len(terms) > 16:
            checks.append('每组尺寸需要1至16项原始文字引用。')
        for term in terms:
            fact = registry.get(term['text_id'])
            numbers = fact.get('values_mm', []) if fact else []
            if len(numbers) != 1 or term['factor'] == 0:
                checks.append(f"无效尺寸引用或系数：{term['text_id']}")
                continue
            value, factor = Decimal(str(numbers[0])), Decimal(str(term['factor']))
            total += value*factor
            formula.append(f"{value}×{factor}[{term['text_id']}]")
        if total <= 0:
            checks.append('构件长度或斜撑直角边必须为正数，检查标高相减顺序。')
        values.append(total)
        formulas.append(' + '.join(formula))
    length = None
    if not checks and kind != 'UNKNOWN':
        with localcontext() as context:
            context.prec = 28
            value = values[0] if kind == 'STRAIGHT' else (values[0]**2+values[1]**2).sqrt()
            length = float(value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP))
    return {'answer': answer, 'length_mm': length,
            'dimension_values_mm': [float(v) for v in values], 'formulas': formulas,
            'checks': list(dict.fromkeys(checks)), 'usable': length is not None,
            'verification': 'native numeric operands and arithmetic only; endpoint/dimension association is a model claim'}


def different(a, b):
    return a['usable'] and b['usable'] and (a['answer']['kind'] != b['answer']['kind'] or
        len(a['dimension_values_mm']) != len(b['dimension_values_mm']) or
        any(abs(x-y) > .01 for x,y in zip(a['dimension_values_mm'], b['dimension_values_mm'])))


def unknown(label_id, reason):
    return {'label_id': label_id, 'kind': 'UNKNOWN', 'dimensions': [], 'endpoints': [],
            'basis': '', 'issue': reason}
