"""400 米跑道几何 —— 校园跑模式用它把"跑了多少米"换算成经纬度。

跑道形状用标准 400 m 参数化：
    两条直道各 84.39 m，两端各一个半径 36.80 m 的半圆
    单圈 = 2 × 84.39 + 2π × 36.80 = 400.00 m

坐标基准是电子科技大学清水河校区田径场（取自 OSM 的 leisure=track 轮廓，way 808264789）：
    中心 30.7462577, 103.9292042；长轴相对正东 -77.82°（近似南北向）
"""

from __future__ import annotations

import math
from dataclasses import dataclass

M_PER_DEG_LAT = 111320.0   # 纬度 1 度对应的米数（地球近似）
LANE_WIDTH_M = 1.22        # 标准跑道道宽


# --------------------------------------------------------------------------- #
# 配速解析
# --------------------------------------------------------------------------- #
def parse_pace(text: str) -> float:
    """把配速字符串换算成"每公里多少秒"。

    支持三种写法：
        "6:00"  → 360  （6 分 00 秒每公里）
        "6:30"  → 390
        "6"     → 360  （纯数字按分钟理解）
        "360"   → 360  （超过 60 的数字按秒理解）
    """
    text = text.strip()
    if ":" in text:
        minutes, _, seconds = text.partition(":")
        return int(minutes) * 60 + float(seconds or 0)
    value = float(text)
    return value * 60 if value <= 60 else value


def format_pace(seconds_per_km: float) -> str:
    minutes, seconds = divmod(seconds_per_km, 60)
    return f"{int(minutes)}:{seconds:04.1f}"


# --------------------------------------------------------------------------- #
# 跑道
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Track:
    """一条标准 400 m 跑道。"""

    center_lat: float
    center_lon: float
    bearing_deg: float = 0.0     # 长轴朝向，相对正东，逆时针为正
    straight_m: float = 84.39
    radius_m: float = 36.80
    lane: int = 1

    @property
    def lap_length(self) -> float:
        return 2 * self.straight_m + 2 * math.pi * self.radius_m

    @property
    def radius_for_lane(self) -> float:
        """第 N 道比第一道半径大 (N-1)×1.22 m。"""
        return self.radius_m + max(self.lane - 1, 0) * LANE_WIDTH_M

    @property
    def lap_length_for_lane(self) -> float:
        return 2 * self.straight_m + 2 * math.pi * self.radius_for_lane

    def local_xy(self, distance_m: float) -> tuple[float, float]:
        """起点前进 distance_m 米后的局部坐标（长轴为 x，逆时针绕行）。"""
        L = self.straight_m
        R = self.radius_for_lane
        curve = math.pi * R
        s = distance_m % self.lap_length_for_lane

        if s < L:                                  # 上直道：向 +x
            return -L / 2 + s, R
        s -= L
        if s < curve:                              # 右弯
            angle = math.pi / 2 - s / R
            return L / 2 + R * math.cos(angle), R * math.sin(angle)
        s -= curve
        if s < L:                                  # 下直道：向 -x
            return L / 2 - s, -R
        s -= L
        angle = -math.pi / 2 - s / R               # 左弯
        return -L / 2 + R * math.cos(angle), R * math.sin(angle)

    def position(self, distance_m: float) -> tuple[float, float]:
        """起点前进 distance_m 米后的 (lat, lon)。"""
        x, y = self.local_xy(distance_m)
        rad = math.radians(self.bearing_deg)
        cos_r, sin_r = math.cos(rad), math.sin(rad)
        east = x * cos_r - y * sin_r
        north = x * sin_r + y * cos_r
        lat = self.center_lat + north / M_PER_DEG_LAT
        lon = self.center_lon + east / (M_PER_DEG_LAT * math.cos(math.radians(self.center_lat)))
        return lat, lon


#: 电子科技大学清水河校区田径场（OSM way 808264789 拟合出的参数）
UESTC_TRACK = Track(
    center_lat=30.7462577,
    center_lon=103.9292042,
    bearing_deg=-77.82,
)


if __name__ == "__main__":
    # 自检：确认单圈长度、配速换算、轨迹是否闭合
    print(f"单圈长度：{UESTC_TRACK.lap_length:.2f} m")
    print(f"第 1 道：{UESTC_TRACK.lap_length_for_lane:.2f} m")
    print(f"6:00/km 配速：{parse_pace('6:00'):.0f} 秒/公里")
    speed = 1000 / parse_pace("6:00")
    print(f"速度：{speed:.3f} m/s，单圈 {UESTC_TRACK.lap_length / speed:.1f} 秒")

    lat0, lon0 = UESTC_TRACK.position(0)
    lat_end, lon_end = UESTC_TRACK.position(UESTC_TRACK.lap_length)
    print(f"起点 {lat0:.6f}, {lon0:.6f}")
    print(f"闭合检查 {lat_end:.6f}, {lon_end:.6f}（应与起点一致）")

    # 均分一圈采样，看轨迹是否落在合理范围
    samples = [UESTC_TRACK.position(UESTC_TRACK.lap_length * i / 8) for i in range(8)]
    for index, (lat, lon) in enumerate(samples):
        print(f"  {index}/8 圈: {lat:.6f}, {lon:.6f}")
