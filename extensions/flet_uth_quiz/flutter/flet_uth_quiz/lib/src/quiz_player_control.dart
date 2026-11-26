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
import 'quiz_profile_storage.dart';
import 'quiz_web_storage.dart';
import 'quiz_webview_settings.dart';

export 'quiz_profile_storage.dart' show activeQuizWindowsProfilePaths;

const _loadTimeout = Duration(seconds: 45);
const _mobileAcquireTimeout = Duration(seconds: 30);
const _mobileCleanupTimeout = Duration(seconds: 5);
const _blockedPopupDisposalTimeout = Duration(seconds: 5);
const _cookiePayloadTimeout = Duration(seconds: 10);
const _cookiePayloadLimit = 16 * 1024;
const _maxSessionCookies = 32;
final _sessionKeyPattern = RegExp(r'^[0-9a-fA-F]{64}$');

Uri? parseQuizBaseUri(String? value, {bool requireHttps = false}) {
  if (value == null ||
      value.isEmpty ||
      hasUnsafeUrlCharacters(value) ||
      value.contains(RegExp(r'\s'))) {
    return null;
  }
  final uri = Uri.tryParse(value);
  final allowedSchemes = requireHttps ? const {'https'} : const {'http', 'https'};
  if (uri == null ||
      !uri.isAbsolute ||
      !uri.hasAuthority ||
      !allowedSchemes.contains(uri.scheme.toLowerCase()) ||
      uri.host.isEmpty ||
      uri.userInfo.isNotEmpty ||
      uri.hasPort ||
      hasExplicitPortOrUserInfo(value)) {
    return null;
  }
  return uri;
}

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

const quizRenderProcessGoneErrorCode = 'render_process_gone';

/// Returns true when two [InAppWebViewController] handles wrap the same
/// underlying platform WebView controller.
///
/// `flutter_inappwebview_android` allocates distinct Dart wrapper instances for
/// `onWebViewCreated` and `onRenderProcessGone`, so outer `identical(a, b)` is
/// always false on Android.
bool isSameQuizWebViewController(
  InAppWebViewController? first,
  InAppWebViewController? second,
) {
  if (first == null || second == null) return false;
  return identical(first, second) || identical(first.platform, second.platform);
}

/// Handles Android `WebViewClient.onRenderProcessGone` on the active quiz WebView.
///
/// Intentionally does NOT auto-reload a live Moodle Quiz attempt so an in-flight
/// POST or attempt submission is never duplicated; instead stops timers, tears
/// down the dead WebView instance, and emits [quizRenderProcessGoneErrorCode] so
/// the user can recover safely. Ignores stale callbacks from older generations.
bool handleQuizMainRenderProcessGone({
  required bool disposed,
  required bool mounted,
  required int callbackGeneration,
  required int activeGeneration,
  required VoidCallback stopLoadTimeout,
  required VoidCallback clearDeadWebViewState,
  required void Function(String code) emitError,
}) {
  if (disposed || !mounted || callbackGeneration != activeGeneration) {
    return false;
  }
  stopLoadTimeout();
  clearDeadWebViewState();
  emitError(quizRenderProcessGoneErrorCode);
  return true;
}

/// Handles Android `WebViewClient.onRenderProcessGone` on an active quiz popup.
void handleQuizPopupRenderProcessGone({
  required bool disposed,
  required bool mounted,
  required VoidCallback closePopup,
  required void Function(String code) emitError,
}) {
  if (disposed || !mounted) return;
  closePopup();
  emitError(quizRenderProcessGoneErrorCode);
}

/// Handles Android `WebViewClient.onRenderProcessGone` on an offstage blocked
/// popup sink by tearing down the dead sink cleanly without crashing.
void handleQuizBlockedPopupSinkRenderProcessGone({
  required bool disposed,
  required bool mounted,
  required VoidCallback removeSink,
}) {
  if (disposed || !mounted) return;
  removeSink();
}

/// Keeps the primary quiz WebView mounted at constant layout bounds while a
/// popup is active, blocking pointer and keyboard focus instead of `Offstage`.
Widget buildQuizMainWebViewLayer({
  required bool obscuredByPopup,
  required Widget child,
}) {
  return IgnorePointer(
    ignoring: obscuredByPopup,
    child: ExcludeFocus(
      excluding: obscuredByPopup,
      child: child,
    ),
  );
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

class _BlockedPopupWindow {
  const _BlockedPopupWindow({required this.windowId, required this.key});

  final int windowId;
  final int key;
}

class _UthQuizPlayerControlState extends State<UthQuizPlayerControl> {
  final GlobalKey _mainWebViewKey = GlobalKey();
  int _webViewGeneration = 0;
  InAppWebViewController? _controller;
  WebViewEnvironment? _environment;
  bool _hasActiveEnvironmentMount = false;
  String? _acquiredEnvironmentKey;
  int? _acquiredEnvironmentEpoch;
  Uri? _trustedOrigin;
  String? _initialUrl;
  String? _sessionKey;
  bool _requireSession = false;
  bool _sessionBootstrapped = false;
  bool _bootstrapStarted = false;
  bool _sessionPayloadRejected = false;
  bool _sessionExpiredSent = false;
  bool _prepared = false;
  bool _renderProcessGone = false;
  bool _disposed = false;
  bool _readySent = false;
  int _nextPopupKey = 0;
  final List<_PopupWindow> _popups = [];
  final List<_BlockedPopupWindow> _blockedPopups = [];
  final Map<int, Timer> _popupLoadTimers = {};
  final Map<int, Timer> _blockedPopupTimers = {};
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
    _trustedOrigin = parseQuizBaseUri(_initialUrl, requireHttps: _requireSession);
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

  bool _validSessionKey(String? value) =>
      value != null && _sessionKeyPattern.hasMatch(value);

  Future<void> _prepare() async {
    if (_trustedOrigin == null) {
      _emitError(_requireSession ? 'session_unavailable' : 'navigation_error');
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
        final profilesRoot = Directory(
          path.join(supportDirectory.path, 'uthhelper', 'quiz-webview'),
        );

        final sessionKey = _sessionKey!;
        _acquiredEnvironmentKey = sessionKey.toLowerCase();
        _hasActiveEnvironmentMount = true;
        late final ({
          WebViewEnvironment environment,
          Directory profileDirectory,
          int sessionEpoch,
        }) acquired;
        try {
          acquired = await sharedQuizWindowsEnvironmentCache.acquire(
            sessionKey,
            profilesRoot,
            createEnvironment: (profileDir) => WebViewEnvironment.create(
              settings: WebViewEnvironmentSettings(
                userDataFolder: profileDir.path,
              ),
            ),
            disposeEnvironment: (env) => env.dispose(),
            markActiveMount: true,
            sweepStale: true,
          );
        } catch (_) {
          _hasActiveEnvironmentMount = false;
          rethrow;
        }
        if (_disposed) {
          if (_hasActiveEnvironmentMount && _acquiredEnvironmentKey != null) {
            sharedQuizWindowsEnvironmentCache.releaseMount(
              _acquiredEnvironmentKey!,
            );
            _hasActiveEnvironmentMount = false;
          }
          return;
        }
        _environment = acquired.environment;
        _acquiredEnvironmentEpoch = acquired.sessionEpoch;
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
      final lease = await sharedMobileSessionQueue.acquire(
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

  Future<void> _initializeController(
    InAppWebViewController controller,
    int mountGeneration,
  ) async {
    if (_disposed || _renderProcessGone || mountGeneration != _webViewGeneration) {
      return;
    }
    _controller = controller;
    try {
      final purge = _purgeQuizOriginStore();
      _initialStorePurge = purge;
      await purge.timeout(_mobileCleanupTimeout);
      if (_disposed || _renderProcessGone || mountGeneration != _webViewGeneration) {
        return;
      }
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

  Future<void> _purgeQuizOriginStore() async {
    if (!kIsWeb && defaultTargetPlatform == TargetPlatform.windows) {
      if (_acquiredEnvironmentKey != null) {
        final bumped = sharedQuizWindowsEnvironmentCache.bumpSessionEpoch(
          _acquiredEnvironmentKey!,
        );
        if (bumped > 0) {
          _acquiredEnvironmentEpoch = bumped;
        }
      }
      await _cookieManager().deleteAllCookies();
      final controller = _controller;
      final origin = _trustedOrigin?.origin;
      if (controller != null && origin != null && origin.isNotEmpty) {
        try {
          await controller
              .callDevToolsProtocolMethod(
                methodName: 'Storage.clearDataForOrigin',
                parameters: <String, dynamic>{
                  'origin': origin,
                  'storageTypes':
                      'local_storage,indexeddb,websql,service_workers',
                },
              )
              .timeout(const Duration(seconds: 2));
        } catch (_) {
          // Best-effort: older WebView2 runtimes or test fakes may not support CDP.
        }
      }
      return;
    }
    if (!kIsWeb &&
        (defaultTargetPlatform == TargetPlatform.android ||
            defaultTargetPlatform == TargetPlatform.iOS)) {
      await purgeQuizOriginCookies(_cookieManager(), _trustedOrigin);
      await purgeQuizOriginWebStorage(
        defaultTargetPlatform,
        WebStorageManager.instance(),
        _trustedOrigin,
      );
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
      if (_controller == null || _disposed || _renderProcessGone) return false;
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
      if (_disposed || _renderProcessGone || _controller == null) return false;
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
    final raw = value.toString();
    final destination = Uri.tryParse(raw);
    if (_sameOrigin(destination, rawUrl: raw) &&
        (destination!.path == '/login/index.php' ||
            destination.path == '/login/')) {
      _sessionExpiredSent = true;
      _trigger('session_expired', 'session_expired');
    }
  }

  bool _sameOrigin(Uri? destination, {String? rawUrl}) {
    final trusted = _trustedOrigin;
    if (trusted == null || destination == null) return false;
    if (!destination.isAbsolute ||
        !destination.hasAuthority ||
        destination.host.isEmpty ||
        destination.userInfo.isNotEmpty ||
        destination.hasPort) {
      return false;
    }
    if (rawUrl != null &&
        (hasUnsafeUrlCharacters(rawUrl) || hasExplicitPortOrUserInfo(rawUrl))) {
      return false;
    }
    return destination.scheme.toLowerCase() == trusted.scheme.toLowerCase() &&
        destination.host.toLowerCase() == trusted.host.toLowerCase() &&
        destination.port == trusted.port;
  }

  void _enforceLoadOrigin(InAppWebViewController controller, WebUri? url) {
    if (_disposed || url == null) return;
    final raw = url.toString();
    if (raw.isEmpty || raw.toLowerCase() == 'about:blank') return;
    final destination = Uri.tryParse(raw);
    if (!_sameOrigin(destination, rawUrl: raw)) {
      unawaited(controller.stopLoading().catchError((_) {}));
    }
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

    final raw = action.request.url?.toString() ?? '';
    if (popup &&
        raw.toLowerCase() == 'about:blank' &&
        !hasUnsafeUrlCharacters(raw)) {
      return NavigationActionPolicy.ALLOW;
    }
    final destination = Uri.tryParse(raw);
    if (_sameOrigin(destination, rawUrl: raw)) {
      return NavigationActionPolicy.ALLOW;
    }

    _trigger('external_link', 'external_link');
    return NavigationActionPolicy.CANCEL;
  }

  Future<bool> _createWindow(CreateWindowAction action) async {
    if (_disposed) return false;
    final disposition = classifyQuizPopupRequest(
      action.request.url?.toString(),
      _trustedOrigin,
      activePopupCount: _popups.length,
    );
    if (disposition == QuizPopupRequestKind.external) {
      _trigger('external_link', 'external_link');
    }
    if (disposition == QuizPopupRequestKind.invalid ||
        disposition == QuizPopupRequestKind.limitReached ||
        disposition == QuizPopupRequestKind.external) {
      if (shouldAdoptBlockedPopupToCancel(defaultTargetPlatform)) {
        final blockedKey = _nextPopupKey++;
        _blockedPopupTimers[blockedKey] = Timer(
          _blockedPopupDisposalTimeout,
          () => _removeBlockedPopup(blockedKey),
        );
        setState(() {
          _blockedPopups.add(
            _BlockedPopupWindow(windowId: action.windowId, key: blockedKey),
          );
        });
        return true;
      }
      return false;
    }

    setState(() {
      _popups.add(
        _PopupWindow(windowId: action.windowId, key: _nextPopupKey++),
      );
    });
    return true;
  }

  void _disposeBlockedPopup(int key, InAppWebViewController controller) {
    unawaited(controller.stopLoading().catchError((_) {}));
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _removeBlockedPopup(key);
    });
  }

  void _removeBlockedPopup(int key) {
    _blockedPopupTimers.remove(key)?.cancel();
    if (_disposed || !mounted) return;
    setState(() => _blockedPopups.removeWhere((popup) => popup.key == key));
  }

  void _closePopup(int key) {
    if (_disposed || !mounted) return;
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

  Widget _blockedPopupSink(_BlockedPopupWindow blocked) {
    return Offstage(
      offstage: true,
      child: SizedBox(
        width: 1,
        height: 1,
        child: InAppWebView(
          key: ValueKey('quiz-blocked-popup-${blocked.key}'),
          windowId: blocked.windowId,
          webViewEnvironment: _environment,
          initialSettings: createQuizWebViewSettings(allowPopups: false),
          onWebViewCreated: (controller) =>
              _disposeBlockedPopup(blocked.key, controller),
          onLoadStart: (controller, _url) =>
              unawaited(controller.stopLoading().catchError((_) {})),
          shouldOverrideUrlLoading: (_controller, _action) async =>
              NavigationActionPolicy.CANCEL,
          onCreateWindow: (_controller, _action) async => false,
          onRenderProcessGone: (_controller, _detail) {
            handleQuizBlockedPopupSinkRenderProcessGone(
              disposed: _disposed,
              mounted: mounted,
              removeSink: () => _removeBlockedPopup(blocked.key),
            );
          },
        ),
      ),
    );
  }

  Widget _popup(_PopupWindow popup) {
    return Positioned.fill(
      child: InAppWebView(
        key: ValueKey('quiz-popup-${popup.key}'),
        windowId: popup.windowId,
        webViewEnvironment: _environment,
        initialSettings: createQuizWebViewSettings(),
        onLoadStart: (controller, url) {
          _enforceLoadOrigin(controller, url);
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
        onRenderProcessGone: (_controller, _detail) {
          handleQuizPopupRenderProcessGone(
            disposed: _disposed,
            mounted: mounted,
            closePopup: () => _closePopup(popup.key),
            emitError: _emitError,
          );
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

    final mountGeneration = _webViewGeneration;

    return LayoutControl(
      control: widget.control,
      child: Stack(
        fit: StackFit.expand,
        children: [
          if (!_renderProcessGone)
            buildQuizMainWebViewLayer(
              obscuredByPopup: _popups.isNotEmpty,
              child: InAppWebView(
                key: _mainWebViewKey,
                webViewEnvironment: _environment,
                initialSettings: createQuizWebViewSettings(),
                onWebViewCreated: (controller) =>
                    unawaited(_initializeController(controller, mountGeneration)),
                onLoadStart: (controller, url) {
                  _enforceLoadOrigin(controller, url);
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
                onRenderProcessGone: (controller, _detail) {
                  handleQuizMainRenderProcessGone(
                    disposed: _disposed,
                    mounted: mounted,
                    callbackGeneration: mountGeneration,
                    activeGeneration: _webViewGeneration,
                    stopLoadTimeout: _stopLoadTimeout,
                    clearDeadWebViewState: () {
                      if (_controller == null ||
                          isSameQuizWebViewController(_controller, controller)) {
                        _controller = null;
                      }
                      _webViewGeneration++;
                      setState(() => _renderProcessGone = true);
                    },
                    emitError: _emitError,
                  );
                },
              ),
            )
          else
            const SizedBox.shrink(),
          for (final blocked in _blockedPopups) _blockedPopupSink(blocked),
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
    for (final timer in _blockedPopupTimers.values) {
      timer.cancel();
    }
    _blockedPopupTimers.clear();
    final lease = _mobileSessionLease;
    if (lease != null) {
      unawaited(_releaseMobileSession(lease));
    }
    if (_hasActiveEnvironmentMount && _acquiredEnvironmentKey != null) {
      sharedQuizWindowsEnvironmentCache.releaseMount(_acquiredEnvironmentKey!);
      _hasActiveEnvironmentMount = false;
    }
    if (_environment != null &&
        _acquiredEnvironmentKey != null &&
        _acquiredEnvironmentEpoch != null) {
      unawaited(
        _cleanupWindowsSessionOnClose(
          _environment!,
          sessionKey: _acquiredEnvironmentKey!,
          sessionEpoch: _acquiredEnvironmentEpoch!,
        ),
      );
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

  Future<void> _cleanupWindowsSessionOnClose(
    WebViewEnvironment environment, {
    required String sessionKey,
    required int sessionEpoch,
  }) async {
    await sharedQuizWindowsEnvironmentCache.runCloseCleanup(
      sessionKey,
      expectedEpoch: sessionEpoch,
      beforeCleanup: _waitForSessionWork,
      cleanup: () async {
        try {
          await CookieManager.instance(webViewEnvironment: environment)
              .deleteAllCookies()
              .timeout(_mobileCleanupTimeout);
        } catch (_) {
          // Keep cached environment & HTTP disk cache intact; cookies are also
          // cleared on next quiz open before bootstrap.
        }
      },
    );
  }

  Future<void> _releaseMobileSession(MobileSessionLease lease) async {
    try {
      if (!await _waitForSessionWork()) {
        // A late cookie write could follow cleanup, so never hand the shared
        // mobile store to another quiz session in this process.
        sharedMobileSessionQueue.disable();
        unawaited(
          _settleSessionWork().then((_) async {
            try {
              await _purgeQuizOriginStore().timeout(_mobileCleanupTimeout);
            } catch (_) {
              // The queue stays disabled if the final purge also fails.
            }
          }),
        );
      }
      await _purgeQuizOriginStore().timeout(_mobileCleanupTimeout);
    } catch (_) {
      // Cleanup failures are deliberately not surfaced with native details.
      sharedMobileSessionQueue.disable();
    } finally {
      lease.release();
      _mobileSessionLease = null;
    }
  }
}
