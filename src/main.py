import argparse
import copy
import dataclasses
import functools
import itertools
import json
import os
from typing import (
    cast,
    Sequence,
)
import plotly.graph_objects as go
import numpy as np
import numpy.typing as npt
import scipy.ndimage


@dataclasses.dataclass
class QubitResponseConfig:
    binarize_threshold_sigma_plus: float
    binarize_threshold_sigma_minus: float
    top_power: float
    f01_height_min: float
    f01_moment_thresholds: Sequence[float]
    f12_distance_min: int
    f12_distance_max: int
    f12_height_min: float

    def __post_init__(self):
        self._validate()

    def _validate(self):
        if self.binarize_threshold_sigma_plus <= 0:
            raise ValueError('binarize_thresholds_sigma_plus must be positive')

        if self.binarize_threshold_sigma_minus >= 0:
            raise ValueError('binarize_thresholds_sigma_minus must be negative')

        if self.f01_height_min <= 0:
            raise ValueError('f01_height_min must be > 0')

        if len(self.f01_moment_thresholds) == 0 or any(
            b <= a for a, b in itertools.pairwise(self.f01_moment_thresholds)
        ):
            raise ValueError('f01_moment_thresholds must be strictly increasing')

        if self.f12_distance_min < 0 or self.f12_distance_min > self.f12_distance_max:
            raise ValueError('bad f12 distance range')

        if self.f12_height_min <= 0:
            raise ValueError('f12_height_min must be > 0')


class QubitResponse:
    def __init__(
        self,
        xs: Sequence[float],
        ys: Sequence[float],
        zs: Sequence[Sequence[float]],
        config: QubitResponseConfig,
    ):
        self.xs = np.asarray(xs, dtype=np.float64)
        self.ys = np.asarray(ys, dtype=np.float64)
        self.zs = np.asarray(zs, dtype=np.float64)
        self.config = config

        self._validate_input()

    @functools.cached_property
    def zs_labeled(self) -> npt.NDArray[np.int32]:
        _zs = self.standardize(self.zs)
        _zs = self.binarize(
            _zs,
            self.config.binarize_threshold_sigma_plus,
            self.config.binarize_threshold_sigma_minus,
        )
        _zs = self.remove_noise(_zs)
        return _zs

    @functools.cached_property
    def f01(self):
        idx_max_height = np.argmax(self.heights)
        max_height = self.heights[idx_max_height]
        max_height_db = self.heights_db[idx_max_height]

        if max_height_db < self.config.f01_height_min:
            return None

        idx_y = len(self.zs) - max_height

        candidates = np.where(np.asarray(self.heights) == max_height)[0]
        idx_max = np.argmax(np.abs(self.zs[idx_y, candidates]))
        idx_x = candidates[idx_max]

        frequency = cast(float, self.xs[idx_x])
        label = cast(int, self.zs_labeled[idx_y, idx_x])
        moment = self.compute_moment(
            self.zs, self.zs_labeled, self.levers, self.y_diffs, label
        )
        quality_level = np.searchsorted(
            self.config.f01_moment_thresholds, moment, side='left'
        )
        quality_level = int(quality_level)
        return QubitResponse.F01(
            idx_x=idx_x,
            idx_y=idx_y,
            frequency=frequency,
            label=label,
            moment=moment,
            quality_level=quality_level,
        )

    @functools.cached_property
    def f12(self):
        if self.f01 is None:
            return None

        peaks = [
            peak
            for peak in self.peaks
            if self.config.f12_distance_min
            <= self.f01.idx_x - peak.x_end + 1
            <= self.config.f12_distance_max
            and peak.height_db >= self.config.f12_height_min
        ]

        if not peaks:
            return None

        max_height = max(peak.height for peak in peaks)
        peaks = [peak for peak in peaks if peak.height == max_height]
        peak = max(peaks, key=lambda p: p.x_end)

        idx_y = self.zs.shape[0] - peak.height
        idx_x = np.argmax(abs(self.zs[idx_y][peak.x_start : peak.x_end])) + peak.x_start
        frequency = cast(float, self.xs[idx_x])

        return QubitResponse.F12(
            idx_x=cast(int, idx_x),
            idx_y=idx_y,
            frequency=frequency,
        )

    @functools.cached_property
    def peaks(self):
        _peaks: list[QubitResponse.Peak] = []
        x_start: int | None = None

        heights = zip(self.heights, self.heights_db)
        heights = itertools.chain([(0, 0)], heights, [(0, 0)])
        for x, ((height_prev, height_db_prev), (height, _)) in enumerate(
            itertools.pairwise(heights)
        ):
            if height > height_prev:
                x_start = x

            if height < height_prev and x_start is not None:
                _peaks.append(
                    QubitResponse.Peak(x_start, x, height_prev, height_db_prev)
                )
                x_start = None

        return _peaks

    @functools.cached_property
    def heights(self) -> npt.NDArray[np.int64]:
        m = self.zs_labeled != 0
        first = np.argmax(m, axis=0)
        all_false = ~m.any(axis=0)
        first[all_false] = self.zs_labeled.shape[0]
        return self.zs_labeled.shape[0] - first

    @functools.cached_property
    def heights_db(self) -> npt.NDArray[np.float64]:
        h_map = np.append(0.0, self.config.top_power - self.ys[::-1])
        return h_map[self.heights]

    @functools.cached_property
    def levers(self) -> npt.NDArray[np.float64]:
        return self.config.top_power - self.ys

    @functools.cached_property
    def y_diffs(self) -> npt.NDArray[np.float64]:
        return np.diff(np.append(self.ys, self.config.top_power))

    def _validate_input(self):
        if self.zs.ndim != 2:
            raise ValueError(f'zs must be 2D, got {self.zs.ndim}D')
        if self.zs.shape != (len(self.ys), len(self.xs)):
            raise ValueError(
                f'shape mismatch: zs{self.zs.shape} vs (len(ys),len(xs))={(len(self.ys),len(self.xs))}'
            )
        if not np.all(np.isfinite(self.zs)):
            raise ValueError('zs contains NaN/Inf')
        if len(self.xs) < 2 or len(self.ys) < 2:
            raise ValueError('xs/ys too short')
        if np.any(np.diff(self.xs) <= 0):
            raise ValueError('xs must be strictly increasing')
        if np.any(np.diff(self.ys) <= 0):
            raise ValueError('ys must be strictly increasing')
        if self.config.top_power <= np.max(self.ys):
            raise ValueError(
                '`top_power` must be greater than the maximum value of ys.'
            )

    @staticmethod
    def compute_moment(
        zs: npt.NDArray[np.float64],
        zs_labeled: npt.NDArray[np.int32],
        levers: npt.NDArray[np.float64],
        y_diffs: npt.NDArray[np.float64],
        label: int,
    ) -> float:
        indices_y, indices_x = np.where(zs_labeled == label)
        weights = np.abs(zs[indices_y, indices_x])
        return float(np.sum(weights * levers[indices_y] * y_diffs[indices_y]))

    @staticmethod
    def standardize(zs: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
        std = zs.std()
        if std < 1e-12:
            raise ValueError('degenerate std')
        return (zs - zs.mean()) / std

    @staticmethod
    def binarize(
        zs: npt.NDArray[np.float64],
        threshold_plus: float,
        threshold_minus: float,
    ) -> npt.NDArray[np.int32]:
        return np.where((zs > threshold_plus) | (zs < threshold_minus), 1, 0).astype(
            np.int32, copy=False
        )

    @staticmethod
    def remove_noise(zs: npt.NDArray[np.int32]):
        result = scipy.ndimage.label(zs)
        labeled, _ = cast(tuple[npt.NDArray[np.int32], int], result)
        objects = scipy.ndimage.find_objects(labeled)
        valid_labels = [
            i + 1
            for i, obj in enumerate(objects)
            if obj is not None and obj[0].stop == zs.shape[0]
        ]
        return labeled * np.isin(labeled, valid_labels).astype(np.int32)

    @dataclasses.dataclass
    class F01:
        idx_x: int
        idx_y: int
        frequency: float
        label: int
        moment: float
        quality_level: int

    @dataclasses.dataclass
    class F12:
        idx_x: int
        idx_y: int
        frequency: float

    @dataclasses.dataclass
    class Peak:
        x_start: int
        x_end: int
        height: int
        height_db: float


def create_figure(data, zs=None):
    if zs is not None:
        data = copy.deepcopy(data)
        data['data'][0]['z'] = zs.tolist()

    return go.Figure(**data)


def process_data(data, conf, image_dir_base=None, plot=False, json_output=False):
    try:
        qubit_response = QubitResponse(
            data['data'][0]['x'], data['data'][0]['y'], data['data'][0]['z'], conf
        )
        f01 = qubit_response.f01
        f12 = qubit_response.f12
    except Exception as e:
        if json_output:
            result = {
                'f01_frequency': None,
                'f12_frequency': None,
                'quality_level': None,
                'status': 'ERROR',
                'error': str(e),
            }
            print(json.dumps(result))
            return
        raise

    if image_dir_base or plot:
        fig = create_figure(data)

        if f01:
            fig.add_vline(
                x=f01.frequency,
                line_width=1,
                line_color='red',
                line_dash='dash',
            )

        if f12:
            fig.add_vline(
                x=f12.frequency,
                line_width=1,
                line_color='purple',
                line_dash='dash',
            )

        if image_dir_base:
            if f01:
                moment = int(f01.moment)
                quality_level = f01.quality_level
            else:
                moment = 0
                quality_level = 0

            image_dir = os.path.join(image_dir_base, str(quality_level))
            os.makedirs(image_dir, exist_ok=True)
            qubit_idx = data['layout']['title']['text'][-3:]

            fig.write_image(
                os.path.join(image_dir, f'qubit_{qubit_idx}_{moment:06}.png')
            )

            fig.write_image(
                os.path.join(image_dir_base, f'qubit_{qubit_idx}_0_marked.png')
            )

            fig1 = create_figure(data)
            fig1.write_image(
                os.path.join(image_dir_base, f'qubit_{qubit_idx}_1_orig.png')
            )

            fig2 = create_figure(data, qubit_response.zs_labeled)
            fig2.write_image(
                os.path.join(image_dir_base, f'qubit_{qubit_idx}_2_binarize.png')
            )

        if plot:
            fig.show()

    if json_output:
        if f01:
            f01_freq = f01.frequency
            quality_level = f01.quality_level
        else:
            f01_freq = None
            quality_level = 0

        if f12:
            f12_freq = f12.frequency
        else:
            f12_freq = None

        result = {
            'f01_frequency': f01_freq,
            'f12_frequency': f12_freq,
            'quality_level': quality_level,
            'status': 'OK',
            'error': None,
        }
        print(json.dumps(result))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-f', '--input-file', required=True)
    parser.add_argument('-c', '--conf-file', required=True)
    parser.add_argument('--image-dir')
    parser.add_argument('--plot', action='store_true')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()

    with open(args.conf_file) as f:
        conf = QubitResponseConfig(**json.load(f))

    with open(args.input_file) as f:
        data = json.load(f)

    process_data(data, conf, args.image_dir, args.plot, args.json)


if __name__ == '__main__':
    main()
