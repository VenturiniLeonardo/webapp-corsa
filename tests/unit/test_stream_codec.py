import pytest

from app.domain.stream_codec import decode_stream, encode_stream


def test_roundtrip():
    ch = {
        "time": list(range(100)),
        "distance": [i * 3.1 for i in range(100)],
        "hr": [None if i % 10 == 0 else 140 + i % 5 for i in range(100)],
    }
    assert decode_stream(encode_stream(ch)) == ch


def test_length_mismatch():
    with pytest.raises(ValueError, match="mismatch"):
        encode_stream({"time": [0, 1], "hr": [1]})


def test_time_decreasing():
    with pytest.raises(ValueError, match="time"):
        encode_stream({"time": [0, 2, 1]})


def test_time_equal_ok():
    encode_stream({"time": [0, 1, 1]})


def test_empty():
    assert decode_stream(encode_stream({})) == {}
    assert decode_stream(encode_stream({"time": [], "hr": []})) == {"time": [], "hr": []}
