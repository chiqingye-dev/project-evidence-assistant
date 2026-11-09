"""Application-owned source ranges; advertise and enforce, never extend access."""
from pydantic import ValidationError
from .agent_protocol import ReadArguments
from .source import SourceError
from .tools import SourceTools,MAX_EXCERPT_LINES

def normalize_read_ranges(value):
    if value is None:return {}
    try:
        if type(value) is not dict or len(value)>16:raise ValueError()
        result={}
        for path,bounds in value.items():
            if type(path) is not str or type(bounds) not in (list,tuple) or len(bounds)!=2:raise ValueError()
            start,end=bounds
            checked=ReadArguments(path=path,start_line=start,end_line=end)
            if checked.end_line-checked.start_line>=MAX_EXCERPT_LINES:raise ValueError()
            result[path]=(checked.start_line,checked.end_line)
        return result
    except (ValueError,TypeError,ValidationError):
        raise ValueError('Read ranges must contain bounded permitted relative paths') from None

def read_is_permitted(ranges,arguments):
    path=arguments.get('path')
    if type(path) is not str:return False
    bounds=ranges.get(path)
    start,end=arguments.get('start_line'),arguments.get('end_line')
    return bounds is not None and type(start) is int and type(end) is int and bounds[0]<=start<=end<=bounds[1]

def read_ranges_schema(ranges):
    return {'oneOf':[{'type':'object','properties':{
        'path':{'type':'string','const':path},
        'start_line':{'type':'integer','minimum':start,'maximum':end},
        'end_line':{'type':'integer','minimum':start,'maximum':end}},
        'required':['path','start_line','end_line'],'additionalProperties':False}
        for path,(start,end) in ranges.items()]}

class RangedSourceTools(SourceTools):
    """All three tools stay in the same approved files and content line spans."""
    def __init__(self,source,read_ranges):
        super().__init__(source);self.read_ranges=normalize_read_ranges(read_ranges)
        if not self.read_ranges:raise ValueError('Explicit tool ranges cannot be empty')
    def _entries(self):return [e for e in super()._entries() if e['path'] in self.read_ranges]
    def _read(self,entry,deadline):
        bounds=self.read_ranges.get(entry['path'])
        if bounds is None:raise SourceError('denied','Outside configured read range')
        _,lines,digest=super()._read(entry,deadline)
        # Preserve true line numbers, file length and full immutable-file digest.
        # Searches cannot expose context before/after the permitted span.
        masked=[line if bounds[0]<=index<=bounds[1] else '' for index,line in enumerate(lines,1)]
        return '\n'.join(masked),masked,digest
    def read_excerpt(self,path,start_line=1,end_line=80):
        if not read_is_permitted(self.read_ranges,dict(path=path,start_line=start_line,end_line=end_line)):
            return self._base('denied')
        return super().read_excerpt(path,start_line,end_line)
