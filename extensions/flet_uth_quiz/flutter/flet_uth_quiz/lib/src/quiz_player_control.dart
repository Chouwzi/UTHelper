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
import 'quiz_cookie_policy.dart';
import 'quiz_popup_policy.dart';
import 'quiz_web_storage.dart';
import 'quiz_webview_settings.dart';

const _loadTimeout = Duration(seconds: 45);
const _mobileAcquireTimeout = Duration(seconds: 30);
const _mobileCleanupTimeout = Duration(seconds: 5);
const _cookiePayloadTimeout = Duration(seconds: 10);
const _cookiePayloadLimit = 16 * 1024;
const _maxSessionCookies = 32;
final _sessionKeyPattern = RegExp(r'^[0-9a-fA-F]{64}$');
final _mobileSessionQueue = MobileSessionQueue();

/// Validates a direct Moodle attempt/summary URL against its original site.
/// The URL is kept on the DataChannel and is never sent in invoke-method args.
Uri? validateQuizLaunchUrl(String? value, Uri? trustedOrigin) {
  if (value == null ||
      value.isEmpty ||
      value.length > 2048 ||
      trustedOrigin == null ||
      trustedOrigin.scheme.toLowerCase() != 'https' ||
      trustedOrigin.hasPort) {
    return null;
  }
  final target = Uri.tryParse(value);
  // Uri canonicalizes an explicit default :443 away, so inspect the original
  // authority too; direct launch URLs must never carry a port or userinfo.
  final rawAuthority = RegExp(r'^https://([^/?#]+)', caseSensitive: false)
      .firstMatch(value)
      ?.group(1);
  if (target == null ||
      rawAuthority == null ||
      rawAuthority.toLowerCase() != trustedOrigin.host.toLowerCase() ||
      target.scheme.toLowerCase() != 'https' ||
      target.host.isEmpty ||
      target.hasPort ||
      target.userInfo.isNotEmpty ||
      target.authority.toLowerCase() != trustedOrigin.host.toLowerCase() ||
      target.scheme.toLowerCase() != trustedOrigin.scheme.toLowerCase() ||
      target.host.toLowerCase() != trustedOrigin.host.toLowerCase() ||
      !const {
        '/mod/quiz/attempt.php',
        '/mod/quiz/summary.php',
      }.contains(target.path)) {
    return null;
  }

  final query = target.queryParametersAll;
  if (query.containsKey('action')) return null;
  final attempts = query['attempt'];
  if (attempts == null ||
      attempts.length != 1 ||
      !RegExp(r'^[1-9][0-9]*$').hasMatch(attempts.single)) {
    return null;
  }
  return target;
}

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

class _UthQuizPlayerControlState extends State<UthQuizPlayerControl> {
  InAppWebViewController? _controller;
  WebViewEnvironment? _environment;
  Future<WebViewEnvironment>? _environmentCreation;
  Directory? _profileDirectory;
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
  Future<void>? _initialStorePurge;
  Future<void>? _bootstrapCompletion;
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
        if (_disposed) return;
        if (runtimeVersion == null || runtimeVersion.isEmpty) {
          _emitError('webview_unavailable');
          return;
        }

        final supportDirectory = await getApplicationSupportDirectory();
        if (_disposed) return;
        final profileDirectory = Directory(
          path.join(
            supportDirectory.path,
            'uthhelper',
            'quiz-webview',
            _sessionKey!.toLowerCase(),
          ),
        );
        await profileDirectory.parent.create(recursive: true);
        if (_disposed) return;
        if (await profileDirectory.exists()) {
          await profileDirectory.delete(recursive: true);
        }
        if (_disposed) return;
        await profileDirectory.create(recursive: true);
        _profileDirectory = profileDirectory;
        if (_disposed) {
          await _deleteProfileDirectory();
          return;
        }
        final creation = WebViewEnvironment.create(
          settings: WebViewEnvironmentSettings(
            userDataFolder: profileDirectory.path,
          ),
        );
        _environmentCreation = creation;
        final environment = await creation;
        if (_disposed) return;
        _environment = environment;
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
    if (_disposed) return;
    _controller = controller;
    try {
      final purge = _purgeSessionStore();
      _initialStorePurge = purge;
      await purge.timeout(_mobileCleanupTimeout);
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

  Future<void> _loadLaunchUrl(
    InAppWebViewController controller,
    String url,
  ) async {
    if (!_disposed) {
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
      await purgeQuizWebStorage(
        defaultTargetPlatform,
        WebStorageManager.instance(),
      );
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
    if (_disposed ||
        name != 'bootstrap_session' ||
        !_requireSession ||
        _bootstrapStarted ||
        args is! Map ||
        args['channel_id'] != _sessionChannel?.id) {
      return false;
    }
    _bootstrapStarted = true;
    final bootstrapDone = Completer<void>();
    _bootstrapCompletion = bootstrapDone.future;
    Uint8List? payload;
    try {
      if (_controller == null || _disposed) return false;
      payload = await _takeSessionPayload();
      final decoded = jsonDecode(utf8.decode(payload));
      if (decoded is! Map ||
          (decoded.length != 1 && decoded.length != 2) ||
          !decoded.containsKey('cookies') ||
          decoded.keys.any(
            (key) => key != 'cookies' && key != 'launch_url',
          ) ||
          decoded['cookies'] is! List) {
        throw const FormatException();
      }
      final launchValue = decoded['launch_url'];
      Uri? launchUri;
      if (launchValue != null) {
        if (launchValue is! String) throw const FormatException();
        launchUri = validateQuizLaunchUrl(launchValue, _trustedOrigin);
        if (launchUri == null) throw const FormatException();
      }
      final cookies = decoded['cookies'] as List;
      if (cookies.isEmpty || cookies.length > _maxSessionCookies) {
        throw const FormatException();
      }
      for (final item in cookies) {
        if (_disposed) return false;
        final cookie = validateQuizCookie(item, _trustedOrigin);
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
        if (_disposed) return false;
      }
      if (_disposed || _controller == null) return false;
      _sessionBootstrapped = true;
      if (launchUri != null) {
        await _loadLaunchUrl(_controller!, launchUri.toString());
      } else {
        await _loadInitialUrl(_controller!);
      }
      return true;
    } catch (_) {
      _sessionBootstrapped = false;
      _rejectSessionPayload();
      _emitError('session_unavailable');
      return false;
    } finally {
      payload?.fillRange(0, payload.length, 0);
      if (!bootstrapDone.isCompleted) bootstrapDone.complete();
    }
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
    if (_disposed) return NavigationActionPolicy.CANCEL;
    if (!action.isForMainFrame) {
      return allowsQuizSubframeNavigation(
        action.request.url?.toString(),
      )
          ? NavigationActionPolicy.ALLOW
          : NavigationActionPolicy.CANCEL;
    }

    final destination = Uri.tryParse(action.request.url?.toString() ?? '');
    if (popup && destination?.toString() == 'about:blank') {
      return NavigationActionPolicy.ALLOW;
    }
    if (_sameOrigin(destination)) return NavigationActionPolicy.ALLOW;

    _trigger('external_link', 'external_link');
    return NavigationActionPolicy.CANCEL;
  }

  Future<bool> _createWindow(CreateWindowAction action) async {
    final disposition = classifyQuizPopupRequest(
      action.request.url?.toString(),
      _trustedOrigin,
      activePopupCount: _popups.length,
    );
    if (_disposed ||
        disposition == QuizPopupRequestKind.invalid ||
        disposition == QuizPopupRequestKind.limitReached) {
      return false;
    }
    if (disposition == QuizPopupRequestKind.external) {
      _trigger('external_link', 'external_link');
      return false;
    }

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

  void _startPopupLoadTimeout(int key) {
    if (_disposed) return;
    _popupLoadTimers.remove(key)?.cancel();
    _popupLoadTimers[key] = Timer(_loadTimeout, () {
      if (!_disposed) _emitError('load_timeout');
    });
  }

  void _stopPopupLoadTimeout(int key) {
    _popupLoadTimers.remove(key)?.cancel();
  }

  void _startLoadTimeout() {
    if (_disposed) return;
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
    if (_disposed) return;
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
        initialSettings: createQuizWebViewSettings(),
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
              initialSettings: createQuizWebViewSettings(),
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
    if (_environment != null ||
        _environmentCreation != null ||
        _profileDirectory != null) {
      final disposal = _disposeWindowsResources();
      unawaited(disposal);
    }
    super.dispose();
  }

  List<Future<void>> _pendingSessionWork() => [
        if (_initialStorePurge != null) _initialStorePurge!,
        if (_bootstrapCompletion != null) _bootstrapCompletion!,
      ];

  Future<void> _settleSessionWork() async {
    await Future.wait(
      _pendingSessionWork().map((operation) async {
        try {
          await operation;
        } catch (_) {
          // Cleanup proceeds after either success or failure.
        }
      }),
    );
  }

  Future<bool> _waitForSessionWork() async {
    try {
      await _settleSessionWork().timeout(_mobileCleanupTimeout);
      return true;
    } catch (_) {
      return false;
    }
  }

  Future<void> _disposeWindowsResources() async {
    if (!await _waitForSessionWork()) {
      // Keep the profile intact while a cookie operation may still use it,
      // then retry disposal once the native operation has actually settled.
      unawaited(
        _settleSessionWork().then((_) => _disposeWindowsResources()),
      );
      return;
    }
    var environment = _environment;
    final creation = _environmentCreation;
    if (environment == null && creation != null) {
      try {
        environment = await creation;
      } catch (_) {
        // A failed creation still leaves a profile directory to remove.
      }
    }
    if (environment != null) {
      try {
        await environment.dispose();
      } catch (_) {
        // Keep the profile intact if native WebView2 may still own it.
        return;
      }
    }
    await _deleteProfileDirectory();
  }

  Future<void> _deleteProfileDirectory() async {
    final directory = _profileDirectory;
    if (directory == null) return;
    try {
      if (await directory.exists()) {
        await directory.delete(recursive: true);
      }
    } catch (_) {
      // The next source session retries clearing the same account-scoped path.
    } finally {
      _profileDirectory = null;
    }
  }

  Future<void> _releaseMobileSession(MobileSessionLease lease) async {
    try {
      if (!await _waitForSessionWork()) {
        // A late cookie write could follow cleanup, so never hand the shared
        // mobile store to another quiz session in this process.
        _mobileSessionQueue.disable();
        unawaited(
          _settleSessionWork().then((_) async {
            try {
              await _purgeSessionStore().timeout(_mobileCleanupTimeout);
            } catch (_) {
              // The queue stays disabled if the final purge also fails.
            }
          }),
        );
      }
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
