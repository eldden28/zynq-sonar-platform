#!/usr/bin/env python3
"""Hardware-shaped DVL, acoustic navigation, and INS simulation for Cora.

The simulators deliberately stop at the future DMA boundaries:

* a DVL front end produces four interleaved complex-int16 baseband beams;
* a hydrophone front end produces four interleaved signed-int16 real channels.

All processing below those boundaries is the same code used by simulated and
future character-device sources.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
import binascii
import json
import math
import multiprocessing
import os
from pathlib import Path
import queue
import struct
import threading
import time
from typing import Callable, Iterable

import numpy as np


FRAME_MAGIC = b"CSF1"
FRAME_VERSION = 1
SENSOR_DVL = 1
SENSOR_HYDROPHONE = 2
FORMAT_CI16 = 1
FORMAT_S16 = 2
FRAME_HEADER = struct.Struct("<4sHHIQQIIHHIIII")

NAV_MAGIC = b"CNV1"
NAV_VERSION = 1
NAV_MODE_LBL = 1
NAV_MODE_IUSBL = 2
NAV_PAYLOAD_NO_CRC = struct.Struct("<4sBBHIiiiQQ")
NAV_PAYLOAD = struct.Struct("<4sBBHIiiiQQI")

SOUND_SPEED_MPS = 1500.0
EARTH_RADIUS_M = 6_378_137.0
GRAVITY_NED = np.asarray((0.0, 0.0, 9.80665), dtype=np.float64)
NAVIGATION_ORIGIN_LATITUDE_DEG = 41.2250
NAVIGATION_ORIGIN_LONGITUDE_DEG = -77.0445
# OpenStreetMap West Branch Susquehanna River centerline points through the
# Williamsport, Pennsylvania state-park frontage, ordered west to east.
SUSQUEHANNA_CENTERLINE_WGS84 = (
    (41.2211052, -77.0570417),
    (41.2215131, -77.0557027),
    (41.2232994, -77.0498380),
    (41.2267645, -77.0420888),
    (41.2278639, -77.0394350),
    (41.2285325, -77.0373790),
    (41.2288180, -77.0352699),
    (41.2287258, -77.0321798),
)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def wrap_angle(angle: float) -> float:
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


def quat_normalize(quaternion: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(quaternion))
    if norm < 1e-12:
        return np.asarray((1.0, 0.0, 0.0, 0.0), dtype=np.float64)
    return np.asarray(quaternion, dtype=np.float64) / norm


def quat_multiply(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    lw, lx, ly, lz = left
    rw, rx, ry, rz = right
    return np.asarray(
        (
            lw * rw - lx * rx - ly * ry - lz * rz,
            lw * rx + lx * rw + ly * rz - lz * ry,
            lw * ry - lx * rz + ly * rw + lz * rx,
            lw * rz + lx * ry - ly * rx + lz * rw,
        ),
        dtype=np.float64,
    )


def quat_conjugate(quaternion: np.ndarray) -> np.ndarray:
    value = np.asarray(quaternion, dtype=np.float64).copy()
    value[1:] *= -1.0
    return value


def quat_from_euler(roll: float, pitch: float, yaw: float) -> np.ndarray:
    cr, sr = math.cos(roll / 2.0), math.sin(roll / 2.0)
    cp, sp = math.cos(pitch / 2.0), math.sin(pitch / 2.0)
    cy, sy = math.cos(yaw / 2.0), math.sin(yaw / 2.0)
    return quat_normalize(
        np.asarray(
            (
                cr * cp * cy + sr * sp * sy,
                sr * cp * cy - cr * sp * sy,
                cr * sp * cy + sr * cp * sy,
                cr * cp * sy - sr * sp * cy,
            )
        )
    )


def quat_from_delta(delta: np.ndarray) -> np.ndarray:
    angle = float(np.linalg.norm(delta))
    if angle < 1e-12:
        return quat_normalize(
            np.asarray((1.0, delta[0] / 2, delta[1] / 2, delta[2] / 2))
        )
    axis = delta / angle
    return np.concatenate(
        (np.asarray((math.cos(angle / 2.0),)), axis * math.sin(angle / 2.0))
    )


def quat_to_matrix(quaternion: np.ndarray) -> np.ndarray:
    w, x, y, z = quat_normalize(quaternion)
    return np.asarray(
        (
            (
                1 - 2 * (y * y + z * z),
                2 * (x * y - z * w),
                2 * (x * z + y * w),
            ),
            (
                2 * (x * y + z * w),
                1 - 2 * (x * x + z * z),
                2 * (y * z - x * w),
            ),
            (
                2 * (x * z - y * w),
                2 * (y * z + x * w),
                1 - 2 * (x * x + y * y),
            ),
        ),
        dtype=np.float64,
    )


def quat_to_euler(quaternion: np.ndarray) -> tuple[float, float, float]:
    matrix = quat_to_matrix(quaternion)
    pitch = math.asin(clamp(-float(matrix[2, 0]), -1.0, 1.0))
    roll = math.atan2(float(matrix[2, 1]), float(matrix[2, 2]))
    yaw = math.atan2(float(matrix[1, 0]), float(matrix[0, 0]))
    return roll, pitch, yaw


def ned_to_wgs84(
    ned: np.ndarray, origin_lat_deg: float, origin_lon_deg: float
) -> tuple[float, float, float]:
    latitude = origin_lat_deg + math.degrees(float(ned[0]) / EARTH_RADIUS_M)
    longitude = origin_lon_deg + math.degrees(
        float(ned[1])
        / (
            EARTH_RADIUS_M
            * max(math.cos(math.radians(origin_lat_deg)), 1e-6)
        )
    )
    return latitude, longitude, float(ned[2])


def wgs84_to_ned(
    latitude_deg: float,
    longitude_deg: float,
    depth_m: float,
    origin_lat_deg: float,
    origin_lon_deg: float,
) -> np.ndarray:
    north = math.radians(latitude_deg - origin_lat_deg) * EARTH_RADIUS_M
    east = (
        math.radians(longitude_deg - origin_lon_deg)
        * EARTH_RADIUS_M
        * math.cos(math.radians(origin_lat_deg))
    )
    return np.asarray((north, east, depth_m), dtype=np.float64)


def parallel_river_mission(
    centerline_wgs84: Iterable[tuple[float, float]],
    lane_half_spacing_m: float = 30.0,
) -> np.ndarray:
    """Build upstream/downstream lanes around a mapped river centerline."""
    centerline = np.asarray(
        [
            wgs84_to_ned(
                latitude,
                longitude,
                0.0,
                NAVIGATION_ORIGIN_LATITUDE_DEG,
                NAVIGATION_ORIGIN_LONGITUDE_DEG,
            )[:2]
            for latitude, longitude in centerline_wgs84
        ],
        dtype=np.float64,
    )
    if len(centerline) < 2:
        raise ValueError("river mission requires at least two centerline points")
    offsets = []
    for index in range(len(centerline)):
        if index == 0:
            tangent = centerline[1] - centerline[0]
        elif index == len(centerline) - 1:
            tangent = centerline[-1] - centerline[-2]
        else:
            tangent = centerline[index + 1] - centerline[index - 1]
        tangent /= np.linalg.norm(tangent)
        offsets.append(
            np.asarray((-tangent[1], tangent[0])) * lane_half_spacing_m
        )
    offsets_array = np.asarray(offsets)
    downstream_lane = centerline - offsets_array
    upstream_lane = centerline + offsets_array
    return np.vstack((downstream_lane, upstream_lane[::-1]))


SUSQUEHANNA_MISSION_WAYPOINTS_NED_M = parallel_river_mission(
    SUSQUEHANNA_CENTERLINE_WGS84
)


@dataclass(frozen=True)
class SensorFrame:
    sensor_type: int
    sequence: int
    timestamp_tai_ns: int
    clock_uncertainty_ns: int
    sample_rate_hz: int
    center_frequency_hz: int
    channels: int
    sample_format: int
    samples_per_channel: int
    flags: int
    payload: bytes

    def to_bytes(self) -> bytes:
        payload_crc = binascii.crc32(self.payload) & 0xFFFFFFFF
        header = FRAME_HEADER.pack(
            FRAME_MAGIC,
            FRAME_VERSION,
            self.sensor_type,
            self.sequence,
            self.timestamp_tai_ns,
            self.clock_uncertainty_ns,
            self.sample_rate_hz,
            self.center_frequency_hz,
            self.channels,
            self.sample_format,
            self.samples_per_channel,
            self.flags,
            len(self.payload),
            payload_crc,
        )
        return header + self.payload

    @classmethod
    def from_bytes(cls, encoded: bytes) -> "SensorFrame":
        if len(encoded) < FRAME_HEADER.size:
            raise ValueError("sensor frame is shorter than its header")
        (
            magic,
            version,
            sensor_type,
            sequence,
            timestamp_tai_ns,
            clock_uncertainty_ns,
            sample_rate_hz,
            center_frequency_hz,
            channels,
            sample_format,
            samples_per_channel,
            flags,
            payload_bytes,
            payload_crc,
        ) = FRAME_HEADER.unpack_from(encoded)
        if magic != FRAME_MAGIC:
            raise ValueError("sensor frame magic does not match")
        if version != FRAME_VERSION:
            raise ValueError(f"unsupported sensor frame version {version}")
        if len(encoded) != FRAME_HEADER.size + payload_bytes:
            raise ValueError("sensor frame payload length does not match")
        payload = encoded[FRAME_HEADER.size :]
        if (binascii.crc32(payload) & 0xFFFFFFFF) != payload_crc:
            raise ValueError("sensor frame payload CRC does not match")
        return cls(
            sensor_type=sensor_type,
            sequence=sequence,
            timestamp_tai_ns=timestamp_tai_ns,
            clock_uncertainty_ns=clock_uncertainty_ns,
            sample_rate_hz=sample_rate_hz,
            center_frequency_hz=center_frequency_hz,
            channels=channels,
            sample_format=sample_format,
            samples_per_channel=samples_per_channel,
            flags=flags,
            payload=payload,
        )

    @classmethod
    def from_ci16(
        cls,
        samples: np.ndarray,
        *,
        sequence: int,
        timestamp_tai_ns: int,
        clock_uncertainty_ns: int,
        sample_rate_hz: int,
        center_frequency_hz: int,
        flags: int = 0,
    ) -> "SensorFrame":
        values = np.asarray(samples)
        if values.ndim != 2:
            raise ValueError("CI16 samples must have shape (samples, channels)")
        peak = max(float(np.max(np.abs(values))), 1e-12)
        scaled = values * min(30000.0 / peak, 30000.0)
        interleaved = np.empty(values.shape + (2,), dtype="<i2")
        interleaved[..., 0] = np.rint(np.real(scaled)).astype("<i2")
        interleaved[..., 1] = np.rint(np.imag(scaled)).astype("<i2")
        return cls(
            SENSOR_DVL,
            sequence,
            timestamp_tai_ns,
            clock_uncertainty_ns,
            sample_rate_hz,
            center_frequency_hz,
            values.shape[1],
            FORMAT_CI16,
            values.shape[0],
            flags,
            interleaved.tobytes(),
        )

    @classmethod
    def from_s16(
        cls,
        samples: np.ndarray,
        *,
        sequence: int,
        timestamp_tai_ns: int,
        clock_uncertainty_ns: int,
        sample_rate_hz: int,
        center_frequency_hz: int,
        flags: int = 0,
    ) -> "SensorFrame":
        values = np.asarray(samples)
        if values.ndim != 2:
            raise ValueError("S16 samples must have shape (samples, channels)")
        if np.issubdtype(values.dtype, np.floating):
            values = np.rint(np.clip(values, -1.0, 1.0) * 30000.0)
        payload = values.astype("<i2").tobytes()
        return cls(
            SENSOR_HYDROPHONE,
            sequence,
            timestamp_tai_ns,
            clock_uncertainty_ns,
            sample_rate_hz,
            center_frequency_hz,
            values.shape[1],
            FORMAT_S16,
            values.shape[0],
            flags,
            payload,
        )

    def ci16(self) -> np.ndarray:
        if self.sample_format != FORMAT_CI16:
            raise ValueError("sensor frame does not contain CI16 samples")
        expected = self.samples_per_channel * self.channels * 2
        values = np.frombuffer(self.payload, dtype="<i2")
        if values.size != expected:
            raise ValueError("CI16 payload size does not match frame metadata")
        values = values.reshape(self.samples_per_channel, self.channels, 2)
        return values[..., 0].astype(np.float64) + 1j * values[
            ..., 1
        ].astype(np.float64)

    def s16(self) -> np.ndarray:
        if self.sample_format != FORMAT_S16:
            raise ValueError("sensor frame does not contain S16 samples")
        expected = self.samples_per_channel * self.channels
        values = np.frombuffer(self.payload, dtype="<i2")
        if values.size != expected:
            raise ValueError("S16 payload size does not match frame metadata")
        return values.reshape(self.samples_per_channel, self.channels)


class FrameRecorder:
    """Length-prefix sensor frames for deterministic host/target replay."""

    LENGTH = struct.Struct("<I")

    def __init__(self, path: Path):
        self.path = Path(path)
        self.output = None

    def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.output = self.path.open("wb")

    def write(self, frame: SensorFrame) -> None:
        if self.output is None:
            raise RuntimeError("frame recorder is not open")
        encoded = frame.to_bytes()
        self.output.write(self.LENGTH.pack(len(encoded)))
        self.output.write(encoded)
        self.output.flush()

    def close(self) -> None:
        if self.output is not None:
            self.output.close()
            self.output = None

    @classmethod
    def replay(cls, path: Path) -> Iterable[SensorFrame]:
        with Path(path).open("rb") as source:
            while True:
                length_data = source.read(cls.LENGTH.size)
                if not length_data:
                    return
                if len(length_data) != cls.LENGTH.size:
                    raise ValueError("truncated sensor recording length")
                length = cls.LENGTH.unpack(length_data)[0]
                encoded = source.read(length)
                if len(encoded) != length:
                    raise ValueError("truncated sensor recording frame")
                yield SensorFrame.from_bytes(encoded)


@dataclass
class ClockStatus:
    tai_ns: int
    locked: bool
    offset_ns: float
    drift_ppm: float
    uncertainty_ns: float


class SimulatedClock:
    def __init__(
        self,
        *,
        epoch_tai_ns: int = 1_800_000_000_000_000_000,
        offset_ns: float = 20_000.0,
        drift_ppm: float = 0.8,
        uncertainty_ns: float = 50_000.0,
        jitter_ns: float = 2_000.0,
        seed: int = 0xC04A,
    ):
        self.epoch_tai_ns = epoch_tai_ns
        self.offset_ns = offset_ns
        self.drift_ppm = drift_ppm
        self.uncertainty_ns = uncertainty_ns
        self.jitter_ns = jitter_ns
        self.elapsed_s = 0.0
        self.locked = True
        self.rng = np.random.default_rng(seed)

    def reset(self) -> None:
        self.elapsed_s = 0.0

    def step(self, dt: float) -> ClockStatus:
        self.elapsed_s += dt
        drift_ns = self.elapsed_s * self.drift_ppm * 1000.0
        jitter = float(self.rng.normal(0.0, self.jitter_ns))
        tai_ns = int(
            self.epoch_tai_ns
            + self.elapsed_s * 1e9
            + self.offset_ns
            + drift_ns
            + jitter
        )
        return ClockStatus(
            tai_ns,
            self.locked,
            self.offset_ns + drift_ns,
            self.drift_ppm,
            self.uncertainty_ns,
        )


@dataclass
class VehicleTruth:
    timestamp_tai_ns: int
    position_ned_m: np.ndarray
    velocity_ned_mps: np.ndarray
    quaternion_body_to_ned: np.ndarray
    acceleration_ned_mps2: np.ndarray
    angular_rate_body_rps: np.ndarray
    waypoint_index: int

    def json(self) -> dict:
        roll, pitch, yaw = quat_to_euler(self.quaternion_body_to_ned)
        latitude, longitude, depth = ned_to_wgs84(
            self.position_ned_m,
            NAVIGATION_ORIGIN_LATITUDE_DEG,
            NAVIGATION_ORIGIN_LONGITUDE_DEG,
        )
        return {
            "timestamp_tai_ns": self.timestamp_tai_ns,
            "position_ned_m": np.round(self.position_ned_m, 5).tolist(),
            "velocity_ned_mps": np.round(self.velocity_ned_mps, 5).tolist(),
            "latitude_deg": latitude,
            "longitude_deg": longitude,
            "depth_m": depth,
            "roll_deg": math.degrees(roll),
            "pitch_deg": math.degrees(pitch),
            "heading_deg": math.degrees(yaw) % 360.0,
            "waypoint_index": self.waypoint_index,
        }


class VehicleSimulator:
    """A bounded Susquehanna River mission with smooth vehicle dynamics."""

    def __init__(self, seed: int = 0xC04A):
        self.rng = np.random.default_rng(seed)
        self.speed_mps = 1.2
        self.depth_m = 15.0
        self.waypoints = SUSQUEHANNA_MISSION_WAYPOINTS_NED_M.copy()
        self.reset()

    def reset(self) -> None:
        self.position = np.asarray(
            (
                self.waypoints[0, 0],
                self.waypoints[0, 1],
                self.depth_m,
            )
        )
        self.velocity = np.zeros(3, dtype=np.float64)
        self.yaw = 0.0
        self.roll = 0.0
        self.pitch = 0.0
        self.previous_velocity = self.velocity.copy()
        self.previous_euler = np.asarray((0.0, 0.0, self.yaw))
        self.previous_quaternion = quat_from_euler(0.0, 0.0, self.yaw)
        self.waypoint_index = 1

    def step(self, dt: float, timestamp_tai_ns: int) -> VehicleTruth:
        target = self.waypoints[self.waypoint_index]
        delta = target - self.position[:2]
        if float(np.linalg.norm(delta)) < 2.0:
            self.waypoint_index = (self.waypoint_index + 1) % len(
                self.waypoints
            )
            target = self.waypoints[self.waypoint_index]
            delta = target - self.position[:2]
        desired_yaw = math.atan2(float(delta[1]), float(delta[0]))
        yaw_rate = clamp(wrap_angle(desired_yaw - self.yaw) * 1.4, -0.35, 0.35)
        self.yaw = wrap_angle(self.yaw + yaw_rate * dt)
        target_velocity = np.asarray(
            (
                self.speed_mps * math.cos(self.yaw),
                self.speed_mps * math.sin(self.yaw),
                0.12 * math.sin(self.position[0] / 20.0),
            )
        )
        alpha = 1.0 - math.exp(-dt / 0.8)
        self.velocity += alpha * (target_velocity - self.velocity)
        depth_error = self.depth_m - self.position[2]
        self.velocity[2] += clamp(depth_error * 0.8, -0.3, 0.3) * dt
        self.position += self.velocity * dt
        acceleration = (self.velocity - self.previous_velocity) / dt
        self.roll += (clamp(-yaw_rate * 0.35, -0.16, 0.16) - self.roll) * alpha
        desired_pitch = clamp(
            math.atan2(-self.velocity[2], max(np.linalg.norm(self.velocity[:2]), 0.1)),
            -0.15,
            0.15,
        )
        self.pitch += (desired_pitch - self.pitch) * alpha
        quaternion = quat_from_euler(self.roll, self.pitch, self.yaw)
        delta_quaternion = quat_multiply(
            quat_conjugate(self.previous_quaternion), quaternion
        )
        if delta_quaternion[0] < 0.0:
            delta_quaternion *= -1.0
        vector_norm = float(np.linalg.norm(delta_quaternion[1:]))
        if vector_norm < 1e-12:
            angular_rate = np.zeros(3, dtype=np.float64)
        else:
            angle = 2.0 * math.atan2(
                vector_norm, float(delta_quaternion[0])
            )
            angular_rate = (
                delta_quaternion[1:] / vector_norm * angle / dt
            )
        self.previous_velocity = self.velocity.copy()
        self.previous_euler = np.asarray((self.roll, self.pitch, self.yaw))
        self.previous_quaternion = quaternion.copy()
        return VehicleTruth(
            timestamp_tai_ns,
            self.position.copy(),
            self.velocity.copy(),
            quaternion,
            acceleration,
            angular_rate,
            self.waypoint_index,
        )

    def imu(
        self, truth: VehicleTruth, gyro_bias: np.ndarray, accel_bias: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        body_from_ned = quat_to_matrix(truth.quaternion_body_to_ned).T
        specific_force = body_from_ned @ (
            truth.acceleration_ned_mps2 - GRAVITY_NED
        )
        gyro = (
            truth.angular_rate_body_rps
            + gyro_bias
            + self.rng.normal(0.0, 0.0008, 3)
        )
        accel = (
            specific_force
            + accel_bias
            + self.rng.normal(0.0, 0.012, 3)
        )
        return gyro, accel


@dataclass
class DvlConfig:
    sample_rate_hz: int = 40_000
    center_frequency_hz: int = 600_000
    update_rate_hz: float = 5.0
    beam_angle_deg: float = 30.0
    pulse_duration_s: float = 0.0024
    chirp_bandwidth_hz: float = 8_000.0
    # 1952 samples cover the configured 30 m bottom (including pulse and
    # multipath margin) while keeping envelope correlation in a 2048-point
    # FFT on the resource-constrained Zynq-7010.
    frame_samples: int = 1952
    sound_speed_mps: float = SOUND_SPEED_MPS
    noise_std: float = 0.018
    multipath_gain: float = 0.18
    bottom_depth_m: float = 30.0
    # Gentle regional trend; the river mission spans several kilometres, so
    # the steeper laboratory-tank plane would unrealistically cross the
    # vehicle depth before the upstream turn.
    bottom_slope_n: float = 0.001
    bottom_slope_e: float = -0.001
    dropout_mask: int = 0

    @property
    def beam_vectors_body(self) -> np.ndarray:
        elevation = math.radians(self.beam_angle_deg)
        horizontal = math.sin(elevation)
        down = math.cos(elevation)
        return np.asarray(
            [
                (
                    horizontal * math.cos(math.radians(azimuth)),
                    horizontal * math.sin(math.radians(azimuth)),
                    down,
                )
                for azimuth in (45.0, 135.0, 225.0, 315.0)
            ],
            dtype=np.float64,
        )

    def transmit_pulse(self) -> np.ndarray:
        count = max(32, round(self.pulse_duration_s * self.sample_rate_hz))
        t = np.arange(count, dtype=np.float64) / self.sample_rate_hz
        chirp_rate = self.chirp_bandwidth_hz / self.pulse_duration_s
        phase = math.pi * chirp_rate * (
            t - self.pulse_duration_s / 2.0
        ) ** 2
        window = np.hanning(count)
        return window * np.exp(1j * phase)


@dataclass
class DvlBeamSolution:
    beam: int
    valid: bool
    range_m: float | None
    radial_velocity_mps: float | None
    doppler_hz: float | None
    correlation: float
    snr_db: float
    peak_sample: float | None


@dataclass
class DvlSolution:
    timestamp_tai_ns: int
    sequence: int
    valid: bool
    degraded: bool
    bottom_lock: bool
    velocity_body_mps: np.ndarray
    altitude_m: float | None
    covariance: np.ndarray
    beams: list[DvlBeamSolution]
    processing_ms: float

    def json(self) -> dict:
        return {
            "timestamp_tai_ns": self.timestamp_tai_ns,
            "sequence": self.sequence,
            "valid": self.valid,
            "degraded": self.degraded,
            "bottom_lock": self.bottom_lock,
            "velocity_body_mps": np.round(self.velocity_body_mps, 5).tolist(),
            "speed_mps": float(np.linalg.norm(self.velocity_body_mps)),
            "altitude_m": self.altitude_m,
            "covariance": np.round(self.covariance, 7).tolist(),
            "processing_ms": self.processing_ms,
            "beams": [asdict(beam) for beam in self.beams],
        }


class DvlSimulator:
    def __init__(self, config: DvlConfig, seed: int = 0xD71):
        self.config = config
        self.rng = np.random.default_rng(seed)
        self.sequence = 0

    def reset(self) -> None:
        self.sequence = 0

    def _beam_range(
        self, position_ned: np.ndarray, direction_ned: np.ndarray
    ) -> float | None:
        cfg = self.config
        numerator = (
            cfg.bottom_depth_m
            + cfg.bottom_slope_n * position_ned[0]
            + cfg.bottom_slope_e * position_ned[1]
            - position_ned[2]
        )
        denominator = (
            direction_ned[2]
            - cfg.bottom_slope_n * direction_ned[0]
            - cfg.bottom_slope_e * direction_ned[1]
        )
        if denominator <= 0.05:
            return None
        value = float(numerator / denominator)
        return value if value > 0.0 else None

    def generate(
        self, truth: VehicleTruth, clock_uncertainty_ns: int
    ) -> tuple[SensorFrame, list[dict]]:
        cfg = self.config
        pulse = cfg.transmit_pulse()
        sample_count = cfg.frame_samples
        channels = np.zeros((sample_count, 4), dtype=np.complex128)
        rotation = quat_to_matrix(truth.quaternion_body_to_ned)
        velocity_body = rotation.T @ truth.velocity_ned_mps
        metadata: list[dict] = []
        for beam_index, beam_body in enumerate(cfg.beam_vectors_body):
            beam_ned = rotation @ beam_body
            beam_range = self._beam_range(truth.position_ned_m, beam_ned)
            radial = float(np.dot(beam_body, velocity_body))
            doppler_hz = (
                2.0 * cfg.center_frequency_hz * radial / cfg.sound_speed_mps
            )
            dropped = bool(cfg.dropout_mask & (1 << beam_index))
            metadata.append(
                {
                    "beam": beam_index,
                    "range_m": beam_range,
                    "radial_velocity_mps": radial,
                    "doppler_hz": doppler_hz,
                    "dropped": dropped,
                }
            )
            if beam_range is None or dropped:
                continue
            delay = 2.0 * beam_range / cfg.sound_speed_mps
            delay_samples = int(round(delay * cfg.sample_rate_hz))
            if delay_samples + pulse.size >= sample_count:
                continue
            indices = np.arange(pulse.size, dtype=np.float64)
            echo = pulse * np.exp(
                2j * math.pi * doppler_hz * indices / cfg.sample_rate_hz
            )
            gain = 0.72 / max(1.0, beam_range / 15.0) ** 1.2
            channels[
                delay_samples : delay_samples + pulse.size, beam_index
            ] += gain * echo
            multipath_delay = delay_samples + round(0.0017 * cfg.sample_rate_hz)
            if multipath_delay + pulse.size < sample_count:
                channels[
                    multipath_delay : multipath_delay + pulse.size,
                    beam_index,
                ] += cfg.multipath_gain * gain * echo * np.exp(0.8j)
        noise = cfg.noise_std * (
            self.rng.normal(size=channels.shape)
            + 1j * self.rng.normal(size=channels.shape)
        )
        channels += noise
        frame = SensorFrame.from_ci16(
            channels,
            sequence=self.sequence,
            timestamp_tai_ns=truth.timestamp_tai_ns,
            clock_uncertainty_ns=clock_uncertainty_ns,
            sample_rate_hz=cfg.sample_rate_hz,
            center_frequency_hz=cfg.center_frequency_hz,
        )
        self.sequence += 1
        return frame, metadata


class DvlProcessor:
    def __init__(self, config: DvlConfig):
        self.config = config
        self.pulse = config.transmit_pulse()
        self.reference_energy = float(np.vdot(self.pulse, self.pulse).real)
        correlation_size = config.frame_samples + self.pulse.size - 1
        self.correlation_fft_size = 1 << (correlation_size - 1).bit_length()
        self.envelope_reference_fft = np.fft.rfft(
            np.abs(self.pulse[::-1]),
            self.correlation_fft_size,
        )

    @staticmethod
    def _parabolic_peak(values: np.ndarray, peak: int) -> float:
        if peak <= 0 or peak >= values.size - 1:
            return float(peak)
        left, center, right = (
            float(values[peak - 1]),
            float(values[peak]),
            float(values[peak + 1]),
        )
        denominator = left - 2.0 * center + right
        if abs(denominator) < 1e-18:
            return float(peak)
        return float(peak + 0.5 * (left - right) / denominator)

    def process(self, frame: SensorFrame) -> DvlSolution:
        started = time.perf_counter()
        if frame.sensor_type != SENSOR_DVL or frame.channels != 4:
            raise ValueError("DVL processor requires a four-channel DVL frame")
        if frame.sample_rate_hz != self.config.sample_rate_hz:
            raise ValueError("DVL frame sample rate does not match configuration")
        samples = frame.ci16()
        beams: list[DvlBeamSolution] = []
        valid_indices: list[int] = []
        radial_velocities: list[float] = []
        ranges: list[float] = []
        weights: list[float] = []
        for beam_index in range(4):
            channel = samples[:, beam_index]
            # A linear-FM pulse has range/Doppler coupling in a complex
            # matched filter.  Locate its amplitude envelope first so the
            # Doppler estimate cannot pull the range peak several samples.
            correlation_size = channel.size + self.pulse.size - 1
            if correlation_size <= self.correlation_fft_size:
                reference_fft = self.envelope_reference_fft
                fft_size = self.correlation_fft_size
            else:
                fft_size = 1 << (correlation_size - 1).bit_length()
                reference_fft = np.fft.rfft(
                    np.abs(self.pulse[::-1]), fft_size
                )
            correlation_envelope = np.fft.irfft(
                np.fft.rfft(np.abs(channel), fft_size) * reference_fft,
                fft_size,
            )[:correlation_size]
            magnitude = np.abs(correlation_envelope)
            peak = int(np.argmax(magnitude))
            peak_value = float(magnitude[peak])
            noise_floor = max(float(np.median(magnitude)), 1e-9)
            snr_db = 20.0 * math.log10(max(peak_value / noise_floor, 1e-9))
            channel_energy = max(float(np.vdot(channel, channel).real), 1e-9)
            correlation = peak_value / math.sqrt(
                self.reference_energy * channel_energy
            )
            fractional_peak = self._parabolic_peak(magnitude, peak)
            delay_samples = fractional_peak - (self.pulse.size - 1)
            valid = correlation >= 0.16 and snr_db >= 12.0 and delay_samples > 1
            beam_range = None
            radial = None
            doppler_hz = None
            if valid:
                beam_range = (
                    delay_samples
                    / frame.sample_rate_hz
                    * self.config.sound_speed_mps
                    / 2.0
                )
                start = int(round(delay_samples))
                stop = start + self.pulse.size
                if start < 0 or stop > samples.shape[0]:
                    valid = False
                else:
                    residual = channel[start:stop] * np.conjugate(self.pulse)
                    amplitude = np.abs(residual)
                    keep = np.abs(self.pulse) > 0.20 * float(
                        np.max(np.abs(self.pulse))
                    )
                    if np.count_nonzero(keep) < 8:
                        valid = False
                    else:
                        indices = np.arange(self.pulse.size)[keep]
                        phase = np.unwrap(np.angle(residual[keep]))
                        slope = np.polyfit(
                            indices,
                            phase,
                            1,
                            w=np.maximum(amplitude[keep], 1e-9),
                        )[0]
                        doppler_hz = float(
                            slope * frame.sample_rate_hz / (2.0 * math.pi)
                        )
                        radial = (
                            doppler_hz
                            * self.config.sound_speed_mps
                            / (2.0 * frame.center_frequency_hz)
                        )
            if valid and radial is not None and beam_range is not None:
                valid_indices.append(beam_index)
                radial_velocities.append(radial)
                ranges.append(beam_range)
                weights.append(max(correlation, 0.05) ** 2)
            beams.append(
                DvlBeamSolution(
                    beam_index,
                    valid,
                    beam_range if valid else None,
                    radial if valid else None,
                    doppler_hz if valid else None,
                    correlation,
                    snr_db,
                    delay_samples if valid else None,
                )
            )
        velocity = np.zeros(3, dtype=np.float64)
        covariance = np.eye(3, dtype=np.float64) * 999.0
        solution_valid = len(valid_indices) >= 3
        if solution_valid:
            matrix = self.config.beam_vectors_body[valid_indices]
            observations = np.asarray(radial_velocities)
            weight_matrix = np.diag(weights)
            normal = matrix.T @ weight_matrix @ matrix
            try:
                inverse_normal = np.linalg.inv(normal)
            except np.linalg.LinAlgError:
                inverse_normal = np.linalg.pinv(normal)
            covariance = inverse_normal * 0.0025
            velocity = inverse_normal @ (
                matrix.T @ weight_matrix @ observations
            )
        altitude = None
        if ranges:
            vertical_ranges = [
                ranges[index]
                * self.config.beam_vectors_body[valid_indices[index], 2]
                for index in range(len(ranges))
            ]
            altitude = float(np.median(vertical_ranges))
        return DvlSolution(
            frame.timestamp_tai_ns,
            frame.sequence,
            solution_valid,
            solution_valid and len(valid_indices) == 3,
            solution_valid,
            velocity,
            altitude,
            covariance,
            beams,
            (time.perf_counter() - started) * 1000.0,
        )


@dataclass
class NavPayload:
    mode: int
    transponder_id: int
    sequence: int
    latitude_deg: float
    longitude_deg: float
    depth_m: float
    transmit_tai_ns: int
    turnaround_ns: int = 0

    def encode(self) -> bytes:
        without_crc = NAV_PAYLOAD_NO_CRC.pack(
            NAV_MAGIC,
            NAV_VERSION,
            self.mode,
            self.transponder_id,
            self.sequence,
            round(self.latitude_deg * 1e7),
            round(self.longitude_deg * 1e7),
            round(self.depth_m * 1000.0),
            self.transmit_tai_ns,
            self.turnaround_ns,
        )
        crc = binascii.crc32(without_crc) & 0xFFFFFFFF
        return without_crc + struct.pack("<I", crc)

    @classmethod
    def decode(cls, encoded: bytes) -> "NavPayload":
        if len(encoded) != NAV_PAYLOAD.size:
            raise ValueError("navigation payload length does not match")
        values = NAV_PAYLOAD.unpack(encoded)
        if values[0] != NAV_MAGIC or values[1] != NAV_VERSION:
            raise ValueError("navigation payload magic/version does not match")
        if (binascii.crc32(encoded[:-4]) & 0xFFFFFFFF) != values[-1]:
            raise ValueError("navigation payload CRC does not match")
        return cls(
            mode=values[2],
            transponder_id=values[3],
            sequence=values[4],
            latitude_deg=values[5] / 1e7,
            longitude_deg=values[6] / 1e7,
            depth_m=values[7] / 1000.0,
            transmit_tai_ns=values[8],
            turnaround_ns=values[9],
        )


CONV_GENERATORS = (0o171, 0o133)
CONV_MEMORY = 6
CONV_STATES = 1 << CONV_MEMORY


def bytes_to_bits(payload: bytes) -> np.ndarray:
    return np.unpackbits(np.frombuffer(payload, dtype=np.uint8))


def bits_to_bytes(bits: np.ndarray) -> bytes:
    values = np.asarray(bits, dtype=np.uint8)
    if values.size % 8:
        raise ValueError("bit count is not byte aligned")
    return np.packbits(values).tobytes()


def fft_correlate_valid(signal: np.ndarray, reference: np.ndarray) -> np.ndarray:
    """Return real valid-mode correlation without requiring SciPy."""
    signal = np.asarray(signal, dtype=np.float64)
    reference = np.asarray(reference, dtype=np.float64)
    if signal.size < reference.size:
        return np.empty(0, dtype=np.float64)
    full_size = signal.size + reference.size - 1
    fft_size = 1 << (full_size - 1).bit_length()
    convolution = np.fft.irfft(
        np.fft.rfft(signal, fft_size)
        * np.fft.rfft(reference[::-1], fft_size),
        fft_size,
    )[:full_size]
    return convolution[reference.size - 1 : signal.size]


def fft_correlate_valid_complex(
    signal: np.ndarray, reference: np.ndarray
) -> np.ndarray:
    """Return complex valid-mode matched-filter output."""
    signal = np.asarray(signal)
    reference = np.asarray(reference)
    if signal.size < reference.size:
        return np.empty(0, dtype=np.complex128)
    full_size = signal.size + reference.size - 1
    fft_size = 1 << (full_size - 1).bit_length()
    convolution = np.fft.ifft(
        np.fft.fft(signal, fft_size)
        * np.fft.fft(np.conjugate(reference[::-1]), fft_size),
        fft_size,
    )[:full_size]
    return convolution[reference.size - 1 : signal.size]


def convolutional_encode(bits: np.ndarray) -> np.ndarray:
    state = 0
    output: list[int] = []
    padded = np.concatenate((np.asarray(bits, dtype=np.uint8), np.zeros(6, dtype=np.uint8)))
    for bit in padded:
        register = ((state << 1) | int(bit)) & 0x7F
        output.extend(
            int((register & generator).bit_count() & 1)
            for generator in CONV_GENERATORS
        )
        state = register & (CONV_STATES - 1)
    return np.asarray(output, dtype=np.uint8)


def convolutional_decode_hard(encoded: np.ndarray, output_bits: int) -> np.ndarray:
    values = np.asarray(encoded, dtype=np.uint8)
    if values.size % 2:
        raise ValueError("convolutional input must contain bit pairs")
    infinity = 1_000_000
    metrics = np.full(CONV_STATES, infinity, dtype=np.int32)
    metrics[0] = 0
    previous_states = np.empty((values.size // 2, CONV_STATES), dtype=np.uint8)
    next_states = np.arange(CONV_STATES, dtype=np.int32)
    input_bits = next_states & 1
    predecessor_zero = next_states >> 1
    predecessor_one = predecessor_zero + CONV_STATES // 2

    def expected_for(predecessors: np.ndarray) -> np.ndarray:
        registers = ((predecessors << 1) | input_bits) & 0x7F
        return np.asarray(
            [
                [
                    (int(register) & generator).bit_count() & 1
                    for generator in CONV_GENERATORS
                ]
                for register in registers
            ],
            dtype=np.uint8,
        )

    expected_zero = expected_for(predecessor_zero)
    expected_one = expected_for(predecessor_one)
    for step in range(values.size // 2):
        received = values[2 * step : 2 * step + 2]
        candidate_zero = metrics[predecessor_zero] + np.count_nonzero(
            expected_zero != received, axis=1
        )
        candidate_one = metrics[predecessor_one] + np.count_nonzero(
            expected_one != received, axis=1
        )
        choose_one = candidate_one < candidate_zero
        metrics = np.where(choose_one, candidate_one, candidate_zero)
        previous_states[step] = np.where(
            choose_one, predecessor_one, predecessor_zero
        )
    state = 0
    decoded = np.empty(values.size // 2, dtype=np.uint8)
    for step in range(decoded.size - 1, -1, -1):
        decoded[step] = state & 1
        state = int(previous_states[step, state])
    return decoded[:output_bits]


@dataclass
class AcousticConfig:
    waveform: str = "dedicated"
    dedicated_sample_rate_hz: int = 96_000
    dedicated_center_frequency_hz: int = 24_000
    chirp_low_hz: int = 18_000
    chirp_high_hz: int = 30_000
    chirp_duration_s: float = 0.020
    # 12 ksym/s keeps the dedicated packet inside the 18-30 kHz navigation
    # band and limits four-channel correlation to a 16384-point FFT.
    symbol_rate: int = 12_000
    array_side_m: float = 0.10
    sound_speed_mps: float = SOUND_SPEED_MPS
    noise_std: float = 0.012
    multipath_gain: float = 0.16
    clock_range_threshold_ns: int = 100_000
    packet_dropout: bool = False
    corrupt_packet: bool = False
    origin_latitude_deg: float = NAVIGATION_ORIGIN_LATITUDE_DEG
    origin_longitude_deg: float = NAVIGATION_ORIGIN_LONGITUDE_DEG

    @property
    def hydrophone_positions_body(self) -> np.ndarray:
        half = self.array_side_m / 2.0
        return np.asarray(
            (
                (-half, -half, 0.0),
                (-half, half, 0.0),
                (half, half, 0.0),
                (half, -half, 0.0),
            ),
            dtype=np.float64,
        )


@dataclass
class AcousticObservation:
    timestamp_tai_ns: int
    sequence: int
    mode: str
    waveform: str
    valid: bool
    payload_valid: bool
    range_valid: bool
    transponder_id: int | None
    transponder_ned_m: np.ndarray | None
    ranges_m: list[float]
    range_m: float | None
    bearing_body_unit: np.ndarray | None
    position_fix_ned_m: np.ndarray | None
    tdoa_us: list[float]
    peak_metrics: list[float]
    clock_uncertainty_ns: int
    error: str | None
    processing_ms: float

    def json(self) -> dict:
        return {
            "timestamp_tai_ns": self.timestamp_tai_ns,
            "sequence": self.sequence,
            "mode": self.mode,
            "waveform": self.waveform,
            "valid": self.valid,
            "payload_valid": self.payload_valid,
            "range_valid": self.range_valid,
            "transponder_id": self.transponder_id,
            "transponder_ned_m": (
                None
                if self.transponder_ned_m is None
                else np.round(self.transponder_ned_m, 5).tolist()
            ),
            "ranges_m": self.ranges_m,
            "range_m": self.range_m,
            "bearing_body_unit": (
                None
                if self.bearing_body_unit is None
                else np.round(self.bearing_body_unit, 6).tolist()
            ),
            "position_fix_ned_m": (
                None
                if self.position_fix_ned_m is None
                else np.round(self.position_fix_ned_m, 5).tolist()
            ),
            "tdoa_us": self.tdoa_us,
            "peak_metrics": self.peak_metrics,
            "clock_uncertainty_ns": self.clock_uncertainty_ns,
            "error": self.error,
            "processing_ms": self.processing_ms,
        }


class DedicatedNavWaveform:
    SYNC_BITS = np.tile(np.asarray((0, 1), dtype=np.uint8), 16)

    def __init__(self, config: AcousticConfig):
        self.config = config
        self.sample_rate_hz = config.dedicated_sample_rate_hz
        self.center_frequency_hz = config.dedicated_center_frequency_hz
        self.samples_per_symbol = self.sample_rate_hz // config.symbol_rate
        if self.samples_per_symbol * config.symbol_rate != self.sample_rate_hz:
            raise ValueError("navigation symbol rate must divide sample rate")
        self.chirp, self.chirp_complex = self._make_chirp()
        self.guard_samples = round(0.004 * self.sample_rate_hz)

    def _make_chirp(self) -> tuple[np.ndarray, np.ndarray]:
        count = round(self.config.chirp_duration_s * self.sample_rate_hz)
        t = np.arange(count, dtype=np.float64) / self.sample_rate_hz
        rate = (
            self.config.chirp_high_hz - self.config.chirp_low_hz
        ) / self.config.chirp_duration_s
        phase = 2.0 * math.pi * (
            self.config.chirp_low_hz * t + 0.5 * rate * t * t
        )
        window = np.hanning(count)
        return window * np.cos(phase), window * np.exp(1j * phase)

    def encode(self, payload: bytes) -> np.ndarray:
        coded = convolutional_encode(bytes_to_bits(payload))
        bits = np.concatenate((self.SYNC_BITS, coded))
        symbols = 1.0 - 2.0 * bits.astype(np.float64)
        envelope = np.repeat(symbols, self.samples_per_symbol)
        indices = np.arange(envelope.size, dtype=np.float64)
        carrier = np.cos(
            2.0
            * math.pi
            * self.center_frequency_hz
            * indices
            / self.sample_rate_hz
        )
        body = 0.55 * envelope * carrier
        edge = min(round(0.001 * self.sample_rate_hz), body.size // 4)
        if edge:
            ramp = np.sin(np.linspace(0.0, math.pi / 2.0, edge)) ** 2
            body[:edge] *= ramp
            body[-edge:] *= ramp[::-1]
        return np.concatenate(
            (0.75 * self.chirp, np.zeros(self.guard_samples), body)
        )

    def detect(self, samples: np.ndarray) -> tuple[float, float]:
        correlation = fft_correlate_valid_complex(
            samples, self.chirp_complex
        )
        if not correlation.size:
            raise ValueError("navigation capture is shorter than the preamble")
        magnitude = np.abs(correlation)
        peak = int(np.argmax(magnitude))
        metric = float(magnitude[peak]) / max(
            math.sqrt(
                float(np.vdot(self.chirp_complex, self.chirp_complex).real)
                * float(
                    np.vdot(
                        samples[peak : peak + self.chirp.size],
                        samples[peak : peak + self.chirp.size],
                    )
                )
            ),
            1e-12,
        )
        return DvlProcessor._parabolic_peak(magnitude, peak), metric

    def detect_channels(
        self, channels: np.ndarray
    ) -> tuple[list[float], list[float]]:
        """Detect the preamble on all hydrophones with one batched FFT."""
        values = np.asarray(channels, dtype=np.float64)
        if values.ndim != 2 or values.shape[0] < self.chirp_complex.size:
            raise ValueError("navigation capture is shorter than the preamble")
        full_size = values.shape[0] + self.chirp_complex.size - 1
        fft_size = 1 << (full_size - 1).bit_length()
        reference_fft = np.fft.fft(
            np.conjugate(self.chirp_complex[::-1]), fft_size
        )
        convolution = np.fft.ifft(
            np.fft.fft(values, fft_size, axis=0)
            * reference_fft[:, np.newaxis],
            axis=0,
        )[:full_size]
        correlation = convolution[
            self.chirp_complex.size - 1 : values.shape[0], :
        ]
        magnitude = np.abs(correlation)
        reference_energy = float(
            np.vdot(self.chirp_complex, self.chirp_complex).real
        )
        arrivals: list[float] = []
        metrics: list[float] = []
        for index in range(values.shape[1]):
            beam_magnitude = magnitude[:, index]
            peak = int(np.argmax(beam_magnitude))
            window = values[peak : peak + self.chirp.size, index]
            metric = float(beam_magnitude[peak]) / max(
                math.sqrt(
                    reference_energy * float(np.vdot(window, window))
                ),
                1e-12,
            )
            arrivals.append(
                DvlProcessor._parabolic_peak(beam_magnitude, peak)
            )
            metrics.append(metric)
        return arrivals, metrics

    def decode(self, samples: np.ndarray, arrival_sample: float) -> bytes:
        coded_bits = (NAV_PAYLOAD.size * 8 + CONV_MEMORY) * 2
        symbol_count = self.SYNC_BITS.size + coded_bits
        start = (
            int(round(arrival_sample))
            + self.chirp.size
            + self.guard_samples
        )
        stop = start + symbol_count * self.samples_per_symbol
        if start < 0 or stop > samples.size:
            raise ValueError("navigation payload is truncated")
        body = samples[start:stop].reshape(
            symbol_count, self.samples_per_symbol
        )
        absolute_indices = np.arange(
            start, stop, dtype=np.float64
        ).reshape(symbol_count, self.samples_per_symbol)
        oscillator = np.exp(
            -2j
            * math.pi
            * self.center_frequency_hz
            * absolute_indices
            / self.sample_rate_hz
        )
        symbols = np.sum(body * oscillator, axis=1)
        known = 1.0 - 2.0 * self.SYNC_BITS
        phase = np.angle(np.sum(symbols[: self.SYNC_BITS.size] * known))
        decisions = (
            np.real(symbols[self.SYNC_BITS.size :] * np.exp(-1j * phase)) < 0
        ).astype(np.uint8)
        decoded = convolutional_decode_hard(
            decisions, NAV_PAYLOAD.size * 8
        )
        return bits_to_bytes(decoded)


class AcousticSimulator:
    def __init__(self, config: AcousticConfig, seed: int = 0xAC05):
        self.config = config
        self.rng = np.random.default_rng(seed)
        self.sequence = 0
        self.dedicated = DedicatedNavWaveform(config)

    def reset(self) -> None:
        self.sequence = 0

    @staticmethod
    def _fractional_delay(signal: np.ndarray, delay: float, length: int) -> np.ndarray:
        output = np.zeros(length, dtype=np.float64)
        destination = np.arange(length, dtype=np.float64) - delay
        source = np.arange(signal.size, dtype=np.float64)
        output[:] = np.interp(destination, source, signal, left=0.0, right=0.0)
        return output

    def _ofdm_waveform(
        self, payload: bytes
    ) -> tuple[np.ndarray, int, int, int]:
        try:
            from cora_ofdm_acoustic import encode_packet, make_acoustic_config
        except ImportError as error:
            raise RuntimeError("OFDM navigation transport is not installed") from error
        cfg = make_acoustic_config(
            "v3",
            1500.0,
            15000.0,
            modulation="qpsk",
        )
        packet = encode_packet(payload, sequence=self.sequence, cfg=cfg)
        # The packet ramp attenuates the first repeated training symbol.  The
        # second training symbol is therefore the stable OFDM ranging epoch.
        active_offset = (
            round(cfg.leading_silence_s * cfg.sample_rate) + cfg.symbol_len
        )
        return (
            np.asarray(packet.samples, dtype=np.float64),
            cfg.sample_rate,
            8250,
            active_offset,
        )

    def generate(
        self,
        truth: VehicleTruth,
        transponder_ned_m: np.ndarray,
        transponder_id: int,
        mode: int,
        clock: ClockStatus,
        *,
        waveform: str | None = None,
        turnaround_ns: int = 0,
    ) -> tuple[SensorFrame, NavPayload, dict]:
        cfg = self.config
        waveform = waveform or cfg.waveform
        relative_ned = np.asarray(transponder_ned_m) - truth.position_ned_m
        range_m = float(np.linalg.norm(relative_ned))
        unit_ned = relative_ned / max(range_m, 1e-9)
        rotation = quat_to_matrix(truth.quaternion_body_to_ned)
        unit_body = rotation.T @ unit_ned
        latitude, longitude, depth = ned_to_wgs84(
            np.asarray(transponder_ned_m),
            cfg.origin_latitude_deg,
            cfg.origin_longitude_deg,
        )
        transmit_tai_ns = int(
            truth.timestamp_tai_ns
            - range_m / cfg.sound_speed_mps * 1e9
        )
        payload = NavPayload(
            mode,
            transponder_id,
            self.sequence,
            latitude,
            longitude,
            depth,
            transmit_tai_ns,
            turnaround_ns,
        )
        encoded_payload = payload.encode()
        if cfg.corrupt_packet:
            damaged = bytearray(encoded_payload)
            damaged[len(damaged) // 2] ^= 0x40
            encoded_payload = bytes(damaged)
        if waveform == "dedicated":
            transmitted = self.dedicated.encode(encoded_payload)
            sample_rate = self.dedicated.sample_rate_hz
            center_frequency = self.dedicated.center_frequency_hz
            active_offset = 0
        elif waveform == "ofdm":
            (
                transmitted,
                sample_rate,
                center_frequency,
                active_offset,
            ) = self._ofdm_waveform(encoded_payload)
        else:
            raise ValueError("navigation waveform must be dedicated or ofdm")
        base_delay = range_m / cfg.sound_speed_mps * sample_rate
        aperture_delays = (
            -(cfg.hydrophone_positions_body @ unit_body)
            / cfg.sound_speed_mps
            * sample_rate
        )
        leading = round(0.010 * sample_rate)
        delays = leading + aperture_delays - float(np.min(aperture_delays))
        length = (
            int(math.ceil(float(np.max(delays))))
            + transmitted.size
            + round(0.030 * sample_rate)
        )
        channels = np.zeros((length, 4), dtype=np.float64)
        if not cfg.packet_dropout:
            for index, delay in enumerate(delays):
                # Model front-end AGC over the kilometre-scale park mission
                # while preserving geometric spreading beyond that range.
                gain = (1.0 - 0.05 * index) / max(1.0, range_m / 1000.0)
                channels[:, index] += gain * self._fractional_delay(
                    transmitted, delay, length
                )
                multipath_delay = delay + 0.0035 * sample_rate
                channels[:, index] += cfg.multipath_gain * gain * self._fractional_delay(
                    transmitted, multipath_delay, length
                )
        channels += self.rng.normal(0.0, cfg.noise_std, channels.shape)
        frame_start_tai_ns = int(
            transmit_tai_ns
            + (base_delay - leading - active_offset) / sample_rate * 1e9
        )
        frame = SensorFrame.from_s16(
            channels,
            sequence=self.sequence,
            timestamp_tai_ns=frame_start_tai_ns,
            clock_uncertainty_ns=round(clock.uncertainty_ns),
            sample_rate_hz=sample_rate,
            center_frequency_hz=center_frequency,
        )
        metadata = {
            "truth_range_m": range_m,
            "truth_unit_body": unit_body.tolist(),
            "base_delay_samples": base_delay,
            "channel_delays": delays.tolist(),
            "frame_start_tai_ns": frame_start_tai_ns,
            "waveform": waveform,
        }
        self.sequence += 1
        return frame, payload, metadata


class AcousticProcessor:
    def __init__(self, config: AcousticConfig):
        self.config = config
        self.dedicated = DedicatedNavWaveform(config)

    @staticmethod
    def _ofdm_reference() -> tuple[object, np.ndarray]:
        from cora_ofdm_acoustic import (
            _grid_to_symbol,
            _training_grids,
            make_acoustic_config,
        )

        cfg = make_acoustic_config(
            "v3", 1500.0, 15000.0, modulation="qpsk"
        )
        reference = _grid_to_symbol(cfg, _training_grids(cfg)[0])
        return cfg, np.asarray(reference, dtype=np.float64)

    def _detect_ofdm(self, channel: np.ndarray) -> tuple[float, float]:
        _, reference = self._ofdm_reference()
        correlation = np.abs(fft_correlate_valid(channel, reference))
        if not correlation.size:
            raise ValueError("OFDM capture is shorter than its training symbol")
        maximum = float(np.max(correlation))
        candidates = np.flatnonzero(correlation >= maximum * 0.75)
        peak = int(candidates[0]) if candidates.size else int(np.argmax(correlation))
        window = channel[peak : peak + reference.size]
        metric = float(correlation[peak]) / max(
            math.sqrt(
                float(np.vdot(reference, reference))
                * float(np.vdot(window, window))
            ),
            1e-12,
        )
        return DvlProcessor._parabolic_peak(correlation, peak), metric

    def _decode_ofdm(self, channel: np.ndarray) -> bytes:
        from cora_ofdm_acoustic import decode_packet

        cfg, _ = self._ofdm_reference()
        result = decode_packet(channel.astype(np.float64) / 32768.0, cfg=cfg)
        if not result.valid:
            raise ValueError(result.error or "OFDM navigation payload failed")
        return result.payload

    def process(
        self,
        frame: SensorFrame,
        vehicle_quaternion: np.ndarray,
        *,
        waveform: str,
        vehicle_position_ned_m: np.ndarray | None = None,
        interrogation_tai_ns: int | None = None,
    ) -> AcousticObservation:
        started = time.perf_counter()
        if frame.sensor_type != SENSOR_HYDROPHONE or frame.channels != 4:
            raise ValueError(
                "acoustic processor requires a four-channel hydrophone frame"
            )
        channels = frame.s16().astype(np.float64) / 32768.0
        if waveform == "dedicated":
            arrivals, metrics = self.dedicated.detect_channels(channels)
        elif waveform == "ofdm":
            arrivals = []
            metrics = []
            for index in range(4):
                arrival, metric = self._detect_ofdm(channels[:, index])
                arrivals.append(arrival)
                metrics.append(metric)
        else:
            raise ValueError("navigation waveform must be dedicated or ofdm")
        best = int(np.argmax(metrics))
        payload = None
        payload_error = None
        for candidate in np.argsort(metrics)[::-1]:
            candidate = int(candidate)
            try:
                if metrics[candidate] < 0.12:
                    raise ValueError("navigation preamble was not detected")
                if waveform == "dedicated":
                    encoded = self.dedicated.decode(
                        channels[:, candidate], arrivals[candidate]
                    )
                else:
                    encoded = self._decode_ofdm(channels[:, candidate])
                payload = NavPayload.decode(encoded)
                best = candidate
                payload_error = None
                break
            except (RuntimeError, ValueError) as error:
                payload_error = str(error)
        tdoa = (
            (np.asarray(arrivals) - arrivals[0])
            / frame.sample_rate_hz
        )
        positions = self.config.hydrophone_positions_body
        matrix = positions[1:, :2] - positions[0, :2]
        rhs = -self.config.sound_speed_mps * tdoa[1:]
        horizontal = np.linalg.lstsq(matrix, rhs, rcond=None)[0]
        horizontal_norm = float(np.linalg.norm(horizontal))
        if horizontal_norm > 1.0:
            horizontal /= horizontal_norm
            horizontal_norm = 1.0
        down = -math.sqrt(max(0.0, 1.0 - horizontal_norm**2))
        bearing_body = np.asarray(
            (horizontal[0], horizontal[1], down), dtype=np.float64
        )
        transponder_ned = None
        range_m = None
        range_valid = False
        position_fix = None
        ranges: list[float] = []
        if payload is not None:
            transponder_ned = wgs84_to_ned(
                payload.latitude_deg,
                payload.longitude_deg,
                payload.depth_m,
                self.config.origin_latitude_deg,
                self.config.origin_longitude_deg,
            )
            arrival_tai_ns = (
                frame.timestamp_tai_ns
                + arrivals[best] / frame.sample_rate_hz * 1e9
            )
            if payload.mode == NAV_MODE_LBL and interrogation_tai_ns is not None:
                range_m = (
                    (
                        arrival_tai_ns
                        - interrogation_tai_ns
                        - payload.turnaround_ns
                    )
                    * 1e-9
                    * self.config.sound_speed_mps
                    / 2.0
                )
                range_valid = range_m > 0.0
            elif payload.mode == NAV_MODE_IUSBL:
                range_m = (
                    (arrival_tai_ns - payload.transmit_tai_ns)
                    * 1e-9
                    * self.config.sound_speed_mps
                )
                range_valid = (
                    range_m > 0.0
                    and frame.clock_uncertainty_ns
                    <= self.config.clock_range_threshold_ns
                )
                if range_valid:
                    rotation = quat_to_matrix(vehicle_quaternion)
                    if (
                        vehicle_position_ned_m is not None
                        and abs(float(rotation[2, 2])) > 0.1
                    ):
                        desired_down = (
                            transponder_ned[2]
                            - float(vehicle_position_ned_m[2])
                        ) / max(range_m, 1e-9)
                        bearing_body[2] = (
                            desired_down
                            - float(rotation[2, 0]) * bearing_body[0]
                            - float(rotation[2, 1]) * bearing_body[1]
                        ) / float(rotation[2, 2])
                        bearing_body = bearing_body / max(
                            float(np.linalg.norm(bearing_body)), 1e-9
                        )
                    direction_ned = rotation @ bearing_body
                    position_fix = transponder_ned - range_m * direction_ned
            if range_m is not None:
                ranges.append(float(range_m))
        mode = (
            "lbl"
            if payload is not None and payload.mode == NAV_MODE_LBL
            else "iusbl"
        )
        valid = payload is not None and (
            (mode == "lbl" and range_valid)
            or (mode == "iusbl" and min(metrics) >= 0.12)
        )
        return AcousticObservation(
            frame.timestamp_tai_ns,
            frame.sequence,
            mode,
            waveform,
            valid,
            payload is not None,
            range_valid,
            None if payload is None else payload.transponder_id,
            transponder_ned,
            ranges,
            range_m,
            bearing_body,
            position_fix,
            [float(value * 1e6) for value in tdoa],
            metrics,
            frame.clock_uncertainty_ns,
            payload_error,
            (time.perf_counter() - started) * 1000.0,
        )


@dataclass
class NavigationSolution:
    timestamp_tai_ns: int
    position_ned_m: np.ndarray
    velocity_ned_mps: np.ndarray
    quaternion_body_to_ned: np.ndarray
    gyro_bias_rps: np.ndarray
    accel_bias_mps2: np.ndarray
    covariance: np.ndarray
    initialized: bool = True

    def json(self, origin_latitude: float, origin_longitude: float) -> dict:
        roll, pitch, yaw = quat_to_euler(self.quaternion_body_to_ned)
        latitude, longitude, depth = ned_to_wgs84(
            self.position_ned_m, origin_latitude, origin_longitude
        )
        horizontal_cov = self.covariance[:2, :2]
        eigenvalues = np.linalg.eigvalsh(horizontal_cov)
        return {
            "timestamp_tai_ns": self.timestamp_tai_ns,
            "position_ned_m": np.round(self.position_ned_m, 5).tolist(),
            "velocity_ned_mps": np.round(self.velocity_ned_mps, 5).tolist(),
            "speed_mps": float(np.linalg.norm(self.velocity_ned_mps)),
            "latitude_deg": latitude,
            "longitude_deg": longitude,
            "depth_m": depth,
            "roll_deg": math.degrees(roll),
            "pitch_deg": math.degrees(pitch),
            "heading_deg": math.degrees(yaw) % 360.0,
            "horizontal_sigma_m": float(
                math.sqrt(max(float(eigenvalues[-1]), 0.0))
            ),
            "horizontal_covariance_m2": np.round(
                horizontal_cov, 6
            ).tolist(),
            "position_sigma_m": np.sqrt(
                np.maximum(np.diag(self.covariance[:3, :3]), 0.0)
            ).tolist(),
            "initialized": self.initialized,
        }


class NavigationEstimator:
    """Small 15-error-state INS with DVL, pressure, heading, and acoustic aid."""

    def __init__(self):
        self.reset()

    def reset(self, timestamp_tai_ns: int = 0) -> None:
        mission_start = SUSQUEHANNA_MISSION_WAYPOINTS_NED_M[0]
        self.solution = NavigationSolution(
            timestamp_tai_ns,
            np.asarray(
                (mission_start[0] + 5.0, mission_start[1] - 4.0, 17.0),
                dtype=np.float64,
            ),
            np.zeros(3, dtype=np.float64),
            quat_from_euler(0.0, 0.0, math.radians(5.0)),
            np.zeros(3, dtype=np.float64),
            np.zeros(3, dtype=np.float64),
            np.diag(
                [
                    25.0,
                    25.0,
                    4.0,
                    1.0,
                    1.0,
                    0.5,
                    0.03,
                    0.03,
                    0.08,
                    1e-5,
                    1e-5,
                    1e-5,
                    0.02,
                    0.02,
                    0.02,
                ]
            ),
        )

    def propagate(
        self,
        timestamp_tai_ns: int,
        gyro_rps: np.ndarray,
        accel_mps2: np.ndarray,
        dt: float,
    ) -> None:
        state = self.solution
        corrected_gyro = np.asarray(gyro_rps) - state.gyro_bias_rps
        corrected_accel = np.asarray(accel_mps2) - state.accel_bias_mps2
        state.quaternion_body_to_ned = quat_normalize(
            quat_multiply(
                state.quaternion_body_to_ned,
                quat_from_delta(corrected_gyro * dt),
            )
        )
        acceleration_ned = (
            quat_to_matrix(state.quaternion_body_to_ned) @ corrected_accel
            + GRAVITY_NED
        )
        state.position_ned_m += state.velocity_ned_mps * dt + 0.5 * acceleration_ned * dt * dt
        state.velocity_ned_mps += acceleration_ned * dt
        state.timestamp_tai_ns = timestamp_tai_ns
        # F is identity except for the position/velocity block.  Applying
        # F P F^T by block avoids two tiny general matrix multiplies, which
        # are disproportionately expensive in the minimal ARM NumPy build.
        covariance = state.covariance.copy()
        covariance[:3, :] += dt * state.covariance[3:6, :]
        covariance[:, :3] += dt * covariance[:, 3:6]
        process_diagonal = np.asarray(
            [1e-6] * 3
            + [2e-4] * 3
            + [2e-5] * 3
            + [1e-9] * 3
            + [2e-7] * 3
        ) * dt
        covariance.flat[::16] += process_diagonal
        state.covariance = covariance

    def _update(
        self,
        residual: np.ndarray,
        measurement_matrix: np.ndarray,
        measurement_covariance: np.ndarray,
    ) -> None:
        covariance = self.solution.covariance
        cross_covariance = covariance @ measurement_matrix.T
        innovation = (
            measurement_matrix @ cross_covariance
            + measurement_covariance
        )
        if innovation.shape == (1, 1):
            gain = cross_covariance / max(float(innovation[0, 0]), 1e-12)
        else:
            try:
                gain = np.linalg.solve(
                    innovation, cross_covariance.T
                ).T
            except np.linalg.LinAlgError:
                gain = cross_covariance @ np.linalg.pinv(innovation)
        correction = gain @ residual
        self.solution.position_ned_m += correction[0:3]
        self.solution.velocity_ned_mps += correction[3:6]
        self.solution.quaternion_body_to_ned = quat_normalize(
            quat_multiply(
                self.solution.quaternion_body_to_ned,
                quat_from_delta(correction[6:9]),
            )
        )
        self.solution.gyro_bias_rps += correction[9:12]
        self.solution.accel_bias_mps2 += correction[12:15]
        updated_covariance = covariance - gain @ cross_covariance.T
        self.solution.covariance = 0.5 * (
            updated_covariance + updated_covariance.T
        )

    def update_pressure(self, depth_m: float, variance: float = 0.04) -> None:
        matrix = np.zeros((1, 15))
        matrix[0, 2] = 1.0
        self._update(
            np.asarray((depth_m - self.solution.position_ned_m[2],)),
            matrix,
            np.asarray(((variance,),)),
        )

    def update_heading(self, heading_rad: float, variance: float = 0.0025) -> None:
        _, _, current = quat_to_euler(self.solution.quaternion_body_to_ned)
        matrix = np.zeros((1, 15))
        matrix[0, 8] = 1.0
        self._update(
            np.asarray((wrap_angle(heading_rad - current),)),
            matrix,
            np.asarray(((variance,),)),
        )

    def update_dvl(self, solution: DvlSolution) -> None:
        if not solution.valid:
            return
        rotation = quat_to_matrix(self.solution.quaternion_body_to_ned)
        measured_ned = rotation @ solution.velocity_body_mps
        matrix = np.zeros((3, 15))
        matrix[:, 3:6] = np.eye(3)
        covariance = rotation @ solution.covariance @ rotation.T
        covariance += np.eye(3) * (0.0025 if not solution.degraded else 0.01)
        self._update(
            measured_ned - self.solution.velocity_ned_mps,
            matrix,
            covariance,
        )

    def update_lbl_range(
        self,
        transponder_ned_m: np.ndarray,
        range_m: float,
        variance: float = 0.25,
    ) -> None:
        delta = self.solution.position_ned_m - np.asarray(transponder_ned_m)
        predicted = float(np.linalg.norm(delta))
        if predicted < 1e-6:
            return
        matrix = np.zeros((1, 15))
        matrix[0, :3] = delta / predicted
        self._update(
            np.asarray((range_m - predicted,)),
            matrix,
            np.asarray(((variance,),)),
        )

    def update_position(
        self, position_ned_m: np.ndarray, covariance: np.ndarray
    ) -> None:
        matrix = np.zeros((3, 15))
        matrix[:, :3] = np.eye(3)
        self._update(
            np.asarray(position_ned_m) - self.solution.position_ned_m,
            matrix,
            np.asarray(covariance),
        )


@dataclass
class NavigationConfig:
    dvl: DvlConfig = field(default_factory=DvlConfig)
    acoustic: AcousticConfig = field(default_factory=AcousticConfig)
    simulation_rate_hz: float = 100.0
    history_rate_hz: float = 10.0
    acoustic_rate_hz: float = 1.0
    acoustic_mode: str = "lbl"
    waveform: str = "dedicated"
    seed: int = 0xC04A
    raw_recording: bool = False


class NavigationEngine:
    """Run both sensor processors and one fused navigation solution."""

    def __init__(
        self,
        config: NavigationConfig | None = None,
        *,
        run_directory: Path = Path("/run/cora-navigation"),
    ):
        self.config = config or NavigationConfig()
        self.run_directory = Path(run_directory)
        self.lock = threading.RLock()
        self.world = VehicleSimulator(self.config.seed)
        self.clock = SimulatedClock(seed=self.config.seed + 1)
        self.dvl_simulator = DvlSimulator(self.config.dvl, self.config.seed + 2)
        self.dvl_processor = DvlProcessor(self.config.dvl)
        self.acoustic_simulator = AcousticSimulator(
            self.config.acoustic, self.config.seed + 3
        )
        self.acoustic_processor = AcousticProcessor(self.config.acoustic)
        self.estimator = NavigationEstimator()
        self.running = False
        self.paused = False
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.process_context = multiprocessing.get_context("fork")
        self.worker_stop_event = self.process_context.Event()
        self.original_affinity: set[int] | None = None
        self.control_cpu: int | None = None
        self.worker_cpu: int | None = None
        self.dvl_process: multiprocessing.Process | None = None
        self.acoustic_process: multiprocessing.Process | None = None
        self.dvl_queue = self.process_context.Queue(maxsize=1)
        self.dvl_result_queue = self.process_context.Queue(maxsize=2)
        self.acoustic_queue = self.process_context.Queue(maxsize=1)
        self.acoustic_result_queue = self.process_context.Queue(maxsize=2)
        self.record_lock = threading.Lock()
        self.history: deque[dict] = deque(maxlen=1200)
        self.history_id = 0
        self.latest_truth: VehicleTruth | None = None
        self.latest_dvl: DvlSolution | None = None
        self.latest_acoustic: AcousticObservation | None = None
        self.latest_clock: ClockStatus | None = None
        self.dvl_truth: list[dict] = []
        self.stats = {
            "steps": 0,
            "deadline_misses": 0,
            "late_wakeups": 0,
            "dvl_frames": 0,
            "acoustic_frames": 0,
            "dvl_invalid": 0,
            "acoustic_invalid": 0,
            "dvl_queue_drops": 0,
            "acoustic_queue_drops": 0,
            "maximum_step_ms": 0.0,
            "mean_step_ms": 0.0,
        }
        self.gyro_bias = np.asarray((0.0004, -0.0003, 0.0005))
        self.accel_bias = np.asarray((0.008, -0.006, 0.012))
        south_center = np.mean(
            (
                SUSQUEHANNA_MISSION_WAYPOINTS_NED_M[0],
                SUSQUEHANNA_MISSION_WAYPOINTS_NED_M[-1],
            ),
            axis=0,
        )
        north_center = np.mean(
            (
                SUSQUEHANNA_MISSION_WAYPOINTS_NED_M[
                    len(SUSQUEHANNA_CENTERLINE_WGS84) - 1
                ],
                SUSQUEHANNA_MISSION_WAYPOINTS_NED_M[
                    len(SUSQUEHANNA_CENTERLINE_WGS84)
                ],
            ),
            axis=0,
        )
        river_axis = north_center - south_center
        river_axis /= np.linalg.norm(river_axis)
        cross_river = np.asarray((-river_axis[1], river_axis[0]))
        first_transponder = south_center + 250.0 * cross_river
        second_transponder = north_center - 250.0 * cross_river
        self.lbl_transponders = (
            np.asarray(
                (first_transponder[0], first_transponder[1], 28.0)
            ),
            np.asarray(
                (second_transponder[0], second_transponder[1], 28.0)
            ),
        )
        self.iusbl_transponder = np.asarray((0.0, 0.0, 0.5))
        self.lbl_index = 0
        self.raw_recorder: FrameRecorder | None = None
        self.telemetry_path = self.run_directory / "telemetry.jsonl"
        self.telemetry_output = None

    def reset(self) -> None:
        with self.lock:
            self.world.reset()
            self.clock.reset()
            self.dvl_simulator.reset()
            self.acoustic_simulator.reset()
            self.estimator.reset()
            self.history.clear()
            self.history_id = 0
            self.latest_truth = None
            self.latest_dvl = None
            self.latest_acoustic = None
            self.dvl_truth = []
            self.lbl_index = 0
            for work_queue in (
                self.dvl_queue,
                self.dvl_result_queue,
                self.acoustic_queue,
                self.acoustic_result_queue,
            ):
                while True:
                    try:
                        work_queue.get_nowait()
                    except queue.Empty:
                        break
            for key in self.stats:
                self.stats[key] = 0 if "mean" not in key and "maximum" not in key else 0.0
            self.run_directory.mkdir(parents=True, exist_ok=True)
            if self.telemetry_output is not None:
                self.telemetry_output.close()
                self.telemetry_output = None
            self.telemetry_path.unlink(missing_ok=True)

    def start(self) -> None:
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                self.paused = False
                return
            self.stop_event.clear()
            self.worker_stop_event.clear()
            self.paused = False
            self.running = True
            self.run_directory.mkdir(parents=True, exist_ok=True)
            if self.config.raw_recording:
                self.raw_recorder = FrameRecorder(
                    self.run_directory / "raw-frames.csf"
                )
                self.raw_recorder.open()
            self.thread = threading.Thread(
                target=self._run, name="cora-navigation", daemon=True
            )
            try:
                available_cpus = sorted(os.sched_getaffinity(0))
            except AttributeError:
                available_cpus = []
            if len(available_cpus) >= 2:
                self.original_affinity = set(available_cpus)
                self.control_cpu = available_cpus[0]
                self.worker_cpu = available_cpus[1]
            self.dvl_process = self.process_context.Process(
                target=self._dvl_process_run,
                name="cora-navigation-dvl",
                daemon=True,
            )
            self.acoustic_process = self.process_context.Process(
                target=self._acoustic_process_run,
                name="cora-navigation-acoustic",
                daemon=True,
            )
            self.dvl_process.start()
            self.acoustic_process.start()
            if self.control_cpu is not None:
                os.sched_setaffinity(0, {self.control_cpu})
            self.telemetry_output = self.telemetry_path.open(
                "a",
                encoding="utf-8",
                buffering=1,
            )
            self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.worker_stop_event.set()
        for work_queue in (self.dvl_queue, self.acoustic_queue):
            try:
                work_queue.put_nowait(None)
            except queue.Full:
                pass
        thread = self.thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        for process in (self.dvl_process, self.acoustic_process):
            if process is None:
                continue
            process.join(timeout=3.0)
            if process.is_alive():
                process.terminate()
                process.join(timeout=1.0)
        with self.lock:
            self.running = False
            self.thread = None
            self.dvl_process = None
            self.acoustic_process = None
            if self.raw_recorder is not None:
                self.raw_recorder.close()
                self.raw_recorder = None
            if self.telemetry_output is not None:
                self.telemetry_output.close()
                self.telemetry_output = None
        if self.original_affinity is not None:
            os.sched_setaffinity(0, self.original_affinity)
        self.original_affinity = None
        self.control_cpu = None
        self.worker_cpu = None

    def set_paused(self, paused: bool) -> None:
        with self.lock:
            self.paused = paused

    def configure(self, values: dict) -> None:
        with self.lock:
            if "acoustic_mode" in values:
                mode = str(values["acoustic_mode"]).lower()
                if mode not in ("lbl", "iusbl"):
                    raise ValueError("acoustic_mode must be lbl or iusbl")
                self.config.acoustic_mode = mode
            if "waveform" in values:
                waveform = str(values["waveform"]).lower()
                if waveform not in ("dedicated", "ofdm"):
                    raise ValueError("waveform must be dedicated or ofdm")
                self.config.waveform = waveform
            numeric = {
                "dvl_noise": (self.config.dvl, "noise_std", 0.0, 0.5),
                "acoustic_noise": (
                    self.config.acoustic,
                    "noise_std",
                    0.0,
                    0.5,
                ),
                "multipath_gain": (
                    self.config.acoustic,
                    "multipath_gain",
                    0.0,
                    0.8,
                ),
                "clock_drift_ppm": (self.clock, "drift_ppm", -100.0, 100.0),
                "clock_uncertainty_ns": (
                    self.clock,
                    "uncertainty_ns",
                    0.0,
                    10_000_000.0,
                ),
                "array_side_m": (
                    self.config.acoustic,
                    "array_side_m",
                    0.01,
                    2.0,
                ),
                "speed_mps": (self.world, "speed_mps", 0.0, 3.0),
                "depth_m": (self.world, "depth_m", 1.0, 29.0),
            }
            for key, (target, attribute, low, high) in numeric.items():
                if key in values:
                    value = float(values[key])
                    if not math.isfinite(value) or not low <= value <= high:
                        raise ValueError(f"{key} must be between {low} and {high}")
                    setattr(target, attribute, value)
            if "dvl_dropout_mask" in values:
                mask = int(values["dvl_dropout_mask"])
                if mask < 0 or mask > 15:
                    raise ValueError("dvl_dropout_mask must be between 0 and 15")
                self.config.dvl.dropout_mask = mask
            for key, target, attribute in (
                ("packet_dropout", self.config.acoustic, "packet_dropout"),
                ("corrupt_packet", self.config.acoustic, "corrupt_packet"),
                ("clock_locked", self.clock, "locked"),
            ):
                if key in values:
                    setattr(target, attribute, bool(values[key]))

    def _record_frame(self, frame: SensorFrame) -> None:
        with self.record_lock:
            if self.raw_recorder is not None:
                self.raw_recorder.write(frame)

    def _compute_dvl(
        self,
        truth: VehicleTruth,
        clock_uncertainty_ns: int,
    ) -> tuple[DvlSolution, list[dict], SensorFrame]:
        frame, metadata = self.dvl_simulator.generate(
            truth, clock_uncertainty_ns
        )
        solution = self.dvl_processor.process(
            SensorFrame.from_bytes(frame.to_bytes())
        )
        return solution, metadata, frame

    def _generate_dvl(
        self,
        truth: VehicleTruth,
        clock_uncertainty_ns: int,
    ) -> tuple[DvlSolution, list[dict]]:
        solution, metadata, frame = self._compute_dvl(
            truth, clock_uncertainty_ns
        )
        self._record_frame(frame)
        return solution, metadata

    def _apply_dvl(
        self,
        solution: DvlSolution,
        metadata: list[dict],
    ) -> None:
        self.latest_dvl = solution
        self.dvl_truth = metadata
        self.estimator.update_dvl(solution)
        self.stats["dvl_frames"] += 1
        if not solution.valid:
            self.stats["dvl_invalid"] += 1

    def _dvl_process_run(self) -> None:
        self.raw_recorder = None
        if self.worker_cpu is not None:
            os.sched_setaffinity(0, {self.worker_cpu})
        try:
            os.nice(5)
        except OSError:
            pass
        while not self.worker_stop_event.is_set():
            try:
                item = self.dvl_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if item is None:
                break
            truth, clock_uncertainty_ns, settings, record_frame = item
            try:
                for attribute, value in settings.items():
                    setattr(self.config.dvl, attribute, value)
                solution, metadata, frame = self._compute_dvl(
                    truth, clock_uncertainty_ns
                )
            except Exception as error:
                print(f"DVL navigation worker: {error}", flush=True)
                continue
            self.dvl_result_queue.put(
                (
                    solution,
                    metadata,
                    frame.to_bytes() if record_frame else None,
                )
            )

    def _compute_acoustic(
        self,
        truth: VehicleTruth,
        clock: ClockStatus,
        vehicle_quaternion: np.ndarray | None = None,
        vehicle_position_ned_m: np.ndarray | None = None,
    ) -> tuple[AcousticObservation, SensorFrame]:
        if vehicle_quaternion is None:
            vehicle_quaternion = (
                self.estimator.solution.quaternion_body_to_ned.copy()
            )
        if vehicle_position_ned_m is None:
            vehicle_position_ned_m = (
                self.estimator.solution.position_ned_m.copy()
            )
        mode = self.config.acoustic_mode
        if mode == "lbl":
            transponder_id = self.lbl_index + 1
            transponder = self.lbl_transponders[self.lbl_index]
            self.lbl_index = (self.lbl_index + 1) % len(self.lbl_transponders)
            turnaround_ns = 50_000_000
            distance = float(np.linalg.norm(transponder - truth.position_ned_m))
            interrogation_tai_ns = int(
                truth.timestamp_tai_ns
                - (2.0 * distance / SOUND_SPEED_MPS) * 1e9
                - turnaround_ns
            )
            frame, _, _ = self.acoustic_simulator.generate(
                truth,
                transponder,
                transponder_id,
                NAV_MODE_LBL,
                clock,
                waveform=self.config.waveform,
                turnaround_ns=turnaround_ns,
            )
            observation = self.acoustic_processor.process(
                SensorFrame.from_bytes(frame.to_bytes()),
                vehicle_quaternion,
                waveform=self.config.waveform,
                vehicle_position_ned_m=vehicle_position_ned_m,
                interrogation_tai_ns=interrogation_tai_ns,
            )
            return observation, frame
        frame, _, _ = self.acoustic_simulator.generate(
            truth,
            self.iusbl_transponder,
            100,
            NAV_MODE_IUSBL,
            clock,
            waveform=self.config.waveform,
        )
        observation = self.acoustic_processor.process(
            SensorFrame.from_bytes(frame.to_bytes()),
            vehicle_quaternion,
            waveform=self.config.waveform,
            vehicle_position_ned_m=vehicle_position_ned_m,
        )
        return observation, frame

    def _generate_acoustic(
        self,
        truth: VehicleTruth,
        clock: ClockStatus,
        vehicle_quaternion: np.ndarray | None = None,
        vehicle_position_ned_m: np.ndarray | None = None,
    ) -> AcousticObservation:
        observation, frame = self._compute_acoustic(
            truth,
            clock,
            vehicle_quaternion,
            vehicle_position_ned_m,
        )
        self._record_frame(frame)
        return observation

    def _apply_acoustic(self, observation: AcousticObservation) -> None:
        self.latest_acoustic = observation
        self.stats["acoustic_frames"] += 1
        if not observation.valid:
            self.stats["acoustic_invalid"] += 1
        elif (
            observation.mode == "lbl"
            and observation.range_valid
            and observation.transponder_ned_m is not None
            and observation.range_m is not None
        ):
            self.estimator.update_lbl_range(
                observation.transponder_ned_m,
                observation.range_m,
            )
        elif (
            observation.mode == "iusbl"
            and observation.position_fix_ned_m is not None
            and observation.range_valid
        ):
            self.estimator.update_position(
                observation.position_fix_ned_m,
                np.diag((0.5, 0.5, 1.0)),
            )

    def _acoustic_process_run(self) -> None:
        self.raw_recorder = None
        if self.worker_cpu is not None:
            os.sched_setaffinity(0, {self.worker_cpu})
        try:
            os.nice(5)
        except OSError:
            pass
        while not self.worker_stop_event.is_set():
            try:
                item = self.acoustic_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if item is None:
                break
            (
                truth,
                clock,
                quaternion,
                position,
                settings,
                record_frame,
            ) = item
            try:
                self.config.acoustic_mode = settings.pop("acoustic_mode")
                self.config.waveform = settings.pop("waveform")
                for attribute, value in settings.items():
                    setattr(self.config.acoustic, attribute, value)
                observation, frame = self._compute_acoustic(
                    truth,
                    clock,
                    quaternion,
                    position,
                )
            except Exception as error:
                print(f"acoustic navigation worker: {error}", flush=True)
                continue
            self.acoustic_result_queue.put(
                (
                    observation,
                    frame.to_bytes() if record_frame else None,
                )
            )

    def _drain_sensor_results(self) -> None:
        while True:
            try:
                solution, metadata, frame_bytes = (
                    self.dvl_result_queue.get_nowait()
                )
            except queue.Empty:
                break
            if frame_bytes is not None:
                self._record_frame(SensorFrame.from_bytes(frame_bytes))
            with self.lock:
                self._apply_dvl(solution, metadata)
        while True:
            try:
                observation, frame_bytes = (
                    self.acoustic_result_queue.get_nowait()
                )
            except queue.Empty:
                break
            if frame_bytes is not None:
                self._record_frame(SensorFrame.from_bytes(frame_bytes))
            with self.lock:
                self._apply_acoustic(observation)

    def _snapshot(self) -> dict:
        assert self.latest_truth is not None
        assert self.latest_clock is not None
        truth = self.latest_truth.json()
        ins = self.estimator.solution.json(
            self.config.acoustic.origin_latitude_deg,
            self.config.acoustic.origin_longitude_deg,
        )
        position_error = (
            self.estimator.solution.position_ned_m
            - self.latest_truth.position_ned_m
        )
        velocity_error = (
            self.estimator.solution.velocity_ned_mps
            - self.latest_truth.velocity_ned_mps
        )
        return {
            "schema_version": 1,
            "application": "cora-navigation",
            "status": (
                "paused" if self.paused else "running" if self.running else "idle"
            ),
            "timestamp_tai_ns": self.latest_clock.tai_ns,
            "simulation": {
                "rate_hz": self.config.simulation_rate_hz,
                "acoustic_mode": self.config.acoustic_mode,
                "waveform": self.config.waveform,
                "speed_mps": self.world.speed_mps,
                "commanded_depth_m": self.world.depth_m,
                "seed": self.config.seed,
            },
            "clock": asdict(self.latest_clock),
            "truth": truth,
            "ins": ins,
            "errors": {
                "position_ned_m": np.round(position_error, 5).tolist(),
                "position_norm_m": float(np.linalg.norm(position_error)),
                "velocity_ned_mps": np.round(velocity_error, 5).tolist(),
                "velocity_norm_mps": float(np.linalg.norm(velocity_error)),
            },
            "dvl": None if self.latest_dvl is None else self.latest_dvl.json(),
            "dvl_truth": self.dvl_truth,
            "acoustic": (
                None
                if self.latest_acoustic is None
                else self.latest_acoustic.json()
            ),
            "transponders": {
                "lbl": [value.tolist() for value in self.lbl_transponders],
                "iusbl": self.iusbl_transponder.tolist(),
            },
            "mission": {
                "waypoints_ned_m": [
                    [float(value[0]), float(value[1]), float(self.world.depth_m)]
                    for value in self.world.waypoints
                ],
                "active_waypoint": int(self.latest_truth.waypoint_index),
            },
            "array": {
                "side_m": self.config.acoustic.array_side_m,
                "positions_body_m": self.config.acoustic.hydrophone_positions_body.tolist(),
            },
            "faults": {
                "dvl_dropout_mask": self.config.dvl.dropout_mask,
                "packet_dropout": self.config.acoustic.packet_dropout,
                "corrupt_packet": self.config.acoustic.corrupt_packet,
                "dvl_noise": self.config.dvl.noise_std,
                "acoustic_noise": self.config.acoustic.noise_std,
                "multipath_gain": self.config.acoustic.multipath_gain,
            },
            "stats": self.stats.copy(),
        }

    def _append_history(self) -> None:
        assert self.latest_truth is not None
        assert self.latest_clock is not None
        solution = self.estimator.solution
        _, _, heading = quat_to_euler(solution.quaternion_body_to_ned)
        horizontal_covariance = solution.covariance[:2, :2]
        covariance_delta = float(
            horizontal_covariance[0, 0] - horizontal_covariance[1, 1]
        )
        maximum_eigenvalue = 0.5 * (
            float(horizontal_covariance[0, 0] + horizontal_covariance[1, 1])
            + math.sqrt(
                covariance_delta * covariance_delta
                + 4.0 * float(horizontal_covariance[0, 1]) ** 2
            )
        )
        compact = {
            "id": self.history_id,
            "timestamp_tai_ns": self.latest_clock.tai_ns,
            "truth_position_ned_m": np.round(
                self.latest_truth.position_ned_m, 5
            ).tolist(),
            "ins_position_ned_m": np.round(
                solution.position_ned_m, 5
            ).tolist(),
            "ins_velocity_ned_mps": np.round(
                solution.velocity_ned_mps, 5
            ).tolist(),
            "position_error_m": float(
                np.linalg.norm(
                    solution.position_ned_m
                    - self.latest_truth.position_ned_m
                )
            ),
            "heading_deg": math.degrees(heading) % 360.0,
            "horizontal_sigma_m": math.sqrt(
                max(maximum_eigenvalue, 0.0)
            ),
            "dvl_valid": bool(
                self.latest_dvl is not None and self.latest_dvl.valid
            ),
            "dvl_altitude_m": (
                None
                if self.latest_dvl is None
                else self.latest_dvl.altitude_m
            ),
            "acoustic_valid": bool(
                self.latest_acoustic is not None
                and self.latest_acoustic.valid
            ),
        }
        self.history.append(compact)
        self.history_id += 1
        line = json.dumps(compact, separators=(",", ":")) + "\n"
        if self.telemetry_output is not None:
            self.telemetry_output.write(line)
        else:
            with self.telemetry_path.open("a", encoding="utf-8") as output:
                output.write(line)

    def step(
        self,
        *,
        dt: float | None = None,
        generate_dvl: bool = True,
        generate_acoustic: bool = False,
        make_snapshot: bool = True,
    ) -> dict | None:
        dt = dt or 1.0 / self.config.simulation_rate_hz
        started = time.perf_counter()
        with self.lock:
            clock = self.clock.step(dt)
            truth = self.world.step(dt, clock.tai_ns)
            gyro, accel = self.world.imu(
                truth, self.gyro_bias, self.accel_bias
            )
            self.estimator.propagate(clock.tai_ns, gyro, accel, dt)
            heading_pressure_interval = max(
                1, round(self.config.simulation_rate_hz / 10.0)
            )
            if self.stats["steps"] % heading_pressure_interval == 0:
                _, _, heading = quat_to_euler(truth.quaternion_body_to_ned)
                heading += float(self.world.rng.normal(0.0, math.radians(0.35)))
                self.estimator.update_heading(heading)
                self.estimator.update_pressure(
                    truth.position_ned_m[2]
                    + float(self.world.rng.normal(0.0, 0.05))
                )
            if generate_dvl:
                solution, metadata = self._generate_dvl(
                    truth, round(clock.uncertainty_ns)
                )
                self._apply_dvl(solution, metadata)
            if generate_acoustic:
                self._apply_acoustic(self._generate_acoustic(truth, clock))
            self.latest_truth = truth
            self.latest_clock = clock
            self.stats["steps"] += 1
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            count = self.stats["steps"]
            self.stats["maximum_step_ms"] = max(
                self.stats["maximum_step_ms"], elapsed_ms
            )
            self.stats["mean_step_ms"] += (
                elapsed_ms - self.stats["mean_step_ms"]
            ) / count
            return self._snapshot() if make_snapshot else None

    def _run(self) -> None:
        dt = 1.0 / self.config.simulation_rate_hz
        dvl_interval = max(
            1, round(self.config.simulation_rate_hz / self.config.dvl.update_rate_hz)
        )
        acoustic_interval = max(
            1, round(self.config.simulation_rate_hz / self.config.acoustic_rate_hz)
        )
        history_interval = max(
            1, round(self.config.simulation_rate_hz / self.config.history_rate_hz)
        )
        step_index = 0
        deadline = time.monotonic()
        while not self.stop_event.is_set():
            if self.paused:
                time.sleep(0.05)
                deadline = time.monotonic() + dt
                continue
            generate_dvl = step_index % dvl_interval == 0
            self.step(
                dt=dt,
                generate_dvl=False,
                generate_acoustic=False,
                make_snapshot=False,
            )
            self._drain_sensor_results()
            with self.lock:
                if generate_dvl:
                    assert self.latest_truth is not None
                    assert self.latest_clock is not None
                    item = (
                        self.latest_truth,
                        round(self.latest_clock.uncertainty_ns),
                        {
                            "noise_std": self.config.dvl.noise_std,
                            "multipath_gain": self.config.dvl.multipath_gain,
                            "dropout_mask": self.config.dvl.dropout_mask,
                        },
                        self.config.raw_recording,
                    )
                    try:
                        self.dvl_queue.put_nowait(item)
                    except queue.Full:
                        self.stats["dvl_queue_drops"] += 1
                if step_index % acoustic_interval == 0:
                    assert self.latest_truth is not None
                    assert self.latest_clock is not None
                    item = (
                        self.latest_truth,
                        self.latest_clock,
                        self.estimator.solution.quaternion_body_to_ned.copy(),
                        self.estimator.solution.position_ned_m.copy(),
                        {
                            "acoustic_mode": self.config.acoustic_mode,
                            "waveform": self.config.waveform,
                            "noise_std": self.config.acoustic.noise_std,
                            "multipath_gain": self.config.acoustic.multipath_gain,
                            "array_side_m": self.config.acoustic.array_side_m,
                            "packet_dropout": self.config.acoustic.packet_dropout,
                            "corrupt_packet": self.config.acoustic.corrupt_packet,
                        },
                        self.config.raw_recording,
                    )
                    try:
                        self.acoustic_queue.put_nowait(item)
                    except queue.Full:
                        self.stats["acoustic_queue_drops"] += 1
                if step_index % history_interval == 0:
                    self._append_history()
            step_index += 1
            deadline += dt
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)
            else:
                with self.lock:
                    self.stats["late_wakeups"] += 1
                    missed_periods = int(max(0.0, -remaining) // dt)
                    self.stats["deadline_misses"] += missed_periods
                if missed_periods:
                    deadline += missed_periods * dt
        with self.lock:
            self.running = False

    def status(self) -> dict:
        with self.lock:
            if self.latest_truth is None:
                return {
                    "schema_version": 1,
                    "application": "cora-navigation",
                    "status": "idle",
                    "simulation": {
                        "acoustic_mode": self.config.acoustic_mode,
                        "waveform": self.config.waveform,
                    },
                    "stats": self.stats.copy(),
                }
            return self._snapshot()

    def history_after(self, cursor: int) -> dict:
        with self.lock:
            oldest = self.history[0]["id"] if self.history else self.history_id
            latest = self.history_id - 1
            reset = cursor > latest or (
                cursor >= 0 and self.history and cursor < oldest - 1
            )
            values = (
                list(self.history)
                if reset
                else [value for value in self.history if value["id"] > cursor]
            )
            return {
                "schema_version": 1,
                "latest_id": latest,
                "reset": reset,
                "history": values,
            }
