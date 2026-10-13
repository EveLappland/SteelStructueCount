"""Structured, redacted task trace; no API settings or headers are persisted."""
import json
import re
import threading
from pathlib import Path

from .store import now


class AuditLog:
    _lock=threading.Lock()

    def __init__(self,directory,stage=None,secrets=()):
        self.directory=Path(directory)
        self.path=self.directory/'logs'/'events.jsonl'
        self.stage=stage
        self.secrets=tuple(s for s in secrets if s)

    def scrub(self,value):
        if isinstance(value,dict):
            return {key:('[REDACTED]' if re.search(r'api[_-]?key|authorization|password|secret',key,re.I)
                         else self.scrub(item)) for key,item in value.items()}
        if isinstance(value,(list,tuple)):
            return [self.scrub(item) for item in value]
        if isinstance(value,str):
            for secret in self.secrets:
                value=value.replace(secret,'[REDACTED]')
            value=re.sub(r'Bearer\s+[^\s\"\']+','Bearer [REDACTED]',value,flags=re.I)
            value=re.sub(r'([?&](?:api_key|key|token)=)[^&\s]+',r'\1[REDACTED]',value,flags=re.I)
        return value

    def event(self,event,**fields):
        row=self.scrub({'at':now(),'job_id':self.directory.name,'stage':self.stage,'event':event,**fields})
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self._lock, self.path.open('a',encoding='utf-8') as target:
            target.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n')

    def text(self,path,content):
        path=Path(path)
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(self.scrub(content),encoding='utf-8')

    def read(self,after=0,limit=200):
        if not self.path.is_file():
            return {'items':[],'next_cursor':0,'total':0}
        with self._lock,self.path.open(encoding='utf-8') as source:
            lines=source.readlines()
        items=[]
        for line in lines[after:after+limit]:
            try:items.append(json.loads(line))
            except json.JSONDecodeError:continue
        return {'items':items,'next_cursor':min(len(lines),after+limit),'total':len(lines)}
