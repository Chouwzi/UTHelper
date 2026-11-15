import 'dart:async';
import 'dart:convert';

import 'package:flet/flet.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_inappwebview/flutter_inappwebview.dart';
import 'package:path/path.dart' as path;
import 'package:path_provider/path_provider.dart';

import 'mobile_session_queue.dart';

const _loadTimeout = Duration(seconds: 45);
const _mobileAcquireTimeout = Duration(seconds: 30);
const _mobileCleanupTimeout = Duration(seconds: 5);
const _cookiePayloadTimeout = Duration(seconds: 10);
const _cookiePayloadLimit = 16 * 1024;
const _maxSessionCookies = 32;
final _sessionKeyPattern = RegExp(r'^[0-9a-fA-F]{64}$');
final _cookieNamePattern = RegExp(r"^[!#\$%&'*+.^_`|~0-9A-Za-z-]+$");
final _mobileSessionQueue = MobileSessionQueue();

class UthQuizPlayerControl extends StatefulWidget {
  const UthQuizPlayerControl({super.key, required this.control});

  final Control control;

  @override
  State<UthQuizPlayerControl> createState() => _UthQuizPlayerControlState();
}

class _PopupWindow {
  const _PopupWindow({required this.windowId, required this.key});

  final int windowId;
  final int key;
}

class _ValidatedCookie {
  const _ValidatedCookie({
    required this.name,
    required this.value,
    required this.domain,
    required this.path,
    required this.isSecure,
    required this.isHttpOnly,
    required this.expiresDate,
    required this.sameSite,
  });

  final String name;
  final String value;
  final String? domain;
  final String path;
  final bool isSecure;
  final bool isHttpOnly;
  final int? expiresDate;
  final HTTPCookieSameSitePolicy? sameSite;
}

class _UthQuizPlayerControlState extends State<UthQuizPlayerControl> {
  InAppWebViewController? _controller;
  WebViewEnvironment? _environment;
  Uri? _trustedOrigin;
  String? _initialUrl;
  String? _sessionKey;
  bool _requireSession = false;
  bool _sessionBootstrapped = false;
  bool _bootstrapStarted = false;
  bool _sessionPayloadRejected = false;
  bool _sessionExpiredSent = false;
  bool _prepared = false;
  bool _disposed = false;
  bool _readySent = false;
  int _nextPopupKey = 0;
  final List<_PopupWindow> _popups = [];
  final Map<int, Timer> _popupLoadTimers = {};
  Timer? _loadTimer;
  final Completer<void> _disposeSignal = Completer<void>();
  MobileSessionLease? _mobileSessionLease;
  DataChannel? _sessionChannel;
  StreamSubscription<Uint8List>? _sessionChannelSubscription;
  Uint8List? _pendingSessionPayload;
  Completer<Uint8List>? _sessionPayloadWaiter;

  @override
  void initState() {
    super.initState();
    _initialUrl = widget.control.getString('url');
    _sessionKey = widget.control.getString('session_key');
    _requireSession = widget.control.getBool('require_session', false)!;
    _trustedOrigin = _parseHttpUri(_initialUrl);
    widget.control.addInvokeMethodListener(_invokeMethod);
    unawaited(_prepare());
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (!_requireSession || _sessionChannel != null) return;
    final channel = FletBackend.of(context).openDataChannel();
    _sessionChannel = channel;
    _sessionChannelSubscription = channel.messages.listen(_onSessionPayload);
    widget.control.triggerEvent('data_channel_open', {
      'channel_name': 'quiz_session',
      'channel_id': channel.id,
    });
  }

  Uri? _parseHttpUri(String? value) {
    if (value == null || value.isEmpty) return null;
    final uri = Uri.tryParse(value);
    if (uri == null ||
        !const {'http', 'https'}.contains(uri.scheme.toLowerCase()) ||
        uri.host.isEmpty) {
      return null;
    }
    return uri;
  }

  bool _validSessionKey(String? value) =>
      value != null && _sessionKeyPattern.hasMatch(value);

  Future<void> _prepare() async {
    if (_trustedOrigin == null) {
      _emitError('navigation_error');
      return;
    }
    if (_requireSession && _trustedOrigin!.scheme.toLowerCase() != 'https') {
      _emitError('session_unavailable');
      return;
    }
    if (!_validSessionKey(_sessionKey)) {
      _emitError('webview_unavailable');
      return;
    }

    try {
      if (!kIsWeb && defaultTargetPlatform == TargetPlatform.windows) {
        final runtimeVersion = await WebViewEnvironment.getAvailableVersion();
        if (runtimeVersion == null || runtimeVersion.isEmpty) {
          _emitError('webview_unavailable');
          return;
        }

        final supportDirectory = await getApplicationSupportDirectory();
        final profileDirectory = Directory(
          path.join(
            supportDirectory.path,
            'uthhelper',
            'quiz-webview',
            _sessionKey!.toLowerCase(),
          ),
        );
        await profileDirectory.create(recursive: true);
        _environment = await WebViewEnvironment.create(
          settings: WebViewEnvironmentSettings(
            userDataFolder: profileDirectory.path,
          ),
        );
      } else if (!kIsWeb &&
          (defaultTargetPlatform == TargetPlatform.android ||
              defaultTargetPlatform == TargetPlatform.iOS)) {
        await _acquireMobileSession();
        if (_mobileSessionLease == null) {
          if (!_disposed) _emitError('webview_unavailable');
          return;
        }
      } else if (!kIsWeb) {
        _emitError('webview_unavailable');
        return;
      }

      if (_disposed) return;
      setState(() => _prepared = true);
    } on MissingPluginException {
      _emitError('webview_unavailable');
    } on PlatformException {
      _emitError('webview_unavailable');
    } catch (_) {
      _emitError('webview_unavailable');
    }
  }

  Future<void> _acquireMobileSession() async {
    final timeout = Completer<void>();
    final timer = Timer(_mobileAcquireTimeout, timeout.complete);
    try {
      final lease = await _mobileSessionQueue.acquire(
        cancelled: Future.any<void>([_disposeSignal.future, timeout.future]),
      );
      if (_disposed || timeout.isCompleted) {
        lease?.release();
        return;
      }
      _mobileSessionLease = lease;
    } finally {
      timer.cancel();
    }
  }

  Future<void> _initializeController(InAppWebViewController controller) async {
    _controller = controller;
    try {
      await _purgeSessionStore().timeout(_mobileCleanupTimeout);
      if (_disposed) return;
      if (!_readySent) {
        _readySent = true;
        _trigger('ready', 'ready');
      }
      if (!_requireSession) {
        await _loadInitialUrl(controller);
      }
    } on MissingPluginException {
      _emitError('webview_unavailable');
    } on PlatformException {
      _emitError('navigation_error');
    } catch (_) {
      _emitError('webview_unavailable');
    }
  }

  Future<void> _loadInitialUrl(InAppWebViewController controller) async {
    final url = _initialUrl;
    if (!_disposed && url != null) {
      await controller.loadUrl(urlRequest: URLRequest(url: WebUri(url)));
    }
  }

  CookieManager _cookieManager() {
    if (!kIsWeb && defaultTargetPlatform == TargetPlatform.windows) {
      final environment = _environment;
      if (environment == null) throw StateError('session_unavailable');
      return CookieManager.instance(webViewEnvironment: environment);
    }
    return CookieManager.instance();
  }

  Future<void> _purgeSessionStore() async {
    await _cookieManager().deleteAllCookies();
    if (!kIsWeb && defaultTargetPlatform != TargetPlatform.windows) {
      await WebStorageManager.instance().deleteAllData();
      await InAppWebViewController.clearAllCache();
    }
  }

  void _onSessionPayload(Uint8List bytes) {
    if (_disposed || !_requireSession || _sessionBootstrapped) return;
    if (bytes.isEmpty || bytes.length > _cookiePayloadLimit) {
      _rejectSessionPayload();
      return;
    }
    final waiter = _sessionPayloadWaiter;
    if (waiter != null && !waiter.isCompleted) {
      waiter.complete(Uint8List.fromList(bytes));
      return;
    }
    if (_pendingSessionPayload != null) {
      _rejectSessionPayload();
      return;
    }
    _pendingSessionPayload = Uint8List.fromList(bytes);
  }

  void _rejectSessionPayload() {
    _sessionPayloadRejected = true;
    _pendingSessionPayload?.fillRange(0, _pendingSessionPayload!.length, 0);
    _pendingSessionPayload = null;
    final waiter = _sessionPayloadWaiter;
    if (waiter != null && !waiter.isCompleted) {
      waiter.completeError(StateError('session_unavailable'));
    }
  }

  Future<Uint8List> _takeSessionPayload() async {
    if (_sessionPayloadRejected) throw StateError('session_unavailable');
    final pending = _pendingSessionPayload;
    if (pending != null) {
      _pendingSessionPayload = null;
      return pending;
    }
    final waiter = Completer<Uint8List>();
    _sessionPayloadWaiter = waiter;
    try {
      return await waiter.future.timeout(_cookiePayloadTimeout);
    } finally {
      if (identical(_sessionPayloadWaiter, waiter)) {
        _sessionPayloadWaiter = null;
      }
    }
  }

  Future<dynamic> _invokeMethod(String name, dynamic args) async {
    if (name != 'bootstrap_session' ||
        !_requireSession ||
        _bootstrapStarted ||
        args is! Map ||
        args['channel_id'] != _sessionChannel?.id) {
      return false;
    }
    _bootstrapStarted = true;
    Uint8List? payload;
    try {
      if (_controller == null || _disposed) return false;
      payload = await _takeSessionPayload();
      final decoded = jsonDecode(utf8.decode(payload));
      if (decoded is! Map ||
          decoded.length != 1 ||
          decoded['cookies'] is! List) {
        throw const FormatException();
      }
      final cookies = decoded['cookies'] as List;
      if (cookies.isEmpty || cookies.length > _maxSessionCookies) {
        throw const FormatException();
      }
      for (final item in cookies) {
        final cookie = _validateCookie(item);
        final applied = await _cookieManager().setCookie(
          url: WebUri(_trustedOrigin!.toString()),
          name: cookie.name,
          value: cookie.value,
          path: cookie.path,
          domain: cookie.domain,
          expiresDate: cookie.expiresDate,
          isSecure: cookie.isSecure,
          isHttpOnly: cookie.isHttpOnly,
          sameSite: cookie.sameSite,
        );
        if (!applied) throw const FormatException();
      }
      if (_disposed || _controller == null) return false;
      _sessionBootstrapped = true;
      await _loadInitialUrl(_controller!);
      return true;
    } catch (_) {
      _sessionBootstrapped = false;
      _rejectSessionPayload();
      _emitError('session_unavailable');
      return false;
    } finally {
      payload?.fillRange(0, payload.length, 0);
    }
  }

  _ValidatedCookie _validateCookie(dynamic raw) {
    if (_trustedOrigin == null ||
        _trustedOrigin!.scheme.toLowerCase() != 'https' ||
        raw is! Map) {
      throw const FormatException();
    }
    const cookieFields = {
      'name',
      'value',
      'domain',
      'path',
      'secure',
      'http_only',
      'same_site',
      'expires',
    };
    if (raw.length != cookieFields.length ||
        !cookieFields.every(raw.containsKey)) {
      throw const FormatException();
    }
    final name = raw['name'];
    final value = raw['value'];
    final domain = raw['domain'];
    final cookiePath = raw['path'];
    final secure = raw['secure'];
    final httpOnly = raw['http_only'];
    final sameSiteValue = raw['same_site'];
    final expires = raw['expires'];
    if (name is! String ||
        !_cookieNamePattern.hasMatch(name) ||
        value is! String ||
        value.isEmpty ||
        value.length > 4096 ||
        value.contains(RegExp(r'[\x00-\x20\x7f;,]')) ||
        (domain != null && domain is! String) ||
        cookiePath is! String ||
        !cookiePath.startsWith('/') ||
        cookiePath.contains(RegExp(r'[\x00-\x20\x7f]')) ||
        secure != true ||
        httpOnly is! bool ||
        (sameSiteValue != null && sameSiteValue is! String) ||
        (expires != null && expires is! int)) {
      throw const FormatException();
    }
    if (domain is String &&
        !_cookieDomainMatches(domain, _trustedOrigin!.host)) {
      throw const FormatException();
    }
    if (!_cookiePathApplies(cookiePath, _trustedOrigin!.path)) {
      throw const FormatException();
    }
    int? expiresDate;
    if (expires != null) {
      final expirySeconds = expires as int;
      if (expirySeconds <= DateTime.now().millisecondsSinceEpoch ~/ 1000 ||
          expirySeconds > 4102444800) {
        throw const FormatException();
      }
      expiresDate = expirySeconds * 1000;
    }
    final sameSite = switch ((sameSiteValue as String?)?.toLowerCase()) {
      null => null,
      'lax' => HTTPCookieSameSitePolicy.LAX,
      'strict' => HTTPCookieSameSitePolicy.STRICT,
      'none' => HTTPCookieSameSitePolicy.NONE,
      _ => throw const FormatException(),
    };
    return _ValidatedCookie(
      name: name,
      value: value,
      domain: domain,
      path: cookiePath,
      isSecure: secure as bool,
      isHttpOnly: httpOnly,
      expiresDate: expiresDate,
      sameSite: sameSite,
    );
  }

  bool _cookieDomainMatches(String domain, String trustedHost) {
    var normalizedDomain = domain.toLowerCase();
    if (normalizedDomain.startsWith('.')) {
      normalizedDomain = normalizedDomain.substring(1);
    }
    return normalizedDomain == trustedHost.toLowerCase() &&
        !normalizedDomain.startsWith('.');
  }

  bool _cookiePathApplies(String cookiePath, String requestPath) {
    if (cookiePath == '/') return true;
    if (requestPath == cookiePath) return true;
    if (!requestPath.startsWith(cookiePath)) return false;
    return cookiePath.endsWith('/') ||
        requestPath.length > cookiePath.length &&
            requestPath[cookiePath.length] == '/';
  }

  void _checkSessionExpired(WebUri? value) {
    if (!_sessionBootstrapped || _sessionExpiredSent || value == null) return;
    final destination = Uri.tryParse(value.toString());
    if (_sameOrigin(destination) &&
        (destination!.path == '/login/index.php' ||
            destination.path == '/login/')) {
      _sessionExpiredSent = true;
      _trigger('session_expired', 'session_expired');
    }
  }

  bool _sameOrigin(Uri? destination) {
    final trusted = _trustedOrigin;
    if (trusted == null || destination == null) return false;
    return destination.scheme.toLowerCase() == trusted.scheme.toLowerCase() &&
        destination.host.toLowerCase() == trusted.host.toLowerCase() &&
        destination.port == trusted.port;
  }

  NavigationActionPolicy _navigationPolicy(
    NavigationAction action, {
    bool popup = false,
  }) {
    if (!action.isForMainFrame) return NavigationActionPolicy.ALLOW;

    final destination = Uri.tryParse(action.request.url?.toString() ?? '');
    if (popup && destination?.toString() == 'about:blank') {
      return NavigationActionPolicy.ALLOW;
    }
    if (_sameOrigin(destination)) return NavigationActionPolicy.ALLOW;

    _trigger('external_link', 'external_link');
    return NavigationActionPolicy.CANCEL;
  }

  Future<bool> _createWindow(CreateWindowAction action) async {
    final target = Uri.tryParse(action.request.url?.toString() ?? '');
    final isBlank = target == null || target.toString() == 'about:blank';
    if (!isBlank && !_sameOrigin(target)) {
      _trigger('external_link', 'external_link');
      return false;
    }

    if (_disposed) return false;
    setState(() {
      _popups.add(
        _PopupWindow(windowId: action.windowId, key: _nextPopupKey++),
      );
    });
    return true;
  }

  void _closePopup(int key) {
    if (_disposed) return;
    _stopPopupLoadTimeout(key);
    setState(() => _popups.removeWhere((popup) => popup.key == key));
  }

  InAppWebViewSettings _settings() {
    return InAppWebViewSettings(
      javaScriptEnabled: true,
      javaScriptCanOpenWindowsAutomatically: true,
      supportMultipleWindows: true,
      useShouldOverrideUrlLoading: true,
      // Cookie stores are explicitly cleared and serialized on mobile before
      // bootstrap. Incognito would clear Android cookies on WebView creation
      // and use a non-persistent iOS store separate from CookieManager.
      incognito: false,
      saveFormData: false,
    );
  }

  void _startPopupLoadTimeout(int key) {
    _popupLoadTimers.remove(key)?.cancel();
    _popupLoadTimers[key] = Timer(_loadTimeout, () {
      if (!_disposed) _emitError('load_timeout');
    });
  }

  void _stopPopupLoadTimeout(int key) {
    _popupLoadTimers.remove(key)?.cancel();
  }

  void _startLoadTimeout() {
    _loadTimer?.cancel();
    _loadTimer = Timer(_loadTimeout, () {
      if (_disposed) return;
      _emitError('load_timeout');
    });
  }

  void _stopLoadTimeout() {
    _loadTimer?.cancel();
    _loadTimer = null;
  }

  void _emitError(String code) {
    if (_disposed) return;
    _trigger('error', code);
  }

  void _trigger(String eventName, String code) {
    widget.control.triggerEvent(eventName, code);
  }

  void _rootRequestedClose() {
    _trigger('close', 'closed');
  }

  Widget _popup(_PopupWindow popup) {
    return Positioned.fill(
      child: InAppWebView(
        key: ValueKey('quiz-popup-${popup.key}'),
        windowId: popup.windowId,
        webViewEnvironment: _environment,
        initialSettings: _settings(),
        onLoadStart: (controller, url) {
          _checkSessionExpired(url);
          _startPopupLoadTimeout(popup.key);
        },
        onLoadStop: (controller, url) => _stopPopupLoadTimeout(popup.key),
        onCreateWindow: (controller, action) => _createWindow(action),
        onCloseWindow: (controller) => _closePopup(popup.key),
        shouldOverrideUrlLoading: (controller, action) async =>
            _navigationPolicy(action, popup: true),
        onReceivedError: (controller, request, error) {
          if (request.isForMainFrame == true) {
            _stopPopupLoadTimeout(popup.key);
            _emitError('navigation_error');
          }
        },
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final url = _initialUrl;
    if (!_prepared || url == null) {
      return LayoutControl(
        control: widget.control,
        child: const Center(child: CircularProgressIndicator()),
      );
    }

    return LayoutControl(
      control: widget.control,
      child: Stack(
        fit: StackFit.expand,
        children: [
          Offstage(
            offstage: _popups.isNotEmpty,
            child: InAppWebView(
              webViewEnvironment: _environment,
              initialSettings: _settings(),
              onWebViewCreated: _initializeController,
              onLoadStart: (controller, url) {
                _checkSessionExpired(url);
                _startLoadTimeout();
              },
              onLoadStop: (controller, url) => _stopLoadTimeout(),
              onReceivedError: (controller, request, error) {
                if (request.isForMainFrame == true) {
                  _loadTimer?.cancel();
                  _emitError('navigation_error');
                }
              },
              shouldOverrideUrlLoading: (controller, action) async =>
                  _navigationPolicy(action),
              onCreateWindow: (controller, action) => _createWindow(action),
              onCloseWindow: (controller) => _rootRequestedClose(),
            ),
          ),
          for (final popup in _popups) _popup(popup),
        ],
      ),
    );
  }

  @override
  void dispose() {
    _disposed = true;
    if (!_disposeSignal.isCompleted) _disposeSignal.complete();
    _loadTimer?.cancel();
    widget.control.removeInvokeMethodListener(_invokeMethod);
    _sessionChannelSubscription?.cancel();
    _sessionChannelSubscription = null;
    _sessionChannel?.close();
    _sessionChannel = null;
    _pendingSessionPayload?.fillRange(0, _pendingSessionPayload!.length, 0);
    _pendingSessionPayload = null;
    final waiter = _sessionPayloadWaiter;
    if (waiter != null && !waiter.isCompleted) {
      waiter.completeError(StateError('session_unavailable'));
    }
    for (final timer in _popupLoadTimers.values) {
      timer.cancel();
    }
    _popupLoadTimers.clear();
    final lease = _mobileSessionLease;
    if (lease != null) {
      unawaited(_releaseMobileSession(lease));
    }
    final environment = _environment;
    if (environment != null) unawaited(environment.dispose());
    super.dispose();
  }

  Future<void> _releaseMobileSession(MobileSessionLease lease) async {
    try {
      await _purgeSessionStore().timeout(_mobileCleanupTimeout);
    } catch (_) {
      // Cleanup failures are deliberately not surfaced with native details.
      _mobileSessionQueue.disable();
    } finally {
      lease.release();
      _mobileSessionLease = null;
    }
  }
}
