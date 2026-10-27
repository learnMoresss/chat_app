/// 国内粗粒度逆地理：腾讯额度满 / 外网不通时，至少给出区片名。
class PlaceApprox {
  PlaceApprox._();

  static final _coordRe = RegExp(r'^-?\d+\.\d+\s*,\s*-?\d+\.\d+$');

  static bool looksLikeCoords(String? s) {
    final t = (s ?? '').trim();
    return t.isEmpty || _coordRe.hasMatch(t);
  }

  static bool needsName(String? name, String? address) {
    final n = (name ?? '').trim();
    if (n.isEmpty || n == '选中位置' || n == '位置') return true;
    if (looksLikeCoords(n)) return true;
    if (looksLikeCoords(address) && (n == '选中位置' || n == '位置')) return true;
    return false;
  }

  /// (name, address)；未命中返回 null。
  static ({String name, String address})? reverse(double lat, double lng) {
    const boxes = <(String, String, double, double, double, double)>[
      ('周浦/康桥一带', '上海市浦东新区周浦镇与康桥镇交界', 31.145, 31.175, 121.545, 121.575),
      ('周浦镇', '上海市浦东新区周浦镇', 31.100, 31.175, 121.540, 121.610),
      ('康桥镇', '上海市浦东新区康桥镇', 31.120, 31.165, 121.545, 121.600),
      ('北蔡镇', '上海市浦东新区北蔡镇', 31.175, 31.210, 121.500, 121.560),
      ('张江', '上海市浦东新区张江镇', 31.185, 31.230, 121.560, 121.630),
      ('江川路/颛桥一带', '上海市闵行区江川路街道与颛桥镇交界', 31.015, 31.060, 121.380, 121.440),
      ('颛桥镇', '上海市闵行区颛桥镇', 31.040, 31.090, 121.370, 121.430),
      ('莘庄', '上海市闵行区莘庄镇', 31.090, 31.140, 121.350, 121.410),
      ('吴泾', '上海市闵行区吴泾镇', 31.000, 31.060, 121.440, 121.500),
      ('梅陇', '上海市闵行区梅陇', 31.070, 31.120, 121.390, 121.450),
      ('人民广场', '上海市黄浦区人民广场', 31.220, 31.240, 121.460, 121.490),
      ('徐家汇', '上海市徐汇区徐家汇', 31.180, 31.205, 121.420, 121.450),
      ('闵行区', '上海市闵行区', 31.000, 31.220, 121.250, 121.480),
      ('浦东新区', '上海市浦东新区', 30.880, 31.380, 121.450, 121.950),
      ('黄浦区', '上海市黄浦区', 31.205, 31.245, 121.450, 121.510),
      ('徐汇区', '上海市徐汇区', 31.140, 31.220, 121.400, 121.470),
      ('上海市', '上海市', 30.650, 31.900, 120.850, 122.000),
      ('北京市', '北京市', 39.400, 41.100, 115.400, 117.600),
      ('广州市', '广东省广州市', 22.800, 23.600, 112.900, 113.800),
      ('深圳市', '广东省深圳市', 22.400, 22.900, 113.700, 114.700),
      ('杭州市', '浙江省杭州市', 29.900, 30.600, 119.800, 120.600),
      ('南京市', '江苏省南京市', 31.700, 32.400, 118.400, 119.200),
      ('成都市', '四川省成都市', 30.400, 31.000, 103.700, 104.500),
    ];
    for (final b in boxes) {
      if (lat >= b.$3 && lat <= b.$4 && lng >= b.$5 && lng <= b.$6) {
        return (name: b.$1, address: b.$2);
      }
    }
    return null;
  }

  static ({String name, String address}) resolve({
    required double lat,
    required double lng,
    String? name,
    String? address,
  }) {
    if (!needsName(name, address)) {
      return (name: name!.trim(), address: (address ?? '').trim().isEmpty ? name.trim() : address!.trim());
    }
    final hit = reverse(lat, lng);
    if (hit != null) return hit;
    return (
      name: '选中位置',
      address: '${lat.toStringAsFixed(5)}, ${lng.toStringAsFixed(5)}',
    );
  }
}
