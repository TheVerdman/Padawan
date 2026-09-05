import asyncio
import json
import time

import pytest

from padawan.pprl.worker_broker import ProcessWorkerBroker, serve_worker_stream


class CaptureWriter:
    def __init__(self, broken=False):
        self.output = b""
        self.closed = False
        self.broken = broken

    def write(self, data):
        self.output += data

    async def drain(self):
        if self.broken:
            raise ConnectionResetError("disconnected fixture")

    def close(self):
        self.closed = True

    async def wait_closed(self):
        if self.broken:
            raise ConnectionResetError("disconnected fixture")


class UnreachableBroker:
    MAXIMUM_REQUEST_BYTES = ProcessWorkerBroker.MAXIMUM_REQUEST_BYTES

    async def request(self, request):
        pytest.fail("malformed frame reached broker")


@pytest.mark.parametrize("data", [b"", b"bad-json", b'{"credential":"private-fixture"}', b"\xff"])
async def test_malformed_frame_has_only_one_generic_reply(data):
    reader, writer = asyncio.StreamReader(), CaptureWriter()
    reader.feed_data(len(data).to_bytes(4, "big") + data)
    reader.feed_eof()
    await serve_worker_stream(reader, writer, UnreachableBroker())
    assert writer.closed
    assert len(writer.output[4:]) == int.from_bytes(writer.output[:4], "big")
    assert json.loads(writer.output[4:]) == {"error": "worker_request_denied"}


@pytest.mark.parametrize("header", [b"\x00\x00", (65_537).to_bytes(4, "big")])
async def test_truncated_or_oversize_frame_does_not_allocate_or_dispatch(header):
    reader, writer = asyncio.StreamReader(), CaptureWriter(broken=True)
    reader.feed_data(header)
    reader.feed_eof()
    await serve_worker_stream(reader, writer, UnreachableBroker())
    assert writer.closed and b"worker_request_denied" in writer.output


async def test_incomplete_stream_expires_and_closes():
    reader, writer = asyncio.StreamReader(), CaptureWriter()
    reader.feed_data((100).to_bytes(4, "big"))
    before = time.monotonic()
    async with asyncio.timeout(12):
        await serve_worker_stream(reader, writer, UnreachableBroker())
    assert 9 <= time.monotonic() - before < 12
    assert writer.closed and b"worker_request_denied" in writer.output


async def test_stream_cancellation_closes_without_a_partial_reply():
    reader, writer = asyncio.StreamReader(), CaptureWriter()
    task = asyncio.create_task(serve_worker_stream(reader, writer, UnreachableBroker()))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert writer.closed and not writer.output
