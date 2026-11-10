import io
from os.path import dirname
from os.path import join

import pytest

from werkzeug import formparser
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.test import Client
from werkzeug.wrappers import Request
from werkzeug.wrappers import Response


@Request.application
def form_data_consumer(request):
    result_object = request.args["object"]
    if result_object == "text":
        return Response(repr(request.form["text"]))
    f = request.files[result_object]
    return Response(
        b"\n".join(
            (
                repr(f.filename).encode("ascii"),
                repr(f.name).encode("ascii"),
                repr(f.content_type).encode("ascii"),
                f.stream.read(),
            )
        )
    )


def get_contents(filename):
    with open(filename, "rb") as f:
        return f.read()


class TestFormParser:
    def test_limiting(self):
        data = b"foo=Hello+World&bar=baz"
        req = Request.from_values(
            data=data,
            content_type="application/x-www-form-urlencoded",
            method="POST",
        )
        req.max_content_length = 400
        assert req.form["foo"] == "Hello World"

        input_stream = io.BytesIO(b"foo=123456")
        req = Request.from_values(
            input_stream=input_stream,
            content_type="application/x-www-form-urlencoded",
            method="POST",
        )
        req.max_content_length = 4
        pytest.raises(RequestEntityTooLarge, lambda: req.form["foo"])
        # content-length was set, so request could exit early without reading anything
        assert input_stream.read() == b"foo=123456"

        data = (
            b"--foo\r\nContent-Disposition: form-field; name=foo\r\n\r\n"
            b"Hello World\r\n"
            b"--foo\r\nContent-Disposition: form-field; name=bar\r\n\r\n"
            b"bar=baz\r\n--foo--"
        )

        with Request.from_values(
            data=data,
            content_type="multipart/form-data; boundary=foo",
            method="POST",
        ) as req:
            req.max_content_length = 400
            assert req.form["foo"] == "Hello World"

        with Request.from_values(
            data=data,
            content_type="multipart/form-data; boundary=foo",
            method="POST",
        ) as req:
            req.max_form_memory_size = 7
            pytest.raises(RequestEntityTooLarge, lambda: req.form["foo"])

        with Request.from_values(
            data=data,
            content_type="multipart/form-data; boundary=foo",
            method="POST",
        ) as req:
            req.max_form_memory_size = 400
            assert req.form["foo"] == "Hello World"

        with Request.from_values(
            data=data,
            content_type="multipart/form-data; boundary=foo",
            method="POST",
        ) as req:
            req.max_form_parts = 1
            pytest.raises(RequestEntityTooLarge, lambda: req.form["foo"])

    def test_urlencoded_no_max(self) -> None:
        r = Request.from_values(method="POST", data={"a": 1, "b": 2})
        r.max_form_parts = 1

        assert r.form["a"] == "1"
        assert r.form["b"] == "2"

    def test_urlencoded_silent_decode(self) -> None:
        r = Request.from_values(
            data=b"\x80",
            content_type="application/x-www-form-urlencoded",
            method="POST",
        )
        assert not r.form

    def test_missing_multipart_boundary(self):
        data = (
            b"--foo\r\nContent-Disposition: form-field; name=foo\r\n\r\n"
            b"Hello World\r\n"
            b"--foo\r\nContent-Disposition: form-field; name=bar\r\n\r\n"
            b"bar=baz\r\n--foo--"
        )
        with Request.from_values(
            data=data,
            content_type="multipart/form-data",
            method="POST",
        ) as req:
            assert req.form == {}

    def test_chunk_split_on_line_break_before_epilogue(self):
        data = b"".join(
            (
                # exactly 64 bytes of header
                b"--thirteenbytes\r\n",
                b"Content-Disposition: form-data; name=tx3065\r\n\r\n",
                # payload that fills 65535 bytes together with the header
                b"\n".join([b"\r" * 31] * 2045 + [b"y" * 31]),
                # This newline is split by the first chunk
                b"\r\n",
                # extra payload that also has the final newline split exactly
                # at the chunk size.
                b"\n".join([b"\r" * 31] * 2047 + [b"x" * 30]),
                b"\r\n--thirteenbytes--",
            )
        )

        with Request.from_values(
            data=data,
            content_type="multipart/form-data; boundary=thirteenbytes",
            method="POST",
        ) as req:
            assert len(req.form["tx3065"]) == (131072 - 64 - 1)
            assert req.form["tx3065"][-1] == "x"
            assert req.form["tx3065"][65470:65473] == "y\r\n"

    def test_parse_form_data_put_without_content(self) -> None:
        """PUT without content parses to empty form data."""
        with Request.from_values(method="PUT") as request:
            assert not request.form
            assert not request.files
            assert not request.data

    def test_parse_form_data_get_without_content(self) -> None:
        """GET parses to empty form data."""
        with Request.from_values() as request:
            assert not request.form
            assert not request.files
            assert not request.data

    def test_parse_form_post_data_trailing_CR(self):
        for k in [1, 2]:
            sample = b"\0" * 65535 + b"\x0d" * k
            with Request.from_values(
                data={"foo": (io.BytesIO(sample), "test.txt")}, method="POST"
            ) as req:
                assert req.files["foo"].read() == sample

    def test_parse_bad_content_type(self) -> None:
        with Request.from_values(content_type="bad-mime-type") as request:
            assert not request.form
            assert not request.files
            assert not request.data


class TestMultiPart:
    def test_basic(self):
        resources = join(dirname(__file__), "multipart")
        client = Client(form_data_consumer)

        repository = [
            (
                "firefox3-2png1txt",
                "---------------------------186454651713519341951581030105",
                [
                    ("anchor.png", "file1", "image/png", "file1.png"),
                    ("application_edit.png", "file2", "image/png", "file2.png"),
                ],
                "example text",
            ),
            (
                "firefox3-2pnglongtext",
                "---------------------------14904044739787191031754711748",
                [
                    ("accept.png", "file1", "image/png", "file1.png"),
                    ("add.png", "file2", "image/png", "file2.png"),
                ],
                "--long text\r\n--with boundary\r\n--lookalikes--",
            ),
            (
                "opera8-2png1txt",
                "----------zEO9jQKmLc2Cq88c23Dx19",
                [
                    ("arrow_branch.png", "file1", "image/png", "file1.png"),
                    ("award_star_bronze_1.png", "file2", "image/png", "file2.png"),
                ],
                "blafasel öäü",
            ),
            (
                "webkit3-2png1txt",
                "----WebKitFormBoundaryjdSFhcARk8fyGNy6",
                [
                    ("gtk-apply.png", "file1", "image/png", "file1.png"),
                    ("gtk-no.png", "file2", "image/png", "file2.png"),
                ],
                "this is another text with ümläüts",
            ),
            (
                "ie6-2png1txt",
                "---------------------------7d91b03a20128",
                [
                    ("file1.png", "file1", "image/x-png", "file1.png"),
                    ("file2.png", "file2", "image/x-png", "file2.png"),
                ],
                "ie6 sucks :-/",
            ),
        ]

        for name, boundary, files, text in repository:
            folder = join(resources, name)
            data = get_contents(join(folder, "request.http"))
            for filename, field, content_type, fsname in files:
                with client.post(
                    f"/?object={field}",
                    data=data,
                    content_type=f'multipart/form-data; boundary="{boundary}"',
                    content_length=len(data),
                ) as response:
                    lines = response.data.split(b"\n", 3)
                    assert lines[0] == repr(filename).encode("ascii")
                    assert lines[1] == repr(field).encode("ascii")
                    assert lines[2] == repr(content_type).encode("ascii")
                    assert lines[3] == get_contents(join(folder, fsname))

            with client.post(
                "/?object=text",
                data=data,
                content_type=f'multipart/form-data; boundary="{boundary}"',
                content_length=len(data),
            ) as response:
                assert response.text == repr(text)

    def test_ie7_unc_path(self):
        client = Client(form_data_consumer)
        data_file = join(dirname(__file__), "multipart", "ie7_full_path_request.http")
        data = get_contents(data_file)
        boundary = "---------------------------7da36d1b4a0164"
        with client.post(
            "/?object=cb_file_upload_multiple",
            data=data,
            content_type=f'multipart/form-data; boundary="{boundary}"',
            content_length=len(data),
        ) as response:
            lines = response.data.split(b"\n", 3)
            assert lines[0] == b"'Sellersburg Town Council Meeting 02-22-2010doc.doc'"

    def test_premature_end_of_file(self):
        data = (
            b"--foo\r\n"
            b'Content-Disposition: form-data; name="test"; filename="test.txt"\r\n'
            b"Content-Type: text/plain\r\n\r\n"
            b"file contents and no end"
        )
        with Request.from_values(
            data=data,
            content_type="multipart/form-data; boundary=foo",
            method="POST",
        ) as req:
            assert not req.files
            assert not req.form

    def test_file_no_content_type(self):
        data = (
            b"--foo\r\n"
            b'Content-Disposition: form-data; name="test"; filename="test.txt"\r\n\r\n'
            b"file contents\r\n--foo--"
        )
        with Request.from_values(
            data=data,
            content_type="multipart/form-data; boundary=foo",
            method="POST",
        ) as data:
            assert data.files["test"].filename == "test.txt"
            assert data.files["test"].read() == b"file contents"

    def test_extra_newline(self):
        # this test looks innocent but it was actually timing out in
        # the Werkzeug 0.5 release version (#394)
        data = (
            b"\r\n\r\n--foo\r\n"
            b'Content-Disposition: form-data; name="foo"\r\n\r\n'
            b"a string\r\n"
            b"--foo--"
        )
        with Request.from_values(
            data=data,
            content_type="multipart/form-data; boundary=foo",
            method="POST",
        ) as req:
            assert not req.files
            assert req.form["foo"] == "a string"

    def test_headers(self):
        data = (
            b"--foo\r\n"
            b'Content-Disposition: form-data; name="foo"; filename="foo.txt"\r\n'
            b"X-Custom-Header: blah\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
            b"file contents, just the contents\r\n"
            b"--foo--"
        )
        with Request.from_values(
            data=data,
            content_type="multipart/form-data; boundary=foo",
            method="POST",
        ) as req:
            foo = req.files["foo"]
            assert foo.mimetype == "text/plain"
            assert foo.mimetype_params == {"charset": "utf-8"}
            assert foo.headers["Content-Type"] == foo.content_type
            assert foo.content_type == "text/plain; charset=utf-8"
            assert foo.headers["X-Custom-Header"] == "blah"

    @pytest.mark.parametrize("ending", [b"\n", b"\r", b"\r\n"])
    def test_nonstandard_line_endings(self, ending: bytes):
        data = ending.join(
            (
                b"--foo",
                b"Content-Disposition: form-data; name=foo",
                b"",
                b"this is just bar",
                b"--foo",
                b"Content-Disposition: form-data; name=bar",
                b"",
                b"blafasel",
                b"--foo--",
            )
        )
        with Request.from_values(
            data=data,
            content_type="multipart/form-data; boundary=foo",
            method="POST",
        ) as req:
            assert req.form["foo"] == "this is just bar"
            assert req.form["bar"] == "blafasel"

    def test_failures(self):
        def parse_multipart(stream, boundary, content_length):
            parser = formparser.MultiPartParser(content_length)
            return parser.parse(stream, boundary, content_length)

        data = b"--foo\r\n\r\nHello World\r\n--foo--"
        pytest.raises(ValueError, parse_multipart, io.BytesIO(data), b"foo", len(data))

        data = (
            b"--foo\r\nContent-Disposition: form-field; name=foo\r\n\r\nHello World\r\n"
        )
        pytest.raises(ValueError, parse_multipart, io.BytesIO(data), b"foo", len(data))

    def test_empty_multipart(self) -> None:
        with Request.from_values(
            method="POST",
            data=b"--boundary--",
            content_type="multipart/form-data; boundary=boundary",
        ) as request:
            assert not request.form
            assert not request.files
            assert not request.data


class TestMultiPartParser:
    def test_file_rfc2231_filename_continuations(self):
        data = (
            b"--foo\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"Content-Disposition: form-data; name=rfc2231;\r\n"
            b"	filename*0*=ascii''a%20b%20;\r\n"
            b"	filename*1*=c%20d%20;\r\n"
            b'	filename*2="e f.txt"\r\n\r\n'
            b"file contents\r\n--foo--"
        )
        with Request.from_values(
            data=data,
            content_type="multipart/form-data; boundary=foo",
            method="POST",
        ) as request:
            assert request.files["rfc2231"].filename == "a b c d e f.txt"
            assert request.files["rfc2231"].read() == b"file contents"


def test_multipart_max_form_memory_size() -> None:
    """max_form_memory_size is tracked across multiple data events."""
    data = b"--bound\r\nContent-Disposition: form-field; name=a\r\n\r\n"
    data += b"a" * 15 + b"\r\n--bound--"
    # The buffer size is less than the max size, so multiple data events will be
    # returned. The field size is greater than the max.
    parser = formparser.MultiPartParser(max_form_memory_size=10, buffer_size=5)

    with pytest.raises(RequestEntityTooLarge):
        parser.parse(io.BytesIO(data), b"bound", None)


class TrackingStream:
    """File-like stream that records writes and whether it was closed.

    ``close`` does not make the buffered data unreadable, so the buffer can be
    inspected after the parser closes the stream on failure.
    """

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.closed = False

    def write(self, data: bytes) -> int:
        self.buffer.extend(data)
        return len(data)

    def seek(self, pos: int) -> None:
        pass

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            return bytes(self.buffer)

        return bytes(self.buffer[:size])

    def close(self) -> None:
        self.closed = True


def tracking_stream_factory():
    streams: list[TrackingStream] = []

    def factory(total_content_length, content_type, filename, content_length=None):
        stream = TrackingStream()
        streams.append(stream)
        return stream

    return factory, streams


def file_part(name: str, filename: str, data: bytes, boundary: bytes) -> bytes:
    return (
        b"--" + boundary + b"\r\n"
        b'Content-Disposition: form-data; name="'
        + name.encode()
        + b'"; filename="'
        + filename.encode()
        + b'"\r\n'
        b"Content-Type: application/octet-stream\r\n\r\n" + data + b"\r\n"
    )


def field_part(name: str, data: bytes, boundary: bytes) -> bytes:
    return (
        b"--" + boundary + b"\r\n"
        b'Content-Disposition: form-data; name="'
        + name.encode()
        + b'"\r\n\r\n'
        + data
        + b"\r\n"
    )


def closing_boundary(boundary: bytes) -> bytes:
    return b"--" + boundary + b"--"


class TestFileSizeLimits:
    def test_max_file_size_exact_limit_allowed(self) -> None:
        boundary = b"bound"
        body = file_part("f", "a.txt", b"abc", boundary) + closing_boundary(boundary)
        # A small buffer forces multiple Data events while staying below a
        # chunk size affected by an unrelated boundary-splitting edge case.
        with formparser.MultiPartParser(max_file_size=3, buffer_size=3) as parser:
            _, files = parser.parse(io.BytesIO(body), boundary, None)
            assert files["f"].read() == b"abc"
            files["f"].close()

    def test_max_file_size_one_byte_over(self) -> None:
        boundary = b"bound"
        body = file_part("f", "a.txt", b"abcd", boundary) + closing_boundary(boundary)

        with pytest.raises(RequestEntityTooLarge):
            with formparser.MultiPartParser(max_file_size=3, buffer_size=2) as parser:
                parser.parse(io.BytesIO(body), boundary, None)

    def test_max_file_size_zero_allows_empty_file(self) -> None:
        boundary = b"bound"
        body = file_part("f", "a.txt", b"", boundary) + closing_boundary(boundary)

        with formparser.MultiPartParser(max_file_size=0) as parser:
            _, files = parser.parse(io.BytesIO(body), boundary, None)
            assert files["f"].read() == b""
            files["f"].close()

    def test_max_file_size_zero_rejects_nonempty_file(self) -> None:
        boundary = b"bound"
        body = file_part("f", "a.txt", b"x", boundary) + closing_boundary(boundary)

        with pytest.raises(RequestEntityTooLarge):
            with formparser.MultiPartParser(max_file_size=0) as parser:
                parser.parse(io.BytesIO(body), boundary, None)

    def test_negative_limit_is_config_error(self) -> None:
        with pytest.raises(ValueError, match="max_file_size"):
            formparser.MultiPartParser(max_file_size=-1)

        with pytest.raises(ValueError, match="max_total_file_size"):
            formparser.MultiPartParser(max_total_file_size=-1)

        with pytest.raises(ValueError, match="max_file_size"):
            formparser._FormDataParser(max_file_size=-1)

        with pytest.raises(ValueError, match="max_total_file_size"):
            formparser._FormDataParser(max_total_file_size=-1)

    def test_total_limit_accumulates_all_files(self) -> None:
        boundary = b"bound"
        body = (
            file_part("f", "a.txt", b"ab", boundary)
            # Same field name as the first file, it must still be counted.
            + file_part("f", "b.txt", b"cde", boundary)
            + closing_boundary(boundary)
        )

        # The combined size is 5, so 5 is allowed and 4 is not.
        with formparser.MultiPartParser(max_total_file_size=5) as parser:
            _, files = parser.parse(io.BytesIO(body), boundary, None)
            for value in files.getlist("f"):
                value.close()

        with pytest.raises(RequestEntityTooLarge):
            with formparser.MultiPartParser(max_total_file_size=4) as parser:
                parser.parse(io.BytesIO(body), boundary, None)

    def test_total_limit_exact_bytes_allowed(self) -> None:
        boundary = b"bound"
        body = (
            file_part("f", "a.txt", b"ab", boundary)
            + file_part("g", "b.txt", b"c", boundary)
            + closing_boundary(boundary)
        )

        with formparser.MultiPartParser(max_total_file_size=3) as parser:
            _, files = parser.parse(io.BytesIO(body), boundary, None)
            assert len(files.getlist("f")) + len(files.getlist("g")) == 2
            for value in files.values():
                value.close()

    def test_text_fields_do_not_count_toward_limits(self) -> None:
        boundary = b"bound"
        body = (
            field_part("text", b"x" * 100, boundary)
            + file_part("f", "a.txt", b"ab", boundary)
            + closing_boundary(boundary)
        )

        with formparser.MultiPartParser(
            max_file_size=2, max_total_file_size=2
        ) as parser:
            form, files = parser.parse(io.BytesIO(body), boundary, None)
            assert form["text"] == "x" * 100
            assert files["f"].read() == b"ab"
            files["f"].close()

    def test_client_content_length_is_not_trusted(self) -> None:
        boundary = b"bound"
        # The part claims to be 1 byte but actually contains 4, and the request
        # content length passed to the parser is also a lie.
        body = (
            b"--bound\r\n"
            b'Content-Disposition: form-data; name="f"; filename="a.txt"\r\n'
            b"Content-Length: 1\r\n\r\n"
            b"abcd\r\n--bound--"
        )

        with pytest.raises(RequestEntityTooLarge):
            with formparser.MultiPartParser(max_file_size=3) as parser:
                parser.parse(io.BytesIO(body), boundary, content_length=1)

    def test_boundary_split_at_every_byte(self) -> None:
        boundary = b"bound"
        body = file_part("f", "a.txt", b"abcdef", boundary) + closing_boundary(boundary)

        # One-byte chunks put the boundary and line breaks in every possible
        # position relative to a chunk edge.
        with pytest.raises(RequestEntityTooLarge):
            with formparser.MultiPartParser(max_file_size=3, buffer_size=1) as parser:
                parser.parse(io.BytesIO(body), boundary, None)

        # Exactly at the limit with small chunks still succeeds.
        body = file_part("f", "a.txt", b"abc", boundary) + closing_boundary(boundary)
        with formparser.MultiPartParser(max_file_size=3, buffer_size=3) as parser:
            _, files = parser.parse(io.BytesIO(body), boundary, None)
            assert files["f"].read() == b"abc"
            files["f"].close()

    def test_truncated_upload_over_limit(self) -> None:
        boundary = b"bound"
        # The request ends in the middle of the file, without a boundary.
        body = file_part("f", "a.txt", b"abcdef", boundary)

        with pytest.raises(RequestEntityTooLarge):
            with formparser.MultiPartParser(max_file_size=3, buffer_size=2) as parser:
                parser.parse(io.BytesIO(body), boundary, None)

    def test_oversized_chunk_is_never_written(self) -> None:
        boundary = b"bound"
        body = file_part("f", "a.txt", b"abcdef", boundary) + closing_boundary(boundary)
        factory, streams = tracking_stream_factory()

        # With a big buffer the entire 6-byte body arrives in one event; none of
        # it may be written before the limit check fails.
        parser = formparser.MultiPartParser(max_file_size=3, stream_factory=factory)
        with pytest.raises(RequestEntityTooLarge):
            with parser:
                parser.parse(io.BytesIO(body), boundary, None)
        assert bytes(streams[0].buffer) == b""

        # With small chunks, the bytes up to the limit are written, but the
        # chunk that would cross the limit is not.
        parser = formparser.MultiPartParser(
            max_file_size=3, buffer_size=2, stream_factory=factory
        )
        with pytest.raises(RequestEntityTooLarge):
            with parser:
                parser.parse(io.BytesIO(body), boundary, None)
        assert bytes(streams[1].buffer) == b"abc"

    def test_failure_closes_all_created_streams(self) -> None:
        boundary = b"bound"
        body = (
            file_part("first", "a.txt", b"ab", boundary)
            + file_part("second", "b.txt", b"cdef", boundary)
            + closing_boundary(boundary)
        )
        factory, streams = tracking_stream_factory()
        parser = formparser.MultiPartParser(max_file_size=3, stream_factory=factory)

        with pytest.raises(RequestEntityTooLarge):
            with parser:
                parser.parse(io.BytesIO(body), boundary, None)

        # The completed first file and the failing second file are both closed.
        assert len(streams) == 2
        assert all(stream.closed for stream in streams)

    def test_successful_files_remain_readable(self) -> None:
        boundary = b"bound"
        body = (
            file_part("first", "a.txt", b"ab", boundary)
            + file_part("second", "b.txt", b"c", boundary)
            + closing_boundary(boundary)
        )
        factory, streams = tracking_stream_factory()
        parser = formparser.MultiPartParser(
            max_total_file_size=3, stream_factory=factory
        )

        with parser:
            _, files = parser.parse(io.BytesIO(body), boundary, None)
            assert files["first"].read() == b"ab"
            assert files["second"].read() == b"c"

        assert not any(stream.closed for stream in streams)

    def test_default_none_does_not_limit(self) -> None:
        boundary = b"bound"
        body = file_part("f", "a.txt", b"x" * 10_000, boundary) + closing_boundary(
            boundary
        )

        with formparser.MultiPartParser() as parser:
            _, files = parser.parse(io.BytesIO(body), boundary, None)
            assert len(files["f"].read()) == 10_000
            files["f"].close()

    def test_request_limits_are_passed_through(self) -> None:
        boundary = b"bound"
        body = file_part("f", "a.txt", b"abcd", boundary) + closing_boundary(boundary)

        with Request.from_values(
            data=body,
            content_type="multipart/form-data; boundary=bound",
            method="POST",
        ) as request:
            request.max_file_size = 3
            with pytest.raises(RequestEntityTooLarge):
                request.files["f"]

        with Request.from_values(
            data=body,
            content_type="multipart/form-data; boundary=bound",
            method="POST",
        ) as request:
            request.max_file_size = 4
            assert request.files["f"].read() == b"abcd"

    def test_request_total_limit(self) -> None:
        boundary = b"bound"
        body = (
            file_part("f", "a.txt", b"ab", boundary)
            + file_part("f", "b.txt", b"cd", boundary)
            + closing_boundary(boundary)
        )

        with Request.from_values(
            data=body,
            content_type="multipart/form-data; boundary=bound",
            method="POST",
        ) as request:
            request.max_total_file_size = 3
            with pytest.raises(RequestEntityTooLarge):
                request.files.getlist("f")

        with Request.from_values(
            data=body,
            content_type="multipart/form-data; boundary=bound",
            method="POST",
        ) as request:
            request.max_total_file_size = 4
            assert [f.read() for f in request.files.getlist("f")] == [
                b"ab",
                b"cd",
            ]

    def test_request_zero_limit_and_negative_config(self) -> None:
        boundary = b"bound"
        body = file_part("f", "a.txt", b"", boundary) + closing_boundary(boundary)

        with Request.from_values(
            data=body,
            content_type="multipart/form-data; boundary=bound",
            method="POST",
        ) as request:
            request.max_file_size = 0
            assert request.files["f"].read() == b""

        with Request.from_values(
            data=body,
            content_type="multipart/form-data; boundary=bound",
            method="POST",
        ) as request:
            request.max_total_file_size = -1
            with pytest.raises(ValueError, match="max_total_file_size"):
                _ = request.files
