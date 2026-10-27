import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:geolocator/geolocator.dart';
import 'package:http/http.dart' as http;
import 'package:latlong2/latlong.dart';

import '../api/api_client.dart';
import '../place_approx.dart';
import '../store.dart';
import '../theme.dart';

/// 选点发位置：国内底图 + 拖选；腾讯额度满时用免费逆地理兜底，拖图必更新。
class LocationPickerPage extends StatefulWidget {
  const LocationPickerPage({super.key, required this.store});

  final AppStore store;

  @override
  State<LocationPickerPage> createState() => _LocationPickerPageState();
}

class _LocationPickerPageState extends State<LocationPickerPage> {
  static const _shanghai = LatLng(31.2304, 121.4737);

  final _map = MapController();
  LatLng _center = _shanghai;
  String _name = '选中位置';
  String _address = '';
  bool _locating = true;
  bool _revLoading = false;
  bool _nearbyLoading = false;
  bool _tencentQuotaDone = false;
  String _nearbyHint = '拖地图选点后点发送';
  List<Map<String, dynamic>> _nearby = const [];
  int? _selectedNearby;
  Timer? _debounce;
  bool _nearbyLoadedOnce = false;
  int _revGen = 0;

  ApiClient? get _api => widget.store.api;

  @override
  void initState() {
    super.initState();
    unawaited(_bootstrap());
  }

  @override
  void dispose() {
    _debounce?.cancel();
    super.dispose();
  }

  bool _looksLikeQuota(Object e) {
    final s = '$e';
    return s.contains('额度') || s.contains('429') || s.contains('上限');
  }

  void _applyLocalPin(LatLng c, {String? name}) {
    _center = c;
    _selectedNearby = null;
    _name = name ?? '选中位置';
    _address = '${c.latitude.toStringAsFixed(5)}, ${c.longitude.toStringAsFixed(5)}';
  }

  Future<void> _bootstrap() async {
    setState(() => _locating = true);
    try {
      final ok = await _ensurePermission();
      LatLng c = _shanghai;
      if (ok) {
        try {
          final pos = await Geolocator.getCurrentPosition(
            locationSettings: const LocationSettings(accuracy: LocationAccuracy.medium),
          );
          c = LatLng(pos.latitude, pos.longitude);
          // 模拟器常在美国：落到上海，避免灰图/无意义点
          if (c.longitude < 70 || c.longitude > 140 || c.latitude < 15 || c.latitude > 55) {
            c = _shanghai;
          }
        } catch (_) {
          c = _shanghai;
        }
      }
      if (!mounted) return;
      _applyLocalPin(c, name: c == _shanghai ? '人民广场附近' : '当前位置');
      _map.move(_center, 15);
      setState(() => _locating = false);
      await _resolveAddress();
      await _loadNearbyOnce();
    } catch (_) {
      if (!mounted) return;
      _applyLocalPin(_shanghai, name: '人民广场附近');
      _map.move(_center, 15);
      setState(() => _locating = false);
      await _resolveAddress();
      await _loadNearbyOnce();
    }
  }

  Future<bool> _ensurePermission() async {
    var perm = await Geolocator.checkPermission();
    if (perm == LocationPermission.denied) {
      perm = await Geolocator.requestPermission();
    }
    if (perm == LocationPermission.denied || perm == LocationPermission.deniedForever) {
      return false;
    }
    return Geolocator.isLocationServiceEnabled();
  }

  void _onUserMovedMap(LatLng c) {
    setState(() => _applyLocalPin(c));
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 600), () {
      unawaited(_resolveAddress());
    });
  }

  /// 服务端逆地理（含离线区片名兜底）→ Nominatim → 本地粗匹配。
  Future<void> _resolveAddress() async {
    final gen = ++_revGen;
    final lat = _center.latitude;
    final lng = _center.longitude;
    setState(() => _revLoading = true);

    Map<String, String>? hit;

    if (_api != null) {
      try {
        final j = await _api!.placeReverse(lat: lat, lng: lng);
        if (gen != _revGen || !mounted) return;
        final n = (j['name'] ?? '').toString().trim();
        final a = (j['address'] ?? '').toString().trim();
        if (n.isNotEmpty && n != '选中位置') {
          hit = {'name': n, 'address': a.isNotEmpty ? a : n};
        } else if (a.isNotEmpty && !_looksLikeCoords(a)) {
          hit = {'name': n.isNotEmpty ? n : a, 'address': a};
        }
        if (j['approx'] == true && _tencentQuotaDone == false) {
          // 服务端已走离线兜底，附近列表多半也额度满
        }
      } catch (e) {
        if (_looksLikeQuota(e)) {
          _tencentQuotaDone = true;
          _nearbyHint = '附近列表暂不可用，拖选后仍可发送；聊天问附近走联网总结';
        }
      }
    }

    hit ??= await _nominatimReverse(lat, lng);
    final approx = PlaceApprox.reverse(lat, lng);
    if (approx != null) {
      hit ??= {'name': approx.name, 'address': approx.address};
    }

    if (gen != _revGen || !mounted) return;
    setState(() {
      _revLoading = false;
      if (hit != null) {
        _name = hit['name']!.isNotEmpty ? hit['name']! : '选中位置';
        _address = hit['address']!.isNotEmpty
            ? hit['address']!
            : '${lat.toStringAsFixed(5)}, ${lng.toStringAsFixed(5)}';
      } else {
        _name = '选中位置';
        _address = '${lat.toStringAsFixed(5)}, ${lng.toStringAsFixed(5)}';
      }
    });
  }

  bool _looksLikeCoords(String s) => PlaceApprox.looksLikeCoords(s);

  Future<Map<String, String>?> _nominatimReverse(double lat, double lng) async {
    try {
      final uri = Uri.parse(
        'https://nominatim.openstreetmap.org/reverse'
        '?format=jsonv2&lat=$lat&lon=$lng&accept-language=zh-CN',
      );
      final res = await http.get(
        uri,
        headers: {'User-Agent': 'chat_app/1.0 (location picker)'},
      ).timeout(const Duration(seconds: 8));
      if (res.statusCode != 200) return null;
      final j = jsonDecode(res.body);
      if (j is! Map) return null;
      final name = (j['name'] ?? j['display_name'] ?? '').toString();
      final display = (j['display_name'] ?? '').toString();
      final short = name.isNotEmpty && name != display ? name : display.split('，').first;
      return {
        'name': short.isNotEmpty ? short : '选中位置',
        'address': display.isNotEmpty ? display : '${lat.toStringAsFixed(5)}, ${lng.toStringAsFixed(5)}',
      };
    } catch (_) {
      return null;
    }
  }

  Future<void> _loadNearbyOnce() async {
    if (_nearbyLoadedOnce) return;
    _nearbyLoadedOnce = true;
    if (_api == null || _tencentQuotaDone) {
      if (mounted) {
        setState(() {
          _nearbyLoading = false;
          if (_tencentQuotaDone) {
            _nearbyHint = '附近列表暂不可用，拖选后仍可发送；聊天问附近走联网总结';
          }
        });
      }
      return;
    }
    setState(() => _nearbyLoading = true);
    try {
      final j = await _api!.placeNearby(lat: _center.latitude, lng: _center.longitude, pageSize: 15);
      final raw = j['items'];
      final list = <Map<String, dynamic>>[];
      if (raw is List) {
        for (final e in raw) {
          if (e is Map) list.add(Map<String, dynamic>.from(e));
        }
      }
      if (!mounted) return;
      setState(() {
        _nearby = list;
        _nearbyLoading = false;
        if (list.isEmpty) _nearbyHint = '暂无附近列表，可直接拖地图选点发送';
      });
    } catch (e) {
      if (!mounted) return;
      if (_looksLikeQuota(e)) _tencentQuotaDone = true;
      setState(() {
        _nearbyLoading = false;
        _nearbyHint = '附近列表暂不可用，拖选后仍可发送；聊天问附近走联网总结';
      });
    }
  }

  void _pickNearby(int i) {
    final p = _nearby[i];
    final lat = (p['lat'] as num?)?.toDouble();
    final lng = (p['lng'] as num?)?.toDouble();
    if (lat == null || lng == null) return;
    final c = LatLng(lat, lng);
    setState(() {
      _selectedNearby = i;
      _center = c;
      _name = (p['title'] ?? '位置').toString();
      _address = (p['address'] ?? '').toString();
    });
    _map.move(c, 17);
  }

  void _jumpShanghai() {
    _debounce?.cancel();
    setState(() => _applyLocalPin(_shanghai, name: '人民广场附近'));
    _map.move(_shanghai, 15);
    unawaited(_resolveAddress());
  }

  void _send() {
    final lat = _center.latitude;
    final lng = _center.longitude;
    final resolved = PlaceApprox.resolve(
      lat: lat,
      lng: lng,
      name: _name,
      address: _address,
    );
    Navigator.pop(context, {
      'name': resolved.name,
      'address': resolved.address,
      'lat': lat,
      'lng': lng,
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFFF7F7F7),
      appBar: AppBar(
        title: const Text('选择位置'),
        actions: [
          TextButton(
            onPressed: _locating ? null : _send,
            child: const Text('发送', style: TextStyle(color: AppColors.green, fontWeight: FontWeight.w600)),
          ),
        ],
      ),
      body: Column(
        children: [
          Expanded(
            flex: 5,
            child: Stack(
              alignment: Alignment.center,
              children: [
                FlutterMap(
                  mapController: _map,
                  options: MapOptions(
                    initialCenter: _center,
                    initialZoom: 15,
                    onMapEvent: (evt) {
                      // 拖动手势结束后再解析，保证每次移动都更新
                      if (evt is MapEventMoveEnd) {
                        _onUserMovedMap(evt.camera.center);
                      }
                    },
                  ),
                  children: [
                    TileLayer(
                      urlTemplate:
                          'https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}',
                      subdomains: const ['1', '2', '3', '4'],
                      userAgentPackageName: 'com.example.chat_app',
                      maxNativeZoom: 18,
                    ),
                  ],
                ),
                const IgnorePointer(
                  child: Icon(Icons.location_on, size: 44, color: Color(0xFFFE2C55)),
                ),
                if (_locating || _revLoading)
                  const Positioned(
                    top: 12,
                    child: Card(
                      child: Padding(
                        padding: EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                        child: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)),
                            SizedBox(width: 8),
                            Text('解析位置…', style: TextStyle(fontSize: 13)),
                          ],
                        ),
                      ),
                    ),
                  ),
                Positioned(
                  right: 12,
                  bottom: 12,
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Material(
                        color: Colors.white,
                        elevation: 2,
                        shape: const CircleBorder(),
                        child: InkWell(
                          customBorder: const CircleBorder(),
                          onTap: _jumpShanghai,
                          child: const SizedBox(
                            width: 40,
                            height: 40,
                            child: Icon(Icons.location_city, color: Color(0xFF191919), size: 22),
                          ),
                        ),
                      ),
                      const SizedBox(height: 8),
                      Material(
                        color: Colors.white,
                        elevation: 2,
                        shape: const CircleBorder(),
                        child: InkWell(
                          customBorder: const CircleBorder(),
                          onTap: () => unawaited(_bootstrap()),
                          child: const SizedBox(
                            width: 40,
                            height: 40,
                            child: Icon(Icons.my_location, color: Color(0xFF191919), size: 22),
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
          Material(
            color: Colors.white,
            child: ListTile(
              leading: const Icon(Icons.place, color: Color(0xFFFE2C55)),
              title: Text(_name, maxLines: 1, overflow: TextOverflow.ellipsis),
              subtitle: Text(
                _address.isEmpty ? '拖动地图选择位置后点发送' : _address,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontSize: 12, color: AppColors.sub),
              ),
              trailing: TextButton(
                onPressed: _send,
                child: const Text('发送到聊天', style: TextStyle(color: AppColors.green)),
              ),
            ),
          ),
          const Divider(height: 1),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 10, 16, 4),
            child: Row(
              children: [
                const Text('附近', style: TextStyle(fontWeight: FontWeight.w600)),
                const Spacer(),
                if (_nearbyLoading)
                  const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2)),
              ],
            ),
          ),
          Expanded(
            flex: 4,
            child: _nearby.isEmpty
                ? Center(
                    child: Padding(
                      padding: const EdgeInsets.all(24),
                      child: Text(
                        _nearbyLoading ? '加载附近…' : _nearbyHint,
                        style: const TextStyle(color: AppColors.sub, height: 1.4),
                        textAlign: TextAlign.center,
                      ),
                    ),
                  )
                : ListView.separated(
                    itemCount: _nearby.length,
                    separatorBuilder: (_, _) => const Divider(height: 1, indent: 56),
                    itemBuilder: (context, i) {
                      final p = _nearby[i];
                      final dist = p['distance'];
                      final selected = _selectedNearby == i;
                      return ListTile(
                        selected: selected,
                        selectedTileColor: const Color(0x1107C160),
                        leading: Icon(
                          Icons.storefront_outlined,
                          color: selected ? AppColors.green : const Color(0xFF666666),
                        ),
                        title: Text('${p['title'] ?? ''}', maxLines: 1, overflow: TextOverflow.ellipsis),
                        subtitle: Text(
                          [
                            if (dist != null) '${dist}m',
                            '${p['address'] ?? ''}',
                          ].where((e) => e.toString().isNotEmpty).join(' · '),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(fontSize: 12),
                        ),
                        onTap: () => _pickNearby(i),
                      );
                    },
                  ),
          ),
        ],
      ),
    );
  }
}
