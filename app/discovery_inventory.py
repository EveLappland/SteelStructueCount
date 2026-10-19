"""Extract native steel specification labels for local coverage checks."""
import re

PROFILE = re.compile(
    r'(?<![\w])(?:(?P<assembly>\d+(?:LF|F|L)\s*[- ]\s*)?'
    r'(?P<angle>L\d+(?:\.\d+)?\s*[X*×]\s*\d+(?:\.\d+)?(?:\s*[X*×]\s*\d+(?:\.\d+)?)?)'
    r'|(?P<rolled>HE[ABM]\d+|IPET?\d+|IPN\d+|UP[NE]\d+)'
    r'|(?P<hollow>(?:RHS|SHS|CHS)\s*\d+(?:\.\d+)?(?:\s*[X*×]\s*\d+(?:\.\d+)?){1,2}))'
    r'(?![\w])',re.I)


def labels(text):
    for match in PROFILE.finditer(text):
        profile=match['angle'] or match['rolled'] or match['hollow']
        profile=re.sub(r'\s+','',profile.upper()).replace('X','*').replace('×','*')
        count=re.match(r'\d+',match['assembly'] or '')
        yield profile,int(count[0]) if count else 1
