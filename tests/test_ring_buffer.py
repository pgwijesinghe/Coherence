import numpy as np
import pytest

from coherence.core.ring_buffer import BufferOverrunError, RingBuffer


def test_push_and_read_exact_block():
    buf = RingBuffer(num_channels=2, capacity_samples=1000)
    chunk = np.arange(200 * 2, dtype=np.float64).reshape(200, 2)
    buf.push(chunk)
    block = buf.try_read_block(0, 200)
    assert np.array_equal(block, chunk)


def test_returns_none_until_enough_samples_available():
    buf = RingBuffer(num_channels=1, capacity_samples=1000)
    buf.push(np.ones((50, 1)))
    assert buf.try_read_block(0, 100) is None
    buf.push(np.ones((50, 1)))
    assert buf.try_read_block(0, 100) is not None


def test_overlapping_reads_advance_by_hop():
    buf = RingBuffer(num_channels=1, capacity_samples=1000)
    data = np.arange(400, dtype=np.float64).reshape(400, 1)
    buf.push(data)
    block_a = buf.try_read_block(0, 256)
    block_b = buf.try_read_block(128, 256)
    assert np.array_equal(block_a, data[0:256])
    assert np.array_equal(block_b, data[128:384])


def test_read_across_wraparound_boundary():
    capacity = 300
    buf = RingBuffer(num_channels=1, capacity_samples=capacity)
    buf.push(np.arange(250, dtype=np.float64).reshape(250, 1))
    buf.try_read_block(0, 100)  # doesn't matter, just advances nothing (no internal read ptr)
    buf.push(np.arange(250, 350, dtype=np.float64).reshape(100, 1))  # wraps past capacity
    block = buf.try_read_block(200, 100)
    expected = np.arange(200, 300, dtype=np.float64).reshape(100, 1)
    assert np.array_equal(block, expected)


def test_overrun_raised_when_reader_falls_behind():
    buf = RingBuffer(num_channels=1, capacity_samples=100)
    buf.push(np.ones((100, 1)))
    buf.push(np.ones((100, 1)))  # write_pos now 200, capacity 100 -> old data at read_pos=0 gone
    with pytest.raises(BufferOverrunError):
        buf.try_read_block(0, 50)
