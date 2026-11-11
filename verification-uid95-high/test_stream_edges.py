"""Protocol and stream checks derived from the exact upload-limit contract."""
import io

import pytest

from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.formparser import MultiPartParser, _FormDataParser

BOUNDARY = b'upload-edge-boundary'


def message(payload, *, field=False):
    name = b'Content-Disposition: form-data; name="item"'
    if not field:
        name += b'; filename="item.bin"'
    return b'--' + BOUNDARY + b'\r\n' + name + b'\r\n\r\n' + payload + b'\r\n--' + BOUNDARY + b'--\r\n'


class Reads(io.BytesIO):
    def __init__(self, raw, chunk):
        super().__init__(raw)
        self.chunk = chunk

    def read(self, size=-1):
        return super().read(self.chunk if size < 0 else min(size, self.chunk))


@pytest.mark.parametrize('chunk', range(1, 65))
@pytest.mark.parametrize('limit_name', ['max_file_size', 'max_total_file_size'])
def test_exact_payload_at_every_short_read(chunk, limit_name):
    payload = b'abcd\r\nxyz\r'
    raw = message(payload)
    _, _, files = _FormDataParser(**{limit_name: len(payload)}).parse(
        Reads(raw, chunk), 'multipart/form-data', len(raw), {'boundary': BOUNDARY.decode()})
    assert files['item'].read() == payload
    assert not files['item'].stream.closed
    files['item'].close()


@pytest.mark.parametrize('chunk', [1, 2, 3, 7, 13, 64])
def test_text_field_only_does_not_consume_zero_file_budget(chunk):
    payload = b'notes' * 80
    raw = message(payload, field=True)
    _, form, files = _FormDataParser(max_file_size=0, max_total_file_size=0).parse(
        Reads(raw, chunk), 'multipart/form-data', len(raw), {'boundary': BOUNDARY.decode()})
    assert form['item'] == payload.decode()
    assert not files


@pytest.mark.parametrize('limit_name', ['max_file_size', 'max_total_file_size'])
def test_reuse_after_failure_resets_per_request_budget(limit_name):
    parser = MultiPartParser(**{limit_name: 4}, buffer_size=7)
    with pytest.raises(RequestEntityTooLarge):
        parser.parse(io.BytesIO(message(b'abcde')), BOUNDARY, None)
    _, files = parser.parse(io.BytesIO(message(b'abcd')), BOUNDARY, None)
    assert files['item'].read() == b'abcd'
    files['item'].close()


def test_factory_failure_closes_previously_completed_file():
    streams = []

    def factory(**kwargs):
        if streams:
            raise OSError('cannot create second upload stream')
        stream = io.BytesIO()
        streams.append(stream)
        return stream

    first = message(b'first').rsplit(b'--' + BOUNDARY + b'--\r\n', 1)[0]
    raw = first + message(b'second')
    parser = MultiPartParser(stream_factory=factory, max_file_size=20, max_total_file_size=40)
    with pytest.raises(OSError, match='cannot create second upload stream'):
        parser.parse(io.BytesIO(raw), BOUNDARY, None)
    assert len(streams) == 1 and streams[0].closed
