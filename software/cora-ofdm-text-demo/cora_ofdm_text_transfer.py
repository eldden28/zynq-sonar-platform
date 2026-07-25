#!/usr/bin/env python3
"""Reliable progressive text transfer over the Cora acoustic OFDM modem."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
import hashlib
import math
from pathlib import Path
import struct
import subprocess
import tempfile
import threading
import time
from typing import Callable, Protocol
import zlib

import numpy as np

from cora_ofdm_acoustic import (
    AcousticConfig,
    DecodeResult,
    EncodedPacket,
    HEADER_REPETITIONS,
    TRAINING_SYMBOLS,
    acquire_superframe,
    colored_room_channel,
    decode_packet,
    decode_superframe,
    decode_superframe_slot,
    encode_packet,
    encode_superframe,
    read_wav,
    write_wav,
)


FRAME_MAGIC = b"CTXT"
FRAME_HEADER = struct.Struct("<4sIHHHI")
DEFAULT_CHUNK_BYTES = 384
DEFAULT_BURST_PACKETS = 8
MAX_TEXT_BYTES = 16_384


class TransferStopped(RuntimeError):
    pass


class PacketBackend(Protocol):
    name: str

    def transceive(self, payload: bytes, sequence: int) -> DecodeResult:
        ...


def encode_text_frame(
    payload: bytes,
    *,
    transfer_id: int,
    sequence: int,
    total_packets: int,
) -> bytes:
    if len(payload) > 0xFFFF:
        raise ValueError("text frame payload is too large")
    return FRAME_HEADER.pack(
        FRAME_MAGIC,
        transfer_id & 0xFFFFFFFF,
        sequence,
        total_packets,
        len(payload),
        zlib.crc32(payload) & 0xFFFFFFFF,
    ) + payload


def decode_text_frame(
    frame: bytes,
    *,
    transfer_id: int,
    sequence: int,
    total_packets: int,
) -> bytes:
    if len(frame) < FRAME_HEADER.size:
        raise ValueError("text frame is shorter than its header")
    (
        magic,
        received_transfer_id,
        received_sequence,
        received_total,
        payload_length,
        received_crc,
    ) = FRAME_HEADER.unpack_from(frame)
    if magic != FRAME_MAGIC:
        raise ValueError("text frame magic is invalid")
    if received_transfer_id != transfer_id:
        raise ValueError("text frame transfer ID is invalid")
    if received_sequence != sequence or received_total != total_packets:
        raise ValueError("text frame sequence metadata is invalid")
    if payload_length > len(frame) - FRAME_HEADER.size:
        raise ValueError("text frame payload length is invalid")
    payload = frame[FRAME_HEADER.size : FRAME_HEADER.size + payload_length]
    expected_crc = zlib.crc32(payload) & 0xFFFFFFFF
    if received_crc != expected_crc:
        raise ValueError("text frame CRC is invalid")
    return payload


class ColoredRoomBackend:
    name = "loopback"
    framing = "continuous-superframe"

    def __init__(
        self,
        noise_dbfs: float = -48.0,
        cfg: AcousticConfig | None = None,
    ):
        self.noise_dbfs = noise_dbfs
        self.cfg = cfg or AcousticConfig()
        self.burst_size = DEFAULT_BURST_PACKETS

    def transceive(self, payload: bytes, sequence: int) -> DecodeResult:
        packet = encode_packet(payload, sequence=sequence, cfg=self.cfg)
        offset = 613 + (sequence * 17) % 193
        samples = np.concatenate(
            (
                np.zeros(offset),
                colored_room_channel(
                    packet.samples,
                    noise_dbfs=self.noise_dbfs,
                    seed=sequence + 100,
                ),
            )
        )
        return decode_packet(samples, cfg=self.cfg)

    def transceive_batch(
        self,
        items: list[tuple[bytes, int]],
    ) -> list[DecodeResult]:
        cfg = replace(
            self.cfg,
            leading_silence_s=0.04,
            trailing_silence_s=0.04,
        )
        superframe = encode_superframe(items, cfg=cfg)
        samples = np.concatenate(
            (
                np.zeros(733),
                colored_room_channel(
                    superframe.samples,
                    noise_dbfs=self.noise_dbfs,
                    seed=173,
                ),
            )
        )
        return decode_superframe(
            samples,
            expected_sequences=[sequence for _payload, sequence in items],
            slot_payload_bytes=superframe.slot_payload_bytes,
            cfg=cfg,
        )


class AlsaAirBackend:
    name = "air"

    def __init__(
        self,
        playback_device: str = "plughw:0,0",
        capture_device: str = "plughw:0,0",
        burst_packets: int = DEFAULT_BURST_PACKETS,
        cfg: AcousticConfig | None = None,
        continuous_framing: bool = True,
    ):
        if burst_packets < 1:
            raise ValueError("burst packet count must be positive")
        self.playback_device = playback_device
        self.capture_device = capture_device
        self.burst_size = burst_packets
        self.cfg = cfg or AcousticConfig()
        self.continuous_framing = continuous_framing
        self.framing = (
            "continuous-superframe"
            if continuous_framing
            else "packet-burst"
        )

    @staticmethod
    def _stereo_pcm(samples: np.ndarray) -> bytes:
        clipped = np.clip(np.asarray(samples), -1.0, 1.0)
        mono = np.rint(clipped * 32767.0).astype("<i2")
        return np.repeat(mono[:, None], 2, axis=1).tobytes()

    @staticmethod
    def _window_bounds(
        packet: EncodedPacket,
        *,
        nominal_start: int,
        capture_samples: int,
        sample_rate: int,
    ) -> tuple[int, int]:
        before = round(0.10 * sample_rate)
        # Stop before the following packet's training symbol. The packet
        # already carries a trailing guard plus 40 ms of silence, so another
        # 40 ms accommodates USB latency without letting synchronization lock
        # onto the next packet in the continuous stream.
        after = round(0.04 * sample_rate)
        begin = max(0, nominal_start - before)
        end = min(
            capture_samples,
            nominal_start + packet.samples.size + after,
        )
        return begin, end

    def transceive_batch(
        self, items: list[tuple[bytes, int]]
    ) -> list[DecodeResult]:
        if self.continuous_framing:
            return self._transceive_superframe(items)
        return self._transceive_packet_batch(items)

    def _transceive_superframe(
        self,
        items: list[tuple[bytes, int]],
    ) -> list[DecodeResult]:
        if not items:
            return []
        cfg = replace(
            self.cfg,
            leading_silence_s=0.04,
            trailing_silence_s=0.04,
        )
        superframe = encode_superframe(items, cfg=cfg)
        sample_rate = cfg.sample_rate
        pre_roll_s = 0.15
        post_roll_s = 0.25
        timeout_s = (
            superframe.duration_s + pre_roll_s + post_roll_s + 8.0
        )
        record_command = [
            "arecord",
            "-q",
            "-D",
            self.capture_device,
            "-t",
            "raw",
            "-f",
            "S16_LE",
            "-r",
            str(sample_rate),
            "-c",
            "1",
            "-",
        ]
        play_command = [
            "aplay",
            "-q",
            "-D",
            self.playback_device,
            "-t",
            "raw",
            "-f",
            "S16_LE",
            "-r",
            str(sample_rate),
            "-c",
            "2",
            "-",
        ]

        captured = bytearray()
        condition = threading.Condition()
        capture_finished = False
        capture_error: BaseException | None = None
        playback_error: BaseException | None = None
        recorder: subprocess.Popen | None = None
        player: subprocess.Popen | None = None

        def capture_reader() -> None:
            nonlocal capture_finished, capture_error
            try:
                assert recorder is not None and recorder.stdout is not None
                while True:
                    chunk = recorder.stdout.read(8192)
                    if not chunk:
                        break
                    with condition:
                        captured.extend(chunk)
                        condition.notify_all()
            except BaseException as error:
                capture_error = error
            finally:
                with condition:
                    capture_finished = True
                    condition.notify_all()

        def playback_writer() -> None:
            nonlocal playback_error
            try:
                assert player is not None and player.stdin is not None
                player.stdin.write(self._stereo_pcm(superframe.samples))
                player.stdin.close()
            except BaseException as error:
                playback_error = error

        def finish_capture() -> None:
            nonlocal playback_error
            try:
                assert player is not None
                return_code = player.wait(timeout=timeout_s)
                if return_code:
                    playback_error = RuntimeError(
                        f"aplay exited with status {return_code}"
                    )
            except BaseException as error:
                playback_error = error
            finally:
                time.sleep(post_roll_s)
                if recorder is not None and recorder.poll() is None:
                    recorder.terminate()

        reader_thread: threading.Thread | None = None
        writer_thread: threading.Thread | None = None
        finish_thread: threading.Thread | None = None
        futures: list[Future[list[DecodeResult]]] = []
        deadline = time.monotonic() + timeout_s
        nominal_frame_start = round(pre_roll_s * sample_rate)
        before = round(0.10 * sample_rate)
        after = round(0.04 * sample_rate)
        slot_symbols = HEADER_REPETITIONS + superframe.payload_symbols
        slot_samples = slot_symbols * cfg.symbol_len

        def capture_snapshot(
            begin: int,
            required_samples: int,
            description: str,
        ) -> np.ndarray:
            with condition:
                while (
                    len(captured) // 2 < required_samples
                    and not capture_finished
                ):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(
                            f"timed out waiting for {description}"
                        )
                    condition.wait(timeout=min(0.1, remaining))
                available = len(captured) // 2
                end = min(available, required_samples)
                if end < required_samples:
                    raise RuntimeError(
                        f"audio capture ended inside {description}"
                    )
                segment_bytes = bytes(captured[begin * 2 : end * 2])
            return (
                np.frombuffer(segment_bytes, dtype="<i2")
                .astype(np.float32)
                / 32768.0
            )

        def decode_block(
            sample_begin: int,
            sample_end: int,
            item_begin: int,
            item_end: int,
        ) -> list[DecodeResult]:
            nominal_begin = nominal_frame_start + sample_begin
            segment_begin = max(0, nominal_begin - before)
            acquisition_samples = (
                nominal_begin + round(0.20 * sample_rate)
            )
            try:
                acquisition_audio = capture_snapshot(
                    segment_begin,
                    acquisition_samples,
                    "superframe training",
                )
                acquisition = acquire_superframe(
                    acquisition_audio,
                    sync_search_samples=round(0.20 * sample_rate),
                    cfg=cfg,
                )
            except (RuntimeError, TimeoutError, ValueError):
                # A failed early acquisition should degrade to the existing
                # complete-block decoder so packet-level retry remains usable.
                block_audio = capture_snapshot(
                    segment_begin,
                    nominal_frame_start + sample_end + after,
                    "superframe block",
                )
                block_items = items[item_begin:item_end]
                return decode_superframe(
                    block_audio,
                    expected_sequences=[
                        sequence for _payload, sequence in block_items
                    ],
                    slot_payload_bytes=superframe.slot_payload_bytes,
                    sync_search_samples=round(0.20 * sample_rate),
                    cfg=cfg,
                )

            results = []
            for local_index, (_payload, sequence) in enumerate(
                items[item_begin:item_end]
            ):
                symbol_offset = (
                    TRAINING_SYMBOLS + local_index * slot_symbols
                ) * cfg.symbol_len
                slot_end = (
                    segment_begin
                    + acquisition.start_sample
                    + symbol_offset
                    + slot_samples
                )
                slot_audio = capture_snapshot(
                    segment_begin,
                    slot_end,
                    f"superframe packet {item_begin + local_index}",
                )
                results.append(
                    decode_superframe_slot(
                        slot_audio,
                        expected_sequence=sequence,
                        slot_payload_bytes=superframe.slot_payload_bytes,
                        symbol_offset=symbol_offset,
                        acquisition=acquisition,
                        cfg=cfg,
                    )
                )
            return results

        try:
            recorder = subprocess.Popen(
                record_command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
            reader_thread = threading.Thread(
                target=capture_reader,
                daemon=True,
                name="cora-ofdm-superframe-capture",
            )
            reader_thread.start()
            time.sleep(pre_roll_s)

            player = subprocess.Popen(
                play_command,
                stdin=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
            writer_thread = threading.Thread(
                target=playback_writer,
                daemon=True,
                name="cora-ofdm-superframe-playback",
            )
            finish_thread = threading.Thread(
                target=finish_capture,
                daemon=True,
                name="cora-ofdm-superframe-finish",
            )
            writer_thread.start()
            finish_thread.start()

            with ThreadPoolExecutor(max_workers=2) as decoder_pool:
                for (
                    sample_begin,
                    sample_end,
                    item_begin,
                    item_end,
                ) in superframe.decode_blocks:
                    futures.append(
                        decoder_pool.submit(
                            decode_block,
                            sample_begin,
                            sample_end,
                            item_begin,
                            item_end,
                        )
                    )
                results = [
                    result
                    for future in futures
                    for result in future.result()
                ]

            if finish_thread is not None:
                finish_thread.join(
                    timeout=max(0.0, deadline - time.monotonic())
                )
            if playback_error is not None:
                raise RuntimeError(
                    f"continuous playback failed: {playback_error}"
                )
            if capture_error is not None:
                raise RuntimeError(
                    f"continuous capture failed: {capture_error}"
                )
            return results
        finally:
            if player is not None and player.poll() is None:
                player.terminate()
            if recorder is not None and recorder.poll() is None:
                recorder.terminate()
            for thread in (writer_thread, finish_thread, reader_thread):
                if thread is not None:
                    thread.join(timeout=2)
            if player is not None and player.poll() is None:
                player.kill()
            if recorder is not None and recorder.poll() is None:
                recorder.kill()

    def _transceive_packet_batch(
        self, items: list[tuple[bytes, int]]
    ) -> list[DecodeResult]:
        if not items:
            return []

        # The streaming path needs only a short quiet guard around each OFDM
        # packet. ALSA is kept open across the whole burst, so the 100 ms
        # process-start guards used by the single-packet fallback are not
        # necessary.
        cfg = replace(
            self.cfg,
            leading_silence_s=0.04,
            trailing_silence_s=0.04,
        )
        packets = [
            encode_packet(payload, sequence=sequence, cfg=cfg)
            for payload, sequence in items
        ]
        sample_rate = cfg.sample_rate
        pre_roll_s = 0.15
        post_roll_s = 0.25
        nominal_capture_start = round(pre_roll_s * sample_rate)
        total_samples = sum(packet.samples.size for packet in packets)
        timeout_s = total_samples / sample_rate + pre_roll_s + post_roll_s + 8

        record_command = [
            "arecord",
            "-q",
            "-D",
            self.capture_device,
            "-t",
            "raw",
            "-f",
            "S16_LE",
            "-r",
            str(sample_rate),
            "-c",
            "1",
            "-",
        ]
        play_command = [
            "aplay",
            "-q",
            "-D",
            self.playback_device,
            "-t",
            "raw",
            "-f",
            "S16_LE",
            "-r",
            str(sample_rate),
            "-c",
            "2",
            "-",
        ]

        captured = bytearray()
        condition = threading.Condition()
        capture_finished = False
        capture_error: BaseException | None = None
        playback_error: BaseException | None = None
        recorder: subprocess.Popen | None = None
        player: subprocess.Popen | None = None

        def capture_reader() -> None:
            nonlocal capture_finished, capture_error
            try:
                assert recorder is not None and recorder.stdout is not None
                while True:
                    chunk = recorder.stdout.read(8192)
                    if not chunk:
                        break
                    with condition:
                        captured.extend(chunk)
                        condition.notify_all()
            except BaseException as error:
                capture_error = error
            finally:
                with condition:
                    capture_finished = True
                    condition.notify_all()

        def playback_writer() -> None:
            nonlocal playback_error
            try:
                assert player is not None and player.stdin is not None
                for packet in packets:
                    player.stdin.write(self._stereo_pcm(packet.samples))
                player.stdin.close()
            except BaseException as error:
                playback_error = error
                if player is not None and player.stdin is not None:
                    try:
                        player.stdin.close()
                    except OSError:
                        pass

        def finish_capture() -> None:
            nonlocal playback_error
            try:
                assert player is not None
                return_code = player.wait(timeout=timeout_s)
                if return_code:
                    playback_error = RuntimeError(
                        f"aplay exited with status {return_code}"
                    )
            except BaseException as error:
                playback_error = error
            finally:
                time.sleep(post_roll_s)
                if recorder is not None and recorder.poll() is None:
                    recorder.terminate()

        reader_thread: threading.Thread | None = None
        writer_thread: threading.Thread | None = None
        finish_thread: threading.Thread | None = None
        futures: list[Future[DecodeResult]] = []
        deadline = time.monotonic() + timeout_s
        cumulative_samples = 0
        try:
            recorder = subprocess.Popen(
                record_command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
            reader_thread = threading.Thread(
                target=capture_reader,
                daemon=True,
                name="cora-ofdm-capture",
            )
            reader_thread.start()
            time.sleep(pre_roll_s)

            player = subprocess.Popen(
                play_command,
                stdin=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
            writer_thread = threading.Thread(
                target=playback_writer,
                daemon=True,
                name="cora-ofdm-playback",
            )
            finish_thread = threading.Thread(
                target=finish_capture,
                daemon=True,
                name="cora-ofdm-playback-finish",
            )
            writer_thread.start()
            finish_thread.start()

            with ThreadPoolExecutor(max_workers=2) as decoder_pool:
                for packet in packets:
                    nominal_start = (
                        nominal_capture_start + cumulative_samples
                    )
                    required_samples = (
                        nominal_start
                        + packet.samples.size
                        + round(0.04 * sample_rate)
                    )
                    required_bytes = required_samples * 2
                    with condition:
                        while (
                            len(captured) < required_bytes
                            and not capture_finished
                        ):
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                raise TimeoutError(
                                    "timed out waiting for captured audio"
                                )
                            condition.wait(timeout=min(0.1, remaining))
                        available_samples = len(captured) // 2
                        begin, end = self._window_bounds(
                            packet,
                            nominal_start=nominal_start,
                            capture_samples=available_samples,
                            sample_rate=sample_rate,
                        )
                        if end * 2 > len(captured):
                            raise RuntimeError(
                                "audio capture ended inside an OFDM packet"
                            )
                        segment_bytes = bytes(captured[begin * 2 : end * 2])
                    segment = (
                        np.frombuffer(segment_bytes, dtype="<i2")
                        .astype(np.float32)
                        / 32768.0
                    )
                    futures.append(
                        decoder_pool.submit(
                            decode_packet,
                            segment,
                            cfg=cfg,
                        )
                    )
                    cumulative_samples += packet.samples.size
                results = [future.result() for future in futures]

            if playback_error is not None:
                raise RuntimeError(
                    f"streaming playback failed: {playback_error}"
                )
            if capture_error is not None:
                raise RuntimeError(
                    f"streaming capture failed: {capture_error}"
                )
            return results
        finally:
            if player is not None and player.poll() is None:
                player.terminate()
            if recorder is not None and recorder.poll() is None:
                recorder.terminate()
            for thread in (writer_thread, finish_thread, reader_thread):
                if thread is not None:
                    thread.join(timeout=2)
            if player is not None and player.poll() is None:
                player.kill()
            if recorder is not None and recorder.poll() is None:
                recorder.kill()

    def transceive(self, payload: bytes, sequence: int) -> DecodeResult:
        cfg = self.cfg
        packet = encode_packet(payload, sequence=sequence, cfg=cfg)
        record_seconds = max(1, math.ceil(packet.duration_s + 0.40))
        with tempfile.TemporaryDirectory(
            prefix="cora-acoustic-text-"
        ) as directory:
            root = Path(directory)
            tx_path = root / "tx.wav"
            rx_path = root / "rx.wav"
            write_wav(tx_path, packet.samples, cfg.sample_rate)
            record_command = [
                "arecord",
                "-q",
                "-D",
                self.capture_device,
                "-f",
                "S16_LE",
                "-r",
                str(cfg.sample_rate),
                "-c",
                "1",
                "-d",
                str(record_seconds),
                str(rx_path),
            ]
            recorder = subprocess.Popen(record_command)
            try:
                time.sleep(0.15)
                subprocess.run(
                    [
                        "aplay",
                        "-q",
                        "-D",
                        self.playback_device,
                        str(tx_path),
                    ],
                    check=True,
                )
                return_code = recorder.wait(timeout=record_seconds + 2)
            finally:
                if recorder.poll() is None:
                    recorder.terminate()
                    recorder.wait()
            if return_code != 0:
                raise RuntimeError(
                    f"arecord exited with status {return_code}"
                )
            samples, rate = read_wav(rx_path)
            if rate != cfg.sample_rate:
                raise RuntimeError(
                    f"capture returned {rate} sample/s, expected "
                    f"{cfg.sample_rate}"
                )
            return decode_packet(samples, cfg=cfg)


PacketCallback = Callable[[dict, bytes], None]


def transfer_text(
    text: str,
    *,
    backend: PacketBackend,
    stop_event: threading.Event | None = None,
    packet_callback: PacketCallback | None = None,
    chunk_bytes: int = DEFAULT_CHUNK_BYTES,
    max_retries: int = 2,
) -> tuple[bytes, dict]:
    source = text.encode("utf-8")
    if not source:
        raise ValueError("text is empty")
    if len(source) > MAX_TEXT_BYTES:
        raise ValueError(
            f"text is {len(source)} UTF-8 bytes; maximum is {MAX_TEXT_BYTES}"
        )
    if chunk_bytes < 1 or chunk_bytes > 1024:
        raise ValueError("chunk size must be between 1 and 1024 bytes")
    if max_retries < 0:
        raise ValueError("max retries cannot be negative")

    total_packets = math.ceil(len(source) / chunk_bytes)
    transfer_id = zlib.crc32(source) & 0xFFFFFFFF
    source_sha256 = hashlib.sha256(source).hexdigest()
    received = bytearray()
    retries = 0
    packet_errors = 0
    start = time.perf_counter()
    cfg = getattr(backend, "cfg", AcousticConfig())
    state = {
        "application": "cora-ofdm-text-transfer",
        "status": "warming",
        "backend": backend.name,
        "framing": getattr(backend, "framing", "standalone-packets"),
        "modem_version": cfg.modem_name,
        "body_code": cfg.body_code,
        "low_frequency_hz": cfg.low_frequency_hz,
        "high_frequency_hz": cfg.high_frequency_hz,
        "bandwidth_hz": (
            cfg.high_frequency_hz - cfg.low_frequency_hz
        ),
        "sample_rate": cfg.sample_rate,
        "fft_len": cfg.fft_len,
        "cp_len": cfg.cp_len,
        "subcarrier_spacing_hz": cfg.subcarrier_spacing_hz,
        "symbol_duration_ms": 1000.0 * cfg.symbol_duration_s,
        "cyclic_prefix_ms": 1000.0 * cfg.cyclic_prefix_duration_s,
        "data_subcarriers": int(cfg.data_bins.size),
        "gross_bit_rate": cfg.gross_bit_rate,
        "transfer_id": f"{transfer_id:08x}",
        "total_bytes": len(source),
        "bytes_received": 0,
        "packets_total": total_packets,
        "packets_received": 0,
        "chunk_bytes": chunk_bytes,
        "retries": 0,
        "packet_errors": 0,
        "elapsed_s": 0.0,
        "payload_throughput_bps": 0.0,
        "eta_s": None,
        "progress_percent": 0.0,
        "last_sync_metric": None,
        "source_sha256": source_sha256,
        "output_sha256": None,
        "error": None,
    }

    chunks = [
        source[index * chunk_bytes : (index + 1) * chunk_bytes]
        for index in range(total_packets)
    ]
    frames = [
        encode_text_frame(
            chunk,
            transfer_id=transfer_id,
            sequence=sequence,
            total_packets=total_packets,
        )
        for sequence, chunk in enumerate(chunks)
    ]
    batch_method = getattr(backend, "transceive_batch", None)
    burst_size = (
        max(1, int(getattr(backend, "burst_size", 1)))
        if callable(batch_method)
        else 1
    )

    def validate(
        result: DecodeResult,
        sequence: int,
    ) -> bytes:
        if not result.valid:
            raise ValueError(result.error or "packet CRC failed")
        return decode_text_frame(
            result.payload,
            transfer_id=transfer_id,
            sequence=sequence,
            total_packets=total_packets,
        )

    try:
        for group_begin in range(0, total_packets, burst_size):
            if stop_event is not None and stop_event.is_set():
                raise TransferStopped("transfer stopped")
            sequences = list(
                range(
                    group_begin,
                    min(total_packets, group_begin + burst_size),
                )
            )
            if callable(batch_method) and len(sequences) > 1:
                results = batch_method(
                    [
                        (frames[sequence], sequence & 0xFF)
                        for sequence in sequences
                    ]
                )
                if len(results) != len(sequences):
                    raise RuntimeError(
                        "burst backend returned the wrong result count"
                    )
            else:
                results = [
                    backend.transceive(
                        frames[sequence],
                        sequence & 0xFF,
                    )
                    for sequence in sequences
                ]

            decoded_group: list[tuple[bytes, DecodeResult]] = []
            for sequence, first_result in zip(sequences, results):
                decoded_chunk = None
                last_error = None
                last_result = first_result
                for attempt in range(max_retries + 1):
                    if stop_event is not None and stop_event.is_set():
                        raise TransferStopped("transfer stopped")
                    if attempt:
                        retries += 1
                        last_result = backend.transceive(
                            frames[sequence],
                            sequence & 0xFF,
                        )
                    try:
                        decoded_chunk = validate(last_result, sequence)
                        break
                    except ValueError as error:
                        last_error = str(error)
                        packet_errors += 1

                if decoded_chunk is None:
                    raise RuntimeError(
                        f"packet {sequence + 1}/{total_packets} failed after "
                        f"{max_retries + 1} attempts: {last_error}"
                    )
                decoded_group.append((decoded_chunk, last_result))

            for sequence, (decoded_chunk, last_result) in zip(
                sequences, decoded_group
            ):
                received.extend(decoded_chunk)
                elapsed = time.perf_counter() - start
                rate = 8.0 * len(received) / elapsed if elapsed else 0.0
                remaining_bits = 8.0 * (len(source) - len(received))
                state.update(
                    {
                        "status": "running",
                        "bytes_received": len(received),
                        "packets_received": sequence + 1,
                        "retries": retries,
                        "packet_errors": packet_errors,
                        "elapsed_s": elapsed,
                        "payload_throughput_bps": rate,
                        "eta_s": remaining_bits / rate if rate else None,
                        "progress_percent": (
                            100.0 * len(received) / len(source)
                        ),
                        "last_sync_metric": last_result.sync_metric,
                    }
                )
                if packet_callback is not None:
                    packet_callback(state.copy(), decoded_chunk)

        output = bytes(received)
        output_sha256 = hashlib.sha256(output).hexdigest()
        if output_sha256 != source_sha256:
            raise RuntimeError("received text SHA-256 does not match source")
        elapsed = time.perf_counter() - start
        state.update(
            {
                "status": "complete",
                "elapsed_s": elapsed,
                "payload_throughput_bps": (
                    8.0 * len(output) / elapsed if elapsed else 0.0
                ),
                "eta_s": 0.0,
                "progress_percent": 100.0,
                "output_sha256": output_sha256,
            }
        )
        return output, state
    except BaseException as error:
        state.update(
            {
                "status": (
                    "stopped"
                    if isinstance(error, TransferStopped)
                    else "failed"
                ),
                "elapsed_s": time.perf_counter() - start,
                "retries": retries,
                "packet_errors": packet_errors,
                "error": str(error),
            }
        )
        raise
