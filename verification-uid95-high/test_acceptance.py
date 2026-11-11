"""Independent acceptance checks for the exact original user prompt.
Kept outside the model workspace; these never repair or alter its source.
"""
import io
import warnings
import pytest
from werkzeug.formparser import _FormDataParser, MultiPartParser
from werkzeug.wrappers import Request
from werkzeug.exceptions import RequestEntityTooLarge

BOUNDARY=b'uf-boundary-A7'

def body(parts, complete=True):
    output=[]
    for name, filename, data, claimed in parts:
        output += [b'--'+BOUNDARY+b'\r\n', b'Content-Disposition: form-data; name="'+name.encode()+b'"']
        if filename is not None:
            output += [b'; filename="'+filename.encode()+b'"']
        output += [b'\r\n']
        if filename is not None:
            output += [b'Content-Type: application/octet-stream\r\n']
        if claimed is not None:
            output += [b'Content-Length: '+str(claimed).encode()+b'\r\n']
        output += [b'\r\n',data,b'\r\n']
    if complete: output += [b'--'+BOUNDARY+b'--\r\n']
    return b''.join(output)

class ShortReads(io.BytesIO):
    def __init__(self,data,chunk=7):
        super().__init__(data); self.chunk=chunk
    def read(self,size=-1):
        return super().read(self.chunk if size<0 else min(self.chunk,size))

class StreamSpy(io.BytesIO):
    def __init__(self):
        super().__init__(); self.writes=[]; self.close_calls=0
    def write(self,data):
        self.writes.append(bytes(data)); return super().write(data)
    def close(self):
        self.close_calls+=1; return super().close()

class Factory:
    def __init__(self): self.streams=[]
    def __call__(self,**kwargs):
        f=StreamSpy(); self.streams.append(f); return f

def parse(data, factory=None, chunk=7, silent=False, **limits):
    parser=_FormDataParser(stream_factory=factory,silent=silent,**limits)
    return parser.parse(ShortReads(data,chunk),'multipart/form-data',len(data),{'boundary':BOUNDARY.decode()})

def fileset(*payloads): return [('same','f%d.bin'%i,p,None) for i,p in enumerate(payloads)]

@pytest.mark.parametrize('attr',['max_file_size','max_total_file_size'])
def test_request_declares_none_defaults(attr):
    assert hasattr(Request,attr), 'Request API missing: '+attr
    assert getattr(Request,attr) is None

@pytest.mark.parametrize('limit',[None,4,5])
def test_single_exact_or_unlimited(limit):
    f=Factory(); _,_,files=parse(body(fileset(b'abcd')),f,max_file_size=limit)
    assert files['same'].read()==b'abcd'
    assert not f.streams[0].closed
    files['same'].close()

@pytest.mark.parametrize('which',['max_file_size','max_total_file_size'])
def test_zero_only_empty(which):
    f=Factory(); _,_,files=parse(body(fileset(b'',b'')),f,**{which:0})
    assert len(files.getlist('same'))==2
    assert all(not x.closed for x in f.streams)
    for x in files.getlist('same'): x.close()
    f=Factory()
    with pytest.raises(RequestEntityTooLarge): parse(body(fileset(b'x')),f,**{which:0})
    assert all(x.closed for x in f.streams)
    assert sum(len(w) for x in f.streams for w in x.writes)==0

@pytest.mark.parametrize('which',['max_file_size','max_total_file_size'])
@pytest.mark.parametrize('parser_class',[_FormDataParser,MultiPartParser])
def test_negative_parser_configuration(which,parser_class):
    with pytest.raises(ValueError): parser_class(**{which:-1})

@pytest.mark.parametrize('which',['max_file_size','max_total_file_size'])
def test_negative_request_configuration(which):
    req=Request.from_values(method='POST',data=body(fileset(b'x')),content_type='multipart/form-data; boundary='+BOUNDARY.decode())
    with pytest.raises(ValueError):
        setattr(req,which,-1)
        _=req.files

@pytest.mark.parametrize('chunk',[1,2,7,13,64,65536])
@pytest.mark.parametrize('which',['max_file_size','max_total_file_size'])
def test_reject_before_excess_write_at_chunk_boundaries(chunk,which):
    f=Factory()
    with pytest.raises(RequestEntityTooLarge): parse(body(fileset(b'abcdefghijk')),f,chunk=chunk,**{which:10})
    assert all(x.closed for x in f.streams)
    assert sum(len(w) for x in f.streams for w in x.writes)<=10

@pytest.mark.parametrize('total',[7,8])
def test_total_counts_duplicates_and_excludes_text(total):
    data=body([('note',None,b'ordinary-field-contents'*10,None),*fileset(b'abc',b'defg')])
    f=Factory(); _,form,files=parse(data,f,max_file_size=4,max_total_file_size=total)
    assert len(form['note'])>100
    assert [x.read() for x in files.getlist('same')]==[b'abc',b'defg']
    for x in files.getlist('same'): x.close()

@pytest.mark.parametrize('chunk',[1,7,65536])
def test_aggregate_overflow_closes_completed_and_current(chunk):
    f=Factory()
    with pytest.raises(RequestEntityTooLarge): parse(body(fileset(b'abcd',b'efgh')),f,chunk=chunk,max_file_size=8,max_total_file_size=7)
    assert len(f.streams)==2
    assert all(x.closed for x in f.streams)
    assert sum(len(w) for x in f.streams for w in x.writes)<=7

@pytest.mark.parametrize('claim',[0,1,999999999])
def test_part_length_not_trusted(claim):
    f=Factory(); data=body([('a','a.bin',b'abcdef',claim)])
    with pytest.raises(RequestEntityTooLarge): parse(data,f,max_file_size=5)
    assert all(x.closed for x in f.streams)

@pytest.mark.parametrize('silent',[False,True])
def test_malformed_body_cleans_all_open_streams(silent):
    f=Factory(); data=body(fileset(b'first',b'second'),complete=False)
    if silent:
        _,form,files=parse(data,f,silent=True)
        assert not files
    else:
        with pytest.raises(ValueError): parse(data,f,silent=False)
    assert len(f.streams)==2
    assert all(x.closed for x in f.streams)

@pytest.mark.parametrize('use_context',[False,True])
def test_low_level_parser_failure_cleanup(use_context):
    f=Factory(); parser=MultiPartParser(stream_factory=f,max_file_size=2,buffer_size=7)
    with pytest.raises(RequestEntityTooLarge):
        if use_context:
            with parser: parser.parse(io.BytesIO(body(fileset(b'abcd'))),BOUNDARY,None)
        else:
            parser.parse(io.BytesIO(body(fileset(b'abcd'))),BOUNDARY,None)
    assert f.streams and all(x.closed for x in f.streams)

@pytest.mark.parametrize('which',['max_file_size','max_total_file_size'])
def test_request_propagates_limits(which):
    req=Request.from_values(method='POST',data=body(fileset(b'abc')),content_type='multipart/form-data; boundary='+BOUNDARY.decode())
    setattr(req,which,2)
    with pytest.raises(RequestEntityTooLarge): _=req.files

@pytest.mark.parametrize('which',['max_file_size','max_total_file_size'])
def test_wsgi_terminated_stream_ignores_underreported_envelope(which):
    data=body(fileset(b'abcdef'))
    req=Request.from_values(method='POST',data=data,content_type='multipart/form-data; boundary='+BOUNDARY.decode())
    req.environ['CONTENT_LENGTH']='1'
    req.environ['wsgi.input_terminated']=True
    setattr(req,which,5)
    with pytest.raises(RequestEntityTooLarge): _=req.files

@pytest.mark.parametrize('restriction',[{'max_form_parts':1},{'max_form_memory_size':200}])
def test_existing_limits_remain_enforced(restriction):
    data=body([('a',None,b'x'*400,None),*fileset(b'abcd')])
    with pytest.raises(RequestEntityTooLarge): parse(data,max_file_size=9999,max_total_file_size=9999,**restriction)

def test_success_keeps_storage_open_after_parser_context():
    f=Factory()
    with MultiPartParser(stream_factory=f,max_file_size=4,max_total_file_size=8) as parser:
        _,files=parser.parse(io.BytesIO(body(fileset(b'abcd',b'efgh'))),BOUNDARY,None)
    assert [x.read() for x in files.getlist('same')]==[b'abcd',b'efgh']
    assert all(not x.closed for x in f.streams)
    for x in files.getlist('same'): x.close()

def test_limits_reset_per_parse_call():
    parser=MultiPartParser(max_file_size=3,max_total_file_size=3)
    for _ in range(2):
        _,files=parser.parse(io.BytesIO(body(fileset(b'abc'))),BOUNDARY,None)
        assert files['same'].read()==b'abc'; files['same'].close()

def test_input_failure_cleans_completed_and_current_files():
    data=body(fileset(b'first',b'second'*30))
    class BrokenInput(ShortReads):
        def read(self,size=-1):
            if self.tell()>len(data)-80: raise OSError('independent input failure')
            return super().read(size)
    f=Factory(); parser=MultiPartParser(stream_factory=f,buffer_size=7,max_file_size=1000)
    with pytest.raises(OSError,match='independent input failure'):
        parser.parse(BrokenInput(data),BOUNDARY,None)
    assert len(f.streams)==2
    assert all(x.closed for x in f.streams)

def test_destination_write_failure_cleans_all_files():
    f=Factory()
    class BrokenDestination(StreamSpy):
        def write(self,data): raise OSError('independent destination failure')
    def factory(**kwargs):
        stream=StreamSpy() if not f.streams else BrokenDestination()
        f.streams.append(stream);return stream
    parser=MultiPartParser(stream_factory=factory,buffer_size=7,max_total_file_size=1000)
    with pytest.raises(OSError,match='independent destination failure'):
        parser.parse(io.BytesIO(body(fileset(b'first',b'second'))),BOUNDARY,None)
    assert len(f.streams)==2
    assert all(x.closed for x in f.streams)
