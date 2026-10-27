import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api/api_client.dart';
import '../api/session.dart';
import '../store.dart';
import '../theme.dart';
import 'shell.dart';

/// 启动流程共用的浅底与主按钮样式。
class _AuthScaffold extends StatelessWidget {
  const _AuthScaffold({
    required this.title,
    required this.child,
    this.subtitle,
    this.leading,
  });

  final String title;
  final String? subtitle;
  final Widget? leading;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return AnnotatedRegion<SystemUiOverlayStyle>(
      value: SystemUiOverlayStyle.dark,
      child: Scaffold(
        backgroundColor: const Color(0xFFF7F7F5),
        appBar: leading == null
            ? null
            : AppBar(
                backgroundColor: const Color(0xFFF7F7F5),
                elevation: 0,
                leading: leading,
              ),
        body: SafeArea(
          child: Column(
            children: [
              Expanded(
                child: ListView(
                  padding: const EdgeInsets.fromLTRB(28, 12, 28, 24),
                  children: [
                    const SizedBox(height: 24),
                    Text(
                      title,
                      style: const TextStyle(
                        fontSize: 32,
                        height: 1.15,
                        fontWeight: FontWeight.w700,
                        letterSpacing: -0.6,
                        color: Color(0xFF111111),
                      ),
                    ),
                    if (subtitle != null) ...[
                      const SizedBox(height: 10),
                      Text(
                        subtitle!,
                        style: const TextStyle(fontSize: 15, height: 1.45, color: Color(0xFF6B6B6B)),
                      ),
                    ],
                    const SizedBox(height: 36),
                    child,
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

InputDecoration _field({required String label, String? hint}) {
  return InputDecoration(
    labelText: label,
    hintText: hint,
    filled: true,
    fillColor: Colors.white,
    contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 16),
    border: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: const BorderSide(color: Color(0xFFE6E6E6))),
    enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: const BorderSide(color: Color(0xFFE6E6E6))),
    focusedBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: const BorderSide(color: AppColors.green, width: 1.5)),
  );
}

Widget _primaryButton({required String label, required VoidCallback? onPressed, bool loading = false}) {
  return SizedBox(
    width: double.infinity,
    height: 50,
    child: FilledButton(
      onPressed: loading ? null : onPressed,
      style: FilledButton.styleFrom(
        backgroundColor: AppColors.green,
        disabledBackgroundColor: const Color(0xFFB7E6C7),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
        elevation: 0,
        textStyle: const TextStyle(fontSize: 17, fontWeight: FontWeight.w600),
      ),
      child: loading
          ? const SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2.2, color: Colors.white))
          : Text(label),
    ),
  );
}

void _showError(BuildContext context, Object e) {
  // 令牌过期由 ApiClient 静默续签；不要弹「令牌无效或已过期」
  if (!shouldToastApiError(e)) return;
  final msg = e is ApiException ? e.message : e.toString();
  ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
}

Future<void> _enterRemote(
  BuildContext context, {
  required String serverUrl,
  required ServerInfo info,
  required AuthTokens tokens,
}) async {
  final sessions = SessionStore();
  try {
    await sessions.save(serverUrl: serverUrl, tokens: tokens).timeout(const Duration(seconds: 10));
  } catch (e) {
    // 安全存储卡住/失败：仍进主界面，只是下次可能要重登
    if (context.mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('会话保存异常，已继续进入：$e')),
      );
    }
  }
  final api = ApiClient(serverUrl)
    ..accessToken = tokens.accessToken
    ..refreshToken = tokens.refreshToken;
  final store = AppStore.remote(
    serverUrl: serverUrl,
    serverName: info.name.isEmpty ? _hostOf(serverUrl) : info.name,
    accountName: tokens.user['username'] as String? ?? '用户',
    meId: tokens.user['id'] as String? ?? 'me',
    meName: tokens.user['display_name'] as String? ?? tokens.user['username'] as String? ?? '我',
    apiClient: api,
    accessToken: tokens.accessToken,
    refreshToken: tokens.refreshToken,
  );
  // 先跳转，避免同步慢/挂起时登录页一直转圈
  if (!context.mounted) return;
  Navigator.of(context).pushAndRemoveUntil(
    MaterialPageRoute(builder: (_) => Shell(store: store)),
    (_) => false,
  );
  // 后台拉同步（带总超时）
  unawaited(() async {
    try {
      await store.pullSync().timeout(const Duration(seconds: 25));
    } catch (_) {
      // Shell 内可离线用空壳 / 本地缓存
    }
  }());
}

String _hostOf(String url) {
  try {
    final u = Uri.parse(url.contains('://') ? url : 'https://$url');
    return u.host.isEmpty ? url : u.host;
  } catch (_) {
    return url;
  }
}

/// 欢迎页：连接自己的后端。
class WelcomePage extends StatelessWidget {
  const WelcomePage({super.key});

  @override
  Widget build(BuildContext context) {
    return AnnotatedRegion<SystemUiOverlayStyle>(
      value: SystemUiOverlayStyle.dark,
      child: Scaffold(
        backgroundColor: const Color(0xFFF7F7F5),
        body: SafeArea(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(28, 0, 28, 28),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Spacer(flex: 2),
                Container(
                  width: 72,
                  height: 72,
                  decoration: BoxDecoration(
                    color: const Color(0xFF1A2030),
                    borderRadius: BorderRadius.circular(18),
                    boxShadow: [
                      BoxShadow(color: Colors.black.withValues(alpha: 0.12), blurRadius: 16, offset: const Offset(0, 6)),
                    ],
                  ),
                  child: Stack(
                    clipBehavior: Clip.none,
                    children: [
                      Center(
                        child: Container(
                          width: 38,
                          height: 38,
                          decoration: BoxDecoration(
                            color: const Color(0xFFF8F6F1),
                            borderRadius: BorderRadius.circular(10),
                          ),
                        ),
                      ),
                      Positioned(
                        right: 10,
                        top: 10,
                        child: Container(
                          width: 16,
                          height: 16,
                          decoration: const BoxDecoration(color: Color(0xFFFF3B48), shape: BoxShape.circle),
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 28),
                const Text(
                  '未读',
                  style: TextStyle(
                    fontSize: 36,
                    height: 1.1,
                    fontWeight: FontWeight.w700,
                    letterSpacing: -0.8,
                    color: Color(0xFF111111),
                  ),
                ),
                const SizedBox(height: 12),
                const Text(
                  '私域对话，人设常在。连上后端即可开始。',
                  style: TextStyle(fontSize: 16, height: 1.5, color: Color(0xFF6B6B6B)),
                ),
                const Spacer(flex: 3),
                _primaryButton(
                  label: '连接服务器',
                  onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const ConnectServerPage())),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class ConnectServerPage extends StatefulWidget {
  const ConnectServerPage({super.key});
  @override
  State<ConnectServerPage> createState() => _ConnectServerPageState();
}

class _ConnectServerPageState extends State<ConnectServerPage> {
  final url = TextEditingController(text: 'https://119.45.30.73:8443/chat-api');
  bool loading = false;

  @override
  void initState() {
    super.initState();
    SessionStore().lastServerUrl().then((s) {
      if (!mounted || s == null || s.isEmpty) return;
      url.text = s;
      setState(() {});
    });
  }

  @override
  void dispose() {
    url.dispose();
    super.dispose();
  }

  Future<void> connect() async {
    final raw = normalizeServerUrl(url.text);
    if (raw.isEmpty) {
      _showError(context, ApiException('请填写后端地址'));
      return;
    }
    setState(() => loading = true);
    final client = ApiClient(raw);
    try {
      final info = await client.fetchInfo();
      if (!mounted) return;
      Navigator.push(
        context,
        MaterialPageRoute(
          builder: (_) => LoginPage(serverUrl: raw, serverInfo: info),
        ),
      );
    } catch (e) {
      if (mounted) _showError(context, e);
    } finally {
      client.close();
      if (mounted) setState(() => loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final http = url.text.trim().startsWith('http://');
    return _AuthScaffold(
      leading: const BackButton(),
      title: '连接服务器',
      subtitle: '填你自己部署的地址。会请求 /api/info 校验能否连通。',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          TextField(
            controller: url,
            keyboardType: TextInputType.url,
            autofocus: true,
            textInputAction: TextInputAction.done,
            onChanged: (_) => setState(() {}),
            onSubmitted: (_) => connect(),
            decoration: _field(label: '后端地址', hint: 'http://127.0.0.1:8000'),
          ),
          if (http) ...[
            const SizedBox(height: 10),
            const Text('http 未加密，适合局域网。', style: TextStyle(fontSize: 13, color: Color(0xFFB36B00))),
          ],
          const SizedBox(height: 20),
          _primaryButton(label: '继续', onPressed: connect, loading: loading),
          const SizedBox(height: 12),
          const Text(
            'iOS 模拟器用 127.0.0.1；Android 模拟器用 10.0.2.2；真机用电脑局域网 IP。',
            style: TextStyle(fontSize: 12, height: 1.4, color: Color(0xFF9A9A9A)),
          ),
        ],
      ),
    );
  }
}

class LoginPage extends StatefulWidget {
  const LoginPage({super.key, required this.serverUrl, required this.serverInfo});
  final String serverUrl;
  final ServerInfo serverInfo;
  @override
  State<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends State<LoginPage> {
  final user = TextEditingController();
  final pass = TextEditingController();
  bool loading = false;
  bool obscure = true;

  @override
  void initState() {
    super.initState();
    SessionStore().lastUsername().then((name) {
      if (!mounted || name == null || name.isEmpty) return;
      user.text = name;
      setState(() {});
    });
  }

  @override
  void dispose() {
    user.dispose();
    pass.dispose();
    super.dispose();
  }

  Future<void> login() async {
    final username = user.text.trim();
    final password = pass.text;
    if (username.isEmpty || password.isEmpty) {
      _showError(context, ApiException('请填写用户名和密码'));
      return;
    }
    if (loading) return;
    setState(() => loading = true);
    final client = ApiClient(widget.serverUrl);
    try {
      final tokens = await client
          .login(
            username: username,
            password: password,
            deviceName: Platform.isAndroid ? 'Android' : 'iOS',
          )
          .timeout(const Duration(seconds: 20));
      if (!mounted) return;
      await _enterRemote(
        context,
        serverUrl: widget.serverUrl,
        info: widget.serverInfo,
        tokens: tokens,
      );
    } on TimeoutException {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('登录超时，请检查网络或稍后重试')),
        );
      }
    } catch (e) {
      if (mounted) {
        // 登录页强制提示，避免 401 被当成鉴权噪声吞掉
        final msg = e is ApiException ? e.message : e.toString();
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
      }
    } finally {
      client.close();
      if (mounted) setState(() => loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return _AuthScaffold(
      leading: const BackButton(),
      title: '登录',
      subtitle: '${widget.serverInfo.name} · ${widget.serverUrl}',
      child: Column(
        children: [
          TextField(
            controller: user,
            autofocus: true,
            textInputAction: TextInputAction.next,
            decoration: _field(label: '用户名'),
          ),
          const SizedBox(height: 14),
          TextField(
            controller: pass,
            obscureText: obscure,
            textInputAction: TextInputAction.done,
            onSubmitted: (_) => login(),
            decoration: _field(label: '密码').copyWith(
              suffixIcon: IconButton(
                onPressed: () => setState(() => obscure = !obscure),
                icon: Icon(obscure ? Icons.visibility_outlined : Icons.visibility_off_outlined, color: const Color(0xFF8A8A8A)),
              ),
            ),
          ),
          const SizedBox(height: 24),
          _primaryButton(label: '登录', onPressed: login, loading: loading),
          const SizedBox(height: 8),
          TextButton(
            onPressed: () => Navigator.push(
              context,
              MaterialPageRoute(
                builder: (_) => RegisterPage(serverUrl: widget.serverUrl, serverInfo: widget.serverInfo),
              ),
            ),
            child: const Text('没有账号？注册', style: TextStyle(color: AppColors.name, fontSize: 15)),
          ),
        ],
      ),
    );
  }
}

class RegisterPage extends StatefulWidget {
  const RegisterPage({super.key, required this.serverUrl, required this.serverInfo});
  final String serverUrl;
  final ServerInfo serverInfo;
  @override
  State<RegisterPage> createState() => _RegisterPageState();
}

class _RegisterPageState extends State<RegisterPage> {
  final user = TextEditingController();
  final pass = TextEditingController();
  final invite = TextEditingController();
  bool loading = false;
  bool obscure = true;

  @override
  void dispose() {
    user.dispose();
    pass.dispose();
    invite.dispose();
    super.dispose();
  }

  Future<void> register() async {
    final username = user.text.trim();
    final password = pass.text;
    if (username.length < 2) {
      _showError(context, ApiException('用户名至少 2 个字符'));
      return;
    }
    if (password.length < 8) {
      _showError(context, ApiException('密码至少 8 位'));
      return;
    }
    if (widget.serverInfo.registrationMode == 'closed') {
      _showError(context, ApiException('当前关闭注册'));
      return;
    }
    if (widget.serverInfo.registrationMode == 'invite' && invite.text.trim().isEmpty) {
      _showError(context, ApiException('需要邀请码'));
      return;
    }
    setState(() => loading = true);
    final client = ApiClient(widget.serverUrl);
    try {
      final tokens = await client.register(
        username: username,
        password: password,
        inviteCode: invite.text.trim(),
      );
      if (!mounted) return;
      await _enterRemote(
        context,
        serverUrl: widget.serverUrl,
        info: widget.serverInfo,
        tokens: tokens,
      );
    } catch (e) {
      if (mounted) _showError(context, e);
    } finally {
      client.close();
      if (mounted) setState(() => loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final needInvite = widget.serverInfo.registrationMode == 'invite';
    return _AuthScaffold(
      leading: const BackButton(),
      title: '注册',
      subtitle: '第一个注册的人会成为管理员。',
      child: Column(
        children: [
          TextField(controller: user, autofocus: true, decoration: _field(label: '用户名')),
          const SizedBox(height: 14),
          TextField(
            controller: pass,
            obscureText: obscure,
            decoration: _field(label: '密码', hint: '至少 8 位').copyWith(
              suffixIcon: IconButton(
                onPressed: () => setState(() => obscure = !obscure),
                icon: Icon(obscure ? Icons.visibility_outlined : Icons.visibility_off_outlined, color: const Color(0xFF8A8A8A)),
              ),
            ),
          ),
          if (needInvite) ...[
            const SizedBox(height: 14),
            TextField(controller: invite, decoration: _field(label: '邀请码')),
          ],
          const SizedBox(height: 24),
          _primaryButton(label: '注册并进入', onPressed: register, loading: loading),
        ],
      ),
    );
  }
}

/// 启动页：有本地会话则自动恢复登录，否则进欢迎页。
class BootstrapPage extends StatefulWidget {
  const BootstrapPage({super.key});
  @override
  State<BootstrapPage> createState() => _BootstrapPageState();
}

class _BootstrapPageState extends State<BootstrapPage> {
  bool _checking = true;

  @override
  void initState() {
    super.initState();
    _restore();
  }

  Future<void> _restore() async {
    final saved = await SessionStore().load();
    if (!mounted) return;
    if (saved == null) {
      setState(() => _checking = false);
      return;
    }

    final api = ApiClient(saved.serverUrl)
      ..accessToken = saved.accessToken
      ..refreshToken = saved.refreshToken;
    AuthTokens tokens = AuthTokens(
      accessToken: saved.accessToken,
      refreshToken: saved.refreshToken,
      user: saved.user,
    );
    api.onTokensUpdated = (t) async {
      tokens = t;
      await SessionStore().save(serverUrl: saved.serverUrl, tokens: t);
    };

    try {
      // 401 由 ApiClient 无感 refresh，不再弹令牌过期
      await api.sync();
      if (api.accessToken != null && api.refreshToken != null) {
        tokens = AuthTokens(
          accessToken: api.accessToken!,
          refreshToken: api.refreshToken!,
          user: tokens.user,
        );
      }

      ServerInfo info;
      try {
        info = await api.fetchInfo();
      } catch (_) {
        info = ServerInfo(
          name: _hostOf(saved.serverUrl),
          version: '',
          registrationMode: 'open',
          minAppVersion: '1.0.0',
        );
      }
      if (!mounted) return;
      await _enterRemote(
        context,
        serverUrl: saved.serverUrl,
        info: info,
        tokens: tokens,
      );
    } on ApiException catch (e) {
      // 仅登录失效才清令牌；网络不通保留会话，用本地缓存进主界面
      if (e.statusCode == 401) {
        await SessionStore().clear();
        if (mounted) setState(() => _checking = false);
        return;
      }
      if (!mounted) return;
      await _enterRemote(
        context,
        serverUrl: saved.serverUrl,
        info: ServerInfo(
          name: _hostOf(saved.serverUrl),
          version: '',
          registrationMode: 'open',
          minAppVersion: '1.0.0',
        ),
        tokens: tokens,
      );
    } catch (_) {
      if (!mounted) return;
      await _enterRemote(
        context,
        serverUrl: saved.serverUrl,
        info: ServerInfo(
          name: _hostOf(saved.serverUrl),
          version: '',
          registrationMode: 'open',
          minAppVersion: '1.0.0',
        ),
        tokens: tokens,
      );
    } finally {
      api.close();
    }
  }

  @override
  Widget build(BuildContext context) {
    if (!_checking) return const WelcomePage();
    return const AnnotatedRegion<SystemUiOverlayStyle>(
      value: SystemUiOverlayStyle.dark,
      child: Scaffold(
        backgroundColor: Color(0xFFF7F7F5),
        body: Center(
          child: SizedBox(
            width: 28,
            height: 28,
            child: CircularProgressIndicator(strokeWidth: 2.4, color: AppColors.green),
          ),
        ),
      ),
    );
  }
}
