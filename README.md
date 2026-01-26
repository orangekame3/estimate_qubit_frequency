## 処理の流れ

1. phase shiftの値を標準化する
2. z < `binarize_threshold_sigma_minus`σ or z > `binarize_threshold_sigma_plus`σ の部分を1, それ以外を0として二値化する.
3. 二値化したデータを[ラベリング](https://docs.scipy.org/doc/scipy-1.17.0/reference/generated/scipy.ndimage.label.html)する.
4. 上端と繋がっていない孤立した要素をノイズとみなして取り除く. 今は上下左右の4近傍で接続性を見ている.
5. 最も高い山の高さを検出する, その高さが`f01_height_min`未満の場合はf01該当なしとする. 山の高さは`top_power`を基準に計算する. 例えば`top_power`が0dBで山が-20dBまで伸びているとき, 山の高さは20dBになる.
6. 山の最も高い部分の周波数をf01とする, 複数ある場合はphase shiftの絶対値が一番大きい部分を採用する.
7. f01を含む山の一次モーメントを計算(`top_power`と各部分のパワーの差を腕の長さ, phase shiftの絶対値を1dBあたりの質量とみなす)し, その値と`f01_moment_thresholds`から`quality_level`を算出する.
8. f01から低周波方向に走査し, `f12_distance_min` ~ `f12_distance_max` の距離(GHz)にあり, `f12_height_min` 以上の高さの山の中で最も周波数の高い山をf12とする. そのような山が存在しない場合はf12該当なしとする. 山の高さは`top_power`を基準に計算する.

## `quality_level`について

f01の山の一次モーメントをm, `f01_moment_thresholds`を`[0.1, 1750.0, 3600.0, 5000.0, 15000.0]`とした場合, 以下のように`quality_level`を算出する.

```
quality_level = 0:           m <=     0.1
quality_level = 1:     0.1 < m <=  1750.0
quality_level = 2:  1750.0 < m <=  3600.0
quality_level = 3:  3600.0 < m <=  5000.0
quality_level = 4:  5000.0 < m <= 15000.0
quality_level = 5: 15000.0 < m
```

## 設定ファイル

- `binarize_threshold_sigma_(plus/minus)`: phase shift値を二値化する際に使う閾値の(+/-)側.
- `top_power`: 山の高さやモーメントの値の計算時に基準とするパワーの値(dB). グラフの上端のパワーの値とすることを推奨.
- `f01_height_min`: f01として採用されるのに必要な最低限の山の高さ(dB).
- `f01_moment_thresholds`: quality_levelの算出に使う閾値. 詳しくは[`quality_level`について](#quality_levelについて)を参照.
- `f12_distance_(min/max)`: f12が存在するはずのf01からの距離範囲(GHz).
- `f12_height_min`: f12として採用されるのに必要な最低限の山の高さ(dB).

## インストール

```
uv sync
```

## 実行

```
cp examples/config/config_example.json ./config.json
uv run src/main.py -c config.json -f /path/to/data.json --json
```

main.pyの出力オプション(複数可)

```
--image-dir <image_dir>: <image_dir>に検出結果画像・元画像・二値化画像を出力する.
--plot: 検出結果をブラウザで表示する.
--json: 検出結果をjsonで出力する(詳しくは以下参照). 
```

`--json`指定時の出力例

検出成功時

```
{
  "f01_frequency": 8.029999999999967,
  "f12_frequency": 7.849999999999971,
  "quality_level": 5,
  "status": "OK",
  "error": null
}
```

f01, f12該当なしの時

```
{
  "f01_frequency": null,
  "f12_frequency": null,
  "quality_level": 0
  "status": "OK",
  "error": null
}
```

例外発生時

```
{
  "f01_frequency": null,
  "f12_frequency": null,
  "quality_level": null
  "status": "ERROR",
  "error": "error message"
}
```
