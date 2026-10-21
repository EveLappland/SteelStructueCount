"""Independent, per-view stage-one counting reference. Never infer lengths."""
from collections import Counter
from pydantic import Field, StrictInt, model_validator
from .domain_base import Contract
from .profiles import section


def count_spec(value):
    profile,multiplier=section(value)
    return f'{multiplier}LF-{profile}' if multiplier!=1 else profile


class SpecCount(Contract):
    spec: str = Field(min_length=1,max_length=120)
    member_cnt: StrictInt = Field(ge=0)


class QuantityReference(Contract):
    total_count: StrictInt | None = Field(ge=0)
    spec_counts: list[SpecCount]
    issue: str | None = Field(max_length=4000)

    @model_validator(mode='after')
    def consistent(self):
        names=[count_spec(item.spec) for item in self.spec_counts]
        if len(names)!=len(set(names)):raise ValueError('每种规格的数量标准只能出现一次')
        if self.total_count is not None and sum(item.member_cnt for item in self.spec_counts)!=self.total_count:
            raise ValueError('各规格 member_cnt 之和必须等于 total_count；不确定时 total_count=null 并填写 issue')
        if self.total_count is None and not self.issue:raise ValueError('数量未知时必须说明原因，不能当作零根')
        return self


def assess_quantity(rows,reference):
    actual=Counter()
    for row in rows:actual[count_spec(row['spec'])]+=row.get('member_cnt',1)
    if not reference:
        return {'reference_available':False,'expected_total':None,'actual_total':sum(actual.values()),
                'matches':None,'differences':[],'feedback':[]}
    ref=QuantityReference.model_validate(reference)
    expected=Counter({count_spec(item.spec):item.member_cnt for item in ref.spec_counts})
    differences=[{'spec':spec,'label_count':expected[spec],'member_cnt':actual[spec]}
                 for spec in sorted(expected.keys()|actual.keys()) if expected[spec]!=actual[spec]]
    available=ref.total_count is not None
    matches=(ref.total_count==sum(actual.values()) and not differences) if available else None
    feedback=[]
    if available and not matches:
        feedback=[{'check':'stage_one_quantity_reference','expected_total':ref.total_count,
                   'actual_total':sum(actual.values()),'differences':differences,
                   'message':'第一阶段独立数量标准与本轮 member_cnt 合计不一致。请结合原图核查遗漏、误合并或重复观察；不能为了凑数删行、复制长度或篡改第一阶段标准。有证据冲突须保留 issue。'}]
    return {'reference_available':available,'expected_total':ref.total_count,'actual_total':sum(actual.values()),
            'matches':matches,'differences':differences if available else [],'feedback':feedback}
