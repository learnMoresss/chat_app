import 'dart:async';
import 'dart:io' show Platform, Process;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api/api_client.dart';
import '../theme.dart';

/// 看 AI 会话：小窗 / 全屏共用。
/// 打开监控不自动唤醒；开机/关机需用户点按钮（互斥）。
class WatchAiController extends ChangeNotifier {
  WatchAiController({
    required this.api,
    required this.deviceId,
    required this.initialViewUrl,
    this.phoneDeviceId,
    this.phoneViewUrl,
    this.deviceOn = false,
  });

  final ApiClient api;
  String deviceId;
  String initialViewUrl;
  String? phoneDeviceId;
  String? phoneViewUrl;

  String kind = 'desktop';
  String url = '';
  String activeDeviceId = '';
  String? leaseId;
  String? leaseToken;
  String? error;
  bool loading = true;
  bool creatingPhone = false;
  Uint8List? png;
  bool fullscreen = false;

  /// 当前 kind 对应设备是否已开机（running）。
  bool deviceOn;
  bool powerBusy = false;

  /// 小窗在聊天页上的位置（相对右下角的偏移，便于拖动）
  Offset pipOffset = const Offset(12, 100);

  void movePip(Offset delta, Size screen) {
    pipOffset = Offset(
      (pipOffset.dx - delta.dx).clamp(8.0, screen.width),
      (pipOffset.dy - delta.dy).clamp(80.0, screen.height),
    );
    notifyListeners();
  }

  Timer? _hb;
  Timer? _shot;
  bool _shotBusy = false;
  bool _disposed = false;

  Future<void> _syncInventory() async {
    final devices = await api.listVirtualDevices();
    Map<String, dynamic>? desk;
    Map<String, dynamic>? phone;
    Map<String, dynamic>? deskAny;
    Map<String, dynamic>? phoneAny;
    for (final raw in devices) {
      final d = Map<String, dynamic>.from(raw as Map);
      final status = (d['status'] as String?) ?? '';
      if (status == 'destroyed') continue;
      final k = (d['kind'] as String?) ?? 'desktop';
      if (k == 'android') {
        phoneAny ??= d;
        if (status == 'running') phone ??= d;
      } else {
        deskAny ??= d;
        if (status == 'running') desk ??= d;
      }
    }
    final deskRow = desk ?? deskAny;
    final phoneRow = phone ?? phoneAny;
    if (deskRow != null) {
      deviceId = deskRow['id'] as String? ?? deviceId;
      initialViewUrl = (deskRow['view_url'] as String?) ?? initialViewUrl;
    }
    if (phoneRow != null) {
      phoneDeviceId = phoneRow['id'] as String?;
      phoneViewUrl = phoneRow['view_url'] as String?;
    }
    deviceOn = kind == 'android' ? phone != null : desk != null;
  }

  /// 挂载监控：仅绑定已 running 的设备；未开机则显示空态，不 create/start。
  Future<void> attach({String kind = 'desktop'}) async {
    this.kind = kind;
    error = null;
    loading = true;
    png = null;
    creatingPhone = false;
    notifyListeners();
    await _releaseLeaseOnly();
    try {
      await _syncInventory();
      if (!deviceOn) {
        error = kind == 'android' ? '手机未开机' : '电脑未开机';
        loading = false;
        if (!_disposed) notifyListeners();
        return;
      }

      String id;
      String view;
      if (kind == 'android') {
        id = phoneDeviceId ?? '';
        view = phoneViewUrl ?? '';
        if (id.isEmpty) throw ApiException('手机未开机');
      } else {
        id = deviceId;
        view = initialViewUrl;
        if (id.isEmpty) throw ApiException('电脑未开机');
      }

      final lease = await api.acquireDeviceLease(id, role: 'view', ttlSec: 300);
      activeDeviceId = id;
      leaseId = lease['id'] as String?;
      leaseToken = lease['token'] as String?;
      url = (lease['view_url'] as String?)?.isNotEmpty == true ? lease['view_url'] as String : view;
      if (url.isEmpty) throw ApiException('无观看 URL');
      _hb = Timer.periodic(const Duration(seconds: 40), (_) async {
        final lid = leaseId;
        final tok = leaseToken;
        if (lid == null || tok == null) return;
        try {
          await api.heartbeatDeviceLease(activeDeviceId, lid, tok);
        } catch (_) {}
      });
      _startShotLoop();
      error = null;
    } catch (e) {
      error = e.toString();
      deviceOn = false;
    }
    loading = false;
    creatingPhone = false;
    if (!_disposed) notifyListeners();
  }

  /// 手动开机（当前 kind）；与关机互斥。
  Future<void> powerOn() async {
    if (powerBusy || deviceOn) return;
    powerBusy = true;
    error = null;
    loading = true;
    notifyListeners();
    try {
      await _syncInventory();
      if (kind == 'android') {
        creatingPhone = true;
        notifyListeners();
        var id = phoneDeviceId ?? '';
        if (id.isEmpty) {
          final created = await api.createVirtualDevice(label: '虚拟手机', kind: 'android');
          id = created['id'] as String? ?? '';
          phoneDeviceId = id;
          phoneViewUrl = created['view_url'] as String?;
          final status = created['status'] as String? ?? '';
          if (status == 'unavailable') {
            final reason = (created['meta'] is Map) ? (created['meta']['reason']?.toString() ?? '') : '';
            throw ApiException(reason.isEmpty ? '手机启动失败' : reason);
          }
        } else {
          final started = await api.startVirtualDevice(id);
          phoneViewUrl = (started['view_url'] as String?) ?? phoneViewUrl;
        }
      } else {
        var id = deviceId;
        if (id.isEmpty) {
          final created = await api.createVirtualDevice(label: '虚拟电脑', kind: 'desktop');
          id = created['id'] as String? ?? '';
          deviceId = id;
          initialViewUrl = created['view_url'] as String? ?? '';
          final status = created['status'] as String? ?? '';
          if (status == 'unavailable') {
            final reason = (created['meta'] is Map) ? (created['meta']['reason']?.toString() ?? '') : '';
            throw ApiException(reason.isEmpty ? '电脑启动失败' : reason);
          }
        } else {
          final started = await api.startVirtualDevice(id);
          initialViewUrl = (started['view_url'] as String?) ?? initialViewUrl;
        }
      }
      deviceOn = true;
      powerBusy = false;
      creatingPhone = false;
      await attach(kind: kind);
    } catch (e) {
      error = e.toString();
      deviceOn = false;
      loading = false;
      powerBusy = false;
      creatingPhone = false;
      if (!_disposed) notifyListeners();
    }
  }

  /// 手动关机（当前 kind）；与开机互斥。关画面轮询，并 stop 设备。
  Future<void> powerOff() async {
    if (powerBusy || !deviceOn) return;
    powerBusy = true;
    notifyListeners();
    try {
      await _releaseLeaseOnly();
      png = null;
      activeDeviceId = '';
      final id = kind == 'android' ? (phoneDeviceId ?? '') : deviceId;
      if (id.isNotEmpty) {
        await api.stopVirtualDevice(id, force: true);
      }
      deviceOn = false;
      error = kind == 'android' ? '手机已关机' : '电脑已关机';
    } catch (e) {
      error = e.toString();
    }
    powerBusy = false;
    loading = false;
    if (!_disposed) notifyListeners();
  }

  void _startShotLoop() {
    _shot?.cancel();
    unawaited(_pullShot());
    _shot = Timer.periodic(const Duration(milliseconds: 1100), (_) => _pullShot());
  }

  Future<void> _pullShot() async {
    if (_shotBusy || activeDeviceId.isEmpty || _disposed) return;
    _shotBusy = true;
    try {
      final bytes = await api.virtualDeviceScreenshot(activeDeviceId);
      if (!_disposed) {
        png = bytes;
        notifyListeners();
      }
    } catch (_) {
    } finally {
      _shotBusy = false;
    }
  }

  Future<void> _releaseLeaseOnly() async {
    _hb?.cancel();
    _hb = null;
    _shot?.cancel();
    _shot = null;
    final lid = leaseId;
    final tok = leaseToken;
    final did = activeDeviceId;
    leaseId = null;
    leaseToken = null;
    if (lid != null && tok != null && did.isNotEmpty) {
      try {
        await api.releaseDeviceLease(did, lid, tok);
      } catch (_) {}
    }
  }

  /// 结束监控：停轮询、释 lease；不 stop 设备（除非用户点了关机）。
  Future<void> detach() async {
    await _releaseLeaseOnly();
    png = null;
    activeDeviceId = '';
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    _hb?.cancel();
    _shot?.cancel();
    super.dispose();
  }

  Future<void> openExternal() async {
    var openUrl = url;
    final tok = api.accessToken;
    if (tok != null && tok.isNotEmpty && openUrl.contains('/virtual-devices/')) {
      final uri = Uri.parse(openUrl);
      openUrl = uri.replace(queryParameters: {
        ...uri.queryParameters,
        'access_token': tok,
      }).toString();
    }
    await Clipboard.setData(ClipboardData(text: openUrl));
    try {
      if (Platform.isMacOS || Platform.isIOS) {
        await Process.run('open', [openUrl]);
      } else if (Platform.isAndroid) {
        await Process.run('am', ['start', '-a', 'android.intent.action.VIEW', '-d', openUrl]);
      }
    } catch (_) {}
  }
}

/// 开机 / 关机互斥按钮。
class WatchAiPowerButton extends StatelessWidget {
  const WatchAiPowerButton({
    super.key,
    required this.controller,
    this.compact = false,
  });

  final WatchAiController controller;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final on = controller.deviceOn;
        final busy = controller.powerBusy;
        final label = busy ? '…' : (on ? '关机' : '开机');
        final color = on ? const Color(0xFFFF8A80) : AppColors.green;
        if (compact) {
          return Material(
            color: color.withValues(alpha: 0.9),
            borderRadius: BorderRadius.circular(6),
            child: InkWell(
              onTap: busy ? null : () => unawaited(on ? controller.powerOff() : controller.powerOn()),
              borderRadius: BorderRadius.circular(6),
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                child: Text(label, style: const TextStyle(color: Colors.white, fontSize: 11, fontWeight: FontWeight.w600)),
              ),
            ),
          );
        }
        return TextButton(
          onPressed: busy ? null : () => unawaited(on ? controller.powerOff() : controller.powerOn()),
          child: Text(label, style: TextStyle(color: color, fontWeight: FontWeight.w600)),
        );
      },
    );
  }
}

/// 聊天页右下角小窗：点画面进全屏；开机/关机手动；× 结束监控。
class WatchAiPipBubble extends StatelessWidget {
  const WatchAiPipBubble({
    super.key,
    required this.controller,
    required this.onExpand,
    required this.onClose,
  });

  final WatchAiController controller;
  final VoidCallback onExpand;
  final VoidCallback onClose;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final media = MediaQuery.of(context);
        final w = media.size.width * 0.42;
        final h = w * (controller.kind == 'android' ? 1.55 : 0.78);
        final maxL = media.size.width - w - 8;
        final maxT = media.size.height - h - media.padding.bottom - 120;
        final left = (media.size.width - w - controller.pipOffset.dx).clamp(8.0, maxL);
        final top = (media.size.height - h - controller.pipOffset.dy).clamp(media.padding.top + 56, maxT);

        Widget preview;
        if (controller.powerBusy || (controller.loading && controller.deviceOn)) {
          preview = const Center(child: SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white54)));
        } else if (controller.png != null) {
          preview = Image.memory(controller.png!, fit: BoxFit.cover, gaplessPlayback: true);
        } else {
          preview = Center(
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 10),
              child: Text(
                controller.deviceOn ? '加载画面…' : (controller.error ?? '未开机'),
                textAlign: TextAlign.center,
                style: const TextStyle(color: Colors.white54, fontSize: 12),
              ),
            ),
          );
        }

        return Positioned(
          left: left,
          top: top,
          width: w,
          height: h,
          child: GestureDetector(
            onPanUpdate: (d) => controller.movePip(d.delta, media.size),
            child: Material(
              elevation: 8,
              borderRadius: BorderRadius.circular(12),
              clipBehavior: Clip.antiAlias,
              color: const Color(0xFF1A1A1A),
              child: Stack(
                fit: StackFit.expand,
                children: [
                  InkWell(onTap: onExpand, child: preview),
                  Positioned(
                    left: 6,
                    top: 6,
                    child: Container(
                      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                      decoration: BoxDecoration(color: Colors.black54, borderRadius: BorderRadius.circular(6)),
                      child: Text(
                        controller.kind == 'android' ? '手机' : '电脑',
                        style: const TextStyle(color: Colors.white, fontSize: 11),
                      ),
                    ),
                  ),
                  Positioned(
                    right: 2,
                    top: 2,
                    child: IconButton(
                      visualDensity: VisualDensity.compact,
                      padding: EdgeInsets.zero,
                      constraints: const BoxConstraints(minWidth: 32, minHeight: 32),
                      icon: const Icon(Icons.close, color: Colors.white, size: 18),
                      onPressed: onClose,
                    ),
                  ),
                  Positioned(
                    left: 6,
                    bottom: 28,
                    child: WatchAiPowerButton(controller: controller, compact: true),
                  ),
                  Positioned(
                    left: 0,
                    right: 0,
                    bottom: 0,
                    child: Container(
                      color: Colors.black45,
                      padding: const EdgeInsets.symmetric(vertical: 4),
                      child: const Text(
                        '点击全屏',
                        textAlign: TextAlign.center,
                        style: TextStyle(color: Colors.white70, fontSize: 11),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        );
      },
    );
  }
}

/// 全屏看 AI；返回键缩小回小窗（不关设备）。
class WatchAiPage extends StatefulWidget {
  const WatchAiPage({super.key, required this.controller});

  final WatchAiController controller;

  @override
  State<WatchAiPage> createState() => _WatchAiPageState();
}

class _WatchAiPageState extends State<WatchAiPage> {
  WatchAiController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    c.fullscreen = true;
    c.addListener(_onCtrl);
    if (c.png == null && !c.loading && c.deviceOn) {
      unawaited(c.attach(kind: c.kind));
    }
  }

  void _onCtrl() {
    if (mounted) setState(() {});
  }

  @override
  void dispose() {
    c.fullscreen = false;
    c.removeListener(_onCtrl);
    super.dispose();
  }

  void _shrink() {
    c.fullscreen = false;
    Navigator.of(context).maybePop();
  }

  Future<void> _closeAll() async {
    await c.detach();
    if (mounted) Navigator.of(context).maybePop('closed');
  }

  Widget _body() {
    if (c.powerBusy || (c.loading && c.deviceOn)) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const CircularProgressIndicator(color: Colors.white),
            if (c.creatingPhone) ...[
              const SizedBox(height: 12),
              const Text('正在准备虚拟手机…', style: TextStyle(color: Colors.white70)),
            ] else if (c.powerBusy) ...[
              const SizedBox(height: 12),
              Text(c.deviceOn ? '正在关机…' : '正在开机…', style: const TextStyle(color: Colors.white70)),
            ],
          ],
        ),
      );
    }
    if (!c.deviceOn) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                c.error ?? (c.kind == 'android' ? '手机未开机' : '电脑未开机'),
                textAlign: TextAlign.center,
                style: const TextStyle(color: Colors.white70, fontSize: 15),
              ),
              const SizedBox(height: 8),
              const Text('打开监控不会自动唤醒，请点开机', style: TextStyle(color: Colors.white38, fontSize: 12)),
              const SizedBox(height: 20),
              FilledButton(
                onPressed: c.powerBusy ? null : () => unawaited(c.powerOn()),
                style: FilledButton.styleFrom(backgroundColor: AppColors.green),
                child: const Text('开机'),
              ),
            ],
          ),
        ),
      );
    }
    if (c.error != null && c.png == null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(c.error!, textAlign: TextAlign.center, style: const TextStyle(color: Colors.white)),
              const SizedBox(height: 16),
              FilledButton(onPressed: () => c.attach(kind: c.kind), child: const Text('重试')),
            ],
          ),
        ),
      );
    }
    final png = c.png;
    return ColoredBox(
      color: const Color(0xFF111111),
      child: Column(
        children: [
          Expanded(
            child: png == null
                ? const Center(child: CircularProgressIndicator(color: Colors.white54))
                : InteractiveViewer(
                    minScale: 0.5,
                    maxScale: 4,
                    child: Center(child: Image.memory(png, gaplessPlayback: true, fit: BoxFit.contain)),
                  ),
          ),
          SafeArea(
            top: false,
            child: Padding(
              padding: const EdgeInsets.fromLTRB(12, 8, 12, 12),
              child: Row(
                children: [
                  Expanded(
                    child: Text(
                      c.kind == 'android' ? '虚拟手机（应用内画面）' : '虚拟电脑（应用内画面）',
                      style: const TextStyle(color: Colors.white54, fontSize: 12),
                    ),
                  ),
                  WatchAiPowerButton(controller: c),
                  TextButton(onPressed: c.openExternal, child: const Text('外开', style: TextStyle(color: Colors.white70))),
                  TextButton(onPressed: _shrink, child: const Text('缩小', style: TextStyle(color: Colors.white))),
                  TextButton(
                    onPressed: _closeAll,
                    child: const Text('关闭', style: TextStyle(color: Color(0xFFFF8A80))),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return PopScope(
      canPop: true,
      child: Scaffold(
        backgroundColor: Colors.black,
        appBar: AppBar(
          title: Text(c.kind == 'android' ? '看手机' : '看电脑'),
          backgroundColor: const Color(0xFF1A1A1A),
          foregroundColor: Colors.white,
          // 覆盖全局 AppBarTheme 的近黑字/图标，否则深色底上看不见
          titleTextStyle: const TextStyle(color: Colors.white, fontSize: 17, fontWeight: FontWeight.w600),
          iconTheme: const IconThemeData(color: Colors.white),
          actionsIconTheme: const IconThemeData(color: Colors.white),
          leading: IconButton(
            icon: const Icon(Icons.keyboard_arrow_down, color: Colors.white),
            onPressed: _shrink,
            tooltip: '缩小为小窗',
          ),
          actions: [
            WatchAiPowerButton(controller: c),
            IconButton(
              icon: Icon(Icons.refresh, color: c.deviceOn ? Colors.white : Colors.white38),
              onPressed: c.deviceOn ? () => c.attach(kind: c.kind) : null,
            ),
            IconButton(
              icon: const Icon(Icons.close, color: Colors.white),
              onPressed: _closeAll,
              tooltip: '关闭监控',
            ),
          ],
          bottom: PreferredSize(
            preferredSize: const Size.fromHeight(44),
            child: Padding(
              padding: const EdgeInsets.fromLTRB(12, 0, 12, 8),
              child: Row(
                children: [
                  Expanded(
                    child: _TabChip(
                      label: '电脑',
                      selected: c.kind == 'desktop',
                      onTap: () => c.attach(kind: 'desktop'),
                    ),
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: _TabChip(
                      label: '手机',
                      selected: c.kind == 'android',
                      onTap: () => c.attach(kind: 'android'),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
        body: _body(),
      ),
    );
  }
}

class _TabChip extends StatelessWidget {
  const _TabChip({required this.label, required this.selected, required this.onTap});
  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: selected ? AppColors.green : const Color(0xFF2A2A2A),
      borderRadius: BorderRadius.circular(8),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(8),
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 8),
          child: Text(
            label,
            textAlign: TextAlign.center,
            style: TextStyle(color: Colors.white, fontWeight: selected ? FontWeight.w600 : FontWeight.w400),
          ),
        ),
      ),
    );
  }
}
