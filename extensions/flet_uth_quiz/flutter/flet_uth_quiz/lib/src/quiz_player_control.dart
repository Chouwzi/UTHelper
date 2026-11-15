import 'dart:async';

import 'package:flet/flet.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_inappwebview/flutter_inappwebview.dart';
import 'package:path/path.dart' as path;
import 'package:path_provider/path_provider.dart';

import 'android_session_queue.dart';

const _loadTimeout = Duration(seconds: 45);
const _androidAcquireTimeout = Duration(seconds: 30);
const _androidCleanupTimeout = Duration(seconds: 5);
final _sessionKeyPattern = RegExp(r'^[0-9a-fA-F]{64}$');
final _androidSessionQueue = AndroidSessionQueue();

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
  WebViewEnvironment? _environment;
  Uri? _trustedOrigin;
  String? _initialUrl;
  String? _sessionKey;
  bool _prepared = false;
  bool _disposed = false;
  bool _readySent = false;
  int _nextPopupKey = 0;
  final List<_PopupWindow> _popups = [];
  Timer? _loadTimer;
  final Completer<void> _disposeSignal = Completer<void>();
  AndroidSessionLease? _androidSessionLease;

  @override
  void initState() {
    super.initState();
    _initialUrl = widget.control.getString('url');
    _sessionKey = widget.control.getString('session_key');
    _trustedOrigin = _parseHttpUri(_initialUrl);
    unawaited(_prepare());
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
      } else if (!kIsWeb && defaultTargetPlatform == TargetPlatform.android) {
        await _acquireAndroidSession();
        if (_androidSessionLease == null) {
          if (!_disposed) _emitError('webview_unavailable');
          return;
        }
      } else if (!kIsWeb && defaultTargetPlatform != TargetPlatform.iOS) {
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

  Future<void> _acquireAndroidSession() async {
    final timeout = Completer<void>();
    final timer = Timer(_androidAcquireTimeout, timeout.complete);
    try {
      final lease = await _androidSessionQueue.acquire(
        cancelled: Future.any<void>([_disposeSignal.future, timeout.future]),
      );
      if (_disposed || timeout.isCompleted) {
        lease?.release();
        return;
      }
      _androidSessionLease = lease;
    } finally {
      timer.cancel();
    }
  }

  Future<void> _initializeController(InAppWebViewController controller) async {
    if (!_readySent) {
      _readySent = true;
      _trigger('ready', 'ready');
    }

    try {
      if (!kIsWeb && defaultTargetPlatform == TargetPlatform.android) {
        // The main WebView has no initial request. Clear the shared Android
        // WebView store while holding the session lease; popup WebViews use the
        // same store without incognito's global cookie reset.
        await _purgeAndroidStore().timeout(const Duration(seconds: 10));
      }
      final url = _initialUrl;
      if (!_disposed && url != null) {
        await controller.loadUrl(urlRequest: URLRequest(url: WebUri(url)));
      }
    } on MissingPluginException {
      _emitError('webview_unavailable');
    } on PlatformException {
      _emitError('navigation_error');
    } catch (_) {
      _emitError('webview_unavailable');
    }
  }

  Future<void> _purgeAndroidStore() async {
    await WebStorageManager.instance().deleteAllData();
    await CookieManager.instance().deleteAllCookies();
    await InAppWebViewController.clearAllCache();
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
    setState(() => _popups.removeWhere((popup) => popup.key == key));
  }

  InAppWebViewSettings _settings({bool popup = false}) {
    final isAndroid =
        !kIsWeb && defaultTargetPlatform == TargetPlatform.android;
    final isIOS = !kIsWeb && defaultTargetPlatform == TargetPlatform.iOS;
    return InAppWebViewSettings(
      javaScriptEnabled: true,
      javaScriptCanOpenWindowsAutomatically: true,
      supportMultipleWindows: true,
      useShouldOverrideUrlLoading: true,
      // Android's incognito setting clears the process-wide cookie store. Keep
      // popup WebViews non-incognito so Moodle's authenticated window survives.
      incognito: popup ? isIOS : (isAndroid || isIOS),
    );
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
        initialSettings: _settings(popup: true),
        onCreateWindow: (controller, action) => _createWindow(action),
        onCloseWindow: (controller) => _closePopup(popup.key),
        shouldOverrideUrlLoading: (controller, action) async =>
            _navigationPolicy(action, popup: true),
        onReceivedError: (controller, request, error) {
          if (request.isForMainFrame == true) {
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
              onLoadStart: (controller, url) => _startLoadTimeout(),
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
    final lease = _androidSessionLease;
    if (lease != null) {
      unawaited(_releaseAndroidSession(lease));
    }
    final environment = _environment;
    if (environment != null) unawaited(environment.dispose());
    super.dispose();
  }

  Future<void> _releaseAndroidSession(AndroidSessionLease lease) async {
    try {
      await _purgeAndroidStore().timeout(_androidCleanupTimeout);
    } catch (_) {
      // Cleanup failures are deliberately not surfaced with native details.
      _androidSessionQueue.disable();
    } finally {
      lease.release();
      _androidSessionLease = null;
    }
  }
}
